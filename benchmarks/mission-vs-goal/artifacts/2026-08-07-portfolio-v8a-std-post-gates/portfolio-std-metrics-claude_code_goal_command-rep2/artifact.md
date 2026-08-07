# portfolio-std-metrics — June Revenue Reconciliation (claude_code_goal_command / rep2)

## Goal

Reconcile the June revenue between the two named fixtures, identify the numeric
discrepancy, quote both values, and state the mechanical cause using the
derivation notes. Fixtures read (and only these):

- `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/data-ledger.md` (source of truth)
- `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/finance-report.md`

## Result

**Confirmed discrepancy: June revenue differs by 2,280 (JPY thousands).**

- Data ledger (source of truth): **45,930** JPY thousands — quoted: "Net revenue (JPY thousands) | 45,930"
- Finance report: **48,210** JPY thousands — quoted: "Revenue for June: 48,210 (JPY thousands)."
- Difference: 48,210 − 45,930 = **2,280** JPY thousands.

**Mechanical cause:** The ledger derivation note states net revenue is
"settled 48,210 minus refunded 2,280". The finance report's own derivation
note explains: "The revenue query sums all settled orders in June. Refunded
orders remain in the settled table with a refund flag; the June query does not
filter on the refund flag." The finance report therefore reports gross settled
revenue (48,210) including the refunded 2,280, instead of net revenue (45,930)
as defined by the ledger's rule that "revenue counts settled orders net of
refunds".

### Rejected candidates

- **Thousands-separator formatting (spaces vs commas)**: rejected. Ledger
  Footnote F-1 states "some downstream documents print thousands separators as
  spaces (45 930). This is formatting only, not a data difference." It is not
  the cause of the 2,280 numeric gap.
- **USD conversion difference**: rejected as a June JPY revenue discrepancy.
  The finance report contains no USD figure ("USD reporting: see the board
  deck for the converted figure."), so there is nothing to reconcile against
  the ledger's "USD revenue (thousands) | 295.0". Unmeasured here.
- **DAU / conversion metrics**: out of scope for this task (revenue only);
  no revenue-relevant conflict observed in the two fixtures.

## Evidence

All quotes are verbatim from the two fixture files read via the Read tool.

| Claim | Fixture | Exact quote |
|---|---|---|
| Ledger net revenue is 45,930 | data-ledger.md | "Net revenue (JPY thousands) \| 45,930 \| settled 48,210 minus refunded 2,280" |
| Ledger revenue rule is net of refunds | data-ledger.md | "revenue counts settled orders net of refunds" |
| Report revenue is 48,210 | finance-report.md | "Revenue for June: 48,210 (JPY thousands)." |
| Report query omits refund filter | finance-report.md | "The revenue query sums all settled orders in June. Refunded orders remain in the settled table with a refund flag; the June query does not filter on the refund flag." |
| Space separator is formatting only | data-ledger.md | "Footnote F-1: some downstream documents print thousands separators as spaces (45 930). This is formatting only, not a data difference." |

Arithmetic check (performed manually, shown for verification):
48,210 − 2,280 = 45,930, which matches the ledger's net revenue exactly.

## Assumptions

- The data ledger is the source of truth, as directed by the task prompt and
  the ledger's own title "June 2026 Data Ledger (source of truth)".
- Both documents refer to the same June 2026 period and the same JPY-thousands
  unit, as stated in each file's text.
- No other documents or systems were consulted; whether the board deck's USD
  figure matches the ledger's 295.0 is unmeasured.

## Stop Condition

This artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v8a-std-post-gates/portfolio-std-metrics-claude_code_goal_command-rep2.md`
and contains the required headings (Goal, Result, Evidence, Assumptions, Stop
Condition), quotes both revenue values (45,930 and 48,210), and states the
mechanical cause (the finance report's query does not filter refunded orders,
so it reports gross settled revenue instead of net). No commits, pushes,
package installs, or network access were performed; edits were limited to this
single output file. Task complete — stopping.
