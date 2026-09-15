# Eval set

This folder holds paired examples used for two things:

1. **DPO training data** for the writing model (chosen = good rewrite,
   rejected = bad rewrite).
2. **A held-out benchmark** — since no public benchmark measures
   "faithful, jargon-free rewriting of financial/technical text," this
   set is the actual yardstick for comparing model or prompt changes.

## What's here now

One illustrative example (`examples/000-illustrative.yaml`), clearly
marked as synthetic — written to show the schema working, not as real
training data. It should not be used for training or scoring.

## What's needed to make this real

Real source excerpts from actual earnings statements and technical books
— ideally ones you have clear rights to use (public SEC filings are
public record and a good source for the earnings-statement side).

For each real excerpt, we need:
- The original passage.
- A **good** rewrite (what we'd want the writing model to produce).
- A **bad** rewrite (a plausible but flawed one — e.g. drops a number,
  keeps jargon, over-simplifies to the point of losing meaning).
- One or two sentences on *why* each is good or bad — this is the part
  that actually teaches the model, not just the text pairs themselves.

## Schema

See `examples/000-illustrative.yaml` for the exact shape. One file per
example, sequentially numbered.

## Split

Once there are enough real examples (rule of thumb: aim for at least
~30–50 to start), split into `train/` (used for DPO) and `test/` (held
out, never trained on, used only to score changes) — not created yet
since there's only the one illustrative file so far.
