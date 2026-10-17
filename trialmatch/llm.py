"""Client for a LOCAL OpenAI-compatible LLM endpoint (Ollama, llama.cpp server, vLLM).

Hospital regulation: patient data must not leave the hospital, so the client refuses any non-local host unless it is
explicitly allow-listed via TM_LLM_ALLOW_HOSTS (e.g. an on-prem GPU server).
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from urllib.parse import urlparse

import requests

from .config import DATA_WORK, LLM_BASE_URL, LLM_ENABLED, LLM_MODEL

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class ExternalEndpointError(RuntimeError):
    pass


def _check_local(url: str) -> None:
    host = urlparse(url).hostname or ""
    allowed = LOCAL_HOSTS | {h.strip() for h in os.environ.get("TM_LLM_ALLOW_HOSTS", "").split(",") if h.strip()}
    if host not in allowed:
        raise ExternalEndpointError(f"LLM endpoint {host!r} is not local; refusing to send patient data.")


def _headers() -> dict:
    key = os.environ.get("TM_LLM_API_KEY")
    key_file = DATA_WORK / "llm_api_key"
    if not key and key_file.exists():
        key = key_file.read_text().strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


@lru_cache(maxsize=1)
def available() -> bool:
    if not LLM_ENABLED:
        return False
    try:
        _check_local(LLM_BASE_URL)
        r = requests.get(f"{LLM_BASE_URL.rstrip('/')}/models", headers=_headers(), timeout=2)
        return r.ok and any(m.get("id") == LLM_MODEL for m in r.json().get("data", []))
    except (requests.RequestException, ExternalEndpointError, ValueError):
        return False


def chat_json(system: str, user: str, schema: dict, *, max_tokens: int = 300, timeout: int = 180) -> dict | None:
    """Single deterministic call with JSON-schema constrained output. Returns None on any failure."""
    _check_local(LLM_BASE_URL)
    body = {
        "model": LLM_MODEL,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "out", "schema": schema, "strict": True}},
    }
    try:
        r = requests.post(f"{LLM_BASE_URL.rstrip('/')}/chat/completions", json=body, headers=_headers(), timeout=timeout)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
    except (requests.RequestException, KeyError, IndexError, ValueError):
        return None
    m = re.search(r"\{.*\}", content, re.DOTALL)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None
