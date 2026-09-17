# Page-scale eval set

Real, full pages — the unit the product actually processes — with
assertions that are checked **in code**, not by asking a model for an
opinion.

Built with `/tmp/build_pages.py`-style extraction (see git history of
`run_page_eval.py`); regenerate by re-running the builder if sources
change.

## Why this exists when `eval/examples/` already did

| | `eval/examples/` (old) | `eval/pages/` (this) |
|---|---|---|
| median input | **266 chars** | **2,376 chars** |
| unit | hand-trimmed excerpt | real full page |
| fidelity measured by | judge model's opinion | counted figures, in code |
| fabrication measured by | judge model's opinion | name watchlist, in code |
| summarisation caught | no | yes (expansion ratio) |

A real book page is ~3,100 characters and a real earnings release ~1,000-1,700.
The old set's median input was **9x smaller than production input**, and
every model decision made against it was therefore made on a different
task than the one that ships. When the same candidates were re-tested on
real pages the ranking inverted: the model the old set ranked worst wrote
the best real page, and the deployed model was quietly *compressing*
pages — a failure the old set structurally could not detect, because a
266-character excerpt has nothing to compress.

## What's in each file

```yaml
id: p001-nvidia
source_type: earnings_statement     # or technical_book
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

## The assertions, and why each one is the right thing to measure

- **`numbers_that_must_survive`** — the product promises "same claims,
  nothing dropped." That is countable: every figure on the source page
  must appear in the rewrite. Scored as a micro-average (total figures
  preserved / total figures offered), never a per-page mean — an earnings
  page carries ~30 figures and a prose page carries 0-1, so a per-page
  mean would let one missed number on a prose page outweigh 36 missed on
  NVIDIA.

- **`entities_that_must_not_appear`** — a watchlist of famous company
  names that are *absent* from the source page. If one shows up in the
  rewrite, the model invented it. This is the documented real failure
  mode where a generic "major cloud providers" becomes "Amazon, Google
  and Microsoft" — plausible, fluent, and false.

- **`jargon_that_must_be_explained`** — the teaching promise. Checked by
  a judge, but asked as a narrow, closed question ("which of these
  specific terms did the rewrite actually make understandable?") rather
  than a holistic score, because narrow questions are far more reliable
  than "rate this 0-10."

- **expansion ratio** (computed, not stored) — output length / input
  length. A rewrite that explains jargon and adds a worked example should
  come out **longer** than its source. A ratio below 1.0 means the page
  was summarised, which is the one thing this product must never do.
  Several models fail exactly here while looking fine to a judge.

## Known limitations — read before trusting a number

- **Prose pages contribute almost no number signal.** After filtering
  (below), four of the five book pages have zero figures. Their fidelity
  signal comes from the jargon and fabrication checks instead. Number
  recall is effectively an earnings-statement metric.

- **Folio numbers are filtered, and that filtering matters.** A printed
  page carries its own page number in the running header ("18 CHAPTER 1
  THE IMPACT OF...", "DEFINING ROLES 23"). The first version of this set
  treated those as facts that must survive, so **every model scored 0% on
  four prose pages for correctly dropping a page number** — a bug in the
  eval, not the models. The builder now drops a header/footer figure
  unless the same figure also appears in the body text.

- **Single run per page, at production temperature (0.3).** Run-to-run
  variance is real and not small: `deepseek-chat-v3.1` scored 94%, then
  58%, then 69% number recall on the same NVIDIA page across three runs.
  Treat gaps under ~10 points between models as noise, and re-run before
  acting on a small difference.

- **Number matching is substring-based.** "106" matches whether the model
  writes "106%" or "106 percent", which is the intent, but it would also
  match an unrelated "106" elsewhere. Recall is therefore a slight
  over-estimate. It is a recall metric, not proof of correct usage — a
  model could preserve a figure and still attach it to the wrong thing.

- **10 pages is small.** Enough to separate a 98% model from an 80% one;
  not enough to separate 98% from 96%.
