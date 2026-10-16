import base64
import csv
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from openpyxl import load_workbook

from storage import DATABASES, read_output, write_output

ROOT = Path(__file__).parent
CACHE = ROOT / ".cache"
DATASET = next(ROOT.glob("rakathon-dataset-*/"), None)


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]


def load_cache(namespace: str, key: str):
    if namespace in DATABASES:
        output = read_output(CACHE, namespace, fingerprint(key))
        if output is not None:
            return output
    path = CACHE / namespace / f"{fingerprint(key)}.json"
    if path.exists():
        output = json.loads(path.read_text(encoding="utf-8"))
        if namespace in DATABASES:
            write_output(CACHE, namespace, fingerprint(key), output)
        return output
    return None


def save_cache(namespace: str, key: str, value: object) -> None:
    if namespace in DATABASES:
        write_output(CACHE, namespace, fingerprint(key), value)
        return
    folder = CACHE / namespace
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{fingerprint(key)}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


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
def trials() -> list[dict]:
    path = next(ROOT.glob("*.xlsx"), None)
    if not path:
        return []
    workbook = load_workbook(path, read_only=True, data_only=True)
    result = []
    for row_number, row in enumerate(workbook.active.values, 1):
        if row_number <= 2 or len(row) < 8 or not row[2] or not row[4]:
            continue
        protocol = str(row[2]).strip()
        source = "\n".join(
            f"{label}: {row[index] or ''}"
            for index, label in [
                (0, "Hospital ID"),
                (2, "Protocol"),
                (3, "Sponsor"),
                (4, "Title"),
                (7, "Diagnosis"),
            ]
        )
        result.append(
            {
                "id": f"T{row_number:03d}",
                "hospital_id": str(row[0] or ""),
                "protocol": protocol,
                "title": str(row[4]).strip(),
                "diagnosis": str(row[7] or ""),
                "sponsor": str(row[3] or ""),
                "phase": str(row[6] or ""),
                "source": {
                    "id": f"xlsx:{row_number}",
                    "title": f"{path.name} / Sheet1 / row {row_number}",
                    "text": source,
                    "date": None,
                    "kind": "workbook",
                },
            }
        )
    workbook.close()
    return result


def get_trial(identifier: str) -> dict:
    return next(item for item in trials() if item["id"] == identifier)


def code_label(code: dict) -> str:
    return str(
        code.get("text")
        or next((item.get("display") or item.get("code", "") for item in code.get("coding", [])), "")
        or ""
    )


@lru_cache(maxsize=8)
def patient_record(identifier: str) -> dict:
    patient = next(item for item in patients() if item["id"] == identifier)
    if DATASET is None:
        raise ValueError("Dataset not found")
    path = DATASET / "fhir" / f"{identifier}.json"
    bundle = json.loads(path.read_text(encoding="utf-8"))
    sources: dict[str, dict] = {}
    cohort_text = "\n".join(f"{key}: {value}" for key, value in patient["raw"].items())
    sources["cohort"] = {
        "id": "cohort",
        "title": "kohorta.csv / " + identifier,
        "text": cohort_text,
        "date": None,
        "kind": "demographics",
    }
    labs = []
    treatments = []
    for entry in bundle.get("entry", []):
        resource = entry["resource"]
        kind = resource["resourceType"]
        source_id = resource.get("id", "")
        date = resource.get("effectiveDateTime") or resource.get("date")
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
            text = f"{title}: {value} {unit}\nDate: {date}\nReference: {json.dumps(resource.get('referenceRange', []), ensure_ascii=False)}"
            labs.append({"name": title, "value": value, "unit": unit, "date": date, "source_id": source_id})
        elif kind in ("MedicationRequest", "MedicationAdministration"):
            medication = code_label(resource.get("medicationCodeableConcept", {}))
            date = date or resource.get("authoredOn") or resource.get("effectivePeriod", {}).get("start")
            text = f"{medication}\nDate: {date}\n{json.dumps(resource.get('dosageInstruction', resource.get('dosage', {})), ensure_ascii=False)}"
            treatments.append({"name": medication, "date": date, "source_id": source_id})
        if text:
            sources[source_id] = {"id": source_id, "title": title, "date": date, "text": text, "kind": kind}
    notes = sorted(
        (
            source
            for source in sources.values()
            if source["kind"] in ("DocumentReference", "DiagnosticReport")
        ),
        key=lambda source: source["date"] or "",
        reverse=True,
    )
    dated = [source["date"] for source in sources.values() if source["date"]]
    snapshot_at = max(dated)[:10] if dated else None
    vitals = {}
    patterns = {
        "height": r"(?:v[ýy]ška)\s*:?\s*(\d{2,3}(?:[.,]\d+)?)\s*cm",
        "weight": r"(?:v[áa]ha|hmotnost)\s*:?\s*(\d{2,3}(?:[.,]\d+)?)\s*kg",
        "ecog": r"(?:ECOG|WHO|PS)\s*:?\s*([0-4])\b",
    }
    for name, pattern in patterns.items():
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
        "sources": sources,
        "vitals": vitals,
        "snapshot_at": snapshot_at,
        "version": patient_version(patient),
        "recent_notes": [{key: value for key, value in note.items() if key != "text"} for note in notes[:12]],
        "latest_labs": list(latest_labs.values()),
        "treatments": treatments,
    }


def patient_version(patient: dict) -> str:
    if DATASET is None:
        raise ValueError("Dataset not found")
    path = DATASET / "fhir" / f"{patient['id']}.json"
    return fingerprint({"mtime": path.stat().st_mtime_ns, "cohort": patient["raw"]})


def select_sources(record: dict, character_budget: int = 90000) -> tuple[list[dict], bool]:
    sources = list(record["sources"].values())
    notes = sorted(
        (item for item in sources if item["kind"] in ("DocumentReference", "DiagnosticReport")),
        key=lambda item: item["date"] or "",
        reverse=True,
    )
    latest_ids = {item["source_id"] for item in record["latest_labs"]}
    structured = [
        item
        for item in sources
        if item["kind"] == "demographics"
        or item["id"] in latest_ids
        or item["kind"] in ("MedicationRequest", "MedicationAdministration")
    ]
    selected = []
    total = 0
    for source in structured + notes:
        if total + len(source["text"]) <= character_budget:
            selected.append(source)
            total += len(source["text"])
    complete = len(selected) == len(sources)
    return selected, complete
