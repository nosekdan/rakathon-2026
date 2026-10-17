"""Tier-3: ask the LOCAL LLM to judge a criterion against a few retrieved patient sentences.

Hallucination guard: the model must quote evidence verbatim; quotes not found in the snippets are rejected and the
result falls back to UNKNOWN.
"""
from __future__ import annotations

import hashlib
import json
import re

from .. import llm
from ..config import DATA_WORK
from ..schema import MET, NOT_MET, REVIEW, UNKNOWN, Criterion, CriterionResult, Fact
from ..text.normalize import fold
from .retrieve import SentenceIndex

SYSTEM = (
    "You screen patients for clinical trials. You get ONE eligibility criterion and short excerpts from the "
    "patient's Czech medical reports. Decide whether the condition described by the criterion applies to the "
    "patient NOW (the latest excerpt date is 'now'; randomization would be now).\n"
    "- answer 'yes' if the excerpts state the condition is present (e.g. bleeding 2 months ago, chronic prednisone).\n"
    "- answer 'no' if the excerpts state it is absent, negated or clearly resolved.\n"
    "- answer 'unknown' ONLY if the excerpts do not mention the condition at all.\n"
    "Do not ask for details the reports cannot contain (exact randomization date, protocol definitions).\n"
    "evidence_quote: copy ONE excerpt sentence exactly, without the [n] number or date. "
    "reason_cs: one short sentence in Czech."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "evidence_quote": {"type": "string"},
        "reason_cs": {"type": "string"},
    },
    "required": ["answer", "evidence_quote", "reason_cs"],
    "additionalProperties": False,
}
CACHE = DATA_WORK / "llm_cache.jsonl"
_cache: dict[str, dict] | None = None


def _load_cache() -> dict[str, dict]:
    global _cache
    if _cache is None:
        _cache = {}
        if CACHE.exists():
            for line in CACHE.read_text(encoding="utf-8").splitlines():
                d = json.loads(line)
                _cache[d["key"]] = d["out"]
    return _cache


def _save(key: str, out: dict) -> None:
    _load_cache()[key] = out
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"key": key, "out": out}, ensure_ascii=False) + "\n")


_PREFIX = re.compile(r"^\s*(?:\[\d+\]\s*)?(?:\(?\d{4}-\d{2}-\d{2}\)?:?\s*|\?:\s*)?")


def _find_source(quote: str, snippets):
    """Accept the quote if any of its lines (without [n]/date prefix) appears verbatim in a retrieved snippet."""
    def norm(t: str) -> str:
        return re.sub(r"\s+", " ", fold(t)).strip()

    for line in quote.splitlines() + [quote]:
        q = norm(_PREFIX.sub("", line).strip().strip('"„“'))[:80]
        if len(q) < 8:
            continue
        for sid, d, s in snippets:
            if q in norm(s):
                return sid, d, s
    return None


def judge(criterion: Criterion, index: SentenceIndex, k: int = 6) -> CriterionResult:
    snippets = index.search(criterion.text, k=k)
    if not snippets:
        return CriterionResult(criterion, UNKNOWN, explanation="Lokální LLM: nenalezen žádný relevantní text.")
    question = ("Does the patient have / satisfy this?" if criterion.kind == "inclusion"
                else "Does the patient have this (exclusion criterion)?")
    excerpts = "\n".join(f"[{i + 1}] {d or '?'}: {s[:400]}" for i, (_, d, s) in enumerate(snippets))
    user = f"Criterion ({criterion.kind}): {criterion.text}\n{question}\n\nExcerpts:\n{excerpts}"
    key = hashlib.sha1(f"{llm.LLM_MODEL}|{criterion.id}|{SYSTEM}|{user}".encode()).hexdigest()
    out = _load_cache().get(key)
    if out is None:
        out = llm.chat_json(SYSTEM, user, SCHEMA)
        if out is None:
            return CriterionResult(criterion, UNKNOWN, explanation="Lokální LLM neodpověděl.")
        _save(key, out)
    src = _find_source(out.get("evidence_quote") or "", snippets)
    answer = out.get("answer")
    if answer in ("yes", "no") and src is None:
        return CriterionResult(criterion, UNKNOWN, confidence=0.3,
                               explanation="Lokální LLM odpověděl bez ověřitelné citace – ignorováno.")
    if answer == "unknown":
        return CriterionResult(criterion, UNKNOWN, explanation=f"Lokální LLM: z textu nelze určit. {out.get('reason_cs', '')}")
    has = answer == "yes"
    status = (MET if has else NOT_MET) if criterion.kind == "inclusion" else (NOT_MET if has else MET)
    if status == NOT_MET:
        status = REVIEW  # never exclude a patient on an LLM judgement alone
    ev = Fact(concept="llm", value=has, date=src[1], method="llm", source_type="document", source_id=src[0],
              snippet=src[2][:500], confidence=0.6)
    return CriterionResult(criterion, status, confidence=0.6,
                           explanation=f"Lokální LLM: {out.get('reason_cs', '')}".strip(), evidence=[ev])
