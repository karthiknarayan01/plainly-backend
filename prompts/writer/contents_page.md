# Writer task: contents_page

> Status: new 2026-09-21, split out of the single writer prompt. Composed
> after `_shared.md` (see `services/worker/llm.py`'s
> `compose_writer_prompt()`), which already contains the full instruction
> under "Structural and reference content" and the carve-out under "The
> one rule that matters most" — this file only reorders that instruction
> to the front, since when this task IS correctly selected, compliance on
> exactly this rule is the entire test. Never routed to at runtime by
> `classify_page_type()` (production only routes to `earnings_statement`
> or `technical_book` — a true contents page should already have been
> skipped before any classification, by `looks_like_contents()` in
> `services/worker/main.py`, saving the API call entirely). This task
> exists for two reasons: it's what `eval/run_page_eval.py` uses to test
> the model's own judgment directly against a known table-of-contents
> page (bypassing the production skip, on purpose, as defense-in-depth
> testing), and the shared "Structural and reference content" rule it
> points back to is what actually protects production against a false
> negative in `looks_like_contents()`.

---

## This page: a table of contents, index, or list of figures

Your only job on this page is to recognize that it is navigation, not
reading, and reply with nothing at all — see "Structural and reference
content" and "The one rule that matters most" above for the full
instruction and why: its page numbers point at the original's
pagination, which means nothing in a re-paginated rewrite, so carrying
any of it over scatters stray fragments like "Preface page 15" through
what should be prose.

Do not describe the page. Do not reformat its entries into a cleaner
list. Do not explain that you are declining, or apologize for having
nothing to say. Any of those is still content appearing where the
correct output is silence. An empty reply is the entire correct output —
nothing else needs to be true of it.
