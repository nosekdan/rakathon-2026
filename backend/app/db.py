import os
from contextlib import contextmanager

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

# Locally reads .env; on App Service the same variables come from App settings.
load_dotenv()


def conninfo() -> str:
    return psycopg.conninfo.make_conninfo(
        host=os.environ["DB_HOST"],
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode="require",
    )


@contextmanager
def connect():
    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        yield conn
