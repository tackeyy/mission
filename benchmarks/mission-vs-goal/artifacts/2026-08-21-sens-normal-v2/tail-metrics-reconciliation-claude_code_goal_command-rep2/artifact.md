# tail-metrics-reconciliation — claude_code_goal_command (rep2)

## Goal

Fact-check every numbered claim in `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
against the raw table in `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`,
recompute each figure, mark each claim correct or incorrect, give the corrected
value with arithmetic shown, and confirm correct claims explicitly.

## Result

Seven adjudicated items: **5 drift**, **2 no-finding**.

| # | Claim (summary text) | Verdict |
|---|---|---|
| 1 | "Total signups for the quarter reached 4,127." | incorrect → 4,217 |
| 2 | "Active users grew from 8,200 to 9,430, a 15% increase." | **correct** |
| 3a | "p95 latency improved 3x over the quarter" | incorrect → ~1.88x |
| 3b | "...and improved every single week." | incorrect → week 9 regressed |
| 4 | "Support tickets are down 42% quarter over quarter." | incorrect → 29.0% |
| 5 | "Average weekly infra cost was held at about USD 1,300." | incorrect → USD 1,450 |
| 6 | "Quarterly uptime was 99.95%." | **correct** |
| 7 | "The week-7 spike ... explained by the paid campaign" | supported (not in findings list) |

Source of truth used throughout: the 13-row weekly table and its trailing
`Notes:` block in `weekly-metrics.md`.

## Evidence

### Raw values quoted from `weekly-metrics.md`

Signups column, weeks 1–13: `290`, `310`, `325`, `301`, `340`, `355`, `410`,
`298`, `362`, `330`, `342`, `276`, `278`.

Infra cost (USD) column, weeks 1–13: `1400`, `1420`, `1380`, `1450`, `1500`,
`1480`, `1620`, `1440`, `1460`, `1430`, `1410`, `1450`, `1410`.

p95 latency (ms), weeks 1–13: `620`, `600`, `570`, `545`, `520`, `490`, `455`,
`380`, `410`, `395`, `370`, `350`, `330`.

Support tickets, week 1 = `210`, week 13 = `149`.
Active users (EOW), week 1 = `8200`, week 13 = `9430`.
Notes line: "Uptime for the quarter was 99.95% (status page export)."

### Confirmed findings (drift)

**F1 — `total_signups`.** Summary asserts "Total signups for the quarter
reached 4,127."
Recomputation:
290+310 = 600; +325 = 925; +301 = 1226; +340 = 1566; +355 = 1921; +410 = 2331;
+298 = 2629; +362 = 2991; +330 = 3321; +342 = 3663; +276 = 3939; +278 = **4217**.
Corrected value: **4,217** (the summary's `4,127` is a digit transposition of
4,217; error = 90 signups).

**F2 — `p95_improvement_factor`.** Summary asserts "p95 latency improved 3x
over the quarter."
Recomputation: week 1 `620` ms → week 13 `330` ms; 620 ÷ 330 = 1.8788…
Corrected value: **≈1.88x** (equivalently a 46.8% reduction:
(620 − 330) ÷ 620 = 290 ÷ 620 = 0.4677). Not 3x.

**F3 — `p95_improved_every_week`.** Summary asserts p95 "improved every single
week."
Recomputation of week-over-week deltas: 620→600 (−20), 600→570 (−30),
570→545 (−25), 545→520 (−25), 520→490 (−30), 490→455 (−35), 455→`380` (−75),
`380`→`410` (**+30, regression**), 410→395 (−15), 395→370 (−25), 370→350 (−20),
350→330 (−20).
Corrected value: **improved in 11 of 12 week-over-week transitions; week 8→9
regressed from `380` ms to `410` ms** (+30 ms). The "every single week" claim is
false.

**F4 — `support_ticket_reduction_pct`.** Summary asserts "Support tickets are
down 42% quarter over quarter."
Recomputation from the only ticket data available (within-quarter, week 1 →
week 13): (210 − 149) ÷ 210 = 61 ÷ 210 = 0.29047… = **29.0%**.
Corrected value: **29.0% (≈29%)**. Note: the table contains no prior-quarter
ticket figure, so a literal "quarter over quarter" comparison is **unmeasurable
from the source of truth**; even the most favourable in-table reading (29.0%)
does not reach 42%.

**F5 — `avg_weekly_infra_cost_usd`.** Summary asserts "Average weekly infra cost
was held at about USD 1,300."
Recomputation of the sum:
1400+1420 = 2820; +1380 = 4200; +1450 = 5650; +1500 = 7150; +1480 = 8630;
+1620 = 10250; +1440 = 11690; +1460 = 13150; +1430 = 14580; +1410 = 15990;
+1450 = 17440; +1410 = **18850**.
Mean = 18850 ÷ 13 = **1450.0** (13 × 1450 = 18850, exact).
Corrected value: **USD 1,450 per week**. Additionally, no single week is at or
below 1,300 — the minimum observed is `1380` (week 3) — so "held at about USD
1,300" is not supportable on any reading.

### Verified claims (confirmed correct — no correction needed)

**V1 — `active_user_growth_pct`.** Summary: "Active users grew from 8,200 to
9,430, a 15% increase."
Recomputation: week 1 EOW = `8200`, week 13 EOW = `9430`; both endpoints match
the table exactly. Growth = (9430 − 8200) ÷ 8200 = 1230 ÷ 8200 = 0.15 =
**15.00%** exactly. **Confirmed correct.**

**V2 — `quarterly_uptime_pct`.** Summary: "Quarterly uptime was 99.95%."
The table has no uptime column, but the fixture's own `Notes:` block states
"Uptime for the quarter was 99.95% (status page export)." The summary value
matches the source of truth character-for-character. **Confirmed correct.**

### Rejected candidates (looked suspicious, but are not findings)

- **`quarterly_uptime_pct` — "no uptime column, therefore unsupported."**
  Suspicious because the recomputation instruction points at "the raw table" and
  uptime is absent from the six table columns, which reads at first like an
  unsourced number. Rejected: the source-of-truth file explicitly carries
  `Uptime for the quarter was 99.95% (status page export).` in its Notes, and
  the summary reproduces it exactly. Flagging it would be a false positive.
- **`active_user_growth_pct` — "15% is a suspiciously round number."**
  Suspicious because every other figure in the summary is wrong and round
  numbers often signal an eyeballed estimate. Rejected: 1230 ÷ 8200 is exactly
  0.15, so the round figure is arithmetically exact, not an approximation.
- **Claim 7, the week-7 campaign attribution.** Suspicious because week 7 also
  contains the largest signup value (`410`) and the largest cost value (`1620`),
  making it an attractive place to plant an error. Rejected: the fixture's Notes
  state "the week-7 signup and cost spike coincides with the paid campaign that
  ran that week", and both week-7 values are in fact the column maxima, so the
  summary is consistent with the source. Causal attribution itself is
  **unmeasured** — the table records no campaign spend or attribution data — but
  the summary's wording mirrors the source note, so it is not a drift. This item
  is not in the required findings list and therefore has no findings row.
- **Interpreting `support_ticket_reduction_pct` as a true prior-quarter
  comparison to rescue 42%.** Rejected as a rescue: no prior-quarter data exists
  in the fixture, so 42% cannot be substantiated; the only computable reduction
  is 29.0%, which is the corrected value reported in F4.

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| quarterly-summary.md | total_signups | 4217 | 4127 | drift |
| quarterly-summary.md | active_user_growth_pct | 15 | 15 | no-finding |
| quarterly-summary.md | p95_improvement_factor | 1.88x | 3x | drift |
| quarterly-summary.md | p95_improved_every_week | false (week 8→9 rose 380→410) | claimed improved every week | drift |
| quarterly-summary.md | support_ticket_reduction_pct | 29.0 | 42 | drift |
| quarterly-summary.md | avg_weekly_infra_cost_usd | 1450 | 1300 | drift |
| quarterly-summary.md | quarterly_uptime_pct | 99.95 | 99.95 | no-finding |

## Assumptions

- "Quarter" means the full 13 rows (weeks 1–13) present in `weekly-metrics.md`;
  no rows were excluded.
- Growth and reduction percentages are computed endpoint-to-endpoint (week 1 vs
  week 13) because that is the only comparison the table supports. For active
  users the week-1 value is read as the starting point, matching the summary's
  own "from 8,200 to 9,430" framing.
- "Average weekly infra cost" is the unweighted arithmetic mean over all 13
  weeks.
- "improved" for p95 latency means a lower millisecond value than the preceding
  week.
- The improvement "factor" is read as the ratio first ÷ last (620/330). Under
  the alternative reading — percentage reduction — the value is 46.8%; neither
  reading yields 3x, so the verdict is unchanged.
- The uptime figure is accepted from the fixture's Notes block, which is part of
  the same source-of-truth file; it was not independently verified against any
  status page (that is **unmeasured** here).
- Only the two named fixture files and this output file were opened.

## Stop Condition

Met. This artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-21-sens-normal-v2/tail-metrics-reconciliation-claude_code_goal_command-rep2.md`,
contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition,
verifies all seven adjudicated items with recomputed arithmetic, states the
corrected value for each incorrect claim, confirms the correct claims in the
"Verified claims" section, separates confirmed findings from rejected
candidates, and includes exactly one findings table with the required header and
verdicts drawn only from `drift` / `no-finding`. No commits, pushes, installs,
or network access were performed, and no other files were modified.
