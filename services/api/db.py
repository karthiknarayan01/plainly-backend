"""Postgres access via the Cloud SQL Python Connector — same pattern as
services/worker/db.py, duplicated rather than shared since these are two
independently-built Docker images and each only needs a subset of the
queries.
"""

from __future__ import annotations

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
