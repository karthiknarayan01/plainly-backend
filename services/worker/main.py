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


def process_chunk(client, chunk: dict) -> None:
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


def main_loop():
    client = llm.make_client()
    print("worker started, polling for pending chunks", flush=True)
    while True:
        # A transient Cloud SQL connector hiccup (seen in practice: a
        # connection timeout under real conditions) must never kill this
        # process — Cloud Run would restart it, but every job sitting
        # 'pending' during that gap waits for a full cold start instead
        # of just the next poll. Log, back off, keep going.
        try:
            with db.get_conn() as conn:
                chunk = db.claim_next_chunk(conn)
        except Exception as exc:
            print(f"claim failed, will retry: {exc}", flush=True)
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        if chunk is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        print(f"[chunk {chunk['id']}] claimed", flush=True)
        process_chunk(client, chunk)


if __name__ == "__main__":
    threading.Thread(target=start_health_server, daemon=True).start()
    main_loop()
