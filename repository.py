"""Read the local dataset: cohort CSV, per-patient FHIR bundles and the study workbook."""

import base64
import csv
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from openpyxl import load_workbook

ROOT = Path(__file__).parent
DATASET = next(ROOT.glob("rakathon-dataset-*/"), None)
SOURCE_BUDGET = 90_000
NOTE_KINDS = ("DocumentReference", "DiagnosticReport")
MEDICATION_KINDS = ("MedicationRequest", "MedicationAdministration")
VITAL_PATTERNS = {
    "height": r"\bv[ýy]ška\s*:?\s*(\d{2,3}(?:[.,]\d+)?)\s*cm\b",
    "weight": r"\b(?:v[áa]ha|hmotnost)\s*:?\s*(\d{2,3}(?:[.,]\d+)?)\s*kg\b",
    "ecog": r"\b(?:ECOG(?:\s*PS)?|WHO\s*PS|PS)\s*:?\s*([0-4])\b",
}
# Cohort columns that are fixed facts about the person; the rest (diagnoses, counts, death) summarize all time.
DEMOGRAPHIC_COLUMNS = ("pseudonym", "pohlavi", "rok_narozeni", "rok_narozeni_pasmo", "vek", "vek_pasmo")


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


@lru_cache(maxsize=1)
def patients() -> list[dict]:
    if DATASET is None:
        return []
    with (DATASET / "kohorta.csv").open(encoding="utf-8-sig", newline="") as handle:
        records = list(csv.DictReader(handle, delimiter=";"))
    return [
        {
            "id": row["pseudonym"],
            "name": f"Pacient {row['pseudonym'][-6:]}",
            "sex": row["pohlavi"],
            "age": row["vek"] or row["vek_pasmo"] or None,
            "birth_year": row["rok_narozeni"] or row["rok_narozeni_pasmo"] or None,
            "diagnoses": row["diagnozy"],
            "target": row["ma_cilovou_dg"] == "ano",
            "deceased": row["zesnuly"] == "ano",
            "notes": int(row["zprav"] or 0),
            "labs": int(row["lab_vysledku"] or 0),
            "raw": row,
        }
        for row in records
    ]


@lru_cache(maxsize=1)
def patients_by_id() -> dict[str, dict]:
    return {patient["id"]: patient for patient in patients()}


def trial_slug(protocol: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", protocol).strip("-").upper() or "TRIAL"


@lru_cache(maxsize=1)
def trials() -> list[dict]:
    path = next(ROOT.glob("*.xlsx"), None)
    if not path:
        return []
    workbook = load_workbook(path, read_only=True, data_only=True)
    result = []
    seen: dict[str, int] = {}
    for row_number, row in enumerate(workbook.active.values, 1):
        if row_number <= 2 or len(row) < 8 or not row[2] or not row[4]:
            continue
        protocol = str(row[2]).strip()
        # IDs derive from the protocol, not the row, so cached registry/criteria survive workbook edits.
        slug = trial_slug(protocol)
        seen[slug] = seen.get(slug, 0) + 1
        result.append(
            {
                "id": slug if seen[slug] == 1 else f"{slug}-{seen[slug]}",
                "hospital_id": str(row[0] or ""),
                "protocol": protocol,
                "title": str(row[4]).strip(),
                "diagnosis": str(row[7] or ""),
                "sponsor": str(row[3] or ""),
                "phase": str(row[6] or ""),
            }
        )
    workbook.close()
    return result


@lru_cache(maxsize=1)
def trials_by_id() -> dict[str, dict]:
    return {trial["id"]: trial for trial in trials()}


def code_label(code: dict) -> str:
    return str(
        code.get("text")
        or next((item.get("display") or item.get("code", "") for item in code.get("coding", [])), "")
        or ""
    )


def patient_record(identifier: str, as_of: str | None = None) -> dict:
    """The patient's record; with as_of (YYYY-MM-DD), only what was documented by that day."""
    patient = patients_by_id().get(identifier)
    if patient is None or DATASET is None:
        raise LookupError("Patient not found")
    path = DATASET / "fhir" / f"{identifier}.json"
    return _patient_record(identifier, path, path.stat().st_mtime_ns, as_of)


@lru_cache(maxsize=16)
def _patient_record(identifier: str, path: Path, mtime: int, as_of: str | None) -> dict:
    patient = patients_by_id()[identifier]
    bundle = json.loads(path.read_text(encoding="utf-8"))
    cohort = patient["raw"] if as_of is None else {key: patient["raw"][key] for key in DEMOGRAPHIC_COLUMNS}
    sources: dict[str, dict] = {
        "cohort": {
            "id": "cohort",
            "title": "kohorta.csv / " + identifier,
            "text": "\n".join(f"{key}: {value}" for key, value in cohort.items()),
            "date": None,
            "kind": "demographics",
        }
    }
    labs = []
    treatments = []
    for index, entry in enumerate(bundle.get("entry", [])):
        resource = entry["resource"]
        kind = resource["resourceType"]
        source_id = resource.get("id") or f"{kind}-{index}"
        date = (
            resource.get("effectiveDateTime")
            or resource.get("date")
            or resource.get("authoredOn")
            or resource.get("effectivePeriod", {}).get("start")
        )
        if as_of and (not date or date[:10] > as_of):
            # Undated resources are excluded too: they could come from after the cut-off.
            continue
        title = code_label(resource.get("code", resource.get("type", {}))) or kind
        text = ""
        if kind == "DocumentReference":
            for content in resource.get("content", []):
                attachment = content.get("attachment", {})
                if attachment.get("contentType") == "text/plain" and attachment.get("data"):
                    text = base64.b64decode(attachment["data"]).decode("utf-8", errors="replace")
                    break
        elif kind == "DiagnosticReport" and resource.get("conclusion"):
            text = resource["conclusion"]
        elif kind == "Observation":
            quantity = resource.get("valueQuantity", {})
            value = quantity.get("value", resource.get("valueString", ""))
            unit = quantity.get("unit", "")
            reference = json.dumps(resource.get("referenceRange", []), ensure_ascii=False)
            text = f"{title}: {value} {unit}\nDate: {date}\nReference: {reference}"
            labs.append({"name": title, "value": value, "unit": unit, "date": date, "source_id": source_id})
        elif kind in MEDICATION_KINDS:
            medication = code_label(resource.get("medicationCodeableConcept", {}))
            dosage = json.dumps(
                resource.get("dosageInstruction", resource.get("dosage", {})), ensure_ascii=False
            )
            text = f"{medication}\nDate: {date}\n{dosage}"
            treatments.append({"name": medication, "date": date, "source_id": source_id})
        if text:
            sources[source_id] = {"id": source_id, "title": title, "date": date, "text": text, "kind": kind}
    notes = sorted(
        (source for source in sources.values() if source["kind"] in NOTE_KINDS),
        key=lambda source: source["date"] or "",
        reverse=True,
    )
    dated = [source["date"] for source in sources.values() if source["date"]]
    vitals = {}
    for name, pattern in VITAL_PATTERNS.items():
        for source in notes:
            match = re.search(pattern, source["text"], re.IGNORECASE)
            if match:
                vitals[name] = {
                    "value": match.group(1),
                    "source_id": source["id"],
                    "quote": match.group(),
                    "start": match.start(),
                    "end": match.end(),
                    "date": source["date"],
                }
                break
    latest_labs: dict[str, dict] = {}
    for lab in sorted(labs, key=lambda item: item["date"] or "", reverse=True):
        latest_labs.setdefault(lab["name"], lab)
    return {
        **patient,
        "as_of": as_of,
        "sources": sources,
        "vitals": vitals,
        "snapshot_at": max(dated)[:10] if dated else None,
        "recent_notes": [{key: value for key, value in note.items() if key != "text"} for note in notes[:12]],
        "latest_labs": list(latest_labs.values()),
        "treatments": treatments,
    }


def select_sources(record: dict, budget: int = SOURCE_BUDGET) -> tuple[list[dict], bool]:
    """Demographics, latest value per lab, medications, then newest notes, within a character budget.

    Older values of a lab are superseded on purpose and do not make the selection incomplete;
    only candidates dropped for budget do.
    """
    sources = list(record["sources"].values())
    latest_ids = {lab["source_id"] for lab in record["latest_labs"]}
    structured = [
        source
        for source in sources
        if source["kind"] == "demographics"
        or source["id"] in latest_ids
        or source["kind"] in MEDICATION_KINDS
    ]
    notes = sorted(
        (source for source in sources if source["kind"] in NOTE_KINDS),
        key=lambda source: source["date"] or "",
        reverse=True,
    )
    candidates = structured + notes
    selected = []
    total = 0
    for source in candidates:
        if total + len(source["text"]) <= budget:
            selected.append(source)
            total += len(source["text"])
    return selected, len(selected) == len(candidates)
