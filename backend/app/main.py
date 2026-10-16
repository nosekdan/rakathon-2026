from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .db import connect

app = FastAPI(title="AI Trial Matching")

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@app.get("/api/health")
def health():
    with connect() as conn:
        row = conn.execute("SELECT version() AS version").fetchone()
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name"
        ).fetchall()
    return {
        "status": "ok",
        "db": row["version"],
        "tables": [t["table_name"] for t in tables],
    }


@app.get("/api/concepts")
def list_concepts():
    with connect() as conn:
        return conn.execute("SELECT * FROM concept ORDER BY id").fetchall()


# Must be mounted last so /api routes take precedence.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
