import json
import sqlite3
from pathlib import Path

DATABASES = {"facts": "patients.sqlite3", "criteria": "studies.sqlite3", "matches": "matches.sqlite3"}


def connect(folder: Path, namespace: str) -> sqlite3.Connection:
    folder.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(folder / DATABASES[namespace], timeout=30)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS outputs ("
        "cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL CHECK(json_valid(payload)), "
        "saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    if namespace == "facts":
        connection.execute(
            "CREATE VIEW IF NOT EXISTS patient_facts AS "
            "SELECT outputs.cache_key, json_extract(outputs.payload, '$.patient_id') AS patient_id, "
            "json_extract(outputs.payload, '$.model') AS model, "
            "json_extract(fact.value, '$.name') AS name, "
            "json_extract(fact.value, '$.value') AS value, "
            "json_extract(fact.value, '$.date') AS observed_at, "
            "json_extract(fact.value, '$.evidence.source_id') AS source_id, "
            "json_extract(fact.value, '$.evidence.quote') AS quote "
            "FROM outputs, json_each(outputs.payload, '$.facts') AS fact"
        )
    elif namespace == "criteria":
        connection.execute(
            "CREATE VIEW IF NOT EXISTS study_criteria AS "
            "SELECT outputs.cache_key, json_extract(outputs.payload, '$.nct_id') AS nct_id, "
            "json_extract(outputs.payload, '$.model') AS model, "
            "json_extract(criterion.value, '$.id') AS criterion_id, "
            "json_extract(criterion.value, '$.requirement') AS requirement, "
            "json_extract(criterion.value, '$.role') AS role, "
            "json_extract(criterion.value, '$.source.quote') AS quote "
            "FROM outputs, json_each(outputs.payload, '$.criteria') AS criterion"
        )
    else:
        connection.execute(
            "CREATE VIEW IF NOT EXISTS pair_results AS "
            "SELECT cache_key, json_extract(payload, '$.patient_id') AS patient_id, "
            "json_extract(payload, '$.trial_id') AS trial_id, "
            "json_extract(payload, '$.model') AS model, "
            "json_extract(payload, '$.summary.status') AS status, "
            "json_extract(payload, '$.summary.match_percent') AS match_percent "
            "FROM outputs"
        )
    return connection


def read_output(folder: Path, namespace: str, key: str):
    if not (folder / DATABASES[namespace]).exists():
        return None
    connection = connect(folder, namespace)
    try:
        row = connection.execute("SELECT payload FROM outputs WHERE cache_key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None
    finally:
        connection.close()


def write_output(folder: Path, namespace: str, key: str, value: object) -> None:
    connection = connect(folder, namespace)
    try:
        with connection:
            connection.execute(
                "INSERT INTO outputs(cache_key, payload) VALUES (?, ?) "
                "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, saved_at=CURRENT_TIMESTAMP",
                (key, json.dumps(value, ensure_ascii=False)),
            )
    finally:
        connection.close()
