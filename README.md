# Plainly Backend

The document-processing and rewrite service backing the
[Plainly app](https://github.com/karthiknarayan01/plainly-app).

Given a PDF, this service produces a plain-language, novel-like rewrite
that stays semantically faithful to the original — same ideas, same
claims, same structure, nothing added or dropped — while preserving
screenshots, charts, and images from the source in context.

## Why

Technical and financial documents are often needlessly hard to read.
Corporate earnings statements in particular are frequently written (and
legally reviewed) in a way that obscures rather than clarifies — dense
jargon, passive voice, buried caveats. The goal is to give ordinary readers
a way to actually understand what a company's earnings release or 10-Q is
saying, without losing or distorting any of the substance.

Primary focus areas:
1. Earnings statements / financial filings — highest priority, since this
   is where the readability gap is most deliberate and the stakes for
   laypeople are highest.
2. Technical books and manuals more broadly.

## Structure

```
services/api/      FastAPI app — job creation, status, SSE stream (stub for now)
services/worker/    claims jobs, runs the generate→judge→retry loop (stub for now)
prompts/            system prompts for the writing model and the judge model
eval/                (original, good rewrite, bad rewrite, why) examples —
                     used both as DPO training data and as the held-out
                     benchmark for comparing model/prompt changes.
                     run_eval.py runs the actual calibrate/benchmark checks.
infra/terraform/    GCP infra: Cloud SQL, Cloud Run (api + worker only), IAM/WIF
```

Writer and judge models are hosted via OpenRouter (pay-per-token), not
self-hosted — no GPU infra in this repo. Earlier this project ran writer/
judge as self-hosted Cloud Run GPU services (Qwen3-32B + a fine-tuned
judge model), but that needed GCP GPU quota approval and cost hundreds to
thousands of dollars/month to keep warm, which doesn't fit this project's
experimental scope. `eval/run_eval.py`'s endpoint config still needs
updating to point at OpenRouter instead of the old Cloud Run URLs — not
done yet.

Full architecture (diagrams, schema, API surface, the generate→judge→retry
quality gate): see the design doc — ask for the link if you don't have it.

## Status

Infra and prompts scaffolded. Real API/worker logic (the actual job
handling and the generate→judge→retry loop) is the next piece of work.

`dev` is the default/live branch; `main` only ever advances via a dev →
main promotion PR (see `.github/workflows/enforce-dev-to-main.yml`).
