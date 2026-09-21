#!/usr/bin/env python3
"""Page-scale benchmark for candidate writer models.

Why this exists alongside run_eval.py: the older eval set in
eval/examples/ is built from hand-trimmed excerpts with a median length of
266 characters, while a real page the product actually processes is
~1,000-3,400. Every model decision made against that set was made on
inputs an order of magnitude smaller than production input, and the
ranking it produced did not survive contact with real pages — the model
it ranked worst wrote the best real page, and the deployed model was
quietly compressing pages.

The other problem it fixes: run_eval.py scores a rewrite almost entirely
through a judge model's opinion. A judge saying "9/10" is not evidence
that a reader got every number. So most of the score here is computed in
code, from the text, with no model involved:

  number_recall   every distinct figure on the source page has to appear
                  in the rewrite. This is the product's core promise
                  ("same claims, nothing dropped") expressed as something
                  countable, and it is the primary ranking key.
  fabrications    real company names that are NOT on the source page and
                  must therefore never show up in the rewrite. This is the
                  documented failure mode where "major cloud providers"
                  becomes "Amazon, Google and Microsoft".
  expansion       output length / input length. A rewrite that explains
                  jargon and adds a worked example should come out LONGER
                  than the source; a ratio below 1.0 means the page was
                  summarised, which is the one thing this product must
                  never do.
  preamble_leak   output beginning with "Let me think through..." or
                  "Here's the rewrite:" — model planning text shipped to
                  the reader.
  truncated       finish_reason == "length": the page was cut off mid
                  sentence because it hit the output cap.
  stray_markdown  "## Heading", an unclosed "**", or a "- " bullet line —
                  formatting the reader's plain-prose renderer displays
                  as literal punctuation. Checked with the same patterns
                  services/worker/main.py and the frontend actually use,
                  not a separate approximation of them.
  highlights      count of well-formed **bold** spans. The prompt asks
                  for roughly 2-4 per page; 0 or >6 is flagged as an
                  anti-pattern (nothing emphasised, or everything is).
  contents page   a source page whose source_type is "contents_page" is
  handling        scored on a different, binary axis: did the writer
                  correctly produce nothing (or a short decline), per its
                  own prompt instruction — not fidelity/expansion, which
                  don't mean anything for a page that should be empty.
                  These pages are excluded from every other aggregate so
                  they can't distort it either direction.

Only `teaching` and `jargon_explained` need a model, and both are asked as
narrow questions ("which of these specific terms did it explain?") rather
than a holistic vibe score. The same judge model is used for every
candidate so the comparison stays fair.

Usage:
  python eval/run_page_eval.py --models deepseek/deepseek-v4-pro-0813,qwen/qwen3-235b-a22b-2507
  python eval/run_page_eval.py --limit 3          # quick smoke run
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from openai import OpenAI

SCRIPT_DIR = Path(__file__).resolve().parent
PAGES_DIR = SCRIPT_DIR / "pages"
RESULTS_DIR = SCRIPT_DIR / "results"
PROMPTS_DIR = SCRIPT_DIR.parent / "prompts"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_JUDGE = "deepseek/deepseek-chat-v3.1"
MAX_OUTPUT_TOKENS = 4000  # generous: a real page rewrite should expand
# Deliberately short, with few retries. A page rewrite that's working takes
# 10-25s; anything much past that is a stalled provider route, not slow
# thinking. Learned the hard way: at 180s x 5 retries a single hung request
# blocked one page for ~18 minutes and two benchmark runs died to it, one
# after spending real money. Fail fast, record the failure, move on.
REQUEST_TIMEOUT_SECONDS = 75
MAX_RETRIES = 2

LEAK_RE = re.compile(
    r"^\s*(let me\b|okay,? (?:let|so)\b|first,? i\b|i'?ll\b|here'?s (?:the|my)\b|"
    r"looking at (?:this|the) (?:passage|page)\b|before (?:i|we) (?:write|begin)\b)",
    re.IGNORECASE,
)

# Mirrors services/worker/main.py's _DECLINED exactly — a short reply that
# announces having nothing to say, rather than actually saying nothing.
# The two services already don't share code (see their db.py docstrings);
# this is the same duplication, same reasoning.
DECLINED_RE = re.compile(
    r"no (output|content|text)\b"
    r"|nothing to (rewrite|translate|simplify)"
    r"|this page (is|appears to be) (a |an )?(table of contents|index|blank)",
    re.IGNORECASE,
)
MAX_DECLINE_LENGTH = 400

# Formatting the reader's renderer cannot display, mirroring the actual
# strip/render logic in plainly-web (stripMarkdown, splitBold) — checked
# here, on the same output the real pipeline would receive, rather than
# assumed safe because the prompt asks for plain prose.
HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
BULLET_RE = re.compile(r"^\s*[-*]\s+\S", re.MULTILINE)
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
# Was 6, loosely matching the prompt's old "roughly two to four" guidance.
# The 2026-09-20 full 25-page run found the real distribution running far
# past even that: 15/21 pages over 6, up to 27 on one numerically-dense
# page — the prompt's "roughly" was being read as a suggestion, not a
# limit. The prompt now states a hard budget of 4; kept one highlight of
# slack here rather than matching it exactly, so a page at 5 (a minor,
# plausible overshoot) isn't flagged identically to one at 27.
MIN_HEALTHY_HIGHLIGHTS = 1
MAX_HEALTHY_HIGHLIGHTS = 5


def load_pages(limit: int | None) -> list[dict]:
    pages = [yaml.safe_load(p.read_text()) for p in sorted(PAGES_DIR.glob("*.yaml"))]
    if not pages:
        raise SystemExit(f"no eval pages found in {PAGES_DIR}")
    return pages[:limit] if limit else pages


def make_client() -> OpenAI:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set (source your .env)")
    return OpenAI(base_url=OPENROUTER_BASE_URL, api_key=key,
                  timeout=REQUEST_TIMEOUT_SECONDS, max_retries=MAX_RETRIES)


def load_prompt(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    marker = "\n---\n"
    i = text.find(marker)
    return (text[i + len(marker):] if i != -1 else text).strip()


def write_page(client: OpenAI, model: str, page_text: str,
               prompt_path: Path | None = None) -> tuple[str, str]:
    """Returns (rewrite, finish_reason). Uses the real production prompt
    unless a variant is passed, so a prompt change can be A/B'd against
    the deployed one on identical pages with identical scoring."""
    system = load_prompt(prompt_path or (PROMPTS_DIR / "writing_model_system_prompt.md"))
    user = (
        "Below is a page from a document. Treat it as the full document and the "
        "full page range to rewrite this turn — it's a single, self-contained "
        "passage, not a multi-page document, and this is a first attempt (no "
        f"prior feedback).\n\n---\n{page_text.strip()}\n---\n\n"
        "Produce your rewrite of this passage now."
    )
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        r = client.chat.completions.create(model=model, messages=msgs, temperature=0.3,
                                           max_tokens=MAX_OUTPUT_TOKENS,
                                           extra_body={"reasoning": {"enabled": False}})
    except Exception as exc:
        if "reasoning" not in str(exc).lower():
            raise
        r = client.chat.completions.create(model=model, messages=msgs, temperature=0.3,
                                           max_tokens=MAX_OUTPUT_TOKENS)
    choice = r.choices[0]
    return (choice.message.content or ""), (choice.finish_reason or "")


# ---------- objective, model-free scoring ----------

def score_numbers(page: dict, rewrite: str) -> tuple[int, int, list[str]]:
    """How many of the page's figures survived into the rewrite."""
    haystack = rewrite.replace(",", "")
    hits, missing = 0, []
    facts = page.get("numbers_that_must_survive") or []
    for f in facts:
        if re.search(rf"(?<!\d){re.escape(f['match'])}(?!\d)", haystack):
            hits += 1
        else:
            missing.append(f["as_written"])
    return hits, len(facts), missing


def score_fabrication(page: dict, rewrite: str) -> list[str]:
    """Real company names absent from the source that appeared anyway."""
    found = []
    for name in page.get("entities_that_must_not_appear") or []:
        if re.search(rf"\b{re.escape(name)}\b", rewrite, re.IGNORECASE):
            found.append(name)
    return found


def score_formatting(rewrite: str) -> dict:
    """Formatting defects the reader's plain-prose renderer can't hide.

    All code-computed, no model involved — same rationale as fidelity and
    fabrication: "looks fine" isn't evidence the actual rendered page is
    fine.
    """
    unclosed_bold = len(re.findall(r"\*\*", rewrite)) % 2 == 1
    highlights = len(BOLD_RE.findall(rewrite))
    return {
        "has_heading": bool(HEADING_RE.search(rewrite)),
        "has_bullets": bool(BULLET_RE.search(rewrite)),
        "unclosed_bold": unclosed_bold,
        "highlight_count": highlights,
        "highlight_out_of_range": not (MIN_HEALTHY_HIGHLIGHTS <= highlights <= MAX_HEALTHY_HIGHLIGHTS),
    }


def is_declined(rewrite: str) -> bool:
    text = rewrite.strip()
    return len(text) <= MAX_DECLINE_LENGTH and bool(DECLINED_RE.search(text))


def judge_teaching(client: OpenAI, judge_model: str, page: dict, rewrite: str) -> dict:
    """Narrow questions only: which listed terms got explained, and a teaching score."""
    terms = page.get("jargon_that_must_be_explained") or []
    system = (
        "You check whether a plain-language rewrite actually explains specific "
        "terms to a reader with no background. Reply with JSON only: "
        '{"explained": ["term", ...], "not_explained": ["term", ...], '
        '"teaching": 0-10, "note": "one sentence"}. '
        "A term counts as explained only if the rewrite makes its meaning clear "
        "in ordinary words (a definition, an analogy, or a worked example) — "
        "using the term, or swapping it for a different technical word, does not "
        "count. `teaching` is how well the passage builds understanding for a "
        "beginner: 10 = every hard idea got a concrete bridge, 0 = bare "
        "restatement in simpler words."
    )
    user = (
        f"Terms to check: {json.dumps(terms)}\n\n"
        f"Original page:\n---\n{page['original_page'][:6000]}\n---\n\n"
        f"Rewrite:\n---\n{rewrite[:6000]}\n---"
    )
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    try:
        r = client.chat.completions.create(model=judge_model, messages=msgs, temperature=0.0,
                                           max_tokens=1500, response_format={"type": "json_object"},
                                           extra_body={"reasoning": {"enabled": False}})
    except Exception:
        r = client.chat.completions.create(model=judge_model, messages=msgs,
                                           temperature=0.0, max_tokens=1500)
    raw = r.choices[0].message.content or ""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {"explained": [], "not_explained": terms, "teaching": 0, "note": "judge returned invalid JSON"}


def evaluate(client: OpenAI, model: str, pages: list[dict], judge_model: str,
             prompt_path: Path | None = None) -> dict:
    per_page, failures = [], 0
    for page in pages:
        # Bail out of a model that is clearly not viable rather than paying to
        # confirm it ten times. z-ai/glm-5.3-flash returned empty responses and
        # truncated output on every page and then hung the run; a model that
        # can't answer three pages isn't going to win on the fourth.
        if failures >= 3:
            print(f"  ABANDONED after {failures} failures — not a viable writer", flush=True)
            break
        try:
            rewrite, finish = write_page(client, model, page["original_page"], prompt_path)
        except Exception as exc:
            failures += 1
            print(f"  [{page['id']}] FAILED: {type(exc).__name__}: {str(exc)[:110]}", flush=True)
            per_page.append({"id": page["id"], "error": f"{type(exc).__name__}: {str(exc)[:300]}"})
            continue

        # A contents/index page is scored on a completely different axis:
        # correct behaviour IS an empty (or declined) reply, per the
        # writer's own prompt instruction. Branching here, before the
        # generic "empty = failure" check below, so getting this right
        # is never mistaken for the model being broken — and routing it
        # to its own tally so it can't silently drag down or inflate
        # fidelity/expansion, neither of which mean anything for a page
        # that's supposed to produce nothing.
        if page["source_type"] == "contents_page":
            correct = not rewrite.strip() or is_declined(rewrite)
            per_page.append({
                "id": page["id"], "source_type": page["source_type"],
                "contents_correct": correct,
                "rewrite_chars": len(rewrite), "rewrite": rewrite,
            })
            print(f"  [{page['id']:<22}] CONTENTS PAGE: "
                  f"{'correctly left empty' if correct else 'WRONGLY REWRITTEN'}", flush=True)
            continue

        if not rewrite.strip():
            failures += 1
            print(f"  [{page['id']}] EMPTY RESPONSE", flush=True)
            per_page.append({"id": page["id"], "error": "empty response"})
            continue

        hits, total, missing = score_numbers(page, rewrite)
        fabs = score_fabrication(page, rewrite)
        expansion = len(rewrite) / max(len(page["original_page"]), 1)
        leak = bool(LEAK_RE.match(rewrite))
        fmt = score_formatting(rewrite)
        jt = judge_teaching(client, judge_model, page, rewrite)
        terms = page.get("jargon_that_must_be_explained") or []
        explained = [t for t in (jt.get("explained") or []) if t in terms]

        row = {
            "id": page["id"], "source_type": page["source_type"],
            "number_recall": (hits / total) if total else None,
            "numbers_hit": hits, "numbers_total": total, "numbers_missing": missing[:12],
            "fabrications": fabs, "expansion": round(expansion, 2),
            "preamble_leak": leak, "truncated": finish == "length",
            **fmt,
            "jargon_explained": len(explained), "jargon_total": len(terms),
            "teaching": jt.get("teaching", 0), "note": jt.get("note", ""),
            "rewrite_chars": len(rewrite), "rewrite": rewrite,
        }
        per_page.append(row)
        rc = "n/a" if row["number_recall"] is None else f"{row['number_recall']*100:.0f}%"
        fmt_flags = "".join([
            "H" if fmt["has_heading"] else "",
            "B" if fmt["has_bullets"] else "",
            "*" if fmt["unclosed_bold"] else "",
        ])
        print(f"  [{page['id']:<22}] nums={rc:<5} fab={len(fabs)} exp={row['expansion']:.2f} "
              f"hl={fmt['highlight_count']} jargon={len(explained)}/{len(terms)} teach={row['teaching']} "
              f"{'LEAK ' if leak else ''}{'TRUNC ' if row['truncated'] else ''}"
              f"{('FMT:' + fmt_flags) if fmt_flags else ''}", flush=True)

    ok = [r for r in per_page if "error" not in r and "contents_correct" not in r]
    contents_rows = [r for r in per_page if "contents_correct" in r]
    recalls = [r["number_recall"] for r in ok if r["number_recall"] is not None]
    # Micro-average is the headline: total figures preserved / total figures on
    # offer. The per-page mean (macro) is reported too but is misleading here —
    # an earnings page carries ~30 figures and a prose book page carries 1, so
    # macro lets a single missed number on a book page cost as much as missing
    # all 36 on the NVIDIA page.
    hits_all = sum(r["numbers_hit"] for r in ok)
    total_all = sum(r["numbers_total"] for r in ok)
    summary = {
        "model": model,
        "pages_ok": len(ok), "pages_failed": failures,
        "number_recall": round(hits_all / total_all, 4) if total_all else None,
        "numbers_hit": hits_all, "numbers_total": total_all,
        "number_recall_macro": round(sum(recalls) / len(recalls), 4) if recalls else None,
        "fabrications_total": sum(len(r["fabrications"]) for r in ok),
        "expansion_mean": round(sum(r["expansion"] for r in ok) / len(ok), 2) if ok else None,
        "pages_that_shrank": sum(1 for r in ok if r["expansion"] < 1.0),
        "preamble_leaks": sum(1 for r in ok if r["preamble_leak"]),
        "truncations": sum(1 for r in ok if r["truncated"]),
        "stray_markdown": sum(1 for r in ok if r["has_heading"] or r["has_bullets"] or r["unclosed_bold"]),
        "highlight_out_of_range": sum(1 for r in ok if r["highlight_out_of_range"]),
        "jargon_explained_rate": (
            round(sum(r["jargon_explained"] for r in ok) / max(sum(r["jargon_total"] for r in ok), 1), 4)
            if ok else None
        ),
        "teaching_mean": round(sum(r["teaching"] for r in ok) / len(ok), 2) if ok else None,
        "contents_pages_correct": sum(1 for r in contents_rows if r["contents_correct"]),
        "contents_pages_total": len(contents_rows),
    }
    return {"summary": summary, "pages": per_page}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", required=True, help="comma-separated OpenRouter model slugs")
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE)
    ap.add_argument("--limit", type=int, default=None, help="only the first N pages")
    ap.add_argument("--writer-prompt", default=None,
                    help="path to a writer-prompt variant to A/B against the deployed one")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    pages = load_pages(args.limit)
    client = make_client()
    print(f"{len(pages)} pages, median {sorted(len(p['original_page']) for p in pages)[len(pages)//2]} chars; "
          f"judge={args.judge_model}\n")

    RESULTS_DIR.mkdir(exist_ok=True)
    out = Path(args.out) if args.out else RESULTS_DIR / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-page-eval.json")

    results = []
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        print(f"=== {model}")
        results.append(evaluate(client, model, pages, args.judge_model,
                                Path(args.writer_prompt) if args.writer_prompt else None))
        # Written after every model, not once at the end: an earlier run was
        # killed mid-flight after a hung model and lost every result it had
        # already paid for.
        out.write_text(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "judge_model": args.judge_model, "page_count": len(pages),
            "writer_prompt": args.writer_prompt or "prompts/writing_model_system_prompt.md",
            "results": results,
        }, indent=2))
        s = results[-1]["summary"]
        rc = "n/a" if s["number_recall"] is None else f"{s['number_recall']*100:.1f}%"
        print(f"  -> recall={rc} fabrications={s['fabrications_total']} "
              f"expansion={s['expansion_mean']} shrank={s['pages_that_shrank']} "
              f"leaks={s['preamble_leaks']} trunc={s['truncations']} "
              f"stray_markdown={s['stray_markdown']} highlight_oor={s['highlight_out_of_range']} "
              f"jargon={s['jargon_explained_rate']} teach={s['teaching_mean']} "
              f"contents={s['contents_pages_correct']}/{s['contents_pages_total']} "
              f"failed={s['pages_failed']}\n", flush=True)

    # Rank: fidelity first (the product's promise), then no fabrication, then teaching.
    def key(r):
        s = r["summary"]
        return (-(s["number_recall"] or 0), s["fabrications_total"], -(s["teaching_mean"] or 0))

    print("=" * 116)
    print(f"{'model':<38}{'recall':>8}{'fab':>5}{'exp':>6}{'shrank':>8}{'fmt':>5}{'jargon':>8}"
          f"{'teach':>7}{'toc':>7}{'fail':>6}")
    print("-" * 116)
    for r in sorted(results, key=key):
        s = r["summary"]
        rc = "n/a" if s["number_recall"] is None else f"{s['number_recall']*100:.1f}%"
        jr = "n/a" if s["jargon_explained_rate"] is None else f"{s['jargon_explained_rate']*100:.0f}%"
        toc = f"{s['contents_pages_correct']}/{s['contents_pages_total']}" if s["contents_pages_total"] else "-"
        print(f"{s['model']:<38}{rc:>8}{s['fabrications_total']:>5}{s['expansion_mean'] or 0:>6.2f}"
              f"{s['pages_that_shrank']:>8}{s['stray_markdown']:>5}{jr:>8}{s['teaching_mean'] or 0:>7.2f}"
              f"{toc:>7}{s['pages_failed']:>6}")

    print(f"\nreport: {out}")


if __name__ == "__main__":
    main()
