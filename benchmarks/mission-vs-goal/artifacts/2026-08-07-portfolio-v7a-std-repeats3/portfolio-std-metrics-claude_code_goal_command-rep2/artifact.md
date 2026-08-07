# portfolio-std-metrics — June Revenue Reconciliation (claude_code_goal_command / rep2)

## Goal

Reconcile the June revenue between two documents, using the data ledger as the source of truth. Identify the numeric discrepancy, quote both values, and state the mechanical cause using the derivation notes. Read exactly these fixtures:

- `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/data-ledger.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/finance-report.md`

## Result

**Confirmed discrepancy**: June revenue differs by **2,280 (JPY thousands)** between the two documents.

- Data ledger (source of truth): **45,930** (JPY thousands, net revenue)
- Finance report: **48,210** (JPY thousands)

**Mechanical cause**: The finance report's revenue query sums all settled orders without filtering out refunded orders. The ledger's derivation rule counts settled orders **net of refunds** (48,210 settled − 2,280 refunded = 45,930), while the finance report reports the gross settled total 48,210 because "the June query does not filter on the refund flag". The difference of 2,280 equals exactly the refunded amount.

## Evidence

Confirmed findings, each with an exact quote from the fixtures:

1. Ledger net revenue value — `data-ledger.md` table row:
   > `| Net revenue (JPY thousands) | 45,930 | settled 48,210 minus refunded 2,280 |`
2. Ledger derivation rule — `data-ledger.md`:
   > "revenue counts settled orders net of refunds"
3. Finance report revenue value — `finance-report.md`:
   > "Revenue for June: 48,210 (JPY thousands)."
4. Finance report mechanical cause — `finance-report.md`:
   > "The revenue query sums all settled orders in June. Refunded orders remain in the settled table with a refund flag; the June query does not filter on the refund flag."
5. Arithmetic check: 48,210 − 2,280 = 45,930, matching the ledger's derivation note "settled 48,210 minus refunded 2,280". Discrepancy = 48,210 − 45,930 = 2,280 (JPY thousands), equal to the refunded amount quoted in the ledger.

Rejected candidate explanations (not the cause of the revenue discrepancy):

- **Thousands-separator formatting**: `data-ledger.md` Footnote F-1 states "some downstream documents print thousands separators as spaces (45 930). This is formatting only, not a data difference." The observed gap is a numeric difference (2,280), not a formatting artifact — rejected.
- **Currency/rate conversion**: Both quoted figures are in JPY thousands. The USD figure ("USD revenue (thousands) | 295.0 | 45,930 / 155.7") is a separate derived metric; the finance report defers USD to the board deck ("USD reporting: see the board deck for the converted figure") — rejected as a cause.
- **Conversion-metric definition (per unique user 2.6% vs per session 3.4%)**: relates to the conversion metric, not revenue — rejected as a cause of the revenue discrepancy.

## Assumptions

- The data ledger is the source of truth, as instructed by the task prompt and stated in the ledger's own title ("Data Ledger (source of truth)").
- Both revenue figures refer to June 2026 in JPY thousands, as stated in each document.
- Only the two named fixture files were read; no other benchmark files were consulted. Whether the finance report's underlying query would reproduce 48,210 against the raw orders table is unmeasured — the reconciliation relies solely on the figures and derivation notes quoted above.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v7a-std-repeats3/portfolio-std-metrics-claude_code_goal_command-rep2.md` and contains the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), quotes both revenue values (45,930 and 48,210 JPY thousands), and states the mechanical cause (the finance report's query does not filter refunded orders, so it reports gross settled 48,210 instead of net 45,930). The task validator's requirements are satisfied; work stops here. No commits, pushes, package installs, or network access were performed.
