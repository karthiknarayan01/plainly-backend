"""OpenRouter client + the writer call for the worker.

One model, one call per page. There is no judge, no fact-check, and no
retry loop — removed 2026-09-17 by explicit request ("there is no need
for the feedback models so remove it. Use just the writer models").

What that trades away, recorded honestly so it can be put back
deliberately rather than rediscovered: the judge was the only thing
checking that a rewrite kept every figure and invented nothing, and
nothing now catches a bad page before a reader sees it. In the last
page-scale benchmark the writer below scored 100% on figure preservation
but did fabricate three company names on one page, so that risk is real
rather than theoretical. See eval/README.md; eval/run_page_eval.py still
measures all of it offline, which is now the only place quality is
checked at all.

What it buys, and why it's the right call for a real document: three
OpenRouter round-trips per page become one, and the retry loop's up to
three attempts per page collapse to a single attempt. A 200-page upload
went from as many as 9 calls per page to exactly 1.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from pathlib import Path

from openai import OpenAI

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# A stable, machine-readable reason code, not prose — this exact string is
# what services/api/db.py's get_job_progress looks for in a failed chunk's
# last_feedback to tell the frontend "stop waiting, show the credits
# message" rather than a generic failure. The two services don't share
# code (see their db.py module docstrings), so this literal is duplicated
# there; keep both in sync if it ever changes.
FAILURE_INSUFFICIENT_CREDITS = "insufficient_credits"


class InsufficientCreditsError(RuntimeError):
    """The OpenRouter account is out of credits (HTTP 402).

    Distinguished from every other failure because it needs a genuinely
    different response: a transient error is worth retrying and, if it
    doesn't recover, only cost one page. An empty OpenRouter balance fails
    every subsequent call the exact same way until the user adds funds —
    an action outside this process — so continuing to dispatch a large
    job's remaining pages one at a time would just burn the rate limiter's
    slots reproducing the same failure a few hundred times. See
    _credits_likely_exhausted below for how call_writer short-circuits
    that, and services/worker/main.py for how this is surfaced to the
    reader instead of silently vanishing.
    """

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Claude, by explicit request. claude-sonnet-5 specifically because it is
# the one model measured at 100% figure preservation on the page-scale
# eval (eval/README.md) with the highest teaching score of anything
# tested, open or closed — and with the judge gone, writer quality is now
# the only quality control there is.
WRITER_MODEL = os.environ.get("WRITER_MODEL", "anthropic/claude-sonnet-5")

REQUEST_TIMEOUT_SECONDS = 120

# A cap is necessary, confirmed by testing: without one, an OpenRouter
# provider route once ignored the reasoning-disable param and requested a
# 131,072-token completion (its entire context window) for a single short
# passage, failing outright. 4000 matches the cap the current
# writer+prompt combination was actually benchmarked under — the prompt
# tells the writer to come out longer than its source, and the largest
# rewrite observed in those runs was ~1,550 tokens. A cap only bounds the
# worst case; it costs nothing when unused, since billing is on tokens
# actually generated.
MAX_WRITER_OUTPUT_TOKENS = 4000


def load_prompt(path: Path) -> str:
    """Strip the leading '# Title' + '> Status: ...' header, keep the rest."""
    text = path.read_text(encoding="utf-8")
    marker = "\n---\n"
    idx = text.find(marker)
    if idx != -1:
        return text[idx + len(marker):].strip()
    return text.strip()


def make_client() -> OpenAI:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        timeout=REQUEST_TIMEOUT_SECONDS,
        # OpenRouter routes across multiple backend providers, and
        # individual ones can transiently rate-limit or time out —
        # confirmed by testing, the SDK's default of 2 retries wasn't
        # always enough.
        max_retries=5,
    )


# OpenRouter caps new accounts at 20 requests/minute for claude-sonnet-5
# ("new-account-rpm"). With 8 worker lanes each taking ~25s per page the
# natural rate is ~19/min — right on the cap — so bursts blew through it
# and produced 55 real 429 failures on a single upload. With the retry
# loop gone a 429 permanently loses that page, so this throttles every
# writer call through one shared token bucket instead of relying on luck.
# Set a little under the cap to leave room for the uneven arrival of 8
# lanes finishing at once.
REQUESTS_PER_MINUTE = int(os.environ.get("WRITER_REQUESTS_PER_MINUTE", "16"))
_rate_lock = threading.Lock()
_recent_calls: deque[float] = deque()


def _throttle() -> None:
    """Blocks until issuing another request stays under REQUESTS_PER_MINUTE."""
    while True:
        with _rate_lock:
            now = time.monotonic()
            while _recent_calls and now - _recent_calls[0] >= 60.0:
                _recent_calls.popleft()
            if len(_recent_calls) < REQUESTS_PER_MINUTE:
                _recent_calls.append(now)
                return
            # Sleep until the oldest call in the window ages out.
            wait = 60.0 - (now - _recent_calls[0]) + 0.05
        time.sleep(max(wait, 0.05))


# A 429 is not a failed page, it's a "come back shortly" — but only if
# something actually comes back. Retried here with backoff because losing
# a page from a 200-page book over a transient limit is the worst possible
# outcome for a reader.
RATE_LIMIT_RETRIES = 4

# Once a 402 is seen, every worker thread in this process skips straight
# to InsufficientCreditsError for a short window instead of making (and
# waiting on, via _throttle) a call that's certain to fail the same way.
# Short and self-healing rather than a permanent kill switch: the user can
# add funds at any moment, and the next call after the window simply tries
# for real again. This is process-local — on Cloud Run with more than one
# instance, each instance independently discovers and pauses on its own —
# which trades perfect coordination for needing no shared state, a
# reasonable trade for an outage that only ends when a human acts anyway.
CREDITS_COOLDOWN_SECONDS = 45.0
_credits_lock = threading.Lock()
_credits_exhausted_until = 0.0


def _credits_likely_exhausted() -> bool:
    with _credits_lock:
        return time.monotonic() < _credits_exhausted_until


def _mark_credits_exhausted() -> None:
    global _credits_exhausted_until
    with _credits_lock:
        _credits_exhausted_until = time.monotonic() + CREDITS_COOLDOWN_SECONDS


def build_writer_user_message(original_text: str) -> str:
    return (
        "Below is a page from a document. Treat it as the full document and "
        "the full page range to rewrite this turn — it's a single, "
        "self-contained passage, not a multi-page document.\n\n"
        f"---\n{original_text.strip()}\n---\n\n"
        "Produce your rewrite of this passage now."
    )


def _create(client: OpenAI, messages: list[dict]):
    try:
        # Some models default to a visible "thinking" pass even for a plain
        # rewrite — confirmed by testing, that burns ~20x the output tokens
        # for no benefit here.
        return client.chat.completions.create(
            model=WRITER_MODEL, messages=messages, temperature=0.3,
            max_tokens=MAX_WRITER_OUTPUT_TOKENS,
            extra_body={"reasoning": {"enabled": False}},
        )
    except Exception as exc:
        # Some models reject this param outright rather than ignoring it
        # ("Reasoning is mandatory for this endpoint"). Retry without it.
        if "reasoning" not in str(exc).lower():
            raise
        return client.chat.completions.create(
            model=WRITER_MODEL, messages=messages, temperature=0.3,
            max_tokens=MAX_WRITER_OUTPUT_TOKENS,
        )


def call_writer(client: OpenAI, original_text: str) -> str:
    if _credits_likely_exhausted():
        raise InsufficientCreditsError(
            "OpenRouter reported insufficient credits on a recent call; "
            "skipping this attempt rather than repeating the same failure."
        )

    system_prompt = load_prompt(PROMPTS_DIR / "writing_model_system_prompt.md")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": build_writer_user_message(original_text)},
    ]
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        _throttle()
        try:
            resp = _create(client, messages)
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            # The SDK sets status_code from the real HTTP response for
            # every APIStatusError, 402 included even though it has no
            # dedicated exception subclass (openai._exceptions maps only
            # 400/401/403/404/409/422/429/5xx by name) — reading it is
            # exact, unlike matching the message text, which is provider
            # wording ("This request requires more credits...", "This
            # request would exceed your available credits...") that isn't
            # guaranteed stable.
            if getattr(exc, "status_code", None) == 402:
                _mark_credits_exhausted()
                raise InsufficientCreditsError(str(exc)) from exc
            is_rate_limited = "429" in str(exc) or "rate limit" in str(exc).lower()
            if not is_rate_limited or attempt == RATE_LIMIT_RETRIES:
                raise
            # The cap is per minute, so waiting out most of a window is the
            # only thing that actually helps; a few hundred ms of backoff
            # would just burn another attempt.
            time.sleep(min(20.0 * (attempt + 1), 60.0))
    raise RuntimeError("unreachable")  # loop either returns or raises
