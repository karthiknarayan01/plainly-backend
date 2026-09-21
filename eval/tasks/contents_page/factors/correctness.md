# Factor: correctness (contents_page)

> Status: written 2026-09-21 as part of splitting eval into one folder
> per writer task. **Code-computed, not judged by a model** — a table of
> contents has no argument to restate and no figures to preserve, so
> none of the other tasks' factors (fidelity, understandability,
> formatting) mean anything here; this is the one and only thing that
> matters for this task, and it's a fact you can check directly against
> the output rather than something that needs a judgment call.

---

## What's checked

A contents_page's correct output is **nothing at all** — the writer's
own prompt instruction (`prompts/writer/_shared.md`'s "Structural and
reference content" section, reinforced by
`prompts/writer/contents_page.md`). `eval/run_page_eval.py`'s
`evaluate()` scores this with a single binary per page: `contents_correct`
is true if the rewrite is empty OR reads as a short decline
(`is_declined()` — a reply under 400 characters that announces having
nothing to say, e.g. "this page is a table of contents", rather than
actually saying nothing). Both count as correct: an empty reply is the
ideal, but a short, honest decline is far better than the alternative —
reformatting or echoing the contents back, which was the real failure
mode this factor was built to catch (see `eval/README.md`'s 2026-09-20
section).

**Excluded from every other aggregate in both directions** — a
contents_page page never contributes to or drags down the
fidelity/expansion/formatting numbers computed for the other two tasks,
since none of those mean anything for a page that's supposed to come
back empty.

## Why this task exists in production at all

`services/worker/main.py`'s `looks_like_contents()` already skips the
LLM call entirely for most real contents pages, before this task's
prompt is ever used in production — this task exists so
`eval/run_page_eval.py` can test the model's *own* judgment directly
against a known contents page, deliberately bypassing that production
skip, as defense-in-depth testing for the cases the heuristic gate
misses. See `prompts/writer/contents_page.md`'s own header for the full
reasoning.
