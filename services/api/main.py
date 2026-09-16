# Real endpoints: job creation, a plain snapshot GET, and an SSE stream
# that polls the DB server-side and pushes updates as chunks complete.
# The worker (services/worker) is the only thing that ever touches
# OpenRouter — this service only ever talks to Postgres.

import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import db

app = FastAPI()

TERMINAL_STATUSES = ("completed", "failed")
POLL_INTERVAL_SECONDS = 1


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
    return {"job_id": job_id}


@app.get("/rewrite-jobs/{job_id}")
def get_rewrite_job(job_id: str):
    with db.get_conn() as conn:
        snapshot = db.get_job_snapshot(conn, job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="job not found")
    return json.loads(_to_json(snapshot))


@app.get("/rewrite-jobs/{job_id}/stream")
async def stream_rewrite_job(job_id: str):
    async def event_generator():
        # One connection for the whole stream, not one per poll — a
        # multi-minute job at a 1s poll interval was opening hundreds of
        # fresh Cloud SQL connections per client, which exhausted the
        # 25-connection limit on the current DB tier under real load
        # (confirmed happening in production, not theoretical).
        last_payload = None
        with db.get_conn() as conn:
            while True:
                snapshot = db.get_job_snapshot(conn, job_id)
                if snapshot is None:
                    yield {"event": "error", "data": json.dumps({"detail": "job not found"})}
                    return
                payload = _to_json(snapshot)
                if payload != last_payload:
                    yield {"event": "update", "data": payload}
                    last_payload = payload
                if snapshot["job"]["status"] in TERMINAL_STATUSES:
                    return
                await asyncio.sleep(POLL_INTERVAL_SECONDS)

    return EventSourceResponse(event_generator())
