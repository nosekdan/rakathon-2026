#!/usr/bin/env bash
# Run TrialScreen on the local rule engine – one command, nothing leaves this machine.
#
#   scripts/run_trialmatch.sh                 rules only: all C61/C64 patients x their studies, then the app
#   scripts/run_trialmatch.sh --llm [N]       rules + local LLM (llama.cpp, Qwen2.5-3B) for the N best-documented
#                                             patients per diagnosis (default 3; ~5 s per unstructured criterion)
#   scripts/run_trialmatch.sh --download-model   fetch llama.cpp + the model into ~/opt (checksums verified)
#   scripts/run_trialmatch.sh --stop          stop the local LLM started by this script
#
# Needs the dataset folder (rakathon-dataset-*/) and the study workbook (*.xlsx) in the repository root.
set -euo pipefail
cd "$(dirname "$0")/.."

LLAMA_BUILD=b11327
LLAMA_SHA256=0f8b6c3e9f3c55420d448ca1c844068577aa9074185226ac0f9d5dd2277cc91a
MODEL_URL=https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf
MODEL_SHA256=626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d
LLAMA_BIN="${TM_LLAMA_BIN:-$HOME/opt/llama.cpp/$LLAMA_BUILD/llama-$LLAMA_BUILD/llama-server}"
MODEL="${TM_LLM_GGUF:-$HOME/opt/models/qwen2.5-3b-instruct-q4_k_m.gguf}"
LLM_PORT="${TM_LLM_PORT:-8092}"
APP_PORT="${PORT:-8765}"

if command -v uv >/dev/null; then PY=(uv run python); UVICORN=(uv run uvicorn)
else PY=(.venv/bin/python); UVICORN=(.venv/bin/uvicorn); fi

download_model() {
  mkdir -p "$(dirname "$MODEL")" "$HOME/opt/llama.cpp/$LLAMA_BUILD"
  if [ ! -x "$LLAMA_BIN" ]; then
    local tgz="$HOME/opt/llama.cpp/$LLAMA_BUILD/llama.tar.gz"
    curl -fL -o "$tgz" "https://github.com/ggml-org/llama.cpp/releases/download/$LLAMA_BUILD/llama-$LLAMA_BUILD-bin-ubuntu-x64.tar.gz"
    echo "$LLAMA_SHA256  $tgz" | sha256sum -c - && tar xzf "$tgz" -C "$HOME/opt/llama.cpp/$LLAMA_BUILD" && rm "$tgz"
  fi
  if [ ! -f "$MODEL" ]; then
    curl -fL -o "$MODEL" "$MODEL_URL"
    echo "$MODEL_SHA256  $MODEL" | sha256sum -c - || { rm -f "$MODEL"; exit 1; }
  fi
  echo "llama.cpp: $LLAMA_BIN"; echo "model:     $MODEL"
}

start_llm() {
  if [ ! -x "$LLAMA_BIN" ] || [ ! -f "$MODEL" ]; then
    echo "Local model not found – run: scripts/run_trialmatch.sh --download-model (≈2 GB)" >&2; exit 1
  fi
  mkdir -p .cache
  [ -s .cache/llm_api_key ] || (umask 077 && head -c 24 /dev/urandom | base64 | tr -d '/+=' > .cache/llm_api_key)
  if ! curl -s -m 1 "http://127.0.0.1:$LLM_PORT/health" | grep -q ok; then
    nohup "$LLAMA_BIN" -m "$MODEL" --alias qwen2.5-3b-instruct --host 127.0.0.1 --port "$LLM_PORT" \
      --api-key-file .cache/llm_api_key -c 4096 -t "${TM_LLM_THREADS:-8}" --temp 0 --no-ui \
      > .cache/llm_server.log 2>&1 &
    echo -n "Starting local LLM"
    for _ in $(seq 1 60); do
      curl -s -m 1 "http://127.0.0.1:$LLM_PORT/health" | grep -q ok && break; echo -n "."; sleep 1
    done
    echo
  fi
  export TM_LLM_BASE_URL="http://127.0.0.1:$LLM_PORT/v1" TM_LLM_MODEL=qwen2.5-3b-instruct
  export TM_LLM_API_KEY="$(cat .cache/llm_api_key)"
}

case "${1:-}" in
  --download-model) download_model; exit 0 ;;
  --stop) pkill -f "llama-server .*--port $LLM_PORT" && echo "Local LLM stopped." || echo "Not running."; exit 0 ;;
  --llm)
    start_llm
    ENGINE=trialmatch+llm; LIMIT="${2:-3}"; export TRIALSCREEN_DB=.cache/trialmatch-llm.sqlite3 ;;
  "")
    ENGINE=trialmatch; LIMIT=0; export TRIALSCREEN_DB=.cache/trialmatch.sqlite3 ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac

export PATIENT_MODEL_DEPLOYMENT=trialmatch TRIAL_MODEL_DEPLOYMENT=trialmatch MODEL_DEPLOYMENT="$ENGINE"
"${PY[@]}" precompute_trialmatch.py --limit "$LIMIT" | grep -v '"kind": "pair"' || true
echo
echo "TrialScreen ($ENGINE): http://127.0.0.1:$APP_PORT   (Ctrl+C to stop)"
exec "${UVICORN[@]}" main:app --host 127.0.0.1 --port "$APP_PORT"
