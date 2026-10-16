"""Precompute patient facts, study criteria and optionally their comparisons. Dry run unless --execute.

Every stage is cached by its input, so reruns only do new work. --max-new caps the number of
uncached model calls (the SDK and schema repair can each add one retry per call).
"""

import argparse
import asyncio
import json
from time import perf_counter

import storage
from pipeline import (
    compare,
    extract_patient,
    extract_trial,
    match_is_current,
    patient_input,
    trial_input,
)
from repository import patient_record, trials_by_id


def build_plan(patient_ids: list[str], trial_ids: list[str], pairs: bool, max_new: int) -> list[dict]:
    patient_ids = list(dict.fromkeys(patient_ids))
    trial_ids = list(dict.fromkeys(trial_ids))
    if not patient_ids and not trial_ids:
        raise ValueError("Select explicit --patient or --trial IDs")
    facts_keys = {}
    criteria_keys = {}
    plan = []
    for identifier in patient_ids:
        facts_keys[identifier] = patient_input(patient_record(identifier))["key"]
        cached = storage.get("facts", facts_keys[identifier]) is not None
        plan.append({"kind": "patient", "patient_id": identifier, "cached": cached})
    for identifier in trial_ids:
        if identifier not in trials_by_id():
            raise ValueError(f"Unknown study ID {identifier}")
        registry = storage.get("registry", identifier)
        if not registry or registry["state"] != "resolved":
            raise ValueError(
                f"Study {identifier} is not linked to the registry; sync registries in the UI first"
            )
        criteria_keys[identifier] = trial_input(registry)["key"]
        cached = storage.get("criteria", criteria_keys[identifier]) is not None
        plan.append({"kind": "trial", "trial_id": identifier, "cached": cached})
    if pairs:
        for patient_id in patient_ids:
            for trial_id in trial_ids:
                match = storage.get("matches", f"{patient_id}:{trial_id}")
                cached = match_is_current(match, facts_keys[patient_id], criteria_keys[trial_id])
                plan.append(
                    {"kind": "pair", "patient_id": patient_id, "trial_id": trial_id, "cached": cached}
                )
    new = sum(not item["cached"] for item in plan)
    if new > max_new:
        raise ValueError(f"Plan needs {new} new model calls, more than --max-new {max_new}")
    return plan


async def execute_plan(plan: list[dict]) -> bool:
    """Run items in order (patients, trials, pairs). Stops at the first failure; returns success."""
    facts: dict[str, dict] = {}
    criteria: dict[str, dict] = {}
    for item in plan:
        started = perf_counter()
        report = dict(item)
        try:
            if item["kind"] == "patient":
                result = await extract_patient(patient_record(item["patient_id"]))
                facts[item["patient_id"]] = result
                report.update(facts=len(result["facts"]), dropped_citations=result["dropped_citations"])
            elif item["kind"] == "trial":
                trial = trials_by_id()[item["trial_id"]]
                result = await extract_trial(trial, storage.get("registry", trial["id"]))
                criteria[trial["id"]] = result
                report.update(criteria=len(result["criteria"]), complete=result["complete"])
            else:
                trial = trials_by_id()[item["trial_id"]]
                result = await compare(trial, facts[item["patient_id"]], criteria[item["trial_id"]])
                report.update(status=result["summary"]["status"])
            report["state"] = "done"
        except Exception as error:  # noqa: BLE001 - job boundary; record and continue
            # Only the type: messages can echo model output or clinical text.
            report.update(state="error", error_type=type(error).__name__)
        report["seconds"] = round(perf_counter() - started, 3)
        print(json.dumps(report), flush=True)
        if report["state"] == "error":
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--patient", action="append", default=[], help="Patient ID (repeatable)")
    parser.add_argument("--trial", action="append", default=[], help="Registry-linked study ID (repeatable)")
    parser.add_argument("--compare", action="store_true", help="Also compare every selected patient x study")
    parser.add_argument("--max-new", type=int, default=2, help="Maximum uncached model calls (default 2)")
    parser.add_argument(
        "--execute", action="store_true", help="Send uncached inputs to the configured model API"
    )
    args = parser.parse_args(argv)
    try:
        plan = build_plan(args.patient, args.trial, args.compare, args.max_new)
    except (ValueError, LookupError, OSError) as error:
        parser.error(str(error))
    new = sum(not item["cached"] for item in plan)
    print(
        json.dumps({"mode": "execute" if args.execute else "dry_run", "new_calls": new, "items": plan}),
        flush=True,
    )
    if not args.execute:
        return 0
    return 0 if asyncio.run(execute_plan(plan)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
