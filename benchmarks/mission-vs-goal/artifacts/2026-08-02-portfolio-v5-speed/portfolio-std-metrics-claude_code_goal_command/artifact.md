# Goal

Reconcile the June revenue figures reported in `data-ledger.md` (source of truth) and `finance-report.md`. Identify the numeric discrepancy, quote both values, and state the mechanical cause using the derivation notes.

# Result

The two documents report different June revenue figures because the Finance Report's query does not exclude refunded orders, while the Data Ledger nets refunds out. The discrepancy is exactly the refunded amount.

- Data Ledger (source of truth) net revenue: **45,930** (JPY thousands)
- Finance Report revenue: **48,210** (JPY thousands)
- Discrepancy: 48,210 − 45,930 = **2,280** (JPY thousands)

# Evidence

From `data-ledger.md`:
- `| Net revenue (JPY thousands) | 45,930 | settled 48,210 minus refunded 2,280 |`

From `finance-report.md`:
- "Revenue for June: 48,210 (JPY thousands)."
- "The revenue query sums all settled orders in June. Refunded orders remain in the settled table with a refund flag; the June query does not filter on the refund flag."

**Mechanical cause**: The Finance Report's "48,210" is the ledger's *settled* subtotal (per the ledger derivation note "settled 48,210 minus refunded 2,280") before refunds are removed. The Finance Report's own text confirms the mechanism: its query "sums all settled orders" and "does not filter on the refund flag," so refunded orders (2,280) that remain in the settled table are counted. The Data Ledger's 45,930 is the settled total net of the 2,280 refunded, per its derivation note. The two figures thus differ by exactly the refunded amount (2,280), and the cause is a missing refund-flag filter in the Finance Report's query, not a data conflict.

# Assumptions

- The Data Ledger's derivation note ("settled 48,210 minus refunded 2,280") is treated as authoritative per the task instruction that the ledger is the source of truth.
- The Finance Report's "48,210" is assumed to be sourced from the same underlying settled-orders table referenced in the ledger's derivation note, based on the numeric match and the Finance Report's own description of its query logic. No independent database access was available to verify this; it is inferred from the two documents' text.
- Footnote F-1 in the ledger (about thousands-separator formatting, e.g. "45 930") is not relevant to this discrepancy, since neither document uses that formatting variant for the figures compared here. This is unmeasured beyond what is stated in the fixture text.
- No other metrics (DAU, conversion, USD revenue) were part of this reconciliation task and were not evaluated for discrepancies.

# Stop Condition

This artifact is complete: it exists at the required path, contains all five required headings (Goal, Result, Evidence, Assumptions, Stop Condition), quotes both revenue values (45,930 and 48,210) with exact source text, and states the mechanical cause (missing refund-flag filter in the Finance Report's settled-orders query) grounded in the fixtures' derivation notes. No further action is needed.
