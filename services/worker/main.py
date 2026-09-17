# Real worker: claims a pending chunk via SKIP LOCKED, runs ONE writer
# call against OpenRouter (llm.py), saves the result, and marks the parent
# job complete once every chunk is done. The generate->judge->retry loop
# this used to run was removed 2026-09-17 — see llm.py's docstring.
#
# The HTTP health server is unrelated to the actual work — it exists only
# because Cloud Run kills a service that never binds $PORT, which is what
# broke every deploy before this was added. Runs in a background thread
# so it doesn't interfere with the claim loop.

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import db
import llm

POLL_INTERVAL_SECONDS = 2
# Each chunk's work is almost entirely waiting on OpenRouter (network
# I/O), not CPU — running several claim/process loops concurrently in
# one instance is safe (claim_next_chunk's SKIP LOCKED guarantees two
# threads never get the same chunk) and turns a large document (a real
# 259-page upload sat at ~1 page/10s single-threaded — 40+ minutes) into
# something that finishes in minutes instead. The openai SDK's client is
# safe to share across threads (httpx underneath is).
#
# Dialed back from 16 to 8: confirmed in production that 16 threads
# here, combined with the API's SSE endpoint (which was separately
# opening a fresh connection per poll — fixed in services/api/main.py),
# actually exhausted the db-f1-micro tier's 25-connection limit under
# real load. The SSE fix removes the larger source of churn, but 8 is
# the number already proven safe rather than pushing straight back to a
# number that's already caused a real outage once.
CONCURRENT_WORKERS = 8


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass


def start_health_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


# Pages with no real extractable text (chapter divider pages, blank
# pages, pure-image pages — confirmed happening in practice, roughly 1
# in 13 pages of a real technical book) must never reach the writer
# model. Found in production: given empty input, the model doesn't
# decline — it fabricates plausible-sounding but entirely invented
# content (confirmed: a completely fictional passage about "IFRS 15"
# accounting rules, for a page that had zero extracted text). Empty
# input mapping to empty output is trivially faithful; skipping the LLM
# call entirely is the only way to guarantee that, since a prompt
# instruction alone is not a hard guarantee against hallucination.
MIN_TEXT_LENGTH = 20


def process_chunk(client, chunk: dict) -> None:
    original_text = chunk["original_text"].strip()
    if len(original_text) < MIN_TEXT_LENGTH:
        with db.get_conn() as conn:
            db.save_chunk_result(conn, chunk["id"], "", {}, True, 0)
            db.maybe_complete_job(conn, chunk["job_id"])
        print(f"[chunk {chunk['id']}] skipped (no extractable text, {len(original_text)} chars)", flush=True)
        return
    try:
        # One writer call. No judge, no fact-check, no retry loop — see
        # llm.py's module docstring for what that trades away and why.
        rewrite = llm.call_writer(client, chunk["original_text"])
        with db.get_conn() as conn:
            # scores/approved stay in the schema but are no longer produced;
            # nothing grades a rewrite now.
            db.save_chunk_result(conn, chunk["id"], rewrite, {}, None, 1)
            db.maybe_complete_job(conn, chunk["job_id"])
        print(f"[chunk {chunk['id']}] completed ({len(rewrite)} chars)", flush=True)
    except Exception as exc:
        print(f"[chunk {chunk['id']}] FAILED: {exc}", flush=True)
        try:
            with db.get_conn() as conn:
                db.mark_chunk_failed(conn, chunk["id"], str(exc))
                db.maybe_complete_job(conn, chunk["job_id"])
        except Exception as inner_exc:
            # DB itself may be the thing that's down — don't let recording
            # the failure become its own unhandled crash.
            print(f"[chunk {chunk['id']}] also failed to record failure: {inner_exc}", flush=True)


def claim_and_process_loop(client, worker_num: int):
    while True:
        # A transient Cloud SQL connector hiccup (seen in practice: a
        # connection timeout under real conditions) must never kill this
        # thread — Cloud Run would restart the whole instance, but every
        # job sitting 'pending' during that gap waits for a full cold
        # start instead of just the next poll. Log, back off, keep going.
        try:
            with db.get_conn() as conn:
                chunk = db.claim_next_chunk(conn)
        except Exception as exc:
            print(f"[worker {worker_num}] claim failed, will retry: {exc}", flush=True)
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        if chunk is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        print(f"[worker {worker_num}] [chunk {chunk['id']}] claimed", flush=True)
        process_chunk(client, chunk)


def main_loop():
    client = llm.make_client()
    print(f"worker started, {CONCURRENT_WORKERS} concurrent lanes polling for pending chunks", flush=True)
    threads = [
        threading.Thread(target=claim_and_process_loop, args=(client, i), daemon=True)
        for i in range(CONCURRENT_WORKERS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


if __name__ == "__main__":
    threading.Thread(target=start_health_server, daemon=True).start()
    main_loop()
