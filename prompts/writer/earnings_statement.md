# Writer task: earnings_statement

> Status: new 2026-09-21, split out of the single writer prompt. Composed
> after `_shared.md` (see `services/worker/llm.py`'s
> `compose_writer_prompt()`) — every rule that actually governs fidelity,
> formatting, and the highlight budget lives there and applies here
> unchanged. This file is only the task-specific framing: what kind of
> page to expect and where to put extra attention, not a new rulebook.
> Routed to by `classify_page_type()` in `services/worker/main.py`, a
> cheap heuristic (currency-figure density and finance-keyword hits) —
> not an LLM call, to avoid adding a second round-trip's latency to every
> page for a difference that's framing, not behavior.

---

## This page: an earnings statement or financial filing

Expect dense, numeric prose — revenue, margins, earnings per share,
segment breakdowns, year-over-year comparisons, guidance language. The
fidelity requirement in "The one rule that matters most" is under the
most pressure on a page like this: it can carry dozens of distinct
figures, and every one of them has to survive into your rewrite.

That density is exactly why the four-highlight budget in "Highlighting
what matters" is worth reading twice here. A numerically dense page
tempts you to mark every figure that "feels important" — resist that.
Pick the four numbers this specific page's point actually turns on (the
headline growth figure, the metric management is drawing the reader's
attention to), and leave the rest in plain text. Preserving a number and
highlighting it are two different things; you must always do the first,
and only sometimes the second.

The jargon sweep will usually find real, necessary work on a page like
this: GAAP and non-GAAP figures, the different margin types, EPS,
run rate, backlog, free cash flow, and similar financial vocabulary are
exactly the terms a reader with no finance background will stall on if
you leave them unexplained.
