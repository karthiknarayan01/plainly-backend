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


def summarize(calls: list[dict]) -> None:
    tokens = [c["input_tokens"] for c in calls]
    ttft = [c["ttft_ms"] for c in calls]
    total = [c["total_ms"] for c in calls if c.get("total_ms") is not None]
    print(f"{len(calls)} calls loaded")
    print(f"input_tokens: min={min(tokens)} median={statistics.median(tokens)} max={max(tokens)}")
    print(f"ttft_ms:      min={min(ttft):.0f} median={statistics.median(ttft):.0f} max={max(ttft):.0f}")
    if total:
        print(f"total_ms:     min={min(total):.0f} median={statistics.median(total):.0f} max={max(total):.0f}")
    if len(tokens) >= 2:
        r = statistics.correlation(tokens, ttft) if hasattr(statistics, "correlation") else None
        if r is not None:
            print(f"correlation(input_tokens, ttft_ms) = {r:.3f}")


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

    tokens = [c["input_tokens"] for c in calls]
    ttft = [c["ttft_ms"] for c in calls]
    total_ms = [c.get("total_ms") for c in calls]

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5))

    axes[0].scatter(tokens, ttft, alpha=0.6, s=18)
    axes[0].set_xlabel("input tokens")
    axes[0].set_ylabel("time to first token (ms)")
    axes[0].set_title("TTFT vs. input size")

    axes[1].scatter(tokens, total_ms, alpha=0.6, s=18, color="tab:orange")
    axes[1].set_xlabel("input tokens")
    axes[1].set_ylabel("total completion time (ms)")
    axes[1].set_title("Total latency vs. input size")

    fig.suptitle(f"Writer latency vs. input size — {len(calls)} real calls")
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
