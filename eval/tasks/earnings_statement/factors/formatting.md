# Factor: formatting (earnings_statement)

> Status: written 2026-09-21 as part of splitting eval into one folder
> per writer task. **Code-computed, not judged by a model** — checked
> with the same patterns the real pipeline uses to render a page (the
> frontend's markdown-stripping, the worker's leaked-preamble filter),
> so a pass here means the real render would have been clean too. This
> file documents the algorithm `eval/run_page_eval.py`'s
> `score_formatting()` actually runs.

---

## What's checked

The reader renders plain prose with one allowed piece of formatting:
**bold** highlights, budgeted at four per page by
`prompts/writer/_shared.md`'s "Highlighting what matters". Checked here:

- **Stray markdown**: a `## Heading` line, a `- bullet`/`* bullet` line,
  or an unclosed `**` pair — any of these reaches the reader as literal
  punctuation, since the renderer expects plain prose and nothing else.
- **Highlight count out of range**: fewer than 1 or more than 5 bold
  spans on a page (one highlight of slack past the prompt's stated
  4-highlight budget, so a minor, plausible overshoot at 5 isn't flagged
  identically to a severe one at 27). Zero means nothing was emphasised;
  too many means everything was, which defeats the same purpose.
- **Declined-but-verbose**: a model announcing it has nothing to say
  ("(No output — this page is a table of contents...)") instead of
  actually staying silent — only relevant on a misrouted page, since a
  correctly classified earnings_statement page should never decline.

## Why this matters here specifically

Earnings pages are the most numerically dense pages in the set, which is
exactly the condition that produced the real defect this axis was built
to catch: the first full run against the expanded set found up to 27
bold spans on one page, driven by the temptation to mark every figure
that "feels important" on a page already full of them. See
`eval/README.md`'s 2026-09-20 section for the full incident and fix.
