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
# production. Picked from real benchmarks against the eval set
# (eval/results/model-compare/, eval/README.md has the full writeup):
#
# deepseek/deepseek-chat-v3.1 is the strongest open-source WRITER found
# by a wide margin (100% approved, 0 avg violations, 9.44 avg
# "understanding" score, judged independently by a different model) — but
# it's also, by a wide margin, the strongest open-source JUDGE found at
# the "understanding" dimension specifically (see that dimension's
# history in judge_model_system_prompt.md): qwen/qwen3-235b-a22b-2507 as
# judge barely discriminated flat-but-correct output from genuinely
# taught output even after two rounds of rubric tightening, while
# deepseek-chat-v3.1 as judge caught it immediately and matched a closed
# frontier model's (claude-sonnet-5) verdict on the same case.
#
# Using deepseek-chat-v3.1 for both roles would be the single best-
# scoring combination, but risks self-preference bias — a model judging
# its own family's output more favorably — on every real-time production
# approve/retry decision, not just an occasional spot check. Chose to
# keep writer and judge as different models instead: qwen/qwen3-235b-
# a22b-2507 as writer (the best remaining independent option: 78%
# approved, 8.89 avg understanding under deepseek-chat-v3.1's own
# scoring) and deepseek-chat-v3.1 as judge, accepting somewhat lower
# writer quality than the single best-scoring pairing in exchange for
# writer and judge never being the same model at decision time. The
# `oracle` eval mode (eval/run_eval.py) exists to periodically validate
# this whole pairing against a closed frontier model and should be
# re-run if this tradeoff needs revisiting.
WRITER_MODEL = os.environ.get("WRITER_MODEL", "qwen/qwen3-235b-a22b-2507")
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "deepseek/deepseek-chat-v3.1")
# Fidelity now comes from a separate, dedicated fact-check call rather
# than the combined judge call above (see judge_factcheck_system_prompt.md
# for why: an oracle-validation run found the combined judge agreeing with
# a closed frontier model on approve/reject only 38% of the time on a
# 24-example set, with the disagreement spread across almost every
# dimension rather than concentrated in one fixable pattern — a
# systematic leniency gap, not a couple of bugs). Splitting the call out
# alone didn't close the gap (still 38% with deepseek-chat-v3.1 doing
# fact-checking) — a direct comparison of fact-check-only candidates
# against the oracle's own fidelity verdicts on the same 24 rewrites
# found deepseek/deepseek-r1-0528 matching 5/6 sampled cases with zero
# errors, clearly ahead of minimax/minimax-m2 (consistently too lenient,
# 1/6) and moonshotai/kimi-k2-thinking + qwen/qwen3-next-80b-a3b-thinking
# (both too unreliable — frequent empty responses, likely not respecting
# the reasoning-disable param). See eval/README.md for the full writeup;
# this is a first, real improvement, not a final answer — worth
# re-running `oracle` against it before trusting the gap is closed.
FACTCHECK_MODEL = os.environ.get("FACTCHECK_MODEL", "deepseek/deepseek-r1-0528")
# Self-consistency: call the fact-check model this many times and take
# the MEDIAN fidelity score (see call_fact_check_consistent for why not
# the strictest — that amplifies a single run's false positive as much as
# it catches a real miss). Dialed down to 1 (i.e. off) for now:
# deepseek-r1-0528 is a reasoning model that already does substantial
# internal reasoning on every single call (confirmed by testing: ~2300
# reasoning tokens on a short passage, and it cannot disable this the way
# deepseek-chat-v3.1 could — OpenRouter rejects the reasoning-disable
# param outright for this model), so it needs self-consistency less than
# a non-reasoning judge would, and 3x calls on an already-slow reasoning
# model measurably slows down real document processing. Raise this back
# to 3 if oracle validation shows it's still missing things a single pass
# catches only some of the time.
FACTCHECK_CONSISTENCY_N = 1
REQUEST_TIMEOUT_SECONDS = 120

SCORE_DIMENSIONS = ("fidelity", "understanding", "readability", "explanation", "style", "overall")


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
MAX_JUDGE_OUTPUT_TOKENS = 6000


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


def _call_judge_model(client: OpenAI, model: str, system_prompt: str, user_message: str, max_tokens: int) -> dict:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            extra_body={"reasoning": {"enabled": False}},
        )
    except Exception:
        # not every OpenRouter provider supports response_format — the
        # prompt itself demands JSON, so fall back to parsing that instead.
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.0, max_tokens=max_tokens
        )
    return parse_judge_json(resp.choices[0].message.content)


def call_judge(client: OpenAI, original_text: str, rewrite: str) -> dict:
    """The 'how well is this taught and written' half of judging —
    understanding/readability/explanation/style. No longer scores fidelity
    or approval at all; see call_fact_check_consistent for that."""
    system_prompt = load_prompt(PROMPTS_DIR / "judge_model_system_prompt.md")
    user_message = build_judge_user_message(original_text, rewrite)
    return _call_judge_model(client, JUDGE_MODEL, system_prompt, user_message, MAX_JUDGE_OUTPUT_TOKENS)


def call_fact_check(client: OpenAI, model: str, original_text: str, rewrite: str) -> dict:
    """A single fact-check pass: {"fidelity": int, "loss": [...], "gain":
    [...], "distortion": [...], "verdict_reason": str}. Use
    call_fact_check_consistent for the self-consistency wrapper actually
    used in production — this is exposed separately for testing/eval."""
    system_prompt = load_prompt(PROMPTS_DIR / "judge_factcheck_system_prompt.md")
    user_message = build_judge_user_message(original_text, rewrite)
    return _call_judge_model(client, model, system_prompt, user_message, MAX_JUDGE_OUTPUT_TOKENS)


def call_fact_check_consistent(
    client: OpenAI, original_text: str, rewrite: str, n: int = FACTCHECK_CONSISTENCY_N
) -> dict:
    """Runs call_fact_check n times and takes the MEDIAN fidelity score,
    not the minimum. Median, not strictest, deliberately: confirmed by
    testing that "take the lowest of n" also amplifies a single run's
    false positive (one hallucinated violation out of three independent
    passes) just as much as it catches a single run's real miss — the
    aggregation needs to be robust to outliers in both directions, not
    biased toward the harshest run. The qualitative loss/gain/distortion
    lists ARE still a union of all n runs — an extra, possibly-spurious
    item in the feedback text costs little (the writer briefly checks
    something that was fine), whereas the same asymmetry on the numeric
    score directly changes the approve/reject decision, where it matters."""
    results = [call_fact_check(client, FACTCHECK_MODEL, original_text, rewrite) for _ in range(n)]
    fidelities = sorted(int(r.get("fidelity", 0)) for r in results)
    median_fidelity = fidelities[len(fidelities) // 2]
    # Use whichever run's fidelity matches the median for the merged verdict_reason/other
    # fields (arbitrary but deterministic choice among ties).
    base = next(r for r in results if int(r.get("fidelity", 0)) == median_fidelity)
    merged = dict(base)
    merged["fidelity"] = median_fidelity
    for key in ("loss", "gain", "distortion"):
        seen: list[str] = []
        for r in results:
            for item in r.get(key, []) or []:
                if item not in seen:
                    seen.append(item)
        merged[key] = seen
    return merged


def get_scores(judge_result: dict, fact_check_result: dict) -> dict:
    """Combines the two calls' outputs into the full SCORE_DIMENSIONS
    dict. `overall` is computed here (capped by fidelity/understanding
    exactly as the old single-call judge prompt used to ask itself to do)
    rather than trusted from either call's self-report, for the same
    reason compute_approved doesn't trust a self-reported `approved`."""
    judge_scores = judge_result.get("scores", {})
    scores = {
        "fidelity": int(fact_check_result.get("fidelity", 0)),
        "understanding": int(judge_scores.get("understanding", 0)),
        "readability": int(judge_scores.get("readability", 0)),
        "explanation": int(judge_scores.get("explanation", 0)),
        "style": int(judge_scores.get("style", 0)),
    }
    scores["overall"] = compute_overall(scores)
    return scores


def compute_overall(scores: dict) -> int:
    """A plain average of the four judged dimensions, capped by fidelity
    and understanding — a fluent but incomplete or un-understandable
    rewrite is worse than a clunky but complete, understood one, so
    neither cap lets a high average paper over a low score on either."""
    avg = round((scores["understanding"] + scores["readability"] + scores["explanation"] + scores["style"]) / 4)
    overall = avg
    if scores["fidelity"] < 7:
        overall = min(overall, scores["fidelity"])
    if scores["understanding"] < 7:
        overall = min(overall, scores["understanding"])
    return overall


def compute_approved(scores: dict) -> bool:
    """Deterministic approval from the combined scores — never trusted as
    a self-report from either call. fidelity >= 9 and understanding >= 8
    both get their own bars since together they're the one thing that
    matters most: a rewrite can be somewhat plain and still ship, but not
    somewhat incomplete and not somewhat un-understandable."""
    return scores["overall"] >= 8 and scores["fidelity"] >= 9 and scores["understanding"] >= 8
