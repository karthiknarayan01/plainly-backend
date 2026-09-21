# Writer task: technical_book

> Status: new 2026-09-21, split out of the single writer prompt. Composed
> after `_shared.md` (see `services/worker/llm.py`'s
> `compose_writer_prompt()`) — every rule that actually governs fidelity,
> formatting, and the highlight budget lives there and applies here
> unchanged. This file is only the task-specific framing: what kind of
> page to expect and where to put extra attention, not a new rulebook.
> Routed to by `classify_page_type()` in `services/worker/main.py` — the
> heuristic's default when a page doesn't read as financial.

---

## This page: a technical or reference book

Expect narrative, concept-building prose rather than a wall of figures —
an idea being explained, a mechanism being described, a concept building
on the one before it. Numbers here are usually illustrative (a worked
example, a size, a count) rather than the whole point of the page the
way they are on a financial statement.

The teaching mechanism in "How to actually do this" and the analogy rule
under Style are doing more of the real work on a page like this than the
jargon sweep alone. A technical term almost always needs an actual
bridge — a comparison to something ordinary, a concrete worked example —
not just a plain-word substitute for its name. Swapping "quantization"
for a simpler-sounding synonym without explaining what it actually does
is translation, not teaching; the "Role" section above exists precisely
for pages like this one.
