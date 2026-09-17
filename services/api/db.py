"""Postgres access via the Cloud SQL Python Connector — same pattern as
services/worker/db.py, duplicated rather than shared since these are two
independently-built Docker images and each only needs a subset of the
queries.

DATABASE_URL, when set, takes precedence and connects straight through
psycopg — the local-development path, so the real API can be run against
a local Postgres for end-to-end testing. Same rationale as the worker's.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager

import psycopg

_connector = None
# FastAPI serves these sync endpoints from a thread pool, so the lazy
# init below can be entered concurrently. The original constructed one
# Connector at import time; the lazy version needs this lock to keep the
# same "exactly one" guarantee. Same reasoning as services/worker/db.py.
_connector_lock = threading.Lock()


def _connect():
    database_url = os.environ.get("DATABASE_URL")
    if database_url:
        return psycopg.connect(database_url)

    # Imported lazily so local runs don't need the GCP dependency at all.
    global _connector
    if _connector is None:
        with _connector_lock:
            if _connector is None:  # re-checked: another thread may have won
                from google.cloud.sql.connector import Connector

                _connector = Connector()
    instance_connection_name = os.environ["DATABASE_INSTANCE_CONNECTION_NAME"]
    return _connector.connect(
        instance_connection_name,
        "psycopg",
        user=os.environ["DATABASE_USER"],
        password=os.environ["DATABASE_PASSWORD"],
        db=os.environ["DATABASE_NAME"],
    )


@contextmanager
def get_conn():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_job(conn, filename: str, pages: list[dict]) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO rewrite_jobs (filename) VALUES (%s) RETURNING id",
            (filename,),
        )
        job_id = cur.fetchone()[0]
        for page in pages:
            cur.execute(
                """
                INSERT INTO rewrite_chunks (job_id, chunk_index, original_text)
                VALUES (%s, %s, %s)
                """,
                (job_id, page["page_number"], page["text"]),
            )
        conn.commit()
        return str(job_id)


def get_job_progress(conn, job_id: str) -> dict | None:
    """Counts only — no rewrite text.

    The client polls this every couple of seconds while a job runs, and a
    200-page job's full snapshot is close to a megabyte of prose. Sending
    that repeatedly just to learn "how many are done" wastes the whole
    payload; the document itself is fetched once, at the end.
    """
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute("SELECT id, filename, status FROM rewrite_jobs WHERE id = %s", (job_id,))
        job = cur.fetchone()
        if job is None:
            return None
        cur.execute(
            """
            SELECT
              count(*) AS total,
              count(*) FILTER (WHERE status = 'completed') AS completed,
              count(*) FILTER (WHERE status = 'failed')    AS failed
            FROM rewrite_chunks WHERE job_id = %s
            """,
            (job_id,),
        )
        counts = cur.fetchone()
        return {"job": job, **counts}


def get_job_snapshot(conn, job_id: str) -> dict | None:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            "SELECT id, filename, status, created_at, updated_at FROM rewrite_jobs WHERE id = %s",
            (job_id,),
        )
        job = cur.fetchone()
        if job is None:
            return None
        cur.execute(
            """
            SELECT id, chunk_index, status, rewrite_text, scores, approved, attempt_count
            FROM rewrite_chunks WHERE job_id = %s ORDER BY chunk_index
            """,
            (job_id,),
        )
        chunks = cur.fetchall()
        return {"job": job, "chunks": chunks}
