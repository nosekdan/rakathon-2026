"""Refine criteria the rules could not structure, using the LOCAL LLM. Trial criteria are public text."""
from __future__ import annotations

import re

from .. import lexicon, llm
from ..schema import Criterion
from ..text.normalize import fold

SYSTEM = ("You convert one clinical-trial eligibility criterion into a structured form. Choose the concept ONLY from "
          "the given list; if none fits, use 'none'. operator: 'present' (patient must have it / exclusion if present), "
          "'absent' (inclusion requires it NOT to be present), or 'not_assessable' for administrative criteria.")


def _concepts() -> list[str]:
    return (list(lexicon.concepts()) + [f"therapy.{c}" for c in lexicon.drug_classes()] +
            ["other_malignancy", "adequate_organ_function", "none"])


def _grounded(concept: str, text: str) -> bool:
    folded = fold(text)
    c = lexicon.concepts().get(concept)
    if c is not None:
        return any(rx.search(folded) for rx in c.criteria)
    words = [w for w in re.findall(r"[a-z]{4,}", fold(lexicon.label(concept, "en"))) if w not in {"prior", "therapy"}]
    return bool(words) and all(w[:9] in folded for w in words)


def refine_with_llm(rec: dict, crits: list[Criterion]) -> list[Criterion]:
    if not llm.available():
        print("local LLM not available – skipping refinement")
        return crits
    allowed = _concepts()
    schema = {"type": "object", "properties": {
        "concept": {"type": "string", "enum": allowed},
        "operator": {"type": "string", "enum": ["present", "absent", "not_assessable"]}},
        "required": ["concept", "operator"], "additionalProperties": False}
    out = []
    for c in crits:
        if c.evaluability != "llm":
            out.append(c)
            continue
        res = llm.chat_json(SYSTEM, f"Criterion ({c.kind}): {c.text}\nConcepts: {', '.join(allowed)}", schema,
                            max_tokens=60)
        # Small local models map criteria to concepts unreliably (e.g. "immunodeficiency" -> immunotherapy), so a
        # proposal is accepted only when the concept is lexically grounded in the criterion text.
        if res and res["concept"] != "none" and res["operator"] != "not_assessable" and _grounded(res["concept"], c.text):
            op = "present" if c.kind == "exclusion" else res["operator"]
            c.concept, c.operator, c.value, c.evaluability, c.source = res["concept"], op, True, "auto", "llm"
        # otherwise stays 'llm' -> judged per patient by extract/llm_extract.py on demand
        out.append(c)
    return out
