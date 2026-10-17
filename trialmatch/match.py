"""Evaluate structured trial criteria against a patient profile, score and explain the result.

Usage:
  python -m trialmatch.match --patient PAC-XXXXXXXXXX [--top 10]
  python -m trialmatch.match --trial KHL-XXXX-XXX [--top 20]
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache

import yaml

from . import lexicon
from .config import DB_PATH, GREEN_MIN, LEXICON_DIR, NEAR_MAX_GAPS, NOTIFY_MIN, TRIALS_CRITERIA
from .profile import PatientProfile, is_stale, load_all_profiles, load_profile
from .schema import MET, NOT_ASSESSABLE, NOT_MET, REVIEW, UNKNOWN, Criterion, CriterionResult

LAB_MAX_AGE_DAYS = 90      # labs older than this relative to the patient's latest record need re-check
ECOG_MAX_AGE_DAYS = 120
# Default thresholds for "adequate organ function" when a trial does not spell them out.
ORGAN_DEFAULTS = [("lab.anc", ">=", 1.5, False), ("lab.platelets", ">=", 100, False), ("lab.hemoglobin", ">=", 90, False),
                  ("lab.bilirubin", "<=", 1.5, True), ("lab.ast", "<=", 2.5, True), ("lab.alt", "<=", 2.5, True),
                  ("lab.egfr", ">=", 30, False)]
STATUS_SCORE = {MET: 1.0, REVIEW: 0.5, NOT_MET: 0.0}


@dataclass
class TrialMatch:
    trial: dict
    results: list[CriterionResult]
    score: float = 0.0              # how well the assessable criteria are met (0-1)
    coverage: float = 0.0           # share of criteria we could assess (0-1)
    rank: float = 0.0
    excluded: bool = False
    gate_failed: bool = False
    to_check: list[str] = field(default_factory=list)

    @property
    def band(self) -> str:
        """Doctor-facing colour: green / amber / low / excluded."""
        if self.excluded or self.gate_failed:
            return "excluded"
        return "green" if self.rank >= GREEN_MIN else "amber" if self.rank >= NOTIFY_MIN else "low"

    def assessed(self) -> tuple[int, int]:
        """(met, assessable) – for one-line summaries like 'splňuje 12/14'."""
        rel = [r for r in self.results if r.status != NOT_ASSESSABLE]
        return sum(r.status == MET for r in rel), sum(r.status != UNKNOWN for r in rel)

    def counts(self) -> dict:
        c = {}
        for r in self.results:
            c[r.status] = c.get(r.status, 0) + 1
        return c

    def to_dict(self) -> dict:
        return {"trial": self.trial, "score": self.score, "coverage": self.coverage, "rank": self.rank,
                "band": self.band, "excluded": self.excluded, "gate_failed": self.gate_failed, "counts": self.counts(),
                "to_check": self.to_check, "results": [r.to_dict() for r in self.results]}


def _cmp(a: float, op: str, b: float) -> bool:
    return {">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b, "=": a == b}.get(op, False)


OP_CS = {">=": "≥", "<=": "≤", ">": ">", "<": "<", "=": "="}


# --- per-concept evaluators -----------------------------------------------------------------------------------------

def _eval_diagnosis(c: Criterion, p: PatientProfile) -> CriterionResult:
    prefixes = c.value or []
    if not p.icd:
        return CriterionResult(c, UNKNOWN, explanation="Diagnóza (MKN-10) není známa.")
    hits = [code for code in p.icd if any(code.startswith(x) or x.startswith(code) for x in prefixes)]
    ev = [f for f in p.facts_for("diagnosis") if f.value in hits]
    if hits:
        truncated = any(len(h) < len(x) and x.startswith(h) for h in hits for x in prefixes)
        return CriterionResult(c, REVIEW if truncated else MET, explanation=(
            f"Diagnóza {', '.join(hits)} odpovídá {', '.join(prefixes)}" +
            (" (kód zkrácen kvůli anonymizaci)." if truncated else ".")), evidence=ev)
    return CriterionResult(c, NOT_MET, explanation=f"Pacient má {', '.join(p.icd)}, studie vyžaduje {', '.join(prefixes)}.",
                           evidence=p.facts_for("diagnosis")[:3])


def _eval_sex(c: Criterion, p: PatientProfile) -> CriterionResult:
    if not p.sex:
        return CriterionResult(c, UNKNOWN, explanation="Pohlaví není známo.")
    cs = {"male": "muž", "female": "žena"}
    ok = p.sex == c.value
    return CriterionResult(c, MET if ok else NOT_MET,
                           explanation=f"Pohlaví {cs.get(p.sex, p.sex)}; studie: {cs.get(c.value, c.value)}.")


def _eval_age(c: Criterion, p: PatientProfile) -> CriterionResult:
    rng = p.age_range()
    if rng is None:
        return CriterionResult(c, UNKNOWN, explanation="Věk není znám (anonymizace).")
    lo, hi = rng
    all_ok = _cmp(lo, c.operator, c.value) and _cmp(hi, c.operator, c.value)
    none_ok = not _cmp(lo, c.operator, c.value) and not _cmp(hi, c.operator, c.value)
    age_txt = str(lo) if lo == hi else f"{lo}–{hi} (pásmo)"
    status = MET if all_ok else NOT_MET if none_ok else REVIEW
    return CriterionResult(c, status, explanation=f"Věk {age_txt}; požadavek {OP_CS[c.operator]} {c.value:g}.")


def _eval_ecog(c: Criterion, p: PatientProfile) -> CriterionResult:
    r = p.resolve("ecog")
    if r is None:
        return CriterionResult(c, UNKNOWN, explanation="ECOG/PS není v dokumentaci uveden.",
                               searched=_searched("ecog", p) | {"terms": "ECOG, PS, WHO PS, výkonnostní stav, KPS/Karnofsky"})
    ok = _cmp(r.value, c.operator, c.value)
    stale = is_stale(r.date, p.index_date, ECOG_MAX_AGE_DAYS)
    status = (MET if ok else NOT_MET) if not stale else REVIEW
    expl = f"Poslední ECOG {r.value} ({r.date or '?'}); požadavek {OP_CS[c.operator]} {c.value}."
    if stale:
        expl += " Údaj je starší než 4 měsíce – ověřit aktuální stav."
    if r.conflict:
        expl += " Ve stejný den uvedeny různé hodnoty."
        status = REVIEW if status == MET else status
    return CriterionResult(c, status, confidence=0.9, explanation=expl, evidence=r.evidence[:3],
                           flags={"stale": stale, "value": r.value, "ok": ok})


def _lab_check(p: PatientProfile, concept: str, op: str, value: float, uln: bool):
    r = p.resolve(concept)
    if r is None:
        return None, None, "chybí"
    f = r.evidence[0]
    if uln:
        ratio = (f.extra or {}).get("uln_ratio")
        if ratio is None:
            return None, r, "chybí horní mez normy"
        ok = _cmp(ratio, op, value)
        return ok, r, f"{f.extra.get('raw_value')} {f.extra.get('raw_unit') or ''} = {ratio:.2f}× ULN"
    ok = _cmp(r.value, op, value)
    return ok, r, f"{r.value:g} {f.unit or ''}"


def _eval_lab(c: Criterion, p: PatientProfile) -> CriterionResult:
    ok, r, txt = _lab_check(p, c.concept, c.operator, c.value, c.uln)
    name = lexicon.label(c.concept)
    req = f"{OP_CS.get(c.operator, c.operator)} {c.value:g}{'× ULN' if c.uln else ' ' + (c.unit or '')}"
    if r is None or ok is None:
        return CriterionResult(c, UNKNOWN, explanation=f"{name}: {txt}; požadavek {req}.",
                               searched={"terms": f"laboratoř / text: {name}", "n_docs": p.n_docs,
                                         "from": p.doc_range[0] if p.doc_range else None,
                                         "to": p.doc_range[1] if p.doc_range else None})
    stale = is_stale(r.date, p.index_date, LAB_MAX_AGE_DAYS)
    status = REVIEW if stale else (MET if ok else NOT_MET)
    expl = f"{name}: {txt} ({r.date}); požadavek {req}."
    if stale:
        expl += " Hodnota je starší než 90 dní."
    actual = (r.evidence[0].extra or {}).get("uln_ratio") if c.uln else r.value
    return CriterionResult(c, status, explanation=expl, evidence=r.evidence[:1],
                           flags={"stale": stale, "value": actual, "ok": ok})


def _eval_organ(c: Criterion, p: PatientProfile) -> CriterionResult:
    fails, oks, missing, ev = [], [], [], []
    for concept, op, val, uln in ORGAN_DEFAULTS:
        ok, r, txt = _lab_check(p, concept, op, val, uln)
        name = lexicon.label(concept)
        if r is None or ok is None:
            missing.append(name)
            continue
        ev.append(r.evidence[0])
        (oks if ok else fails).append(f"{name} {txt}")
    if fails:
        return CriterionResult(c, REVIEW, confidence=0.7, evidence=ev,
                               explanation="Mimo orientační limity: " + "; ".join(fails) + ". Limity studie ověřit v protokolu.")
    if oks and not missing:
        return CriterionResult(c, MET, confidence=0.8, evidence=ev, explanation="Krevní obraz, jaterní a renální testy v orientačních limitech.")
    if oks:
        return CriterionResult(c, MET, confidence=0.6, evidence=ev,
                               explanation="Dostupné hodnoty v limitech; chybí: " + ", ".join(missing) + ".")
    return CriterionResult(c, UNKNOWN, explanation="Laboratorní hodnoty nejsou k dispozici.")


def _eval_presence(c: Criterion, p: PatientProfile, concept: str) -> CriterionResult:
    """Boolean concept. Inclusion: present/absent required. Exclusion: presence excludes."""
    r = p.resolve(concept)
    name = lexicon.label(concept)
    want_present = c.operator == "present"
    if concept == "other_malignancy":
        return _eval_other_malignancy(c, p)
    if r is None:
        searched = _searched(concept, p)
        if c.kind == "exclusion" or not want_present:
            return CriterionResult(c, MET, confidence=0.6, searched=searched,
                                   explanation=f"{name}: v dokumentaci nenalezeno (ověřit, že opravdu není).")
        return CriterionResult(c, UNKNOWN, explanation=f"{name}: v dokumentaci nenalezeno.", searched=searched)
    expl_state = {"present": "uvedeno", "absent": "výslovně negováno", "uncertain": "nejisté"}[r.state]
    expl = f"{name}: {expl_state}" + (f" ({r.date})" if r.date else "") + (f" – {r.note}" if r.note else "") + "."
    if r.state == "uncertain":
        return CriterionResult(c, REVIEW, confidence=0.5, explanation=expl, evidence=r.evidence)
    present = r.state == "present"
    flags: dict = {}
    if c.kind == "exclusion":
        status = NOT_MET if present else MET
    else:
        status = MET if present == want_present else NOT_MET
    conf = max((f.confidence for f in r.evidence), default=0.7)
    if present and c.time_window_days and r.date:
        if is_stale(r.date, p.index_date, c.time_window_days):
            if c.kind == "exclusion":  # last occurrence lies outside the exclusion window -> does not exclude
                status, conf = MET, min(conf, 0.7)
                expl += f" Mimo okno {c.time_window_days} dní – nevylučuje (ověřit, zda nebylo později)."
            else:
                status = REVIEW
                flags["stale"] = True
                expl += f" Kritérium se týká posledních {c.time_window_days} dní – ověřit časování."
        elif c.kind == "exclusion":
            until = date.fromisoformat(r.date) + timedelta(days=c.time_window_days)
            flags["eligible_from"] = until.isoformat()
            expl += f" V okně {c.time_window_days} dní – vhodný nejdříve od {until:%d. %m. %Y}."
    if r.conflict:
        status = REVIEW
        flags["conflict"] = True
    if status == NOT_MET and c.qualified and not flags.get("eligible_from"):
        status = REVIEW
        flags["qualified"] = True
        expl += " Kritérium má další podmínky (např. aktivní / s novými ložisky / pro jinou malignitu) – ověřte."
    return CriterionResult(c, status, confidence=conf, explanation=expl, evidence=r.evidence, flags=flags)


def _searched(concept: str, p: PatientProfile) -> dict:
    return {"terms": lexicon.search_terms(concept), "n_docs": p.n_docs,
            "from": p.doc_range[0] if p.doc_range else None, "to": p.doc_range[1] if p.doc_range else None}


def _eval_other_malignancy(c: Criterion, p: PatientProfile) -> CriterionResult:
    trial_icd = set(c.extra_icd) if hasattr(c, "extra_icd") else set()
    r = p.resolve("primary_malignancies")
    prim = list(r.value) if r else []
    others = [x for x in prim if not any(x.startswith(t) or t.startswith(x) for t in trial_icd)]
    if len(others) <= (0 if trial_icd else 1):
        return CriterionResult(c, MET, confidence=0.7, explanation="Jiná primární malignita v MKN-10 nenalezena.")
    return CriterionResult(c, REVIEW, confidence=0.6, evidence=r.evidence[:1] if r else [],
                           explanation=f"Další malignity v MKN-10: {', '.join(others)} – ověřit, zda spadají do výjimky "
                                       f"(časové okno, léčba).")


def evaluate(c: Criterion, p: PatientProfile, trial_icd: list[str] | None = None,
             overrides: dict | None = None) -> CriterionResult:
    auto = _evaluate(c, p, trial_icd)
    auto.decided_by = _decided_by(auto)
    ov = (overrides or {}).get(c.id)
    if ov:  # the doctor's decision always wins; the automated evidence stays visible
        note = f" – {ov['note']}" if ov.get("note") else ""
        return CriterionResult(c, ov["status"], 1.0, f"Ověřeno lékařem ({ov.get('user', '?')}, {ov.get('ts', '')[:10]})"
                               f"{note}. Automaticky: {auto.explanation}", auto.evidence, "doctor", auto.searched)
    return auto


def _decided_by(r: CriterionResult) -> str:
    methods = {f.method for f in r.evidence}
    if "llm" in methods:
        return "llm"
    if r.criterion.concept in ("diagnosis", "sex", "age") or methods == {"structured"}:
        return "structured"
    return "rule"


def _evaluate(c: Criterion, p: PatientProfile, trial_icd: list[str] | None = None) -> CriterionResult:
    if c.evaluability == "not_assessable":
        return CriterionResult(c, NOT_ASSESSABLE, explanation="Z dokumentace nelze posoudit (ověří zkoušející).")
    if not c.concept:
        return CriterionResult(c, UNKNOWN, explanation="Kritérium zatím není strukturované – ruční kontrola / lokální LLM.")
    k = c.concept
    if k == "diagnosis":
        return _eval_diagnosis(c, p)
    if k == "sex":
        return _eval_sex(c, p)
    if k == "age":
        return _eval_age(c, p)
    if k == "ecog":
        return _eval_ecog(c, p)
    if k.startswith("lab."):
        return _eval_lab(c, p)
    if k == "adequate_organ_function":
        return _eval_organ(c, p)
    if k == "other_malignancy":
        c.extra_icd = trial_icd or []
        return _eval_other_malignancy(c, p)
    if k == "any_of":
        subs = [_eval_presence(Criterion(**{**c.to_dict(), "concept": x}), p, x) for x in c.value]
        best = next((s for s in subs if s.status == MET and s.confidence >= 0.7), None) or \
            next((s for s in subs if s.status == REVIEW), None) or \
            next((s for s in subs if s.status == UNKNOWN), None) or subs[0]
        names = " / ".join(lexicon.label(x) for x in c.value)
        return CriterionResult(c, best.status, best.confidence, f"Alespoň jedno z: {names}. {best.explanation}",
                               best.evidence)
    return _eval_presence(c, p, k)


# --- trial level ----------------------------------------------------------------------------------------------------

def load_trials() -> list[dict]:
    out = []
    for path in sorted(TRIALS_CRITERIA.glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        d["criteria"] = [Criterion.from_dict(c) for c in d["criteria"]]
        out.append(d)
    return out


def match_trial(trial: dict, p: PatientProfile, overrides: dict | None = None) -> TrialMatch:
    icd = trial["trial"].get("icd_prefixes") or []
    return score_results(trial["trial"], [evaluate(c, p, icd, overrides) for c in trial["criteria"]])


def fill_with_llm(m: TrialMatch, index) -> TrialMatch:
    """Re-judge unstructured criteria with the local LLM (on demand, per patient-trial pair)."""
    from .extract.llm_extract import judge
    results = []
    for r in m.results:
        if r.status == UNKNOWN and not r.criterion.concept:
            r = judge(r.criterion, index)
            r.decided_by = "llm"
        results.append(r)
    return score_results(m.trial, results)


def llm_changes(before: TrialMatch, after: TrialMatch) -> list[tuple[CriterionResult, CriterionResult]]:
    """Pairs (before, after) for criteria the local LLM judged, in criterion order."""
    # fill_with_llm keeps untouched results as the same objects, so identity tells what the LLM re-judged
    return [(a, b) for a, b in zip(before.results, after.results) if b is not a]


def _crit_label(c: Criterion) -> str:
    if not c.concept:
        return c.text[:80]
    return "Podmínka" if c.concept == "any_of" else lexicon.label(c.concept)


def score_results(trial_meta: dict, results: list[CriterionResult]) -> TrialMatch:
    icd = trial_meta.get("icd_prefixes") or []
    num = den = total = 0.0
    excluded = gate_failed = False
    to_check: list[str] = []
    for r in results:
        c = r.criterion
        if r.status == NOT_MET and (r.decided_by == "llm" or c.source == "llm"):
            # Invariant: the LLM never excludes a patient – it can only flag the criterion for the doctor.
            r.status = REVIEW
            r.explanation = "🤖 Návrh LLM: kritérium možná nesplněno – ověřte. " + r.explanation
        if r.status == NOT_ASSESSABLE:
            continue
        w = c.weight
        total += w
        if r.status == UNKNOWN:
            if c.concept:
                to_check.append(f"{_crit_label(c)}: chybí údaj")
            continue
        den += w
        s = STATUS_SCORE[r.status]
        num += w * (s if r.status != MET else 0.75 + 0.25 * min(r.confidence, 1.0))
        if r.status == NOT_MET:
            if c.hard_gate:
                gate_failed = True
            if r.confidence >= 0.75 or c.hard_gate:
                excluded = True
        if r.status == REVIEW or (r.status == MET and r.confidence < 0.7):
            to_check.append(f"{_crit_label(c)}: {r.explanation}")
    score = num / den if den else 0.0
    coverage = den / total if total else 0.0
    rank = score * (0.4 + 0.6 * coverage)
    if not icd:
        rank *= 0.5
        to_check.insert(0, "Studie nemá v seznamu diagnózu – posoudit ručně (např. extension studie).")
    if gate_failed:
        rank *= 0.05
    elif excluded:
        rank *= 0.35
    return TrialMatch(trial_meta, results, round(score, 3), round(coverage, 3), round(rank, 3), excluded,
                      gate_failed, to_check[:12])


def match_patient(p: PatientProfile, trials: list[dict] | None = None, include_gate_failed: bool = False,
                  overrides: dict | None = None) -> list[TrialMatch]:
    """overrides: {trial_id: {criterion_id: {...}}} – the doctor's confirmed/corrected criteria for this patient."""
    trials = trials or load_trials()
    out = [match_trial(t, p, (overrides or {}).get(t["trial"]["local_id"])) for t in trials]
    if not include_gate_failed:
        out = [m for m in out if not m.gate_failed]
    return sorted(out, key=lambda m: m.rank, reverse=True)


def gate_candidates(profiles: dict[str, PatientProfile], trial: dict) -> list[PatientProfile]:
    """Cheap stage A: diagnosis + sex + age gates before full evaluation."""
    gates = [c for c in trial["criteria"] if c.hard_gate]
    out = []
    for p in profiles.values():
        if all(evaluate(c, p).status != NOT_MET for c in gates):
            out.append(p)
    return out


def match_trial_all(trial: dict, profiles: dict[str, PatientProfile]) -> list[tuple[PatientProfile, TrialMatch]]:
    res = [(p, match_trial(trial, p)) for p in gate_candidates(profiles, trial)]
    return sorted(res, key=lambda x: x[1].rank, reverse=True)


# --- gap analysis: why a patient is not (yet) eligible and what would change it ---------------------------------------

ACTIONABLE = ("missing", "stale", "modifiable", "time_window")
GAP_CS = {"missing": "chybí údaj", "stale": "stará hodnota", "modifiable": "hodnota se může změnit",
          "time_window": "časové okno", "future_state": "budoucí stav", "verify": "k ověření", "hard": "nesplněno"}


@dataclass
class Gap:
    kind: str                 # missing | stale | modifiable | time_window | future_state | verify | hard
    criterion: Criterion
    detail: str
    action: str = ""
    eligible_from: str | None = None


@lru_cache(maxsize=1)
def _pathways() -> list[dict]:
    return yaml.safe_load((LEXICON_DIR / "pathways.yaml").read_text(encoding="utf-8"))


def future_note(c: Criterion, p: PatientProfile) -> str | None:
    """Trial needs a state the patient is heading towards (e.g. CRPC for a patient with HSPC on ADT)."""
    wanted = c.value if c.concept == "any_of" else [c.concept]
    for rule in _pathways():
        if rule["requires"] in wanted and c.operator == "present" and c.kind == "inclusion" and any(
                (r := p.resolve(x)) is not None and r.state == "present" for x in rule["if_any"]):
            return rule["note"]
    return None


def _main_concept(c: Criterion) -> str:
    return (c.value[0] if c.concept == "any_of" and c.value else c.concept) or ""


def gaps(m: TrialMatch, p: PatientProfile) -> list[Gap]:
    out: list[Gap] = []
    for r in m.results:
        c = r.criterion
        name = _crit_label(c)
        if r.status in (MET, NOT_ASSESSABLE):
            continue
        if r.decided_by == "doctor":
            if r.status == NOT_MET:
                out.append(Gap("hard", c, f"{name}: nesplněno (ověřeno lékařem)"))
            continue
        if r.status == UNKNOWN:
            if not c.concept:
                continue  # unstructured criterion: shown as "to verify" in the detail, not a blocking gap
            note = future_note(c, p)
            if note:
                out.append(Gap("future_state", c, note, note))
            else:
                out.append(Gap("missing", c, f"{name}: chybí údaj", lexicon.action(_main_concept(c))))
        elif r.status == REVIEW:
            if r.flags.get("stale"):
                out.append(Gap("stale", c, f"{name}: stará hodnota", "Zopakovat: " + lexicon.action(_main_concept(c))))
            else:
                out.append(Gap("verify", c, f"{name}: ověřit", "Ověřit v dokumentaci"))
        elif r.status == NOT_MET:
            if c.hard_gate:
                out.append(Gap("hard", c, r.explanation))
            elif r.flags.get("eligible_from"):
                d = r.flags["eligible_from"]
                out.append(Gap("time_window", c, f"{name}: v časovém okně", f"Vhodný nejdříve od {d}", d))
            elif c.concept and (c.concept.startswith("lab.") or c.concept == "ecog") and r.flags.get("value") is not None:
                actual, limit = float(r.flags["value"]), float(c.value)
                close = (abs(actual - limit) <= 1) if c.concept == "ecog" else \
                    abs(actual - limit) <= 0.2 * max(abs(limit), 1e-9)
                modifiable = c.concept == "ecog" or c.concept in ("lab.weight", "lab.bmi") or \
                    not c.concept.startswith("lab.") or True  # lab values can change with treatment / supportive care
                if close and modifiable:
                    out.append(Gap("modifiable", c, f"{name}: {actual:g} vs. {OP_CS.get(c.operator, c.operator)} {limit:g}",
                                   "Hodnota blízko limitu – může se změnit (léčba, podpůrná péče, kontrola)"))
                else:
                    out.append(Gap("hard", c, r.explanation))
            else:
                note = future_note(c, p)
                out.append(Gap("future_state", c, note, note) if note else Gap("hard", c, r.explanation))
    return out


def bucket(m: TrialMatch, gs: list[Gap]) -> str:
    """eligible | near | future | none – used for the trial -> patients view and for notifications."""
    if m.gate_failed:
        return "none"
    kinds = {g.kind for g in gs}
    if "hard" in kinds:
        return "none"
    if "future_state" in kinds:
        return "future"
    actionable = [g for g in gs if g.kind in ACTIONABLE]
    if not actionable and m.band == "green":
        return "eligible"
    if len(actionable) <= NEAR_MAX_GAPS and m.score >= 0.6:
        return "near"
    return "none"


def summary_line(m: TrialMatch, gs: list[Gap]) -> str:
    """One sentence for pop-ups and lists: 'splňuje 12/14 · chybí: ECOG, testosteron'."""
    met, assessable = m.assessed()
    parts = [f"splňuje {met}/{assessable} posouzených kritérií"]
    tw = [g for g in gs if g.kind == "time_window"]
    if tw:
        parts.append(f"vhodný od {min(g.eligible_from for g in tw)}")
    for kind, label in (("missing", "chybí"), ("stale", "zopakovat"), ("modifiable", "blízko limitu"),
                        ("future_state", "budoucí")):
        items = [g for g in gs if g.kind == kind]
        if items:
            names = [_crit_label(g.criterion) for g in items] if kind != "future_state" else [items[0].detail]
            parts.append(f"{label}: " + ", ".join(dict.fromkeys(names))[:120])
    llm_n = sum(r.decided_by == "llm" for r in m.results if r.status != UNKNOWN)
    if llm_n:
        parts.append(f"🤖 {llm_n}× návrh LLM")
    return " · ".join(parts)


def trial_buckets(trial: dict, profiles: dict[str, PatientProfile],
                  overrides_by_patient: dict | None = None) -> dict[str, list]:
    """Trial -> patients grouped into eligible / near / future, each item (profile, match, gaps)."""
    out: dict[str, list] = {"eligible": [], "near": [], "future": []}
    tid = trial["trial"]["local_id"]
    for p in gate_candidates(profiles, trial):
        ov = ((overrides_by_patient or {}).get(p.pseudonym) or {}).get(tid)
        m = match_trial(trial, p, ov)
        gs = gaps(m, p)
        b = bucket(m, gs)
        if b in out:
            out[b].append((p, m, gs))
    out["eligible"].sort(key=lambda x: x[1].rank, reverse=True)
    out["near"].sort(key=lambda x: (sum(g.kind in ACTIONABLE for g in x[2]), -x[1].score))
    out["future"].sort(key=lambda x: -x[1].score)
    return out


def _print_match(m: TrialMatch, verbose: bool = False) -> None:
    t = m.trial
    flag = "VYLOUČEN" if m.excluded else "kandidát"
    print(f"{t['local_id']:15s} {m.rank:5.2f}  shoda {m.score:4.0%}  pokrytí {m.coverage:4.0%}  {flag:9s} "
          f"{m.counts()}  {(t.get('brief_title') or t['title_cs'])[:70]}")
    if verbose:
        for r in m.results:
            if r.status == NOT_ASSESSABLE:
                continue
            print(f"     {r.status:13s} {r.criterion.kind[:3]} {r.explanation[:120]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patient")
    ap.add_argument("--trial")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("-v", action="store_true")
    ap.add_argument("--llm", action="store_true", help="fill unstructured criteria with the local LLM (patient mode)")
    a = ap.parse_args()
    import duckdb  # CLI on the hackathon dataset only

    con = duckdb.connect(str(DB_PATH), read_only=True)
    trials = load_trials()
    if a.patient:
        p = load_profile(con, a.patient)
        print(p.pseudonym, p.sex, p.age or p.age_band, p.icd)
        matches = match_patient(p, trials)[: a.top]
        if a.llm:
            from .extract.retrieve import SentenceIndex, patient_docs
            index = SentenceIndex(patient_docs(con, p.pseudonym))
        for m in matches:
            _print_match(m, a.v)
            if a.llm:
                m2 = fill_with_llm(m, index)
                print(f"   + lokální LLM: shoda {m.score:.0%} → {m2.score:.0%}, pokrytí {m.coverage:.0%} → "
                      f"{m2.coverage:.0%}, pořadí {m.rank:.2f} → {m2.rank:.2f}")
                for b, a2 in llm_changes(m, m2):
                    print(f"     {b.status} → {a2.status:13s} {a2.criterion.text[:70]}")
                    print(f"       {a2.explanation[:140]}")
    if a.trial:
        trial = next(t for t in trials if t["trial"]["local_id"] == a.trial)
        profiles = load_all_profiles(con)
        res = match_trial_all(trial, profiles)
        print(f"{trial['trial']['local_id']}: {len(res)} pacientů prošlo základními filtry")
        for p, m in res[: a.top]:
            print(f"  {p.pseudonym}  {m.rank:5.2f}  shoda {m.score:4.0%}  pokrytí {m.coverage:4.0%}  "
                  f"{'VYLOUČEN' if m.excluded else 'kandidát':9s} {m.counts()}")


if __name__ == "__main__":
    main()
