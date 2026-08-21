# tail-metrics-reconciliation — mission arm (rep1)

## Mission

Fact-check every numbered claim in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
against the raw table in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`,
recompute each figure from the table, mark each claim correct or incorrect, and
state the corrected value with the arithmetic shown.

Task id: `tail-metrics-reconciliation`. Category: analysis. Arm: mission
(profile full, complexity Complex). This artifact makes no claim about
benchmark superiority of either arm.

## Plan

Adopted as a canonical `mission-plan/1` document via
`mission-state.py planning adopt-core` (see Evidence). Nine steps:

| step | action | acceptance check |
|---|---|---|
| s1 | Sum the 13 Signups rows | compared to claim 1 (4,127) |
| s2 | Recompute active-user growth 8200 → 9430 | compared to claim 2 (15%) |
| s3 | Recompute p95 improvement factor 620/330 | compared to claim 3 (3x) |
| s4 | Scan p95 column week-over-week for regressions | compared to claim 3 ("every single week") |
| s5 | Recompute ticket reduction 210 → 149 | compared to claim 4 (42%) |
| s6 | Recompute mean weekly infra cost = sum/13 | compared to claim 5 (~USD 1,300) |
| s7 | Check uptime against the fixture Notes line | compared to claim 6 (99.95%) |
| s8 | Check week-7 spike explanation against the Notes line | compared to claim 7 |
| s9 | Write this artifact | eight mandated headings, exactly one findings table |

s1–s8 are independent; s9 depends on all of them.

## Execution

All 13 data rows were taken verbatim from `weekly-metrics.md`. Every sum,
ratio and percentage below was computed mechanically in `python3` (transcript
in Evidence), not by mental arithmetic.

### Claim 1 — total signups

Signups column, weeks 1–13:
`290, 310, 325, 301, 340, 355, 410, 298, 362, 330, 342, 276, 278`

```
290+310+325+301+340+355+410+298+362+330+342+276+278 = 4217
```

Claim states "Total signups for the quarter reached **4,127**."
Recomputed: **4,217**. Off by 90 (a digit transposition of 4,217 → 4,127).
**Incorrect.** Corrected value: **4,217**.

### Claim 2 — active user growth

Active users (EOW) week 1 = `8200`, week 13 = `9430`.

```
(9430 - 8200) / 8200 = 1230 / 8200 = 0.15 = 15.0%
```

Claim states "Active users grew from **8,200** to **9,430**, a **15%**
increase." Both endpoints and the percentage match the table exactly.
**Correct.**

### Claim 3a — p95 improvement factor

p95 latency week 1 = `620` ms, week 13 = `330` ms.

```
620 / 330 = 1.8788... ≈ 1.88x
```

Claim states "p95 latency improved **3x** over the quarter."
A 3x improvement would require an end value of 620/3 ≈ 206.7 ms; the lowest
value anywhere in the table is `330`. **Incorrect.** Corrected value:
**≈1.88x** (a 46.8% reduction: (620−330)/620 = 0.4677).

### Claim 3b — "improved every single week"

p95 column week-over-week:
`620 → 600 → 570 → 545 → 520 → 490 → 455 → 380 → 410 → 395 → 370 → 350 → 330`

Scanning for any week where p95 did not decrease:

```
week 8 = 380, week 9 = 410  ->  410 > 380  (regression of +30 ms)
```

That is the only violation; all other 11 transitions decrease. Claim states
p95 "improved **every single week**". **Incorrect.** Corrected value: p95
improved in 11 of the 12 week-over-week transitions; it **regressed in week 9**
(`380` ms → `410` ms).

### Claim 4 — support ticket reduction

Support tickets week 1 = `210`, week 13 = `149`.

```
(210 - 149) / 210 = 61 / 210 = 0.290476... ≈ 29.0%
```

Claim states "Support tickets are down **42%** quarter over quarter."
**Incorrect.** Corrected value: **≈29.0%** (a 61-ticket reduction).

Note on the comparison basis: the fixture contains only this quarter, so a
literal prior-quarter baseline is **unmeasured** — no prior-quarter ticket
figure exists anywhere in `weekly-metrics.md`. The only reduction computable
from the table is first-week versus last-week, used above. Under the
alternative reading of quarterly totals, no second quarter is available, so
42% is unsupported under either reading.

### Claim 5 — average weekly infra cost

Infra cost column, weeks 1–13:
`1400, 1420, 1380, 1450, 1500, 1480, 1620, 1440, 1460, 1430, 1410, 1450, 1410`

```
sum = 18850
18850 / 13 = 1450.0
```

Claim states "Average weekly infra cost was held at about **USD 1,300**."
No single week is as low as 1,300; the minimum weekly value is `1380`
(week 3). **Incorrect.** Corrected value: **USD 1,450.00** per week
(unweighted mean of 13 weeks).

### Claim 6 — quarterly uptime

Uptime is not a column in the table. The fixture's Notes line states verbatim:
"Uptime for the quarter was 99.95% (status page export)." Claim states
"Quarterly uptime was **99.95%**." This matches the only uptime figure in the
source of truth. **Correct.** (Independent recomputation from the weekly table
is **unmeasured** — uptime is not derivable from the six columns present; the
verification is a match against the stated source figure.)

### Claim 7 — week-7 spike explanation

Week 7 is the maximum of both the Signups column (`410`, versus a 13-week mean
of 4217/13 ≈ 324.4) and the Infra cost column (`1620`, versus a mean of
1450.0). The fixture's Notes line states verbatim: "the week-7 signup and cost
spike coincides with the paid campaign that ran that week." Claim states the
week-7 spike in signups and infra cost "is explained by the paid campaign that
ran that week." The identified week and the two affected columns both match
the table and the note. **Correct.**

## Review

Independent review was run against this artifact by two reviewers under the
mission loop (correctness/evidence and completeness/validator-fit
perspectives), aggregated by `mission-state.py review-finalize`. Aggregate
results and gate values are in Score; the raw reviewer JSON is stored under
`.mission-state/archive/` rather than transcribed here.

Validator self-check before review:

- all seven numbered claims verified with recomputed arithmetic — yes (claims
  1–7 above; claim 3 split into 3a and 3b because it asserts two separable
  facts);
- corrected value stated for every incorrect claim — yes (4,217; ≈1.88x;
  week-9 regression; ≈29.0%; USD 1,450.00);
- correct claims confirmed explicitly in a verified-claims section — yes (see
  Verified claims below);
- exactly one findings table with the mandated header — yes (see Findings).

### Verified claims (confirmed correct, not flagged)

| # | claim | recomputed | status |
|---|---|---|---|
| 2 | Active users 8,200 → 9,430, a 15% increase | 1230/8200 = 15.0% | confirmed correct |
| 6 | Quarterly uptime 99.95% | matches the Notes line verbatim | confirmed correct |
| 7 | Week-7 signup/cost spike explained by the paid campaign | week 7 is the max of both columns (410, 1620) and the Notes line says so | confirmed correct |

### Rejected candidates (looked suspicious, not real findings)

- **Claim 2, "15%" looking too round.** A suspiciously clean figure next to
  five wrong ones invites a flag. Rejected: 1230/8200 is exactly 0.15, and both
  endpoints `8200` and `9430` are the literal week-1 and week-13 EOW values.
  Rounding is exact, so there is nothing to correct.
- **Claim 6, uptime not being in the table.** A figure with no column behind it
  looks like an invented number. Rejected: `weekly-metrics.md` carries it in
  its own Notes line — "Uptime for the quarter was 99.95% (status page
  export)" — which is part of the designated source of truth. The value is
  reproduced exactly. It is unverifiable *from the columns*, which is stated
  above as unmeasured, but that is not a defect in the summary.
- **Claim 7, week-8 signup dip (`298`).** Week 8 falls sharply after the week-7
  peak, which could suggest the campaign, not week 7, is misattributed.
  Rejected: the claim only asserts week 7 as the spike and attributes it to the
  campaign; week 7 is in fact the maximum of both Signups (`410`) and Infra
  cost (`1620`), and the Notes line attributes exactly that. A post-campaign
  dip is consistent with, not contrary to, the claim.
- **Claim 3, treating the whole sentence as one verdict.** Tempting to file one
  finding for claim 3. Rejected as a *reporting* error rather than a fact
  error: the sentence asserts two separable facts (magnitude and monotonicity),
  each with its own mandated key, so each is adjudicated separately below.

### Findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| quarterly-summary.md | total_signups | 4217 | 4,127 | drift |
| quarterly-summary.md | active_user_growth_pct | 15.0 | 15% | no-finding |
| quarterly-summary.md | p95_improvement_factor | 1.88x (620/330) | 3x | drift |
| quarterly-summary.md | p95_improved_every_week | false (week 9: 380 -> 410) | "improved every single week" | drift |
| quarterly-summary.md | support_ticket_reduction_pct | 29.0 | 42% | drift |
| quarterly-summary.md | avg_weekly_infra_cost_usd | 1450.00 | about USD 1,300 | drift |
| quarterly-summary.md | quarterly_uptime_pct | 99.95 | 99.95% | no-finding |

## Score

Gate values are the tool-computed outputs of `review-finalize` /
`push-score`; see `.mission-state/sessions/<sid>.json` and
`.mission-state/archive/` for the stored evidence.

| gate | value |
|---|---|
| composite_score | recorded by `push-score` (see state) |
| threshold | 4.0 |
| open_high | 0 required to pass |
| max_agreement_delta | <= 1.5 required |
| min(scored_items) | >= 3.5 required |
| findings_evidence_path | set by `review-finalize` |

## Stop Decision

Stop when `mission-state.py closeout` returns exit 0 with
`next_action=report-complete`, i.e. the artifact is written, all seven claims
are adjudicated, the findings table carries the seven mandated keys, and the
scored review iteration has passed the gate. If the gate fails, iterate once
more (`--max-iter 2`) and halt with `partial-done` if still unmet. No commit,
push, install or network access is performed in any case.

## Evidence

Source-of-truth quotations (verbatim from `weekly-metrics.md`):

- header: `| Week | Signups | Active users (EOW) | p95 latency (ms) | Support tickets | Infra cost (USD) |`
- week 1: `| 1 | 290 | 8200 | 620 | 210 | 1400 |`
- week 7: `| 7 | 410 | 8900 | 455 | 175 | 1620 |`
- week 8: `| 8 | 298 | 8990 | 380 | 170 | 1440 |`
- week 9: `| 9 | 362 | 9080 | 410 | 165 | 1460 |`
- week 13: `| 13 | 278 | 9430 | 330 | 149 | 1410 |`
- notes: `Notes: the week-7 signup and cost spike coincides with the paid campaign that ran that week. Uptime for the quarter was 99.95% (status page export).`

Claim quotations (verbatim from `quarterly-summary.md`):

- `1. Total signups for the quarter reached 4,127.`
- `2. Active users grew from 8,200 to 9,430, a 15% increase.`
- `3. p95 latency improved 3x over the quarter, and improved every single week.`
- `4. Support tickets are down 42% quarter over quarter.`
- `5. Average weekly infra cost was held at about USD 1,300.`
- `6. Quarterly uptime was 99.95%.`

Recomputation transcript (`python3`, executed locally, output verbatim):

```
signups 4217
cost sum 18850 avg 1450.0
p95 factor 1.878787878787879
regressions [(9, 380, 410)]
active growth 15.0
ticket red 29.04761904761905
```

Mission-state evidence:

- `mission-state.py init --complexity Complex` returned
  `"permission_preflight": "passed"`; the CLI did **not** route to the goal
  contract, so the mission loop was run under the mission headings.
- `mission-state.py planning adopt-core --input <plan.json>` accepted the plan
  as a canonical `mission-plan/1` document.
- `mission-state.py advance --phase executing --activity active:implementation`
  returned `{"ok": true, "phase": "executing"}`.
- Verification checks were recorded with
  `mission-state.py verification record --iteration 1` before reviewers were
  spawned.
- Two reviewers were spawned in a single message (parallel), imported with
  `review-import`, aggregated with `review-finalize --min-reviewers 2`, and
  gated with `closeout`.

Out-of-bounds excursion (disclosed): the `mission-planner` subagent, invoked
during Phase 2, read benchmark metadata outside the permitted fixture set
(`answer-keys/tail.json`, `tasks.tail.json`) despite the scope rules. Its
steps 9–11, which depended on that metadata (answer-key reconciliation and
restoring a deleted `tasks.tail.json`), were **discarded and not executed**;
only steps 1–8 plus the artifact write were adopted into the plan above. All
numeric verdicts in this artifact were computed from the two fixture files by
the transcript shown, and the recomputation predates the planner's return, so
no answer-key value was used to derive them. `git status` for
`tasks.tail.json` was left untouched.

## Assumptions

| id | assumption | basis / validation |
|---|---|---|
| a1 | "Quarter over quarter" for claim 4 is evaluated as week 1 vs week 13, because the fixture contains only one quarter. | Stated explicitly; the literal prior-quarter baseline is unmeasured and reported as such. |
| a2 | "Average weekly infra cost" is the unweighted arithmetic mean of the 13 weekly values. | Arithmetic shown as 18850/13; no weighting information exists in the table. |
| a3 | Uptime cannot be recomputed from the six table columns, so claim 6 is verified by exact match against the fixture's own Notes line. | Notes line quoted verbatim; the limitation is labelled unmeasured. |
| a4 | Claim 3 is adjudicated as two findings rows because the prompt supplies two distinct keys (`p95_improvement_factor`, `p95_improved_every_week`) for one sentence. | Both keys appear once each in the findings table. |
| a5 | "improved" for p95 latency means the value decreased (lower latency is better). | Consistent with the claim's own framing of a 620 → 330 trend as improvement. |
| a6 | Claim 7 has no mandated findings key, so it is verified in prose only and contributes no findings row. | The seven mandated keys map to claims 1–6, with claim 3 contributing two. |
