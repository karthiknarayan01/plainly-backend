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
  python eval/run_eval.py oracle --oracle-model openai/gpt-5

Requires OPENROUTER_API_KEY (e.g. via a local .env file — see
eval/README.md). Writer/judge model names default to
qwen/qwen3-235b-a22b-2507 and deepseek/deepseek-chat-v3.1, both
open-source per product requirement — override with --writer-model /
--judge-model to try others (e.g. a closed model for a one-off quality
comparison; see eval/README.md's model-compare section). `oracle`'s own
model defaults to anthropic/claude-sonnet-5 and is exempt from the
open-source requirement by design — it exists specifically to check the
open-source judge against a model with no incentive to share its blind
spots.
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
# Both must be open-source per explicit product requirement — see the
# fuller rationale in services/worker/llm.py, which this mirrors, and
# eval/README.md for the full benchmark writeup. Short version:
# deepseek/deepseek-chat-v3.1 is the single strongest open-source model
# at BOTH writing and judging (especially the "understanding" dimension,
# where qwen3-235b-a22b-2507 as judge barely discriminated flat-but-
# correct output from genuinely taught output). Using it for both roles
# would score highest but risks self-preference bias on every real-time
# approve/retry decision, so writer and judge are kept as different
# models: qwen3-235b-a22b-2507 as writer (best independent option),
# deepseek-chat-v3.1 as judge. Use the `oracle` command below to validate
# this tradeoff against a closed frontier model periodically.
DEFAULT_WRITER_MODEL = "qwen/qwen3-235b-a22b-2507"
DEFAULT_JUDGE_MODEL = "deepseek/deepseek-chat-v3.1"
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


MAX_JUDGE_OUTPUT_TOKENS = 3000


def call_judge(client: OpenAI, model: str, original_excerpt: str, rewrite: str) -> dict:
    system_prompt = load_prompt(PROMPTS_DIR / "judge_model_system_prompt.md")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": build_judge_user_message(original_excerpt, rewrite)},
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


SCORE_DIMENSIONS = ("fidelity", "understanding", "readability", "explanation", "style", "overall")


def violation_count(judge_result: dict) -> int:
    total = 0
    for key in ("loss", "gain", "distortion", "confusing_terms"):
        value = judge_result.get(key, [])
        if isinstance(value, list):
            total += len(value)
        elif value and str(value).strip().lower() not in ("none", ""):
            total += 1
    return total


def get_scores(judge_result: dict) -> dict:
    """Scores dict with all 5 dimensions present, defaulting missing ones to 0
    (a malformed/missing scores block should read as a failure, not silently
    drop out of an average)."""
    raw = judge_result.get("scores", {})
    return {dim: int(raw.get(dim, 0)) for dim in SCORE_DIMENSIONS}


def compute_approved(scores: dict) -> bool:
    """Recomputed from the judge's scores rather than trusting its
    self-reported `approved` field — mirrors services/worker/llm.py.
    Confirmed in production the judge can self-report approved=true while
    writing down fidelity below its own stated threshold; the eval runner
    needs to apply the same override or its calibration/benchmark numbers
    would disagree with what the production worker actually does.
    understanding >= 8 added alongside fidelity — see llm.compute_approved
    and judge_model_system_prompt.md's "understanding" dimension."""
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
    examples = load_examples(args.only, args.limit)

    results = []
    passed = 0
    good_scores_all, bad_scores_all = [], []
    for ex in examples:
        print(f"[{ex['id']}] judging good_rewrite...", flush=True)
        good = call_judge(client, judge_model, ex["original_excerpt"], ex["good_rewrite"])
        print(f"[{ex['id']}] judging bad_rewrite...", flush=True)
        bad = call_judge(client, judge_model, ex["original_excerpt"], ex["bad_rewrite"])

        good_scores, bad_scores = get_scores(good), get_scores(bad)
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
            print(f"    good verdict: {good.get('verdict_reason')}")
            print(f"    bad verdict:  {bad.get('verdict_reason')}")

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
    examples = load_examples(args.only, args.limit)

    results = []
    approved_count = 0
    all_scores = []
    for ex in examples:
        print(f"[{ex['id']}] generating rewrite...", flush=True)
        fresh_rewrite = call_writer(client, writer_model, ex)
        print(f"[{ex['id']}] judging...", flush=True)
        judge_result = call_judge(client, judge_model, ex["original_excerpt"], fresh_rewrite)

        scores = get_scores(judge_result)
        approved = compute_approved(scores)
        vcount = violation_count(judge_result)
        approved_count += int(approved)
        all_scores.append(scores)

        print(f"[{ex['id']}] {'APPROVED' if approved else 'REJECTED'} {format_scores(scores)} (violations={vcount})")
        if not approved:
            print(f"    verdict: {judge_result.get('verdict_reason')}")

        results.append(
            {
                "id": ex["id"],
                "approved": approved,
                "scores": scores,
                "violation_count": vcount,
                "fresh_rewrite": fresh_rewrite,
                "judge_result": judge_result,
                "reference_good_rewrite": ex["good_rewrite"],
            }
        )

    n = len(examples)
    avg_violations = sum(r["violation_count"] for r in results) / n if n else 0.0
    approval_rate = approved_count / n if n else 0.0
    avg_scores = average_scores(all_scores)
    print(f"\nBenchmark: {approved_count}/{n} approved ({approval_rate:.0%}). Avg violations: {avg_violations:.2f}")
    print(f"Avg scores: {format_scores({d: round(avg_scores[d], 2) for d in SCORE_DIMENSIONS})}")

    report = {
        "mode": "benchmark",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "writer_model": writer_model,
        "judge_model": judge_model,
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
    oracle_model = args.oracle_model
    examples = load_examples(args.only, args.limit)

    results = []
    agree = 0
    prod_all_scores, oracle_all_scores = [], []
    for ex in examples:
        print(f"[{ex['id']}] generating rewrite ({writer_model})...", flush=True)
        fresh_rewrite = call_writer(client, writer_model, ex)

        print(f"[{ex['id']}] production judge ({judge_model})...", flush=True)
        prod_result = call_judge(client, judge_model, ex["original_excerpt"], fresh_rewrite)
        prod_scores = get_scores(prod_result)
        prod_approved = compute_approved(prod_scores)

        print(f"[{ex['id']}] oracle judge ({oracle_model})...", flush=True)
        oracle_result = call_judge(client, oracle_model, ex["original_excerpt"], fresh_rewrite)
        oracle_scores = get_scores(oracle_result)
        oracle_approved = compute_approved(oracle_scores)

        agrees = prod_approved == oracle_approved
        agree += int(agrees)
        prod_all_scores.append(prod_scores)
        oracle_all_scores.append(oracle_scores)

        flag = "" if agrees else "  <-- DISAGREE"
        print(f"[{ex['id']}] production: {'APPROVED' if prod_approved else 'REJECTED'} {format_scores(prod_scores)}")
        print(f"[{ex['id']}] oracle:     {'APPROVED' if oracle_approved else 'REJECTED'} {format_scores(oracle_scores)}{flag}")
        if not agrees:
            print(f"    oracle verdict: {oracle_result.get('verdict_reason')}")

        results.append({
            "id": ex["id"],
            "fresh_rewrite": fresh_rewrite,
            "production": {"scores": prod_scores, "approved": prod_approved, "judge_result": prod_result},
            "oracle": {"scores": oracle_scores, "approved": oracle_approved, "judge_result": oracle_result},
            "agree": agrees,
        })

    n = len(examples)
    agreement_rate = agree / n if n else 0.0
    avg_prod = average_scores(prod_all_scores)
    avg_oracle = average_scores(oracle_all_scores)
    print(f"\nOracle validation: {agree}/{n} agree on approve/reject ({agreement_rate:.0%}).")
    print(f"Avg production judge scores: {format_scores({d: round(avg_prod[d], 2) for d in SCORE_DIMENSIONS})}")
    print(f"Avg oracle scores:           {format_scores({d: round(avg_oracle[d], 2) for d in SCORE_DIMENSIONS})}")
    if agreement_rate < 1.0:
        print("Disagreements above are where trusting the production judge alone would have shipped or rejected the wrong thing — inspect those first.")

    report = {
        "mode": "oracle",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "writer_model": writer_model,
        "judge_model": judge_model,
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
