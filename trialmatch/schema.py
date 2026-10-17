"""Core data types shared by extraction, trial structuring and matching."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Fact:
    """One normalized observation about a patient, always with provenance."""
    concept: str                      # e.g. "ecog", "lab.psa", "metastasis.brain", "therapy.arpi", "drug.enzalutamide"
    value: Any = True                 # number / string / bool
    unit: str | None = None
    date: str | None = None           # ISO date (shifted per patient, intervals valid)
    polarity: str = "affirmed"        # affirmed | negated
    certainty: str = "certain"        # certain | uncertain | hypothetical
    experiencer: str = "patient"      # patient | other
    method: str = "rule"              # structured | rule | llm
    source_type: str = "document"     # document | imaging | lab | treatment | diagnosis | upload | demographics
    source_id: str | None = None
    section: str | None = None
    snippet: str | None = None        # sentence the fact was read from (evidence)
    span: tuple[int, int] | None = None
    confidence: float = 0.8
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Criterion:
    """One atomic eligibility criterion in machine-readable form."""
    id: str
    trial_id: str
    kind: str                         # inclusion | exclusion
    text: str                         # original criterion text (English)
    concept: str | None = None        # see lexicon; None = not structured (needs LLM / manual review)
    operator: str | None = None       # >= <= > < = in present absent
    value: Any = None
    unit: str | None = None
    uln: bool = False                 # value is a multiple of the upper limit of normal
    time_window_days: int | None = None
    evaluability: str = "auto"        # auto | llm | not_assessable
    hard_gate: bool = False
    weight: float = 1.0
    source: str = "rule"              # rule | llm | registry | manual
    status: str = "draft"             # draft | reviewed
    note: str | None = None
    text_span: list[int] | None = None  # [start, end] of the phrase the rule recognised in `text` (explainability)
    qualified: bool = False           # criterion has extra conditions the rule cannot check -> never a hard exclusion

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Criterion:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


MET, NOT_MET, UNKNOWN, REVIEW, NOT_ASSESSABLE = "MET", "NOT_MET", "UNKNOWN", "NEEDS_REVIEW", "NOT_ASSESSABLE"


@dataclass
class CriterionResult:
    criterion: Criterion
    status: str
    confidence: float = 1.0
    explanation: str = ""
    evidence: list[Fact] = field(default_factory=list)
    decided_by: str = "rule"          # structured | rule | llm | doctor  (an "llm" result can never exclude)
    searched: dict | None = None      # what was looked for when nothing was found (terms, documents, date range)
    flags: dict = field(default_factory=dict)  # e.g. {"stale": True}, {"eligible_from": "2026-11-12"}

    def to_dict(self) -> dict:
        return {"criterion": self.criterion.to_dict(), "status": self.status, "confidence": self.confidence,
                "explanation": self.explanation, "evidence": [e.to_dict() for e in self.evidence],
                "decided_by": self.decided_by, "searched": self.searched, "flags": self.flags}
