# Plainly

Plainly turns hard-to-read documents — earnings statements, financial
filings, technical books — into plain language a reader with no
background in the subject can actually follow, while staying faithful to
the original: same facts, same numbers, same claims, nothing invented and
nothing dropped. Diagrams, charts and tables are preserved in place
rather than lost in translation.

**Why:** corporate earnings statements in particular are often written
(and legally reviewed) in a way that obscures more than it informs —
dense jargon, passive voice, buried caveats. The product bet is that
fidelity, not just simplicity, is what actually matters: a rewrite that
drops a caveat or invents a reason is worse than useless, because it
looks trustworthy while being wrong. Everything in this repo — the
prompt design, the evaluation framework, the model choices — is built
around measuring and defending that fidelity, not just producing
easier-to-read prose.

## How it works

1. A PDF is opened client-side (`pdf.js`); its text is extracted
   page-by-page in the browser and sent to the backend. The original
   file itself never leaves the browser.
2. The backend queues one chunk per page and a pool of workers rewrites
   each one, in parallel, against an LLM.
3. Pages advertising a figure or table ("Figure 3.1", "Table 2") are
   detected from their own text and reproduced as the original page
   image alongside the rewrite, rather than dropped — a rewrite is text,
   so anything visual would otherwise be silently lost.
4. The reader shows the whole rewritten document once every page is
   done, laid out to match the shape of the source PDF, so switching
   between the original and the rewrite doesn't feel like switching
   between two different apps.

## Architecture

| Component | Tech | What it's for |
|---|---|---|
| Frontend | Next.js (App Router), React, TypeScript | The reader UI, PDF rendering, and thin API routes that proxy to the backend without exposing its URL to the browser |
| PDF handling | `pdf.js` | Client-side text extraction and page rendering — no PDF ever touches a server |
| API | FastAPI (Python) | Job creation, progress polling, and the finished-document endpoint; talks only to Postgres, never to the LLM |
| Worker | Python, threaded pool | Claims queued pages (`SELECT ... FOR UPDATE SKIP LOCKED`, so concurrent workers never double-process a page) and makes the rewrite call for each |
| Model access | OpenRouter | Single API surface across every model provider, open- and closed-weight, so the writer model is a one-line config change rather than a vendor integration |
| Database | Postgres (Cloud SQL) | The job/page queue and results store — the only shared state between the API and the workers |
| Hosting | Google Cloud Run (API + worker), Vercel (frontend) | Both backend services scale on request volume; the worker's concurrency is what turns a 200-page document into a job that finishes in minutes instead of hours |
| Infra as code | Terraform | Cloud SQL, Cloud Run services, IAM, and secrets are all declared, not click-configured |

The writer is a single LLM call per page, deliberately: earlier designs
also ran a judge and a fact-check call per page with a retry loop,
turning one page into up to nine round-trips. Removed in favor of doing
the quality work up front, in the prompt and in evaluation, rather than
at request time — the reasoning and the real cost/latency numbers behind
that trade are in `services/worker/llm.py`.

## Evaluation

Rewriting at scale is a fidelity problem before it's a fluency problem —
a rewrite that reads beautifully but drops a caveat or invents a company
name is worse than a clunky one that keeps every fact. Prompting alone
can't guarantee that, so nothing here is taken on faith: model and prompt
decisions are backed by a benchmark, and the benchmark's own metrics are
checked for bugs the same way the models are.

### Why a purpose-built eval set

Off-the-shelf reading-comprehension benchmarks don't measure "faithful,
jargon-free rewriting of a real document," so the eval set is hand-built
from real material, not synthetic text: **25 real pages — 7 companies'
public earnings releases and 18 pages spanning 4 books** (distributed
systems, ML engineering, ML inference, and a finance-education text
chosen specifically because teaching financial concepts to a beginner is
*exactly* the product's own job, making it the closest genre match to
what a rewrite is supposed to do). An early version used hand-trimmed
excerpts; re-measuring showed their median length was 266 characters
against a real page's ~1,000–3,400, a gap wide enough that it changed
which model looked best once real page-scale input was tested. The
current set is real, full pages: median ~2,500 characters, matching what
the pipeline actually processes.

### What gets measured, and how

Four separate questions, not one blended score — a model (or a prompt
change) can be strong on one and weak on another, and averaging them
together would hide exactly the trade-off that matters for a production
decision. Most of the score is computed **in code**, not asked of a
model — a judge saying "9/10" isn't evidence a reader got every number:

- **Fidelity** — every distinct figure on the source page has to appear
  in the rewrite, checked by direct extraction and matching, not opinion.
  Reported as a micro-average across all figures in the set (an earnings
  page carries ~30 figures, a prose page 0–1; averaging per-page instead
  would let one missed figure on a prose page outweigh 36 missed on an
  earnings page) — plus a per-page watchlist of real company names
  *absent* from that specific page, so inventing one is caught the same
  way a dropped figure is.
- **Understandability** — judged, since it can't be counted, but by an
  **independent model that is never the model being scored**, asked a
  narrow, closed question per page ("which of these specific terms did
  the rewrite actually explain?") rather than a holistic "rate this
  0–10" — narrow questions measure far more consistently than
  open-ended ones.
- **Formatting** — checked with the *exact same patterns the real
  pipeline uses* to render a page (the frontend's markdown-stripping
  logic, the worker's leaked-preamble filter), not a separate
  approximation of them: stray headings/bullets/unclosed bold, and
  highlight count against the prompt's own stated budget. This axis
  caught a real, previously invisible defect — see "Three bugs the eval
  process itself caught" below.
- **Structural-content handling** — dedicated table-of-contents pages
  (one from each of the 4 books), scored on whether the writer correctly
  produces *nothing* for them, per its own prompt instruction — a
  rewrite has no argument to restate on a page that's pure navigation,
  and carrying its page numbers into a re-paginated document only
  scatters meaningless fragments through the prose.
- **Expansion ratio** (feeds the fidelity read) — output length over
  input length. A rewrite that explains jargon and adds a worked example
  should come out *longer* than its source; a ratio below 1.0 means the
  page was quietly summarized, which this product is not supposed to do.

### Results

Eight writer candidates, benchmarked head-to-head on the same real pages,
same scoring, evaluated by an independent judge model (this comparison
predates the set's later expansion to 25 pages / 7 companies / 4 books
described above; re-running it against the larger set is on the list in
`eval/README.md`, not yet done):

| Model | Open-weight | Fidelity (figures preserved) | Fabricated entities | Understandability |
|---|---|---|---|---|
| **`deepseek/deepseek-v4.1-flash`** | **Yes** | **100.0%** | **0** | 7.2 / 10 |
| `anthropic/claude-sonnet-5` (closed reference) | No | 100.0% | 3 | 8.5 / 10 |
| `deepseek/deepseek-v4-pro-0813` | Yes | 98.6% | 0 | 4.2 / 10 |
| `minimax/minimax-m3` | Yes | 93.7% | 3 | 6.2 / 10 |
| `qwen/qwen3-235b-a22b-2507` | Yes | 85.2% | 0 | 7.1 / 10 |
| `deepseek/deepseek-chat-v3.1` | Yes | 85.2% | 0 | 6.7 / 10 |
| `meta-llama/llama-4-maverick` | Yes | 80.3% | 0 | 4.5 / 10 |
| `z-ai/glm-5.3-flash` | Yes | — | — | empty responses on every page — not viable |

**Headline result: `deepseek-v4.1-flash` matched the closed frontier
reference (Claude Sonnet 5) exactly on fidelity (100.0%) and beat it on
fabrication (0 vs. 3 invented entities)** — the closed model inserted
three real company names into one page that its source never mentioned;
the open model invented nothing anywhere in the set. On understandability
the closed reference still leads (8.5 vs. 7.2); targeted prompt work
closed roughly half to two-thirds of that specific gap without touching
fidelity (see `eval/README.md`'s "Can prompt changes close the gap"
section for the A/B methodology — three runs per arm, since a single run
on this metric was later shown to be actively misleading).

At current OpenRouter pricing (`$0.60` vs. `$10.00` per million output
tokens), `deepseek-v4.1-flash` costs **~17x less per output token** than
Claude Sonnet 5 — material at this task's scale, since a faithful rewrite
runs *longer* than its source by design, and a full book is hundreds of
pages. Worth re-checking before quoting elsewhere: OpenRouter pricing
moves, and this is a live lookup, not a fixed number.

### Three bugs the eval process itself caught

Worth stating plainly, since it's part of what makes the numbers above
trustworthy: three real bugs were found and fixed as a direct result of
running this evaluation, and only one of the three was in a model under
test — the other two were in the harness and the product itself. An
early version mis-averaged fidelity per-page rather than per-figure
(letting a one-figure prose page outweigh a 36-figure earnings page), and
separately scored a book's own running-header page number as a fact
every model was required to preserve, penalizing every candidate for
correctly dropping it. Expanding the set to a fourth book genre then
caught a real gap in the *production* contents-page detector
(`services/worker/main.py`): it required a leader-dot-then-page-number
pattern ("Chapter 3 . . . . . 17"), and a finance-education book's table
of contents lists chapters with no page numbers at all — never matched
it once. Fixed with a second, independent detection signal, confirmed
against every non-contents page in the set to produce zero false
positives before shipping. All three are documented, with the
before/after numbers, in `eval/tasks/README.md` and `eval/README.md`.

### Running it

```bash
pip install -r eval/requirements.txt
echo 'OPENROUTER_API_KEY=sk-or-...' > .env
set -a; source .env; set +a

python eval/run_page_eval.py --models deepseek/deepseek-v4.1-flash,qwen/qwen3-235b-a22b-2507
```

Full methodology, every candidate tried, and what was ruled out and why:
`eval/README.md` and `eval/tasks/README.md`.

## Repo structure

```
services/api/     FastAPI — job creation, progress, and the finished-document endpoint
services/worker/  Claims queued pages and makes the rewrite call for each
prompts/writer/   One prompt file per page task, composed with a shared base — see below
eval/tasks/       The evaluation framework described above — one folder per task, pages + factors
infra/terraform/  Cloud SQL, Cloud Run, IAM/secrets, and latency metrics, as code
infra/sql/        Postgres schema
```

### One prompt per task, not one prompt for everything

The writer prompt is split by what kind of page it's rewriting —
`prompts/writer/earnings_statement.md`, `technical_book.md`,
`contents_page.md` — each composed at call time with
`prompts/writer/_shared.md`, which carries every rule that applies
regardless of task (fidelity, the highlight budget, output format).
Production picks the task with a cheap, in-code heuristic
(`services/worker/main.py`'s `classify_page_type()`), not a second model
call, so the page count stays at exactly one OpenRouter round-trip —
the same constraint that removed the judge/retry loop in the first
place. `eval/tasks/` mirrors the same three-way split: one directory per
task, each with its own real pages and its own factor files (what gets
measured for that task, and how — code-computed where possible, a
narrow judge question only where it has to be).

### Tracing one request through the pipeline

Every job gets one ID (`job_id`, generated when the API creates it) that
threads through every log line the pipeline produces for that job — page
classification, the writer call, the save to Postgres — as structured
JSON on stdout, which Cloud Run ingests as Cloud Logging entries
automatically. Filtering logs on that one ID reconstructs a job's full
processing sequence end to end.

Latency here means time-to-first-token, not total completion time — and
TTFT for a page is measured as the sum of its actual components, not
one opaque number: how long a page queued before a worker claimed it,
how long the in-code page-type classifier took, how long the rate
limiter made the call wait, and how long the model itself took to
produce a first token. Each is its own Cloud Monitoring metric
(`infra/terraform/metrics.tf`), so a slow page can be attributed to a
specific cause — useful for optimization later, not just a dashboard
number. See `eval/observability/README.md` for the full breakdown.

`dev` is the default/live branch; `main` only advances via a dev → main
promotion PR.
