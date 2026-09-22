# Factor: fidelity (earnings_statement)

> Status: written 2026-09-21 as part of splitting eval into one folder
> per writer task. This factor is **code-computed, not judged by a
> model** — a deliberate, hard-won design choice (see eval/README.md's
> "understanding" dimension history: judge-model unreliability is
> documented there twice). Turning this into a model judgment instead of
> exact matching would be a real regression, not a reorganization, so
> this file documents the algorithm `eval/run_page_eval.py` actually
> runs rather than a prompt fed to any model.

---

## What's checked

**Number recall** (`score_numbers` in `eval/run_page_eval.py`): every
distinct figure on the source page — extracted automatically when the
page was built (`eval/build_page_set.py`'s `numbers()`) — must appear
somewhere in the rewrite, checked by direct substring match on the
normalized figure (commas stripped, `$`/`%` stripped from the match
target). Reported as a **micro-average** across the whole set (total
figures preserved / total figures on offer), not a per-page mean — an
earnings page can carry ~30 figures, so a per-page mean would let one
missed number on a low-figure page outweigh a dozen missed on a
figure-dense one.

**Fabrication** (`score_fabrication`): a per-page watchlist of real
company names *absent* from that specific page (`eval/build_page_set.py`'s
`WATCHLIST`, filtered per page by `forbidden()`). Any watchlisted name
that appears in the rewrite is invented — this targets a real,
previously-observed failure mode where a generic "major cloud providers"
becomes "Amazon, Google and Microsoft" in the output: plausible, fluent,
and false. The watchlist is checked per page specifically because one
earnings_statement page (Amazon's) genuinely names Anthropic and OpenAI
as real customers — the point is never "ban the name," it's "catch it
where the source doesn't actually mention it."

## Why earnings_statement specifically stresses this factor

A financial filing is the most figure-dense page type in the set — the
product's core promise ("same facts, same numbers, nothing dropped") is
under the most pressure here, which is why this task's writer prompt
(`prompts/writer/earnings_statement.md`) calls out fidelity discipline
explicitly even though the underlying rule lives in
`prompts/writer/_shared.md` and applies to every task identically.
