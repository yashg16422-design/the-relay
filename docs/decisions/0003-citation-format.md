# 0003 — Citation format for answers

- Status: accepted (with verification outstanding)
- Date: 2026-11-09
- Phase / gate: A

## Problem
Every answer must let a reader find the supporting passage in the correct paper. If the citation is vague or the answer includes unsupported claims, the assistant may mislead an engineer.

## Options
1. Cite only the paper identifier and page.
2. Cite paper title or arXiv identifier and version, printed PDF page, and section heading when available.
3. Cite paper title or arXiv identifier and version, section heading, and a short quoted passage, without a page number.

## Evidence
No paper has been downloaded or parsed yet. The brief requires paper citations and the plan requires preserving page numbers. Verify the candidate format against the exact required PDF versions after ingestion by checking that a person can locate each cited passage in the source PDF and that parser page numbering matches the printed PDF page. Capture any mismatch before selecting the final format.

## Decision (builder's recommendation)
Recommend option 2: identify the paper and exact version, cite the 1-based PDF viewer page index (counting from the first page, including any front matter), and include the section heading when present. The parser's 1-based page index must map directly to that viewer page. If the paper also prints a page label, it may be recorded as secondary metadata but is not the citation locator. This gives a stable location and avoids ambiguity between printed page labels and PDF page positions. The final rendering should show a consistent format such as `[Attention Is All You Need, arXiv:1706.03762v7, PDF p. 3, §3.2]`; this example is a format illustration, not a verified paper citation.

## What it costs
The citation needs metadata and page mapping to survive PDF extraction. Section headings may be absent or parsed incorrectly, so the renderer should allow a page-only citation when no reliable heading exists. A quoted passage would be easier to verify but would make answers longer and consume more tokens.

## What would change our mind
Use a different format if page mapping is unreliable for any required paper, or if a reader cannot locate citations in a short usability check. Keep a page locator in every format unless the source PDF itself has no stable page numbers.

## CTO review
`GO WITH CHANGES` — define one page-number convention and verify it against extracted page indices. Exact paper versions and citations remain unverified until ingestion.

## Aloo's call
Accepted by Aloo on 2026-11-09: “ok accepted bro also the goal should be to maximise the score and technicality no matter even if it seems out of scope as long as its helping okay so yeah begin now...” The citation format is accepted as a direction; verify page mapping against all three source PDFs before use.
