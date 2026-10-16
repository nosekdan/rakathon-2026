# TrialScreen

Local hackathon prototype for clinician-reviewed oncology trial screening.
FastAPI backend, plain JavaScript frontend, uv-managed Python environment.

## Run

```sh
cd ~/projects/hackathon
uv sync
uv run uvicorn main:app --host 127.0.0.1 --port 8765
```

Open http://127.0.0.1:8765. API documentation: `/api/docs`.
Keep the supplied patient dataset directory and study workbook in this folder.
The existing `.env` supplies `API_KEY`. Optional `MODEL_DEPLOYMENT` defaults to
`gpt-6-1-sol-dz-eu`. Requests use the organizer's Azure endpoint with the
`api-key` header. Never put the key in frontend code.

For a source-only checkout, configure credentials using `.env.example` as the
template and obtain the private inputs through the authorized hackathon channel.
No patient inputs, study workbook, extracted outputs or credentials belong in Git.

## Views

- Patient card: real pseudonymized records, measurements and source documents.
- Study matching: four criterion states, explanations, exact highlighted quotes,
  study sources, and conditional re-screening after progression or washout.
- Data and debugging: searchable patient/study tables and structured model output.

The dataset contains 1,830 patients; the workbook imports 79 studies. Registry
links require exact protocol/registry IDs, not title similarity. Three public
ClinicalTrials.eu pages are configured in `clinical_eu_links.json`: MK-5684-004,
PEAK-1 and MK-2400-001. Their browser-imported text is cached locally. Other
studies remain unavailable for inference until their public linkage is verified.
The registry button resolves ClinicalTrials.gov metadata; it does not certify
local recruitment. ClinicalTrials.eu can reject automated requests with HTTP 403.
In that case, browser-obtained public text can be submitted through
`POST /api/registry/import` with `trial_id`, `url` and `text`; identifier validation
still applies. Full public eligibility text comes from ClinicalTrials.gov.

Select a patient and choose **Posoudit studie** to run the model. Results are
cached by patient, registry, pipeline and model version. The explicitly labeled
synthetic mode needs no API calls and demonstrates all four states and the alert.

## Structured Storage and Flow

Earlier versions persisted these outputs as JSON cache files. The same cache API
now stores them in three independent SQLite databases under `.cache/`:

| Database | Content | SQL view |
| --- | --- | --- |
| `patients.sqlite3` | Dated patient facts, evidence offsets, source selection, limitations, patient/model versions | `patient_facts` |
| `studies.sqlite3` | Normalized inclusion/exclusion requirements, exact quotes, AND/OR tree, registry/model versions | `study_criteria` |
| `matches.sqlite3` | Patient-study decisions, reasons, evidence, missing-data actions and summaries | `pair_results` |

Each database has an `outputs(cache_key, payload, saved_at)` table containing
validated structured JSON. Views expose individual facts/criteria as rows.
This is not yet a fully normalized clinical terminology or numeric-rules schema:
fact values and criterion requirements can still be text. Evidence and AND/OR
logic remain in the complete JSON even when not exposed by a view.
SQLite is local and not encrypted; filesystem protection is still required.
Legacy JSON is imported on first access without deletion. After migration,
SQLite is authoritative. Stop an older server before using the upgraded app.

```sh
sqlite3 .cache/patients.sqlite3 'SELECT patient_id, model, COUNT(*) FROM patient_facts GROUP BY patient_id, model;'
sqlite3 .cache/studies.sqlite3 'SELECT nct_id, COUNT(*) FROM study_criteria GROUP BY nct_id;'
sqlite3 .cache/matches.sqlite3 'SELECT patient_id, trial_id, status FROM pair_results;'
```

1. Parse local FHIR/CSV records and select source documents. Extract each
  patient's dated facts once per source, pipeline and model version.
2. Verify study identity against public registries. Extract requirements once
  per registry, pipeline and model version, retaining exclusions and AND/OR.
3. Compare a selected patient-study pair. The current LLM call sees criteria,
  facts and selected original text. Validate exact quotes, evaluate the logic
  in code and save four-state decisions. It still resends the full selected
  text for every pair; this is the main avoidable repeated work.
4. Opening a patient reads saved comparisons. Extraction alone creates no new
  candidate-study results. Explicit screening still makes comparison calls;
  there is no automatic background scheduler yet. The refresh-button behavior
  was not changed by the storage/precomputation work.

## Bounded Precomputation

The CLI defaults to a dry run, requires explicit IDs and never compares pairs.
Run it while no interactive screening job is active. Planning may import a
legacy cache locally but does not call the model or fetch registry pages.

```sh
uv run python precompute.py --patient PATIENT_ID --trial T058
uv run python precompute.py --patient PATIENT_ID --trial T058 --max-new 2 --execute
uv run python precompute.py --trial T053 --trial T058 --trial T059 --max-new 0 --execute
```

Replace `PATIENT_ID` with an ID from the local UI. Limits: five explicit patients,
five studies, at most ten new extractions, with a default of two. Cache hits do
not count as new extractions. `--max-new 0` permits cache reuse only. Studies
must already have cached, identifier-verified public sources. The command stops
on the first failure, returns a nonzero exit code and saves per-item timings and
error types under `.cache/precompute/`. No raw clinical text is logged. The SDK
can retry a failed request once; schema repair can also require another call,
so the extraction limit is not a strict HTTP-request or spending limit.

`MODEL_DEPLOYMENT` remains the comparison/default deployment.
`PATIENT_MODEL_DEPLOYMENT` and `TRIAL_MODEL_DEPLOYMENT` optionally override the
two extraction stages. All default to Sol. Restart the app after configuration
changes. Extraction caches are separated by model; changing an extraction model
also invalidates saved comparisons in the UI.
The advertised `gpt-4.1-mini` ID returned `DeploymentNotFound` in a harmless probe.
The organizer must supply a callable smaller deployment name; the `/models`
catalog alone is insufficient. No patient data was sent to that probe.

### Measured Pilot and Planning Estimates

Only two new patient extractions were attempted on Sol:

- About 13k selected characters: 117 seconds, 39 facts, zero rejected quotes.
- About 41k selected characters: timeout after 361 seconds including one retry;
  no partial extraction was stored and no further cloud calls were made.
- The existing patient and three study extractions were reused. Study cache
  reads took 1-2 ms each, excluding process startup. No comparisons were run.
- Local stores after the pilot: two patient extractions (111 facts), three
  studies (97 criteria), and three previously computed pair results.

One success and one timeout are not a throughput benchmark or clinical quality
evaluation. For budgeting only, assume 2-6 minutes per patient extraction attempt:

| Scope | Serial compute time | Ideal four-worker time |
| --- | --- | --- |
| 445 target-cohort patients | 15-45 hours | 4-11 hours |
| All 1,830 patients | 61-183 hours | 15-46 hours |

These estimates exclude extra work after failures, queueing, public registry
verification and clinician review. Four-worker figures assume provider quotas
allow it; concurrency is not implemented by the pilot CLI. There is no current
fresh timing benchmark for study extraction or pair comparison. A planning-only
assumption of 1-3 minutes per study would add about 1.3-4 hours for 79 studies.
At an assumed 1-3 minutes per comparison, even 445 patients x 3 shortlisted
studies means 22-67 serial hours. Measure a small comparison batch before using
either assumption. Dollar cost cannot be estimated without actual deployment
pricing and token-usage measurements.

### Recommended Next Steps

1. Keep SQLite and precompute facts/criteria independently. Add a bounded worker
  that recomputes only changed sources and affected pairs, then let the UI read
  those results. Avoid all 1,830 x 79 = 144,570 pair comparisons.
2. Shortlist with explicit diagnosis, demographics and recruiting-site metadata.
  Retain uncertain/generalized diagnoses and unknown values for review; absence
  of a code is not proof of absence of disease. Formalize clear age/lab/date/unit
  rules for deterministic evaluation without losing exceptions or OR branches.
3. Retrieve patient-scoped passages per criterion using SQLite FTS and structured
  filters first. Add multilingual embeddings/hybrid retrieval only when synonym
  recall needs it. A vector database is optional infrastructure, not an
  eligibility engine. Retrieval misses cannot establish clinical negatives.
  Always preserve source IDs, exact spans, chronology and an auditable fallback.
4. Evaluate a smaller model on a clinician-reviewed sample of positives,
  contradictions, missing information, conflicts and AND/OR cases. Keep Sol for
  difficult criteria or escalation only if that evaluation supports it. Exact
  quotations alone do not establish extraction correctness or completeness.

## Interpretation and Privacy

- `met`: supported; `not_met`: contradicted; `unknown`: ambiguity requiring review;
  `missing`: absent or outdated information. Missing is not negative evidence.
- The percentage is individual criteria met, not an eligibility probability.
  AND/OR logic is evaluated separately. An unsuccessful OR branch is not itself
  exclusion. A re-screening trigger is not a promise of future eligibility.
- Public eligibility criteria and model citations require clinician verification.
  Exact-quote validation verifies provenance, not clinical reasoning correctness.
  The prototype is not validated clinical decision support or enrollment approval.
- Dataset dates are shifted. Recency uses the patient's latest record, not today.
  Names and absent ages are not invented. Source selection has a 90,000-character
  budget; incomplete coverage is disclosed and suppresses the positive alert.
- Patient data is pseudonymized, not anonymous. Inference sends selected records
  to the organizer's configured cloud API. Public registry requests contain only
  study metadata. UI fonts and icons are served locally.
- Keep the server on localhost. There is no production authentication system.
  `.cache/` contains sensitive derived records and must be protected like inputs.
  Delete the dataset, original archives and derived caches after the event in
  accordance with the dataset README. Do not commit or redistribute them.

## Checks

```sh
uv run pytest tests/test_screening.py -q
uv run ruff check screening.py repository.py pipeline.py main.py demo.py storage.py precompute.py tests
```

The private-input integration test skips when the dataset/workbook is absent;
synthetic logic, persistence and precompute tests do not need cloud credentials.