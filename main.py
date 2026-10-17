import asyncio
import hashlib
import json
import secrets
import threading
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import storage
from demo import demo_data
from pipeline import (
    MODEL,
    PATIENT_MODEL,
    TRIAL_MODEL,
    compare,
    extract_patient,
    extract_trial,
    match_is_current,
    patient_input,
    resolve_trial,
    trial_input,
)
from repository import DATASET, patient_record, patients, patients_by_id, trials, trials_by_id
from screening import STATUSES, Logic, TrialExtraction, badge, evaluate_logic, future_watch


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Warm the database overview in the background so its first opening does not wait.
    warm = asyncio.create_task(asyncio.to_thread(overview))
    yield
    warm.cancel()


app = FastAPI(title="TrialScreen", docs_url="/api/docs", lifespan=lifespan)
STATIC = Path(__file__).parent / "static"
MAX_JOBS = 20
jobs: dict[str, dict] = {}
job_tasks: set[asyncio.Task] = set()


@app.middleware("http")
async def local_security(request: Request, call_next):
    host = request.url.hostname
    if host not in ("127.0.0.1", "localhost", "testserver"):
        return JSONResponse(status_code=400, content={"detail": "Local demo only"})
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse(status_code=403, content={"detail": "Cross-origin requests disabled"})
    response = await call_next(request)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def public_registry(registry: dict | None) -> dict | None:
    return {key: value for key, value in registry.items() if key != "sources"} if registry else None


def load_patient(patient_id: str) -> dict:
    try:
        return patient_record(patient_id)
    except (LookupError, FileNotFoundError):
        raise HTTPException(404, "Patient not found") from None


# Criteria are stored under a hash of their input, so a key always names the same content and can be kept in memory.
_criteria_cache: dict[str, tuple[dict, TrialExtraction]] = {}


def criteria_by_key(key: str) -> tuple[dict, TrialExtraction] | None:
    if key not in _criteria_cache:
        criteria = storage.get("criteria", key)
        if not criteria:
            return None
        _criteria_cache[key] = (criteria, TrialExtraction.model_validate(criteria))
    return _criteria_cache[key]


@lru_cache(maxsize=4096)
def _facts_key(patient_id: str, mtime: int) -> str:
    return patient_input(load_patient(patient_id))["key"]


def facts_key(patient_id: str) -> str:
    """The facts key of a patient without re-reading the record while its FHIR file is unchanged."""
    path = DATASET / "fhir" / f"{patient_id}.json" if DATASET else None
    return _facts_key(patient_id, path.stat().st_mtime_ns if path and path.exists() else 0)


def cached_results(
    record: dict, facts: dict | None = None, registries: dict | None = None, trial_edits: dict | None = None
) -> list[dict]:
    """Saved comparisons whose facts and criteria still match the current inputs, assembled for the UI.

    record needs only "id" and "deceased"; facts, registries and trial_edits (a shared cache of criteria_edit)
    may be passed in to avoid repeated reads.
    """
    trial_edits = {} if trial_edits is None else trial_edits
    if facts is None:
        key = patient_input(record)["key"] if "sources" in record else facts_key(record["id"])
        facts = storage.get("facts", key)
    if not facts:
        return []
    results = []
    own_edits = storage.by_owner("edits", record["id"])
    overrides = decision_edits(own_edits)
    added = {edit["trial_id"]: edit for edit in own_edits if edit.get("scope") == "added"}
    for match in storage.by_owner("matches", record["id"]):
        registry = registries.get(match["trial_id"]) if registries is not None else \
            storage.get("registry", match["trial_id"])
        if match["trial_id"] not in trials_by_id() or not registry or registry["state"] != "resolved":
            continue
        cached = criteria_by_key(trial_input(registry)["key"])
        if not cached or not match_is_current(match, facts["key"], cached[0]["key"]):
            continue
        criteria, extraction = cached
        if match["trial_id"] not in trial_edits:
            trial_edits[match["trial_id"]] = criteria_edit(match["trial_id"])
        edit = trial_edits[match["trial_id"]]
        review = criteria_review(edit, criteria["key"])
        if review["applied"]:
            criteria = effective_criteria(criteria, edit)
            match = apply_criteria_edits(match, criteria, edit, added.get(match["trial_id"]))
        match = apply_decision_edits(match, criteria, overrides)
        # Recruitment can change without invalidating the comparison, so the alert is decided at read time.
        alert = badge(
            match["summary"]["status"],
            criteria_complete=extraction.complete,
            facts_complete=facts["complete"],
            recruiting=registry["status"] == "RECRUITING",
            site_recruiting=registry["site_recruiting"],
            deceased=record["deceased"],
        )
        results.append(
            {
                **{
                    key: match[key]
                    for key in ("patient_id", "trial_id", "assessed_at", "decisions", "future_watch")
                },
                "model": match.get("label", MODEL),
                "snapshot_at": facts["snapshot_at"],
                "summary": {**match["summary"], "badge": alert},
                "criteria_review": review,
                "criteria": {key: criteria[key] for key in ("criteria", "logic", "complete", "limitations")},
                "registry": public_registry(registry),
                "patient_facts": {"source_ids": facts["source_ids"], "complete": facts["complete"]},
                "limitations": ["Posouzení veřejných kritérií, nikoli potvrzení zařazení do studie."]
                + criteria["limitations"]
                + facts["limitations"]
                + (
                    []
                    if facts["complete"]
                    else ["Posouzen byl výběr záznamů; úplnost dokumentace není potvrzena."]
                ),
            }
        )
    return results


def decision_edits(own_edits: list[dict]) -> dict[tuple[str, str], dict]:
    """Criteria decided by a clinician for this patient (from the patient's edits), by (trial ID, criterion ID)."""
    return {
        (edit["trial_id"], edit["criterion_id"]): edit
        for edit in own_edits
        if edit.get("scope", "").startswith("decision:") and edit.get("status")
    }


def recompute_summary(match: dict, criteria: dict, decisions: list[dict], decided: set[str] = frozenset()) -> dict:
    """The match with new decisions: counts, logic status, match % and the future watch computed again.

    decided = criterion IDs a clinician decided; administrative ones then count as assessable.
    """
    counts = Counter(decision["status"] for decision in decisions)
    total = len(decisions)
    summary = {**match["summary"], "total": total, "counts": {status: counts[status] for status in STATUSES}}
    summary["status"] = evaluate_logic(
        Logic.model_validate(criteria["logic"]), {d["criterion_id"]: d["status"] for d in decisions}
    )
    summary["coverage_percent"] = round(100 * (counts["met"] + counts["not_met"]) / total) if total else 0
    denominator = total
    if "assessable" in summary:
        # Administrative criteria (consent, ...) count once the clinician has decided them.
        administrative = {
            item["id"] for item in criteria["criteria"] if item.get("tm", {}).get("evaluability") == "not_assessable"
        }
        summary["assessable"] = total - len(administrative - decided)
        summary["administrative"] = len(administrative - decided)
        denominator = summary["assessable"]
    summary["match_percent"] = round(100 * counts["met"] / denominator) if denominator else 0
    watch = future_watch(TrialExtraction.model_validate(criteria), decisions)
    return {**match, "decisions": decisions, "summary": summary, "future_watch": watch}


def apply_decision_edits(match: dict, criteria: dict, overrides: dict) -> dict:
    """Clinician decisions replace the automated ones (kept under "original") and the summary is recomputed.

    The stored comparison is never overwritten; this happens at read time, like the recruitment alert.
    """
    mine = {criterion: edit for (trial, criterion), edit in overrides.items() if trial == match["trial_id"]}
    if not mine:
        return match
    decisions = []
    for decision in match["decisions"]:
        edit = mine.get(decision["criterion_id"])
        if edit:
            detail = " · ".join(part for part in (edit.get("value"), edit.get("note")) if part)
            decision = {
                **decision,
                "status": edit["status"],
                "reason": ("Doplněno lékařem" + (f": {detail}" if detail else "")).rstrip(".") + ".",
                "action": None,
                "clinician": edit,
                "original": {key: decision[key] for key in ("status", "reason", "action")},
            }
        decisions.append(decision)
    return recompute_summary(match, criteria, decisions, set(mine))


# Clinician review of a study's criteria: confirm them, remove some, add new ones. One document per study in edits;
# the extracted criteria are never overwritten and the edit applies at read time to every patient.


def criteria_edit(trial_id: str) -> dict | None:
    return storage.get("edits", f"criteria:{trial_id}")


def edit_version(removed: list[str], added: list[dict]) -> str:
    content = json.dumps([sorted(removed), [(item["role"], item["text"]) for item in added]], ensure_ascii=False)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def criteria_review(edit: dict | None, criteria_key: str) -> dict:
    """Whether a clinician confirmed this study's criteria. An edit made for an older extraction is not applied."""
    if not edit:
        return {"verified": False, "verified_at": None, "edited": False, "applied": False, "stale": False}
    current = edit["base_key"] == criteria_key
    return {
        "verified": current and edit.get("verified", False),
        "verified_at": edit.get("verified_at") if current else None,
        "edited": bool(edit["removed"] or edit["added"]),
        "applied": current,
        "stale": not current,
    }


def added_items(edit: dict) -> list[dict]:
    return [criterion for item in edit["added"] for criterion in item["criteria"]]


def effective_criteria(criteria: dict, edit: dict) -> dict:
    """The extracted criteria without the removed ones and with the clinician's additions."""
    removed = set(edit["removed"])

    def prune(node: dict) -> dict | None:
        if node["op"] == "ref":
            return None if node["criterion_id"] in removed else node
        children = [child for child in map(prune, node.get("children", [])) if child]
        return {**node, "children": children} if children else None

    added = added_items(edit)
    kept = prune(criteria["logic"])
    refs = [{"op": "ref", "criterion_id": item["id"]} for item in added]
    logic = {"op": "all", "children": ([kept] if kept else []) + refs}
    if kept and kept["op"] == "all":
        logic = {**kept, "children": kept["children"] + refs}
    return {
        **criteria,
        "criteria": [item for item in criteria["criteria"] if item["id"] not in removed] + added,
        "logic": logic,
    }


def apply_criteria_edits(match: dict, criteria: dict, edit: dict, added: dict | None) -> dict:
    """Decisions for the edited criteria: removed ones dropped, added ones from the recomputation (or pending)."""
    computed = {d["criterion_id"]: d for d in added["decisions"]} if added and added["version"] == edit["version"] \
        else {}
    removed = set(edit["removed"])
    decisions = [decision for decision in match["decisions"] if decision["criterion_id"] not in removed]
    for item in added_items(edit):
        decisions.append(computed.get(item["id"]) or {
            "criterion_id": item["id"], "status": "missing", "reason": "Kritérium přidal lékař, přepočítává se.",
            "action": "Obnovit stránku za chvíli.", "fact_ids": [], "evidence": [],
        })
    return recompute_summary(match, criteria, decisions)


def diagnosis_icd(criteria: dict) -> list[str]:
    for item in criteria["criteria"]:
        if item.get("tm", {}).get("concept") == "diagnosis":
            return item["tm"].get("value") or []
    return []


def evaluate_added(trial_id: str, edit: dict, job: dict | None = None) -> None:
    """Evaluate the clinician's added criteria for every patient compared with the study (rules only, local)."""
    from engine_trialmatch import evaluate_criteria

    registry = storage.get("registry", trial_id)
    cached = criteria_by_key(trial_input(registry)["key"]) if registry else None
    if not cached or cached[0]["key"] != edit["base_key"]:
        return
    items, icd = added_items(edit), diagnosis_icd(cached[0])
    owners = sorted({m["patient_id"] for k, m in storage.rows("matches").items()
                     if "@" not in k and m["trial_id"] == trial_id})
    if job is not None:
        job.update(total=len(owners), message="Přepočet přidaných kritérií")
    for patient_id in owners:
        try:
            record = patient_record(patient_id)
            facts = storage.get("facts", facts_key(patient_id))
            if facts:
                decisions = evaluate_criteria(record, facts, items, icd) if items else []
                storage.put("edits", f"added:{trial_id}:{patient_id}", patient_id, {
                    "scope": "added", "trial_id": trial_id, "version": edit["version"], "decisions": decisions,
                })
        except Exception as error:  # noqa: BLE001 - one patient must not stop the recomputation
            if job is not None:
                job["errors"].append(f"{patient_id}: {type(error).__name__}")
        if job is not None:
            job["done"] += 1


@app.get("/api/bootstrap")
def bootstrap():
    registries = storage.rows("registry")
    return {
        "patients": [{key: value for key, value in item.items() if key != "raw"} for item in patients()],
        "trials": [{**trial, "registry": public_registry(registries.get(trial["id"]))} for trial in trials()],
        "model": MODEL,
        "models": {"patient": PATIENT_MODEL, "trial": TRIAL_MODEL, "comparison": MODEL},
        "dataset": {"name": "Rakathon / FN Motol a Homolka", "dates_shifted": True, "pseudonymized": True},
        "jobs": list(jobs.values())[-5:],
        "seeded": any(trial.get("synthetic") for trial in trials()),
    }


def current_facts(record: dict) -> dict | None:
    """Facts extracted from the record as it is now; None when unprocessed or the record has changed."""
    return storage.get("facts", patient_input(record)["key"])


def current_criteria(registry: dict | None) -> dict | None:
    resolved = registry and registry["state"] == "resolved"
    return storage.get("criteria", trial_input(registry)["key"]) if resolved else None


def edits(owner: str, scope: str) -> dict[str, dict]:
    """Clinician edits made against one extraction (scope = its key), by the fact or criterion ID."""
    return {edit["ref"]: edit for edit in storage.by_owner("edits", owner) if edit["scope"] == scope}


def result_summary(result: dict) -> dict:
    return {
        "trial_id": result["trial_id"],
        "assessed_at": result["assessed_at"],
        **{key: result["summary"][key] for key in ("status", "badge", "match_percent", "counts", "total")},
        "verified": result["criteria_review"]["verified"],
        # Only blocker is progression or a washout: a candidate for later.
        "future": bool(result.get("future_watch")),
        # Rule-engine results also say how many criteria were assessable from documentation.
        **{key: result["summary"][key] for key in ("assessable", "administrative") if key in result["summary"]},
    }


_overview_cache: dict = {}
_overview_lock = threading.Lock()


@app.get("/api/overview")
def overview():
    """All workbook studies and every screened patient, for browsing the whole database.

    Cached until any table changes (storage.version), so browsing is instant and edits show up immediately.
    """
    with _overview_lock:
        current = storage.version()
        if _overview_cache.get("version") != current:
            _overview_cache.update(version=current, value=build_overview())
        return _overview_cache["value"]


def build_overview():
    # Benchmark comparisons are keyed "patient:trial@date" and never shown in the UI.
    owners = {match["patient_id"] for key, match in storage.rows("matches").items() if "@" not in key}
    owners |= {facts["patient_id"] for facts in storage.rows("facts").values() if not facts.get("as_of")}
    registries = storage.rows("registry")
    trial_edits = {trial["id"]: criteria_edit(trial["id"]) for trial in trials()}
    entries = []
    for patient_id in sorted(owners & patients_by_id().keys()):
        facts = storage.get("facts", facts_key(patient_id))
        summary = patients_by_id()[patient_id]
        results = cached_results({"id": patient_id, "deceased": summary["deceased"]}, facts, registries, trial_edits)
        if not facts and not results:
            continue
        reviewed = edits(patient_id, facts["key"]).values() if facts else []
        entries.append(
            {
                "id": patient_id,
                "facts": len(facts["facts"]) if facts else None,
                "verified": sum(edit["verified"] for edit in reviewed),
                "edited": sum(edit["value"] is not None for edit in reviewed),
                "results": [result_summary(result) for result in results],
            }
        )
    studies = []
    for trial in trials():
        registry = registries.get(trial["id"])
        criteria = current_criteria(registry)
        edit = trial_edits[trial["id"]]
        review = criteria_review(edit, criteria["key"]) if criteria else None
        if review and review["applied"]:
            criteria = effective_criteria(criteria, edit)
        studies.append(
            {
                **trial,
                "registry": public_registry(registry),
                "criteria": len(criteria["criteria"]) if criteria else None,
                "criteria_review": review,
            }
        )
    return {"patients": entries, "trials": studies}


@app.get("/api/demo")
def get_demo():
    return demo_data()


@app.get("/api/patients/{patient_id}")
def get_patient(patient_id: str):
    record = load_patient(patient_id)
    return {
        **{key: value for key, value in record.items() if key not in ("raw", "sources")},
        "sources": [
            {key: value for key, value in source.items() if key != "text"}
            for source in record["sources"].values()
        ],
        "results": cached_results(record),
    }


@app.get("/api/patients/{patient_id}/facts")
def get_facts(patient_id: str):
    facts = current_facts(load_patient(patient_id))
    if not facts:
        return {"facts": None}
    corrections = edits(patient_id, facts["key"])
    return {
        **{key: facts.get(key) for key in ("model", "snapshot_at", "complete", "limitations", "synthetic")},
        "facts": [{**fact, "edit": corrections.get(fact["id"])} for fact in facts["facts"]],
    }


class Edit(BaseModel):
    value: str | None = Field(default=None, max_length=2000)
    verified: bool = False


def save_edit(owner: str, scope: str, ref: str, edit: Edit) -> dict:
    # An empty value means "unchanged"; the original extraction is never overwritten.
    payload = {
        "scope": scope,
        "ref": ref,
        "value": (edit.value or "").strip() or None,
        "verified": edit.verified,
        "edited_at": datetime.now(UTC).isoformat(),
    }
    storage.put("edits", f"{scope}:{ref}", owner, payload)
    return payload


class Confirmation(BaseModel):
    verified: bool = True


@app.put("/api/patients/{patient_id}/facts")
def confirm_facts(patient_id: str, confirmation: Confirmation):
    """Confirm (or unconfirm) every fact at once, keeping any value the clinician has corrected."""
    facts = current_facts(load_patient(patient_id))
    if not facts:
        raise HTTPException(404, "Facts not extracted")
    corrections = edits(patient_id, facts["key"])
    return {
        fact["id"]: save_edit(
            patient_id,
            facts["key"],
            fact["id"],
            Edit(value=corrections.get(fact["id"], {}).get("value"), verified=confirmation.verified),
        )
        for fact in facts["facts"]
    }


@app.put("/api/patients/{patient_id}/facts/{fact_id}")
def edit_fact(patient_id: str, fact_id: str, edit: Edit):
    facts = current_facts(load_patient(patient_id))
    if not facts or fact_id not in {fact["id"] for fact in facts["facts"]}:
        raise HTTPException(404, "Fact not found")
    return save_edit(patient_id, facts["key"], fact_id, edit)


class CriterionDecision(BaseModel):
    status: Literal["met", "not_met"] | None = None  # None removes the clinician's decision
    value: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=2000)


@app.put("/api/patients/{patient_id}/criteria/{trial_id}/{criterion_id}")
def decide_criterion(patient_id: str, trial_id: str, criterion_id: str, decision: CriterionDecision):
    """A clinician fills in a criterion the screening could not decide (or corrects one). Stored in edits."""
    results = cached_results(load_patient(patient_id))
    if not any(
        result["trial_id"] == trial_id and any(d["criterion_id"] == criterion_id for d in result["decisions"])
        for result in results
    ):
        raise HTTPException(404, "Criterion not found")
    payload = {
        "scope": f"decision:{trial_id}",
        "ref": criterion_id,
        "trial_id": trial_id,
        "criterion_id": criterion_id,
        "status": decision.status,
        "value": (decision.value or "").strip() or None,
        "note": (decision.note or "").strip() or None,
        "edited_at": datetime.now(UTC).isoformat(),
    }
    storage.put("edits", f"decision:{patient_id}:{trial_id}:{criterion_id}", patient_id, payload)
    return payload


@app.get("/api/patients/{patient_id}/sources/{source_id}")
def get_source(patient_id: str, source_id: str, trial_id: str | None = None):
    source = load_patient(patient_id)["sources"].get(source_id)
    if source is None and trial_id:
        source = (storage.get("registry", trial_id) or {}).get("sources", {}).get(source_id)
    if source is None:
        raise HTTPException(404, "Source not found")
    return source


@app.get("/api/trials/{trial_id}")
def get_trial_detail(trial_id: str):
    trial = trials_by_id().get(trial_id)
    if trial is None:
        raise HTTPException(404, "Trial not found")
    registry = storage.get("registry", trial_id)
    criteria = current_criteria(registry)
    edit = criteria_edit(trial_id)
    review = criteria_review(edit, criteria["key"]) if criteria else None
    removed = []
    if review and review["applied"]:
        removed = [item for item in criteria["criteria"] if item["id"] in set(edit["removed"])]
        criteria = effective_criteria(criteria, edit)
    added = {item["id"] for item in added_items(edit)} if review and review["applied"] else set()
    if criteria:
        criteria = {**criteria, "criteria": [{**item, "added": item["id"] in added} for item in criteria["criteria"]]}
    return {**trial, "registry": registry, "criteria": criteria, "removed": removed, "criteria_review": review}


class CriteriaChange(BaseModel):
    role: Literal["inclusion", "exclusion"]
    text: str = Field(min_length=2, max_length=500)


class CriteriaEdit(BaseModel):
    removed: list[str] = Field(default_factory=list, max_length=500)
    added: list[CriteriaChange] = Field(default_factory=list, max_length=50)


class CriteriaVerification(BaseModel):
    verified: bool = True


def extracted_criteria(trial_id: str) -> dict:
    if trial_id not in trials_by_id():
        raise HTTPException(404, "Trial not found")
    criteria = current_criteria(storage.get("registry", trial_id))
    if not criteria:
        raise HTTPException(409, "Criteria not extracted yet")
    return criteria


@app.put("/api/trials/{trial_id}/criteria")
async def edit_criteria(trial_id: str, change: CriteriaEdit):
    """A clinician removes or adds criteria of a study. Any change needs a new confirmation."""
    from engine_trialmatch import clinician_criteria

    criteria = extracted_criteria(trial_id)
    known = {item["id"] for item in criteria["criteria"]}
    if not set(change.removed) <= known:
        raise HTTPException(404, "Criterion not found")
    previous = criteria_edit(trial_id)
    previous = previous if previous and previous["base_key"] == criteria["key"] else None
    icd = diagnosis_icd(criteria)
    previous_added = (previous or {}).get("added", [])
    # Kept additions keep their IDs (their per-patient decisions stay valid); new ones are numbered after them.
    number = max((entry["number"] for entry in previous_added), default=0)
    added = []
    for item in change.added:
        text = item.text.strip()
        kept = next((old for old in previous_added if (old["role"], old["text"]) == (item.role, text)), None)
        if not kept:
            number += 1
            kept = {"number": number, "role": item.role, "text": text,
                    "criteria": clinician_criteria(trial_id, item.role, text, number, icd)}
        added.append(kept)
    removed = sorted(set(change.removed))
    version = edit_version(removed, added)
    edit = {
        "scope": "criteria",
        "trial_id": trial_id,
        "base_key": criteria["key"],
        "removed": removed,
        "added": added,
        "version": version,
        "verified": bool(previous and previous["version"] == version and previous.get("verified")),
        "verified_at": previous.get("verified_at") if previous and previous["version"] == version else None,
        "edited_at": datetime.now(UTC).isoformat(),
    }
    storage.put("edits", f"criteria:{trial_id}", trial_id, edit)
    job = new_job("criteria")
    task = asyncio.create_task(asyncio.to_thread(run_criteria_job, job, trial_id, edit))
    job_tasks.add(task)
    task.add_done_callback(job_tasks.discard)
    return {"edit": edit, "job": job}


def run_criteria_job(job: dict, trial_id: str, edit: dict) -> None:
    job.update(state="running")
    evaluate_added(trial_id, edit, job)
    job.update(state="done", message="Dokončeno s upozorněním" if job["errors"] else "Dokončeno")


@app.put("/api/trials/{trial_id}/criteria/verified")
def verify_criteria(trial_id: str, verification: CriteriaVerification):
    """A clinician confirms (or withdraws the confirmation of) the study's criteria as they are now."""
    criteria = extracted_criteria(trial_id)
    edit = criteria_edit(trial_id)
    if not edit or edit["base_key"] != criteria["key"]:
        edit = {"scope": "criteria", "trial_id": trial_id, "base_key": criteria["key"], "removed": [], "added": [],
                "version": edit_version([], [])}
    edit = {**edit, "verified": verification.verified,
            "verified_at": datetime.now(UTC).isoformat() if verification.verified else None}
    storage.put("edits", f"criteria:{trial_id}", trial_id, edit)
    return criteria_review(edit, criteria["key"])


class JobRequest(BaseModel):
    kind: str
    patient_id: str | None = None
    trial_ids: list[str] = Field(default_factory=list, max_length=5)


async def run_job(job: dict, request: JobRequest) -> None:
    job.update(state="running", message="Spouštění")
    try:
        selected = [trials_by_id()[identifier] for identifier in request.trial_ids]
        if request.kind == "registry":
            selected = selected or trials()
            job["total"] = len(selected)
            # Sequential on purpose: the public registry rate-limits parallel requests.
            for trial in selected:
                try:
                    result = await resolve_trial(trial, refresh=True)
                    job["message"] = f"{trial['protocol']}: {result['state']}"
                except Exception as error:  # noqa: BLE001 - job boundary; record and continue
                    job["errors"].append(f"{trial['protocol']}: {type(error).__name__}")
                job["done"] += 1
        else:
            assert request.patient_id
            job["total"] = len(selected)
            job["message"] = "Extrakce údajů z dokumentace"
            record = await asyncio.to_thread(patient_record, request.patient_id)
            facts = await extract_patient(record)
            for trial in selected:
                try:
                    job["message"] = f"{trial['protocol']}: ověření registru"
                    registry = await resolve_trial(trial)
                    if registry["state"] != "resolved":
                        job["errors"].append(f"{trial['protocol']}: studie není propojena s registrem")
                        continue
                    job["message"] = f"{trial['protocol']}: extrakce kritérií"
                    criteria = await extract_trial(trial, registry)
                    job["message"] = f"{trial['protocol']}: porovnání"
                    await compare(trial, facts, criteria)
                except Exception as error:  # noqa: BLE001 - job boundary; record and continue
                    # Error messages can echo model output, so only the type is surfaced.
                    job["errors"].append(f"{trial['protocol']}: zpracování selhalo ({type(error).__name__})")
                finally:
                    job["done"] += 1
        job.update(state="done", message="Dokončeno s upozorněním" if job["errors"] else "Dokončeno")
    except Exception as error:  # noqa: BLE001 - job boundary; record and continue
        job.update(
            state="error",
            message=f"Zpracování selhalo ({type(error).__name__}). "
            "Zkontrolujte připojení, konfiguraci a zdrojové podklady.",
        )


def new_job(kind: str) -> dict:
    for finished in list(jobs)[: max(0, len(jobs) - MAX_JOBS + 1)]:
        del jobs[finished]
    job_id = secrets.token_hex(8)
    job = {
        "id": job_id,
        "state": "queued",
        "kind": kind,
        "done": 0,
        "total": 0,
        "message": "Čeká na zpracování",
        "errors": [],
    }
    jobs[job_id] = job
    return job


@app.post("/api/jobs")
async def create_job(request: JobRequest):
    if request.kind not in ("registry", "screen"):
        raise HTTPException(400, "Invalid job kind")
    if any(job["state"] in ("queued", "running") for job in jobs.values()):
        raise HTTPException(409, "Another job is already running")
    if request.kind == "screen" and (not request.patient_id or not request.trial_ids):
        raise HTTPException(400, "Choose a patient and at least one trial")
    if request.patient_id and request.patient_id not in patients_by_id():
        raise HTTPException(404, "Patient not found")
    if any(identifier not in trials_by_id() for identifier in request.trial_ids):
        raise HTTPException(404, "Trial not found")
    job = new_job(request.kind)
    task = asyncio.create_task(run_job(job, request))
    job_tasks.add(task)
    task.add_done_callback(job_tasks.discard)
    return job


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    return jobs[job_id]


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
