# Factor: fidelity (technical_book)

> Status: written 2026-09-21 as part of splitting eval into one folder
> per writer task. This factor is **code-computed, not judged by a
> model** — the same deliberate design choice as
> earnings_statement/factors/fidelity.md, for the same reason (see
> eval/README.md's "understanding" dimension history for the documented
> judge-model unreliability that motivated it). The algorithm is
> identical across tasks — `eval/run_page_eval.py`'s `score_numbers()`
> and `score_fabrication()` don't branch on source_type — this file
> exists to document what it means for *this* task's pages specifically.

---

## What's checked

**Number recall**: every distinct figure on the source page — a
worked-example count, a size, a version number, whatever the page
happens to state — must appear somewhere in the rewrite, matched by
normalized substring. Reported as a micro-average across the whole set.
Several technical_book pages carry zero or one figure at all (a
concept-explanation page has nothing numeric to preserve), so
`numbers_that_must_survive` is often short or empty here — `number_recall`
shows as `n/a` for a page with no figures on offer, which is correct,
not a gap.

**Fabrication**: the same per-page real-company watchlist as every other
task, checked even though a technical book is far less likely to
reference a real company by name than an earnings release — a model
reaching for a real, unmentioned company as an "illustrative example"
(a documented risk in general-purpose writing, not unique to finance)
would still be caught here.

## Why fidelity means something different here

A technical_book page's real fidelity risk usually isn't a dropped
number — the current run has caught zero fabrications and near-100%
recall on almost every technical_book page. It's a dropped or garbled
*concept*, which number-matching can't detect at all; that's what the
understandability factor in this same directory is for.
