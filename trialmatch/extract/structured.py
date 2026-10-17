"""Tier-1 extraction from structured tables (ICD-10, labs, drugs by ATC, procedures). Highest trust."""
from __future__ import annotations

from functools import lru_cache

from .. import lexicon
from ..schema import Fact
from ..text.normalize import fold

# Codes that do not count as a separate primary malignancy (metastases, uncertain, non-melanoma skin).
_NOT_PRIMARY = ("C77", "C78", "C79", "C80", "C44", "C97")
RADIOTHERAPY_DOC_TYPES = ("Rato", "Ratb")  # radiotherapy treatment room / brachytherapy reports


def _d(ts) -> str | None:
    return str(ts)[:10] if ts is not None else None


def diagnosis_facts(icd_codes: list[str]) -> list[Fact]:
    facts = []
    for code in icd_codes:
        facts.append(Fact(concept="diagnosis", value=code, method="structured", source_type="diagnosis",
                          source_id=code, confidence=1.0, snippet=f"MKN-10: {code}"))
        for key, c in lexicon.concepts().items():
            if any(code.startswith(p) for p in c.icd):
                facts.append(Fact(concept=key, value=True, method="structured", source_type="diagnosis",
                                  source_id=code, confidence=0.95, snippet=f"MKN-10: {code}"))
    primaries = sorted({c[:3] for c in icd_codes if c[:1] in "C" and not c.startswith(_NOT_PRIMARY)})
    if primaries:
        facts.append(Fact(concept="primary_malignancies", value=primaries, method="structured",
                          source_type="diagnosis", confidence=1.0, snippet="MKN-10: " + ", ".join(primaries)))
    return facts


@lru_cache(maxsize=1)
def _lab_index() -> dict[str, tuple[str, float]]:
    return {name: (key, lab.local_factor) for key, lab in lexicon.labs().items() for name in lab.local_names}


def lab_facts(rows) -> list[Fact]:
    """rows: iterable of (lab_id, ts, name, value, unit, lower, upper)."""
    idx = _lab_index()
    facts = []
    for lab_id, ts, name, value, unit, lower, upper in rows:
        if name not in idx or value is None:
            continue
        key, factor = idx[name]
        uln = (value / upper) if upper else None
        facts.append(Fact(concept=f"lab.{key}", value=round(value * factor, 4), unit=lexicon.labs()[key].unit,
                          date=_d(ts), method="structured", source_type="lab", source_id=lab_id, confidence=1.0,
                          snippet=f"{name} {value} {unit or ''} (norma {lower if lower is not None else '?'}–"
                                  f"{upper if upper is not None else '?'})",
                          extra={"uln_ratio": round(uln, 3) if uln else None, "raw_value": value, "raw_unit": unit,
                                 "upper": upper, "lower": lower}))
    return facts


@lru_cache(maxsize=1)
def _drug_by_atc() -> dict[str, lexicon.Drug]:
    return {d.atc: d for d in lexicon.drugs() if len(d.atc) >= 7}


@lru_cache(maxsize=20000)
def _procedure_concepts(name: str) -> tuple[str, ...]:
    folded = fold(name or "")
    keys = ("therapy.radiotherapy", "procedure.prostatectomy", "procedure.orchiectomy", "procedure.nephrectomy")
    out = []
    for k in keys:
        if any(rx.search(folded) for rx in lexicon.concepts()[k].patient):
            out.append(k)
    return tuple(out)


def treatment_facts(rows) -> list[Fact]:
    """rows: iterable of (kind, date, code, name, atc)."""
    facts, seen = [], set()
    by_atc = _drug_by_atc()
    for kind, date, code, name, atc in rows:
        if atc and atc in by_atc:
            d = by_atc[atc]
            base = {"value": True, "date": _d(date), "method": "structured", "source_type": "treatment",
                    "source_id": f"{kind}:{code}", "confidence": 1.0, "snippet": f"{kind}: {name} ({atc})"}
            facts.append(Fact(concept=f"drug.{d.name}", extra={"atc": atc, "classes": d.classes}, **base))
            for cls in d.classes + ([] if "adt" in d.classes else ["systemic"]):
                facts.append(Fact(concept=f"therapy.{cls}", extra={"drug": d.name}, **base))
        elif kind == "vykon":
            for concept in _procedure_concepts(name):
                key = (concept, _d(date))
                if key in seen:
                    continue
                seen.add(key)
                facts.append(Fact(concept=concept, value=True, date=_d(date), method="structured",
                                  source_type="treatment", source_id=f"vykon:{code}", confidence=0.95,
                                  snippet=f"Výkon: {name}"))
    return facts


def radiotherapy_doc_facts(rows) -> list[Fact]:
    """rows: (doc_id, ts, type, type_label) of radiotherapy treatment-room reports -> radiotherapy was given."""
    facts = []
    for doc_id, ts, typ, label in rows:
        if typ and typ.startswith(RADIOTHERAPY_DOC_TYPES):
            facts.append(Fact(concept="therapy.radiotherapy", value=True, date=_d(ts), method="structured",
                              source_type="document", source_id=doc_id, confidence=0.95,
                              snippet=f"Zpráva typu: {label}"))
    return facts
