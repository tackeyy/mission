# tail-metrics-reconciliation — claude_code_goal_command (rep2)

## Goal

Fact-check every numbered claim in `quarterly-summary.md` against the raw table in
`weekly-metrics.md`, recompute each figure from the table, mark each claim correct or
incorrect, give the corrected value with arithmetic shown, and emit one
machine-checkable findings table covering the seven adjudication keys.

## Result

Seven adjudication items evaluated. **Four are drift** (`total_signups`,
`p95_improved_every_week`, `p95_improvement_factor`, `support_ticket_reduction_pct`,
`avg_weekly_infra_cost_usd` — five, listed precisely below) and **two are compliant**
(`active_user_growth_pct`, `quarterly_uptime_pct`). Exact split:

- drift (5): `total_signups`, `p95_improved_every_week`, `p95_improvement_factor`,
  `support_ticket_reduction_pct`, `avg_weekly_infra_cost_usd`
- no-finding (2): `active_user_growth_pct`, `quarterly_uptime_pct`

Summary claim 7 (the week-7 spike attribution) is not one of the seven adjudication
keys, so it has no findings row; it is checked in the prose below and is correct.

### Findings table

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| quarterly-summary.md | total_signups | 4217 | 4127 | drift |
| quarterly-summary.md | active_user_growth_pct | 15% | 15% | no-finding |
| quarterly-summary.md | p95_improved_every_week | false | true | drift |
| quarterly-summary.md | p95_improvement_factor | 1.88x | 3x | drift |
| quarterly-summary.md | support_ticket_reduction_pct | 29.0% | 42% | drift |
| quarterly-summary.md | avg_weekly_infra_cost_usd | 1450 | 1300 | drift |
| quarterly-summary.md | quarterly_uptime_pct | 99.95% | 99.95% | no-finding |

## Evidence

Source of truth: `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`
(13 weekly rows, plus a Notes paragraph). Claims under test:
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`.

### Confirmed findings (incorrect claims)

**F1 — `total_signups`: claim says 4,127; table sums to 4,217.**

Claim quote: "Total signups for the quarter reached 4,127."

Signups column, weeks 1–13: `290, 310, 325, 301, 340, 355, 410, 298, 362, 330, 342, 276, 278`.

```
290+310 = 600
600+325 = 925
925+301 = 1226
1226+340 = 1566
1566+355 = 1921
1921+410 = 2331
2331+298 = 2629
2629+362 = 2991
2991+330 = 3321
3321+342 = 3663
3663+276 = 3939
3939+278 = 4217
```

Corrected value: **4,217**. The stated 4,127 differs by 90 and looks like a digit
transposition of the true total.

**F2 — `p95_improved_every_week`: claim says every single week; week 8 → week 9 regressed.**

Claim quote: "p95 latency improved 3x over the quarter, and improved every single week."

p95 latency (ms) by week: `620, 600, 570, 545, 520, 490, 455, 380, 410, 395, 370, 350, 330`.
Week 8 is `380` and week 9 is `410`. Since lower is better, `410 > 380` is a
**regression of +30 ms**, so the "every single week" part is false. Every other
week-over-week step is a decrease.

Corrected value: **false** — p95 improved in 11 of the 12 week-over-week steps; it
worsened once (week 8 → week 9, 380 ms → 410 ms).

**F3 — `p95_improvement_factor`: claim says 3x; table gives ≈1.88x.**

Same claim sentence. First week p95 `620`, last week p95 `330`.

```
620 / 330 = 1.8787...  →  ≈1.88x
```

Corrected value: **≈1.88x** (equivalently a 46.8% reduction:
`(620-330)/620 = 290/620 = 0.4677` → 46.8%). A 3x improvement would require an
end-of-quarter p95 of `620 / 3 = 206.7 ms`, which no week reaches — the minimum
observed is `330`.

**F4 — `support_ticket_reduction_pct`: claim says 42%; table gives 29.0%.**

Claim quote: "Support tickets are down 42% quarter over quarter."

Support tickets by week: `210, 205, 198, 190, 186, 180, 175, 170, 165, 160, 155, 152, 149`.
Using the only comparison the table supports (first week vs. last week of the quarter):

```
210 - 149 = 61
61 / 210 = 0.290476...  →  29.0%
```

Corrected value: **29.0%** (210 → 149). Note the claim says "quarter over quarter",
but the table contains only this one quarter; there is no prior-quarter ticket total
in either fixture, so a literal quarter-over-quarter comparison is **unmeasured**
from the provided data. Under either reading the stated 42% is unsupported: the
within-quarter reduction is 29.0%, and the cross-quarter figure cannot be computed
at all.

**F5 — `avg_weekly_infra_cost_usd`: claim says about USD 1,300; table gives 1,450.**

Claim quote: "Average weekly infra cost was held at about USD 1,300."

Infra cost (USD) by week: `1400, 1420, 1380, 1450, 1500, 1480, 1620, 1440, 1460, 1430, 1410, 1450, 1410`.

```
1400+1420 = 2820
2820+1380 = 4200
4200+1450 = 5650
5650+1500 = 7150
7150+1480 = 8630
8630+1620 = 10250
10250+1440 = 11690
11690+1460 = 13150
13150+1430 = 14580
14580+1410 = 15990
15990+1450 = 17440
17440+1410 = 18850

18850 / 13 = 1450.0
```

Corrected value: **USD 1,450 per week**. Additionally, no single week is as low as
1,300 — the minimum observed is `1380` (week 3) — so "held at about USD 1,300" is not
defensible even as a loose approximation.

### Verified claims (correct — explicitly confirmed, not flagged)

**V1 — `active_user_growth_pct`: 15% is CORRECT.**

Claim quote: "Active users grew from 8,200 to 9,430, a 15% increase."

Active users (EOW) week 1 = `8200`, week 13 = `9430`. Both endpoints match the claim
exactly.

```
9430 - 8200 = 1230
1230 / 8200 = 0.15 exactly  →  15%
```

Confirmed correct — the endpoints and the percentage all reconcile to the table.

**V2 — `quarterly_uptime_pct`: 99.95% is CORRECT.**

Claim quote: "Quarterly uptime was 99.95%."

The weekly table has no uptime column, but the source-of-truth file's Notes state:
"Uptime for the quarter was 99.95% (status page export)." The claim reproduces this
figure exactly. Confirmed correct. Scope note: this is a match against the fixture's
stated figure, not an independent recomputation — there is no per-week uptime data to
recompute from, so the underlying 99.95% is **unmeasured** by me.

**V3 — week-7 spike attribution (claim 7): CORRECT.** (No findings row; not among the
seven adjudication keys.)

Claim quote: "The week-7 spike in signups and infra cost is explained by the paid
campaign that ran that week."

Week 7 signups `410` is the maximum of the Signups column (next highest: `362`,
week 9). Week 7 infra cost `1620` is the maximum of the Infra cost column (next
highest: `1500`, week 5). So both a signup spike and a cost spike genuinely occur in
week 7. The Notes state: "the week-7 signup and cost spike coincides with the paid
campaign that ran that week." Confirmed consistent with the source of truth.
Caveat: the fixture asserts coincidence; causal attribution ("is explained by") is not
independently verifiable from these two files and is **unmeasured**.

### Rejected candidates (looked suspicious, but are not findings)

- **`active_user_growth_pct` rounding.** Suspicious because 15% is a suspiciously
  round number next to five other wrong figures, and the fixture pattern primes you to
  expect drift. Rejected: `1230/8200 = 0.15` is exact to the last digit — no rounding
  is even involved. Reporting it would be a false positive.

- **Active-user growth measured week-over-week instead of endpoint-to-endpoint.**
  Suspicious because summing weekly deltas is a plausible alternate method. Rejected:
  the weekly deltas telescope to the same endpoint difference
  (`8200 → 9430`, total `+1230`), so the method choice cannot change the answer.

- **`quarterly_uptime_pct` has no table column.** Suspicious because every other claim
  is backed by a numeric column, and a claim with no column looks fabricated.
  Rejected: the figure is present verbatim in the source-of-truth file's own Notes
  line, so the summary is faithfully reproducing the source. A missing column is a
  data-availability limitation, not a summary defect.

- **Week 12 signup dip (`276`, the quarter's minimum).** Suspicious as a possible
  planted anomaly needing explanation. Rejected: no numbered claim asserts anything
  about week 12, so there is nothing to contradict. An unexplained data point is not a
  claim error.

- **Week 4 signups (`301`) dipping below week 3 (`325`), and week 8 (`298`) below
  week 7 (`410`).** Suspicious as non-monotonic growth. Rejected: no claim asserts
  monotonic signup growth — the monotonicity assertion is about p95 only, and that one
  is already reported as F2. Flagging these separately would double-count.

- **Infra cost stated as "about" USD 1,300.** Suspicious that hedging language ("about",
  "held at") might make the claim un-falsifiable. Rejected as a *rejection*, i.e. it is
  kept as finding F5: the true mean `1450` is 11.5% above 1,300, and no week is below
  `1380`, so the hedge cannot cover the gap. Listed here only to record that the hedge
  was considered and found insufficient.

## Assumptions

1. **Source of truth.** `weekly-metrics.md` (table + its Notes paragraph) is
   authoritative; `quarterly-summary.md` is the artifact under test. Where the two
   disagree, the summary is wrong.
2. **Quarter endpoints.** "From … to …" and "over the quarter" comparisons use week 1
   as the start and week 13 as the end. This is corroborated for active users, where
   the claim's own stated endpoints (8,200 and 9,430) match rows 1 and 13 exactly.
3. **p95 direction.** Lower p95 latency is better, so a week-over-week *increase* is a
   regression. "3x improvement" is read as a ratio `start / end`.
4. **Support tickets "quarter over quarter."** No prior-quarter data exists in either
   fixture, so I evaluated the closest computable within-quarter reduction
   (week 1 → week 13). The literal QoQ figure is unmeasured; this is stated in F4
   rather than silently substituted.
5. **Average infra cost.** Unweighted arithmetic mean over all 13 weeks. No weighting
   scheme is specified or derivable from the fixture.
6. **Rounding.** Percentages reported to one decimal; the improvement factor to two.
   `active_user_growth_pct` needed no rounding (exact 15%).
7. **Scope.** Only the two named fixture files and this output file were opened. No
   benchmark task definitions, scoring configuration, or answer keys were read, listed,
   or grepped.
8. **Arithmetic verification method.** All sums and quotients above were computed by
   hand and shown step by step in the Evidence section; no script was executed, so the
   arithmetic is auditable from the shown steps rather than machine-verified.

## Stop Condition

Met. This artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-metrics-reconciliation-claude_code_goal_command-rep2.md`
and contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition.
All seven adjudication keys have exactly one findings row each, with `verdict` values
drawn only from `drift` / `no-finding`; every incorrect claim has a corrected value
with the arithmetic shown; correct claims are confirmed explicitly in the
verified-claims section; and rejected candidates are separated from confirmed
findings with reasons. Exactly one file was written; nothing was committed, pushed,
or installed, and no network access was used.
