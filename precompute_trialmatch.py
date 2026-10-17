"""Precompute the rule engine for whole diagnosis groups (patients x the workbook studies for their diagnosis).

    python precompute_trialmatch.py                 # all target patients (C61, C64) x their studies
    python precompute_trialmatch.py --limit 3       # only the 3 best-documented patients per diagnosis

The stage deployments must already be set to trialmatch / trialmatch+llm (scripts/run_trialmatch.sh does that).
Everything is cached, so a rerun only computes what is new.
"""

import argparse
import asyncio

import precompute
import storage
from repository import patients, trials

# ICD-10 prefix -> words identifying the workbook studies for that diagnosis.
GROUPS = {"C61": ("prostat",), "C64": ("ledvin", "renáln", "renaln")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dx", nargs="+", default=list(GROUPS), help="ICD-10 groups (default C61 C64)")
    parser.add_argument("--limit", type=int, default=0, help="best-documented patients per group (0 = all)")
    args = parser.parse_args()
    status = 0
    for dx in args.dx:
        words = GROUPS[dx]
        trial_ids = [t["id"] for t in trials() if any(w in t["diagnosis"].lower() for w in words)]
        asyncio.run(precompute.resolve_missing(trial_ids))
        registry = storage.rows("registry")
        resolved = [t for t in trial_ids if registry.get(t, {}).get("state") == "resolved"]
        group = sorted((p for p in patients() if dx in p["diagnoses"] and not p["deceased"]),
                       key=lambda p: (p["notes"], p["labs"]), reverse=True)
        if args.limit:
            group = group[: args.limit]
        print(f"{dx}: {len(group)} patients x {len(resolved)} studies "
              f"({len(trial_ids) - len(resolved)} studies without a unique registry match skipped)", flush=True)
        argv = [x for p in group for x in ("--patient", p["id"])] + [x for t in resolved for x in ("--trial", t)]
        status |= precompute.main([*argv, "--compare", "--max-new", "1000000", "--execute"])
    return status


if __name__ == "__main__":
    raise SystemExit(main())
