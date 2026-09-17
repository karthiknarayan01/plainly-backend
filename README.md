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
services/worker/    claims jobs (SKIP LOCKED), makes ONE writer call per
                    page against OpenRouter, saves results
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

> **2026-09-17 (latest) — the judge is gone and the writer is Claude.**
> By explicit request the feedback models were removed entirely: there is
> now **one model and one call per page**, `anthropic/claude-sonnet-5` as
> writer, with no judge, no fact-check and no retry loop.
>
> What that costs, stated plainly: nothing now checks a rewrite before a
> reader sees it. The judge was the only thing verifying that every figure
> survived and that nothing was invented, and in the benchmark below this
> writer did fabricate three company names on one page. `eval/` still
> measures all of it offline, and that is now the only place quality is
> checked at all.
>
> What it buys: a page costs one OpenRouter round-trip instead of three,
> and the retry loop's up-to-three attempts collapse to one — up to 9x
> fewer calls per page on a long document, which is what makes a 200-page
> upload finish in minutes.
>
> The section below documents the open-weight benchmark that ran before
> this change. It remains the best evidence available about writer
> quality, and `deepseek/deepseek-v4.1-flash` remains the strongest
> open-weight option measured (100% figure preservation, zero
> fabrications) if the open-source requirement is ever reinstated.
>
> **Superseded (kept for the benchmark data):** all three production
> models being open-weight —
> writer `deepseek/deepseek-v4.1-flash`, judge and fact-check
> `deepseek/deepseek-chat-v3.1`.
>
> The writer was picked by benchmarking 8 candidates on 10 **real full
> pages**, scored mostly by metrics computed in code rather than by a
> judge model's opinion. See `eval/pages/README.md` for the eval design
> and the "2026-09-17 (final)" section of `eval/README.md` for the
> results table and caveats.
>
> | | fidelity (figures kept) | fabricated names | teaching |
> |---|---|---|---|
> | **`deepseek-v4.1-flash`** (now deployed) | **100.0%** | **0** | 7.2 |
> | `claude-sonnet-5` (closed reference) | 100.0% | 3 | 8.5 |
> | `qwen3-235b-a22b-2507` (previously deployed) | 85.2% | 0 | 7.1 |
>
> The open model matched the closed frontier reference on fidelity and
> beat it on fabrication, so production needs no closed model. The
> previously deployed writer was silently dropping about one figure in
> seven — a serious defect on a financial document, and one the older
> excerpt-based benchmark rated "78% approved."
>
> Everything in this section below this note predates that benchmark and
> was measured on hand-trimmed excerpts with a median length of 266
> characters — roughly 9x smaller than a real page. Kept as history, not
> as the current basis for anything.

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

### How a rewrite gets scored — two separate calls, not one

Judging is split into two independent model calls, not one combined
call — see "Why two calls" below for why. Together they produce six 0-10
scores (`services/worker/llm.py`'s `get_scores` combines them; neither
call's own `approved`/`overall` self-report is trusted):

- **fidelity** (`prompts/judge_factcheck_system_prompt.md`) — a
  dedicated fact-check pass: is every claim, number, and fact from the
  original actually present, unchanged, with nothing invented. Includes
  explicit checks for fabrication dressed as an illustrative analogy
  (a real company name standing in for a generic reference like "major
  cloud providers") and for a precise term quietly swapped for a
  similar-but-different one ("gross margin" → "profit"). Run with
  self-consistency (`FACTCHECK_CONSISTENCY_N`, currently 1) — multiple
  independent passes, median fidelity score wins, not the strictest
  (testing showed "strictest wins" amplifies a single run's false
  positive as much as it catches a real miss).
- **understanding, readability, explanation, style**
  (`prompts/judge_model_system_prompt.md`) — the "how well is this
  taught and written" half. `understanding` in particular is not "is the
  vocabulary simple" but "did a real bridge (analogy, image, worked
  example) get built for every idea that needed one," scored as an
  explicit ratio (ideas-with-a-bridge / non-trivial ideas), not an
  impression.
- **overall** — computed in code (`compute_overall`), a plain average of
  the four judged dimensions capped by `fidelity` and `understanding`
  specifically: a rewrite can be a little plain and still ship, but not
  incomplete or incomprehensible.

`approved` is **never** trusted from either call's self-report — it's
recomputed in code (`overall >= 8 AND fidelity >= 9 AND understanding >=
8`), after finding in production that a judge model can write down
`fidelity: 7` and `approved: true` in the same response, contradicting
its own stated rule.

### Why two calls, not one

The original design used one combined call for all six dimensions. An
oracle-validation run (below) found it agreeing with a closed frontier
model on approve/reject only 38% of the time across 24 real examples,
with the disagreement spread across almost every dimension — a
systematic leniency gap, not one or two fixable bugs. Splitting fidelity
into its own dedicated call (nothing else competing for the model's
attention) plus self-consistency didn't move that number on its own —
still 38% with the same model doing fact-checking. What did help: a
direct comparison of fact-check-only candidates against the oracle's own
verdicts found `deepseek/deepseek-r1-0528` matching 5/6 sampled cases
with zero errors, clearly ahead of the alternatives tried (see
`eval/README.md` for the full comparison, including two open-source
"-thinking" models that turned out too unreliable — frequent empty
responses — to use in production regardless of accuracy). This is a
first real improvement, not a fully re-validated number — see
`eval/README.md`'s oracle section for what's confirmed vs. still open.

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
as writer, `deepseek/deepseek-chat-v3.1` as judge, `deepseek/deepseek-
r1-0528` as fact-checker** — a deliberate quality tradeoff (78% vs. 100%
writer approval on the eval set) in exchange for that separation.

**Cost**: measured directly, not estimated — one real fact-check call
against `deepseek-r1-0528` cost $0.0065 (it cannot disable its reasoning
step; ~2,300 of ~2,400 output tokens were mandatory reasoning). Scaled to
a real 259-page book (the same one used throughout this investigation),
the full pipeline — writer + judge + fact-check — comes to roughly **$5
for the whole book**, versus roughly $0.33-$1.88 under earlier, cheaper
configurations. Still trivial in absolute terms for the quality gained,
but worth knowing precisely rather than assuming it's free.

### Oracle validation — checking the checker

`run_eval.py oracle` is not part of the live pipeline. It re-scores the
same fresh writer output with both the production (open-source) judge
and a closed frontier "oracle" model — **`anthropic/claude-sonnet-5`** by
default — and reports where they disagree, to answer "is our cheap judge
actually trustworthy" with measured evidence instead of a one-time
calibration run.

First run (9 examples): 56% agreement — turned out to be partly a lucky
small sample. Grown to 24 examples (per explicit request, specifically
adding more instances of the failure patterns the first run surfaced)
and re-run: **9/24 agreement (38%)**, a more statistically reliable
number. The most serious individual disagreement in the first run —
production judge scored a fresh rewrite `fidelity: 10` (perfect) while
the oracle rejected it at `fidelity: 6` for **fabricated specific company
names and technical details** not present anywhere in the original — but
the bigger finding from the larger sample is that the oracle scores
lower than production on almost every dimension, in almost every
example, not just on a couple of catchable patterns. That's real,
measured evidence of what the open-source-only requirement costs in
reliability. Not grounds to override the requirement, but a gap worth
monitoring by re-running `oracle` periodically, especially after any
prompt or model change — see `eval/README.md` for the full history of
what's been tried (decomposing the judge into two calls, self-
consistency, and a fact-check model swap to `deepseek-r1-0528`) and
what's still open.

## Status

Real end-to-end pipeline is live: PDF upload → per-page text extraction →
job/chunk queue in Postgres → worker runs generate→judge→retry per chunk
against OpenRouter → results stream back over SSE. See "Model evaluation"
above for how the writer/judge models were chosen and what's still an
open reliability question.

`dev` is the default/live branch; `main` only ever advances via a dev →
main promotion PR (see `.github/workflows/enforce-dev-to-main.yml`).
