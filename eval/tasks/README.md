# Task-scoped eval set

Real, full pages — the unit the product actually processes — with
assertions that are checked **in code**, not by asking a model for an
opinion, organized one directory per writer task rather than a flat
list. 25 pages: 7 real, public earnings releases and 18 real pages from
4 technical/finance books (14 prose + 4 tables of contents).

```
eval/tasks/
  earnings_statement/
    pages/       7 real earnings-release pages (committed — public disclosures)
    factors/     fidelity.md, understandability.md, formatting.md
    README.md
  technical_book/
    pages/       14 real book pages (not committed — copyrighted; see eval/sources/README.md)
    factors/     fidelity.md, understandability.md, formatting.md
    README.md
  contents_page/
    pages/       4 real tables of contents, one per book (not committed)
    factors/     correctness.md
    README.md
```

Built by `eval/build_page_set.py`; regenerate with `python
eval/build_page_set.py` — OPENROUTER_API_KEY isn't needed for this step,
only the `PLAINLY_*_PDF` env vars documented in `eval/sources/README.md`.

## Why one directory per task

This mirrors `prompts/writer/`'s split into one prompt file per task
(`earnings_statement.md`, `technical_book.md`, `contents_page.md`,
composed with `prompts/writer/_shared.md`) — the same three tasks the
production writer actually routes between (see
`services/worker/main.py`'s `classify_page_type()` and
`looks_like_contents()`). A page's `source_type` field, its directory,
and the writer prompt file used to rewrite it during eval are always the
same three-way match — `eval/run_page_eval.py`'s `write_page()` picks
the task straight from `page["source_type"]`.

Reorganized 2026-09-21 from a flat `eval/pages/` directory. The page
data itself, and every number ever measured against it in
`eval/README.md`, is unchanged by the move — this is a pure
reorganization, verified by re-running the harness before and after and
confirming identical scores (see `eval/README.md`'s reorg entry).

## Why this exists when `eval/examples/` already did

| | `eval/examples/` (older) | `eval/tasks/*/pages/` (this) |
|---|---|---|
| median input | **266 chars** | **~2,500 chars** |
| unit | hand-trimmed excerpt | real full page |
| fidelity measured by | judge model's opinion | counted figures, in code |
| fabrication measured by | judge model's opinion | name watchlist, in code |
| formatting measured by | not measured | markdown/highlight checks, in code |
| contents-page handling | not covered | a dedicated task, own factor |
| summarisation caught | no | yes (expansion ratio) |
| companies covered | 5 | **7** |
| book genres covered | 1 | **4** (systems, ML, ML-inference, finance education) |
| organized by | flat list | one directory per writer task |

A real book page is ~3,100 characters and a real earnings release
~1,000–1,700. The old set's median input was **9x smaller than
production input**, and every model decision made against it was
therefore made on a different task than the one that ships. When the
same candidates were re-tested on real pages the ranking inverted: the
model the old set ranked worst wrote the best real page, and the
deployed model at the time was quietly *compressing* pages — a failure
the old set structurally could not detect, because a 266-character
excerpt has nothing to compress. `eval/examples/` and `eval/run_eval.py`
are kept as-is (not reorganized into this structure) — already
documented there as superseded, reorganizing dead-ish legacy code would
add risk for no benefit.

## What's measured, and where it lives

Four factors, split across the three task directories rather than
listed once — not every factor applies to every task (contents_page has
only one: did it correctly produce nothing):

1. **Fidelity** (`factors/fidelity.md`, earnings_statement &
   technical_book) — every figure on the source page has to survive into
   the rewrite, plus a per-page watchlist of real company names that
   must never appear if they're absent from the source. Both
   **code-computed**, not judged.
2. **Understandability** (`factors/understandability.md`, same two
   tasks) — the one factor that needs a model, since whether a passage
   actually teaches a beginner isn't countable. Asked as a narrow,
   closed question per page (which specific listed terms got explained)
   by an **independent judge model, never the model being scored**.
3. **Formatting** (`factors/formatting.md`, same two tasks) — checked
   with the exact patterns the real pipeline uses to render a page
   (the frontend's markdown-stripping, the worker's leaked-preamble
   filter), so a pass here means the real render would be clean too.
   **Code-computed.**
4. **Correctness** (`factors/correctness.md`, contents_page only) — did
   the writer produce nothing (or a short, honest decline), per its own
   prompt instruction. **Code-computed**, binary, and excluded from
   every other task's aggregates in both directions.

Every factor file documents either the exact algorithm code runs
(fidelity, formatting, correctness) or the literal judge prompt text
used (understandability) — the same "prompts belong in files, not
buried inline in a script" principle already applied to
`prompts/writer/`, closing a real gap: before this reorg,
`eval/run_page_eval.py`'s two judge questions were inline Python
strings, not files.

## A real bug this set found, entirely for free

Expanding to four books immediately caught a real gap in the production
contents-page detector (`services/worker/main.py`'s `looks_like_contents`)
— found and fixed at zero API cost, since it's a property of the
detector, not something that needs a model call to check. The original
detector required a leader-dot-then-page-number pattern (`Chapter 3
. . . . . 17`); the finance-education book's table of contents lists
chapters with **no page numbers at all** ("Chapter 1. - Twelve Basic
Principles / Chapter 2. - The Balance Sheet / ..."), which never matched
it once. Fixed by adding a second, independent signal — three or more
distinct "Chapter N" / "Section X" markers next to a "Table of Contents"
heading — confirmed against every non-contents page in the set to
produce zero false positives before shipping it. Detection is now 4/4 on
every table-of-contents page across all four books.

## Known limitations — read before trusting a number

- **Number matching is substring-based.** "106" matches whether the
  model writes "106%" or "106 percent", which is the intent, but it
  would also match an unrelated "106" elsewhere. Recall is therefore a
  slight over-estimate — a recall metric, not proof of correct usage. A
  model could preserve a figure and still attach it to the wrong thing.

- **Folio numbers are filtered, and that filtering matters.** A printed
  page carries its own page number in the running header ("18 CHAPTER 1
  THE IMPACT OF...", "DEFINING ROLES 23"). An earlier version of this set
  treated those as facts that must survive, so every model scored 0% on
  several prose pages for *correctly* dropping a page number — a bug in
  the eval, not the models. The builder now drops a header/footer figure
  unless the same figure also appears in the body text.

- **Single run per page, at production temperature (0.3).** Run-to-run
  variance is real and not small: one model scored 94%, then 58%, then
  69% number recall on the identical page across three separate runs.
  Treat gaps under ~10 points between models as noise, and re-run before
  acting on a small difference — see `eval/README.md`'s "Can prompt
  changes close the gap" section for a case where a single run reached
  the *opposite* conclusion from three replicates.

- **The heuristic that routes a page to earnings_statement vs.
  technical_book in production (`classify_page_type()`) is not what
  eval uses.** Eval always uses a page's true, hand-verified
  `source_type` to pick the writer prompt, testing each task prompt in
  isolation. The heuristic's own accuracy, measured directly against
  this set's known labels, is 19/21 (90%) — both misses were a
  finance-education book page classified as earnings_statement instead
  of technical_book, an understandable confusion since that book is
  genuinely finance-dense prose. This is a bounded risk by design: the
  rules that actually govern fidelity/fabrication/formatting live in
  `prompts/writer/_shared.md` and apply identically regardless of which
  of the two task files gets composed with it, so a misroute shifts
  framing, not the substance of what's enforced.

- **25 pages is still small** relative to a production traffic mix.
  Enough to separate a 98%-fidelity model from an 80% one; not enough to
  reliably separate 98% from 96%, or to bound formatting-defect rates
  tightly. Growing this further — more companies, more book genres, more
  contents-page formats — is the direct way to raise that ceiling, and
  it costs no API money: only the writer/judge benchmark runs do.

## What the formatting factor actually caught, first time it ran

Worth stating plainly, since it's the reason this factor was added
rather than a hypothetical: the first full run against this set found
the deployed writer producing up to **27 bold highlights on a single
page** — 15 of 21 non-contents pages over even a loose 6-highlight
ceiling — against a prompt that asked for "roughly two to four." That
had shipped undetected because nothing before this measured it; every
prior eval run scored fidelity, fabrication, and teaching, none of which
a page can fail by over-highlighting. Fixed in the prompt (a hard
budget, not a suggestion) and spot-checked on the three worst
offenders: 27→7, 20→2, 19→4 highlights, no cost to fidelity or
fabrication on the same three pages. Full writeup, including what
wasn't fully fixed (a real ~10% residual preamble-leak rate the eval
also caught): `eval/README.md`'s "2026-09-20" section.
