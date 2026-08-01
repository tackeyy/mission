# Portfolio Std Metrics — June Revenue Reconciliation

## Goal
The benchmark artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v2/portfolio-std-metrics-claude_code_goal_command.md` and includes headings Goal, Result, Evidence, Assumptions, and Stop Condition. Task: reconcile the June revenue figures reported in `data-ledger.md` (source of truth) and `finance-report.md`, quoting both values and stating the mechanical cause of the discrepancy per the derivation notes.

## Result
There is a discrepancy between the two documents' June revenue figures.

- Data ledger (source of truth) reports **Net revenue (JPY thousands): 45,930**.
- Finance report reports **Revenue for June: 48,210 (JPY thousands)**.
- Difference: 48,210 − 45,930 = 2,280 (JPY thousands).

**Mechanical cause**: The finance report's revenue query sums all settled orders in June without excluding refunded orders. Refunded orders remain in the settled table but are flagged separately, and the finance report's query does not filter on that refund flag — so it captures gross settled revenue (48,210) rather than net revenue after refunds. The data ledger applies the correct derivation: settled revenue minus refunds (48,210 − 2,280 = 45,930).

## Evidence
- Data ledger, "Net revenue (JPY thousands)" row: `45,930` with derivation note `settled 48,210 minus refunded 2,280`.
- Data ledger derivation rules (top of file): "revenue counts settled orders net of refunds".
- Finance report: `Revenue for June: 48,210 (JPY thousands).`
- Finance report explanation: "The revenue query sums all settled orders in June. Refunded orders remain in the settled table with a refund flag; the June query does not filter on the refund flag."

These quotes together show the finance report's 48,210 figure equals the ledger's pre-refund settled total (48,210), confirming the refund-flag filtering omission as the mechanical cause, rather than a different data source or time period.

## Assumptions
- The data ledger is treated as the source of truth per task instructions, so its net revenue value (45,930) is the correct June figure and the finance report's figure (48,210) is the erroneous/incomplete one.
- No other fixtures or systems were consulted; this reconciliation is based solely on the two named fixture files. USD conversion, DAU, and conversion-rate figures in the ledger are unmeasured/not applicable to this task and are not claimed here.
- Only the two named fixture files were read; no other files under `benchmarks/mission-vs-goal/` were opened, per task constraints.

## Stop Condition
This artifact satisfies the stop condition: it exists at the required path, contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition, quotes both revenue values (45,930 and 48,210), and states the mechanical cause of the discrepancy (finance report's settled-orders query not filtering the refund flag, per the ledger's derivation note). No commits, pushes, package installs, or network access were performed; no other benchmark files were read or modified.
