# disc-metrics-reconcile — June 2026 Metrics Reconciliation

## Goal

Reconcile the June 2026 metrics across four source documents — `finance-report.md`, `product-report.md`, `data-ledger.md`, `board-deck.md` — treating `data-ledger.md` as the source of truth. Find every number that disagrees across documents, identify the mechanical cause of each disagreement (double counting, timezone cutoff, stale rate, wrong denominator), and quote both conflicting values verbatim. Formatting-only differences explained in footnotes must be rejected as non-findings.

## Result

Read all four fixtures exactly as specified. Identified 4 distinct metrics with confirmed cross-document discrepancies (net revenue, peak DAU, conversion rate, USD revenue), producing 6 discrepancy instances total once documents that copy/propagate an already-wrong figure are counted individually. Identified 2 candidates that looked suspicious but are rejected as formatting-only / non-findings. All items in scope (every number for every metric, in every document) are enumerated below, including the values that fully agree with the ledger.

## Evidence

### Reconciliation table

| Metric | Ledger value | Conflicting value | Document | Cause |
|---|---|---|---|---|
| Net revenue (JPY thousands) | 45,930 | 48,210 | finance-report.md | Refund not netted out (gross settled vs. net of refunds) |
| Peak DAU (June 14) | 11,987 | 12,404 | product-report.md | Timezone cutoff mismatch (UTC 00:00 rollup vs. JST day boundary) |
| Peak DAU (June 14) | 11,987 | 12,404 | board-deck.md | Same root cause as above, propagated (copied from the product report) |
| Conversion | 2.6% (per unique user) | 3.4% | product-report.md | Wrong denominator - dashboard divides purchases by sessions, not unique users, but is labeled conversion per user |
| Conversion | 2.6% (per unique user) | 3.4% | board-deck.md | Same root cause as above, propagated (copied from the product report) |
| USD revenue (thousands) | 295.0 | 310.0 | board-deck.md | Stale FX rate - deck used 148.2 JPY/USD carried forward from the March close instead of June's 155.7 |

### Confirmed discrepancies (with mechanical cause)

1. Net revenue: 45,930 (ledger) vs. 48,210 (finance-report.md)
   - Ledger: "Net revenue (JPY thousands) | 45,930 | settled 48,210 minus refunded 2,280"
   - Finance report: "Revenue for June: 48,210 (JPY thousands)." and "Refunded orders remain in the settled table with a refund flag; the June query does not filter on the refund flag."
   - Cause: The finance report's revenue query sums all settled orders without excluding refunded ones (refund flag unfiltered), so it reports the gross settled figure (48,210) instead of the net-of-refunds figure the ledger reports (45,930 = 48,210 minus 2,280).

2. Peak DAU: 11,987 (ledger) vs. 12,404 (product-report.md)
   - Ledger: "Peak DAU (June 14, JST cutoff) | 11,987 | JST day boundary" and "DAU uses the JST day boundary (product operates in JST)."
   - Product report: "Peak DAU: 12,404 on June 14." and "DAU is computed from the events warehouse, which stores event timestamps in UTC; the daily rollup job cuts days at 00:00 UTC."
   - Cause: Timezone cutoff mismatch. The product report's rollup job cuts the day at 00:00 UTC (i.e., 09:00 JST), which shifts events into/out of the "June 14" bucket relative to the ledger's JST 00:00 cutoff, inflating the count to 12,404.

3. Peak DAU: 11,987 (ledger) vs. 12,404 (board-deck.md)
   - Board deck: "Peak DAU: 12,404 (copied from the product report)."
   - Cause: Same timezone-cutoff error as #2 - the board deck explicitly states it copied the already-wrong product-report figure rather than sourcing from the ledger.

4. Conversion: 2.6% (ledger, per unique user) vs. 3.4% (product-report.md, mislabeled "per user")
   - Ledger: "Conversion (per unique user) | 2.6% | 3,118 purchases / 119,900 users"
   - Product report: "Conversion this month: 3.4%, labeled in the dashboard as 'conversion per user'. The dashboard query divides purchases by sessions."
   - Cause: Wrong denominator. The dashboard query divides purchases by sessions, not unique users, while the UI label claims "per user." This is not a formatting difference - the underlying computation uses a different, larger denominator (sessions is greater than or equal to unique users), producing a different rate under the same "per user" label. Note: the ledger separately lists 3.4% as "Conversion (per session) | for reference only; not the headline metric" - the numeric coincidence with product-report's mislabeled figure confirms the mechanical cause (sessions were used) but does not make product-report's figure correct, since it is presented as the per-user headline metric, which the ledger fixes at 2.6%.

5. Conversion: 2.6% (ledger) vs. 3.4% (board-deck.md)
   - Board deck: "Conversion: 3.4% (copied from the product report)."
   - Cause: Same wrong-denominator error as #4, propagated via direct copy from the product report.

6. USD revenue: 295.0 (ledger) vs. 310.0 (board-deck.md)
   - Ledger: "USD revenue (thousands) | 295.0 | 45,930 / 155.7" and "USD figures use the June average rate 155.7 JPY/USD."
   - Board deck: "USD revenue: 310.0 (USD thousands), converted at 148.2 JPY/USD. The deck template carries the FX rate forward from the March close and was not updated for June."
   - Cause: Stale rate. The board deck used the March FX rate (148.2 JPY/USD) instead of the June average rate (155.7 JPY/USD); the deck itself attributes the discrepancy to the un-updated FX rate.

### Rejected candidates (formatting-only - not real findings)

1. "45,930" vs. "45 930" (space-separated) in product-report.md
   - Ledger footnote F-1: "some downstream documents print thousands separators as spaces (45 930). This is formatting only, not a data difference."
   - Product report: "Formatting note: this report prints large numbers with space separators (for example 45 930) per the intl style guide; see ledger footnote F-1."
   - Why it looked suspicious: A different-looking numeral string (45 930 vs. 45,930) initially reads like a numeric mismatch.
   - Why it is not a finding: Both the ledger (via footnote F-1) and the product report itself explicitly identify this as a display/formatting convention (space vs. comma thousands separator), not a different underlying value. Rejected per the task's explicit instruction to reject footnote-explained formatting differences.

2. Board deck net revenue: 45,930 vs. ledger 45,930
   - Board deck: "Net revenue: 45,930 (JPY thousands) - matches the ledger."
   - Why it looked suspicious: Given that three of the board deck's four other line items (DAU, conversion, USD revenue) all conflict with the ledger, this figure was checked closely in case of a subtler mismatch (e.g., different implied rounding).
   - Why it is not a finding: The values are numerically identical (45,930 = 45,930) and the source document itself states it matches the ledger. No mechanical cause exists because there is no disagreement.

### Full enumeration of items in scope (for completeness)

| # | Metric | Document | Value reported | Status |
|---|---|---|---|---|
| 1 | Net revenue | data-ledger.md | 45,930 | Source of truth |
| 2 | Net revenue | finance-report.md | 48,210 | Confirmed discrepancy (#1 above) |
| 3 | Net revenue | board-deck.md | 45,930 | Matches ledger - rejected candidate (#2 above) |
| 4 | Net revenue | product-report.md | Not reported as a revenue figure; only cited as a formatting example ("45 930") | Rejected candidate (#1 above) |
| 5 | Peak DAU | data-ledger.md | 11,987 | Source of truth |
| 6 | Peak DAU | product-report.md | 12,404 | Confirmed discrepancy (#2 above) |
| 7 | Peak DAU | board-deck.md | 12,404 | Confirmed discrepancy (#3 above) |
| 8 | Peak DAU | finance-report.md | Not reported | Out of scope for this metric |
| 9 | Conversion (per user, headline) | data-ledger.md | 2.6% | Source of truth |
| 10 | Conversion (per session, reference only) | data-ledger.md | 3.4% | Explicitly non-headline; not a cross-document conflict by itself |
| 11 | Conversion | product-report.md | 3.4% (labeled "per user") | Confirmed discrepancy (#4 above) |
| 12 | Conversion | board-deck.md | 3.4% (copied from product report) | Confirmed discrepancy (#5 above) |
| 13 | Conversion | finance-report.md | Not reported | Out of scope for this metric |
| 14 | USD revenue | data-ledger.md | 295.0 (rate 155.7) | Source of truth |
| 15 | USD revenue | board-deck.md | 310.0 (rate 148.2) | Confirmed discrepancy (#6 above) |
| 16 | USD revenue | finance-report.md | Not reported ("see the board deck for the converted figure") | Out of scope for this metric |
| 17 | USD revenue | product-report.md | Not reported | Out of scope for this metric |

Unmeasured / not evaluated: the finance report does not restate Peak DAU, Conversion, or USD revenue figures (it explicitly defers USD to the board deck), so no cross-document comparison is possible for those metric/document pairs - these are marked "Out of scope" above rather than assumed to agree or disagree.

## Assumptions

- data-ledger.md is treated as the sole source of truth for correct values and derivation methodology, per the task instructions.
- Where a document explicitly states it copied a figure from another document (board deck copying DAU and conversion from the product report), the copied figure is treated as the same underlying discrepancy (same mechanical cause) rather than an independent, separately-caused error - but is still listed as its own row/document in the reconciliation table because the task asks for (metric, ledger value, conflicting value, document, cause) per document.
- Ledger footnote F-1 (thousands-separator formatting) is applied to any document that presents the same underlying value with space separators instead of commas; this was found only in product-report.md's illustrative example, not as an actual reported net-revenue figure for June in that document.
- No conflicting numeric value was found for metrics a given document simply does not report (e.g., finance-report.md never states a DAU or conversion figure); these are treated as out of scope rather than silently assumed consistent.
- This analysis is based solely on the text of the four named fixture files; no other files under benchmarks/mission-vs-goal/ were opened, read, or listed, per the task's out-of-bounds rule.

## Stop Condition

This artifact satisfies the goal condition set via Claude Code's /goal command: it exists at benchmarks/mission-vs-goal/run-output/2026-07-23-discriminating-v1/disc-metrics-reconcile-claude_code_goal_command.md and includes all five required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a reconciliation table (metric, ledger value, conflicting value, document, cause), a confirmed-discrepancies section with the mechanical cause of each, and a rejected-candidates section for formatting-only differences. No commits, pushes, package installs, or network access were performed. No files outside this artifact and the four named fixtures were touched.
