"""Database connection management and migration execution.

The agent stores all mutable state in Postgres. This module provides a single
place to open connections (``get_connection``) and apply the raw-SQL migration
files (``run_migrations``) in filename order from a clean database.
"""

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg2


MIGRATIONS_DIR = Path(__file__).parent / "migrations"


@contextmanager
def get_connection(database_url: str | None = None) -> Iterator[psycopg2.extensions.connection]:
    """Context manager yielding a psycopg2 connection.

    Reads ``DATABASE_URL`` from the environment when no explicit URL is given.
    The connection is committed on clean exit and rolled back (with a
    re-raised exception) if the body raises.
    """
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    conn = psycopg2.connect(url)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_migrations(conn: psycopg2.extensions.connection, migrations_dir: Path | None = None) -> None:
    """Execute every ``*.sql`` file in ``migrations_dir`` in filename order."""
    directory = migrations_dir or MIGRATIONS_DIR
    sql_files = sorted(directory.glob("*.sql"))
    if not sql_files:
        raise FileNotFoundError(f"No migration files found in {directory}")
    for sql_file in sql_files:
        sql = sql_file.read_text(encoding="utf-8")
        with conn.cursor() as cursor:
            cursor.execute(sql)
    conn.commit()