"""Postgres access via the Cloud SQL Python Connector — works identically
with local ADC credentials and with the Cloud Run service account, no
manual proxy binary needed either place.

DATABASE_URL, when set, takes precedence and connects straight through
psycopg instead. That's the local-development path: running the real
pipeline end-to-end against a local Postgres is the only way to test
what a user actually sees (job -> chunks -> SSE -> reader), and the
Cloud SQL connector can't point at localhost.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager

import psycopg

_connector = None
# main.py runs CONCURRENT_WORKERS claim loops in parallel, so the lazy
# init below is reachable from several threads at once. Without this lock
# each of them can see `_connector is None` and build its own Connector —
# the original code sidestepped that by constructing one at import time,
# and the lazy version has to hold the same guarantee explicitly.
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


STALE_CLAIM_MINUTES = 5


def claim_next_chunk(conn) -> dict | None:
    # Also reclaims a chunk stuck in 'processing' past STALE_CLAIM_MINUTES
    # — the worker instance that claimed it may have crashed or been
    # replaced mid-attempt (confirmed happening in practice: a chunk
    # orphaned by a mid-processing restart otherwise stays stuck forever,
    # nothing else ever re-queues it). Fresh 'pending' work is preferred
    # over reclaiming stale work when both exist.
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            """
            UPDATE rewrite_chunks
            SET status = 'processing', claimed_at = now(), updated_at = now()
            WHERE id = (
                SELECT id FROM rewrite_chunks
                WHERE status = 'pending'
                   OR (status = 'processing' AND claimed_at < now() - make_interval(mins => %s))
                ORDER BY (status = 'pending') DESC, created_at
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING id, job_id, original_text
            """,
            (STALE_CLAIM_MINUTES,),
        )
        row = cur.fetchone()
        conn.commit()
        return row


def save_chunk_result(conn, chunk_id: str, rewrite_text: str, scores: dict, approved: bool, attempt_count: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE rewrite_chunks
            SET status = 'completed',
                rewrite_text = %s,
                scores = %s,
                approved = %s,
                attempt_count = %s,
                updated_at = now()
            WHERE id = %s
            """,
            (rewrite_text, json.dumps(scores), approved, attempt_count, chunk_id),
        )
        conn.commit()


def mark_chunk_failed(conn, chunk_id: str, error: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE rewrite_chunks
            SET status = 'failed', last_feedback = %s, updated_at = now()
            WHERE id = %s
            """,
            (error, chunk_id),
        )
        conn.commit()


def maybe_complete_job(conn, job_id: str) -> str | None:
    """Returns the job's new terminal status if this call just set one
    (so the caller can log a job_completed/job_failed event exactly
    once), or None if the job isn't finished yet or was already terminal.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM rewrite_chunks WHERE job_id = %s",
            (job_id,),
        )
        statuses = [row[0] for row in cur.fetchall()]
        if not statuses:
            return None
        if all(s == "completed" for s in statuses):
            new_status = "completed"
        elif any(s == "failed" for s in statuses) and all(s in ("completed", "failed") for s in statuses):
            new_status = "failed"
        else:
            return None  # still chunks pending/processing
        cur.execute(
            "UPDATE rewrite_jobs SET status = %s, updated_at = now() WHERE id = %s AND status NOT IN ('completed', 'failed')",
            (new_status, job_id),
        )
        conn.commit()
        return new_status if cur.rowcount else None


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


def get_job_snapshot(conn, job_id: str) -> dict | None:
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute("SELECT id, filename, status, created_at, updated_at FROM rewrite_jobs WHERE id = %s", (job_id,))
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
