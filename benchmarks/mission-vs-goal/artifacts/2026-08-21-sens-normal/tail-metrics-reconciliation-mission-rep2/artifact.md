# tail-metrics-reconciliation — mission arm (rep2)

## Mission

Fact-check every numbered claim (1–7) in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
against the raw table in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`,
recompute each figure from the table, mark each claim correct or incorrect,
state the corrected value with arithmetic for every incorrect claim, and
explicitly confirm the claims that are correct.

Scope: the two fixture files above (read) and this artifact (write), plus
`.mission-state/` for mission bookkeeping. No commits, pushes, package
installs, or network access were performed. No benchmark metadata (task
definitions, scoring configuration, answer keys) was opened, listed, or
grepped.

Complexity: Complex. Arm: mission. Profile: full. `--max-iter 2`.

## Plan

Adopted as a canonical `mission-plan/1` document via
`mission-state.py planning adopt-core` (generation 1, validated
2026-08-21T01:46:15Z).

| step | action | acceptance check |
|---|---|---|
| s1 | Read both fixtures; enumerate the seven claims verbatim | seven claims enumerated |
| s2 | Recompute each claim's figure from the raw table with explicit arithmetic | every claim has shown arithmetic |
| s3 | Re-verify every sum/average/ratio with an independent tool run | tool-computed totals match prose totals |
| s4 | Classify correct/incorrect; separate confirmed findings from rejected candidates | correct claims explicitly confirmed; rejections justified |
| s5 | Write the artifact with the eight required headings and exactly one findings table | headings present; verdicts only `drift`/`no-finding` |
| s6 | Run two independent reviewers; finalize the scored review iteration | `review-finalize` and `closeout` exit 0 |

Deviation from the default Complex flow: the iteration-1 plan was authored
inline by the orchestrator rather than by a spawned `mission-planner`
subagent, to keep this controlled run bounded. The plan still went through
the same `planning adopt-core` validation gate (two rejections — `resource-invalid`,
then `step-invalid` — before acceptance), so the plan contract was machine-checked,
not self-asserted. The reviewer stage was **not** shortcut: two independent
reviewers were spawned in parallel.

## Execution

Source of truth, `weekly-metrics.md` (13 rows, weeks 1–13):

| Week | Signups | Active users (EOW) | p95 latency (ms) | Support tickets | Infra cost (USD) |
|---:|---:|---:|---:|---:|---:|
| 1 | 290 | 8200 | 620 | 210 | 1400 |
| 2 | 310 | 8310 | 600 | 205 | 1420 |
| 3 | 325 | 8420 | 570 | 198 | 1380 |
| 4 | 301 | 8500 | 545 | 190 | 1450 |
| 5 | 340 | 8610 | 520 | 186 | 1500 |
| 6 | 355 | 8730 | 490 | 180 | 1480 |
| 7 | 410 | 8900 | 455 | 175 | 1620 |
| 8 | 298 | 8990 | 380 | 170 | 1440 |
| 9 | 362 | 9080 | 410 | 165 | 1460 |
| 10 | 330 | 9170 | 395 | 160 | 1430 |
| 11 | 342 | 9260 | 370 | 155 | 1410 |
| 12 | 276 | 9340 | 350 | 152 | 1450 |
| 13 | 278 | 9430 | 330 | 149 | 1410 |

### Claim 1 — "Total signups for the quarter reached 4,127." → INCORRECT

Arithmetic (running sum over the Signups column):
290 + 310 = 600; +325 = 925; +301 = 1226; +340 = 1566; +355 = 1921;
+410 = 2331; +298 = 2629; +362 = 2991; +330 = 3321; +342 = 3663;
+276 = 3939; +278 = **4217**.

Corrected value: **4,217**. The summary states `4,127`, which is 90 low and
consistent with a transposition of the last two digits (`4,217` → `4,127`).

### Claim 2 — "Active users grew from 8,200 to 9,430, a 15% increase." → CORRECT

Endpoints match the table exactly: week 1 EOW `8200`, week 13 EOW `9430`.
Growth = (9430 − 8200) / 8200 = 1230 / 8200 = 0.15 = **15.0%** exactly.
All three figures in the claim are confirmed.

### Claim 3 — "p95 latency improved 3x over the quarter, and improved every single week." → INCORRECT (both parts)

Part A (magnitude): 620 ms (week 1) → 330 ms (week 13).
620 / 330 = **1.879x** (a 46.8% reduction: (620 − 330) / 620 = 290 / 620 = 46.77%).
A true 3x improvement would require an end value of 620 / 3 ≈ 206.7 ms.
Corrected value: **≈1.88x (46.8% reduction)**, not 3x.

Part B (monotonicity): the series is not monotonically improving. Week 8 is
`380` and week 9 is `410` — latency **rose by 30 ms** between those weeks. It
is the only such week pair; every other week-over-week step decreases.
Corrected statement: improved in 11 of the 12 week-over-week steps, with a
regression at week 9.

### Claim 4 — "Support tickets are down 42% quarter over quarter." → INCORRECT

Within the available data the decline is week 1 → week 13:
(210 − 149) / 210 = 61 / 210 = 0.29048 = **29.0%**.
Corrected value: **≈29.0%** (210 → 149). A 42% decline would require an end
value of 210 × 0.58 = 121.8 tickets, which no week in the table reaches (the
minimum is `149` at week 13).

Note on wording: the fixture contains no prior-quarter data, so a literal
quarter-over-quarter comparison is not computable from the source of truth.
This claim is marked incorrect on the strongest available reading
(first week vs. last week of the quarter), which is also the reading most
favourable to the summary — see Assumptions.

### Claim 5 — "Average weekly infra cost was held at about USD 1,300." → INCORRECT

Sum of the Infra cost column:
1400 + 1420 = 2820; +1380 = 4200; +1450 = 5650; +1500 = 7150; +1480 = 8630;
+1620 = 10250; +1440 = 11690; +1460 = 13150; +1430 = 14580; +1410 = 15990;
+1450 = 17440; +1410 = **18850**.
Mean = 18850 / 13 = **1450.0**.

Corrected value: **USD 1,450 per week**. The claimed ~1,300 is 11.5% below the
actual mean and below *every* individual week — the cheapest week is `1380`
(week 3) — so "about 1,300" is not defensible as rounding.

### Claim 6 — "Quarterly uptime was 99.95%." → CORRECT

Uptime is not a column in the table, but the source-of-truth file's own Notes
line states: "Uptime for the quarter was 99.95% (status page export)." The
claim matches the source verbatim. Not independently recomputable from the
weekly rows; confirmed against the fixture's stated value only.

### Claim 7 — "The week-7 spike in signups and infra cost is explained by the paid campaign that ran that week." → CORRECT

Week 7 is the maximum of both columns: signups `410` (highest of all 13 weeks;
next highest is `362` at week 9) and infra cost `1620` (highest of all 13
weeks; next highest is `1500` at week 5). The source-of-truth Notes line states:
"the week-7 signup and cost spike coincides with the paid campaign that ran
that week." Both the spike and its attribution are supported by the fixture.

### Confirmed findings (4)

| # | Claim | Claimed | Correct | Evidence quoted from fixture |
|---|---|---|---|---|
| 1 | Total signups | `4,127` | `4,217` | Signups column, weeks 1–13 summed |
| 3 | p95 improvement | `3x` and "improved every single week" | `1.88x`; regression at week 9 | `| 8 | 298 | 8990 | 380 |` vs `| 9 | 362 | 9080 | 410 |` |
| 4 | Support tickets | `down 42%` | `down 29.0%` | `210` (week 1) → `149` (week 13) |
| 5 | Avg weekly infra cost | `about USD 1,300` | `USD 1,450` | 18850 / 13; min week = `1380` |

### Rejected candidates (looked suspicious, are not findings)

- **Claim 2's "15%" rounding.** Suspicious because summary percentages in this
  fixture are otherwise wrong, so 15% invited a rounding challenge. Rejected:
  1230 / 8200 = 0.15 exactly, with no rounding at all.
- **Claim 2's baseline choice (8,200).** Suspicious because "grew from" could
  arguably mean a start-of-quarter value that precedes week 1's EOW figure.
  Rejected: the table has no pre-week-1 row, `8200` is the only value the source
  of truth offers as a starting point, and the claim quotes it exactly.
- **Claim 6 being unverifiable from the table.** Suspicious because uptime has
  no column, which normally means "unmeasured". Rejected as a finding: the
  fixture's own Notes line — the same source of truth — states `99.95%`, and the
  claim reproduces it exactly. Flagging it would be reporting a compliant item.
- **Claim 7's causal wording ("is explained by").** Suspicious because the
  fixture Notes only says the spike "coincides with" the campaign, and
  coincidence is weaker than explanation. Rejected: the task is arithmetic
  reconciliation against the source of truth, both quantitative parts of the
  claim (week 7 is the max for signups and for infra cost) are confirmed, and
  the fixture supplies the campaign attribution itself. The
  coincidence-vs-causation nuance is recorded here rather than asserted as drift.
- **Week 8's signup dip (`298`) and week 12's dip (`276`).** Suspicious as
  anomalies in the raw data. Rejected: no numbered claim references them, so
  they are outside the fact-check scope.

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| quarterly-summary.md | claim-1-total-signups | 4217 | 4127 | drift |
| quarterly-summary.md | claim-2-active-user-growth | 8200 to 9430, 15% | 8200 to 9430, 15% | no-finding |
| quarterly-summary.md | claim-3-p95-improvement | 1.88x, not monotonic (week 9 = 410 > week 8 = 380) | 3x, improved every single week | drift |
| quarterly-summary.md | claim-4-support-ticket-decline | 29.0% | 42% | drift |
| quarterly-summary.md | claim-5-avg-weekly-infra-cost | 1450 | about 1300 | drift |
| quarterly-summary.md | claim-6-quarterly-uptime | 99.95% | 99.95% | no-finding |
| quarterly-summary.md | claim-7-week7-campaign-spike | week 7 max signups 410 and max infra cost 1620, campaign noted | week-7 spike explained by paid campaign | no-finding |

### Verified claims (explicitly confirmed correct)

- **Claim 2** — "Active users grew from 8,200 to 9,430, a 15% increase."
  CONFIRMED CORRECT. 1230 / 8200 = 15.0% exactly; both endpoints match the table.
- **Claim 6** — "Quarterly uptime was 99.95%." CONFIRMED CORRECT against the
  source-of-truth Notes line (not recomputable from the weekly rows).
- **Claim 7** — "The week-7 spike in signups and infra cost is explained by the
  paid campaign that ran that week." CONFIRMED CORRECT; week 7 holds the maximum
  of both columns (`410`, `1620`) and the Notes line attributes it to the campaign.

## Review

Two independent reviewers were spawned in parallel in a single message
(perspectives: `arithmetic-correctness` and `contract-compliance`), per the
Complex tier reviewer count of 2. Each returned a `mission-review/1` JSON
document, imported via `review-import` and aggregated via `review-finalize`
with `--min-reviewers 2` and reviewer windows supplied for both perspectives.

Verification (`mission-state.py verification record --iteration 1`) recorded
tool-executed checks before the reviewers ran: the signup sum, infra-cost sum
and mean, growth percentage, ticket-decline percentage, p95 ratio, and the
non-monotonic week pair were all recomputed by a Python run independent of the
prose arithmetic, and matched.

Reviewer findings and the raw scoring JSON are stored under
`.mission-state/archive/`; per the mission output-compression rule they are
referenced rather than restated here. Aggregated gate values appear below.

## Score

Reviewer scores as submitted (raw, archived):

| perspective | mission_achievement | accuracy | completeness | usability | findings |
|---|---:|---:|---:|---:|---:|
| arithmetic-correctness | 5.0 | 5.0 | 5.0 | 5.0 | 0 |
| contract-compliance | 4.9 | 5.0 | 5.0 | 4.8 | 0 |

`review-finalize --iteration 1 --min-reviewers 2` exited 0 and pushed the
scoring evidence to
`.mission-state/archive/iter-1-0d03667e-scoring-3d25dd994c96d3b3.json`, with
the review aggregate at
`.mission-state/archive/iter-1-0d03667e-reviews-c388a9351f583895.json`
(revision scope git, base = head = `4ac5596ea3144df439701dd8e88253aef16fa78d`).
No reviewer raised any High/Medium/Low finding, so no rework round was needed.

**Unmeasured:** the aggregated gate fields (`composite_score`,
`open_high`, `max_agreement_delta`) were not readable back from the state CLI
at report time — `get --field` returned `null` for each — so this artifact does
not assert a composite score. The pass predicate that would have been applied is:

```
passes = findings_evidence_path exists
  AND evidence_high_count == open_high
  AND max_agreement_delta <= 1.5
  AND composite_score >= 4.0
  AND min(scored_items) >= 3.5
  AND open_high == 0
```

## Stop Decision

Stopped after iteration 1 with the mission **not** marked as passed.
`closeout` (= `mark-passes` → `next`) was run and exited 2 twice with a gate
error unrelated to review quality:

1. `specialist selection checkpoint missing before pass: record
   task_profile.primary and specialists_decision.policy` — addressed by running
   `specialists recommend --record-state`, which returned no eligible
   specialists for this task profile;
2. `specialist selection checkpoint is not terminal or valid: selection
   lifecycle_state must be terminal for decision=none` — still open at stop
   time.

The run budget was exhausted before this bookkeeping gate could be cleared, so
the state was closed with `mark-halt --category partial-done` rather than
`mark-passes`. `--max-iter 2` was not reached; iteration 2 was not run, because
no reviewer finding required rework — the blocker is state bookkeeping, not
artifact content.

Deliverable status: the analysis work is complete (all seven claims verified
with recomputed arithmetic, corrected values stated, correct claims confirmed,
findings table emitted) and the scored review iteration ran to
`review-finalize` exit 0. What did not complete is the `mark-passes` gate. No
pass is claimed.

## Evidence

- Fixture read (source of truth):
  `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`
  (13 data rows; Notes line supplies uptime and the week-7 campaign attribution).
- Fixture read (claims under test):
  `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
  (7 numbered claims).
- Independent recomputation (tool-executed, not mental arithmetic):
  `signups_sum 4217`, `active_growth 15.0`, `p95_ratio 1.878787878787879`,
  `p95_pct 46.774193548387096`, `p95_non_monotonic [(8, 380, 9, 410)]`,
  `tickets_pct 29.04761904761905`, `cost_sum 18850`, `avg 1450.0`,
  `max_signup_week (7, 410)`, `max_cost_week 7 1620`, `min_cost 1380`.
- Mission state: `.mission-state/sessions/cc-a3ef32f0-0d8d-43a1-b360-e94d67b37122.json`
  (mission_id `0d03667ee70d08ff`, fencing epoch 1).
- Plan contract: adopted `mission-plan/1`, generation 1, validated
  `2026-08-21T01:46:15Z` after two machine rejections (`resource-invalid`,
  `step-invalid`).
- Reviewer evidence and scoring JSON: `.mission-state/archive/` (referenced,
  not restated).
- Routing: the CLI did **not** route this task to the goal contract — `init`
  returned an active multi-session mission state (no `route: "goal"` verdict and
  no `routed-goal` halt), so the mission loop was run as implementer.
- Unmeasured: uptime cannot be recomputed from the weekly table (no uptime
  column); it is confirmed only against the fixture's stated `99.95%`. No
  benchmark metadata was read, so no comparison against an answer key was or
  could be made. No claim of benchmark superiority is made.

## Assumptions

- **a1** — `weekly-metrics.md` is the sole source of truth, including its Notes
  line (uptime, campaign attribution), because the file header says
  "source of truth".
- **a2** — For claims about change "over the quarter" or "quarter over quarter",
  the comparison is week 1 vs week 13, because the fixture contains no
  prior-quarter rows. For claim 4 this is the reading most favourable to the
  summary; under any stricter reading the claim is at best unverifiable, so the
  `drift` verdict is not an artefact of this assumption.
- **a3** — "About USD 1,300" (claim 5) is judged against the computed mean of
  1450. Since 1300 is below the minimum weekly value (`1380`), the claim fails
  under any reasonable tolerance for "about", not merely under exact equality.
- **a4** — Claim 3 is treated as two conjoined assertions (3x magnitude, weekly
  monotonicity); a single `drift` row covers both, since both fail.
- **a5** — No commits, pushes, installs, or network access; edits limited to
  this artifact and `.mission-state/`.
