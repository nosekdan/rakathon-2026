"""Central paths and settings. Everything stays local; the only network target at runtime is the local LLM."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = ROOT / "data" / "raw"
DATA_WORK = ROOT / "data" / "work"
DB_PATH = DATA_WORK / "trialmatch.duckdb"
TRIALS_DIR = ROOT / "trials"
TRIALS_RAW = TRIALS_DIR / "raw"
TRIALS_CRITERIA = TRIALS_DIR / "criteria"
TRIALS_XLSX = ROOT / "resources" / "Seznam studií - FNMH.xlsx"
LEXICON_DIR = Path(__file__).resolve().parent / "lexicon"

# Local LLM (OpenAI-compatible endpoint: llama.cpp server by default; Ollama / vLLM also work). Never point this at an external service.
LLM_BASE_URL = os.environ.get("TM_LLM_BASE_URL", "http://127.0.0.1:8091/v1")
LLM_MODEL = os.environ.get("TM_LLM_MODEL", "qwen2.5-3b-instruct")
LLM_ENABLED = os.environ.get("TM_LLM_ENABLED", "1") == "1"

# Doctor-facing match bands ("Míra shody" = TrialMatch.rank). Proposed by the consulting oncologist; tune here.
NOTIFY_MIN = float(os.environ.get("TM_NOTIFY_MIN", "0.50"))   # below: never shown in pop-ups
GREEN_MIN = float(os.environ.get("TM_GREEN_MIN", "0.60"))     # at/above: green; between: amber
NOTIFY_RANK_JUMP = 0.10        # re-notify when the match rises by at least this much
SNOOZE_DAYS = 7
NEAR_MAX_GAPS = 2              # "close to eligible": at most this many actionable gaps
STATE_DB = DATA_WORK / "app_state.sqlite"
