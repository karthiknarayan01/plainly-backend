# Factor: formatting (technical_book)

> Status: written 2026-09-21 as part of splitting eval into one folder
> per writer task. **Code-computed, not judged by a model** — identical
> algorithm to earnings_statement/factors/formatting.md
> (`eval/run_page_eval.py`'s `score_formatting()` doesn't branch on
> source_type); this file documents what it means for *this* task.

---

## What's checked

- **Stray markdown**: a `## Heading` line, a `- bullet`/`* bullet` line,
  or an unclosed `**` pair. A technical book's own source pages are far
  more likely than an earnings release to already contain headings,
  numbered lists, and code-like formatting — which raises the real risk
  this check exists for: the writer echoing the source's own structure
  into a rewrite that's supposed to render as plain prose.
- **Highlight count out of range**: fewer than 1 or more than 5 bold
  spans on a page. Technical_book pages generally run lower on this
  metric than earnings pages (fewer standalone figures worth marking),
  which is expected — the budget in `prompts/writer/_shared.md` is a
  ceiling for every task, not a target every page has to hit.
- **Declined-but-verbose**: relevant on this task only if a page is
  misrouted (i.e. an actual contents page that `classify_page_type()`
  called technical_book) — a correctly-typed technical_book page should
  never decline outright.
