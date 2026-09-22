#!/usr/bin/env python3
"""Plots input-token-count vs. latency from real production logs.

This is an offline analysis tool, not a live production dashboard for
every possible latency factor — that distinction matters because it's
the same "no false claims" standard this project applies elsewhere (see
eval/README.md and the top-level README.md's Results section): the
Cloud Monitoring dashboard in infra/terraform/metrics.tf answers "what
is p95/p99 latency right now," and is what the README's latency
screenshot comes from. This script answers a different, narrower
question — "does a bigger input page cost more time" — by pulling a
batch of real llm_call_end log lines and drawing a scatter plot, once,
by hand. It is not wired into any pipeline and does not run on a
schedule.

Usage:
  # 1. Pull recent structured logs from Cloud Logging as JSONL:
  gcloud logging read \\
    'resource.type="cloud_run_revision" jsonPayload.event="llm_call_end"' \\
    --format=json --limit=1000 > /tmp/llm_calls.json
  python eval/observability/format_gcloud_logs.py /tmp/llm_calls.json > /tmp/llm_calls.jsonl

  # 2. Plot it:
  python eval/observability/analyze_latency.py /tmp/llm_calls.jsonl \\
      --out eval/observability/latency_vs_tokens.png

Each line of the input file is expected to be one llm_call_end JSON
object (the exact shape services/worker/llm.py's log() call emits) —
either straight from a local run's stdout, or reshaped from a
`gcloud logging read --format=json` export via
format_gcloud_logs.py (a real gcloud export nests the payload one level
deeper, under `jsonPayload`, plus a lot of Cloud Logging envelope fields
this script doesn't need).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def load_calls(path: Path) -> list[dict]:
    calls = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if row.get("event") != "llm_call_end":
            continue
        if row.get("input_tokens") is None or row.get("ttft_ms") is None:
            continue
        calls.append(row)
    return calls


def _corr(xs: list, ys: list) -> float | None:
    if len(xs) < 2 or not hasattr(statistics, "correlation"):
        return None
    try:
        return statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None  # constant input — correlation undefined, not an error here


def summarize(calls: list[dict]) -> None:
    in_tok = [c["input_tokens"] for c in calls]
    out_tok = [c["output_tokens"] for c in calls if c.get("output_tokens") is not None]
    ttft = [c["ttft_ms"] for c in calls]
    total = [c["total_ms"] for c in calls if c.get("total_ms") is not None]
    print(f"{len(calls)} calls loaded")
    print(f"input_tokens:  min={min(in_tok)} median={statistics.median(in_tok)} max={max(in_tok)}")
    if out_tok:
        print(f"output_tokens: min={min(out_tok)} median={statistics.median(out_tok)} max={max(out_tok)}")
    print(f"ttft_ms:       min={min(ttft):.0f} median={statistics.median(ttft):.0f} max={max(ttft):.0f}")
    if total:
        print(f"total_ms:      min={min(total):.0f} median={statistics.median(total):.0f} max={max(total):.0f}")

    # Which factor actually drives latency, rather than assuming it's input
    # size. Measured on real traffic, the answer was output tokens, by a
    # lot — see this directory's README.
    for name, xs, ys, ylab in (
        ("input_tokens -> ttft_ms", in_tok, ttft, "ttft"),
        ("input_tokens -> total_ms", in_tok, total, "total"),
        ("output_tokens -> total_ms", out_tok, total, "total"),
    ):
        if len(xs) == len(ys) and len(xs) >= 2:
            r = _corr(xs, ys)
            if r is not None:
                print(f"correlation({name}) = {r:+.3f}")
    if out_tok and total and len(out_tok) == len(total):
        per_tok = [t / o for t, o in zip(total, out_tok) if o]
        if per_tok:
            print(f"ms per output token: min={min(per_tok):.1f} "
                  f"median={statistics.median(per_tok):.1f} max={max(per_tok):.1f}")


def plot(calls: list[dict], out_path: Path) -> None:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        raise SystemExit(
            "matplotlib is not installed — pip install matplotlib, "
            "or run with --summary-only to skip plotting."
        )

    in_tok = [c["input_tokens"] for c in calls]
    out_tok = [c.get("output_tokens") for c in calls]
    ttft = [c["ttft_ms"] for c in calls]
    total_ms = [c.get("total_ms") for c in calls]

    fig, axes = plt.subplots(1, 3, figsize=(14, 4.4))

    axes[0].scatter(in_tok, ttft, alpha=0.85, s=45, c="tab:green", edgecolors="none")
    axes[0].set_xlabel("input tokens")
    axes[0].set_ylabel("time to first token (ms)")
    axes[0].set_title("TTFT vs. input size\n(milliseconds either way — input size barely matters)",
                      fontsize=9.5)

    axes[1].scatter(in_tok, total_ms, alpha=0.85, s=45, c="tab:blue", edgecolors="none")
    axes[1].set_xlabel("input tokens")
    axes[1].set_ylabel("total completion time (ms)")
    axes[1].set_title("Total vs. input size\n(rises, but confounded: longer page → longer rewrite)",
                      fontsize=9.5)

    axes[2].scatter(out_tok, total_ms, alpha=0.85, s=45, c="tab:orange", edgecolors="none")
    axes[2].set_xlabel("output tokens")
    axes[2].set_ylabel("total completion time (ms)")
    axes[2].set_title("Total vs. OUTPUT size\n(near-linear — the variable that actually tracks)",
                      fontsize=9.5)
    # Fit line, to make the linearity legible rather than asserted.
    pairs = [(o, t) for o, t in zip(out_tok, total_ms) if o is not None and t is not None]
    if len(pairs) >= 2:
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        slope = sum(x * y for x, y in pairs) / sum(x * x for x in xs)  # through origin
        lo, hi = min(xs), max(xs)
        axes[2].plot([0, hi], [0, slope * hi], "--", color="gray", linewidth=1)
        axes[2].annotate(f"~{slope:.1f} ms per output token",
                         xy=(lo, slope * hi * 0.55), fontsize=9, color="dimgray")

    fig.suptitle(f"What actually drives writer latency — {len(calls)} real production calls")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    print(f"saved: {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", type=Path, help="JSONL file of llm_call_end log lines")
    ap.add_argument("--out", type=Path, default=Path("eval/observability/latency_vs_tokens.png"))
    ap.add_argument("--summary-only", action="store_true", help="print stats, skip the plot")
    args = ap.parse_args()

    if not args.logs.exists():
        raise SystemExit(f"not found: {args.logs}")
    calls = load_calls(args.logs)
    if not calls:
        raise SystemExit("no usable llm_call_end lines found (need input_tokens and ttft_ms on each)")

    summarize(calls)
    if not args.summary_only:
        plot(calls, args.out)


if __name__ == "__main__":
    main()
