# tail-metrics-reconciliation — claude_code_goal_command — rep1

## Goal

Fact-check every numbered claim in `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
against the raw table in `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`,
recompute each figure from the table, mark each claim correct or incorrect, and give the corrected
value with the arithmetic shown — confirming correct claims explicitly rather than flagging them.

## Result

Seven adjudicated items. **Five are drift (incorrect claims); two are compliant (correct claims).**

| # | Claim (summary) | Verdict |
|---|---|---|
| 1 | Total signups 4,127 | incorrect → 4,217 |
| 2 | Active users 8,200 → 9,430 = 15% | **correct** |
| 3a | p95 improved 3x | incorrect → ~1.88x |
| 3b | p95 improved every single week | incorrect → week 9 regressed |
| 4 | Support tickets down 42% | incorrect → 29.05% |
| 5 | Average weekly infra cost ~USD 1,300 | incorrect → USD 1,450 |
| 6 | Quarterly uptime 99.95% | **correct** |

Claim 7 (week-7 spike explained by the paid campaign) is not one of the seven adjudicated keys, but it
was checked and is supported by the fixture — see Evidence.

### Findings table

| location | key | expected | actual | verdict |
| --- | --- | --- | --- | --- |
| quarterly-summary.md | total_signups | 4217 | 4127 | drift |
| quarterly-summary.md | active_user_growth_pct | 15 | 15 | no-finding |
| quarterly-summary.md | p95_improvement_factor | 1.88 | 3 | drift |
| quarterly-summary.md | p95_improved_every_week | false | true | drift |
| quarterly-summary.md | support_ticket_reduction_pct | 29.05 | 42 | drift |
| quarterly-summary.md | avg_weekly_infra_cost_usd | 1450 | 1300 | drift |
| quarterly-summary.md | quarterly_uptime_pct | 99.95 | 99.95 | no-finding |

## Evidence

Source of truth: the 13-row table in `weekly-metrics.md` (header
`| Week | Signups | Active users (EOW) | p95 latency (ms) | Support tickets | Infra cost (USD) |`).

### Confirmed findings (drift)

**1. `total_signups` — claim: "Total signups for the quarter reached 4,127."**

Column sum, week 1 → 13:
`290 + 310 + 325 + 301 + 340 + 355 + 410 + 298 + 362 + 330 + 342 + 276 + 278`

Running total: 290, 600, 925, 1226, 1566, 1921, 2331, 2629, 2991, 3321, 3663, 3939, **4217**.

Corrected value: **4,217**. The summary states `4,127` — a 90-unit shortfall consistent with a
digit transposition (`4,217` → `4,127`). Sanity check: 4217 / 13 = 324.4 mean weekly signups, which
sits inside the observed per-week range 276–410.

**2. `p95_improvement_factor` — claim: "p95 latency improved 3x over the quarter."**

Week 1 p95 = `620` ms (row `| 1 | 290 | 8200 | 620 | 210 | 1400 |`).
Week 13 p95 = `330` ms (row `| 13 | 278 | 9430 | 330 | 149 | 1410 |`).

620 / 330 = 1.8787… → **1.88x** (equivalently a 46.8% reduction: (620 − 330) / 620 = 290 / 620 = 0.4677).
A 3x improvement would require an end-of-quarter p95 of 620 / 3 = 206.7 ms, which does not appear
anywhere in the column. Corrected value: **≈1.88x**.

**3. `p95_improved_every_week` — claim: "…and improved every single week."**

False. p95 regressed between week 8 and week 9:
- week 8: `| 8 | 298 | 8990 | 380 | 170 | 1440 |` → p95 = `380`
- week 9: `| 9 | 362 | 9080 | 410 | 165 | 1460 |` → p95 = `410`

410 > 380, i.e. a 30 ms regression (+7.9%). All other 11 week-over-week steps decrease
(620→600→570→545→520→490→455→380, then 410→395→370→350→330). Corrected statement:
**p95 improved in 11 of 12 week-over-week steps; it regressed once, from 380 ms (week 8) to
410 ms (week 9).**

**4. `support_ticket_reduction_pct` — claim: "Support tickets are down 42% quarter over quarter."**

Week 1 tickets = `210`; week 13 tickets = `149`.
(210 − 149) / 210 = 61 / 210 = 0.290476… → **29.05%**.

A 42% reduction from 210 would land at 210 × 0.58 = 121.8 tickets, which is below every value in the
column (minimum observed is `149`). Corrected value: **29.05% (≈29%)**.

Note on framing: the fixture contains a single quarter, so a literal prior-quarter baseline is
**unmeasured**. The only in-fixture baseline is week 1, which is what is used above; the corrected
figure is therefore a start-of-quarter → end-of-quarter reduction, not a true QoQ comparison.

**5. `avg_weekly_infra_cost_usd` — claim: "Average weekly infra cost was held at about USD 1,300."**

Column sum: `1400 + 1420 + 1380 + 1450 + 1500 + 1480 + 1620 + 1440 + 1460 + 1430 + 1410 + 1450 + 1410`

Running total: 1400, 2820, 4200, 5650, 7150, 8630, 10250, 11690, 13150, 14580, 15990, 17440, **18850**.

18850 / 13 = **1450.0**. Corrected value: **USD 1,450**. The claimed ~1,300 is below the *minimum*
single-week cost in the table (`1380`, week 3), so no weighting or subset of weeks supports it.

### Verified claims (confirmed correct — not findings)

**`active_user_growth_pct` — claim: "Active users grew from 8,200 to 9,430, a 15% increase." — CORRECT.**

- Start: week 1 "Active users (EOW)" = `8200` — matches the claimed 8,200.
- End: week 13 "Active users (EOW)" = `9430` — matches the claimed 9,430.
- Growth: (9430 − 8200) / 8200 = 1230 / 8200 = 0.15 exactly → **15.0%**.

All three components of the claim reconcile to the table with no rounding slack. Confirmed correct.

**`quarterly_uptime_pct` — claim: "Quarterly uptime was 99.95%." — CORRECT.**

`weekly-metrics.md` states verbatim: "Uptime for the quarter was 99.95% (status page export)."
The claimed `99.95%` matches the source-of-truth file exactly. Confirmed correct.

### Claim 7 (checked, not an adjudicated key)

Claim 7: "The week-7 spike in signups and infra cost is explained by the paid campaign that ran that week."

Supported by the fixture. Week 7 row: `| 7 | 410 | 8900 | 455 | 175 | 1620 |` — `410` is the maximum
value in the Signups column and `1620` is the maximum value in the Infra cost column, so a spike in
both metrics does exist in week 7. The notes section of `weekly-metrics.md` states: "the week-7 signup
and cost spike coincides with the paid campaign that ran that week." No findings row is emitted for
this claim because it is not in the list of seven adjudicated keys.

### Rejected candidates (looked suspicious, not real findings)

- **`active_user_growth_pct` (15%)** — looked suspicious because the other headline percentages in
  the summary (42%, 3x, ~1,300) are all wrong, and a round "15%" reads like a rounded-up marketing
  figure. It is not a finding: 1230 / 8200 = 0.15 exactly, with no rounding at all.
- **`quarterly_uptime_pct` (99.95%)** — looked suspicious because it is the one figure with no
  supporting column in the table, so it initially appeared unsourced. It is not a finding: the value
  is stated verbatim in the notes line of `weekly-metrics.md`, which is the designated source of
  truth, and the summary reproduces it exactly.
- **Week-8 signups (`298`) as a data error** — the signups column dips sharply at week 8 (410 → 298)
  right after the campaign week, which looked like a possible transcription error. Rejected: no claim
  in `quarterly-summary.md` depends on week-8 signups, and there is no independent source in the
  fixture to contradict the value. Whether `298` is itself accurate is **unmeasured**.
- **Week-9 infra cost (`1460`) rising while p95 also rose** — flagged during review as a possible
  paired anomaly. Rejected as a separate finding: the p95 regression is already captured under
  `p95_improved_every_week`, and no claim asserts anything about week-9 cost. Any causal link between
  the two is **unmeasured**.

## Assumptions

- `weekly-metrics.md` is the source of truth in full, including its trailing notes paragraph; the
  uptime figure and the week-7 campaign attribution are therefore treated as authoritative even
  though they are not table columns.
- "Over the quarter" / "quarter over quarter" comparisons are evaluated as week 1 → week 13, since
  the fixture contains exactly one quarter (13 weeks) and no prior-quarter data. A genuine
  prior-quarter baseline is unmeasured.
- "Active users" is read from the "Active users (EOW)" column; week 1 EOW is used as the starting
  value because the claim's stated start (8,200) matches it exactly.
- "Average weekly infra cost" is read as the unweighted arithmetic mean over all 13 weeks.
- "Improvement" in p95 latency means a decrease in milliseconds.
- The `expected` column for `p95_improvement_factor` is rounded to two decimals (1.8787… → 1.88);
  `support_ticket_reduction_pct` is rounded to two decimals (29.0476… → 29.05).
- Only the two named fixture files and this output file were opened. No other path under
  `benchmarks/mission-vs-goal/` was read, listed, or grepped; no task definitions, scoring
  configuration, or answer keys were consulted.
- No commits, pushes, package installs, or network access were performed. No claim of benchmark
  superiority is made here; this artifact covers only the assigned task.

## Stop Condition

Met. The artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-metrics-reconciliation-claude_code_goal_command-rep1.md`
and contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition.

All seven adjudicated items have recomputed arithmetic shown; every incorrect claim has a corrected
value (4,217 / ≈1.88x / regressed at week 9 / 29.05% / USD 1,450); both correct claims are confirmed
in the "Verified claims" section rather than flagged; and exactly one findings table with the header
`| location | key | expected | actual | verdict |` is present, with one row per item and verdicts
restricted to `drift` and `no-finding`.
