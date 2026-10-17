"""Clinician edits of a study's criteria (remove / add / confirm) apply at read time (synthetic data)."""

from engine_trialmatch import clinician_criteria
from main import apply_criteria_edits, criteria_review, edit_version, effective_criteria

ITEM = {"requirement": "r", "role": "inclusion", "source": {"source_id": "registry", "quote": "q"}}
CRITERIA = {
    "key": "base",
    "criteria": [{**ITEM, "id": "I1", "tm": {"evaluability": "auto"}}, {**ITEM, "id": "I2", "tm": {"evaluability": "auto"}}],
    "logic": {"op": "all", "children": [{"op": "ref", "criterion_id": "I1"}, {"op": "ref", "criterion_id": "I2"}]},
    "complete": True,
}
MATCH = {
    "trial_id": "T",
    "decisions": [
        {"criterion_id": "I1", "status": "met", "reason": "ok", "action": None},
        {"criterion_id": "I2", "status": "not_met", "reason": "ne", "action": None},
    ],
    "summary": {"status": "not_met", "match_percent": 50, "total": 2, "assessable": 2, "administrative": 0,
                "counts": {"met": 1, "not_met": 1, "unknown": 0, "missing": 0}, "coverage_percent": 100},
    "future_watch": [],
}


def edit(removed=(), added=()):
    added = list(added)
    return {"base_key": "base", "removed": list(removed), "added": added, "verified": False,
            "version": edit_version(list(removed), added)}


def test_removing_a_criterion_drops_its_decision_and_recomputes():
    change = edit(removed=["I2"])
    criteria = effective_criteria(CRITERIA, change)
    result = apply_criteria_edits(MATCH, criteria, change, None)
    assert [item["id"] for item in criteria["criteria"]] == ["I1"]
    assert result["summary"]["status"] == "met"
    assert result["summary"]["match_percent"] == 100
    assert CRITERIA["criteria"][1]["id"] == "I2", "the extraction itself is never changed"


def test_added_criterion_is_pending_until_recomputed_then_uses_its_decision():
    added = [{"number": 1, "role": "inclusion", "text": "ECOG ≤ 1",
              "criteria": clinician_criteria("T", "inclusion", "ECOG ≤ 1", 1, ["C61"])}]
    change = edit(added=added)
    criteria = effective_criteria(CRITERIA, change)
    pending = apply_criteria_edits(MATCH, criteria, change, None)
    assert pending["decisions"][-1]["criterion_id"] == "L1"
    assert pending["decisions"][-1]["status"] == "missing"
    computed = {"version": change["version"], "decisions": [
        {"criterion_id": "L1", "status": "met", "reason": "ECOG 1", "action": None, "fact_ids": [], "evidence": []}]}
    assert apply_criteria_edits(MATCH, criteria, change, computed)["summary"]["counts"]["met"] == 2
    stale = {**computed, "version": "older"}
    assert apply_criteria_edits(MATCH, criteria, change, stale)["decisions"][-1]["status"] == "missing"


def test_unreadable_text_is_left_to_the_clinician():
    [item] = clinician_criteria("T", "inclusion", "Ochota spolupracovat", 1, ["C61"])
    assert item["requirement"].startswith("Posoudí lékař")
    assert item["source"] == {"source_id": "clinician", "quote": "Ochota spolupracovat"}


def test_review_applies_only_to_the_extraction_it_was_made_for():
    confirmed = {**edit(removed=["I2"]), "verified": True, "verified_at": "2026-10-17"}
    assert criteria_review(confirmed, "base")["verified"]
    newer = criteria_review(confirmed, "new-registry-text")
    assert newer["stale"] and not newer["applied"] and not newer["verified"]
    assert criteria_review(None, "base") == {"verified": False, "verified_at": None, "edited": False,
                                             "applied": False, "stale": False}
