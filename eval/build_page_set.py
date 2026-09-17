#!/usr/bin/env python3
"""Regenerates eval/pages/ — the page-scale eval set.

Real full pages (the earnings releases in eval/sources/, plus real pages
from a technical-book PDF) annotated with the assertions that
run_page_eval.py checks in code: every figure that must survive, the
jargon that must be explained, and a watchlist of real company names
absent from the page that must therefore never appear in a rewrite.

See eval/pages/README.md for the design and its known limitations.

The technical-book pages are deliberately NOT committed: they are full
pages of a copyrighted book, and this repo's practice is to keep
third-party copyrighted text out of it (see the note on the reference
novel in prompts/writing_model_system_prompt.md). Set PLAINLY_BOOK_PDF to
a local copy to regenerate them. Without it you get the earnings half
only — public-disclosure material, which is committed.
"""

import os
import re
import statistics
from pathlib import Path

import yaml

BE = str(Path(__file__).resolve().parent.parent)
OUT = f"{BE}/eval/pages"
BOOK = os.environ.get("PLAINLY_BOOK_PDF")

NUM = re.compile(r"\$?\d[\d,]*\.\d+|\$\d[\d,]*|\d[\d,]*%|\b\d{4}\b|\b\d{2,3}\b")

# Jargon a layperson cannot be assumed to know. If it appears on the page, the
# rewrite has to make it understandable — that is the product's whole promise.
JARGON = [
    "GAAP", "non-GAAP", "EPS", "diluted", "gross margin", "operating margin",
    "operating income", "net income", "free cash flow", "operating cash flow",
    "capital expenditures", "EBITDA", "ARPU", "DAU", "WAU", "year-over-year",
    "sequentially", "constant currency", "guidance", "fiscal year", "8-K",
    "trailing twelve", "regulatory credits", "backlog", "bookings",
    "availability", "scalability", "QA", "Quality Assurance", "throughput",
    "headcount", "roadmap", "SLA", "architecture", "infrastructure",
]

# Real, famous names a model might reach for as an "illustrative example".
# Any that don't appear in the source are fabrication if they show up in output.
WATCHLIST = ["Amazon", "AWS", "Google", "Microsoft", "Azure", "Apple", "Meta",
             "Facebook", "Netflix", "Tesla", "Walmart", "Coca-Cola", "IBM",
             "Oracle", "Salesforce", "Uber", "Airbnb", "Twitter", "OpenAI"]

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

examples = []
for fn, company in [("nvidia-q2-fy2027.md","NVIDIA"), ("microsoft-q4-fy2026.md","Microsoft"),
                    ("alphabet-q2-2026.md","Alphabet"), ("tesla-q2-2026.md","Tesla"),
                    ("reddit-q2-2026.md","Reddit")]:
    raw = open(f"{BE}/eval/sources/{fn}").read()
    body = "\n".join(l for l in raw.splitlines() if not l.startswith("Source:"))
    examples.append({"id": None, "source_type": "earnings_statement",
                     "source": f"eval/sources/{fn}", "company": company,
                     "original_page": clean(body)})

if BOOK and os.path.exists(BOOK):
    from pypdf import PdfReader
    r = PdfReader(BOOK)
    picked = 0
    for i in range(43, 220):
        t = clean(r.pages[i].extract_text() or "")
        if len(t) < 2600 or "intentionally left blank" in t:
            continue
        examples.append({"id": None, "source_type": "technical_book",
                         "source": f"technical book, PDF page index {i}",
                         "company": None, "original_page": t})
        picked += 1
        if picked == 5:
            break
else:
    print("PLAINLY_BOOK_PDF not set — generating the earnings half only "
          "(book pages are not committed; see eval/pages/README.md)")

for n, ex in enumerate(examples, 1):
    slug = (ex["company"] or ex["source"].split("index ")[-1])
    ex["id"] = f"p{n:03d}-" + (ex["company"].lower() if ex["company"] else f"scalability-p{slug}")
    p = ex["original_page"]
    ex["chars"] = len(p)
    ex["numbers_that_must_survive"] = numbers(p)
    ex["jargon_that_must_be_explained"] = jargon_on_page(p)
    ex["entities_that_must_not_appear"] = forbidden(p)

os.makedirs(OUT, exist_ok=True)
for f in os.listdir(OUT):
    if f.endswith(".yaml"):
        os.remove(os.path.join(OUT, f))
for ex in examples:
    yaml.safe_dump(ex, open(f"{OUT}/{ex['id']}.yaml","w"), sort_keys=False, allow_unicode=True, width=78)

print(f"{len(examples)} page-scale examples")
for ex in examples:
    print(f"  {ex['id']:<28} {ex['chars']:>5}ch  {len(ex['numbers_that_must_survive']):>2} nums  "
          f"{len(ex['jargon_that_must_be_explained']):>2} jargon  {ex['source_type']}")
print("median chars:", int(statistics.median(e["chars"] for e in examples)))
