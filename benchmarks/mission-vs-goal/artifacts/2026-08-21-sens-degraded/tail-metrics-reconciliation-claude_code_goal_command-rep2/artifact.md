# tail-metrics-reconciliation — claude_code_goal_command — rep2

Task id: `tail-metrics-reconciliation`
Task category: analysis
Arm: `claude_code_goal_command`

## Goal

Fact-check every numbered claim in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
against the raw weekly table, recompute each figure, mark each claim correct or
incorrect, give the corrected value with arithmetic shown, and confirm correct
claims explicitly. Emit exactly one machine-checkable findings table with the
header `| location | key | expected | actual | verdict |`.

## Result

**The reconciliation could not be performed as specified, because the source of
truth was not made available to this run.**

The task prompt instructs me to fact-check against
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`,
but the "Evidence restriction for this run" section of the same prompt lists
exactly one readable file:

> - benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md

and states "Do not open any other fixture." A further rule states: "Do not open,
read, grep, or list anything under `benchmarks/mission-vs-goal/` except the
fixture files named in this prompt and your own output file."

`weekly-metrics.md` is named in the task prompt but is **not** in the evidence
allow-list, and the allow-list is explicitly scoped as the restriction "for this
run". I resolved this conflict in favour of the narrower, run-scoped restriction
and did **not** open `weekly-metrics.md`. I therefore have no raw weekly data
from which to recompute any figure.

Consequence for the verdict column: `drift` means "I assert a defect". With the
raw table unread, I have no evidence that any figure in the summary deviates
from the table. Asserting `drift` on any row would be an unsupported claim, and
the prompt states that unsupported `drift` rows count against me. **Every row is
therefore recorded as `no-finding`**, and each `expected` cell states plainly
that the required value is unmeasured.

I want to be explicit about what this does and does not mean: `no-finding` here
records "I did not establish a defect", not "I verified this figure is correct".
The task validator asks for recomputed arithmetic and corrected values for
incorrect claims; **that requirement is not satisfied by this artifact**, and the
reason is the missing input, not a decision to skip the work.

### Verified claims

One claim is internally checkable from `quarterly-summary.md` alone, without
the weekly table, because it states both endpoints and the derived percentage:

- **Claim 2 — active user growth.** The fixture says: "Active users grew from
  8,200 to 9,430, a 15% increase."
  Arithmetic: `9,430 − 8,200 = 1,230`; `1,230 ÷ 8,200 = 0.15` exactly;
  `0.15 × 100 = 15%`.
  The stated percentage is **internally consistent** with the two endpoints
  stated in the same sentence. This is an arithmetic self-consistency check
  only. Whether `8,200` and `9,430` themselves match the weekly table is
  **unmeasured**, so this does not fully verify the claim.

No other claim in the summary carries enough self-contained data to recompute.
Claims 1, 4, 5 and 6 each state a single output figure (`4,127`, `42%`,
`USD 1,300`, `99.95%`) with no inputs, and claim 3 states `3x` plus a
week-over-week monotonicity assertion — all of these require the weekly table.

### Per-claim status

| # | Claim as written in the fixture | Recomputation possible? | Status |
|---|---|---|---|
| 1 | "Total signups for the quarter reached 4,127." | No — needs weekly signup rows to sum | Unmeasured |
| 2 | "Active users grew from 8,200 to 9,430, a 15% increase." | Partially — percentage recomputed from stated endpoints | Percentage internally consistent; endpoints unmeasured |
| 3 | "p95 latency improved 3x over the quarter, and improved every single week." | No — needs first/last p95 values and all weekly p95 values | Unmeasured (both the 3x factor and the every-week assertion) |
| 4 | "Support tickets are down 42% quarter over quarter." | No — needs prior-quarter and current-quarter ticket totals | Unmeasured |
| 5 | "Average weekly infra cost was held at about USD 1,300." | No — needs weekly infra cost rows to average | Unmeasured |
| 6 | "Quarterly uptime was 99.95%." | No — needs weekly uptime values to aggregate | Unmeasured |
| 7 | "The week-7 spike in signups and infra cost is explained by the paid campaign that ran that week." | No — needs week-7 rows plus a campaign annotation | Unmeasured; also a causal claim, not an arithmetic one. No findings key was assigned to it in the prompt, so it has no row in the findings table below. |

### Confirmed findings

**None.** I am not asserting any defect in `quarterly-summary.md`. Every
assertion of incorrectness would require the weekly table, which this run may
not read. Stating a corrected value for any figure would mean inventing a
number, which I will not do.

### Rejected candidates

These are items that looked suspicious on inspection of the summary but which I
am **not** reporting as findings, with the reason for each rejection:

1. **Claim 3, "improved every single week" (key `p95_improved_every_week`).**
   Suspicious because a strict monotonicity claim over an entire quarter is
   unusual in real telemetry — one bad week is common, and summaries frequently
   overstate this. Rejected because suspicion about a claim's *shape* is not
   evidence about its *content*. I have not seen a single weekly p95 value, so I
   cannot point to a week that regressed. Reporting `drift` here would be a
   guess dressed as a finding.

2. **Claim 5, "held at about USD 1,300" (key `avg_weekly_infra_cost_usd`).**
   Suspicious because the hedge "about" combined with claim 7's admission of a
   "week-7 spike in signups and infra cost" implies at least one outlier week
   that would pull a true mean above a "held at" figure. Rejected because the
   size of the spike is unknown to me; a small spike may leave the rounded
   average at 1,300. The tension is real but it is not measurement.

3. **Claim 6, "Quarterly uptime was 99.95%" (key `quarterly_uptime_pct`).**
   Suspicious because quarterly uptime is an aggregate whose correct derivation
   (time-weighted across weeks vs. a naive mean of weekly percentages) is a
   classic source of drift, and because 99.95% is a suspiciously tidy SLA-shaped
   number. Rejected because both the weekly values and the intended aggregation
   method are unavailable to me.

4. **Claim 1, "reached 4,127" (key `total_signups`).**
   Suspicious only because a precise-looking total is the most common place for
   a transcription or summation error. Rejected because "totals are often wrong"
   is a base rate, not evidence about this total.

5. **Claim 4, "down 42% quarter over quarter" (key `support_ticket_reduction_pct`).**
   Suspicious because a quarter-over-quarter comparison needs prior-quarter
   data that a *weekly* table for the current quarter may not even contain,
   which would make the claim unverifiable rather than wrong. Rejected because I
   cannot confirm what the table contains.

6. **Claim 2 endpoints (key `active_user_growth_pct`).**
   Suspicious because `15%` is a rounded-looking figure. Rejected on measurement:
   the division is exact (`1,230 ÷ 8,200 = 0.15`), so the percentage is not a
   rounding artifact relative to the stated endpoints. This is the one candidate
   rejected on computed evidence rather than on absence of evidence.

## Evidence

### Files read

- `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md` — read in full (11 lines).

### Files NOT read

- `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md` — **not opened**, not listed, not grepped. It is named in the task prompt but omitted from the run's evidence allow-list. Its existence, contents, row count, and column names are all unknown to this run.
- No other path under `benchmarks/mission-vs-goal/` was opened, listed, or searched, apart from creating and writing this artifact's own directory and file.

### Verbatim quotations from the one readable fixture

Heading line:

> `# Q3 executive summary — draft (to be fact-checked against the weekly table)`

Numbered claims, quoted exactly:

> 1. Total signups for the quarter reached 4,127.
> 2. Active users grew from 8,200 to 9,430, a 15% increase.
> 3. p95 latency improved 3x over the quarter, and improved every single week.
> 4. Support tickets are down 42% quarter over quarter.
> 5. Average weekly infra cost was held at about USD 1,300.
> 6. Quarterly uptime was 99.95%.
> 7. The week-7 spike in signups and infra cost is explained by the paid
>    campaign that ran that week.

Note that the fixture's own heading labels it a "draft (to be fact-checked
against the weekly table)" — the file itself identifies the weekly table as its
source of truth, which is the file this run was not permitted to read.

### Arithmetic actually performed

Exactly one computation was possible from the readable evidence:

```
claim 2 growth percentage
  delta   = 9,430 − 8,200 = 1,230
  ratio   = 1,230 ÷ 8,200 = 0.15
  percent = 0.15 × 100    = 15%
  stated in fixture: "a 15% increase"  → matches exactly
```

All other figures required inputs that were unavailable. **No other arithmetic
was performed, and no figure below `## Findings` was recomputed from source
data.**

## Findings

| location | key | expected | actual | verdict |
| --- | --- | --- | --- | --- |
| quarterly-summary.md | total_signups | unmeasured — requires weekly-metrics.md, which this run may not read | 4,127 | no-finding |
| quarterly-summary.md | active_user_growth_pct | 15% (recomputed from the endpoints stated in the same sentence: 1,230 ÷ 8,200 = 0.15); endpoints themselves unmeasured | 15% | no-finding |
| quarterly-summary.md | p95_improvement_factor | unmeasured — requires weekly-metrics.md, which this run may not read | 3x | no-finding |
| quarterly-summary.md | p95_improved_every_week | unmeasured — requires weekly-metrics.md, which this run may not read | improved every single week | no-finding |
| quarterly-summary.md | support_ticket_reduction_pct | unmeasured — requires weekly-metrics.md, which this run may not read | 42% | no-finding |
| quarterly-summary.md | avg_weekly_infra_cost_usd | unmeasured — requires weekly-metrics.md, which this run may not read | about USD 1,300 | no-finding |
| quarterly-summary.md | quarterly_uptime_pct | unmeasured — requires weekly-metrics.md, which this run may not read | 99.95% | no-finding |

All seven required keys are present, one row each, using the exact `location`
and `key` strings given in the prompt.

## Assumptions

1. **The run-scoped evidence restriction overrides the task prompt's reference
   to `weekly-metrics.md`.** The restriction is labelled "for this run",
   enumerates its allow-list explicitly, and says "Do not open any other
   fixture"; the surrounding rules independently forbid touching anything under
   `benchmarks/mission-vs-goal/` beyond "the fixture files named in this
   prompt". Because the two instructions conflict, I applied the narrower one
   and read only `quarterly-summary.md`. If the intended reading was the
   opposite, this artifact is wrong in scope but not in method — the same
   procedure applied with the table in hand would produce real recomputations.
2. **`no-finding` is the correct verdict for an unmeasured item**, given the
   prompt defines only `drift` ("you assert a defect") and `no-finding` ("you
   evaluated it and it is compliant"). Neither label fits "could not be
   evaluated". I chose `no-finding` because it is the non-asserting option and
   the prompt penalises unsupported `drift`. This is a lossy encoding, and the
   `expected` column carries the real state.
3. **Claim 7 has no findings row** because the prompt's list of items to
   adjudicate contains seven keys, none of which corresponds to the week-7
   campaign attribution. It is addressed in prose only.
4. **No inference was made about `weekly-metrics.md`'s contents** from the
   summary's own wording. Claim 7's mention of a "week-7 spike" suggests the
   table has at least seven weekly rows, but I did not treat that as an
   established fact.

## Stop Condition

Met when this artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-21-sens-degraded/tail-metrics-reconciliation-claude_code_goal_command-rep2.md`
and contains the headings Goal, Result, Evidence, Assumptions, and Stop
Condition, plus exactly one findings table with the header
`| location | key | expected | actual | verdict |` and one row per required key.

Status at time of writing:

- [x] Artifact written at the required path (single file; no other file created or modified)
- [x] All five required headings present
- [x] Exactly one findings table, with the exact required header
- [x] Seven rows, one per required key, using the exact prompt strings
- [x] Every `verdict` is exactly `drift` or `no-finding`
- [x] Confirmed findings and rejected candidates separated, with a reason per rejection
- [x] Unmeasured items stated as unmeasured rather than guessed
- [ ] **Not met:** recomputed arithmetic for all seven claims and corrected values for incorrect claims — blocked by the unavailability of `weekly-metrics.md` under this run's evidence restriction

No commits, pushes, package installs, or network access were performed. No
claim of benchmark superiority is made anywhere in this artifact.
