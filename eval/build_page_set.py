#!/usr/bin/env python3
"""Regenerates eval/tasks/<task>/pages/ — the page-scale eval set, one
directory per writer task (earnings_statement, technical_book,
contents_page), mirroring prompts/writer/'s one-file-per-task split.

Real full pages (the earnings releases in eval/sources/, plus real pages
from four technical/finance books) annotated with the assertions that
run_page_eval.py checks in code: every figure that must survive, the
jargon that must be explained, and a watchlist of real company names
absent from the page that must therefore never appear in a rewrite.

See eval/tasks/README.md for the design and its known limitations.

The technical-book pages are deliberately NOT committed: they are full
pages of copyrighted books, and this repo's practice is to keep
third-party copyrighted text out of it (see the note on the reference
novel in prompts/writer/_shared.md). Set the env vars below to local
copies to regenerate them. Without any of them you get the earnings
half only — public-disclosure material, which is committed.
"""

import os
import re
import statistics
from pathlib import Path

import yaml

BE = str(Path(__file__).resolve().parent.parent)
# One directory per task (eval/tasks/<source_type>/pages/), not a flat
# eval/pages/ — mirrors prompts/writer/'s one-file-per-task split, and
# eval/run_page_eval.py picks the matching writer task prompt straight
# from the same source_type. Moved here 2026-09-21; see eval/tasks/README.md.
TASK_DIRS = {
    "earnings_statement": f"{BE}/eval/tasks/earnings_statement/pages",
    "technical_book": f"{BE}/eval/tasks/technical_book/pages",
    "contents_page": f"{BE}/eval/tasks/contents_page/pages",
}

NUM = re.compile(r"\$?\d[\d,]*\.\d+|\$\d[\d,]*|\d[\d,]*%|\b\d{4}\b|\b\d{2,3}\b")

# Jargon a layperson cannot be assumed to know. If it appears on the page, the
# rewrite has to make it understandable — that is the product's whole promise.
# Split by domain since the set now spans finance, distributed-systems, and
# ML/inference material, each with its own vocabulary a non-specialist won't
# have.
JARGON = [
    # finance / earnings
    "GAAP", "non-GAAP", "EPS", "diluted", "gross margin", "operating margin",
    "operating income", "net income", "free cash flow", "operating cash flow",
    "capital expenditures", "EBITDA", "ARPU", "DAU", "WAU", "year-over-year",
    "sequentially", "constant currency", "guidance", "fiscal year", "8-K",
    "trailing twelve", "regulatory credits", "backlog", "bookings",
    "run rate", "annualized", "accrual", "T-account", "debit", "credit",
    "balance sheet", "income statement", "depreciation", "amortization",
    # distributed systems / scalability
    "availability", "scalability", "QA", "Quality Assurance", "throughput",
    "headcount", "roadmap", "SLA", "architecture", "infrastructure",
    # ML / inference
    "inference", "quantization", "batching", "attention", "context window",
    "parameters", "fine-tuning", "fine-tune", "prompt", "token", "latency",
    "KV cache", "GPU", "checkpoint",
]

# Real, famous names a model might reach for as an "illustrative example".
# Any that don't appear in the source are fabrication if they show up in
# output. Anthropic/OpenAI added alongside the Amazon source, which
# legitimately names both as real Trainium customers — the point of this
# list is never "ban the name," it's "catch it where the source doesn't
# actually mention it."
WATCHLIST = ["Amazon", "AWS", "Google", "Microsoft", "Azure", "Apple", "Meta",
             "Facebook", "Netflix", "Tesla", "Walmart", "Coca-Cola", "IBM",
             "Oracle", "Salesforce", "Uber", "Airbnb", "Twitter", "OpenAI",
             "Anthropic", "NVIDIA"]

# One book PDF per env var, so any subset can be provided. Page indices were
# hand-picked (pypdf 0-based) for real, substantial content — dense prose,
# not a dedication or a blank page. A `toc_page` is included per book
# specifically to test that the contents/index detector in
# services/worker/main.py generalizes across different books' table-of-
# contents formatting, not just the one it was originally tuned against.
BOOKS = [
    {
        "env": "PLAINLY_SCALABILITY_PDF",
        "slug": "scalability",
        "pages": [43, 44, 45, 48, 49],
        "toc_page": 9,
    },
    {
        "env": "PLAINLY_AI_ENGINEERING_PDF",
        "slug": "ai-engineering",
        "pages": [31, 35, 45],
        "toc_page": 6,
    },
    {
        "env": "PLAINLY_INFERENCE_ENGINEERING_PDF",
        "slug": "inference-engineering",
        "pages": [20, 69, 165],
        "toc_page": 4,
    },
    {
        "env": "PLAINLY_FINANCIAL_STATEMENTS_PDF",
        "slug": "financial-statements",
        "pages": [87, 101, 123],
        "toc_page": 1,
    },
]


def clean(t):
    t = re.sub(r"ptg\d+", "", t)
    return re.sub(r"[ \t]+", " ", t).strip()


def numbers(text):
    """Distinct figures a faithful rewrite must carry over.

    Excludes the running header/footer folio number on book pages. A printed
    page carries its own page number in the header ("18 CHAPTER 1 THE IMPACT
    OF...", "DEFINING ROLES 23"); that is an artifact of the physical book, not
    a claim in the text, and a plain-language rewrite is right to drop it.
    Leaving it in made every model score 0% on four prose pages for behaving
    correctly, which is a bug in the eval and not in the models.
    """
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    edges = set()
    for edge in (lines[:1] + lines[-1:]):
        # a bare 1-3 digit number sitting in a header/footer line
        for m in re.finditer(r"(?<!\d)\d{1,3}(?!\d)", edge):
            edges.add(m.group(0))
    body_without_edges = "\n".join(lines[1:-1]) if len(lines) > 2 else ""

    seen, out = set(), []
    for m in NUM.finditer(text):
        tok = m.group(0)
        core = tok.lstrip("$").rstrip("%").replace(",", "")
        if len(core) < 2 or core in seen:
            continue
        # drop a header/footer folio number unless the same figure is also
        # used somewhere in the actual body text
        if core in edges and not re.search(rf"(?<!\d){re.escape(core)}(?!\d)", body_without_edges):
            continue
        seen.add(core)
        out.append({"as_written": tok, "match": core})
    return out


def jargon_on_page(text):
    low = text.lower()
    return [j for j in JARGON if j.lower() in low]


def forbidden(text):
    return [w for w in WATCHLIST if w.lower() not in text.lower()]


def make_example(source_type, source, company, text):
    return {"id": None, "source_type": source_type, "source": source,
            "company": company, "original_page": clean(text)}


examples = []

# --- earnings statements: real, public 8-K exhibits / IR press releases ---
for fn, company in [
    ("nvidia-q2-fy2027.md", "NVIDIA"),
    ("microsoft-q4-fy2026.md", "Microsoft"),
    ("alphabet-q2-2026.md", "Alphabet"),
    ("tesla-q2-2026.md", "Tesla"),
    ("reddit-q2-2026.md", "Reddit"),
    ("apple-q3-fy2026.md", "Apple"),
    ("amazon-q2-2026.md", "Amazon"),
]:
    raw = open(f"{BE}/eval/sources/{fn}").read()
    body = "\n".join(l for l in raw.splitlines() if not l.startswith("Source:"))
    examples.append(make_example("earnings_statement", f"eval/sources/{fn}", company, body))

# --- technical / finance-education books: real pages + one TOC page each ---
any_book = False
for book in BOOKS:
    path = os.environ.get(book["env"])
    if not path or not os.path.exists(path):
        continue
    any_book = True
    from pypdf import PdfReader
    r = PdfReader(path)

    for i in book["pages"]:
        t = r.pages[i].extract_text() or ""
        if len(t.strip()) < 800:
            continue  # a picked index that turned out blank/thin in this copy
        examples.append(make_example(
            "technical_book", f"{book['slug']}, PDF page index {i}", None, t))

    toc_i = book.get("toc_page")
    if toc_i is not None:
        t = r.pages[toc_i].extract_text() or ""
        if t.strip():
            ex = make_example(
                "contents_page", f"{book['slug']}, PDF page index {toc_i} (table of contents)",
                None, t)
            examples.append(ex)

if not any_book:
    print("No PLAINLY_*_PDF env vars set — generating the earnings half only "
          "(book pages are not committed; see eval/tasks/README.md)")

for n, ex in enumerate(examples, 1):
    if ex["company"]:
        slug = ex["company"].lower()
    else:
        # "scalability, PDF page index 43 (table of contents)" -> "scalability-p43-toc"
        src = ex["source"]
        book_slug = src.split(",")[0]
        idx = re.search(r"index (\d+)", src).group(1)
        suffix = "-toc" if ex["source_type"] == "contents_page" else ""
        slug = f"{book_slug}-p{idx}{suffix}"
    ex["id"] = f"p{n:03d}-{slug}"
    p = ex["original_page"]
    ex["chars"] = len(p)
    ex["numbers_that_must_survive"] = numbers(p)
    ex["jargon_that_must_be_explained"] = jargon_on_page(p)
    ex["entities_that_must_not_appear"] = forbidden(p)

for d in TASK_DIRS.values():
    os.makedirs(d, exist_ok=True)
    for f in os.listdir(d):
        if f.endswith(".yaml"):
            os.remove(os.path.join(d, f))
for ex in examples:
    out_dir = TASK_DIRS[ex["source_type"]]
    yaml.safe_dump(ex, open(f"{out_dir}/{ex['id']}.yaml", "w"), sort_keys=False, allow_unicode=True, width=78)

print(f"{len(examples)} page-scale examples")
for ex in examples:
    print(f"  {ex['id']:<32} {ex['chars']:>5}ch  {len(ex['numbers_that_must_survive']):>2} nums  "
          f"{len(ex['jargon_that_must_be_explained']):>2} jargon  {ex['source_type']}")
if examples:
    print("median chars:", int(statistics.median(e["chars"] for e in examples)))
