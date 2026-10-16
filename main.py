import asyncio
import secrets
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import APIError
from pydantic import BaseModel, Field

from demo import demo_data
from pipeline import (
    MODEL,
    PATIENT_MODEL,
    TRIAL_MODEL,
    VERSION,
    attach_public_source,
    compare,
    extract_patient,
    extract_trial,
    resolve_trial,
    sync_registry,
)
from repository import get_trial, load_cache, patient_record, patients, save_cache, trials

app = FastAPI(title="TrialScreen", docs_url="/api/docs")
STATIC = Path(__file__).parent / "static"
jobs: dict[str, dict] = {}
job_tasks: set[asyncio.Task] = set()
pipeline_lock = asyncio.Lock()


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


def public_trial(trial: dict) -> dict:
    registry = load_cache("registry", trial["id"])
    return {
        **trial,
        "registry": {key: value for key, value in registry.items() if key not in ("sources", "identity")}
        if registry
        else None,
    }


def cached_results(record: dict) -> list[dict]:
    results = []
    for trial in trials():
        result = load_cache("matches", record["id"] + ":" + trial["id"])
        registry = load_cache("registry", trial["id"])
        if (
            result
            and registry
            and result.get("patient_version") == record["version"]
            and result.get("registry_version") == registry.get("version")
            and result.get("pipeline_version") == VERSION
            and result.get("model") == MODEL
            and result.get("patient_model", result.get("model")) == PATIENT_MODEL
            and result.get("trial_model", result.get("model")) == TRIAL_MODEL
        ):
            results.append({key: value for key, value in result.items() if key != "sources"})
    return results


@app.get("/api/bootstrap")
def bootstrap():
    return {
        "patients": [{key: value for key, value in item.items() if key != "raw"} for item in patients()],
        "trials": [public_trial(item) for item in trials()],
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
    try:
        record = patient_record(patient_id)
    except (StopIteration, FileNotFoundError):
        raise HTTPException(404, "Patient not found") from None
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
    try:
        record = patient_record(patient_id)
        source = record["sources"].get(source_id)
        if not source and trial_id:
            registry = load_cache("registry", trial_id) or {}
            source = registry.get("sources", {}).get(source_id)
        if not source:
            raise HTTPException(404, "Source not found")
        return source
    except StopIteration:
        raise HTTPException(404, "Patient not found") from None


@app.get("/api/trials/{trial_id}")
def get_trial_detail(trial_id: str):
    try:
        return {**public_trial(get_trial(trial_id)), "registry": load_cache("registry", trial_id)}
    except StopIteration:
        raise HTTPException(404, "Trial not found") from None


class JobRequest(BaseModel):
    kind: str
    patient_id: str | None = None
    trial_ids: list[str] = Field(default_factory=list, max_length=5)


async def run_job(job_id: str, request: JobRequest):
    job = jobs[job_id]
    async with pipeline_lock:
        job.update(state="running", message="Spouštění")
        try:
            if request.kind == "registry":
                selected = (
                    [get_trial(identifier) for identifier in request.trial_ids]
                    if request.trial_ids
                    else trials()
                )
                job["total"] = len(selected)

                def progress(protocol, state):
                    job["done"] += 1
                    job["message"] = f"{protocol}: {state}"

                await sync_registry(selected, progress)
            else:
                record = patient_record(request.patient_id)
                selected = [get_trial(identifier) for identifier in request.trial_ids]
                job["total"] = len(selected)
                job["message"] = "Extrakce údajů z dokumentace"
                facts = await extract_patient(record)
                for trial in selected:
                    job["message"] = f"{trial['protocol']}: ověření registru"
                    registry = await resolve_trial(trial)
                    if not registry.get("clinical_eu_verified"):
                        job["errors"].append(
                            f"{trial['protocol']}: chybí ověřené propojení ClinicalTrials.eu"
                        )
                        job["done"] += 1
                        continue
                    job["message"] = f"{trial['protocol']}: extrakce kritérií"
                    criteria = await extract_trial(registry)
                    job["message"] = f"{trial['protocol']}: porovnání s dokumentací"
                    await compare(record, trial, registry, criteria, facts)
                    job["done"] += 1
            job.update(state="done", message="Dokončeno" if not job["errors"] else "Dokončeno s upozorněním")
        except (httpx.HTTPError, APIError, ValueError, OSError, LookupError, RuntimeError) as error:
            job.update(
                state="error",
                message=f"Zpracování selhalo ({type(error).__name__}). Zkontrolujte připojení, konfiguraci a zdrojové podklady.",
            )


@app.post("/api/jobs")
async def create_job(request: JobRequest):
    if request.kind not in ("registry", "screen"):
        raise HTTPException(400, "Invalid job kind")
    if any(job["state"] in ("queued", "running") for job in jobs.values()):
        raise HTTPException(409, "Another job is already running")
    if request.kind == "screen" and (not request.patient_id or not request.trial_ids):
        raise HTTPException(400, "Choose a patient and at least one trial")
    if request.patient_id and not any(item["id"] == request.patient_id for item in patients()):
        raise HTTPException(404, "Patient not found")
    if any(not any(item["id"] == identifier for item in trials()) for identifier in request.trial_ids):
        raise HTTPException(404, "Trial not found")
    job_id = secrets.token_hex(8)
    jobs[job_id] = {
        "id": job_id,
        "state": "queued",
        "kind": request.kind,
        "done": 0,
        "total": 0,
        "message": "Čeká na zpracování",
        "errors": [],
    }
    task = asyncio.create_task(run_job(job_id, request))
    job_tasks.add(task)
    task.add_done_callback(job_tasks.discard)
    return jobs[job_id]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")
    return jobs[job_id]


class PublicSourceImport(BaseModel):
    trial_id: str
    url: str
    text: str = Field(min_length=100, max_length=200000)


@app.post("/api/registry/import")
def import_public_source(payload: PublicSourceImport):
    registry = load_cache("registry", payload.trial_id)
    if not registry or not registry.get("identity"):
        raise HTTPException(400, "Resolve the registry identifier first")
    try:
        attach_public_source(registry, payload.url, payload.text)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    save_cache("registry", payload.trial_id, registry)
    return {"verified": True, "nct_id": registry["nct_id"]}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
