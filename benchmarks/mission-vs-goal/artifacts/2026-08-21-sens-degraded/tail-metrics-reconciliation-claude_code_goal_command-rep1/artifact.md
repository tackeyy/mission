# tail-metrics-reconciliation — claude_code_goal_command — rep1

## Goal

Fact-check every numbered claim in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
against the raw weekly table, recompute each figure, mark each claim correct or
incorrect, and give corrected values with arithmetic shown.

## Result

**No claim could be verified or refuted.** The task's own evidence restriction
for this run permits reading exactly one file:

> - benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md

`weekly-metrics.md` — the raw table that is the sole source of truth for every
recomputation — is **not** on the permitted list, and the run rules additionally
forbid opening anything under `benchmarks/mission-vs-goal/` other than the
fixture files named in the prompt. I did not open it.

Consequently **all seven figures are unmeasured**. There is no arithmetic to
show, because there are no inputs: signup counts, active-user counts, p95
latency series, ticket counts, weekly infra costs, and weekly uptime all live in
the withheld table. Recomputation is arithmetically impossible from the summary
alone — the summary states conclusions, not the operands.

I am therefore **not asserting any drift**, and I am equally **not confirming
any claim as correct**. Every findings row is `no-finding` in the narrow,
literal sense that I am asserting no defect; this is *not* a positive assertion
of compliance. The prose below is the authoritative reading of those rows.

This means the task validator ("verify all seven claims with recomputed
arithmetic") **cannot be satisfied** under this run's evidence restriction. I am
reporting that blocker rather than fabricating arithmetic.

### Confirmed findings

None. Zero claims were confirmed as incorrect, because confirming incorrectness
requires the raw table.

### Verified-correct claims

None. Zero claims were confirmed as correct, because confirming correctness also
requires the raw table. Marking a claim "correct" on the strength of it merely
appearing in the summary would be circular.

### Rejected candidates (looked suspicious, not asserted as findings)

These are internal-consistency observations from the summary text alone. Each is
a reason one *might* have guessed at drift; none is supported by evidence, so
none is reported as `drift`.

1. **Claim 2, `active_user_growth_pct`.** The summary says *"Active users grew
   from 8,200 to 9,430, a 15% increase."* This is the only claim carrying its own
   operands, so it is the one case where a partial check is possible:
   (9,430 − 8,200) / 8,200 = 1,230 / 8,200 = 0.15000 = **15.00%**. The stated
   percentage is internally consistent with the stated endpoints. Rejected as a
   finding because the *endpoints themselves* (8,200 and 9,430) are unverified —
   they come from the withheld table, so a correct percentage over wrong operands
   would still be a defect I cannot see. I therefore do not confirm it correct
   either.
2. **Claim 3, `p95_improved_every_week` / `p95_improvement_factor`.** *"p95
   latency improved 3x over the quarter, and improved every single week."* A
   universally-quantified per-week claim conjoined with an aggregate factor is a
   classic drift site — a single non-monotonic week falsifies the second half
   while leaving the first half intact. Rejected because detecting a
   non-monotonic week requires the weekly p95 series.
3. **Claim 7, week-7 spike.** *"The week-7 spike in signups and infra cost is
   explained by the paid campaign that ran that week."* This asserts both the
   existence of a week-7 spike and a causal attribution. Rejected because the
   spike's existence is a table fact, and the causal attribution is not derivable
   from any table of metrics even if I had it. Note this claim is also absent
   from the seven adjudication keys, so it has no findings row.
4. **Claim 5, `avg_weekly_infra_cost_usd`.** *"held at about USD 1,300."* The
   hedge *"about"* has no stated tolerance, so even with the table the verdict
   would depend on an unstated rounding rule. Rejected on top of the evidence
   blocker.
5. **Claim 6, `quarterly_uptime_pct`.** *"Quarterly uptime was 99.95%."*
   Aggregating weekly uptime to a quarterly figure admits several defensible
   methods (unweighted mean of weekly percentages vs. total-uptime-minutes over
   total-minutes), which can differ in the fourth significant digit. Rejected on
   top of the evidence blocker.

## Evidence

Only file read during this run (full contents, quoted verbatim):

`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`

```
# Q3 executive summary — draft (to be fact-checked against the weekly table)

1. Total signups for the quarter reached 4,127.
2. Active users grew from 8,200 to 9,430, a 15% increase.
3. p95 latency improved 3x over the quarter, and improved every single week.
4. Support tickets are down 42% quarter over quarter.
5. Average weekly infra cost was held at about USD 1,300.
6. Quarterly uptime was 99.95%.
7. The week-7 spike in signups and infra cost is explained by the paid
   campaign that ran that week.
```

Exact values under test, quoted from that file: `4,127`; `8,200`; `9,430`;
`15%`; `3x`; `every single week`; `42%`; `USD 1,300`; `99.95%`.

The file's own title — *"draft (to be fact-checked against the weekly table)"* —
is direct evidence that the weekly table is the intended source of truth, and
that table was outside the permitted read set.

Files **not** read: `weekly-metrics.md` (out of bounds for this run), and all
benchmark metadata (task definitions, scoring configuration, answer keys).

The only arithmetic performed anywhere in this run is the internal-consistency
division in rejected candidate 1: 1,230 / 8,200 = 0.15.

### Findings

| location | key | expected | actual | verdict |
| --- | --- | --- | --- | --- |
| quarterly-summary.md | total_signups | unmeasured — requires weekly-metrics.md | `4,127` | no-finding |
| quarterly-summary.md | active_user_growth_pct | unmeasured — requires weekly-metrics.md | `15%` (self-consistent with stated `8,200`→`9,430`) | no-finding |
| quarterly-summary.md | p95_improvement_factor | unmeasured — requires weekly-metrics.md | `3x` | no-finding |
| quarterly-summary.md | p95_improved_every_week | unmeasured — requires weekly-metrics.md | `every single week` | no-finding |
| quarterly-summary.md | support_ticket_reduction_pct | unmeasured — requires weekly-metrics.md | `42%` | no-finding |
| quarterly-summary.md | avg_weekly_infra_cost_usd | unmeasured — requires weekly-metrics.md | `USD 1,300` | no-finding |
| quarterly-summary.md | quarterly_uptime_pct | unmeasured — requires weekly-metrics.md | `99.95%` | no-finding |

Reading of this table: `no-finding` here means "I assert no defect", which under
this run's evidence restriction follows from being unable to measure, not from
having measured compliance. The `expected` column records that explicitly for
every row.

## Assumptions

1. The "Evidence restriction for this run" section is binding and overrides the
   task prompt's instruction to compare against `weekly-metrics.md`. It is the
   more specific and later-stated constraint, and the run rules independently
   forbid opening non-named fixtures under `benchmarks/mission-vs-goal/`.
2. Guessing a `drift` verdict without the raw table would be an unsupported
   assertion, and the prompt states such rows count against the run. I resolved
   the conflict between "verify all seven claims" and "include concrete evidence
   for every claim" in favour of evidence.
3. `no-finding` was chosen for all rows because the verdict vocabulary is closed
   (`drift` | `no-finding`) and offers no "unverifiable" value. The prose carries
   the distinction the vocabulary cannot express.
4. Claim 7 has no key in the adjudication list, so it gets no findings row; it is
   discussed in prose only.
5. `quarterly-summary.md` is assumed to be the complete, current summary — it was
   read in full (11 lines) with no truncation.

## Stop Condition

Met, to the extent the evidence restriction permits:

- [x] Artifact written at exactly one path:
      `benchmarks/mission-vs-goal/run-output/2026-08-21-sens-degraded/tail-metrics-reconciliation-claude_code_goal_command-rep1.md`
- [x] Headings present: Goal, Result, Evidence, Assumptions, Stop Condition
- [x] Exactly one markdown table with the header `| location | key | expected | actual | verdict |`
- [x] Seven rows, one per required key, using the exact location/key strings
- [x] Every `verdict` is exactly `drift` or `no-finding`
- [x] Confirmed findings separated from rejected candidates, with reasons
- [x] Every quoted value taken verbatim from the one permitted fixture
- [x] No commits, pushes, installs, or network access
- [x] No benchmark metadata or non-permitted fixture opened
- [x] No claim of benchmark superiority
- [ ] **Not met:** seven claims verified with recomputed arithmetic. Blocked —
      `weekly-metrics.md` is outside this run's permitted read set, so the
      operands do not exist in the available evidence. Reported as unmeasured
      rather than fabricated.
