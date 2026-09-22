#!/usr/bin/env python3
"""Reshapes a `gcloud logging read --format=json` export into the flat
JSONL analyze_latency.py expects.

A real gcloud export is a JSON array of full LogEntry objects — severity,
resource, timestamp, insertId, and the rest of the envelope, with the
fields services/worker/logging_json.py actually logged nested one level
down under `jsonPayload`. This pulls just that nested object back out,
one per line, dropping entries gcloud returned that aren't structured
JSON payloads at all (a stray textPayload line, for instance).

Usage:
  gcloud logging read \\
    'resource.type="cloud_run_revision" jsonPayload.event="llm_call_end"' \\
    --format=json --limit=1000 > /tmp/llm_calls.json
  python eval/observability/format_gcloud_logs.py /tmp/llm_calls.json > /tmp/llm_calls.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <gcloud-export.json>")
    path = Path(sys.argv[1])
    entries = json.loads(path.read_text())
    n = 0
    for entry in entries:
        payload = entry.get("jsonPayload")
        if not payload:
            continue
        print(json.dumps(payload))
        n += 1
    print(f"{n} entries written", file=sys.stderr)


if __name__ == "__main__":
    main()
