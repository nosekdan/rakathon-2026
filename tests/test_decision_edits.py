"""Clinician decisions on criteria are applied at read time and recompute the summary (synthetic data)."""

from main import apply_decision_edits

ITEM = {"requirement": "r", "role": "inclusion", "source": {"source_id": "registry", "quote": "q"}}
CRITERIA = {
    "criteria": [
        {**ITEM, "id": "I1", "tm": {"evaluability": "auto"}},
        {**ITEM, "id": "I2", "tm": {"evaluability": "auto"}},
        {**ITEM, "id": "C1", "tm": {"evaluability": "not_assessable"}},
    ],
    "complete": True,
    "logic": {"op": "all", "children": [{"op": "ref", "criterion_id": c} for c in ("I1", "I2", "C1")]},
}
MATCH = {
    "trial_id": "T",
    "decisions": [
        {"criterion_id": "I1", "status": "met", "reason": "ok", "action": None},
        {"criterion_id": "I2", "status": "missing", "reason": "ECOG chybí.", "action": "Zapsat ECOG"},
        {"criterion_id": "C1", "status": "unknown", "reason": "Z dokumentace nelze posoudit.", "action": None},
    ],
    "summary": {"status": "missing", "match_percent": 50, "total": 3, "assessable": 2, "administrative": 1,
                "counts": {"met": 1, "not_met": 0, "unknown": 1, "missing": 1}, "coverage_percent": 33},
}


def edit(criterion, status, value=None):
    return {("T", criterion): {"trial_id": "T", "criterion_id": criterion, "status": status, "value": value,
                               "note": None, "edited_at": "2026-10-17T10:00:00+00:00"}}


def test_no_edits_leaves_match_untouched():
    assert apply_decision_edits(MATCH, CRITERIA, {}) is MATCH


def test_clinician_decision_replaces_status_and_recomputes():
    result = apply_decision_edits(MATCH, CRITERIA, edit("I2", "met", "ECOG 1."))
    decision = result["decisions"][1]
    assert decision["status"] == "met" and decision["reason"] == "Doplněno lékařem: ECOG 1."
    assert decision["original"]["status"] == "missing"
    assert result["summary"]["counts"]["met"] == 2 and result["summary"]["match_percent"] == 100
    assert MATCH["decisions"][1]["status"] == "missing"          # the stored comparison is not modified


def test_deciding_an_administrative_criterion_moves_it_into_the_denominator():
    edits = {**edit("I2", "met"), **edit("C1", "met", "souhlas podepsán")}
    summary = apply_decision_edits(MATCH, CRITERIA, edits)["summary"]
    assert summary["assessable"] == 3 and summary["administrative"] == 0
    assert summary["status"] == "met" and summary["match_percent"] == 100


def test_not_met_by_clinician_makes_study_not_met():
    summary = apply_decision_edits(MATCH, CRITERIA, edit("I2", "not_met"))["summary"]
    assert summary["status"] == "not_met" and summary["counts"]["not_met"] == 1


def test_confirming_the_screening_keeps_its_reason():
    decision = apply_decision_edits(MATCH, CRITERIA, edit("I1", "met"))["decisions"][0]
    assert decision["confirmed"] and decision["reason"] == "ok" and decision["status"] == "met"
    changed = apply_decision_edits(MATCH, CRITERIA, edit("I1", "not_met"))["decisions"][0]
    assert not changed["confirmed"] and changed["reason"] == "Doplněno lékařem."
