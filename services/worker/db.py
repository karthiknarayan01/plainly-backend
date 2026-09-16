"""Postgres access via the Cloud SQL Python Connector — works identically
with local ADC credentials and with the Cloud Run service account, no
manual proxy binary needed either place.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager

import psycopg
from google.cloud.sql.connector import Connector

_connector = Connector()


def _connect():
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


def maybe_complete_job(conn, job_id: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM rewrite_chunks WHERE job_id = %s",
            (job_id,),
        )
        statuses = [row[0] for row in cur.fetchall()]
        if not statuses:
            return
        if all(s == "completed" for s in statuses):
            new_status = "completed"
        elif any(s == "failed" for s in statuses) and all(s in ("completed", "failed") for s in statuses):
            new_status = "failed"
        else:
            return  # still chunks pending/processing
        cur.execute(
            "UPDATE rewrite_jobs SET status = %s, updated_at = now() WHERE id = %s",
            (new_status, job_id),
        )
        conn.commit()


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
