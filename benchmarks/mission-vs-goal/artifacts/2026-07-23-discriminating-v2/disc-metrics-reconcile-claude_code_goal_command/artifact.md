# Disc Metrics Reconcile — claude_code_goal_command

## Goal

Reconcile the June 2026 metrics across four fixture documents — `finance-report.md`, `product-report.md`, `data-ledger.md` (source of truth), and `board-deck.md` — by finding every number that disagrees with the ledger, identifying the mechanical cause of each disagreement, and quoting both conflicting values. Formatting-only differences that are explained by a footnote must be rejected as non-findings, not reported as discrepancies.

## Result

Four metrics were checked against the ledger across the finance report, product report, and board deck. Three real discrepancies were found (Net revenue vs. finance report, USD revenue vs. board deck, Peak DAU vs. product report and board deck, Conversion vs. product report and board deck — see table below for the exact count of conflicting cells). One candidate (thousands-separator formatting) was evaluated and rejected as formatting-only per the ledger's own footnote.

### Reconciliation Table

| Metric | Ledger value | Conflicting value | Document | Cause |
|---|---|---|---|---|
| Net revenue (JPY thousands) | 45,930 | 48,210 | finance-report.md | Refunded orders not excluded (query does not filter the refund flag on the settled-orders table) |
| USD revenue (thousands) | 295.0 | 310.0 | board-deck.md | Stale FX rate (148.2 JPY/USD carried forward from the March close instead of the June average rate of 155.7) |
| Peak DAU (June 14) | 11,987 | 12,404 | product-report.md | Timezone cutoff mismatch (daily rollup cuts days at 00:00 UTC instead of the product's JST day boundary) |
| Peak DAU (June 14) | 11,987 | 12,404 | board-deck.md | Same timezone-cutoff error, propagated (deck copies the product report's UTC-cutoff figure) |
| Conversion (per unique user) | 2.6% | 3.4% | product-report.md | Wrong denominator (dashboard query divides purchases by sessions, not unique users, despite being labeled "conversion per user") |
| Conversion (per unique user) | 2.6% | 3.4% | board-deck.md | Same wrong-denominator error, propagated (deck copies the product report's per-session figure) |

### Fully Compliant Items (no discrepancy)

| Metric | Ledger value | Document | Status |
|---|---|---|---|
| Net revenue (JPY thousands) | 45,930 | board-deck.md | Matches — board deck states "Net revenue: 45,930 (JPY thousands) — matches the ledger" |
| USD revenue (thousands) | 295.0 | finance-report.md | No conflicting number stated — finance report defers to the board deck ("USD reporting: see the board deck for the converted figure") rather than printing its own figure |

## Evidence

### Confirmed discrepancies

1. **Net revenue: 45,930 vs. 48,210 (finance-report.md)**
   - Ledger: `| Net revenue (JPY thousands) | 45,930 | settled 48,210 minus refunded 2,280 |`
   - Finance report: "Revenue for June: 48,210 (JPY thousands)."
   - Finance report's own methodology note: "The revenue query sums all settled orders in June. Refunded orders remain in the settled table with a refund flag; the June query does not filter on the refund flag."
   - Mechanical cause: the ledger nets settled revenue against refunds (48,210 − 2,280 = 45,930), while the finance report's query includes refunded orders because it does not filter on the refund flag. This is not a formatting difference — it is two different underlying totals (gross settled vs. net-of-refunds).

2. **USD revenue: 295.0 vs. 310.0 (board-deck.md)**
   - Ledger: `| USD revenue (thousands) | 295.0 | 45,930 / 155.7 |` and derivation rule: "USD figures use the June average rate 155.7 JPY/USD."
   - Board deck: "USD revenue: 310.0 (USD thousands), converted at 148.2 JPY/USD. The deck template carries the FX rate forward from the March close and was not updated for June."
   - Mechanical cause: stale FX rate. The board deck used 148.2 JPY/USD (March close) instead of the June average of 155.7 JPY/USD, producing 310.0 instead of the ledger's 295.0. The board deck itself documents this as an un-updated template value.

3. **Peak DAU: 11,987 vs. 12,404 (product-report.md, and board-deck.md which copies it)**
   - Ledger: `| Peak DAU (June 14, JST cutoff) | 11,987 | JST day boundary |` and derivation rule: "DAU uses the JST day boundary (product operates in JST)."
   - Product report: "Peak DAU: 12,404 on June 14." with methodology: "DAU is computed from the events warehouse, which stores event timestamps in UTC; the daily rollup job cuts days at 00:00 UTC."
   - Board deck: "Peak DAU: 12,404 (copied from the product report)."
   - Mechanical cause: timezone cutoff mismatch. The ledger counts DAU using a JST midnight boundary; the product report's rollup job cuts the day at 00:00 UTC (9 hours offset from JST), pulling in a different set of events for "June 14" and yielding a higher count (12,404 vs. 11,987). The board deck inherits this same error because it copies the product report's figure rather than the ledger's.

4. **Conversion (per unique user): 2.6% vs. 3.4% (product-report.md, and board-deck.md which copies it)**
   - Ledger: `| Conversion (per unique user) | 2.6% | 3,118 purchases / 119,900 users |` and derivation rule: "conversion is purchases divided by unique users." The ledger separately lists `| Conversion (per session) | 3.4% | for reference only; not the headline metric |`.
   - Product report: "Conversion this month: 3.4%, labeled in the dashboard as \"conversion per user\". The dashboard query divides purchases by sessions."
   - Board deck: "Conversion: 3.4% (copied from the product report)."
   - Mechanical cause: wrong denominator. The product report's dashboard labels 3.4% as "per user" but the underlying query actually divides purchases by sessions, which matches the ledger's separately-listed (and explicitly non-headline) per-session conversion figure of 3.4%. The headline per-unique-user metric is 2.6%. The board deck inherits the same wrong-denominator value because it copies the product report's figure.

### Rejected candidates (formatting-only, not real findings)

1. **Thousands-separator style (e.g., "45,930" vs. "45 930")**
   - Ledger footnote F-1: "some downstream documents print thousands separators as spaces (45 930). This is formatting only, not a data difference."
   - Product report: "Formatting note: this report prints large numbers with space separators (for example 45 930) per the intl style guide; see ledger footnote F-1."
   - Why it looked suspicious: a numeral rendered as "45 930" instead of "45,930" superficially resembles a data mismatch (different character sequence, could be misread as a different number or a truncation).
   - Why it is not a real finding: both the ledger (via footnote F-1) and the product report explicitly identify this as a separator-style/formatting convention, not a change in the underlying value. No numeric value is actually altered — 45,930 and "45 930" represent the identical quantity. Per the task rules, formatting-only differences explained in footnotes must be rejected as non-findings.

## Assumptions

- The data ledger (`data-ledger.md`) is treated as the authoritative source of truth for all four metrics, per the task instructions.
- "Net revenue" in the board deck's line "Net revenue: 45,930 (JPY thousands) — matches the ledger" is treated as a confirmed match (not a discrepancy) because the printed value is numerically identical to the ledger's 45,930, not merely a formatting variant.
- The finance report does not print its own USD revenue figure (it explicitly defers to the board deck), so it is recorded as "no conflicting number stated" for USD revenue rather than as a discrepancy or a match — this is unmeasured/not applicable rather than compliant-by-value.
- Only the four named fixture files were read, and no other files under `benchmarks/mission-vs-goal/` were opened, greped, or listed, per the task's scope restriction.
- The two Peak DAU citations (product report and board deck) and the two Conversion citations (product report and board deck) are counted as separate reconciliation-table rows (one per conflicting document) since the board deck is a distinct document with its own printed value, even though the root mechanical cause traces back to the product report in both cases.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-07-23-discriminating-v2/disc-metrics-reconcile-claude_code_goal_command.md` and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), a reconciliation table (metric, ledger value, conflicting value, document, cause), a confirmed-discrepancies section with the mechanical cause of each finding, and a rejected-candidates section explaining why the formatting-only thousands-separator difference is a non-finding. All four named fixtures were read and all four ledger metrics (Net revenue, Peak DAU, Conversion per unique user, USD revenue) were checked against both other documents, with fully-compliant items enumerated alongside discrepancies. No commit, push, package install, or network access was performed; no files outside the designated output path (and the four named fixtures) were touched. The task is complete.
