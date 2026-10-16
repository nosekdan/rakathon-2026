import asyncio
import secrets
from pathlib import Path

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
from repository import patient_record, patients, patients_by_id, trials, trials_by_id
from screening import TrialExtraction, badge

app = FastAPI(title="TrialScreen", docs_url="/api/docs")
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


def cached_results(record: dict) -> list[dict]:
    """Saved comparisons whose facts and criteria still match the current inputs, assembled for the UI."""
    facts = storage.get("facts", patient_input(record)["key"])
    if not facts:
        return []
    results = []
    for match in storage.by_owner("matches", record["id"]):
        registry = storage.get("registry", match["trial_id"])
        if match["trial_id"] not in trials_by_id() or not registry or registry["state"] != "resolved":
            continue
        criteria = storage.get("criteria", trial_input(registry)["key"])
        if not criteria or not match_is_current(match, facts["key"], criteria["key"]):
            continue
        extraction = TrialExtraction.model_validate(criteria)
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
                "model": MODEL,
                "snapshot_at": facts["snapshot_at"],
                "summary": {**match["summary"], "badge": alert},
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
    }


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
    resolved = registry and registry["state"] == "resolved"
    return {
        **trial,
        "registry": registry,
        "criteria": storage.get("criteria", trial_input(registry)["key"]) if resolved else None,
    }


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
            semaphore = asyncio.Semaphore(4)

            async def refresh(trial: dict) -> None:
                async with semaphore:
                    try:
                        result = await resolve_trial(trial, refresh=True)
                        job["message"] = f"{trial['protocol']}: {result['state']}"
                    except Exception as error:  # noqa: BLE001 - job boundary; record and continue
                        job["errors"].append(f"{trial['protocol']}: {type(error).__name__}")
                    job["done"] += 1

            await asyncio.gather(*(refresh(trial) for trial in selected))
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
    for finished in list(jobs)[: max(0, len(jobs) - MAX_JOBS + 1)]:
        del jobs[finished]
    job_id = secrets.token_hex(8)
    job = {
        "id": job_id,
        "state": "queued",
        "kind": request.kind,
        "done": 0,
        "total": 0,
        "message": "Čeká na zpracování",
        "errors": [],
    }
    jobs[job_id] = job
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
