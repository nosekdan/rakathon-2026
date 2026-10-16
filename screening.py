"""Model schemas and pure, deterministic screening logic. No I/O."""

import re
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["met", "not_met", "unknown", "missing"]
STATUSES: tuple[Status, ...] = ("met", "not_met", "unknown", "missing")


class Evidence(BaseModel):
    source_id: str
    quote: str = Field(min_length=1)


class Criterion(BaseModel):
    id: str
    requirement: str
    role: Literal["inclusion", "exclusion"]
    source: Evidence
    trigger: Literal["none", "progression", "washout", "before_treatment"] = "none"


class Logic(BaseModel):
    op: Literal["all", "any", "ref"]
    criterion_id: str | None = None
    children: list["Logic"] = Field(default_factory=list)


class TrialExtraction(BaseModel):
    criteria: list[Criterion]
    logic: Logic
    complete: bool
    limitations: list[str] = Field(default_factory=list)


class Fact(BaseModel):
    name: str
    value: str
    date: str | None = None
    evidence: Evidence


class PatientExtraction(BaseModel):
    facts: list[Fact]
    limitations: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    criterion_id: str
    status: Status
    reason: str
    fact_ids: list[str] = Field(default_factory=list)
    action: str | None = None


class Comparison(BaseModel):
    decisions: list[Decision]


def locate_evidence(evidence: Evidence, sources: dict[str, dict]) -> dict | None:
    """Find a quote in its source; tolerate whitespace differences, return the exact original span."""
    source = sources.get(evidence.source_id)
    if not source:
        return None
    text = source["text"]
    start = text.find(evidence.quote)
    if start >= 0:
        end = start + len(evidence.quote)
    else:
        words = evidence.quote.split()
        match = re.search(r"\s+".join(map(re.escape, words)), text) if words else None
        if not match:
            return None
        start, end = match.span()
    return {"source_id": evidence.source_id, "quote": text[start:end], "start": start, "end": end}


def validate_logic(logic: Logic, criterion_ids: set[str]) -> None:
    seen: set[str] = set()

    def visit(node: Logic) -> None:
        if node.op == "ref":
            if node.criterion_id not in criterion_ids or node.children:
                raise ValueError("Invalid criterion reference")
            seen.add(node.criterion_id)
        else:
            if not node.children or node.criterion_id is not None:
                raise ValueError("Empty or invalid logic group")
            for child in node.children:
                visit(child)

    visit(logic)
    if seen != criterion_ids:
        raise ValueError("Criteria omitted from logic")


def evaluate_logic(logic: Logic, decisions: dict[str, Status]) -> Status:
    if logic.op == "ref":
        return decisions.get(logic.criterion_id or "", "missing")
    states = [evaluate_logic(child, decisions) for child in logic.children]
    if not states:
        return "unknown"
    if logic.op == "all":
        if "not_met" in states:
            return "not_met"
        if all(state == "met" for state in states):
            return "met"
    else:
        if "met" in states:
            return "met"
        if all(state == "not_met" for state in states):
            return "not_met"
    return "unknown" if "unknown" in states else "missing"


def verify_decisions(
    criteria: list[Criterion], decisions: list[Decision], facts: dict[str, dict]
) -> list[dict]:
    """One decision per criterion. Claims without valid fact citations are downgraded to unknown."""
    by_id: dict[str, Decision] = {}
    duplicates = set()
    for decision in decisions:
        if decision.criterion_id in by_id:
            duplicates.add(decision.criterion_id)
        by_id[decision.criterion_id] = decision
    verified = []
    for criterion in criteria:
        decision = by_id.get(criterion.id)
        if decision is None or criterion.id in duplicates:
            verified.append(
                {
                    "criterion_id": criterion.id,
                    "status": "unknown",
                    "reason": "Model neposkytl jednoznačný výsledek.",
                    "action": "Ruční kontrola kritéria.",
                    "fact_ids": [],
                    "evidence": [],
                }
            )
            continue
        requested = list(dict.fromkeys(decision.fact_ids))
        cited = [facts[identifier] for identifier in requested if identifier in facts]
        item = {
            "criterion_id": criterion.id,
            "status": decision.status,
            "reason": decision.reason,
            "action": decision.action,
            "fact_ids": [fact["id"] for fact in cited],
            "evidence": [fact["evidence"] for fact in cited],
        }
        if len(cited) != len(requested) or (decision.status in ("met", "not_met") and not cited):
            item.update(
                status="unknown",
                reason="Tvrzení modelu nemá ověřitelný doklad. Vyžaduje kontrolu.",
                action="Ověřit v původní dokumentaci.",
            )
        verified.append(item)
    return verified


def summarize(extraction: TrialExtraction, decisions: list[dict]) -> dict:
    states = {item["criterion_id"]: item["status"] for item in decisions}
    counts = Counter(states.values())
    total = len(extraction.criteria)
    return {
        "status": evaluate_logic(extraction.logic, states),
        "match_percent": round(100 * counts["met"] / total) if total else 0,
        "counts": {state: counts[state] for state in STATUSES},
        "total": total,
        "coverage_percent": round(100 * (counts["met"] + counts["not_met"]) / total) if total else 0,
    }


def badge(
    status: Status,
    *,
    criteria_complete: bool,
    facts_complete: bool,
    recruiting: bool,
    site_recruiting: bool,
    deceased: bool,
) -> bool:
    """The positive alert: every published criterion met on complete inputs at a recruiting local site."""
    return (
        status == "met"
        and criteria_complete
        and facts_complete
        and recruiting
        and site_recruiting
        and not deceased
    )


def future_watch(extraction: TrialExtraction, decisions: list[dict]) -> list[dict]:
    """Flag a re-screen when one progression/washout criterion is the only thing blocking eligibility."""
    states: dict[str, Status] = {item["criterion_id"]: item["status"] for item in decisions}
    blockers = [identifier for identifier, state in states.items() if state != "met"]
    if len(blockers) != 1 or states[blockers[0]] != "not_met":
        return []
    criterion = next(item for item in extraction.criteria if item.id == blockers[0])
    if criterion.trigger not in ("progression", "washout"):
        return []
    if evaluate_logic(extraction.logic, states) == "met":
        return []
    if evaluate_logic(extraction.logic, {**states, criterion.id: "met"}) != "met":
        return []
    return [
        {
            "criterion_id": criterion.id,
            "trigger": criterion.trigger,
            "text": "Znovu posoudit po dokumentované progresi."
            if criterion.trigger == "progression"
            else "Znovu posoudit po uplynutí protokolového odstupu.",
            "caveat": "Budoucí způsobilost není zaručena. Znovu ověřit všechny podmínky a nábor.",
        }
    ]
