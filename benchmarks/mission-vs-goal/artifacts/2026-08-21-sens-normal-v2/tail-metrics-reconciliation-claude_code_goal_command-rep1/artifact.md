# tail-metrics-reconciliation — claude_code_goal_command — rep1

## Goal

Fact-check every numbered claim in `quarterly-summary.md` against the raw table in
`weekly-metrics.md`, recompute each figure from the table, mark each claim correct or
incorrect, and give the corrected value with the arithmetic shown. Correct claims must be
confirmed as correct rather than flagged.

Sources read (only these two fixtures plus this output file):

- `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
- `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`

## Result

Of the 7 numbered claims, **4 are incorrect** (claims 1, 3, 4, 5 — with claim 3 containing
two separable defects) and **3 are correct** (claims 2, 6, 7).

| # | Claim (abbreviated) | Verdict |
|---|---|---|
| 1 | Total signups 4,127 | incorrect → 4,217 |
| 2 | Active users 8,200 → 9,430, a 15% increase | correct |
| 3a | p95 improved 3x over the quarter | incorrect → 1.88x |
| 3b | p95 improved every single week | incorrect → week 9 regressed |
| 4 | Support tickets down 42% QoQ | incorrect → 29.0% |
| 5 | Average weekly infra cost about USD 1,300 | incorrect → USD 1,450 |
| 6 | Quarterly uptime 99.95% | correct |
| 7 | Week-7 signup/infra spike explained by paid campaign | correct |

### Findings table

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| quarterly-summary.md | total_signups | 4217 | 4,127 | drift |
| quarterly-summary.md | active_user_growth_pct | 15.0 | 15% | no-finding |
| quarterly-summary.md | p95_improvement_factor | 1.88x | 3x | drift |
| quarterly-summary.md | p95_improved_every_week | false | improved every single week | drift |
| quarterly-summary.md | support_ticket_reduction_pct | 29.0 | 42% | drift |
| quarterly-summary.md | avg_weekly_infra_cost_usd | 1450 | about USD 1,300 | drift |
| quarterly-summary.md | quarterly_uptime_pct | 99.95 | 99.95% | no-finding |

## Evidence

### Confirmed findings (defects)

**1. `total_signups` — incorrect.**
Summary text: `1. Total signups for the quarter reached 4,127.`
Column `Signups` from `weekly-metrics.md`, all 13 weeks:

```
290 + 310 + 325 + 301 + 340 + 355 + 410 + 298 + 362 + 330 + 342 + 276 + 278
= 600 + 325 = 925
+ 301 = 1226 + 340 = 1566 + 355 = 1921 + 410 = 2331
+ 298 = 2629 + 362 = 2991 + 330 = 3321 + 342 = 3663
+ 276 = 3939 + 278 = 4217
```

Corrected value: **4,217** (the summary understates by 90).

**3a. `p95_improvement_factor` — incorrect.**
Summary text: `3. p95 latency improved 3x over the quarter, ...`
Week 1 p95 = `620` ms; week 13 p95 = `330` ms.

```
620 / 330 = 1.8788 ≈ 1.88x   (reduction = 290 ms = 46.8%)
```

A 3x improvement would require an end-of-quarter p95 of `620 / 3 ≈ 207` ms, which no week
attains (the minimum observed value is `330`). Corrected value: **≈1.88x**.

**3b. `p95_improved_every_week` — incorrect.**
Summary text: `... and improved every single week.`
The p95 series is `620, 600, 570, 545, 520, 490, 455, 380, 410, 395, 370, 350, 330`.
Week 8 = `380` → week 9 = `410` is a regression of `410 - 380 = +30` ms (worse, since lower
latency is better). Corrected statement: p95 improved in 11 of the 12 week-over-week
transitions; **week 9 regressed from 380 ms to 410 ms**.

**4. `support_ticket_reduction_pct` — incorrect.**
Summary text: `4. Support tickets are down 42% quarter over quarter.`
Column `Support tickets`: week 1 = `210`, week 13 = `149`.

```
(210 - 149) / 210 = 61 / 210 = 0.29047… ≈ 29.0%
```

Corrected value: **29.0%** (a 42% reduction would require an end value of
`210 × 0.58 = 121.8`, well below the observed `149`).

For completeness under an alternative reading — first half vs second half of the quarter
(weeks 1–6 vs 8–13, excluding the campaign week 7) — the totals are
`210+205+198+190+186+180 = 1169` and `170+165+160+155+152+149 = 951`, giving
`(1169 - 951) / 1169 = 18.6%`. Neither reading reaches 42%, so the claim is incorrect under
both. The single-week endpoint reading (29.0%) is used as the corrected value because the
claim quotes one percentage rather than a period aggregate.

**5. `avg_weekly_infra_cost_usd` — incorrect.**
Summary text: `5. Average weekly infra cost was held at about USD 1,300.`
Column `Infra cost (USD)`, all 13 weeks:

```
1400 + 1420 + 1380 + 1450 + 1500 + 1480 + 1620 + 1440 + 1460 + 1430 + 1410 + 1450 + 1410
= 2820 + 1380 = 4200 + 1450 = 5650 + 1500 = 7150 + 1480 = 8630
+ 1620 = 10250 + 1440 = 11690 + 1460 = 13150 + 1430 = 14580
+ 1410 = 15990 + 1450 = 17440 + 1410 = 18850

18850 / 13 = 1450.0
```

Corrected value: **USD 1,450**. Additionally, no single week is as low as 1,300 — the
minimum observed is `1380` (week 3) — so "held at about USD 1,300" is not supported by any
row either.

### Verified claims (confirmed correct — not defects)

**2. `active_user_growth_pct` — correct.**
Summary text: `2. Active users grew from 8,200 to 9,430, a 15% increase.`
Column `Active users (EOW)`: week 1 = `8200`, week 13 = `9430` — both endpoints match the
summary exactly.

```
(9430 - 8200) / 8200 = 1230 / 8200 = 0.15 exactly = 15.0%
```

The stated 15% is exact, not merely rounded. **Confirmed correct.**

**6. `quarterly_uptime_pct` — correct.**
Summary text: `6. Quarterly uptime was 99.95%.`
`weekly-metrics.md` notes line: `Uptime for the quarter was 99.95% (status page export).`
The value matches the source of truth character-for-character. **Confirmed correct.**
(Uptime has no per-week column, so it cannot be independently recomputed from the table —
see Assumptions.)

**7. Week-7 spike attribution — correct.**
Summary text: `7. The week-7 spike in signups and infra cost is explained by the paid
campaign that ran that week.`
Week 7 is the maximum of both columns: `Signups` = `410` (next highest `362` in week 9) and
`Infra cost (USD)` = `1620` (next highest `1500` in week 5). The notes line states: `the
week-7 signup and cost spike coincides with the paid campaign that ran that week.` Both the
existence of the spike and the attribution are supported. **Confirmed correct.**

### Rejected candidates (looked suspicious, but are not findings)

- **`active_user_growth_pct` rounding.** A stated "15%" against a computed ratio invites a
  precision complaint. Rejected: `1230 / 8200 = 0.15` terminates exactly, so there is no
  rounding error to flag.
- **`quarterly_uptime_pct` not derivable from the table.** The table has no uptime column,
  which initially looks like an unsupported claim. Rejected: the prompt designates
  `weekly-metrics.md` as the source of truth, and its notes line carries the value
  `99.95% (status page export)`. The summary matches it, so it is compliant.
- **Week-7 wording "explained by" vs the notes' "coincides with".** The summary asserts
  causation where the source says coincidence. Rejected as a numeric/reconciliation
  finding: the fixture offers no data to refute the attribution, and the task is arithmetic
  fact-checking. Flagging it would be an assertion I cannot evidence. Noted here only as a
  wording nuance, and recorded as `no-finding`-adjacent (claim 7 is not in the required
  findings rows).
- **Week-12 signup dip (`276`) and week-8/12 troughs.** These look anomalous versus
  neighbouring weeks, but no numbered claim references them, so there is nothing to
  adjudicate.
- **Active users at week 13 (`9430`) vs the "grew from 8,200" baseline being an EOW figure.**
  Using week-1 EOW as the starting point could be argued to understate the true start.
  Rejected: the summary quotes `8,200` and `9,430`, both of which appear verbatim in the
  `Active users (EOW)` column, so the claim is internally consistent with the table as
  given.

## Assumptions

- **Quarter = all 13 rows** of `weekly-metrics.md` (weeks 1–13). Totals and averages use all
  13 weeks; the average infra cost divides by 13.
- **"Quarter over quarter" for support tickets** is interpreted as first-week vs last-week
  of the quarter (210 → 149), because the fixture contains only one quarter of data and no
  prior-quarter figures exist. The alternative half-over-half reading is computed above and
  also fails the 42% claim, so the verdict is robust to this choice.
- **Lower p95 latency = better**, so an increase week-over-week counts as a regression.
- **Uptime is unmeasured from the table itself** — there is no per-week uptime column. It is
  verified only against the notes line in `weekly-metrics.md`, which the prompt designates
  as the source of truth. I did not independently recompute it.
- **Claim 3 is split into two findings rows** (`p95_improvement_factor` and
  `p95_improved_every_week`) per the required item list, so the 7 numbered claims map onto
  7 findings rows with claim 7 (campaign attribution) verified in prose rather than in the
  table.
- Rounding: percentages are given to one decimal place; the improvement factor to two.

## Stop Condition

Met. The artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-21-sens-normal-v2/tail-metrics-reconciliation-claude_code_goal_command-rep1.md`
and contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition; all
seven required items are adjudicated in exactly one findings table with verdicts limited to
`drift` / `no-finding`; every incorrect claim has a corrected value with arithmetic shown;
correct claims are confirmed in the verified-claims section; rejected candidates are listed
separately with reasons. No files outside this artifact were written, and no fixture other
than the two named files was opened. No commits, pushes, installs, or network access were
performed.
