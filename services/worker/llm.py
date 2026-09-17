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
# 2026-09-17: investigated switching production off the open-source-only
# pairing per explicit instruction ("I really don't care now, just use a
# much bigger but reliable and better model"), but the obvious swap —
# anthropic/claude-sonnet-5 for writer AND judge AND fact-check — was
# tried and REJECTED after real testing, not shipped: full 24-example
# `benchmark` run scored 2/24 approved (8%, worse than the 78% open-
# source baseline it would have replaced), root-caused to
# claude-sonnet-5-as-writer ignoring this prompt's explicit "no
# meta-commentary" instruction and printing its planning process as
# literal visible output ("Let me think through what's actually in this
# passage before rewriting...") on nearly every example — exactly the
# kind of defect a real user would see and call "garbage." Separately,
# claude-sonnet-5-as-judge/fact-check calibrated at only 2/24 against the
# hand-labeled good/bad reference set, including scoring several
# hand-labeled GOOD examples fidelity=0 — a real miscalibration against
# this rubric, not noise. Full results:
# eval/results/claude-writer-candidate-benchmark.json and
# eval/results/claude-judge-calibrate.json.
#
# openai/gpt-5 was tried first as writer and rejected even earlier, on
# reliability grounds: it forces internal reasoning it can't disable
# ("Reasoning is mandatory for this endpoint and cannot be disabled"),
# which ate the output token budget and produced a truncated rewrite then
# an empty-response crash on consecutive real eval examples.
#
# Follow-up: the writer's leaked-preamble problem turned out to be a real
# prompt gap, not a hard model limitation — writing_model_system_prompt.md
# now has an explicit "first word of your reply must be the first word of
# the rewrite" constraint, and a re-test with claude-sonnet-5 as writer
# confirmed the leak is gone (eval/results/claude-writer-promptfix-subset.json).
# claude-sonnet-5 is STILL not the writer default, though: with the leak
# fixed, what surfaced instead is a real fabrication tendency — invented
# hardware specs, a fabricated fiscal year, dropped precision qualifiers —
# landing at 33% approved on that same subset, still well behind this
# pairing's 78-100%. That's a genuine quality gap for this specific task,
# not a prompt bug, so it's not the writer here. The prompt fix itself IS
# kept — it's a general hardening (the exact failure mode eval example
# 009 exists for) confirmed not to regress this pairing's own performance
# (eval/results/current-production-with-promptfix.json).
#
# Then the whole picture changed once these were tested on REAL FULL
# PAGES instead of eval excerpts — and this is what's actually deployed.
# The eval set's median excerpt is 266 characters; a real book page is
# ~3,100. Every benchmark number above was measured on snippets 11x
# smaller than the real input, which hides the behaviour that matters at
# page scale. On a real 3,129-char page (The Art of Scalability, p.44):
#
# - claude-sonnet-5: 3,827 chars out — expanded the page, which is what a
#   teaching rewrite should do. Explains jargon inline ("availability —
#   basically, how often the site is up"), gives the reason behind each
#   claim, covers every question in the source.
# - deepseek-chat-v3.1: 2,304 chars out — COMPRESSED a 3,129-char page.
#   Accurate and readable, but flat, and coming out shorter than the
#   source is a bad sign for a "nothing dropped" guarantee.
# - qwen3-235b-a22b-2507 (the previous writer): 2,795 chars, and it
#   editorialises — invents a section heading and rhetorical asides
#   ("Who owns the peace?") that appear nowhere in the source.
#
# None truncated at MAX_WRITER_OUTPUT_TOKENS (claude used the most, 1,020
# of 2,000) and none leaked a preamble, now that the output-boundary
# constraint is in writing_model_system_prompt.md.
#
# That made claude-sonnet-5 the writer for a few hours — and then a
# purpose-built page-scale benchmark replaced that judgement with
# measurement, and found an open model that matches it. See
# eval/pages/README.md for the eval design and eval/run_page_eval.py for
# the harness; most of the score is computed in code (figures preserved,
# invented company names, output/input length ratio) rather than asked of
# a judge model. 10 real pages, median 2,376 chars:
#
#   model                        fidelity  fabricated  expansion  teaching
#   deepseek-v4.1-flash          100.0%         0        1.59       7.2
#   claude-sonnet-5 (closed ref) 100.0%         3        2.44       8.5
#   deepseek-v4-pro-0813          98.6%         0        1.24       4.2
#   minimax-m3                    93.7%         3        1.94       6.2
#   qwen3-235b-a22b-2507          85.2%         0        1.62       7.1
#   deepseek-chat-v3.1            85.2%         0        1.25       6.7
#   llama-4-maverick              80.3%         0        1.18       4.5
#   glm-5.3-flash                 empty responses — not a viable writer
#
# deepseek-v4.1-flash preserved every single figure across the set, the
# same as the closed reference, and unlike it invented nothing —
# claude-sonnet-5 put three real company names into the NVIDIA page that
# the source never mentions, which is precisely the fabrication failure
# this product cannot afford. It also never truncated, never leaked a
# preamble, and shrank only one page. It's open-weight, and ~25x cheaper
# on output than the closed reference.
#
# The honest gap: claude-sonnet-5 still teaches better (8.5 vs 7.2, and
# 81% vs 67% of jargon terms explained) and expands more generously.
# Fidelity is the promise, though, and an open model that holds fidelity
# while inventing nothing is the right trade here — closing the teaching
# gap is prompt work, not a reason to ship a closed model.
WRITER_MODEL = os.environ.get("WRITER_MODEL", "deepseek/deepseek-v4.1-flash")
# Judge deliberately stays a different model family from the writer —
# both to avoid self-preference bias on every approve/retry decision, and
# because deepseek-chat-v3.1 is the only judge candidate with a real
# calibration result behind it (21/24 against the hand-labeled set;
# claude-sonnet-5 as judge managed 2/24, scoring hand-labeled GOOD
# examples fidelity=0).
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "deepseek/deepseek-chat-v3.1")
# Fidelity comes from a separate, dedicated fact-check call rather than
# the combined judge call above — see judge_factcheck_system_prompt.md
# for why a single combined call proved unreliable.
#
# This was deepseek-r1-0528, picked for accuracy against the oracle's
# fidelity verdicts (5/6 on a sampled comparison). Changed 2026-09-17 on
# reliability grounds after watching it fail for real: mid-run it
# returned malformed, truncated JSON (literally `{"`) and crashed a
# benchmark, which is the same failure class already documented above for
# other reasoning models that can't disable their reasoning step and
# exhaust the output budget before emitting valid JSON. In production
# that exception marks the chunk FAILED, and a failed chunk is not a
# cosmetic problem — see chunksToSections in plainly-web: it used to hide
# every page after the first failure. An accurate fact-checker that
# intermittently destroys a document is worse than a slightly less
# accurate one that always answers.
FACTCHECK_MODEL = os.environ.get("FACTCHECK_MODEL", "deepseek/deepseek-chat-v3.1")
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


# A cap is necessary, confirmed by testing: without one, an OpenRouter
# provider route for qwen/qwen3-235b-a22b-2507 ignored the
# reasoning-disable param and requested a 131,072-token completion (its
# entire context window) for a single short passage, failing outright.
# This is a real-money and real-reliability guard against that class of
# runaway request on a real user's document.
#
# Raised 2000 -> 4000 on 2026-09-17, to match the cap the current
# writer+prompt combination was actually measured under. The prompt now
# explicitly tells the writer to come out LONGER than its source (that
# change took mean expansion 1.59 -> 2.09 and eliminated compressed
# pages), and the page-scale eval that validated it ran at 4000. Leaving
# production at 2000 would mean shipping a config no benchmark covered:
# the largest rewrite observed in those runs was ~1,550 tokens, only 23%
# under the old ceiling, and on a denser page than the eval set contains
# (its longest is 3,349 chars) an earnings page expanding 3.7x would have
# been truncated mid-sentence. A cap only bounds the worst case; it costs
# nothing when unused, since billing is on tokens actually generated.
MAX_WRITER_OUTPUT_TOKENS = 4000
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
