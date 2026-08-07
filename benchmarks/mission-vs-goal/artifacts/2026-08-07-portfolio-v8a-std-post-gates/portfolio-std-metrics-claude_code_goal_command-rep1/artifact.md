# portfolio-std-metrics — June Revenue Reconciliation (claude_code_goal_command / rep1)

## Goal

Reconcile the June revenue between the two named fixtures, identify the numeric
discrepancy, quote both values, and state the mechanical cause using the
derivation notes. The data ledger is the source of truth.

Fixtures read (exactly these two):
- `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/data-ledger.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/finance-report.md`

## Result

**Confirmed discrepancy**: June revenue differs by **2,280 (JPY thousands)**.

- Data ledger (source of truth): **Net revenue (JPY thousands) = 45,930**
- Finance report: **"Revenue for June: 48,210 (JPY thousands)"**
- Difference: 48,210 − 45,930 = **2,280**, which equals the refunded amount in the ledger derivation note.

**Mechanical cause**: The finance report's revenue query sums all settled orders
without filtering out refunded orders. Refunded orders remain in the settled
table with a refund flag, and the June query does not apply that flag, so the
report shows gross settled (48,210) instead of net of refunds (45,930 = 48,210 − 2,280),
which is the ledger's derivation rule ("revenue counts settled orders net of refunds").

## Evidence

From `data-ledger.md` (source of truth):

- Derivation rule: "revenue counts settled orders net of refunds"
- Table row: `| Net revenue (JPY thousands) | 45,930 | settled 48,210 minus refunded 2,280 |`

From `finance-report.md`:

- "Revenue for June: 48,210 (JPY thousands)."
- Mechanical cause stated in the report itself: "The revenue query sums all
  settled orders in June. Refunded orders remain in the settled table with a
  refund flag; the June query does not filter on the refund flag."

Arithmetic check: 48,210 − 2,280 = 45,930, matching the ledger's net figure exactly.

## Assumptions

- Both figures are in the same unit (JPY thousands), as each document states
  explicitly, so the comparison is unit-consistent. No unit conversion was needed.
- Footnote F-1 in the ledger notes that some downstream documents print
  thousands separators as spaces ("45 930") and that this "is formatting only,
  not a data difference." The finance report uses comma separators (48,210), so
  no formatting-only discrepancy is involved here; the 2,280 gap is a real data
  difference. (Rejected candidate: the space-separator formatting difference of
  Footnote F-1 is explicitly not the cause.)
- The USD figure (295.0 thousands in the ledger) was not compared: the finance
  report defers USD to "the board deck," which is not among the fixtures, so
  any USD-side discrepancy is unmeasured.
- Other metrics (Peak DAU 11,987; conversion 2.6% per unique user vs 3.4% per
  session) appear only in the ledger, not in the finance report, so they were
  not reconciled (unmeasured for this task).

## Stop Condition

This artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8a-std-post-gates/portfolio-std-metrics-claude_code_goal_command-rep1.md`
and contains the required headings (Goal, Result, Evidence, Assumptions, Stop
Condition), quotes both revenue values (45,930 and 48,210, JPY thousands), and
states the mechanical cause (June query does not filter the refund flag, so
gross settled is reported instead of net of refunds). No commits, pushes,
package installs, or network access were performed; edits were limited to this
single output file. Task complete — stopping.
