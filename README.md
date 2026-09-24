# Plainly

Reads a dense PDF and rewrites it in plain English, without dropping or
inventing a single fact.

Financial filings and technical books are hard to read for a reason:
jargon, passive voice, caveats buried three clauses deep. Plainly
rewrites them for someone with no background in the subject. The hard
part isn't making text simpler. It's making it simpler while keeping
every number, claim and caveat exactly as it was, because a rewrite that
quietly drops a caveat looks trustworthy while being wrong.

## See it

Real output from the deployed pipeline, rewriting NVIDIA's Q2 FY2027
press release.

**Source:**

> Revenue reached $96.2 billion, up 106% year-over-year and 18%
> sequentially.

**Rewrite:**

> **Revenue** — the total money coming in from sales, before any costs
> are subtracted — hit **$96.2 billion** for the quarter. That's up 106%
> compared to the same quarter a year earlier, meaning the company
> brought in a little more than double what it did back then. Compared
> to the previous quarter (Q1), which brought in $81.6 billion, revenue
> grew another 18%. So the company isn't just bigger than it was a year
> ago — it's still growing fast quarter to quarter, too.

Every figure survives. Terms get explained where they appear, not in a
glossary three pages away. The rewrite runs longer than the source,
which is the point.

## What came out of it

An open-weight model matched the closed frontier one, for 17x less.
Benchmarked head to head on real pages, `deepseek-v4.1-flash` tied
Claude Sonnet 5 on fidelity (100% of figures preserved) and beat it on
fabrication: the closed model invented three company names on a page
that never mentioned them, the open one invented nothing anywhere. At
list pricing that's ~17x less per output token, which matters when a
faithful rewrite is longer than its source and a book runs to hundreds
of pages.

## How it works

1. The PDF is opened in the browser (`pdf.js`) and its text extracted
   page by page. The file itself never leaves the device.
2. The backend queues one job per page. A pool of workers rewrites them
   in parallel, one model call each.
3. Pages that advertise a figure or table are detected from their own
   text and reproduced as the original page image next to the rewrite,
   so nothing visual gets lost in a text-only pipeline.
4. The reader shows the finished document laid out like the source PDF,
   so switching between the two doesn't feel like switching apps.

One model call per page, deliberately. An earlier design added a judge
and a fact-check call with a retry loop, turning one page into as many
as nine round trips. That work moved up front into the prompt and the
eval suite instead. Reasoning and cost numbers are in
`services/worker/llm.py`.

## Evaluation

Prompting alone can't guarantee fidelity, so nothing here is taken on
faith. Model and prompt changes are backed by a benchmark, and the
benchmark gets debugged as carefully as the thing it measures.

### The eval set

Off-the-shelf benchmarks don't measure "faithful, jargon-free rewriting
of a real document", so this one is hand-built from real material: 25
full pages, 7 companies' public earnings releases and 18 pages across 4
books (distributed systems, ML engineering, ML inference, and a
finance-education text whose whole job is explaining financial concepts
to beginners, which is the closest match to what this product does).

An earlier version used hand-trimmed excerpts. Their median length was
266 characters against a real page's 1,000–3,400, and that gap changed
which model looked best. When the same candidates were re-tested on full
pages the ranking inverted: the model ranked worst wrote the best real
page, and the deployed model turned out to be quietly compressing pages,
which a 266-character excerpt structurally cannot reveal.

### What's scored

Four separate questions, never blended into one number, because a model
can be strong on one and weak on another and the average hides the
trade-off. Most of it is computed in code. A judge saying "9/10" is not
evidence that a reader got every figure.

- **Fidelity.** Every distinct figure on the source page has to appear
  in the rewrite, matched directly rather than judged. Micro-averaged
  across all figures, since an earnings page carries ~30 and a prose
  page carries one. Plus a per-page watchlist of real company names
  absent from that page, which catches "major cloud providers" turning
  into "Amazon, Google and Microsoft".
- **Understandability.** The one dimension that needs a model, so it
  gets asked a narrow closed question ("which of these specific terms
  did the rewrite explain?") by a judge that is never the model being
  scored. Narrow questions measure far more consistently than "rate this
  0–10", which twice gave a flat rewrite a perfect score.
- **Formatting.** Checked with the same patterns the real renderer uses,
  not an approximation of them: stray headings, bullets, unclosed bold,
  and highlight count against the prompt's stated budget.
- **Structural content.** Tables of contents are scored on producing
  nothing at all, since their page numbers point into a document that no
  longer exists once it's been rewritten.

### Model comparison

Eight writer candidates, same pages, same scoring, independent judge.
(Predates the set's expansion to 25 pages; re-running it on the larger
set is still on the list.)

| Model | Open-weight | Figures preserved | Fabricated entities | Understandability |
|---|---|---|---|---|
| **`deepseek/deepseek-v4.1-flash`** | **Yes** | **100.0%** | **0** | 7.2 / 10 |
| `anthropic/claude-sonnet-5` (closed reference) | No | 100.0% | 3 | 8.5 / 10 |
| `deepseek/deepseek-v4-pro-0813` | Yes | 98.6% | 0 | 4.2 / 10 |
| `minimax/minimax-m3` | Yes | 93.7% | 3 | 6.2 / 10 |
| `qwen/qwen3-235b-a22b-2507` | Yes | 85.2% | 0 | 7.1 / 10 |
| `deepseek/deepseek-chat-v3.1` | Yes | 85.2% | 0 | 6.7 / 10 |
| `meta-llama/llama-4-maverick` | Yes | 80.3% | 0 | 4.5 / 10 |
| `z-ai/glm-5.3-flash` | Yes | — | — | empty responses on every page |

The closed reference still leads on understandability, and prompt work
narrowed it rather than closing it: teaching went **6.5 → 7.5** against
the reference's 8.5, A/B'd three runs per arm because a single run on
that metric was shown to be actively misleading. Fidelity was unchanged.

Those are different runs from the table above, which is one benchmark
scoring all eight models identically — worth keeping internally
comparable rather than patching one row with a number the others were
never measured against. On the current 25-page set the same model scores
7.1. Full A/B, including what refused to move: `eval/README.md`.

### Three bugs the eval caught

Worth naming, since they're why the numbers above are trustworthy. An
early version mis-averaged fidelity per page instead of per figure,
letting one missed number on a prose page outweigh 36 missed on an
earnings page. It also scored a book's own running-header page number as
a fact every model had to preserve, penalising every candidate for
correctly dropping it.

The third was in production code. The contents-page detector required a
leader-dot-then-page-number pattern (`Chapter 3 . . . . . 17`), so a
finance book whose contents page lists chapters with no page numbers at
all sailed straight through to the writer. Fixed with a second detection
signal and confirmed against every non-contents page in the set before
shipping.

### Running it

```bash
pip install -r eval/requirements.txt
echo 'OPENROUTER_API_KEY=sk-or-...' > .env
set -a; source .env; set +a

python eval/run_page_eval.py --models deepseek/deepseek-v4.1-flash
```

Full methodology and every candidate ruled out: `eval/README.md` and
`eval/tasks/README.md`.

## Architecture

| Component | Tech | Role |
|---|---|---|
| Frontend | Next.js, React, TypeScript | Reader UI, PDF rendering, API routes that proxy to the backend without exposing its URL |
| PDF handling | `pdf.js` | Client-side text extraction. No PDF touches a server |
| API | FastAPI | Job creation, progress, finished document. Talks only to Postgres, never to a model |
| Worker | Python, threaded pool | Claims pages with `SELECT ... FOR UPDATE SKIP LOCKED`, so concurrent workers never double-process one |
| Model access | OpenRouter | One API across every provider, open and closed weight, so the writer model is a config change rather than an integration |
| Database | Postgres (Cloud SQL) | Job queue and results. The only shared state between API and workers |
| Hosting | Cloud Run, Vercel | Worker concurrency is what turns a 200-page document into minutes instead of hours |
| Infra | Terraform | Cloud SQL, Cloud Run, IAM, secrets and latency metrics, all declared |

### One prompt per page type

The writer prompt is split by what it's rewriting:
`prompts/writer/earnings_statement.md`, `technical_book.md`,
`contents_page.md`, each composed at call time with
`prompts/writer/_shared.md`, which holds the rules that apply
everywhere. A cheap in-code heuristic picks the task, not a second model
call, so it stays at one round trip per page. `eval/tasks/` mirrors the
same split, one directory per task with its own pages and its own
scoring factors.

Splitting it was measured, not assumed: same 25 pages through the old
single prompt and the new split one, identical on fidelity, fabrication
and leaks, better on highlight discipline, jargon coverage and
contents-page handling.

### Tracing a request

Every job gets one ID, threaded through every log line it produces:
classification, the model call, the database write. Structured JSON on
stdout, which Cloud Run picks up as Cloud Logging entries. Filter on
that one ID and you get the whole sequence in order, not just the
result. The same lines carry per-stage timings, which feed Cloud
Monitoring metrics declared in `infra/terraform/metrics.tf`.

## Repo structure

```
services/api/     FastAPI: job creation, progress, finished document
services/worker/  Claims queued pages, makes the rewrite call
prompts/writer/   One prompt per page type, plus the shared base
eval/tasks/       One folder per task: real pages and scoring factors
eval/observability/  Latency instrumentation and offline analysis
infra/terraform/  Cloud SQL, Cloud Run, IAM, secrets, metrics
infra/sql/        Postgres schema
```

`dev` is the live branch. `main` only advances through a promotion PR.
