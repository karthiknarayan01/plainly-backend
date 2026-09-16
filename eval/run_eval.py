#!/usr/bin/env python3
"""Eval runner for Plainly's writing/judge models, served via OpenRouter.

Two modes:

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

Usage:
  python eval/run_eval.py calibrate
  python eval/run_eval.py benchmark
  python eval/run_eval.py benchmark --only 001 --out eval/results/run1.json

Requires OPENROUTER_API_KEY (e.g. via a local .env file — see
eval/README.md). Writer/judge model names default to qwen/qwen3-32b and
meta-llama/llama-3.1-70b-instruct — override with --writer-model /
--judge-model to try others, since Selene-1-Mini (the originally
researched judge model) isn't available hosted anywhere.
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
DEFAULT_WRITER_MODEL = "qwen/qwen3-32b"
# meta-llama/llama-3.1-70b-instruct was the original pick but hit a
# persistently overloaded shared capacity pool on both its backend
# providers when tested. openai/gpt-4o-mini was reliable but failed a
# real calibration case — confirmed by inspecting its raw output, it
# flagged jargon terms ("GAAP", "operating expenses") that appear only
# in original_excerpt, not in the rewrite it was supposed to be judging,
# meaning it wasn't reliably distinguishing the two texts. gemini-2.5-flash
# got the same case right (0 violations on the good rewrite, correctly
# rejected the bad one) — confirmed by testing, not just picked by name.
DEFAULT_JUDGE_MODEL = "google/gemini-2.5-flash"
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
            extra_body={"reasoning": {"enabled": False}},
        )
    except Exception as exc:
        # Some models (confirmed: GPT-5) reject this outright — "Reasoning
        # is mandatory for this endpoint and cannot be disabled" — rather
        # than just ignoring the field. Retry without it.
        if "reasoning" not in str(exc).lower():
            raise
        resp = client.chat.completions.create(model=model, messages=messages, temperature=0.3)
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
            response_format={"type": "json_object"},
            extra_body={"reasoning": {"enabled": False}},
        )
    except Exception:
        # not every model/provider on OpenRouter supports response_format —
        # the prompt itself demands JSON, so fall back to parsing that instead.
        resp = client.chat.completions.create(model=model, messages=messages, temperature=0.0)
    return parse_judge_json(resp.choices[0].message.content)


SCORE_DIMENSIONS = ("fidelity", "readability", "explanation", "style", "overall")


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

        # Require good to actually be approved and bad rejected, plus a
        # real gap in overall score — not every dimension strictly greater
        # (style/readability legitimately tie at 10/10 even when fidelity
        # is what actually separates a good rewrite from a bad one).
        example_pass = (
            bool(good.get("approved"))
            and not bool(bad.get("approved"))
            and good_scores["overall"] > bad_scores["overall"]
        )

        status = "PASS" if example_pass else "FAIL"
        print(f"[{ex['id']}] {status}")
        print(f"    good: {format_scores(good_scores)} approved={good.get('approved')}")
        print(f"    bad:  {format_scores(bad_scores)} approved={bad.get('approved')}")
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

        approved = bool(judge_result.get("approved"))
        scores = get_scores(judge_result)
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

    args = parser.parse_args()
    if args.command == "calibrate":
        sys.exit(run_calibrate(args))
    else:
        sys.exit(run_benchmark(args))


if __name__ == "__main__":
    main()
