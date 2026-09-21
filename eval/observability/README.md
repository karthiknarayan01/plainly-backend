# Latency observability

Two different tools for two different questions, on purpose — neither
substitutes for the other:

| | `infra/terraform/metrics.tf` | `analyze_latency.py` (this dir) |
|---|---|---|
| Question answered | "What is p95/p99 latency right now?" | "Does a bigger input cost more time?" |
| Data source | Live Cloud Monitoring, continuously | A batch of exported log lines, once |
| Where it runs | Cloud Run + Cloud Monitoring, always on | Your machine, on demand |
| What it produces | A dashboard, queryable anytime | One PNG, from whatever you exported |

## The live metrics (production dashboard)

`services/worker/logging_json.py` writes structured JSON to stdout on
every log call; Cloud Run ingests that as Cloud Logging automatically.
`infra/terraform/metrics.tf` turns fields from those logs into Cloud
Monitoring DISTRIBUTION metrics, queryable for p50/p95/p99 with no
app-side metrics library. The dashboard it also creates
(`google_monitoring_dashboard.writer_latency`) is what the top-level
`README.md`'s latency screenshot comes from.

What matters here is time-to-first-token specifically, not total
completion time — and TTFT for a page isn't one opaque number, it's the
sum of four measured components, each its own metric so a slow page can
be attributed to a cause instead of guessed at:

| Metric | What it measures | Logged from |
|---|---|---|
| `page_claim_wait_ms` | Queueing delay — time between a chunk being created and a worker lane claiming it | `page_completed` |
| `page_classify_ms` | Cost of the in-code `classify_page_type()` heuristic | `page_completed` |
| `page_throttle_wait_ms` | Time blocked in the rate limiter before the request was sent | `page_completed` |
| `writer_ttft_ms` | Model + network time from dispatch to first token | `llm_call_end` |
| **`page_ttft_since_claim_ms`** | **The sum of all four — the real, full time a reader waited for a page's first visible output. The headline number.** | `page_completed` |
| `writer_total_ms` | Model + network time from dispatch to full completion (not TTFT — a separate, slower number) | `llm_call_end` |
| `page_total_ms` | The whole page through the DB save — for capacity planning, not the headline latency number | `page_completed` |

`page_ttft_since_claim_ms` is computed once, in
`services/worker/main.py`'s `process_chunk()`, from the other four
components measured right where each actually happens
(`claim_and_process_loop()` for queueing, `classify_page_type()`'s own
call for classification, `llm.py`'s `_throttle()` and `_consume_stream()`
for the rate-limit and model components) — not estimated or
reconstructed after the fact.

## The offline analysis script (this directory)

`analyze_latency.py` answers a narrower, one-off question: does writer
latency actually track input size, and by how much. It is not wired
into any pipeline, doesn't run on a schedule, and isn't a second copy of
the dashboard above — it reads a batch of real `llm_call_end` log lines
(exported once via `gcloud logging read`, reshaped by
`format_gcloud_logs.py`) and plots `input_tokens` against `ttft_ms` and
`total_ms`.

```bash
gcloud logging read \
  'resource.type="cloud_run_revision" jsonPayload.event="llm_call_end"' \
  --format=json --limit=1000 > /tmp/llm_calls.json
python eval/observability/format_gcloud_logs.py /tmp/llm_calls.json > /tmp/llm_calls.jsonl
python eval/observability/analyze_latency.py /tmp/llm_calls.jsonl \
    --out eval/observability/latency_vs_tokens.png
```

Needs `matplotlib` (in `eval/requirements.txt`) and real exported log
data — there's nothing to plot before the pipeline has actually run in
an environment where Cloud Logging is receiving these lines (Cloud Run,
not a bare local run against a local Postgres, though `--summary-only`
works against any JSONL of the same shape, local runs included).
