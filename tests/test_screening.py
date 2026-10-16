import asyncio
import re
import sqlite3

import pytest

import main
import pipeline
import precompute
import storage
from repository import VITAL_PATTERNS, select_sources, trial_slug
from screening import (
    Comparison,
    Criterion,
    Decision,
    Evidence,
    Fact,
    Logic,
    PatientExtraction,
    TrialExtraction,
    badge,
    evaluate_logic,
    future_watch,
    locate_evidence,
    summarize,
    validate_logic,
    verify_decisions,
)


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "PATH", tmp_path / "test.sqlite3")


def leaf(identifier):
    return Logic(op="ref", criterion_id=identifier)


def criterion(identifier, trigger="none"):
    return Criterion(
        id=identifier,
        requirement=identifier,
        role="inclusion",
        source=Evidence(source_id="registry", quote=identifier),
        trigger=trigger,
    )


RECORD = {
    "id": "synthetic",
    "snapshot_at": "2025-01-01",
    "deceased": False,
    "sources": {
        "cohort": {"id": "cohort", "kind": "demographics", "text": "vek: 64", "date": None},
        "note": {
            "id": "note",
            "kind": "DocumentReference",
            "text": "ECOG 1.  Stabilni.",
            "date": "2025-01-01",
        },
        "hb-new": {"id": "hb-new", "kind": "Observation", "text": "Hb 120", "date": "2025-01-01"},
        "hb-old": {"id": "hb-old", "kind": "Observation", "text": "Hb 110", "date": "2024-01-01"},
    },
    "latest_labs": [{"source_id": "hb-new"}],
}
REGISTRY = {
    "state": "resolved",
    "nct_id": "NCT00000000",
    "status": "RECRUITING",
    "site_recruiting": True,
    "sources": {"registry": {"id": "registry", "text": "Age >= 18\nECOG 0-1"}},
}
TRIAL = {"id": "T-1", "protocol": "T-1"}


def fake_model(responses):
    async def model_json(schema, deployment, instruction, payload):
        return responses[schema]

    return model_json


# Pure logic


def test_missing_is_not_negative_or_positive():
    assert (
        evaluate_logic(Logic(op="all", children=[leaf("A"), leaf("B")]), {"A": "met", "B": "missing"})
        == "missing"
    )


def test_alternative_does_not_exclude():
    assert (
        evaluate_logic(Logic(op="any", children=[leaf("A"), leaf("B")]), {"A": "not_met", "B": "met"})
        == "met"
    )


def test_ambiguous_evidence_requires_review():
    assert (
        evaluate_logic(Logic(op="all", children=[leaf("A"), leaf("B")]), {"A": "missing", "B": "unknown"})
        == "unknown"
    )


def test_unrepresented_criteria_rejected():
    with pytest.raises(ValueError):
        validate_logic(leaf("A"), {"A", "B"})


def test_citations_are_exact_and_tolerate_whitespace():
    sources = {"doc": {"text": "Hb  105\ng/l."}}
    assert locate_evidence(Evidence(source_id="doc", quote="Hb"), sources)["start"] == 0
    found = locate_evidence(Evidence(source_id="doc", quote="105 g/l"), sources)
    assert (found["start"], found["end"], found["quote"]) == (4, 11, "105\ng/l")
    assert locate_evidence(Evidence(source_id="doc", quote="Hb 150"), sources) is None
    assert locate_evidence(Evidence(source_id="other", quote="Hb"), sources) is None


def test_decisions_must_cite_existing_facts():
    facts = {"F1": {"id": "F1", "evidence": {"source_id": "note", "quote": "ECOG 1"}}}
    decisions = [
        Decision(criterion_id="A", status="met", reason="ok", fact_ids=["F1"]),
        Decision(criterion_id="B", status="met", reason="no citation"),
        Decision(criterion_id="C", status="not_met", reason="bad id", fact_ids=["F9"]),
        Decision(criterion_id="D", status="missing", reason="absent"),
        Decision(criterion_id="E", status="met", reason="dup", fact_ids=["F1"]),
        Decision(criterion_id="E", status="not_met", reason="dup", fact_ids=["F1"]),
    ]
    result = verify_decisions([criterion(c) for c in "ABCDEF"], decisions, facts)
    assert [item["status"] for item in result] == [
        "met",
        "unknown",
        "unknown",
        "missing",
        "unknown",
        "unknown",
    ]
    assert result[0]["evidence"] == [facts["F1"]["evidence"]]


def test_badge_requires_complete_inputs_and_local_recruitment():
    ready = {"criteria_complete": True, "facts_complete": True, "recruiting": True, "site_recruiting": True}
    ready["deceased"] = False
    assert badge("met", **ready)
    assert not badge("unknown", **ready)
    for field in ("criteria_complete", "facts_complete", "recruiting", "site_recruiting"):
        assert not badge("met", **{**ready, field: False})
    assert not badge("met", **{**ready, "deceased": True})


def test_future_watch_only_for_single_trigger_blocker():
    extraction = TrialExtraction(
        criteria=[criterion("A"), criterion("P", "progression")],
        logic=Logic(op="all", children=[leaf("A"), leaf("P")]),
        complete=True,
    )
    blocked = [{"criterion_id": "A", "status": "met"}, {"criterion_id": "P", "status": "not_met"}]
    assert future_watch(extraction, blocked)[0]["trigger"] == "progression"
    assert future_watch(extraction, [{**blocked[0], "status": "missing"}, blocked[1]]) == []
    assert summarize(extraction, blocked)["counts"] == {"met": 1, "not_met": 1, "unknown": 0, "missing": 0}


# Dataset parsing


def test_superseded_labs_do_not_make_selection_incomplete():
    selected, complete = select_sources(RECORD)
    assert complete
    assert {source["id"] for source in selected} == {"cohort", "note", "hb-new"}
    assert not select_sources(RECORD, budget=10)[1]


def test_ecog_pattern_needs_word_boundary():
    assert re.search(VITAL_PATTERNS["ecog"], "ECOG: 1", re.IGNORECASE).group(1) == "1"
    assert re.search(VITAL_PATTERNS["ecog"], "WHO PS 2", re.IGNORECASE).group(1) == "2"
    assert re.search(VITAL_PATTERNS["ecog"], "caps 1 tbl", re.IGNORECASE) is None
    assert re.search(VITAL_PATTERNS["ecog"], "WHO 2016", re.IGNORECASE) is None


def test_trial_ids_derive_from_protocol():
    assert trial_slug("MK-5684-004") == "MK-5684-004"
    assert trial_slug("peak-1 (CGT9486)") == "PEAK-1-CGT9486"


def test_local_sites_match_declined_names():
    locations = [{"facility": "Nemocnice Na Homolce"}, {"facility": "FN v Motole"}, {"facility": "Brno"}]
    assert len(pipeline.local_sites(locations)) == 2


# Stages


def test_stages_cache_by_input_and_compare_from_facts(monkeypatch):
    extraction = TrialExtraction(
        criteria=[
            Criterion(
                id="age",
                requirement="Age >= 18",
                role="inclusion",
                source=Evidence(source_id="registry", quote="Age >= 18"),
            ),
            Criterion(
                id="ecog",
                requirement="ECOG 0-1",
                role="inclusion",
                source=Evidence(source_id="registry", quote="ECOG 0 - 1"),
            ),
        ],
        logic=Logic(op="all", children=[leaf("age"), leaf("ecog")]),
        complete=True,
    )
    patient = PatientExtraction(
        facts=[
            Fact(name="ECOG", value="1", evidence=Evidence(source_id="note", quote="ECOG 1. Stabilni.")),
            Fact(name="invented", value="x", evidence=Evidence(source_id="note", quote="not in text")),
        ]
    )
    comparison = Comparison(
        decisions=[
            Decision(criterion_id="age", status="missing", reason="chybí", action="doplnit"),
            Decision(criterion_id="ecog", status="met", reason="ECOG 1", fact_ids=["F1"]),
        ]
    )
    calls = []

    async def model_json(schema, deployment, instruction, payload):
        calls.append((schema, payload))
        return {PatientExtraction: patient, TrialExtraction: extraction, Comparison: comparison}[schema]

    monkeypatch.setattr(pipeline, "model_json", model_json)

    async def run():
        facts = await pipeline.extract_patient(RECORD)
        criteria = await pipeline.extract_trial(TRIAL, REGISTRY)
        return facts, criteria, await pipeline.compare(TRIAL, facts, criteria)

    facts, criteria, match = asyncio.run(run())
    assert [fact["id"] for fact in facts["facts"]] == ["F1"]
    assert facts["facts"][0]["evidence"]["quote"] == "ECOG 1.  Stabilni."
    assert facts["dropped_citations"] == 1 and not facts["complete"]
    # The unverifiable criterion quote is kept, not dropped, and marks the extraction incomplete.
    assert criteria["criteria"][1]["source"]["unverified"] and not criteria["complete"]
    assert match["decisions"][1]["evidence"] == [facts["facts"][0]["evidence"]]
    assert match["summary"]["status"] == "missing"
    compare_payload = calls[2][1]
    assert list(compare_payload) == ["snapshot_at", "patient_facts", "criteria"]
    assert "sources" not in compare_payload

    asyncio.run(run())
    assert len(calls) == 3, "cached stages must not call the model again"

    monkeypatch.setattr(main, "trials_by_id", lambda: {"T-1": TRIAL})
    storage.put("registry", "T-1", "T-1", REGISTRY)
    [view] = main.cached_results(RECORD)
    assert view["summary"]["badge"] is False
    assert "sources" not in view["registry"]
    monkeypatch.setattr(pipeline, "MODEL", "other")
    monkeypatch.setattr(main, "match_is_current", pipeline.match_is_current)
    assert main.cached_results(RECORD) == []


def test_registry_retries_rate_limit(monkeypatch):
    import httpx

    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        identity = {"nctId": "NCT1", "orgStudyIdInfo": {"id": "ABC-123"}}
        return httpx.Response(
            200, json={"studies": [{"protocolSection": {"identificationModule": identity}}]}
        )

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs)
    )
    monkeypatch.setattr(pipeline, "REGISTRY_PACE_SECONDS", 0)
    result = asyncio.run(pipeline.resolve_trial({"id": "ABC-123", "protocol": "ABC-123"}))
    assert result["state"] == "resolved" and len(calls) == 2


def test_truncated_output_is_not_repaired(monkeypatch):
    class Completions:
        calls = 0

        async def create(self, **kwargs):
            Completions.calls += 1
            message = type("Message", (), {"content": "{"})
            return type(
                "Response",
                (),
                {"choices": [type("Choice", (), {"finish_reason": "length", "message": message})]},
            )

    class Client:
        def __init__(self, **kwargs):
            self.chat = type("Chat", (), {"completions": Completions()})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setenv("API_KEY", "test")
    monkeypatch.setattr(pipeline, "AsyncOpenAI", Client)
    with pytest.raises(ValueError, match="truncated"):
        asyncio.run(pipeline.model_json(Comparison, "model", "", {}))
    assert Completions.calls == 1


def test_storage_views_expose_rows():
    storage.put("facts", "k", "p1", {"model": "m", "facts": [{"id": "F1", "name": "ECOG", "value": "1"}]})
    storage.put(
        "matches", "p1:T", "p1", {"trial_id": "T", "summary": {"status": "met", "match_percent": 100}}
    )
    assert storage.by_owner("matches", "p1")[0]["trial_id"] == "T"
    with sqlite3.connect(storage.PATH) as connection:
        assert connection.execute("SELECT patient_id, fact_id, name FROM patient_facts").fetchone() == (
            "p1",
            "F1",
            "ECOG",
        )
        assert connection.execute("SELECT trial_id, status FROM pair_results").fetchone() == ("T", "met")


# Jobs, API, CLI


def test_unexpected_job_error_reaches_terminal_state(monkeypatch):
    async def boom(record):
        raise TypeError("unexpected")

    monkeypatch.setattr(main, "extract_patient", boom)
    monkeypatch.setattr(main, "patient_record", lambda identifier: RECORD)
    monkeypatch.setattr(main, "trials_by_id", lambda: {"T-1": TRIAL})
    job = {"state": "queued", "done": 0, "total": 0, "message": "", "errors": []}
    asyncio.run(main.run_job(job, main.JobRequest(kind="screen", patient_id="synthetic", trial_ids=["T-1"])))
    assert job["state"] == "error"


def test_one_failing_trial_does_not_stop_the_job(monkeypatch):
    async def facts(record):
        return {}

    async def resolve(trial):
        if trial["id"] == "BAD":
            raise RuntimeError
        return {"state": "unresolved"}

    monkeypatch.setattr(main, "extract_patient", facts)
    monkeypatch.setattr(main, "resolve_trial", resolve)
    monkeypatch.setattr(main, "patient_record", lambda identifier: RECORD)
    monkeypatch.setattr(main, "trials_by_id", lambda: {"BAD": {"id": "BAD", "protocol": "BAD"}, "T-1": TRIAL})
    job = {"state": "queued", "done": 0, "total": 0, "message": "", "errors": []}
    asyncio.run(
        main.run_job(job, main.JobRequest(kind="screen", patient_id="synthetic", trial_ids=["BAD", "T-1"]))
    )
    assert job["state"] == "done" and job["done"] == 2 and len(job["errors"]) == 2


def test_local_api_does_not_expose_secret():
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    response = client.get("/api/bootstrap")
    assert response.status_code == 200
    assert "API_KEY" not in response.text
    assert client.get("/.env").status_code == 404
    cross_origin = client.post(
        "/api/jobs", json={"kind": "screen"}, headers={"origin": "https://external.example"}
    )
    assert cross_origin.status_code == 403
    assert TestClient(main.app, base_url="http://evil.example").get("/api/bootstrap").status_code == 400


def test_precompute_dry_run_and_budget(monkeypatch, capsys):
    monkeypatch.setattr(precompute, "patient_record", lambda identifier: RECORD)
    monkeypatch.setattr(precompute, "trials_by_id", lambda: {"T-1": TRIAL})
    storage.put("registry", "T-1", "T-1", REGISTRY)

    async def forbidden(*args, **kwargs):
        pytest.fail("Dry run must not execute")

    monkeypatch.setattr(precompute, "execute_plan", forbidden)
    assert precompute.main(["--patient", "synthetic", "--trial", "T-1", "--compare", "--max-new", "3"]) == 0
    assert '"new_calls": 3' in capsys.readouterr().out
    with pytest.raises(ValueError, match="max-new"):
        precompute.build_plan(["synthetic"], ["T-1"], pairs=True, max_new=2)
    with pytest.raises(ValueError, match="explicit"):
        precompute.build_plan([], [], pairs=False, max_new=2)
    storage.put("registry", "T-1", "T-1", {"state": "unresolved"})
    with pytest.raises(ValueError, match="no unique"):
        precompute.build_plan([], ["T-1"], pairs=False, max_new=2)


def test_demo_is_self_consistent():
    from demo import demo_data

    data = demo_data()
    assert [result["summary"]["badge"] for result in data["patient"]["results"]] == [True, False, False]
    for result in data["patient"]["results"]:
        for decision in result["decisions"]:
            assert all(evidence["start"] >= 0 for evidence in decision["evidence"])


def test_real_import_preserves_identifiers_and_sources():
    from repository import DATASET, ROOT, patient_record, patients, trials

    if DATASET is None or not any(ROOT.glob("*.xlsx")):
        pytest.skip("Local private dataset and study workbook required")
    cohort = patients()
    assert len(cohort) == 1830
    assert len(trials()) == 79
    assert len({trial["id"] for trial in trials()}) == 79
    patient = next(item for item in cohort if "C61" in item["diagnoses"] and not item["deceased"])
    record = patient_record(patient["id"])
    assert record["sources"]["cohort"]["text"].startswith("pseudonym:")
    assert select_sources(record)[0]
    for vital in record["vitals"].values():
        assert record["sources"][vital["source_id"]]["text"][vital["start"] : vital["end"]] == vital["quote"]
