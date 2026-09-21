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
| `apple-q3-fy2026.md` | Apple | Q3 FY2026 (ended Jun 27, 2026) | [SEC 8-K, Exhibit 99.1](https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/a8-kex991q3202606272026.htm) |
| `amazon-q2-2026.md` | Amazon | Q2 2026 (ended Jun 30, 2026) | [SEC 8-K, Exhibit 99.1](https://www.sec.gov/Archives/edgar/data/1018724/000101872426000024/amzn-20260630xex991.htm) |

SpaceX is not included — it's privately held and doesn't file public
earnings statements.

Apple and Amazon were added specifically for content the first five
didn't cover: Apple's release states a one-time, non-recurring effect
("~2 points of gross margin benefit from tariff refunds") that a faithful
rewrite has to keep flagged as one-time rather than folded into the
underlying trend; Amazon's names real AI-industry customers (Anthropic,
OpenAI, as real Trainium chip customers) — a case where those names
belong in the rewrite because the source actually states them, the
opposite failure direction from the fabrication check on every other page,
where the same names are deliberately absent and must stay absent.

## Technical-book sources

Four books, each contributing a few real full pages plus one real
table-of-contents page (see `eval/pages/README.md` for why contents pages
are scored separately from ordinary prose). Chosen for genre spread, not
just volume — financial narrative, distributed-systems prose, ML/inference
technical writing, and finance *education* (concepts explained, closer in
spirit to what this product itself produces) are different enough in
vocabulary and structure that a detector or a writer tuned on only one of
them can look solid and still be narrow.

| Book | Genre | Why it's here |
|---|---|---|
| *The Art of Scalability* (Abbott & Fisher) | Distributed systems / org design | The original book source; kept for continuity with earlier results |
| *AI Engineering* (Chip Huyen, O'Reilly) | ML systems | Different vocabulary (fine-tuning, context window) than the systems book; also the source of the user-commissioned reference rewrite used in `prompts/writing_model_system_prompt.md`'s worked example |
| *Inference Engineering* | ML/inference internals | Denser technical vocabulary (quantization, KV cache, batching) and real `Figure N.N` captions, for the figure-reproduction path |
| *Financial Statements: A Step-by-Step Guide* | Finance education | The one book whose whole purpose is teaching financial concepts to a beginner — the closest genre match to what a rewrite is supposed to do, and a real stress test: can a rewrite explain accounting concepts as well as a book written to do exactly that? |

All are copyrighted commercial books, handled the same way as the
original *AI Engineering* source: full pages are used locally for
evaluation but never committed (`eval/pages/*.yaml` for book-derived
pages is gitignored), and only short excerpts (a few paragraphs) would
ever be quoted into a committed file for comparison purposes. Regenerate
locally by pointing the four `PLAINLY_*_PDF` env vars in
`eval/build_page_set.py` at local copies.
