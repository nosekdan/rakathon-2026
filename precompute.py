import argparse
import asyncio
import json
from datetime import UTC, datetime
from time import perf_counter

from openai import APIError

from pipeline import PATIENT_MODEL, TRIAL_MODEL, VERSION, extract_patient, extract_trial
from repository import (
    load_cache,
    patient_record,
    patient_version,
    patients,
    save_cache,
    select_sources,
    trials,
)


def build_plan(patient_ids: list[str], trial_ids: list[str], max_new: int = 2) -> list[dict]:
    patient_ids = list(dict.fromkeys(patient_ids))
    trial_ids = list(dict.fromkeys(trial_ids))
    if not 0 <= max_new <= 10 or len(patient_ids) > 5 or len(trial_ids) > 5:
        raise ValueError("Pilot limit: five patients, five studies and at most ten new extractions")
    if not patient_ids and not trial_ids:
        raise ValueError("Select explicit --patient or --trial IDs; there is no whole-cohort mode")
    cohort = {patient["id"]: patient for patient in patients()} if patient_ids else {}
    trial_map = {trial["id"]: trial for trial in trials()} if trial_ids else {}
    plan = []
    for identifier in patient_ids:
        if identifier not in cohort:
            raise ValueError("Unknown patient ID")
        version = patient_version(cohort[identifier])
        key = f"{PATIENT_MODEL}:{VERSION}:{identifier}:{version}"
        plan.append(
            {
                "kind": "patient",
                "id": identifier,
                "model": PATIENT_MODEL,
                "version": version,
                "cache_hit": load_cache("facts", key) is not None,
            }
        )
    for identifier in trial_ids:
        if identifier not in trial_map:
            raise ValueError("Unknown study ID")
        registry = load_cache("registry", identifier)
        if not registry or not registry.get("clinical_eu_verified") or not registry.get("version"):
            raise ValueError("Selected study needs a cached, identifier-verified public registry source")
        key = f"{TRIAL_MODEL}:{VERSION}:{registry['version']}"
        plan.append(
            {
                "kind": "trial",
                "id": identifier,
                "model": TRIAL_MODEL,
                "version": registry["version"],
                "cache_hit": load_cache("criteria", key) is not None,
            }
        )
    if sum(not item["cache_hit"] for item in plan) > max_new:
        raise ValueError("Plan exceeds --max-new; reduce the selection or explicitly raise the pilot limit")
    return plan


async def execute_plan(plan: list[dict]) -> dict:
    report: dict = {
        "started_at": datetime.now(UTC).isoformat(),
        "pipeline_version": VERSION,
        "comparisons": 0,
        "items": [],
    }
    for item in plan:
        started = perf_counter()
        result = dict(item)
        try:
            if item["kind"] == "patient":
                record = patient_record(item["id"])
                if record["version"] != item["version"]:
                    raise ValueError("Patient changed after planning")
                sources, complete = select_sources(record)
                result.update(
                    selected_characters=sum(len(source["text"]) for source in sources),
                    source_selection_complete=complete,
                )
                facts = await extract_patient(record)
                result.update(
                    fact_count=len(facts["facts"]),
                    dropped_citations=facts["dropped_citations"],
                )
            else:
                registry = load_cache("registry", item["id"])
                if not registry or registry.get("version") != item["version"]:
                    raise ValueError("Registry changed after planning")
                criteria = await extract_trial(registry)
                result.update(criterion_count=len(criteria.criteria), criteria_complete=criteria.complete)
            result["state"] = "done"
        except (APIError, ValueError, OSError, LookupError) as error:
            result.update(state="error", error_type=type(error).__name__)
        result["seconds"] = round(perf_counter() - started, 3)
        report["items"].append(result)
        save_cache("precompute", report["started_at"], report)
        print(json.dumps(result), flush=True)
        if result["state"] == "error":
            break
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded extraction-only pilot; dry run unless --execute")
    parser.add_argument("--patient", action="append", default=[], help="Patient ID; repeat up to five times")
    parser.add_argument(
        "--trial", action="append", default=[], help="Verified study ID; repeat up to five times"
    )
    parser.add_argument(
        "--max-new", type=int, default=2, help="Maximum new extractions, default 2, hard limit 10"
    )
    parser.add_argument(
        "--execute", action="store_true", help="Send uncached selected records to the organizer API"
    )
    args = parser.parse_args(argv)
    try:
        plan = build_plan(args.patient, args.trial, args.max_new)
    except (ValueError, OSError, LookupError) as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                "mode": "execute" if args.execute else "dry_run",
                "items": plan,
                "new_extractions": sum(not item["cache_hit"] for item in plan),
                "comparisons": 0,
            }
        ),
        flush=True,
    )
    if not args.execute:
        return 0
    report = asyncio.run(execute_plan(plan))
    return int(any(item["state"] == "error" for item in report["items"]))


if __name__ == "__main__":
    raise SystemExit(main())
