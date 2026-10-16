"""Apply backend/db/schema.sql to the database configured in .env.

Usage: python -m backend.scripts.init_db
"""
from pathlib import Path

from backend.app.db import connect

SCHEMA = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def main():
    with connect() as conn:
        conn.execute(SCHEMA.read_text())
    print(f"Applied {SCHEMA.name}")


if __name__ == "__main__":
    main()
