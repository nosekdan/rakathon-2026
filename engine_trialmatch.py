"""Rule-based screening engine (trialmatch) behind the same three pipeline stages as the language models.

Select it per stage in .env, like a model deployment:

    PATIENT_MODEL_DEPLOYMENT=trialmatch   TRIAL_MODEL_DEPLOYMENT=trialmatch   MODEL_DEPLOYMENT=trialmatch

`trialmatch+llm` as MODEL_DEPLOYMENT additionally lets a LOCAL model judge criteria the rules cannot structure
(OpenAI-compatible endpoint from TM_LLM_BASE_URL / TM_LLM_MODEL, e.g. Ollama at http://127.0.0.1:11434/v1;
non-local hosts are refused). The local model never excludes a patient: its "no" becomes "unknown".

Outputs have exactly the shape of the model stages (facts, criteria, matches), so storage, the UI, clinician edits
and the benchmark work unchanged. Facts are found by Czech/English lexicons and rules (trialmatch/lexicon), each
with an exact quote located in its source; comparisons are deterministic code.
"""

import json
import re
from datetime import UTC, datetime

from screening import (
    Decision,
    Evidence,
    TrialExtraction,
    future_watch,
    locate_evidence,
    summarize,
    verify_decisions,
)
from trialmatch import lexicon
from trialmatch.match import evaluate, fill_with_llm
from trialmatch.profile import profile_from_record
from trialmatch.schema import MET, NOT_ASSESSABLE, NOT_MET, REVIEW, UNKNOWN, Criterion
from trialmatch.trials.structure import structure_registry_text

ENGINE = "trialmatch"
FACTS_LIMITATION = "Údaje vybrány pravidly (slovník a vzory), nikoli jazykovým modelem; každý má citaci ze zdroje."
STATE_CS = {"present": "ano", "absent": "ne (negováno v dokumentaci)", "uncertain": "nejisté / plánované"}
OP_CS = {">=": "≥", "<=": "≤", ">": ">", "<": "<", "=": "="}
# Concepts the comparison may need even though they are not facts in the text.
DEMOGRAPHIC = ("age", "sex")


def is_engine(deployment: str) -> bool:
    return deployment.split("+")[0] == ENGINE


# Stage 1: patient -> facts -------------------------------------------------------------------------------------------


def _lab_ranges(text: str) -> tuple[float | None, float | None]:
    """repository.py writes 'Reference: [<FHIR referenceRange JSON>]' into lab sources."""
    match = re.search(r"Reference:\s*(\[.*\])", text or "")
    try:
        ranges = json.loads(match.group(1)) if match else []
    except ValueError:
        ranges = []
    first = ranges[0] if ranges else {}
    return first.get("low", {}).get("value"), first.get("high", {}).get("value")


def _profile(record: dict):
    raw = record["raw"]
    age = int(raw["vek"]) if str(raw.get("vek") or "").isdigit() else None
    labs = []
    for lab in record["latest_labs"]:
        low, high = _lab_ranges(record["sources"].get(lab["source_id"], {}).get("text", ""))
        labs.append({**lab, "lower": low, "upper": high})
    return profile_from_record(
        record["sources"],
        pseudonym=record["id"],
        sex=raw.get("pohlavi") or record.get("sex"),
        age=age,
        age_band=raw.get("vek_pasmo") or None,
        icd=(raw.get("diagnozy") or "").split(),
        labs=labs,
        snapshot_at=record.get("snapshot_at"),
    )


def _fact_key(fact) -> str:
    return f"{fact.concept}|{fact.source_id}|{list(fact.span) if fact.span else ''}|{fact.value}"


def _value(fact, resolved) -> str:
    if fact.concept.startswith(("lab.", "vitals.")) or resolved.state == "value":
        return f"{fact.value:g} {fact.unit or ''}".strip() if isinstance(fact.value, (int, float)) else str(fact.value)
    if fact.polarity == "negated":
        return STATE_CS["absent"]
    if fact.certainty != "certain":
        return STATE_CS["uncertain"]
    return STATE_CS["present"]


def _evidence(fact, record: dict) -> dict | None:
    sources = record["sources"]
    if fact.source_type == "diagnosis":
        raw = record["raw"].get("diagnozy", "")
        return locate_evidence(Evidence(source_id="cohort", quote=f"diagnozy: {raw}"), sources)
    if fact.source_type == "lab":
        source = sources.get(fact.source_id)
        if not source:
            return None
        return locate_evidence(Evidence(source_id=source["id"], quote=source["text"].split("\n")[0].strip()), sources)
    source = sources.get(fact.source_id)
    if not source:
        return None
    for quote in (fact.snippet, source["text"][fact.span[0]:fact.span[1]] if fact.span else None):
        if quote and quote.strip():
            found = locate_evidence(Evidence(source_id=source["id"], quote=quote.strip()), sources)
            if found:
                return found
    return None


def extract_patient(record: dict, task: dict, deployment: str) -> dict:
    """Same result shape as pipeline.extract_patient; one fact per piece of evidence the comparison can cite."""
    profile = _profile(record)
    facts, dropped = [], 0

    def add(name, value, date, evidence, tm):
        if evidence:
            facts.append({"id": f"F{len(facts) + 1}", "name": name, "value": value, "date": date,
                          "evidence": evidence, "tm": tm})

    cohort = record["sources"]["cohort"]
    raw = record["raw"]
    age_column = "vek" if raw.get("vek") else "vek_pasmo"
    if raw.get(age_column):
        add("Věk", str(raw[age_column]), None,
            locate_evidence(Evidence(source_id="cohort", quote=f"{age_column}: {raw[age_column]}"), {"cohort": cohort}),
            {"key": "demographic|age"})
    if raw.get("pohlavi"):
        add("Pohlaví", {"male": "muž", "female": "žena"}.get(raw["pohlavi"], raw["pohlavi"]), None,
            locate_evidence(Evidence(source_id="cohort", quote=f"pohlavi: {raw['pohlavi']}"), {"cohort": cohort}),
            {"key": "demographic|sex"})
    seen = set()
    for concept in profile.concepts():
        resolved = profile.resolve(concept)
        if resolved is None or concept.startswith("drug."):
            continue
        for fact in resolved.evidence:
            key = _fact_key(fact)
            if key in seen:
                continue
            seen.add(key)
            evidence = _evidence(fact, record)
            dropped += evidence is None
            add(lexicon.label(concept), _value(fact, resolved), fact.date, evidence,
                {"key": key, "concept": concept, "polarity": fact.polarity, "certainty": fact.certainty,
                 "method": fact.method})
    return {
        "key": task["key"],
        "patient_id": record["id"],
        "model": deployment,
        "snapshot_at": record["snapshot_at"],
        "as_of": record.get("as_of"),
        "source_ids": [source["id"] for source in task["payload"]["sources"]],
        # The rules read every record, not a budgeted selection.
        "complete": dropped == 0,
        "dropped_citations": dropped,
        "limitations": [FACTS_LIMITATION],
        "facts": facts,
    }


# Stage 2: trial -> criteria ------------------------------------------------------------------------------------------


def _requirement(c: Criterion) -> str:
    """Normalized as a condition the patient must satisfy (exclusions as required absence), in Czech."""
    k = c.concept
    if not k:
        prefix = "Nelze posoudit z dokumentace" if c.evaluability == "not_assessable" else "Nestrukturováno"
        return f"{prefix}: {c.text[:220]}"
    if k == "diagnosis":
        return f"Diagnóza {', '.join(c.value or [])} (MKN-10)"
    if k == "sex":
        return f"Pohlaví: {'muž' if c.value == 'male' else 'žena'}"
    if k == "age":
        return f"Věk {OP_CS.get(c.operator, c.operator)} {c.value:g} let"
    if k == "ecog" or k.startswith("lab."):
        unit = "× ULN" if c.uln else (c.unit or "")
        return f"{lexicon.label(k)} {OP_CS.get(c.operator, c.operator)} {c.value:g} {unit}".strip()
    if k == "any_of":
        return "Alespoň jedno z: " + ", ".join(lexicon.label(x) for x in c.value or [])
    if k == "adequate_organ_function":
        return lexicon.label(k)
    if c.kind == "exclusion" or c.operator == "absent":
        return f"Bez: {lexicon.label(k)}"
    return f"{lexicon.label(k)}: přítomno"


def _trigger(c: Criterion) -> str:
    if c.kind == "exclusion" and c.time_window_days and c.concept:
        return "washout"
    if c.kind == "inclusion" and c.concept in ("status.progression", "prostate.crpc"):
        return "progression"
    return "none"


def _locate_loose(quote: str, text: str) -> dict | None:
    """Find a quote ignoring the registry's markdown escapes (e.g. 10\\^9/L) and whitespace; return the exact
    original span, so the quote stays a contiguous substring of the source."""
    kept = [i for i, ch in enumerate(text) if ch != "\\"]
    plain = "".join(text[i] for i in kept)
    words = quote.replace("\\", "").split()
    match = re.search(r"\s+".join(map(re.escape, words)), plain) if words else None
    if not match:
        return None
    start, end = kept[match.start()], kept[match.end() - 1] + 1
    return {"source_id": "registry", "quote": text[start:end], "start": start, "end": end}


def _criterion_source(c: Criterion, sources: dict) -> dict:
    candidates = [c.text, c.text.split(": ", 1)[-1]]
    if c.text_span:
        candidates.append(c.text[c.text_span[0]:c.text_span[1]])
    for quote in candidates:
        if quote.strip():
            found = locate_evidence(Evidence(source_id="registry", quote=quote.strip()), sources) or \
                _locate_loose(quote.strip(), sources["registry"]["text"])
            if found:
                return found
    return {"source_id": "registry", "quote": c.text[:300] or "—", "unverified": True}


def extract_trial(trial: dict, registry: dict, task: dict, deployment: str) -> dict:
    sources = {"registry": registry["sources"]["registry"]}
    criteria = structure_registry_text(trial["id"], sources["registry"]["text"], trial.get("diagnosis", ""),
                                       trial.get("title", ""))
    items = []
    for c in criteria:
        items.append({
            "id": c.id.split(":", 1)[1],
            "requirement": _requirement(c),
            "role": c.kind,
            "source": _criterion_source(c, sources),
            "trigger": _trigger(c),
            "tm": c.to_dict(),
        })
    unstructured = sum(1 for c in criteria if not c.concept and c.evaluability != "not_assessable")
    # The diagnosis gate comes from the FNMH workbook, not from the registry text, so it cannot be quoted there.
    unverified = sum(1 for item in items if item["source"].get("unverified") and item["tm"]["source"] != "registry")
    limitations = ["Brána diagnózy (MKN-10) vychází ze seznamu studií FNMH, ne z textu registru."]
    if unstructured:
        limitations.append(f"{unstructured} kritérií pravidla nestrukturovala – posoudí lékař (nebo lokální LLM).")
    if unverified:
        limitations.append(f"Citace {unverified} kritérií nebyla nalezena v textu registru.")
    return {
        "key": task["key"],
        "trial_id": trial["id"],
        "nct_id": registry["nct_id"],
        "model": deployment,
        "criteria": items,
        "logic": {"op": "all", "children": [{"op": "ref", "criterion_id": item["id"]} for item in items]},
        "complete": not unverified,
        "limitations": limitations,
    }


# Stage 3: facts x criteria -> decisions ------------------------------------------------------------------------------


def _decision(cid: str, r, fact_ids: dict[str, str]) -> Decision:
    c = r.criterion
    cited = list(dict.fromkeys(fact_ids[k] for k in (_fact_key(f) for f in r.evidence) if k in fact_ids))
    if c.concept in DEMOGRAPHIC and f"demographic|{c.concept}" in fact_ids:
        cited.append(fact_ids[f"demographic|{c.concept}"])
    main = (c.value[0] if c.concept == "any_of" and c.value else c.concept) or ""
    if r.status == NOT_ASSESSABLE:
        return Decision(criterion_id=cid, status="unknown", reason="Z dokumentace nelze posoudit.",
                        action="Ověří zkoušející.")
    if r.status == UNKNOWN or (r.status == MET and not cited):
        # Nothing found is not evidence of absence: an exclusion "not found" is missing, not met.
        action = lexicon.action(main) if main else "Ruční kontrola kritéria."
        return Decision(criterion_id=cid, status="missing", reason=r.explanation, action=action)
    if r.status == REVIEW:
        return Decision(criterion_id=cid, status="unknown", reason=r.explanation, fact_ids=cited,
                        action="Ověřit v původní dokumentaci.")
    if r.status == NOT_MET and r.decided_by == "llm":  # the local model never excludes
        return Decision(criterion_id=cid, status="unknown", reason=r.explanation, fact_ids=cited,
                        action="Ověřit v původní dokumentaci.")
    return Decision(criterion_id=cid, status="met" if r.status == MET else "not_met", reason=r.explanation,
                    fact_ids=cited)


def compare(trial: dict, facts: dict, criteria: dict, deployment: str, model_version: str) -> dict:
    from repository import patient_record

    record = patient_record(facts["patient_id"], facts.get("as_of"))
    profile = _profile(record)
    ours = [Criterion.from_dict(item["tm"]) for item in criteria["criteria"]]
    trial_icd = next((c.value for c in ours if c.concept == "diagnosis"), []) or []
    results = [evaluate(c, profile, trial_icd) for c in ours]
    if deployment.endswith("+llm"):
        from trialmatch.config import NOTIFY_MIN
        from trialmatch.extract.retrieve import SentenceIndex
        from trialmatch.match import score_results

        ruled = score_results({"icd_prefixes": trial_icd}, results)
        # The local model is slow on CPU (~5 s per criterion): only spend it on promising pairs.
        if not ruled.gate_failed and ruled.rank >= NOTIFY_MIN:
            notes = [(s["id"], (s.get("date") or "")[:10] or None, s["text"]) for s in record["sources"].values()
                     if s["kind"] in ("DocumentReference", "DiagnosticReport")]
            results = fill_with_llm(ruled, SentenceIndex(notes)).results
    fact_ids = {fact["tm"]["key"]: fact["id"] for fact in facts["facts"] if fact.get("tm")}
    decisions = [_decision(item["id"], r, fact_ids) for item, r in zip(criteria["criteria"], results)]
    extraction = TrialExtraction.model_validate(criteria)
    verified = verify_decisions(extraction.criteria, decisions, {fact["id"]: fact for fact in facts["facts"]})
    summary = summarize(extraction, verified)
    # Percent of criteria assessable from documentation: administrative conditions (consent, contraception, ...)
    # are settled at screening for everyone and leave the denominator; silence is never counted as met.
    administrative = sum(1 for item in criteria["criteria"] if item["tm"].get("evaluability") == "not_assessable")
    assessable = summary["total"] - administrative
    summary.update(
        match_percent=round(100 * summary["counts"]["met"] / assessable) if assessable else 0,
        assessable=assessable,
        administrative=administrative,
    )
    return {
        "patient_id": facts["patient_id"],
        "trial_id": trial["id"],
        "facts_key": facts["key"],
        "criteria_key": criteria["key"],
        "model": deployment,
        "label": "Pravidla trialmatch" + (" + lokální LLM" if deployment.endswith("+llm") else ""),
        "pipeline_version": model_version,
        "assessed_at": datetime.now(UTC).isoformat(),
        "summary": summary,
        "decisions": verified,
        "future_watch": future_watch(extraction, verified),
    }
