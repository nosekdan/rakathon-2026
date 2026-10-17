"""Load and compile the YAML lexicons once."""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import yaml

from ..config import LEXICON_DIR


@dataclass
class Concept:
    key: str
    label_cs: str
    label_en: str
    icd: list[str]
    patient: list[re.Pattern]
    criteria: list[re.Pattern]
    policy: str
    search_terms: str = ""
    action: str = ""


@dataclass
class Drug:
    name: str
    atc: str
    classes: list[str]
    patterns: list[re.Pattern]


@dataclass
class Lab:
    key: str
    label_cs: str
    label_en: str
    local_names: list[str]
    unit: str
    local_factor: float
    criteria: list[re.Pattern]
    criteria_units: list[tuple[re.Pattern, float]]
    uln_capable: bool
    action: str = ""
    modifiable: bool = False


def _load(name: str) -> dict:
    return yaml.safe_load((LEXICON_DIR / name).read_text(encoding="utf-8"))


def _rx(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p) for p in patterns]


@lru_cache(maxsize=1)
def concepts() -> dict[str, Concept]:
    return {
        k: Concept(k, v["label"]["cs"], v["label"]["en"], v.get("icd", []), _rx(v.get("patient", [])),
                   _rx(v.get("criteria", [])), v.get("policy", "ever"), v.get("search_terms", ""), v.get("action", ""))
        for k, v in _load("concepts.yaml").items()
    }


@lru_cache(maxsize=1)
def drugs() -> list[Drug]:
    return [Drug(d["name"], d["atc"], d["classes"], _rx(d["patterns"])) for d in _load("drugs.yaml")["drugs"]]


@lru_cache(maxsize=1)
def drug_classes() -> dict[str, dict]:
    return _load("drugs.yaml")["classes"]


@lru_cache(maxsize=1)
def labs() -> dict[str, Lab]:
    return {
        k: Lab(k, v["label"]["cs"], v["label"]["en"], v["local_names"], v["unit"], float(v.get("local_factor", 1)),
               _rx(v["criteria"]), [(re.compile(u), float(f)) for u, f in v.get("criteria_units", {}).items()],
               bool(v.get("uln_capable", False)), v.get("action", ""), bool(v.get("modifiable", False)))
        for k, v in _load("labs.yaml").items()
    }


def label(concept: str, lang: str = "cs") -> str:
    """Human-readable label for any concept key (concept, lab.*, therapy.*, drug.*)."""
    if concept in concepts():
        c = concepts()[concept]
        return c.label_cs if lang == "cs" else c.label_en
    if concept.startswith("lab."):
        lab = labs().get(concept[4:])
        if lab:
            return lab.label_cs if lang == "cs" else lab.label_en
    if concept.startswith("therapy."):
        cls = drug_classes().get(concept[8:])
        if cls:
            return f"Předchozí léčba: {cls['cs']}" if lang == "cs" else f"Prior therapy: {cls['en']}"
    if concept.startswith("drug."):
        return f"Lék: {concept[5:]}" if lang == "cs" else f"Drug: {concept[5:]}"
    return BUILTIN_LABELS.get(concept, {}).get(lang, concept)


def action(concept: str) -> str:
    """Suggested step for the doctor when information for a concept is missing."""
    if concept in concepts() and concepts()[concept].action:
        return concepts()[concept].action
    if concept.startswith("lab.") and concept[4:] in labs():
        return labs()[concept[4:]].action or f"Odběr: {labs()[concept[4:]].label_cs}"
    if concept.startswith(("therapy.", "drug.")):
        return "Doplnit anamnézu předchozí léčby"
    return {"ecog": "Zapsat ECOG při příští kontrole", "diagnosis": "Doplnit diagnózu (MKN-10)",
            "age": "Doplnit věk", "sex": "Doplnit pohlaví",
            "adequate_organ_function": "Odběr KO + biochemie (jaterní a renální testy)"}.get(concept, "Ověřit v dokumentaci")


def search_terms(concept: str) -> str:
    """Human-readable list of what the extractor looks for – shown when nothing was found."""
    if concept in concepts():
        return concepts()[concept].search_terms
    if concept.startswith("therapy."):
        cls = concept[8:]
        names = [d.name for d in drugs() if cls in d.classes and not d.name.startswith("generic")]
        return ", ".join(names[:8]) + (" …" if len(names) > 8 else "")
    if concept.startswith("drug."):
        return concept[5:]
    return ""


BUILTIN_LABELS = {
    "diagnosis": {"cs": "Diagnóza (MKN-10)", "en": "Diagnosis (ICD-10)"},
    "sex": {"cs": "Pohlaví", "en": "Sex"},
    "age": {"cs": "Věk", "en": "Age"},
    "ecog": {"cs": "Výkonnostní stav ECOG", "en": "ECOG performance status"},
    "vitals.height": {"cs": "Výška", "en": "Height"},
    "adequate_organ_function": {"cs": "Přiměřená orgánová funkce (krevní obraz, játra, ledviny)",
                                "en": "Adequate organ function"},
    "tnm": {"cs": "TNM klasifikace", "en": "TNM stage"},
    "gleason": {"cs": "Gleason skóre", "en": "Gleason score"},
    "isup": {"cs": "ISUP grade", "en": "ISUP grade"},
    "other_malignancy": {"cs": "Jiná malignita", "en": "Other malignancy"},
    "therapy.systemic": {"cs": "Předchozí systémová protinádorová léčba", "en": "Prior systemic therapy"},
}
