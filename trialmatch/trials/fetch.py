"""Resolve each registry trial to a ClinicalTrials.gov record and cache its eligibility criteria.

Trial criteria are public data, so this is the only step that talks to an external service; patient data never does.
Manual overrides live in trials/mapping.csv (local_id;nct_id) and always win.

Usage: python -m trialmatch.trials.fetch [--refresh]
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time

import requests

from ..config import TRIALS_DIR, TRIALS_RAW
from .registry import TrialEntry, load_registry

API = "https://clinicaltrials.gov/api/v2/studies"
MAPPING = TRIALS_DIR / "mapping.csv"
STOP = {"pozn", "and", "the", "study", "phase", "trial"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def protocol_tokens(protocol: str) -> list[str]:
    """'J2J-MC-JZLH (EMBER-4)' -> ['J2J-MC-JZLH', 'EMBER-4'] (longest first)."""
    parts = re.split(r"[()/,;]|\s-\s|\s+", protocol)
    toks = [p.strip(" .") for p in parts if len(_norm(p)) >= 4 and p.lower().strip(" .") not in STOP]
    whole = protocol.split("(")[0].strip()
    return sorted(set(toks + ([whole] if whole else [])), key=len, reverse=True)


def _search(term: str, page_size: int = 10) -> list[dict]:
    r = requests.get(API, params={"query.term": term, "pageSize": page_size, "format": "json"}, timeout=30)
    r.raise_for_status()
    return r.json().get("studies", [])


def _get(nct: str) -> dict:
    r = requests.get(f"{API}/{nct}", params={"format": "json"}, timeout=30)
    r.raise_for_status()
    return r.json()


def _score(study: dict, trial: TrialEntry, tokens: list[str]) -> float:
    ident = study["protocolSection"]["identificationModule"]
    ids = [ident.get("orgStudyIdInfo", {}).get("id", "")] + [s["id"] for s in ident.get("secondaryIdInfos", [])]
    nids = {_norm(i) for i in ids if i}
    title = (ident.get("briefTitle", "") + " " + ident.get("officialTitle", "")).lower()
    score = 0.0
    for t in tokens:
        nt = _norm(t)
        if nt in nids:
            score += 10
        elif any(nt in i for i in nids):
            score += 5
        elif nt in _norm(title):
            score += 3
    if "extension" in title and "extension" not in trial.title_cs.lower():
        score -= 4
    # word overlap with the registry title (helps when the registry title is in English)
    words = {w for w in re.findall(r"[a-z]{5,}", trial.title_cs.lower())}
    score += 0.2 * len(words & set(re.findall(r"[a-z]{5,}", title)))
    return score


def resolve(trial: TrialEntry, manual: dict[str, str]) -> tuple[str | None, float, str]:
    if trial.local_id in manual:
        return manual[trial.local_id], 99.0, "manual"
    tokens = protocol_tokens(trial.protocol)
    best, best_score = None, 0.0
    for tok in tokens[:3]:
        for st in _search(tok):
            s = _score(st, trial, tokens)
            if s > best_score:
                best, best_score = st["protocolSection"]["identificationModule"]["nctId"], s
        time.sleep(0.2)
    # Only accept when an ID token actually matched (>= 5); title overlap alone is not enough.
    return (best, best_score, "auto") if best_score >= 5 else (None, best_score, "unresolved")


def load_manual() -> dict[str, str]:
    if not MAPPING.exists():
        return {}
    with open(MAPPING, encoding="utf-8") as fh:
        return {r["local_id"]: r["nct_id"] for r in csv.DictReader(fh, delimiter=";") if r.get("nct_id")}


def fetch_all(refresh: bool = False) -> list[dict]:
    TRIALS_RAW.mkdir(parents=True, exist_ok=True)
    manual = load_manual()
    report = []
    for trial in load_registry():
        out = TRIALS_RAW / f"{trial.local_id}.json"
        if out.exists() and not refresh:
            rec = json.loads(out.read_text(encoding="utf-8"))
            report.append({"local_id": trial.local_id, "nct_id": rec.get("nct_id"), "how": rec.get("how")})
            continue
        try:
            nct, score, how = resolve(trial, manual)
            study = _get(nct) if nct else None
        except requests.RequestException as e:
            print(f"{trial.local_id}: network error {e}", file=sys.stderr)
            continue
        rec = {**trial.to_dict(), "nct_id": nct, "how": how, "match_score": score, "study": study}
        out.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
        report.append({"local_id": trial.local_id, "nct_id": nct, "how": how})
        print(f"{trial.local_id:16s} {trial.protocol[:30]:30s} -> {nct or '-':12s} ({how}, {score:.1f})")
    ok = sum(1 for r in report if r["nct_id"])
    print(f"\nresolved {ok}/{len(report)} trials; unresolved ones can be added to {MAPPING}")
    return report


if __name__ == "__main__":
    fetch_all(refresh="--refresh" in sys.argv)
