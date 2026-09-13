# Plainly Backend

The document-processing and rewrite service backing the
[Plainly app](https://github.com/karthiknarayan01/plainly-app).

Given a PDF, this service produces a plain-language, novel-like rewrite
that stays semantically faithful to the original — same ideas, same
claims, same structure, nothing added or dropped — while preserving
screenshots, charts, and images from the source in context.

## Why

Technical and financial documents are often needlessly hard to read.
Corporate earnings statements in particular are frequently written (and
legally reviewed) in a way that obscures rather than clarifies — dense
jargon, passive voice, buried caveats. The goal is to give ordinary readers
a way to actually understand what a company's earnings release or 10-Q is
saying, without losing or distorting any of the substance.

Primary focus areas:
1. Earnings statements / financial filings — highest priority, since this
   is where the readability gap is most deliberate and the stakes for
   laypeople are highest.
2. Technical books and manuals more broadly.

## Status

Early stage — this repo was just created. Stack, the rewrite pipeline
architecture, and how fidelity to the original is verified are still to be
worked out.

`dev` is the default/live branch; `main` only ever advances via a dev →
main promotion PR (see `.github/workflows/enforce-dev-to-main.yml`).
