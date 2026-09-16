"""OpenRouter client + writer/judge calls for the worker.

This is the same logic already validated against real API calls in
eval/run_eval.py — lifted here rather than re-derived, since that
version is what's been tested (reasoning-disable handling, GPT-5
fallback, judge JSON parsing, rubric score extraction all confirmed
working against real requests earlier this session).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from openai import OpenAI

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Both must be open-source (open-weight) models per explicit product
# requirement — closed models are for benchmark comparison only, never
# production. Picked from a real benchmark against the eval set
# (eval/results/model-compare/): deepseek/deepseek-chat-v3.1 was the
# strongest open-source writer tested (same 89% approval rate as the
# Qwen3 candidates, but far fewer violations: 1.11 avg vs 4.33-4.89, and
# perfect 10/10/10 on readability/explanation/style). qwen/qwen3-235b-
# a22b-2507 was picked as judge over deepseek/deepseek-chat-v3.1 despite
# the latter's own perfect 9/9 calibration score, specifically to avoid
# using the same model as both writer and judge — an LLM judging its own
# model family's output risks self-preference bias, which is exactly the
# kind of blind spot that motivated fixing the judge in the first place.
WRITER_MODEL = os.environ.get("WRITER_MODEL", "deepseek/deepseek-chat-v3.1")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "qwen/qwen3-235b-a22b-2507")
REQUEST_TIMEOUT_SECONDS = 120

SCORE_DIMENSIONS = ("fidelity", "readability", "explanation", "style", "overall")


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


def build_writer_user_message(original_text: str, feedback: str | None) -> str:
    if feedback:
        context = (
            "Below is a page from a document. Treat it as the full document and "
            "the full page range to rewrite this turn — it's a single, "
            "self-contained passage, not a multi-page document. This is a retry "
            f"after feedback on your previous attempt:\n\n{feedback.strip()}"
        )
    else:
        context = (
            "Below is a page from a document. Treat it as the full document and "
            "the full page range to rewrite this turn — it's a single, "
            "self-contained passage, not a multi-page document, and this is a "
            "first attempt (no prior feedback)."
        )
    return f"{context}\n\n---\n{original_text.strip()}\n---\n\nProduce your rewrite of this passage now."


def build_judge_user_message(original_text: str, rewrite: str) -> str:
    return (
        f"Original passage:\n---\n{original_text.strip()}\n---\n\n"
        f"Rewritten version:\n---\n{rewrite.strip()}\n---\n\n"
        f"Evaluate the rewrite."
    )


# A single-page rewrite has no legitimate reason to need more than this —
# confirmed necessary by testing: without a cap, one OpenRouter provider
# route for qwen/qwen3-235b-a22b-2507 ignored the reasoning-disable param
# and requested a 131,072-token completion (its entire context window)
# for a single short passage, failing outright. This is a real-money and
# real-reliability guard against exactly that class of runaway request on
# a real user's document, not just a benchmark convenience.
MAX_WRITER_OUTPUT_TOKENS = 2000
MAX_JUDGE_OUTPUT_TOKENS = 3000


def call_writer(client: OpenAI, original_text: str, feedback: str | None) -> str:
    system_prompt = load_prompt(PROMPTS_DIR / "writing_model_system_prompt.md")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": build_writer_user_message(original_text, feedback)},
    ]
    try:
        # Qwen3 defaults to "thinking mode" even for a plain rewrite task —
        # confirmed by testing, this burns ~20x more output tokens for no
        # benefit here.
        resp = client.chat.completions.create(
            model=WRITER_MODEL, messages=messages, temperature=0.3,
            max_tokens=MAX_WRITER_OUTPUT_TOKENS,
            extra_body={"reasoning": {"enabled": False}},
        )
    except Exception as exc:
        # Some models (confirmed: GPT-5) reject this outright rather than
        # ignoring it. Retry without it.
        if "reasoning" not in str(exc).lower():
            raise
        resp = client.chat.completions.create(
            model=WRITER_MODEL, messages=messages, temperature=0.3, max_tokens=MAX_WRITER_OUTPUT_TOKENS
        )
    return resp.choices[0].message.content.strip()


def parse_judge_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"judge did not return valid JSON:\n{raw}")


def call_judge(client: OpenAI, original_text: str, rewrite: str) -> dict:
    system_prompt = load_prompt(PROMPTS_DIR / "judge_model_system_prompt.md")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": build_judge_user_message(original_text, rewrite)},
    ]
    try:
        resp = client.chat.completions.create(
            model=JUDGE_MODEL,
            messages=messages,
            temperature=0.0,
            max_tokens=MAX_JUDGE_OUTPUT_TOKENS,
            response_format={"type": "json_object"},
            extra_body={"reasoning": {"enabled": False}},
        )
    except Exception:
        # not every OpenRouter provider supports response_format — the
        # prompt itself demands JSON, so fall back to parsing that instead.
        resp = client.chat.completions.create(
            model=JUDGE_MODEL, messages=messages, temperature=0.0, max_tokens=MAX_JUDGE_OUTPUT_TOKENS
        )
    return parse_judge_json(resp.choices[0].message.content)


def get_scores(judge_result: dict) -> dict:
    raw = judge_result.get("scores", {})
    return {dim: int(raw.get(dim, 0)) for dim in SCORE_DIMENSIONS}


def compute_approved(scores: dict) -> bool:
    """Recomputes approval from the judge's own scores rather than trusting
    its self-reported `approved` field. Confirmed in production (job
    f0081b63, chunk 8) that the judge can write down fidelity=7 and
    approved=true in the same JSON object despite its own prompt stating
    the threshold is fidelity>=9 — a real instruction-following failure,
    not a parsing bug. The threshold is simple arithmetic over numbers the
    judge already produced, so there's no reason to let an LLM's
    inconsistent self-report be the thing that ships or rejects a page."""
    return scores["overall"] >= 8 and scores["fidelity"] >= 9
