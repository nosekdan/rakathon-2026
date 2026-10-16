from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["met", "not_met", "unknown", "missing"]


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
    evidence: list[Evidence] = Field(default_factory=list)
    action: str | None = None


class Comparison(BaseModel):
    decisions: list[Decision]


def locate_evidence(evidence: Evidence, sources: dict[str, dict]) -> dict | None:
    source = sources.get(evidence.source_id)
    if not source:
        return None
    start = source["text"].find(evidence.quote)
    if start < 0:
        return None
    return {**evidence.model_dump(), "start": start, "end": start + len(evidence.quote)}


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


def summarize(extraction: TrialExtraction, decisions: list[dict], recruitment: str) -> dict:
    states = {item["criterion_id"]: item["status"] for item in decisions}
    root = evaluate_logic(extraction.logic, states)
    counts = Counter(states.values())
    total = len(extraction.criteria)
    match_percent = round(100 * counts["met"] / total) if total else 0
    return {
        "status": root,
        "badge": root == "met" and extraction.complete and recruitment == "RECRUITING",
        "match_percent": match_percent,
        "counts": {state: counts[state] for state in ("met", "not_met", "unknown", "missing")},
        "total": total,
        "coverage_percent": round(100 * (counts["met"] + counts["not_met"]) / total) if total else 0,
    }
