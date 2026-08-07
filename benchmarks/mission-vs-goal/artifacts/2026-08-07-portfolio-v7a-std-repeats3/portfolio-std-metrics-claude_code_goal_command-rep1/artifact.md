# portfolio-std-metrics — June Revenue Reconciliation (claude_code_goal_command / rep1)

## Goal

Reconcile the June revenue between the two named fixtures, with the data ledger as
the source of truth: identify the numeric discrepancy, quote both values, and state
the mechanical cause using the derivation notes.

- Task id: `portfolio-std-metrics`
- Arm: `claude_code_goal_command` (Claude Code built-in `/goal` command as completion controller)
- Fixtures read (exactly these two):
  - `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/data-ledger.md`
  - `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/finance-report.md`

## Result

**Confirmed discrepancy — June revenue (JPY thousands):**

- Data ledger (source of truth): net revenue **45,930**
- Finance report: revenue **48,210**
- Difference: 48,210 − 45,930 = **2,280** (JPY thousands), which equals the refunded amount recorded in the ledger.

**Mechanical cause:** The finance report's revenue query sums all settled orders
without filtering out refund-flagged orders, so it reports **gross settled revenue
(48,210)**. The ledger's definition of revenue is net of refunds
(settled 48,210 minus refunded 2,280 = 45,930). The 2,280 gap is exactly the
unfiltered refunded amount.

### Rejected candidates (not the cause of the discrepancy)

- **Thousands-separator formatting** — ledger Footnote F-1 states: "some downstream
  documents print thousands separators as spaces (45 930). This is formatting only,
  not a data difference." Rejected as a cause.
- **Currency/FX conversion** — the ledger's USD figure ("USD revenue (thousands) | 295.0 | 45,930 / 155.7")
  is a separate derived metric; the finance report defers USD to the board deck
  ("USD reporting: see the board deck for the converted figure"). Both revenue figures
  compared here are in JPY thousands, so FX is not involved. Rejected.
- **DAU / conversion metrics** — the ledger's DAU (11,987) and conversion figures
  (2.6% per user, 3.4% per session) do not appear in the finance report's revenue
  claim and are unrelated to the revenue discrepancy. Rejected.

## Evidence

All quotes are verbatim from the two fixture files.

1. Ledger net revenue value and derivation
   (`data-ledger.md`, metrics table row):
   > `| Net revenue (JPY thousands) | 45,930 | settled 48,210 minus refunded 2,280 |`
2. Ledger derivation rule defining revenue
   (`data-ledger.md`, derivation rules):
   > "revenue counts settled orders net of refunds"
3. Finance report revenue value (`finance-report.md`):
   > "Revenue for June: 48,210 (JPY thousands)."
4. Finance report's query mechanics — the mechanical cause (`finance-report.md`):
   > "The revenue query sums all settled orders in June. Refunded orders remain in
   > the settled table with a refund flag; the June query does not filter on the
   > refund flag."
5. Formatting footnote used to reject the separator candidate
   (`data-ledger.md`, Footnote F-1):
   > "some downstream documents print thousands separators as spaces (45 930).
   > This is formatting only, not a data difference."
6. Arithmetic check (computed locally from the quoted values, not printed in either
   fixture as a standalone delta): 48,210 − 45,930 = 2,280, matching the ledger's
   "refunded 2,280".

## Assumptions

- The data ledger is authoritative, as directed by the task prompt ("The data ledger
  is the source of truth") and the ledger's own title ("Data Ledger (source of truth)").
- Both revenue figures are in the same unit, JPY thousands, as each document states
  explicitly; no unit conversion was needed.
- The delta of 2,280 was computed by me from the two quoted values; the fixtures do
  not print the difference as a standalone number (the ledger states the components
  "settled 48,210 minus refunded 2,280").
- No other files under `benchmarks/mission-vs-goal/` were opened, listed, or
  searched; only the two named fixtures and this output file were touched.
  Anything beyond the two fixtures (e.g. the actual settled-orders table or the
  board deck) is unmeasured and was not consulted.

## Stop Condition

This artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-07-portfolio-v7a-std-repeats3/portfolio-std-metrics-claude_code_goal_command-rep1.md`
and contains the required headings (Goal, Result, Evidence, Assumptions, Stop
Condition), both revenue values quoted verbatim (45,930 and 48,210 in JPY
thousands), and the mechanical cause (refund-flagged orders not filtered from the
settled-orders sum). The task validator's requirements are satisfied, so the run
stops here. No commits, pushes, package installs, or network access were performed.
