# Real endpoints: job creation, a full snapshot GET, and a cheap progress
# GET the client polls while a job runs. The worker (services/worker) is
# the only thing that ever touches OpenRouter — this service only ever
# talks to Postgres.

import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import db
from logging_json import log, request_id_var

app = FastAPI()

TERMINAL_STATUSES = ("completed", "failed")


@app.get("/health")
def health():
    return {"status": "ok"}


class PageInput(BaseModel):
    page_number: int
    text: str


class CreateJobRequest(BaseModel):
    filename: str
    pages: list[PageInput]


def _default(obj):
    if isinstance(obj, (UUID,)):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"not JSON serializable: {obj!r}")


def _to_json(snapshot: dict) -> str:
    return json.dumps(snapshot, default=_default)


@app.post("/rewrite-jobs")
def create_rewrite_job(body: CreateJobRequest):
    if not body.pages:
        raise HTTPException(status_code=400, detail="pages must not be empty")
    with db.get_conn() as conn:
        job_id = db.create_job(conn, body.filename, [p.model_dump() for p in body.pages])
    # This is the one point where a "request" in the user-facing sense
    # (one uploaded document) begins — job_id is used as the request_id
    # everywhere downstream (services/worker's logs, Cloud Logging
    # filtering) rather than minting a second, parallel identifier.
    request_id_var.set(job_id)
    log("job_received", filename=body.filename, page_count=len(body.pages))
    return {"job_id": job_id}


@app.get("/rewrite-jobs/{job_id}")
def get_rewrite_job(job_id: str):
    request_id_var.set(job_id)
    with db.get_conn() as conn:
        snapshot = db.get_job_snapshot(conn, job_id)
    if snapshot is None:
        log("job_lookup_not_found", severity="WARNING")
        raise HTTPException(status_code=404, detail="job not found")
    return json.loads(_to_json(snapshot))


@app.get("/rewrite-jobs/{job_id}/progress")
def get_rewrite_job_progress(job_id: str):
    """Cheap counts for the client's poll loop.

    Replaced the SSE stream on 2026-09-17. The stream existed to reveal
    pages as they finished, but the reader only ever showed the completed
    run from page 1 — so a job processing pages out of order (8 lanes)
    surfaced a fraction of the document and looked like the rest had been
    lost. The client now waits for the whole document and shows progress
    from this endpoint while it waits.
    """
    request_id_var.set(job_id)
    with db.get_conn() as conn:
        progress = db.get_job_progress(conn, job_id)
    if progress is None:
        log("job_lookup_not_found", severity="WARNING")
        raise HTTPException(status_code=404, detail="job not found")
    return json.loads(_to_json(progress))
