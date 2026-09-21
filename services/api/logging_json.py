"""Structured JSON logging.

Duplicated verbatim in services/api/logging_json.py rather than shared
from one place — same reasoning as db.py's duplication in both services
(see its module docstring): services/api's Docker build context is
services/api/ itself (see its Dockerfile, `COPY . .` with no visibility
outside that directory), so a services/common/ module isn't reachable
from there without a build-context change neither service currently
needs for anything else. Keep both copies in sync if this ever changes.

Cloud Run ingests stdout as Cloud Logging entries and auto-parses a
one-line JSON payload: `severity`, `message`, and `time` are special and
get promoted to top-level LogEntry fields (confirmed against GCP's
structured-logging docs — an unrecognized `severity` value silently
falls back to DEFAULT, so it must be an uppercase LogSeverity enum value:
DEFAULT/DEBUG/INFO/NOTICE/WARNING/ERROR/CRITICAL/ALERT/EMERGENCY).
Everything else in the payload — event, request_id, chunk_id, and any
extra fields — lands under `jsonPayload`, filterable directly, e.g.
`jsonPayload.request_id="<job_id>"` reconstructs one job's whole
pipeline. No new dependency: stdlib `json` + `print(..., flush=True)`.
`flush=True` matters here specifically because Python block-buffers
non-TTY stdout by default, which can delay or withhold lines — this
codebase's existing prints already rely on it for the same reason.

request_id/chunk_id are contextvars rather than passed explicitly to
every log call. That's safe for the worker's actual shape: each of its
CONCURRENT_WORKERS lanes is one persistent daemon thread running its own
claim/process loop (not a ThreadPoolExecutor pool), so each OS thread
already has an isolated contextvars.Context by default — setting the var
once per claimed chunk, before any logging for that chunk happens, has
no cross-chunk leakage risk since the same thread re-sets it every loop
iteration. This would NOT be safe unchanged if the worker ever moves to
a real ThreadPoolExecutor.submit()-per-chunk model — context isn't
copied into pool threads automatically there, and would need
`contextvars.copy_context().run(...)` at submission time instead.
"""

from __future__ import annotations

import contextvars
import json
import time as _time
from datetime import datetime, timezone

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)
chunk_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "chunk_id", default=None
)

# Cloud Logging hard-rejects an entry over 256 KiB, and Cloud Run's own
# stdout capture truncates well before that (~100 KB, no documented exact
# number) and falls back to unparsed textPayload when it does — so any
# field that could hold real document text is trimmed defensively. This
# is a safety net, not a substitute for simply not logging page text or
# full prompts in the first place (callers shouldn't pass them at all).
MAX_FIELD_CHARS = 2000


def _trim(value):
    if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
        return value[:MAX_FIELD_CHARS] + f"...<{len(value) - MAX_FIELD_CHARS} chars trimmed>"
    return value


def log(event: str, severity: str = "INFO", message: str | None = None, **fields) -> None:
    """Emit one structured JSON log line to stdout.

    `event` is the stable, filterable action name (e.g. "page_completed",
    "llm_call_end") — filter Cloud Logging on jsonPayload.event to see
    every occurrence of one kind of step across every request.
    """
    entry = {
        "severity": severity,
        "message": message or event,
        "time": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "request_id": request_id_var.get(),
        "chunk_id": chunk_id_var.get(),
    }
    entry.update({k: _trim(v) for k, v in fields.items()})
    print(json.dumps(entry, default=str), flush=True)


class Timer:
    """`with Timer() as t: ...` — `t.ms` holds the elapsed time afterward.

    Call `t.elapsed_ms()` instead if you need a reading *inside* the
    `with` block (e.g. to log a duration before the block has finished) —
    `t.ms` stays None until `__exit__` runs, so reading it early always
    returns None instead of the real elapsed time so far.

    perf_counter, not wall-clock time, since this is only ever used to
    measure a duration on one machine within one process, not to
    correlate timestamps across machines.
    """

    def __enter__(self):
        self._start = _time.perf_counter()
        self.ms: float | None = None
        return self

    def elapsed_ms(self) -> float:
        return round((_time.perf_counter() - self._start) * 1000, 1)

    def __exit__(self, *exc_info):
        self.ms = self.elapsed_ms()
        return False
