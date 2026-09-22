# Task: technical_book

14 real pages spanning 4 books (distributed systems, ML engineering, ML
inference, finance education — see `eval/sources/README.md` for why
each was chosen). Not committed: full pages of copyrighted books: set
the `PLAINLY_*_PDF` env vars in `eval/build_page_set.py` to local copies
to regenerate. Writer prompt: `prompts/writer/_shared.md` +
`prompts/writer/technical_book.md`. Factors measured:
`factors/fidelity.md`, `factors/understandability.md`,
`factors/formatting.md`.

Full design, known limitations, and cross-task comparison:
`eval/tasks/README.md`.
