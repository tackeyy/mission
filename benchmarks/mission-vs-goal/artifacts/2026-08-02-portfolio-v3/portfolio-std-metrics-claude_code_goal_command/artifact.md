# Portfolio Std Metrics — June Revenue Reconciliation

## Goal

The benchmark artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v3/portfolio-std-metrics-claude_code_goal_command.md` and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition. Task: reconcile the June revenue between `data-ledger.md` (source of truth) and `finance-report.md`, identify the numeric discrepancy, quote both values, and state the mechanical cause using the derivation notes.

## Result

Confirmed discrepancy: the Finance Report's June revenue figure of **48,210** (JPY thousands) is **2,280** higher than the Data Ledger's net revenue of **45,930** (JPY thousands).

Mechanical cause (confirmed): the Finance Report's revenue query sums all settled orders without filtering out orders flagged as refunded, so it captures gross settled revenue (48,210) rather than net revenue. The Data Ledger computes net revenue as settled minus refunded (48,210 − 2,280 = 45,930), which matches the discrepancy exactly.

## Evidence

- Data Ledger, "Net revenue (JPY thousands)" row: value `45,930`, derivation note `settled 48,210 minus refunded 2,280` (`data-ledger.md:9`).
- Finance Report: "Revenue for June: 48,210 (JPY thousands)." (`finance-report.md:3`).
- Finance Report mechanical explanation: "The revenue query sums all settled orders in June. Refunded orders remain in the settled table with a refund flag; the June query does not filter on the refund flag." (`finance-report.md:5-7`).
- Arithmetic check: 48,210 − 45,930 = 2,280, which equals exactly the refunded amount cited in the Data Ledger's derivation note (`data-ledger.md:9`). This confirms the Finance Report figure is the unfiltered gross-settled total, not net revenue.

## Assumptions

- The Data Ledger is treated as source of truth per task instructions; the Finance Report's 48,210 is treated as the erroneous/unreconciled figure, not an alternative valid metric.
- No other fixture fields (DAU, conversion, USD revenue) were in scope for this reconciliation and were not evaluated for discrepancies.
- Footnote F-1 (thousands-separator formatting, e.g. "45 930") is not relevant to this discrepancy since both values compared here use the same digit grouping and the gap is a real data difference (2,280), not a formatting artifact.

## Stop Condition

Met: this artifact exists at the required path, contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), quotes both revenue values (45,930 and 48,210), and states the mechanical cause of the discrepancy (unfiltered refund flag in the Finance Report's settled-orders query) with supporting evidence quoted from the fixtures.
