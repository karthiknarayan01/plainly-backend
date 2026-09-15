# Raw sources

Real earnings releases, pulled from SEC filings and company investor
relations pages — public disclosures, not a copyright concern the way the
reference novel was (companies publish these specifically to be read and
quoted).

These are raw material, not eval examples yet. Each `eval/examples/*.yaml`
entry needs an original excerpt, a good rewrite, a bad rewrite, and why —
these files are where the "original excerpt" side comes from. Picking
specific passages and drafting the good/bad rewrite pairs is the next
step.

| File | Company | Quarter | Source |
|---|---|---|---|
| `nvidia-q2-fy2027.md` | NVIDIA | Q2 FY2027 (ended Jul 26, 2026) | [investor.nvidia.com](https://investor.nvidia.com/news/press-release-details/2026/NVIDIA-Announces-Financial-Results-for-Second-Quarter-Fiscal-2027/default.aspx) |
| `microsoft-q4-fy2026.md` | Microsoft | Q4 FY2026 (ended Jun 30, 2026) | [news.microsoft.com](https://news.microsoft.com/source/2026/07/29/microsoft-cloud-and-ai-strength-fuels-fourth-quarter-results-4/) |
| `alphabet-q2-2026.md` | Alphabet | Q2 2026 (ended Jun 30, 2026) | [SEC 8-K exhibit](https://www.sec.gov/Archives/edgar/data/0001652044/000165204426000066/googexhibit991q22026.htm) |
| `tesla-q2-2026.md` | Tesla | Q2 2026 (ended Jun 30, 2026) | [TSLA-Q2-2026-Update.pdf](https://assets-ir.tesla.com/tesla-contents/IR/TSLA-Q2-2026-Update.pdf) |
| `reddit-q2-2026.md` | Reddit | Q2 2026 (ended Jun 30, 2026) | [SEC 8-K exhibit](https://www.sec.gov/Archives/edgar/data/1713445/000171344526000098/earningspressreleaseq226.htm) |

SpaceX is not included — it's privately held and doesn't file public
earnings statements.

## Technical-book source

For the book side of the eval set, the user provided the original text and
a user-commissioned simplified rewrite of Chapter 1 ("Introduction to
Building AI Applications with Foundation Models") of *AI Engineering* by
Chip Huyen (O'Reilly, 2024) — a copyrighted commercial book, unlike the
public SEC filings above. Following the same handling used for the
Chetan Bhagat style reference: only short excerpts (a few paragraphs) are
quoted into `eval/examples/*.yaml` for comparison purposes, never the full
chapter, and the full source PDFs are not committed to this repo.
