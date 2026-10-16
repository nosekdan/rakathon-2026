# TrialScreen

Local hackathon prototype for clinician-reviewed oncology trial screening.
FastAPI backend, plain JavaScript frontend, uv-managed Python environment.

## Run

```sh
uv sync
uv run uvicorn main:app --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765 (API docs: `/api/docs`). Put the supplied patient dataset directory
(`rakathon-dataset-*/`) and the study workbook (`*.xlsx`) in this folder, and copy `.env.example` to
`.env` with the organizer's `API_KEY`. Never put the key in frontend code. No patient inputs, workbook,
derived outputs or credentials belong in Git.

The **Syntetické demo** switch needs no data or API calls and shows all four criterion states and the alert.

## How it works

Three stages, each cached by a hash of exactly what it sends to the model. A stage reruns only when
its input, its model deployment or `pipeline.VERSION` changes.

1. **Patient → facts** (`extract_patient`). Selected FHIR/CSV sources go to the model once per patient.
   It returns dated facts, each with a quote that must be found in the source. Facts with quotes
   that cannot be located are dropped (and mark the extraction incomplete). The rest get IDs `F1…Fn`.
2. **Study → criteria** (`resolve_trial`, `extract_trial`). The workbook protocol is matched to exactly
   one ClinicalTrials.gov study by identifier, never by title. Its eligibility text is extracted once
   into normalized criteria plus an AND/OR tree. A criterion whose quote cannot be located is **kept**
   and flagged, since dropping it would make the study look easier to enter.
3. **Compare** (`compare`). The model sees only the structured facts and criteria (no raw documents)
   and must cite facts by ID. A `met`/`not_met` without a valid citation becomes `unknown`. The
   AND/OR result is evaluated in code; evidence shown in the UI is the cited facts' source spans.

Studies are linked to ClinicalTrials.gov on demand the first time they are screened, because the workbook
has no eligibility text. **Data a ladění → Propojit registry** refreshes all 79 (status, sites, criteria
text). Requests are sequential and paced, since the registry answers bursts with HTTP 429, so this takes
a few minutes. Recruitment status is read from the registry at display time, so a status change updates the alert
without invalidating any extraction.

All outputs live in one SQLite file, `.cache/trialscreen.sqlite3`. The tables `registry`, `facts`,
`criteria` and `matches` each hold key → JSON. These views help with inspection:

```sh
sqlite3 .cache/trialscreen.sqlite3 'SELECT patient_id, name, value, quote FROM patient_facts LIMIT 20;'
sqlite3 .cache/trialscreen.sqlite3 'SELECT trial_id, criterion_id, role, requirement FROM study_criteria;'
sqlite3 .cache/trialscreen.sqlite3 'SELECT patient_id, trial_id, status, match_percent FROM pair_results;'
```

| File | Role |
| --- | --- |
| `repository.py` | Read the cohort CSV, FHIR bundles and workbook; select sources within a 90k-char budget |
| `screening.py` | Pydantic schemas and pure logic: quote location, AND/OR evaluation, verification, alert |
| `pipeline.py` | Registry lookup, model calls and the three cached stages |
| `storage.py` | The SQLite store |
| `main.py` | HTTP API and the background job runner |
| `precompute.py` | CLI for batch precomputation |

## Precompute

Dry run by default. `--max-new` caps uncached model calls (default 2). The SDK and schema repair can
each add a retry, so it is not a strict request or spending limit. Studies not yet in the local registry
cache are looked up on ClinicalTrials.gov first (only protocol IDs are sent).

```sh
uv run python precompute.py --patient PATIENT_ID --trial MK-5684-004 --compare
uv run python precompute.py --patient PATIENT_ID --trial MK-5684-004 --compare --max-new 3 --execute
```

It prints one JSON line per item with timings, stops at the first failure and exits nonzero. Error
messages are never printed (they can echo clinical text), only error types.

`MODEL_DEPLOYMENT` is the comparison/default deployment. `PATIENT_MODEL_DEPLOYMENT` and
`TRIAL_MODEL_DEPLOYMENT` optionally override the extraction stages. Restart the app after changes.

Pilot measurements on Sol: about 13k selected characters took 117 s; about 41k timed out after two
180-second attempts. Large patients need smaller source selection or chunked extraction.

## Interpretation and privacy

- `met`: supported; `not_met`: contradicted; `unknown`: ambiguity requiring review;
  `missing`: absent or outdated information. Missing is not negative evidence.
- The percentage is individual criteria met, not an eligibility probability. An unsuccessful OR
  branch is not itself exclusion. A re-screening trigger is not a promise of future eligibility.
- Comparison quality depends on fact extraction recall: anything not extracted shows as `missing`,
  never as `not_met`.
- Exact-quote validation verifies provenance, not clinical reasoning. The prototype is not validated
  clinical decision support or enrollment approval.
- Dataset dates are shifted. Recency uses the patient's latest record, not today. Names and absent
  ages are not invented. Incomplete source coverage is disclosed and suppresses the positive alert.
- Patient data is pseudonymized, not anonymous. Extraction sends selected records to the organizer's
  configured cloud API. Registry requests contain only study identifiers.
- Keep the server on localhost; there is no authentication. `.cache/` holds sensitive derived records
  and must be protected like the inputs. Delete the dataset and caches after the event in accordance
  with the dataset README.

## Checks

```sh
uv run pytest -q
uv run ruff check .
```

The private-data integration test skips when the dataset/workbook is absent; all others are synthetic.
