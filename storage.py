"""One local SQLite file. Every table is key -> JSON payload, with an indexed owner (patient or trial ID)."""

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

# TRIALSCREEN_DB selects another file, e.g. the synthetic demo database written by seed_demo.py.
PATH = Path(__file__).parent / os.getenv("TRIALSCREEN_DB", ".cache/trialscreen.sqlite3")
# studies: trials added outside the workbook. edits: clinician corrections and confirmations.
TABLES = ("registry", "facts", "criteria", "matches", "studies", "edits")
SCHEMA = "".join(
    f"CREATE TABLE IF NOT EXISTS {table} (key TEXT PRIMARY KEY, owner TEXT NOT NULL, "
    "payload TEXT NOT NULL CHECK(json_valid(payload)), saved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);"
    f"CREATE INDEX IF NOT EXISTS {table}_owner ON {table}(owner);"
    for table in TABLES
) + (
    "CREATE VIEW IF NOT EXISTS patient_facts AS SELECT facts.owner AS patient_id, "
    "json_extract(facts.payload, '$.model') AS model, json_extract(fact.value, '$.id') AS fact_id, "
    "json_extract(fact.value, '$.name') AS name, json_extract(fact.value, '$.value') AS value, "
    "json_extract(fact.value, '$.date') AS observed_at, "
    "json_extract(fact.value, '$.evidence.source_id') AS source_id, "
    "json_extract(fact.value, '$.evidence.quote') AS quote "
    "FROM facts, json_each(facts.payload, '$.facts') AS fact;"
    "CREATE VIEW IF NOT EXISTS study_criteria AS SELECT criteria.owner AS trial_id, "
    "json_extract(criteria.payload, '$.nct_id') AS nct_id, json_extract(criteria.payload, '$.model') AS model, "
    "json_extract(criterion.value, '$.id') AS criterion_id, json_extract(criterion.value, '$.role') AS role, "
    "json_extract(criterion.value, '$.requirement') AS requirement, "
    "json_extract(criterion.value, '$.source.quote') AS quote "
    "FROM criteria, json_each(criteria.payload, '$.criteria') AS criterion;"
    "CREATE VIEW IF NOT EXISTS pair_results AS SELECT owner AS patient_id, "
    "json_extract(payload, '$.trial_id') AS trial_id, json_extract(payload, '$.model') AS model, "
    "json_extract(payload, '$.summary.status') AS status, "
    "json_extract(payload, '$.summary.match_percent') AS match_percent FROM matches;"
)
_ready: set[Path] = set()
_writes = 0  # writes made by this process; saved_at alone has one-second resolution


def connect() -> sqlite3.Connection:
    path = PATH
    if path not in _ready:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)
        _ready.add(path)
    return sqlite3.connect(path, timeout=30)


def _table(name: str) -> str:
    if name not in TABLES:
        raise ValueError(f"Unknown table {name}")
    return name


def get(table: str, key: str):
    with closing(connect()) as connection:
        row = connection.execute(f"SELECT payload FROM {_table(table)} WHERE key = ?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def by_owner(table: str, owner: str) -> list:
    with closing(connect()) as connection:
        rows = connection.execute(f"SELECT payload FROM {_table(table)} WHERE owner = ?", (owner,)).fetchall()
    return [json.loads(row[0]) for row in rows]


def rows(table: str) -> dict[str, object]:
    with closing(connect()) as connection:
        result = connection.execute(f"SELECT key, payload FROM {_table(table)}").fetchall()
    return {key: json.loads(payload) for key, payload in result}


def version() -> tuple:
    """Changes whenever any table changes (this process or another one, e.g. precompute.py)."""
    query = " UNION ALL ".join(f"SELECT count(*), max(saved_at) FROM {table}" for table in TABLES)
    with closing(connect()) as connection:
        return (str(PATH), _writes, tuple(connection.execute(query).fetchall()))


def put(table: str, key: str, owner: str, value: object) -> None:
    global _writes
    _writes += 1
    with closing(connect()) as connection, connection:
        connection.execute(
            f"INSERT INTO {_table(table)}(key, owner, payload) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET owner=excluded.owner, payload=excluded.payload, "
            "saved_at=CURRENT_TIMESTAMP",
            (key, owner, json.dumps(value, ensure_ascii=False)),
        )
