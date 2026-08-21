# tail-metrics-reconciliation — mission arm (rep2)

## Mission

Fact-check every numbered claim in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
against the raw table in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`,
recompute each figure from the table, mark each claim correct or incorrect,
give the corrected value with the arithmetic shown, and confirm correct claims
explicitly rather than flagging them.

Scope: the two fixture files above (read-only), this artifact (write), and
`.mission-state/` (mission arm state). No commits, no network, no package
installs. No other file under `benchmarks/mission-vs-goal/` was opened, listed,
or grepped. This artifact makes no claim about benchmark arm superiority.

Complexity: Complex. Routing: the mission state CLI did **not** route this task
to the goal contract (`init` returned an active mission session, no
`route: "goal"` verdict and no `routed-goal` halt), so the mission loop was run
as the implementer role.

## Plan

Canonical plan adopted via `mission-state.py planning adopt-core`
(`mission-plan/1`, digest
`sha256:a40b09a71bafb4e4cc11c6ebebf3496aacb6b4e2569fa94567d2caad4e55d220`,
stored at `.mission-state/plans/a40b09a71bafb4e4.json`).

| Step | Action | Acceptance check |
|---|---|---|
| S1 | Read both fixtures verbatim | Both fixtures read in full; no other benchmark file opened |
| S2 | Recompute every derived figure from the table rows | Signup sum, growth %, p95 ratio, per-week p95 monotonicity, ticket reduction %, mean infra cost, uptime, week-7 spike all recomputed |
| S3 | Adjudicate each required key | Each key has expected/actual/verdict backed by a quoted fixture value; no compliant item asserted as drift |
| S4 | Write the artifact | Required headings present; exactly one findings table; correct claims confirmed in a verified-claims section |
| S5 | Verification record → 2 parallel reviewers → `review-finalize` → `closeout` | `closeout` exits 0 |

## Execution

### Source of truth (weekly-metrics.md, 13 rows)

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

Trailing note in the same file (also part of the source of truth): "the week-7
signup and cost spike coincides with the paid campaign that ran that week.
Uptime for the quarter was 99.95% (status page export)."

### Claim-by-claim recomputation

**Claim 1 — "Total signups for the quarter reached 4,127." → INCORRECT.**
Sum of the Signups column:
290 + 310 + 325 + 301 + 340 + 355 + 410 + 298 + 362 + 330 + 342 + 276 + 278
Running total: 290, 600, 925, 1226, 1566, 1921, 2331, 2629, 2991, 3321, 3663,
3939, **4217**.
Corrected value: **4,217** (the claim's `4,127` transposes the middle digits;
difference = 4217 − 4127 = 90).

**Claim 2 — "Active users grew from 8,200 to 9,430, a 15% increase." → CORRECT.**
Endpoints match the table exactly (week 1 EOW `8200`, week 13 EOW `9430`).
Growth = (9430 − 8200) / 8200 = 1230 / 8200 = 0.15 = **15.0%** exactly.
No correction needed; confirmed correct.

**Claim 3a — "p95 latency improved 3x over the quarter" → INCORRECT.**
Week 1 p95 = `620` ms, week 13 p95 = `330` ms.
Improvement factor = 620 / 330 = **1.88x** (1.8788…, 2 d.p.).
Absolute improvement = 620 − 330 = 290 ms, i.e. a 46.8% reduction
(290 / 620 = 0.4677). A 3x improvement would require an end value of
620 / 3 = 206.7 ms, which no week reaches (minimum observed is `330`).
Corrected value: **≈1.88x** (not 3x).

**Claim 3b — "and improved every single week" → INCORRECT.**
Week-over-week p95 deltas (negative = improvement):
−20, −30, −25, −25, −30, −35, −75, **+30**, −15, −25, −20, −20.
The week 8 → week 9 transition regressed: `380` ms → `410` ms (+30 ms).
Corrected value: **p95 improved in 11 of the 12 week-over-week transitions;
it regressed once, from 380 ms (week 8) to 410 ms (week 9).**

**Claim 4 — "Support tickets are down 42% quarter over quarter." → INCORRECT.**
The fixture contains no prior-quarter data, so the only computable reduction is
within the quarter, week 1 → week 13: tickets `210` → `149`.
Reduction = (210 − 149) / 210 = 61 / 210 = 0.29048 = **29.0%** (29.05%, 2 d.p.).
Corrected value: **≈29.0%** (not 42%). To reach 42% the final week would need
210 × (1 − 0.42) = 121.8 tickets; the lowest value in the column is `149`.

**Claim 5 — "Average weekly infra cost was held at about USD 1,300." → INCORRECT.**
Sum of the Infra cost column:
1400 + 1420 + 1380 + 1450 + 1500 + 1480 + 1620 + 1440 + 1460 + 1430 + 1410 +
1450 + 1410 = **18,850**.
Mean = 18850 / 13 = **USD 1,450.00** exactly.
Corrected value: **USD 1,450 per week**. Additionally, no single week is at or
below 1,300 — the minimum observed weekly cost is `1380` (week 3) — so 1,300 is
not defensible as an approximation under any reading.

**Claim 6 — "Quarterly uptime was 99.95%." → CORRECT.**
Uptime is not a column of the weekly table; the source-of-truth file states it
in its trailing note: "Uptime for the quarter was 99.95% (status page export)."
The claim reproduces that value exactly. Confirmed correct. Note: this value is
asserted by the fixture, not recomputable from the table's columns — the check
performed is an exact-match reconciliation against the stated source value, and
the underlying uptime measurement itself is unmeasured here.

**Claim 7 — "The week-7 spike in signups and infra cost is explained by the
paid campaign that ran that week." → CORRECT.**
Week 7 is the maximum of both columns: signups `410` (next highest is `362` in
week 9) and infra cost `1620` (next highest is `1500` in week 5). The
source-of-truth note attributes exactly this: "the week-7 signup and cost spike
coincides with the paid campaign that ran that week." Confirmed correct. The
fixture note establishes coincidence; strict causal attribution is unmeasured,
but the claim matches the source statement and both spikes are real, so it is
not flagged.

## Review

Validator restated and checked:

1. *"Verify all seven claims with recomputed arithmetic"* — all seven numbered
   claims are covered above. Claim 3 carries two independently checkable
   assertions (a 3x factor and a "every single week" universal), each recomputed
   separately as 3a and 3b, matching the two required findings keys
   `p95_improvement_factor` and `p95_improved_every_week`.
2. *"State the corrected value for every incorrect claim"* — corrected values
   given: 4,217; ≈1.88x; "11 of 12 transitions, regression 380→410 at week 8→9";
   ≈29.0%; USD 1,450.
3. *"Confirm correct claims explicitly in a verified-claims section"* — see
   Verified claims below.

### Verified claims (correct — confirmed, not flagged)

| # | Claim | Recomputed check | Status |
|---|---|---|---|
| 2 | Active users grew 8,200 → 9,430, a 15% increase | 1230 / 8200 = 0.15 = 15.0% | Correct as written |
| 6 | Quarterly uptime was 99.95% | Exact match to fixture note "Uptime for the quarter was 99.95% (status page export)" | Correct as written |
| 7 | Week-7 spike in signups and infra cost explained by the paid campaign | Week 7 is the max of both columns (`410`, `1620`); fixture note states the campaign ran that week | Correct as written |

### Rejected candidates (looked suspicious, not real findings)

- **Claim 2's endpoints (8,200 / 9,430).** Suspicious because a summary quoting
  a round "15%" alongside two hand-picked endpoints is a common place to hide a
  rounded or cherry-picked figure. Rejected: both endpoints are verbatim table
  values (week 1 `8200`, week 13 `9430`) and 1230/8200 = 0.15 exactly, with no
  rounding at all. Reporting this would be a false drift.
- **Claim 2 phrased as "grew from 8,200"** while 8,200 is the *end-of-week-1*
  active-user count, not a pre-quarter baseline. Suspicious as an off-by-one
  window. Rejected: the fixture provides no earlier baseline, so week-1 EOW is
  the only available start value and the summary's own framing matches it. Not
  a defect in the claim.
- **Claim 6 uptime, because it cannot be derived from any table column.**
  Suspicious because an unverifiable number in a summary is normally a red flag.
  Rejected: the source-of-truth file states it explicitly in its notes, and the
  claim matches that statement character for character. Verdict is `no-finding`
  on reconciliation grounds; the independent accuracy of the status-page export
  is out of scope and unmeasured.
- **Claim 7's causal wording ("is explained by").** Suspicious because the
  fixture says "coincides with", which is weaker than causation. Rejected as a
  finding: the numeric substance of the claim (a genuine week-7 spike in both
  signups and cost) is confirmed by the table maxima and the fixture attributes
  it to the campaign, so treating a coincide/explain wording gap as a metric
  defect would be a false drift. Noted as a caveat only. Claim 7 also has no
  assigned findings key in the adjudication list, so it is confirmed in prose
  and in the verified-claims table rather than in the findings table.
- **Week 8 signups (`298`) as a possible data error**, since it breaks the
  otherwise upward signup trend right after the campaign week. Rejected: no
  claim depends on week-8 signups, and a post-campaign dip is not a
  reconciliation defect. There is no source to compare it against.
- **Support-ticket reduction measured on column totals instead of endpoints.**
  Suspicious as an alternative reading that might rescue the 42% claim: the sum
  of tickets is 210+205+198+190+186+180+175+170+165+160+155+152+149 = 2,295,
  but with no prior-quarter total there is nothing to divide by, so this reading
  yields no percentage at all and cannot produce 42% either. The endpoint
  reading (29.0%) stands; the claim is still a confirmed drift.

## Score

Self-assessment against the validator, before the reviewer pass:

| Axis | Assessment | Basis |
|---|---|---|
| Completeness | All 7 numbered claims verified; all 7 required findings keys present | Sections above |
| Arithmetic correctness | Every figure recomputed from table rows with running totals or explicit division shown | Execution section |
| Evidence discipline | Every drift quotes the exact fixture value (`4,127`, `620`, `330`, `380`, `410`, `210`, `149`, `1380`, `1620`) | Findings table + prose |
| False-positive risk | 6 candidates examined and explicitly rejected with reasons | Rejected candidates |

Machine-computed gate values from `mission-state.py review-finalize` /
`closeout` are recorded in `.mission-state/` and reproduced in Evidence below.

## Stop Decision

Stop when: the artifact is written with all required headings and exactly one
findings table, all seven claims are verified with arithmetic, corrected values
are stated for every incorrect claim, correct claims are confirmed in the
verified-claims section, and `mission-state.py closeout` exits 0 within
`--max-iter 2`. No commit, push, install, or network access is performed.

## Evidence

### Findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| quarterly-summary.md | total_signups | 4217 | 4,127 | drift |
| quarterly-summary.md | active_user_growth_pct | 15.0 | 15% | no-finding |
| quarterly-summary.md | p95_improvement_factor | 1.88x | 3x | drift |
| quarterly-summary.md | p95_improved_every_week | false (regression week 8 380ms -> week 9 410ms) | "improved every single week" | drift |
| quarterly-summary.md | support_ticket_reduction_pct | 29.0 | 42% | drift |
| quarterly-summary.md | avg_weekly_infra_cost_usd | 1450 | about USD 1,300 | drift |
| quarterly-summary.md | quarterly_uptime_pct | 99.95 | 99.95% | no-finding |

### Quoted fixture evidence per confirmed finding

- `total_signups`: summary states "Total signups for the quarter reached
  4,127."; table Signups column sums to 4217 (weeks: `290`, `310`, `325`, `301`,
  `340`, `355`, `410`, `298`, `362`, `330`, `342`, `276`, `278`).
- `p95_improvement_factor`: summary states "p95 latency improved 3x over the
  quarter"; table p95 week 1 = `620`, week 13 = `330`; 620/330 = 1.88.
- `p95_improved_every_week`: summary states "improved every single week"; table
  p95 week 8 = `380`, week 9 = `410` — a 30 ms regression.
- `support_ticket_reduction_pct`: summary states "Support tickets are down 42%
  quarter over quarter."; table Support tickets week 1 = `210`, week 13 = `149`;
  (210−149)/210 = 29.0%.
- `avg_weekly_infra_cost_usd`: summary states "Average weekly infra cost was
  held at about USD 1,300."; Infra cost column sums to 18850 over 13 weeks =
  1450; column minimum is `1380`.

### No-finding evidence

- `active_user_growth_pct`: summary "grew from 8,200 to 9,430, a 15% increase";
  table Active users week 1 = `8200`, week 13 = `9430`; 1230/8200 = 15.0%.
- `quarterly_uptime_pct`: summary "Quarterly uptime was 99.95%."; weekly-metrics
  note "Uptime for the quarter was 99.95% (status page export)."

### Mission-state evidence (auditable loop)

- Session: `.mission-state/sessions/cc-60ec73e0-8be8-454f-8d94-839e9bae3e3c.json`
  (mission id `9b42f6625530d5c3`, complexity Complex, `permission_preflight: passed`).
- Routing: not routed to goal — `init` created an active mission session; no
  `route: "goal"` verdict and no `routed-goal` halt was returned.
- Plan: `.mission-state/plans/a40b09a71bafb4e4.json`, digest
  `sha256:a40b09a71bafb4e4cc11c6ebebf3496aacb6b4e2569fa94567d2caad4e55d220`.
- Verification record, review imports, `review-finalize` output and `closeout`
  result are stored under `.mission-state/` and summarised in the completion
  note below.
- Recomputation was executed, not eyeballed: the column sums, the mean, the
  growth percentage, the p95 ratio and the full week-over-week delta list were
  produced by running arithmetic over the transcribed table rows, and the
  transcription was taken verbatim from the fixture read.

### Completion note (filled after the gated loop)

- `review-finalize` (iteration 1, 2 reviewers): PENDING — values are filled in
  only after the command has actually run; they are not predicted here.
- `closeout`: PENDING — filled in from the actual command exit and
  `next_action`.

## Assumptions

- **A1 — Source-of-truth boundary.** The weekly-metrics file *including its
  trailing notes* is the source of truth. Claims 6 and 7 are checkable only
  against those notes; without them both would be unmeasured rather than
  correct. Validation: both fixtures read verbatim and quoted above.
- **A2 — "Quarter over quarter" reading for claim 4.** The fixture contains no
  prior-quarter data, so the ticket reduction is evaluated week 1 → week 13
  (29.0%). Under any reading the summary's 42% is unreachable: the minimum
  ticket count in the column is `149`, and 42% off 210 would require 121.8. The
  true prior-quarter comparison is **unmeasured** — no prior-quarter data
  exists in scope.
- **A3 — Rounding conventions.** Percentages are reported to one decimal place
  and the p95 factor to two decimals. Exact quotients used: 1230/8200 = 0.15
  (exact), 61/210 = 0.290476…, 620/330 = 1.878787…, 18850/13 = 1450 (exact).
- **A4 — Claim 3 splits into two findings rows.** The adjudication list supplies
  two keys for the single numbered claim 3, so the factor and the
  every-week universal are adjudicated separately.
- **A5 — Claim 7 has no findings key.** The adjudication list names seven keys
  and none corresponds to the week-7 campaign claim, so it is verified in prose
  and in the verified-claims table only; the findings table contains exactly the
  seven required rows.
- **A6 — Uptime is reconciled, not independently measured.** The 99.95% figure
  is asserted by the fixture's status-page note. Its real-world accuracy is
  unmeasured here; the check is an exact-match reconciliation.
- **A7 — Local authoring sync skipped.** The `/mission` bootstrap normally runs
  `mission-local-authoring-sync.sh` first, which needs network access. The run
  rules forbid network access, so it was skipped deliberately and the
  repository-local `scripts/mission-state.py` was used instead. Effect on the
  result: unmeasured, but the state CLI ran from this checkout without error.
- **A8 — No benchmark metadata consulted.** Task definitions, scoring
  configuration and answer keys under `benchmarks/mission-vs-goal/` were not
  opened, listed, or grepped. All verdicts derive only from the two fixtures.
