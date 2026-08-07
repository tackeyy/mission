# portfolio-std-metrics — June Revenue Reconciliation (claude_code_goal_command / rep3)

## Goal

Reconcile the June revenue between the two specified fixture documents, identify the numeric discrepancy, quote both values, and state the mechanical cause using the derivation notes.

- Fixtures read (exactly these two, as instructed):
  - `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/data-ledger.md` (source of truth)
  - `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/finance-report.md`

## Result

**Confirmed discrepancy — June revenue (JPY thousands): 45,930 (data ledger) vs 48,210 (finance report). Difference: 2,280 (JPY thousands).**

- Data ledger (source of truth): "Net revenue (JPY thousands) | 45,930"
- Finance report: "Revenue for June: 48,210 (JPY thousands)."

**Mechanical cause:** The finance report's revenue query sums all settled orders without filtering out refunded orders. The ledger's derivation rule states revenue "counts settled orders net of refunds" and its derivation note shows "settled 48,210 minus refunded 2,280" = 45,930. The finance report itself states: "Refunded orders remain in the settled table with a refund flag; the June query does not filter on the refund flag." Therefore the report includes the 2,280 (JPY thousands) of refunded orders that the ledger correctly subtracts, producing the gross figure 48,210 instead of the net figure 45,930.

### Confirmed findings

1. Ledger net revenue value: `45,930` (JPY thousands) — quoted from ledger table row "Net revenue (JPY thousands) | 45,930 | settled 48,210 minus refunded 2,280".
2. Finance report revenue value: `48,210` (JPY thousands) — quoted from "Revenue for June: 48,210 (JPY thousands)."
3. Discrepancy amount: `2,280` (JPY thousands), exactly matching the ledger's "refunded 2,280" derivation note.
4. Mechanical cause: missing refund-flag filter in the finance report's June revenue query ("the June query does not filter on the refund flag"), so refunded orders are not subtracted.

### Rejected candidates (not the cause)

- **Thousands-separator formatting**: Ledger Footnote F-1 says "some downstream documents print thousands separators as spaces (45 930). This is formatting only, not a data difference." Rejected — the observed gap is a real numeric difference of 2,280, not formatting.
- **USD conversion**: The ledger's USD figure ("USD revenue (thousands) | 295.0 | 45,930 / 155.7") is not involved; the finance report defers USD to the board deck ("see the board deck for the converted figure"). Both compared values are in JPY thousands, so exchange-rate handling is not the cause.
- **DAU / conversion metric definitions** (JST boundary, per-user vs per-session conversion): unrelated to the revenue line; no revenue impact.

## Evidence

Direct quotes from the fixtures:

From `data-ledger.md`:
- "Derivation rules: revenue counts settled orders net of refunds"
- "| Net revenue (JPY thousands) | 45,930 | settled 48,210 minus refunded 2,280 |"
- "Footnote F-1: some downstream documents print thousands separators as spaces (45 930). This is formatting only, not a data difference."

From `finance-report.md`:
- "Revenue for June: 48,210 (JPY thousands)."
- "The revenue query sums all settled orders in June. Refunded orders remain in the settled table with a refund flag; the June query does not filter on the refund flag."

Arithmetic check: 48,210 − 2,280 = 45,930 (matches the ledger's net figure exactly).

## Assumptions

- The data ledger is the source of truth, as stated in the task prompt and in the ledger's own title ("source of truth").
- Both revenue figures are in the same unit (JPY thousands), as each document states explicitly; no unit conversion was needed or performed.
- Only the two named fixture files were read; no other files under `benchmarks/mission-vs-goal/` were opened. Whether the finance report's query behavior reproduces against any underlying database is unmeasured — the cause is stated from the documents' own derivation notes, which is what the task requires.

## Stop Condition

This artifact exists at the required path, includes the required headings (Goal, Result, Evidence, Assumptions, Stop Condition), quotes both revenue values (45,930 and 48,210, JPY thousands), and states the mechanical cause (the finance report's June query does not filter on the refund flag, so refunded orders of 2,280 are not subtracted). Task complete; no further edits are needed.
