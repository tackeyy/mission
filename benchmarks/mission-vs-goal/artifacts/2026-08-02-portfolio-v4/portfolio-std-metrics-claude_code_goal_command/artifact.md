# Goal

The benchmark artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v4/portfolio-std-metrics-claude_code_goal_command.md` and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition.

Task: Reconcile the June revenue between two documents. Read exactly `data-ledger.md` (source of truth) and `finance-report.md`. Identify the numeric discrepancy, quote both values, and state the mechanical cause using the derivation notes.

# Result

- Data ledger (source of truth) net revenue for June: **45,930** (JPY thousands).
- Finance report revenue for June: **48,210** (JPY thousands).
- Discrepancy: 48,210 − 45,930 = **2,280** (JPY thousands).
- Mechanical cause (confirmed): the finance report's revenue query sums all settled orders but does not filter out orders flagged as refunded, so it reports the gross settled total instead of the net-of-refunds total. The ledger's derivation note shows the correct calculation is settled 48,210 minus refunded 2,280 = 45,930, and the finance report's own text confirms it does not apply the refund-flag filter.

# Evidence

From `data-ledger.md`:
- "| Net revenue (JPY thousands) | 45,930 | settled 48,210 minus refunded 2,280 |"
- "Derivation rules: revenue counts settled orders net of refunds; ..."

From `finance-report.md`:
- "Revenue for June: 48,210 (JPY thousands)."
- "The revenue query sums all settled orders in June. Refunded orders remain in the settled table with a refund flag; the June query does not filter on the refund flag."

Confirmed finding: The 2,280 gap (48,210 − 45,930) exactly equals the "refunded 2,280" figure named in the ledger's derivation note for net revenue, and the finance report's own description ("does not filter on the refund flag") mechanically explains why refunded orders remain included in its total.

Rejected candidates: None — no alternative discrepancy causes (e.g., currency conversion, DAU/conversion metric differences, formatting-only differences such as the space-separated thousands format noted in Footnote F-1) are implicated, since the finance report text explicitly and solely attributes its figure to the unfiltered settled-orders query.

# Assumptions

- The data ledger's `data-ledger.md` is treated as the authoritative source of truth per the task prompt, and its derivation note is treated as the ground-truth calculation method.
- No other data source (e.g., a board deck, USD conversion, DAU, or conversion metrics) was consulted, per the task's read-scope restriction to the two named fixtures only.
- Whether the finance report's 48,210 figure is otherwise "correct" for a different definition of revenue (gross settled, not net) is unmeasured — this artifact only confirms the mechanical/numeric cause of the discrepancy between the two documents' stated figures.

# Stop Condition

This artifact exists at the required path and includes all five required headings (Goal, Result, Evidence, Assumptions, Stop Condition), quotes both revenue values (45,930 and 48,210) verbatim from the fixtures, and states the mechanical cause (unfiltered refund flag in the finance report's settled-orders query) with supporting quotes. No further edits are planned; the task is complete.
