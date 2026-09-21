# Real worker: claims a pending chunk via SKIP LOCKED, runs ONE writer
# call against OpenRouter (llm.py), saves the result, and marks the parent
# job complete once every chunk is done. The generate->judge->retry loop
# this used to run was removed 2026-09-17 — see llm.py's docstring. The
# one writer call now uses a task-specific prompt, chosen by
# classify_page_type() below (a heuristic, not a second model call) —
# still exactly one OpenRouter round-trip per page.
#
# The HTTP health server is unrelated to the actual work — it exists only
# because Cloud Run kills a service that never binds $PORT, which is what
# broke every deploy before this was added. Runs in a background thread
# so it doesn't interfere with the claim loop.

import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import db
import llm
from logging_json import Timer, chunk_id_var, log, request_id_var

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


# Front/back matter that is navigation, not reading: tables of contents,
# indexes, lists of figures. Rewriting these produced exactly the junk a
# reader complained about — stray lines like "Preface page 15" scattered
# through the prose — because a list of titles and page numbers has no
# argument to restate, and page numbers from the original are meaningless
# against a re-paginated rewrite anyway. Detected here rather than asked
# of the model: it's deterministic, and it saves the API call entirely.
# Matched without anchors for the same reason as _TOC_LEADER: pdf.js
# hands over a page as a single line, so "^...$" would never fire.
_TOC_HEADING = re.compile(
    r"\b(table of contents|list of (figures|tables|illustrations))\b",
    re.IGNORECASE,
)
# A contents entry is a title, a run of leader dots, then a page number.
# Deliberately NOT line-based: the page text arrives from pdf.js in the
# browser, which returns a page as ONE line with no newlines at all. A
# line-oriented version of this check was calibrated against pypdf (which
# does emit newlines), passed its test, and then detected nothing
# whatsoever in production. Matching the leader runs directly works on
# either shape.
#
# Real PDFs also space the dots out (". . . . . 17", not "......17"), so
# the dot run has to tolerate whitespace. Page numbers may be arabic or
# roman, since front matter runs i, ix, xxiii.
_TOC_LEADER = re.compile(
    r"(?:\.\s*){3,}\s*(?:\d{1,4}|[ivxlc]{1,7})\b",
    re.IGNORECASE,
)
MIN_TOC_ENTRIES = 5

# Found via the expanded eval set (eval/pages/), not assumed: an e-book-style
# contents page can list chapters with NO page numbers at all — nothing for
# _TOC_LEADER to match, since it has no leader-dot-then-number to find. Real
# example that slipped through without this: a "Table of Contents" heading
# followed by "Chapter 1. - Twelve Basic Principles / Chapter 2. - The
# Balance Sheet / Chapter 3. - The Income Statement...", never once matching
# _TOC_LEADER. Three or more distinct "Chapter N" / "Section X" markers next
# to the heading is a second, independent signal that survives even that
# format — confirmed against the eval set's 21 non-contents pages to produce
# zero false positives (a page that mentions "chapter 3" once in passing
# doesn't also enumerate "chapter 1", "chapter 2" and "chapter 3" as
# structural markers the way a contents page does).
_CHAPTER_MARKER = re.compile(r"\b(chapter|section)\s+\d*[a-z]?\.?\s", re.IGNORECASE)
MIN_CHAPTER_MARKERS = 3


def looks_like_contents(text: str) -> bool:
    """True for navigation pages — contents, index, list of figures.

    These have no argument to restate, and their page numbers refer to the
    original's pagination, which means nothing in a re-paginated rewrite.
    Rewriting them scattered stray lines like "Preface page 15" through
    the prose.
    """
    entries = len(_TOC_LEADER.findall(text))
    if entries >= MIN_TOC_ENTRIES:
        return True
    if not _TOC_HEADING.search(text):
        return False
    # A heading plus even a couple of leader-dot entries is conclusive...
    if entries >= 2:
        return True
    # ...and so is a heading plus several distinct chapter/section markers,
    # for a contents page whose entries carry no page numbers to match on.
    markers = {m.group(0).strip().lower() for m in _CHAPTER_MARKER.finditer(text)}
    return len(markers) >= MIN_CHAPTER_MARKERS


# A short reply that announces it has nothing to say, rather than simply
# saying nothing. Bounded by length so a genuine short rewrite that merely
# contains the word "contents" is never discarded.
_DECLINED = re.compile(
    r"no (output|content|text)\b"
    r"|nothing to (rewrite|translate|simplify)"
    r"|this page (is|appears to be) (a |an )?(table of contents|index|blank)",
    re.IGNORECASE,
)
MAX_DECLINE_LENGTH = 400


def is_declined(rewrite: str) -> bool:
    text = rewrite.strip()
    return len(text) <= MAX_DECLINE_LENGTH and bool(_DECLINED.search(text))


# Routes a page to one of the two writer task prompts (prompts/writer/
# earnings_statement.md, technical_book.md — see llm.compose_writer_prompt).
# Production has no ground-truth document type, and an LLM classification
# call would add a second round-trip's latency to every single page for a
# difference that's mostly framing (the rules that actually govern
# fidelity/formatting live in prompts/writer/_shared.md and apply to both
# tasks identically) — so this is a cheap heuristic, same philosophy as
# looks_like_contents() above, not a model call. A true contents page is
# expected to already be gone by the time this runs (looks_like_contents()
# is checked first in process_chunk()); this never routes to the
# "contents_page" task itself, which exists for eval's direct testing of
# the model's own judgment, not for production routing.
_FINANCE_SIGNALS = re.compile(
    r"\$[\d,]+(?:\.\d+)?|"
    r"\b(?:GAAP|EPS|revenue|earnings|fiscal|quarter(?:ly)?|guidance|"
    r"gross margin|operating margin|net income|free cash flow|"
    r"year-over-year|EBITDA|diluted|shareholders?)\b",
    re.IGNORECASE,
)
_BOOK_SIGNALS = re.compile(
    r"\bfor example\b|\bin other words\b|\bFigure \d|\bListing \d|"
    r"\bchapter\b|\bin this (?:section|chapter)\b|\bas shown\b|"
    r"\bconsider the following\b",
    re.IGNORECASE,
)


def classify_page_type(text: str) -> str:
    """Picks a writer task: "earnings_statement" or "technical_book"."""
    finance_hits = len(_FINANCE_SIGNALS.findall(text))
    book_hits = len(_BOOK_SIGNALS.findall(text))
    return "earnings_statement" if finance_hits > book_hits else "technical_book"


def _log_job_transition(new_status: str | None) -> None:
    # maybe_complete_job only returns non-None the one time it actually
    # flips the job to a terminal status (see db.py's own docstring on
    # the race it closes), so this never double-logs a job's completion
    # even though every one of its chunks calls it.
    if new_status == "completed":
        log("job_completed")
    elif new_status == "failed":
        log("job_failed", severity="WARNING")


def process_chunk(client, chunk: dict) -> None:
    request_id_var.set(str(chunk["job_id"]))
    chunk_id_var.set(str(chunk["id"]))
    original_text = chunk["original_text"].strip()

    with Timer() as page_timer:
        if len(original_text) < MIN_TEXT_LENGTH:
            with db.get_conn() as conn:
                db.save_chunk_result(conn, chunk["id"], "", {}, True, 0)
                new_status = db.maybe_complete_job(conn, chunk["job_id"])
            log("page_skipped", reason="no_extractable_text", chars=len(original_text))
            _log_job_transition(new_status)
            return
        if looks_like_contents(original_text):
            with db.get_conn() as conn:
                db.save_chunk_result(conn, chunk["id"], "", {}, None, 0)
                new_status = db.maybe_complete_job(conn, chunk["job_id"])
            log("page_skipped", reason="contents_page_detected")
            _log_job_transition(new_status)
            return
        try:
            # One writer call. No judge, no fact-check, no retry loop —
            # see llm.py's module docstring for what that trades away.
            task = classify_page_type(original_text)
            log("page_classified", task=task)
            rewrite = llm.call_writer(client, chunk["original_text"], task)
            if is_declined(rewrite):
                # The model was asked to reply with nothing for a
                # navigation page and instead explained itself — "(No
                # output — this page is a table of contents...)".
                # Observed for real. Shipping that puts the model's
                # apology in the middle of the book, so treat it as the
                # empty reply it was meant to be.
                rewrite = ""
            with db.get_conn() as conn:
                # scores/approved stay in the schema but are no longer
                # produced; nothing grades a rewrite now.
                db.save_chunk_result(conn, chunk["id"], rewrite, {}, None, 1)
                new_status = db.maybe_complete_job(conn, chunk["job_id"])
            log("page_completed", rewrite_chars=len(rewrite), task=task,
                duration_ms=page_timer.elapsed_ms())
            _log_job_transition(new_status)
        except llm.InsufficientCreditsError as exc:
            # Stored as a stable code (llm.FAILURE_INSUFFICIENT_CREDITS),
            # not the raw exception text: services/api/db.py's
            # get_job_progress looks for this exact string across a
            # job's chunks so the reader can stop waiting and show a
            # real explanation — "we're out of credits" — instead of the
            # generic "something went wrong" a raw error dump would
            # produce, or worse, silence.
            log("page_failed", severity="WARNING", reason="insufficient_credits",
                detail=str(exc), duration_ms=page_timer.elapsed_ms())
            try:
                with db.get_conn() as conn:
                    db.mark_chunk_failed(conn, chunk["id"], llm.FAILURE_INSUFFICIENT_CREDITS)
                    new_status = db.maybe_complete_job(conn, chunk["job_id"])
                _log_job_transition(new_status)
            except Exception as inner_exc:
                log("page_failure_not_recorded", severity="ERROR", detail=str(inner_exc))
        except Exception as exc:
            log("page_failed", severity="ERROR", reason="exception",
                detail=str(exc), duration_ms=page_timer.elapsed_ms())
            try:
                with db.get_conn() as conn:
                    db.mark_chunk_failed(conn, chunk["id"], str(exc))
                    new_status = db.maybe_complete_job(conn, chunk["job_id"])
                _log_job_transition(new_status)
            except Exception as inner_exc:
                # DB itself may be the thing that's down — don't let
                # recording the failure become its own unhandled crash.
                log("page_failure_not_recorded", severity="ERROR", detail=str(inner_exc))


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
            log("claim_failed", severity="WARNING", worker_lane=worker_num, detail=str(exc))
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        if chunk is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        request_id_var.set(str(chunk["job_id"]))
        chunk_id_var.set(str(chunk["id"]))
        log("chunk_claimed", worker_lane=worker_num)
        process_chunk(client, chunk)


def main_loop():
    client = llm.make_client()
    log("worker_started", concurrent_lanes=CONCURRENT_WORKERS)
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
