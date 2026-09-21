# Page-scale eval set

Real, full pages — the unit the product actually processes — with
assertions that are checked **in code**, not by asking a model for an
opinion. 25 pages: 7 real, public earnings releases and 18 real pages
from 4 technical/finance books (14 prose + 4 tables of contents).

Built by `eval/build_page_set.py`; regenerate with `python
eval/build_page_set.py` after sourcing OPENROUTER isn't needed for this
step, only the `PLAINLY_*_PDF` env vars documented there and in
`eval/sources/README.md`.

## Why this exists when `eval/examples/` already did

| | `eval/examples/` (old) | `eval/pages/` (this) |
|---|---|---|
| median input | **266 chars** | **~2,400 chars** |
| unit | hand-trimmed excerpt | real full page |
| fidelity measured by | judge model's opinion | counted figures, in code |
| fabrication measured by | judge model's opinion | name watchlist, in code |
| formatting measured by | not measured | markdown/highlight checks, in code |
| contents-page handling | not covered | a dedicated, separately-scored category |
| summarisation caught | no | yes (expansion ratio) |
| companies covered | 5 | **7** |
| book genres covered | 1 | **4** (systems, ML, ML-inference, finance education) |

A real book page is ~3,100 characters and a real earnings release
~1,000–1,700. The old set's median input was **9x smaller than
production input**, and every model decision made against it was
therefore made on a different task than the one that ships. When the
same candidates were re-tested on real pages the ranking inverted: the
model the old set ranked worst wrote the best real page, and the
deployed model at the time was quietly *compressing* pages — a failure
the old set structurally could not detect, because a 266-character
excerpt has nothing to compress.

## What's measured, across four axes

This eval set exists to answer four separate questions, not one blended
score — a model can be strong on one and weak on another, and averaging
them together would hide exactly the trade-off that matters for a
production decision.

### 1. Fidelity — did every fact survive?

`numbers_that_must_survive`: every distinct figure on the source page,
auto-extracted. Scored as a **micro-average** (total figures preserved /
total figures offered across the whole set), never a per-page mean — an
earnings page carries ~30 figures and a prose page carries 0–1, so a
per-page mean would let one missed number on a prose page outweigh 36
missed on NVIDIA's.

`entities_that_must_not_appear`: a per-page watchlist of real company
names *absent* from that specific page. If one shows up in the rewrite,
the model invented it. Targets a real, previously-observed failure mode:
a generic "major cloud providers" becoming "Amazon, Google and
Microsoft" in the output — plausible, fluent, and false. (The list
itself — Amazon, Anthropic, OpenAI, NVIDIA, and 15 others — is checked
*per page*: Amazon's own release genuinely names Anthropic and OpenAI as
real Trainium customers, so on that one page those two are correctly
absent from the forbidden list and present in the source instead.)

### 2. Understandability — did the reader actually learn something?

`jargon_that_must_be_explained`: every technical/financial/ML term on the
page from a domain-spanning list (GAAP and EBITDA; availability and SLA;
quantization and KV cache). Checked by an **independent judge model —
never the model being scored** — asked a narrow, closed question per
page ("which of these specific terms did the rewrite actually make
understandable?") rather than a holistic "rate this 0–10", because narrow
questions measure far more consistently than open-ended ones (see
`eval/README.md`'s "understanding" dimension history for why: the first
two attempts at an open-ended version of this check scored an admittedly
flat rewrite a perfect 10/10, twice).

### 3. Formatting — will this actually render correctly?

The reader renders plain prose with a small set of allowed formatting
(the writer is asked for 2–4 **bold** highlights per page and nothing
else). Checked with the *exact same patterns* the real pipeline uses —
`stripMarkdown`/`splitBold` in the frontend, `_DECLINED` in
`services/worker/main.py` — not a separate approximation of them, so a
formatting pass here means the real render would have been clean too:

- a stray `## Heading`, a `- bullet` line, or an unclosed `**` — these
  reach a plain-prose page as literal punctuation
- highlight count outside a healthy 1–6 range — zero means nothing was
  emphasised, more than 6 means everything was
- a model announcing it has nothing to say ("(No output — this page is a
  table of contents...)") instead of actually staying silent, which
  would put the model's own commentary in the middle of a real document

### 4. Contents-page handling — is the model's own "don't rewrite this" instruction being followed?

Four pages (`source_type: contents_page`, one from each book) are real
tables of contents. Correct behaviour here is **producing nothing**, per
the writer's own prompt instruction — fidelity and expansion don't mean
anything for a page that's supposed to come back empty, so these are
scored on a separate binary (did it stay silent, yes/no) and **excluded
from every other aggregate**, in both directions: they can't be
mistaken for the model failing, and they can't inflate expansion or
dodge a fidelity requirement by producing nothing.

## What's in each file

```yaml
id: p001-nvidia
source_type: earnings_statement     # | technical_book | contents_page
source: eval/sources/nvidia-q2-fy2027.md
original_page: |                    # the real page text, verbatim
  ...
numbers_that_must_survive:          # every distinct figure on the page
  - as_written: "$96.2"
    match: "96.2"
jargon_that_must_be_explained:      # terms a layperson can't be assumed to know
  - GAAP
  - gross margin
entities_that_must_not_appear:      # real names NOT on this page
  - Amazon
  - Microsoft
```

## A real bug this set found, entirely for free

Expanding to four books immediately caught a real gap in the production
contents-page detector (`services/worker/main.py`'s `looks_like_contents`)
— found and fixed at zero API cost, since it's a property of the
detector, not something that needs a model call to check. The original
detector required a leader-dot-then-page-number pattern (`Chapter 3
. . . . . 17`); the finance-education book's table of contents lists
chapters with **no page numbers at all** ("Chapter 1. - Twelve Basic
Principles / Chapter 2. - The Balance Sheet / ..."), which never matched
it once. Fixed by adding a second, independent signal — three or more
distinct "Chapter N" / "Section X" markers next to a "Table of Contents"
heading — confirmed against all 21 non-contents pages in the set to
produce zero false positives before shipping it. Detection is now 4/4 on
every table-of-contents page across all four books.

## Known limitations — read before trusting a number

- **Number matching is substring-based.** "106" matches whether the
  model writes "106%" or "106 percent", which is the intent, but it
  would also match an unrelated "106" elsewhere. Recall is therefore a
  slight over-estimate — a recall metric, not proof of correct usage. A
  model could preserve a figure and still attach it to the wrong thing.

- **Folio numbers are filtered, and that filtering matters.** A printed
  page carries its own page number in the running header ("18 CHAPTER 1
  THE IMPACT OF...", "DEFINING ROLES 23"). An earlier version of this set
  treated those as facts that must survive, so every model scored 0% on
  several prose pages for *correctly* dropping a page number — a bug in
  the eval, not the models. The builder now drops a header/footer figure
  unless the same figure also appears in the body text.

- **Single run per page, at production temperature (0.3).** Run-to-run
  variance is real and not small: one model scored 94%, then 58%, then
  69% number recall on the identical page across three separate runs.
  Treat gaps under ~10 points between models as noise, and re-run before
  acting on a small difference — see `eval/README.md`'s "Can prompt
  changes close the gap" section for a case where a single run reached
  the *opposite* conclusion from three replicates.

- **25 pages is still small** relative to a production traffic mix.
  Enough to separate a 98%-fidelity model from an 80% one; not enough to
  reliably separate 98% from 96%, or to bound formatting-defect rates
  tightly. Growing this further — more companies, more book genres, more
  contents-page formats — is the direct way to raise that ceiling, and
  it costs no API money: only the writer/judge benchmark runs do.

- **Formatting checks are pattern-based, same caveat as fidelity's
  substring matching.** A model could produce a highlight count in a
  healthy range while highlighting the wrong things, or avoid a bare `##`
  while still writing something that reads like a heading in prose form.
  These catch the mechanical failure modes actually observed in
  production, not every conceivable formatting problem.

## What the formatting axis actually caught, first time it ran

Worth stating plainly, since it's the reason this axis was added rather
than a hypothetical: the first full run against this set found the
deployed writer producing up to **27 bold highlights on a single page**
— 15 of 21 non-contents pages over even a loose 6-highlight ceiling —
against a prompt that asked for "roughly two to four." That had shipped
undetected because nothing before this measured it; every prior eval run
scored fidelity, fabrication, and teaching, none of which a page can fail
by over-highlighting. Fixed in the prompt (a hard budget, not a
suggestion) and spot-checked on the three worst offenders: 27→7, 20→2,
19→4 highlights, no cost to fidelity or fabrication on the same three
pages. Full writeup, including what wasn't fully fixed (a real ~10%
residual preamble-leak rate the eval also caught): `eval/README.md`'s
"2026-09-20" section.
