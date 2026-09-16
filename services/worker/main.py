# Real worker: claims a pending chunk via SKIP LOCKED, runs the
# generate->judge->retry LangGraph loop (retry_graph.py) using OpenRouter
# (llm.py), saves the result, and marks the parent job complete once every
# chunk is done.
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
from retry_graph import run_generate_judge_retry

POLL_INTERVAL_SECONDS = 2
# Each chunk's work is almost entirely waiting on OpenRouter (network
# I/O), not CPU — running several claim/process loops concurrently in
# one instance is safe (claim_next_chunk's SKIP LOCKED guarantees two
# threads never get the same chunk) and turns a large document (a real
# 259-page upload sat at ~1 page/10s single-threaded — 40+ minutes) into
# something that finishes in minutes instead. The openai SDK's client is
# safe to share across threads (httpx underneath is).
#
# Not scaled higher than this for now: the real ceiling isn't CPU (the
# container only has 1 vCPU, but this workload barely uses it), it's
# Postgres — the current db-f1-micro tier allows only 25 total
# connections. A thread only holds one briefly (claiming, then saving),
# not for the whole LLM wait, so this has real headroom, but going much
# higher without also testing OpenRouter's tolerance for the concurrency
# and bumping the DB tier would be guessing rather than verifying.
CONCURRENT_WORKERS = 16


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
        result = run_generate_judge_retry(client, chunk["original_text"])
        scores = llm.get_scores(result["judge_result"])
        with db.get_conn() as conn:
            db.save_chunk_result(
                conn,
                chunk["id"],
                result["rewrite"],
                scores,
                result["approved"],
                result["attempt_count"],
            )
            db.maybe_complete_job(conn, chunk["job_id"])
        print(f"[chunk {chunk['id']}] completed approved={result['approved']} attempts={result['attempt_count']} overall={scores.get('overall')}", flush=True)
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
