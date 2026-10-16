import pytest

from screening import (
    Criterion,
    Evidence,
    Logic,
    TrialExtraction,
    evaluate_logic,
    locate_evidence,
    summarize,
    validate_logic,
)


def leaf(identifier):
    return Logic(op="ref", criterion_id=identifier)


def test_structured_outputs_use_separate_databases_and_import_legacy_cache(tmp_path, monkeypatch):
    import json
    import sqlite3

    import repository

    monkeypatch.setattr(repository, "CACHE", tmp_path)
    payload = {"patient_id": "synthetic", "model": "test", "facts": [{"name": "age", "value": "64"}]}
    legacy = tmp_path / "facts" / f"{repository.fingerprint('legacy')}.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps(payload))
    assert repository.load_cache("facts", "legacy") == payload
    repository.save_cache("criteria", "study", {"criteria": [], "logic": {"op": "all"}})
    repository.save_cache("matches", "pair", {"summary": {"status": "missing"}})
    assert {path.name for path in tmp_path.glob("*.sqlite3")} == {
        "patients.sqlite3",
        "studies.sqlite3",
        "matches.sqlite3",
    }
    connection = sqlite3.connect(tmp_path / "patients.sqlite3")
    try:
        assert connection.execute("SELECT patient_id, name, value FROM patient_facts").fetchone() == (
            "synthetic",
            "age",
            "64",
        )
    finally:
        connection.close()
    repository.save_cache("facts", "legacy", {**payload, "facts": []})
    assert repository.load_cache("facts", "legacy")["facts"] == []


def test_missing_is_not_negative_or_positive():
    logic = Logic(op="all", children=[leaf("age"), leaf("lab")])
    assert evaluate_logic(logic, {"age": "met", "lab": "missing"}) == "missing"


def test_precompute_is_bounded_and_dry_run_by_default(monkeypatch, capsys):
    import precompute

    monkeypatch.setattr(precompute, "patients", lambda: [{"id": "synthetic"}])
    monkeypatch.setattr(precompute, "patient_version", lambda patient: "v1")
    monkeypatch.setattr(precompute, "load_cache", lambda namespace, key: None)

    async def forbidden(*args, **kwargs):
        pytest.fail("Dry run must not invoke extraction")

    monkeypatch.setattr(precompute, "execute_plan", forbidden)
    assert precompute.main(["--patient", "synthetic"]) == 0
    assert '"new_extractions": 1' in capsys.readouterr().out
    with pytest.raises(ValueError, match="exceeds"):
        precompute.build_plan(["synthetic"], [], max_new=0)
    with pytest.raises(ValueError, match="Pilot limit"):
        precompute.build_plan([str(index) for index in range(6)], [])
    with pytest.raises(ValueError, match="explicit"):
        precompute.build_plan([], [])
    monkeypatch.setattr(precompute, "trials", lambda: [{"id": "T001"}])
    with pytest.raises(ValueError, match="verified"):
        precompute.build_plan([], ["T001"])


def test_precompute_reuses_caches_and_enriches_metadata(tmp_path, monkeypatch):
    import asyncio

    import pipeline
    import precompute
    import repository

    monkeypatch.setattr(repository, "CACHE", tmp_path)
    record = {"id": "synthetic", "version": "patient-v1", "sources": {}}
    registry = {"version": "trial-v1", "nct_id": "NCT00000000", "clinical_eu_verified": True}
    facts_key = f"{pipeline.PATIENT_MODEL}:{pipeline.VERSION}:synthetic:patient-v1"
    criteria_key = f"{pipeline.TRIAL_MODEL}:{pipeline.VERSION}:trial-v1"
    repository.save_cache("facts", facts_key, {"facts": [], "dropped_citations": 0})
    repository.save_cache("registry", "T001", registry)
    extraction = TrialExtraction(criteria=[], logic=Logic(op="all"), complete=False)
    repository.save_cache("criteria", criteria_key, extraction.model_dump())
    monkeypatch.setattr(precompute, "patients", lambda: [record])
    monkeypatch.setattr(precompute, "trials", lambda: [{"id": "T001"}])
    monkeypatch.setattr(precompute, "patient_version", lambda patient: patient["version"])
    monkeypatch.setattr(precompute, "patient_record", lambda identifier: record)
    monkeypatch.setattr(precompute, "select_sources", lambda record: ([], False))

    async def forbidden(*args, **kwargs):
        pytest.fail("Cached extraction must not invoke the model or compare")

    monkeypatch.setattr(pipeline, "model_json", forbidden)
    monkeypatch.setattr(pipeline, "compare", forbidden)
    plan = precompute.build_plan(["synthetic"], ["T001"], max_new=0)
    report = asyncio.run(precompute.execute_plan(plan))
    assert report["comparisons"] == 0
    assert all(item["cache_hit"] and item["state"] == "done" for item in report["items"])
    assert repository.load_cache("facts", facts_key)["patient_id"] == "synthetic"
    assert repository.load_cache("criteria", criteria_key)["nct_id"] == "NCT00000000"
    assert repository.load_cache("criteria", criteria_key)["model"] == pipeline.TRIAL_MODEL


def test_new_patient_extraction_has_identity_and_verified_evidence(tmp_path, monkeypatch):
    import asyncio

    import pipeline
    import repository
    from screening import Fact, PatientExtraction

    monkeypatch.setattr(repository, "CACHE", tmp_path)
    source = {"id": "note", "text": "ECOG 1"}
    record = {"id": "synthetic", "version": "v1", "snapshot_at": "2025-01-01"}
    monkeypatch.setattr(pipeline, "select_sources", lambda record: ([source], True))

    async def model_json(schema, instruction, payload):
        return PatientExtraction(
            facts=[
                Fact(
                    name="ECOG",
                    value="1",
                    date="2025-01-01",
                    evidence=Evidence(source_id="note", quote="ECOG 1"),
                )
            ]
        )

    monkeypatch.setattr(pipeline, "model_json", model_json)
    result = asyncio.run(pipeline.extract_patient(record))
    assert result["patient_id"] == "synthetic"
    assert result["model"] == pipeline.PATIENT_MODEL
    assert result["facts"][0]["evidence"]["start"] == 0
    assert result["dropped_citations"] == 0


def test_precompute_stops_on_error_and_preserves_input_metrics(tmp_path, monkeypatch):
    import asyncio

    import precompute
    import repository

    monkeypatch.setattr(repository, "CACHE", tmp_path)
    monkeypatch.setattr(precompute, "patient_record", lambda identifier: {"version": "v1"})
    monkeypatch.setattr(precompute, "select_sources", lambda record: ([{"text": "ECOG 1"}], False))

    async def failed(record):
        raise ValueError("Synthetic failure containing details that must not be logged")

    monkeypatch.setattr(precompute, "extract_patient", failed)
    item = {"kind": "patient", "id": "synthetic", "version": "v1", "cache_hit": False}
    report = asyncio.run(precompute.execute_plan([item, item]))
    assert len(report["items"]) == 1
    assert report["items"][0]["error_type"] == "ValueError"
    assert report["items"][0]["selected_characters"] == 6
    assert "Synthetic failure" not in str(report)


def test_cached_matches_require_all_stage_models(monkeypatch):
    import main

    monkeypatch.setattr(main, "MODEL", "comparison")
    monkeypatch.setattr(main, "PATIENT_MODEL", "patient")
    monkeypatch.setattr(main, "TRIAL_MODEL", "study")
    monkeypatch.setattr(main, "trials", lambda: [{"id": "T001"}])
    result = {
        "patient_version": "p1",
        "registry_version": "t1",
        "pipeline_version": main.VERSION,
        "model": "comparison",
        "patient_model": "patient",
        "trial_model": "study",
    }
    monkeypatch.setattr(
        main, "load_cache", lambda namespace, key: result if namespace == "matches" else {"version": "t1"}
    )
    record = {"id": "synthetic", "version": "p1"}
    assert len(main.cached_results(record)) == 1
    monkeypatch.setattr(main, "PATIENT_MODEL", "different")
    assert main.cached_results(record) == []
    monkeypatch.setattr(main, "PATIENT_MODEL", "patient")
    monkeypatch.setattr(main, "TRIAL_MODEL", "different")
    assert main.cached_results(record) == []


def test_alternative_does_not_exclude():
    logic = Logic(op="any", children=[leaf("A"), leaf("B")])
    assert evaluate_logic(logic, {"A": "not_met", "B": "met"}) == "met"


def test_ambiguous_evidence_requires_review():
    logic = Logic(op="all", children=[leaf("A"), leaf("B")])
    assert evaluate_logic(logic, {"A": "missing", "B": "unknown"}) == "unknown"


def test_citations_are_exact_and_local():
    sources = {"doc": {"text": "Hb 105 g/l."}}
    result = locate_evidence(Evidence(source_id="doc", quote="105 g/l"), sources)
    assert result["start"] == 3
    assert result["end"] == 10
    assert locate_evidence(Evidence(source_id="doc", quote="Hb 150"), sources) is None


def test_unrepresented_criteria_rejected():
    with pytest.raises(ValueError):
        validate_logic(leaf("A"), {"A", "B"})


def test_incomplete_or_closed_trial_never_badged():
    extraction = TrialExtraction(
        criteria=[
            Criterion(
                id="A",
                requirement="Age >= 18",
                role="inclusion",
                source=Evidence(source_id="protocol", quote="Age >= 18"),
            )
        ],
        logic=leaf("A"),
        complete=False,
    )
    decisions = [{"criterion_id": "A", "status": "met"}]
    assert not summarize(extraction, decisions, "RECRUITING")["badge"]
    extraction.complete = True
    assert not summarize(extraction, decisions, "COMPLETED")["badge"]
    assert summarize(extraction, decisions, "RECRUITING")["badge"]


def test_real_import_preserves_identifiers_and_sources():
    from repository import DATASET, ROOT, patient_record, patients, select_sources, trials

    if DATASET is None or not any(ROOT.glob("*.xlsx")):
        pytest.skip("Local private dataset and study workbook required")

    cohort = patients()
    assert len(cohort) == 1830
    assert len(trials()) == 79
    patient = next(item for item in cohort if "C61" in item["diagnoses"] and not item["deceased"])
    record = patient_record(patient["id"])
    assert record["sources"]["cohort"]["text"].startswith("pseudonym:")
    selected, complete = select_sources(record)
    assert selected
    assert isinstance(complete, bool)
    for vital in record["vitals"].values():
        text = record["sources"][vital["source_id"]]["text"]
        assert text[vital["start"] : vital["end"]] == vital["quote"]


def test_public_pages_require_a_matching_registry_identifier():
    from pipeline import public_source_verified

    identity = {"nctId": "NCT05239728", "secondaryIdInfos": [{"id": "2023-505023-31-00"}]}
    assert public_source_verified("EU CT: 2023-505023-31-00", identity)
    assert public_source_verified("Registry2023-505023-31-00Location", identity)
    assert not public_source_verified("A similar title. NCT00000000", identity)


def test_local_api_does_not_expose_secret():
    from fastapi.testclient import TestClient

    from main import app
    from repository import patients

    client = TestClient(app)
    response = client.get("/api/bootstrap")
    assert response.status_code == 200
    assert len(response.json()["patients"]) == len(patients())
    assert "API_KEY" not in response.text
    assert client.get("/.env").status_code == 404
    assert (
        client.post(
            "/api/jobs", json={"kind": "screen"}, headers={"origin": "https://external.example"}
        ).status_code
        == 403
    )
