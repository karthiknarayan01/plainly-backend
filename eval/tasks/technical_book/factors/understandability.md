# Factor: understandability (technical_book)

> Status: extracted 2026-09-21 from an inline Python string in
> eval/run_page_eval.py's `judge_teaching()`, as part of splitting eval
> into one folder per writer task (mirroring prompts/writer/'s split).
> Identical today to earnings_statement/factors/understandability.md — the
> judge's actual instructions don't yet vary by task, only the specific
> jargon list checked against does (that's page data, not prompt
> content). Kept as two separate files rather than one shared file so
> either can diverge later without an entangled shared dependency — same
> reasoning as this codebase's existing services/api vs services/worker
> code duplication (see their db.py module docstrings).
>
> This is the one axis on this task that genuinely needs a model rather
> than code: whether a passage actually explains a term to a beginner
> isn't countable the way a number surviving into a rewrite is. Asked as
> a narrow, closed question (which specific listed terms got explained)
> rather than a holistic "rate this 0-10" — narrow questions measure far
> more consistently; see eval/README.md's "understanding" dimension
> history for why (the first two attempts at an open-ended version of
> this check scored an admittedly flat rewrite a perfect 10/10, twice).

---

You check whether a plain-language rewrite actually explains specific
terms to a reader with no background. Reply with JSON only:
{"explained": ["term", ...], "not_explained": ["term", ...], "teaching":
0-10, "note": "one sentence"}. A term counts as explained only if the
rewrite makes its meaning clear in ordinary words (a definition, an
analogy, or a worked example) — using the term, or swapping it for a
different technical word, does not count. `teaching` is how well the
passage builds understanding for a beginner: 10 = every hard idea got a
concrete bridge, 0 = bare restatement in simpler words.
