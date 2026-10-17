"""The rule engine behind the pipeline stages, on synthetic data only (no dataset, no model calls)."""

import engine_trialmatch as engine
import repository
from screening import Logic, validate_logic
from trialmatch import llm

NOTE = ("Dg.: C61 adenokarcinom prostaty, st.p. RAPE 2021. Nyní mCRPC, mnohočetné mts skeletu. "
        "PS WHO = 1. Bez mozkových metastáz.\nZávěr: pokračujeme v ADT.")
RECORD = {
    "id": "PAC-TEST000001",
    "as_of": None,
    "snapshot_at": "2026-05-02",
    "raw": {"vek": "70", "vek_pasmo": "", "pohlavi": "male", "diagnozy": "C61"},
    "sources": {
        "cohort": {"id": "cohort", "title": "kohorta", "date": None, "kind": "demographics",
                   "text": "vek: 70\npohlavi: male\ndiagnozy: C61"},
        "DOC-1": {"id": "DOC-1", "title": "Zpráva", "date": "2026-05-01", "kind": "DocumentReference", "text": NOTE},
        "lab-1": {"id": "lab-1", "title": "HGB", "date": "2026-05-02", "kind": "Observation",
                  "text": 'HGB: 131 g/l\nDate: 2026-05-02\nReference: [{"low": {"value": 135}, "high": {"value": 175}}]'},
    },
    "latest_labs": [{"name": "HGB", "value": 131, "unit": "g/l", "date": "2026-05-02", "source_id": "lab-1"}],
}
REGISTRY_LINES = [
    "Minimum age: 18 Years", "Maximum age: not specified", "Sex: MALE", "Healthy volunteers: False",
    "Inclusion Criteria:", "",
    "* Histologically confirmed adenocarcinoma of the prostate.",
    "* ECOG performance status of 0 or 1.",
    "* Hemoglobin ≥9.0 g/dL; platelets ≥100 x 10\\^9/L.",
    "", "Exclusion Criteria:", "",
    "* Known brain metastases.",
    "* Has symptomatic pleural effusion requiring drainage.",
    ]
REGISTRY_TEXT = "\n".join(REGISTRY_LINES)
REGISTRY = {
    "nct_id": "NCT00000000",
    "sources": {"registry": {"id": "registry", "text": REGISTRY_TEXT}},
}
TRIAL = {"id": "TEST-TRIAL", "diagnosis": "Karcinom prostaty", "title": "Synthetic"}


def facts_and_criteria():
    facts = engine.extract_patient(RECORD, {"key": "fk", "payload": {"sources": list(RECORD["sources"].values())}},
                                   "trialmatch")
    criteria = engine.extract_trial(TRIAL, REGISTRY, {"key": "ck"}, "trialmatch")
    return facts, criteria


def test_facts_have_exact_located_quotes():
    facts, _ = facts_and_criteria()
    assert facts["dropped_citations"] == 0 and facts["facts"]
    for fact in facts["facts"]:
        source = RECORD["sources"][fact["evidence"]["source_id"]]
        assert source["text"][fact["evidence"]["start"]:fact["evidence"]["end"]] == fact["evidence"]["quote"]
    names = {fact["name"] for fact in facts["facts"]}
    assert {"Výkonnostní stav ECOG", "Hemoglobin", "Kastračně rezistentní karcinom prostaty"} <= names


def test_criteria_are_quoted_from_registry_and_logic_is_valid():
    _, criteria = facts_and_criteria()
    ids = {item["id"] for item in criteria["criteria"]}
    validate_logic(Logic.model_validate(criteria["logic"]), ids)
    assert criteria["complete"]
    by_req = {item["requirement"]: item for item in criteria["criteria"]}
    assert "Výkonnostní stav ECOG ≤ 1" in by_req
    assert any(r.startswith("Bez: Metastázy CNS") for r in by_req)
    platelets = next(item for item in criteria["criteria"] if item["requirement"].startswith("Trombocyty"))
    assert "10\\^9/L" in platelets["source"]["quote"]          # escaped registry text is quoted exactly


def test_compare_gives_cited_decisions(monkeypatch):
    monkeypatch.setattr(repository, "patient_record", lambda *_: RECORD)
    facts, criteria = facts_and_criteria()
    result = engine.compare(TRIAL, facts, criteria, "trialmatch", "screen-v2")
    states = {d["criterion_id"]: d for d in result["decisions"]}
    ecog = next(d for d in result["decisions"] if d["status"] == "met" and "ECOG" in d["reason"])
    assert ecog["fact_ids"] and ecog["evidence"]
    assert all(d["fact_ids"] for d in states.values() if d["status"] in ("met", "not_met"))
    assert result["summary"]["counts"]["met"] >= 5


def test_local_llm_never_excludes(monkeypatch, tmp_path):
    from trialmatch.extract import llm_extract

    monkeypatch.setattr(llm_extract, "CACHE", tmp_path / "llm_cache.jsonl")  # keep the real cache clean
    monkeypatch.setattr(llm_extract, "_cache", None)
    monkeypatch.setattr(repository, "patient_record", lambda *_: {
        **RECORD, "sources": {**RECORD["sources"], "DOC-2": {
            "id": "DOC-2", "title": "Zpráva", "date": "2026-05-02", "kind": "DocumentReference",
            "text": "Pravostranný fluidothorax, punkce 800 ml."}}})
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: {
        "answer": "yes", "evidence_quote": "Pravostranný fluidothorax, punkce 800 ml.", "reason_cs": "Výpotek."})
    facts, criteria = facts_and_criteria()
    result = engine.compare(TRIAL, facts, criteria, "trialmatch+llm", "screen-v2")
    effusion = next(item["id"] for item in criteria["criteria"] if "pleural" in item["source"]["quote"])
    decision = next(d for d in result["decisions"] if d["criterion_id"] == effusion)
    assert decision["status"] == "unknown"


def test_match_percent_counts_only_assessable_criteria(monkeypatch):
    monkeypatch.setattr(repository, "patient_record", lambda *_: RECORD)
    facts, criteria = facts_and_criteria()
    criteria["criteria"].append({**criteria["criteria"][-1], "id": "E99", "requirement": "Nelze posoudit: consent",
                                 "tm": {**criteria["criteria"][-1]["tm"], "id": "TEST-TRIAL:E99", "concept": None,
                                        "evaluability": "not_assessable"}})
    criteria["logic"]["children"].append({"op": "ref", "criterion_id": "E99"})
    summary = engine.compare(TRIAL, facts, criteria, "trialmatch", "screen-v2")["summary"]
    assert summary["administrative"] == 1 and summary["assessable"] == summary["total"] - 1
    assert summary["match_percent"] == round(100 * summary["counts"]["met"] / summary["assessable"])
    # silence about an exclusion stays "missing" and is not counted as met
    decisions = engine.compare(TRIAL, facts, criteria, "trialmatch", "screen-v2")["decisions"]
    effusion = next(item["id"] for item in criteria["criteria"] if "pleural" in item["source"]["quote"])
    assert next(d for d in decisions if d["criterion_id"] == effusion)["status"] == "missing"
