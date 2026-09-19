#!/usr/bin/env python3
"""Eval runner for Plainly's writing/judge models, served via OpenRouter.

Three modes:

  calibrate   Sanity-checks the judge model itself. For every example in
              eval/examples/, this sends the judge the original_excerpt
              paired with the hand-labeled good_rewrite, then again paired
              with the hand-labeled bad_rewrite, and checks the judge
              approves the good one and rejects the bad one. If the judge
              can't tell our own good example from our own bad one, it
              can't be trusted to grade anything else — run this first.

  benchmark   The real eval. Runs original_excerpt through the writing
              model to get a fresh rewrite, then has the judge score that
              fresh output. Reports an approval rate and per-example
              detail, and writes a JSON report so runs can be compared
              over time (e.g. after a prompt or model change).

  oracle      Validates the production writer+judge pairing itself,
              rather than trusting a good calibration run forever. Runs
              the production writer, then scores the same fresh output
              with BOTH the production (open-source) judge and a closed
              frontier "oracle" model, and reports where they disagree on
              approve/reject. Not part of the live pipeline — this is
              deliberately expensive and meant to be run occasionally
              (after a model or prompt change, or periodically as a
              sanity check) to answer "is our cheap judge actually
              trustworthy," not on every real chunk.

Usage:
  python eval/run_eval.py calibrate
  python eval/run_eval.py benchmark
  python eval/run_eval.py benchmark --only 001 --out eval/results/run1.json
  python eval/run_eval.py oracle

Requires OPENROUTER_API_KEY (e.g. via a local .env file — see
eval/README.md). Writer/judge/fact-check default to the open-source
pairing documented in eval/README.md (qwen3-235b-a22b-2507 writer,
deepseek-chat-v3.1 judge, deepseek-r1-0528 fact-check) — override any of
--writer-model / --judge-model / --factcheck-model / --oracle-model to
try others.

2026-09-17: a full anthropic/claude-sonnet-5-for-everything candidate was
tried and rejected here, not shipped — 8% approved on `benchmark` (a
severe regression from this pairing's 78%), caused by claude-sonnet-5 as
writer ignoring this prompt's "no meta-commentary" instruction and
printing its planning process as visible output on nearly every example,
plus claude-sonnet-5 as judge miscalibrating badly against the existing
rubric (2/24 on `calibrate`, including scoring hand-labeled GOOD
reference examples fidelity=0). See services/worker/llm.py and
eval/README.md for the full writeup and the results files. Also ruled
out: openai/gpt-5 as writer — it forces internal reasoning it can't
disable, confirmed to eat the output token budget and produce a
truncated or empty rewrite on real eval examples.

The leaked-preamble part of that was a real, fixable prompt gap —
writing_model_system_prompt.md now has an explicit output-boundary
constraint, and a re-test confirmed it stops the leak. claude-sonnet-5
still isn't the writer default, though: with the leak gone, what's left
is a real fabrication tendency (invented specifics, dropped precision
qualifiers) that landed it at 33% on a subset re-test — better than 8%,
still behind this pairing's 78-100%. See services/worker/llm.py for the
full current state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from openai import OpenAI

SCRIPT_DIR = Path(__file__).resolve().parent
EXAMPLES_DIR = SCRIPT_DIR / "examples"
RESULTS_DIR = SCRIPT_DIR / "results"
PROMPTS_DIR = SCRIPT_DIR.parent / "prompts"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Mirrors services/worker/llm.py, which has the full rationale and
# 2026-09-17 history of what was tried and reverted. Reverted to the
# validated open-source pairing while a fix for the writer's
# leaked-preamble problem (see that file) is developed and re-tested.
DEFAULT_WRITER_MODEL = "qwen/qwen3-235b-a22b-2507"
DEFAULT_JUDGE_MODEL = "deepseek/deepseek-chat-v3.1"
DEFAULT_FACTCHECK_MODEL = "deepseek/deepseek-r1-0528"
# Dialed down to 1 (off) — see services/worker/llm.py's fuller rationale:
# a reasoning model already does substantial internal reasoning per call
# and cannot disable it, so self-consistency matters less here than for a
# non-reasoning judge, and 3x calls measurably slows down an already-slow
# model. Override with --factcheck-consistency to test raising it again.
DEFAULT_FACTCHECK_CONSISTENCY_N = 1
REQUEST_TIMEOUT_SECONDS = 120


def load_prompt(path: Path) -> str:
    """Strip the leading '# Title' + '> Status: ...' header, keep the rest."""
    text = path.read_text(encoding="utf-8")
    marker = "\n---\n"
    idx = text.find(marker)
    if idx != -1:
        return text[idx + len(marker):].strip()
    return text.strip()


def load_examples(only: Optional[str], limit: Optional[int]) -> list[dict]:
    examples = []
    for f in sorted(EXAMPLES_DIR.glob("*.yaml")):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if data["id"].startswith("000-"):
            continue  # synthetic, format-illustration only — never scored
        if only and not data["id"].startswith(only):
            continue
        examples.append(data)
    if limit:
        examples = examples[:limit]
    if not examples:
        raise SystemExit("no eval examples matched — check --only/--limit")
    return examples


def make_client() -> OpenAI:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise SystemExit(
            "OPENROUTER_API_KEY is not set — put it in a local .env file "
            "(see eval/README.md) and `source` it before running this script."
        )
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        timeout=REQUEST_TIMEOUT_SECONDS,
        # OpenRouter routes across multiple backend providers, and
        # individual ones (DeepInfra, Bedrock, etc.) can transiently
        # rate-limit or time out — confirmed by testing, the SDK's
        # default of 2 retries wasn't always enough.
        max_retries=5,
    )


def build_writer_user_message(example: dict) -> str:
    kind = "earnings statement" if example["source_type"] == "earnings_statement" else "technical book chapter"
    return (
        f"Below is an excerpt from a {kind}. Treat it as the full document and "
        f"the full page range to rewrite this turn — it's a single, "
        f"self-contained passage, not a multi-page document, and this is a "
        f"first attempt (no prior feedback).\n\n"
        f"---\n{example['original_excerpt'].strip()}\n---\n\n"
        f"Produce your rewrite of this passage now."
    )


def build_judge_user_message(original_excerpt: str, rewrite: str) -> str:
    return (
        f"Original passage:\n---\n{original_excerpt.strip()}\n---\n\n"
        f"Rewritten version:\n---\n{rewrite.strip()}\n---\n\n"
        f"Evaluate the rewrite."
    )


# A single-page rewrite has no legitimate reason to need more than this —
# real pages in the eval set and production runs top out at a few hundred
# output tokens. Confirmed necessary by testing: without a cap, one
# provider route for qwen/qwen3-235b-a22b-2507 ignored the reasoning-
# disable param and requested a 131,072-token completion (its entire
# context window) for a single short passage, failing outright rather
# than just wasting tokens. This is a real-money and real-reliability
# guard, not just a benchmark convenience — a production worker call must
# never be able to do this on a real user's document.
MAX_WRITER_OUTPUT_TOKENS = 2000


def call_writer(client: OpenAI, model: str, example: dict) -> str:
    system_prompt = load_prompt(PROMPTS_DIR / "writing_model_system_prompt.md")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": build_writer_user_message(example)},
    ]
    try:
        # Qwen3 defaults to "thinking mode" (visible chain-of-thought before
        # the answer) even for a plain rewrite task — confirmed by testing,
        # this burns ~20x more output tokens for no benefit here.
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.3,
            max_tokens=MAX_WRITER_OUTPUT_TOKENS,
            extra_body={"reasoning": {"enabled": False}},
        )
    except Exception as exc:
        # Some models (confirmed: GPT-5) reject this outright — "Reasoning
        # is mandatory for this endpoint and cannot be disabled" — rather
        # than just ignoring the field. Retry without it.
        if "reasoning" not in str(exc).lower():
            raise
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.3, max_tokens=MAX_WRITER_OUTPUT_TOKENS
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


MAX_JUDGE_OUTPUT_TOKENS = 6000


def _call_judge_model(client: OpenAI, model: str, system_prompt: str, user_message: str) -> dict:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.0,
            max_tokens=MAX_JUDGE_OUTPUT_TOKENS,
            response_format={"type": "json_object"},
            extra_body={"reasoning": {"enabled": False}},
        )
    except Exception:
        # not every model/provider on OpenRouter supports response_format —
        # the prompt itself demands JSON, so fall back to parsing that instead.
        resp = client.chat.completions.create(
            model=model, messages=messages, temperature=0.0, max_tokens=MAX_JUDGE_OUTPUT_TOKENS
        )
    return parse_judge_json(resp.choices[0].message.content)


_JUDGE_PROMPT = load_prompt(PROMPTS_DIR / "judge_model_system_prompt.md")
_FACTCHECK_PROMPT = load_prompt(PROMPTS_DIR / "judge_factcheck_system_prompt.md")


def call_judge(client: OpenAI, model: str, original_excerpt: str, rewrite: str) -> dict:
    """understanding/readability/explanation/style — no fidelity, no
    approval. See call_fact_check_consistent for fidelity."""
    return _call_judge_model(client, model, _JUDGE_PROMPT, build_judge_user_message(original_excerpt, rewrite))


def call_fact_check(client: OpenAI, model: str, original_excerpt: str, rewrite: str) -> dict:
    """A single fact-check pass: {"fidelity": int, "loss": [...], "gain":
    [...], "distortion": [...], "verdict_reason": str}."""
    return _call_judge_model(client, model, _FACTCHECK_PROMPT, build_judge_user_message(original_excerpt, rewrite))


def call_fact_check_consistent(client: OpenAI, model: str, original_excerpt: str, rewrite: str, n: int) -> dict:
    """Runs call_fact_check n times and takes the MEDIAN fidelity score
    (not the minimum — mirrors services/worker/llm.py's fuller rationale:
    "strictest wins" amplifies a single run's false positive just as much
    as it catches a real miss). Loss/gain/distortion lists are still a
    union of all n runs — cheap to over-include in feedback text, unlike
    the numeric score which directly drives the approve/reject decision."""
    results = [call_fact_check(client, model, original_excerpt, rewrite) for _ in range(n)]
    fidelities = sorted(int(r.get("fidelity", 0)) for r in results)
    median_fidelity = fidelities[len(fidelities) // 2]
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


SCORE_DIMENSIONS = ("fidelity", "understanding", "readability", "explanation", "style", "overall")


def violation_count(combined_result: dict) -> int:
    total = 0
    for key in ("loss", "gain", "distortion", "confusing_terms"):
        value = combined_result.get(key, [])
        if isinstance(value, list):
            total += len(value)
        elif value and str(value).strip().lower() not in ("none", ""):
            total += 1
    return total


def compute_overall(scores: dict) -> int:
    """A plain average of the four judged dimensions, capped by fidelity
    and understanding — mirrors services/worker/llm.py."""
    avg = round((scores["understanding"] + scores["readability"] + scores["explanation"] + scores["style"]) / 4)
    overall = avg
    if scores["fidelity"] < 7:
        overall = min(overall, scores["fidelity"])
    if scores["understanding"] < 7:
        overall = min(overall, scores["understanding"])
    return overall


def get_scores(judge_result: dict, fact_check_result: dict) -> dict:
    """Combines the two calls' outputs into the full SCORE_DIMENSIONS
    dict, computing `overall` here rather than trusting either call's
    self-report — mirrors services/worker/llm.py."""
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


def merge_judge_results(judge_result: dict, fact_check_result: dict) -> dict:
    """For storage/printing: one dict carrying both calls' qualitative
    fields, mirroring retry_graph.py's combined_result."""
    return {
        **judge_result,
        "loss": fact_check_result.get("loss", []),
        "gain": fact_check_result.get("gain", []),
        "distortion": fact_check_result.get("distortion", []),
        "fidelity_verdict": fact_check_result.get("verdict_reason"),
    }


def compute_approved(scores: dict) -> bool:
    """Deterministic approval from the combined scores — never trusted as
    a self-report from either call. Mirrors services/worker/llm.py."""
    return scores["overall"] >= 8 and scores["fidelity"] >= 9 and scores["understanding"] >= 8


def format_scores(scores: dict) -> str:
    return " ".join(f"{dim[:3]}={scores[dim]}" for dim in SCORE_DIMENSIONS)


def average_scores(all_scores: list[dict]) -> dict:
    n = len(all_scores) or 1
    return {dim: sum(s[dim] for s in all_scores) / n for dim in SCORE_DIMENSIONS}


def write_report(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def default_report_path(mode: str) -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return RESULTS_DIR / f"{ts}-{mode}.json"


def run_calibrate(args: argparse.Namespace) -> int:
    client = make_client()
    judge_model = args.judge_model
    factcheck_model = args.factcheck_model
    n = args.factcheck_consistency
    examples = load_examples(args.only, args.limit)

    results = []
    passed = 0
    good_scores_all, bad_scores_all = [], []
    for ex in examples:
        print(f"[{ex['id']}] judging good_rewrite...", flush=True)
        good_judge = call_judge(client, judge_model, ex["original_excerpt"], ex["good_rewrite"])
        good_fc = call_fact_check_consistent(client, factcheck_model, ex["original_excerpt"], ex["good_rewrite"], n)
        print(f"[{ex['id']}] judging bad_rewrite...", flush=True)
        bad_judge = call_judge(client, judge_model, ex["original_excerpt"], ex["bad_rewrite"])
        bad_fc = call_fact_check_consistent(client, factcheck_model, ex["original_excerpt"], ex["bad_rewrite"], n)

        good, bad = merge_judge_results(good_judge, good_fc), merge_judge_results(bad_judge, bad_fc)
        good_scores, bad_scores = get_scores(good_judge, good_fc), get_scores(bad_judge, bad_fc)
        good_scores_all.append(good_scores)
        bad_scores_all.append(bad_scores)
        good_approved, bad_approved = compute_approved(good_scores), compute_approved(bad_scores)

        # Require good to actually be approved and bad rejected, plus a
        # real gap in overall score — not every dimension strictly greater
        # (style/readability legitimately tie at 10/10 even when fidelity
        # is what actually separates a good rewrite from a bad one).
        example_pass = (
            good_approved
            and not bad_approved
            and good_scores["overall"] > bad_scores["overall"]
        )

        status = "PASS" if example_pass else "FAIL"
        print(f"[{ex['id']}] {status}")
        print(f"    good: {format_scores(good_scores)} approved={good_approved}")
        print(f"    bad:  {format_scores(bad_scores)} approved={bad_approved}")
        if not example_pass:
            print(f"    good verdict: {good.get('verdict_reason')} | {good.get('fidelity_verdict')}")
            print(f"    bad verdict:  {bad.get('verdict_reason')} | {bad.get('fidelity_verdict')}")

        results.append({
            "id": ex["id"], "pass": example_pass,
            "good_scores": good_scores, "bad_scores": bad_scores,
            "good": good, "bad": bad,
        })
        passed += int(example_pass)

    avg_good = average_scores(good_scores_all)
    avg_bad = average_scores(bad_scores_all)
    print(f"\nCalibration: {passed}/{len(examples)} examples passed.")
    print(f"Avg good scores: {format_scores({d: round(avg_good[d], 1) for d in SCORE_DIMENSIONS})}")
    print(f"Avg bad scores:  {format_scores({d: round(avg_bad[d], 1) for d in SCORE_DIMENSIONS})}")

    out_path = Path(args.out) if args.out else default_report_path("calibrate")
    write_report(out_path, {
        "mode": "calibrate", "judge_model": judge_model,
        "avg_good_scores": avg_good, "avg_bad_scores": avg_bad,
        "results": results,
    })
    print(f"Report written to {out_path}")

    return 0 if passed == len(examples) else 1


def run_benchmark(args: argparse.Namespace) -> int:
    client = make_client()
    writer_model = args.writer_model
    judge_model = args.judge_model
    factcheck_model = args.factcheck_model
    n = args.factcheck_consistency
    examples = load_examples(args.only, args.limit)

    results = []
    approved_count = 0
    all_scores = []
    for ex in examples:
        print(f"[{ex['id']}] generating rewrite...", flush=True)
        fresh_rewrite = call_writer(client, writer_model, ex)
        print(f"[{ex['id']}] judging...", flush=True)
        judge_result = call_judge(client, judge_model, ex["original_excerpt"], fresh_rewrite)
        fact_check_result = call_fact_check_consistent(client, factcheck_model, ex["original_excerpt"], fresh_rewrite, n)
        combined = merge_judge_results(judge_result, fact_check_result)

        scores = get_scores(judge_result, fact_check_result)
        approved = compute_approved(scores)
        vcount = violation_count(combined)
        approved_count += int(approved)
        all_scores.append(scores)

        print(f"[{ex['id']}] {'APPROVED' if approved else 'REJECTED'} {format_scores(scores)} (violations={vcount})")
        if not approved:
            print(f"    verdict: {combined.get('verdict_reason')} | {combined.get('fidelity_verdict')}")

        results.append(
            {
                "id": ex["id"],
                "approved": approved,
                "scores": scores,
                "violation_count": vcount,
                "fresh_rewrite": fresh_rewrite,
                "judge_result": combined,
                "reference_good_rewrite": ex["good_rewrite"],
            }
        )

    total = len(examples)
    avg_violations = sum(r["violation_count"] for r in results) / total if total else 0.0
    approval_rate = approved_count / total if total else 0.0
    avg_scores = average_scores(all_scores)
    print(f"\nBenchmark: {approved_count}/{total} approved ({approval_rate:.0%}). Avg violations: {avg_violations:.2f}")
    print(f"Avg scores: {format_scores({d: round(avg_scores[d], 2) for d in SCORE_DIMENSIONS})}")

    report = {
        "mode": "benchmark",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "writer_model": writer_model,
        "judge_model": judge_model,
        "factcheck_model": factcheck_model,
        "factcheck_consistency": n,
        "approval_rate": approval_rate,
        "avg_violations": avg_violations,
        "avg_scores": avg_scores,
        "results": results,
    }
    out_path = Path(args.out) if args.out else default_report_path("benchmark")
    write_report(out_path, report)
    print(f"Report written to {out_path}")

    return 0


# Not for production — this is deliberately closed and expensive, used to
# periodically ask "is our cheap open-source judge actually trustworthy?"
# rather than trust it forever on the strength of one calibration run.
# claude-sonnet-5 by default because it's the model the target style/
# quality bar (the commissioned reference rewrites in eval/examples/) was
# itself produced with, and because it caught a real production judge
# miss (see eval/README.md's "understanding" dimension writeup) on the
# first try, with no rubric iteration needed.
DEFAULT_ORACLE_MODEL = "anthropic/claude-sonnet-5"


def run_oracle(args: argparse.Namespace) -> int:
    client = make_client()
    writer_model = args.writer_model
    judge_model = args.judge_model
    factcheck_model = args.factcheck_model
    n = args.factcheck_consistency
    oracle_model = args.oracle_model
    examples = load_examples(args.only, args.limit)

    results = []
    agree = 0
    prod_all_scores, oracle_all_scores = [], []
    for ex in examples:
        print(f"[{ex['id']}] generating rewrite ({writer_model})...", flush=True)
        fresh_rewrite = call_writer(client, writer_model, ex)

        print(f"[{ex['id']}] production judge ({judge_model} + {factcheck_model} x{n})...", flush=True)
        prod_judge = call_judge(client, judge_model, ex["original_excerpt"], fresh_rewrite)
        prod_fc = call_fact_check_consistent(client, factcheck_model, ex["original_excerpt"], fresh_rewrite, n)
        prod_combined = merge_judge_results(prod_judge, prod_fc)
        prod_scores = get_scores(prod_judge, prod_fc)
        prod_approved = compute_approved(prod_scores)

        # Oracle uses the same two-call architecture (for a fair,
        # apples-to-apples comparison against the production two-call
        # setup) but no self-consistency — a strong closed model doesn't
        # need three passes to catch what it catches in one.
        print(f"[{ex['id']}] oracle judge ({oracle_model})...", flush=True)
        oracle_judge = call_judge(client, oracle_model, ex["original_excerpt"], fresh_rewrite)
        oracle_fc = call_fact_check(client, oracle_model, ex["original_excerpt"], fresh_rewrite)
        oracle_combined = merge_judge_results(oracle_judge, oracle_fc)
        oracle_scores = get_scores(oracle_judge, oracle_fc)
        oracle_approved = compute_approved(oracle_scores)

        agrees = prod_approved == oracle_approved
        agree += int(agrees)
        prod_all_scores.append(prod_scores)
        oracle_all_scores.append(oracle_scores)

        flag = "" if agrees else "  <-- DISAGREE"
        print(f"[{ex['id']}] production: {'APPROVED' if prod_approved else 'REJECTED'} {format_scores(prod_scores)}")
        print(f"[{ex['id']}] oracle:     {'APPROVED' if oracle_approved else 'REJECTED'} {format_scores(oracle_scores)}{flag}")
        if not agrees:
            print(f"    oracle verdict: {oracle_combined.get('verdict_reason')} | {oracle_combined.get('fidelity_verdict')}")

        results.append({
            "id": ex["id"],
            "fresh_rewrite": fresh_rewrite,
            "production": {"scores": prod_scores, "approved": prod_approved, "judge_result": prod_combined},
            "oracle": {"scores": oracle_scores, "approved": oracle_approved, "judge_result": oracle_combined},
            "agree": agrees,
        })

    total = len(examples)
    agreement_rate = agree / total if total else 0.0
    avg_prod = average_scores(prod_all_scores)
    avg_oracle = average_scores(oracle_all_scores)
    print(f"\nOracle validation: {agree}/{total} agree on approve/reject ({agreement_rate:.0%}).")
    print(f"Avg production judge scores: {format_scores({d: round(avg_prod[d], 2) for d in SCORE_DIMENSIONS})}")
    print(f"Avg oracle scores:           {format_scores({d: round(avg_oracle[d], 2) for d in SCORE_DIMENSIONS})}")
    if agreement_rate < 1.0:
        print("Disagreements above are where trusting the production judge alone would have shipped or rejected the wrong thing — inspect those first.")

    report = {
        "mode": "oracle",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "writer_model": writer_model,
        "judge_model": judge_model,
        "factcheck_model": factcheck_model,
        "factcheck_consistency": n,
        "oracle_model": oracle_model,
        "agreement_rate": agreement_rate,
        "avg_production_scores": avg_prod,
        "avg_oracle_scores": avg_oracle,
        "results": results,
    }
    out_path = Path(args.out) if args.out else default_report_path("oracle")
    write_report(out_path, report)
    print(f"Report written to {out_path}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--only", default=None, help="only run examples whose id starts with this prefix, e.g. 00 or 006")
    common.add_argument("--limit", type=int, default=None, help="only run the first N matching examples")
    common.add_argument("--out", default=None, help="path to write the JSON report (default: eval/results/<timestamp>-<mode>.json)")
    common.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL, help=f"OpenRouter model slug for the judge (default: {DEFAULT_JUDGE_MODEL})")
    common.add_argument("--factcheck-model", default=DEFAULT_FACTCHECK_MODEL, help=f"OpenRouter model slug for the fact-check call (default: {DEFAULT_FACTCHECK_MODEL})")
    common.add_argument("--factcheck-consistency", type=int, default=DEFAULT_FACTCHECK_CONSISTENCY_N, help=f"self-consistency runs for the fact-check call, median fidelity wins (default: {DEFAULT_FACTCHECK_CONSISTENCY_N})")

    cal = sub.add_parser("calibrate", parents=[common], help="check the judge model against hand-labeled good/bad pairs")

    bench = sub.add_parser("benchmark", parents=[common], help="run the writing model + judge model against the eval set")
    bench.add_argument("--writer-model", default=DEFAULT_WRITER_MODEL, help=f"OpenRouter model slug for the writer (default: {DEFAULT_WRITER_MODEL})")

    oracle = sub.add_parser("oracle", parents=[common], help="validate the production writer+judge pairing against a closed frontier model")
    oracle.add_argument("--writer-model", default=DEFAULT_WRITER_MODEL, help=f"OpenRouter model slug for the writer (default: {DEFAULT_WRITER_MODEL})")
    oracle.add_argument("--oracle-model", default=DEFAULT_ORACLE_MODEL, help=f"OpenRouter model slug for the oracle judge (default: {DEFAULT_ORACLE_MODEL})")

    args = parser.parse_args()
    if args.command == "calibrate":
        sys.exit(run_calibrate(args))
    elif args.command == "benchmark":
        sys.exit(run_benchmark(args))
    else:
        sys.exit(run_oracle(args))


if __name__ == "__main__":
    main()
