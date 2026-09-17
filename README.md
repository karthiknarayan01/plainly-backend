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
services/api/       FastAPI app — job creation, status, SSE stream over Postgres
services/worker/    claims jobs (SKIP LOCKED), runs the generate→judge→retry
                    loop (LangGraph) against OpenRouter, saves results
prompts/            system prompts for the writing model and the judge model
eval/               (original, good rewrite, bad rewrite, why) examples —
                    used both as DPO training data and as the held-out
                    benchmark for comparing model/prompt changes.
                    run_eval.py runs calibrate/benchmark/oracle — see
                    "Model evaluation" below and eval/README.md for the
                    full writeup.
infra/terraform/    GCP infra: Cloud SQL, Cloud Run (api + worker), Secret
                    Manager, IAM
infra/sql/          Postgres schema (rewrite_jobs, rewrite_chunks)
```

Writer and judge models are hosted via OpenRouter (pay-per-token), not
self-hosted — no GPU infra in this repo. Earlier this project ran writer/
judge as self-hosted Cloud Run GPU services, but that needed GCP GPU
quota approval and cost hundreds to thousands of dollars/month to keep
warm, which didn't fit this project's experimental scope; that
infrastructure was removed entirely.

## Model evaluation

This section is a summary — `eval/README.md` has the full writeup,
including every candidate tried and why each one was ruled out.

### How the eval set is built

`eval/examples/*.yaml` holds hand-built, paired examples: a real
`original_excerpt` (from an actual SEC earnings filing or a real
production job's input — never invented), a hand-written `good_rewrite`
(what we'd want the writer to produce — for two examples this is the
user's own commissioned rewrite, done with Claude Opus 5, used as the
target quality bar), a hand-written `bad_rewrite` (a plausible but flawed
rewrite), and a rationale for each explaining exactly why it's good or
bad. Nine real examples exist as of this writing, each targeting a
specific, previously-observed failure mode — dropped numbers, invented
reasons, unexplained jargon, mishandled tables of contents, leaked
model preambles. Several were added directly from real production
failures (the actual input/output a real document produced), not
synthesized worst cases.

`run_eval.py calibrate` checks whether a candidate judge model can tell
our own `good_rewrite` from our own `bad_rewrite` — if it can't, it can't
be trusted to score anything else, so this runs before any other eval.
`run_eval.py benchmark` runs a candidate writer model on every
`original_excerpt` fresh and has the judge score the real output — this
is the actual quality measurement used to pick a writer.

### How the judge scores a rewrite

The judge model reads the original passage and a rewrite, and returns six
0-10 scores plus lists of specific issues found (see
`prompts/judge_model_system_prompt.md` for the full rubric with anchor
descriptions for every score band):

- **fidelity** — is every claim, number, and fact from the original
  actually present, unchanged, with nothing invented.
- **understanding** — not "is the vocabulary simple" but "did a real
  bridge (analogy, image, worked example) get built for every idea that
  needed one," scored as an explicit ratio (ideas-with-a-bridge /
  non-trivial ideas), not an impression.
- **readability** — no unexplained jargon for a reader with no
  background in the subject.
- **explanation** — why/how, not just what; a claim explained beats a
  claim merely stated.
- **style** — short sentences, direct language, no hedging (mechanics
  only — separate from `understanding`, which is about whether the ideas
  actually land).
- **overall** — holistic, but capped by `fidelity` and `understanding`
  specifically: a rewrite can be a little plain and still ship, but not
  incomplete or incomprehensible.

`approved` is **never** trusted from the judge's own self-report — it's
recomputed in code (`overall >= 8 AND fidelity >= 9 AND understanding >=
8`) from the scores the judge already produced, after finding in
production that a judge model can write down `fidelity: 7` and
`approved: true` in the same response, contradicting its own stated rule.

### Benchmark results

All numbers are from real API calls against the eval set above, not
estimates. Full per-example detail in `eval/results/model-compare/` and
the "Judge candidates tried" / "Writer candidates tried" sections of
`eval/README.md`.

**Judge candidates** (`calibrate`, does the judge agree with our own
good/bad labels):

| Model | Open-source | Calibration |
|---|---|---|
| `google/gemini-2.5-flash` | No | 8/9 (was production judge before the open-source requirement) |
| `deepseek/deepseek-chat-v3.1` | Yes | 9/9 — also the only candidate that reliably scores `understanding` correctly (see below) |
| `qwen/qwen3-235b-a22b-2507` | Yes | 9/9 on the original rubric, but barely discriminates on `understanding` even with explicit arithmetic instructions |
| `z-ai/glm-4.6` | Yes | 6/9 — ruled out |
| `openai/gpt-4o-mini` | No | Ruled out — flagged jargon from the original passage as if it appeared in the rewrite it was judging |
| `meta-llama/llama-3.1-70b-instruct` | Yes | Ruled out — hit a persistently overloaded shared capacity pool on OpenRouter |

**Writer candidates** (`benchmark`, real fresh output judged for quality):

| Model | Open-source | Approval rate | Avg violations | Notes |
|---|---|---|---|---|
| `openai/gpt-5` | No | 100% (9/9) | 0.22 | Closed reference. Only model of 5 tested that handled a table-of-contents page correctly on the first try |
| `deepseek/deepseek-chat-v3.1` | Yes | 100% (9/9)* | 0.00* | *Under the final rubric+prompt, judged independently by qwen3-235b — the single best result of any model, open or closed |
| `anthropic/claude-sonnet-5` | No | 78% (7/9) | 0.67 | Closed reference — scored *worse* than every open candidate on this eval set |
| `qwen/qwen3-235b-a22b-2507` | Yes | 78% (7/9)* | — | *Under final rubric+prompt, judged by deepseek-chat-v3.1 — **the deployed writer** |
| `qwen/qwen3-32b` | Yes | 56-89%** | 1.00-4.33** | **Varies by rubric version — was the original production writer |

A real finding along the way: table-of-contents mishandling (dropped
page numbers, paraphrased titles) failed identically across every model
tested except GPT-5 — including closed Claude Sonnet 5. That was strong
evidence it was a **prompt gap** (nothing told any model that structural
content needs different handling than prose), not a model capability
gap, and fixing the prompt resolved it for every model, not just the one
deployed.

### Why writer and judge are different models

`deepseek/deepseek-chat-v3.1` tested as the single best open-source model
at **both** writing and judging. It isn't used for both: a judge sharing
a model family with the writer risks self-preference bias (rating its
own family's output more favorably) on every real-time production
approve/retry decision. Deployed pairing: **`qwen/qwen3-235b-a22b-2507`
as writer, `deepseek/deepseek-chat-v3.1` as judge** — a deliberate
quality tradeoff (78% vs. 100% approval on the eval set) in exchange for
that separation.

### Oracle validation — checking the checker

`run_eval.py oracle` is not part of the live pipeline. It re-scores the
same fresh writer output with both the production (open-source) judge
and a closed frontier "oracle" model — **`anthropic/claude-sonnet-5`** by
default — and reports where they disagree, to answer "is our cheap judge
actually trustworthy" with measured evidence instead of a one-time
calibration run.

First real run (all 9 examples): **5/9 agreement (56%)** on
approve/reject. The most serious disagreement — production judge scored
a fresh rewrite `fidelity: 10` (perfect) while the oracle rejected it at
`fidelity: 6`, because the rewrite had **fabricated specific company
names and technical details** (invented AWS/Google Cloud/Azure
references, invented factory/server details) not present anywhere in the
original passage. The production judge missed it entirely; the oracle
caught it on the first try. This is real, measured evidence of what the
open-source-only requirement costs in reliability — not grounds to
override that requirement, but a gap worth monitoring by re-running
`oracle` periodically, especially after any prompt or model change.

## Status

Real end-to-end pipeline is live: PDF upload → per-page text extraction →
job/chunk queue in Postgres → worker runs generate→judge→retry per chunk
against OpenRouter → results stream back over SSE. See "Model evaluation"
above for how the writer/judge models were chosen and what's still an
open reliability question.

`dev` is the default/live branch; `main` only ever advances via a dev →
main promotion PR (see `.github/workflows/enforce-dev-to-main.yml`).
