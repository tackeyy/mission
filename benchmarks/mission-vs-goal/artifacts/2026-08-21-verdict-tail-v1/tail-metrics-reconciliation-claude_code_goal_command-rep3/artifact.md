# tail-metrics-reconciliation — claude_code_goal_command — rep3

## Goal

Fact-check every numbered claim in `quarterly-summary.md` against the raw table in
`weekly-metrics.md`, recompute each figure from the table, mark each claim correct or
incorrect, give the corrected value with arithmetic shown, and emit one machine-checkable
findings table covering the seven adjudication keys.

## Result

Seven adjudication items evaluated. **4 drift**, **3 no-finding**.

| # | Claim (quarterly-summary.md) | Verdict |
|---|---|---|
| 1 | Total signups 4,127 | incorrect → **4,217** |
| 2 | Active users 8,200 → 9,430, +15% | **correct** |
| 3a | p95 improved 3x | incorrect → **~1.88x** |
| 3b | p95 improved every single week | incorrect (week 8 → week 9 regressed) |
| 4 | Support tickets down 42% QoQ | incorrect → **~29.0%** |
| 5 | Avg weekly infra cost ~USD 1,300 | incorrect → **USD 1,450** |
| 6 | Quarterly uptime 99.95% | **correct** |
| 7 | Week-7 spike explained by paid campaign | **correct** (not an adjudication key; see Rejected candidates) |

### Findings table (machine-checkable)

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| quarterly-summary.md | total_signups | 4217 | 4127 | drift |
| quarterly-summary.md | active_user_growth_pct | 15 | 15 | no-finding |
| quarterly-summary.md | p95_improvement_factor | 1.88 | 3 | drift |
| quarterly-summary.md | p95_improved_every_week | false | true | drift |
| quarterly-summary.md | support_ticket_reduction_pct | 29.05 | 42 | drift |
| quarterly-summary.md | avg_weekly_infra_cost_usd | 1450 | 1300 | drift |
| quarterly-summary.md | quarterly_uptime_pct | 99.95 | 99.95 | no-finding |

## Evidence

Source of truth: `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`
(13 weekly rows, weeks 1–13). Claims: `.../quarterly-summary.md`.

### Confirmed findings (drift)

**F1 — total_signups.** Claim text: `1. Total signups for the quarter reached 4,127.`
Recomputation from the Signups column:

```
290 + 310 + 325 + 301 + 340 + 355 + 410 + 298 + 362 + 330 + 342 + 276 + 278
= 600, 925, 1226, 1566, 1921, 2331, 2629, 2991, 3321, 3663, 3939, 4217
```

Corrected value: **4,217**. The claimed `4,127` is short by 90 (consistent with a digit
transposition, but the defect stands regardless of cause).

**F2 — p95_improvement_factor.** Claim text: `3. p95 latency improved 3x over the quarter`.
Week 1 p95 = `620` ms; week 13 p95 = `330` ms.

```
620 / 330 = 1.8787...  ≈ 1.88x
Reduction: (620 - 330) / 620 = 290 / 620 = 46.77%
```

Corrected value: **≈1.88x** (a 46.8% reduction), not 3x. A true 3x would require a
week-13 p95 of `620 / 3 ≈ 206.7` ms.

**F3 — p95_improved_every_week.** Claim text: `and improved every single week.`
The p95 column is not monotonically decreasing: week 8 = `380`, week 9 = `410`.

```
week 8 -> week 9: 380 -> 410  (+30 ms, a regression)
```

Corrected statement: p95 improved in 11 of the 12 week-over-week transitions; it regressed
once, from `380` ms (week 8) to `410` ms (week 9). All other transitions decrease
(620→600→570→545→520→490→455→380, then 410→395→370→350→330).

**F4 — support_ticket_reduction_pct.** Claim text: `4. Support tickets are down 42% quarter
over quarter.` Week 1 tickets = `210`; week 13 tickets = `149`.

```
(210 - 149) / 210 = 61 / 210 = 0.290476... = 29.05%
```

Corrected value: **≈29.0%** (29.05%), not 42%. Note this is a start-of-quarter vs
end-of-quarter comparison within the same quarter; the fixture contains no prior-quarter
data, so a literal "quarter over quarter" baseline is **unmeasured** — see Assumptions.
Under either reading, 42% is not obtainable from the table.

**F5 — avg_weekly_infra_cost_usd.** Claim text: `5. Average weekly infra cost was held at
about USD 1,300.` Sum of the Infra cost column:

```
1400 + 1420 + 1380 + 1450 + 1500 + 1480 + 1620 + 1440 + 1460 + 1430 + 1410 + 1450 + 1410
= 2820, 4200, 5650, 7150, 8630, 10250, 11690, 13150, 14580, 15990, 17440, 18850
18850 / 13 = 1450.0
```

Corrected value: **USD 1,450** per week (exactly). The minimum weekly value in the table is
`1380`, so no week was at or below 1,300 — "about USD 1,300" is below the entire observed
range.

### Verified claims (confirmed correct — not flagged)

**V1 — active_user_growth_pct.** Claim text: `2. Active users grew from 8,200 to 9,430, a
15% increase.` Week 1 "Active users (EOW)" = `8200`; week 13 = `9430`.

```
9430 - 8200 = 1230
1230 / 8200 = 0.15 exactly = 15%
```

Both endpoints and the percentage are exactly right. **Correct.**

**V2 — quarterly_uptime_pct.** Claim text: `6. Quarterly uptime was 99.95%.` The fixture's
notes line states: `Uptime for the quarter was 99.95% (status page export).` Exact match to
the source of truth. **Correct.**

**V3 — week-7 spike attribution** (claim 7, not one of the seven adjudication keys).
Claim text: `7. The week-7 spike in signups and infra cost is explained by the paid campaign
that ran that week.` The notes line states: `the week-7 signup and cost spike coincides with
the paid campaign that ran that week.` The table corroborates the spike: week 7 signups
`410` is the maximum of the Signups column, and week 7 infra cost `1620` is the maximum of
the Infra cost column. **Correct** as to the existence of the spike and the stated
coincidence. Causation ("explained by") is not verifiable from the fixture — the notes say
"coincides with" — but this is a wording nuance, not a numeric defect, so it is not
reported as drift.

### Rejected candidates (looked suspicious, not real findings)

- **claim 2 rounding (`active_user_growth_pct`).** Suspicious because most other claims in
  the summary are wrong, and a round "15%" often signals a rounded-off approximation.
  Rejected: `1230 / 8200` is exactly `0.15`, so there is no rounding at all. Reported as
  `no-finding`.
- **claim 6 uptime unverifiable from the table (`quarterly_uptime_pct`).** Suspicious
  because there is no uptime column in the weekly table, so the figure cannot be
  recomputed from weekly rows. Rejected: the same fixture file carries the value in its
  notes (`99.95% (status page export)`), which is the designated source of truth, and the
  summary reproduces it verbatim. Reported as `no-finding`.
- **claim 7 causal wording ("explained by" vs the notes' "coincides with").** Suspicious as
  an overstatement of causality. Rejected as a finding: it is not one of the seven
  adjudication keys, and the underlying facts (week-7 maxima at `410` signups and `1620`
  infra cost; a paid campaign that week) are all supported by the fixture.
- **claim 5 hedge word "about" (`avg_weekly_infra_cost_usd`).** Suspicious that "about USD
  1,300" might be defensible as a loose approximation. Rejected as a defense: the true mean
  is `1450`, an 11.5% overshoot of the claim, and every one of the 13 weekly values
  (min `1380`) exceeds 1,300. Reported as `drift`.
- **claim 4 "quarter over quarter" framing.** Suspicious that the intended baseline is a
  prior quarter absent from the fixture, which would make the claim untestable rather than
  wrong. Rejected as an exemption: no prior-quarter data exists in either fixture file, and
  the only in-fixture reading (week 1 vs week 13) yields 29.05%. Reported as `drift` on the
  numeric value.

## Assumptions

- "Quarter start" and "quarter end" are week 1 and week 13 respectively, for the
  active-users, p95, and support-ticket endpoint comparisons.
- "Average weekly infra cost" is the unweighted arithmetic mean over all 13 weeks.
- "Total signups for the quarter" is the sum of all 13 rows of the Signups column.
- "p95 improved 3x" is read as a start/end ratio (`620 / 330`). Under the alternative
  reading "a 3x reduction = a 66.7% decrease", the claim is also false (actual decrease is
  46.8%), so the verdict is unchanged either way.
- **Unmeasured:** no prior-quarter data exists in the fixture, so a literal
  prior-quarter-vs-this-quarter comparison for claim 4 cannot be computed. The 29.05%
  figure is the week-1-vs-week-13 in-quarter change.
- **Unmeasured:** uptime is not derivable from the weekly table (no uptime column); claim 6
  is verified against the fixture's notes line only, not recomputed.
- **Unmeasured:** causality for the week-7 spike (campaign → spike) cannot be established
  from the fixture; only co-occurrence is documented.
- Only the two named fixture files and this output file were opened. No other file under
  `benchmarks/mission-vs-goal/` was read, listed, or searched. No commits, pushes, package
  installs, or network access were performed.
- All arithmetic above was performed by hand from the quoted table values; no script was
  executed.

## Stop Condition

Met. This artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-metrics-reconciliation-claude_code_goal_command-rep3.md`
and contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition. All
seven numbered claims are verified with recomputed arithmetic, every incorrect claim carries
a corrected value, correct claims are confirmed explicitly in the "Verified claims" section,
and exactly one findings table with the header
`| location | key | expected | actual | verdict |` is present with one row per adjudication
item and verdicts limited to `drift` / `no-finding`. No further work is required.
