# Task: contents_page

4 real tables of contents, one per book in the `technical_book` task
(same source PDFs, one different page index each — see
`eval/build_page_set.py`'s `BOOKS` list). Not committed, same reason as
`technical_book/`'s pages. Writer prompt: `prompts/writer/_shared.md` +
`prompts/writer/contents_page.md`. One factor measured:
`factors/correctness.md` — did the writer correctly produce nothing.

Never routed to at runtime by production (`services/worker/main.py`'s
`looks_like_contents()` already skips the LLM call for a detected
contents page before any task routing happens) — this task exists so
`eval/run_page_eval.py` can test the model's own judgment directly
against a known contents page, as defense-in-depth for the cases the
production heuristic misses.

Full design, known limitations, and cross-task comparison:
`eval/tasks/README.md`.
