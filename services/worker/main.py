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
    with db.get_conn() as conn:
        try:
            result = run_generate_judge_retry(client, chunk["original_text"])
            scores = llm.get_scores(result["judge_result"])
            db.save_chunk_result(
                conn,
                chunk["id"],
                result["rewrite"],
                scores,
                result["approved"],
                result["attempt_count"],
            )
            print(f"[chunk {chunk['id']}] completed approved={result['approved']} attempts={result['attempt_count']} overall={scores.get('overall')}", flush=True)
        except Exception as exc:
            db.mark_chunk_failed(conn, chunk["id"], str(exc))
            print(f"[chunk {chunk['id']}] FAILED: {exc}", flush=True)
        db.maybe_complete_job(conn, chunk["job_id"])


def main_loop():
    client = llm.make_client()
    print("worker started, polling for pending chunks", flush=True)
    while True:
        with db.get_conn() as conn:
            chunk = db.claim_next_chunk(conn)
        if chunk is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        print(f"[chunk {chunk['id']}] claimed", flush=True)
        process_chunk(client, chunk)


if __name__ == "__main__":
    threading.Thread(target=start_health_server, daemon=True).start()
    main_loop()
