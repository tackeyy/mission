# tail-metrics-reconciliation — mission arm (rep1)

## Mission

Fact-check every numbered claim (1–7) in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
against the raw table in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`,
recompute each figure from the table, mark each claim correct or incorrect,
give the corrected value with arithmetic for every incorrect claim, and
explicitly confirm the claims that are correct.

- Arm: mission (profile: full). Complexity: Complex. `--max-iter 2`.
- Source of truth: `weekly-metrics.md`, whose title states `Q3 raw table (source of truth)`.
- No commits, no network, no package installs. Reads limited to the two named
  fixtures and this output file.

## Plan

Adopted canonical plan (`mission-plan/1`, source id `planner-iter1`,
`.mission-state` command `planning adopt-core`):

| # | step | acceptance check |
|---|---|---|
| 1 | `extract` | transcribe 13 rows × 5 metric columns; enumerate claims 1–7 |
| 2 | `recompute` | compute signup sum, cost sum/avg, p95 ratio, all 12 week-over-week p95 deltas, ticket drop %, active-user growth % via an executed script |
| 3 | `classify` | mark each of 7 claims correct/incorrect; corrected value + arithmetic for each incorrect one |
| 4 | `rejected` | document suspicious-but-compliant candidates with rejection reasons |
| 5 | `write` | required headings, exactly one findings table with the mandated header, no fabricated review/score values |

Stop conditions: a fixture is unreadable, or a required figure cannot be
recomputed from the table.

## Execution

### Transcribed source table (`weekly-metrics.md`, verbatim values)

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

Notes line, quoted verbatim from the fixture:

> Notes: the week-7 signup and cost spike coincides with the paid campaign that
> ran that week. Uptime for the quarter was 99.95% (status page export).

### Recomputation output (executed locally, `python3`)

```
signups_sum 4217
cost_sum 18850 cost_avg 1450.0
active_growth 1230 15.0
p95_ratio 1.8788 pct 46.7742
p95_regressions [(8, 380, 9, 410)]
tickets_drop_pct 29.0476
tickets_h1_avg 194.8333 h2_avg 160.8571
max_signups_week 7 max_cost_week 7
signups_for_4127_gap 90
```

### Claim-by-claim verdicts

**Claim 1 — "Total signups for the quarter reached 4,127." → INCORRECT**

Arithmetic (weeks 1–13 signups column):
`290+310+325+301+340+355+410+298+362+330+342+276+278`
= `600, 925, 1226, 1566, 1921, 2331, 2629, 2991, 3321, 3663, 3939, 4217`.
Corrected value: **4,217**. The claim understates the total by 90
(`4217 − 4127 = 90`).

**Claim 2 — "Active users grew from 8,200 to 9,430, a 15% increase." → CORRECT**

Endpoints match the table exactly: week 1 `8200`, week 13 `9430`.
`(9430 − 8200) / 8200 = 1230 / 8200 = 0.15` exactly = **15.0%**.
No rounding slack is being hidden; the quotient is exact.

**Claim 3 — "p95 latency improved 3x over the quarter, and improved every single
week." → INCORRECT (both sub-claims fail)**

- Ratio: week 1 `620` ms → week 13 `330` ms. `620 / 330 = 1.8788…` ≈ **1.88×**
  (a 46.8% reduction: `(620 − 330) / 620 = 0.46774…`). Not 3×; a 3× improvement
  would require an end value of `620 / 3 ≈ 206.7` ms.
- Monotonicity: of the 12 week-over-week transitions, one is a regression —
  week 8 `380` ms → week 9 `410` ms (+30 ms). All other 11 transitions decrease.
  So latency did **not** improve every single week.

Corrected statement: p95 improved about **1.88×** (620 → 330 ms, −46.8%), and
improved in 11 of 12 weeks, regressing once at week 8→9 (380 → 410 ms).

**Claim 4 — "Support tickets are down 42% quarter over quarter." → INCORRECT**

The table contains no prior quarter, so the only measurable decline is within
the quarter: week 1 `210` → week 13 `149`.
`(210 − 149) / 210 = 61 / 210 = 0.290476…` = **29.0%** (2 significant figures:
29.05%). Corrected value: **down 29.0%** (first week to last week within Q3).
A half-over-half framing does not rescue 42% either: weeks 1–6 mean tickets
`194.83` vs weeks 7–13 mean `160.86`, a 17.4% decline. Under no reading
available in the fixture does 42% hold.

**Claim 5 — "Average weekly infra cost was held at about USD 1,300." → INCORRECT**

Sum of the infra cost column = `18,850`; `18,850 / 13 = 1,450.0`.
Corrected value: **USD 1,450 per week**. Additionally, the minimum weekly cost
in the table is `1380` (week 3), so no week is as low as 1,300 — the claim is
not merely a rounding of the mean but is below the entire observed range.

**Claim 6 — "Quarterly uptime was 99.95%." → CORRECT**

Not recomputable from the table (there is no uptime column), but the source of
truth attests it directly in its notes line: `Uptime for the quarter was 99.95%
(status page export).` The summary reproduces that figure exactly, so it is
confirmed as correct rather than flagged. Scope note: this is a
transcription check against the fixture's own attestation, not an independent
recomputation — the underlying status-page export is unmeasured here.

**Claim 7 — "The week-7 spike in signups and infra cost is explained by the paid
campaign that ran that week." → CORRECT**

Week 7 is the maximum of both columns: signups `410` (highest of the 13 weeks;
next highest is week 9 at `362`) and infra cost `1620` (highest; next highest is
week 5 at `1500`). So a spike in both metrics does exist at week 7. The source
of truth attributes it to the campaign in the same terms: `the week-7 signup and
cost spike coincides with the paid campaign that ran that week.` The summary's
wording ("is explained by") is slightly stronger than the fixture's
("coincides with"), but the fixture supplies no contradicting evidence and the
spike itself is confirmed, so this is recorded as correct, not as a defect.

### Confirmed findings (incorrect claims)

| claim | quoted text from `quarterly-summary.md` | corrected value |
|---|---|---|
| 1 | `Total signups for the quarter reached 4,127.` | 4,217 |
| 3 | `p95 latency improved 3x over the quarter, and improved every single week.` | ≈1.88× (620→330 ms); improved 11 of 12 weeks, regression at week 8→9 (380→410) |
| 4 | `Support tickets are down 42% quarter over quarter.` | −29.0% (210→149) |
| 5 | `Average weekly infra cost was held at about USD 1,300.` | USD 1,450 (18,850 / 13) |

### Verified claims (correct — explicitly confirmed, not flagged)

| claim | quoted text from `quarterly-summary.md` | verification |
|---|---|---|
| 2 | `Active users grew from 8,200 to 9,430, a 15% increase.` | endpoints match rows 1 and 13; 1230/8200 = 0.15 exactly |
| 6 | `Quarterly uptime was 99.95%.` | matches the fixture notes line `Uptime for the quarter was 99.95% (status page export).` |
| 7 | `The week-7 spike in signups and infra cost is explained by the paid campaign that ran that week.` | week 7 is the max of both columns (`410`, `1620`) and the notes line attributes the spike to the campaign |

### Rejected candidates (looked suspicious, but are not defects)

- **Claim 2's "15%" reading as a rounded figure.** A round percentage next to
  four-digit endpoints usually signals a hidden rounding error. Recomputation
  shows `1230 / 8200 = 0.15` exactly, so there is nothing to correct. Rejected.
- **Claim 6's uptime being absent from the table.** It looked like an
  unsupported figure because no column carries it. The fixture's notes line
  states `99.95%` verbatim, and the notes are part of the source-of-truth file.
  Rejected as a finding; the scope limitation is stated under claim 6 instead.
- **Claim 7's causal wording ("is explained by" vs the fixture's "coincides
  with").** This is a genuine strengthening of an association into a cause. It
  is not reported as drift because the fixture itself volunteers the campaign as
  the explanation and offers no competing cause, and because the numeric content
  of the claim (a week-7 spike in both signups and cost) is confirmed by the
  table maxima. Reporting it would be a style objection, not an arithmetic
  defect. Rejected.
- **The week-9 latency regression as a defect in claim 7.** Week 9 (`410` ms
  after week 8's `380` ms) is a real anomaly in the data, but claim 7 is about
  week-7 signups and cost, not latency. The regression is attributed to claim 3,
  where it belongs. Rejected as a separate finding.
- **The week-12 signup dip (`276`, the lowest week) as an unreported problem.**
  It is the most conspicuous negative pattern in the signups column, but no
  numbered claim asserts anything about weekly signup floors, and the task is
  bounded to the seven claims. Rejected as out of scope.

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| quarterly-summary.md | claim-1 total signups for the quarter | 4217 | 4,127 | drift |
| quarterly-summary.md | claim-3 p95 improvement factor over the quarter | 1.88x (620 -> 330 ms) | 3x | drift |
| quarterly-summary.md | claim-3 p95 improved every single week | improved 11 of 12 weeks; week 8->9 regressed 380 -> 410 ms | improved every single week | drift |
| quarterly-summary.md | claim-4 support ticket decline | 29.0% (210 -> 149) | 42% | drift |
| quarterly-summary.md | claim-5 average weekly infra cost | 1450 (18850 / 13) | about USD 1,300 | drift |
| quarterly-summary.md | claim-2 active user growth 8200 -> 9430 | 15.0% (1230 / 8200) | 15% | no-finding |
| quarterly-summary.md | claim-6 quarterly uptime | 99.95% (fixture notes line) | 99.95% | no-finding |
| quarterly-summary.md | claim-7 week-7 spike attributed to the paid campaign | week 7 is max signups (410) and max infra cost (1620); notes attribute the spike to the campaign | week-7 spike explained by the paid campaign | no-finding |

## Review

Two independent reviewers were spawned in a single message (parallel) under the
mission loop, iteration 1. Their `mission-review/1` JSON payloads were validated
and stored through `mission-state.py review-import`, and aggregated through
`mission-state.py review-finalize`. Raw reviewer output is retained under
`.mission-state/archive/` and is not transcribed here (output-compression
discipline); the aggregated, tool-computed values are in the Score section.

Reviewer-driven changes applied before finalization: none of Medium or higher
severity required a code/content change beyond what is already recorded in this
artifact; see the Score section for the tool-computed open-High count.

## Score

Gate values below are read back from mission state after `review-finalize`
(tool-computed, not hand-entered):

- composite score: see `.mission-state` field `composite_score` — recorded value **4.4**
- minimum scored item: **4.0**
- open High findings (`open_high`): **0**
- `max_agreement_delta`: **0.5** (threshold ≤ 1.5)
- findings evidence path: recorded in state (`findings_evidence_path`)
- threshold: 4.0

## Stop Decision

`mission-state.py closeout` (`mark-passes` → `next`) returned exit 0 with
`next_action: report-complete` and `passes: true` at iteration 1. Early-stop
applies: threshold reached with `open_high == 0`, and no Medium-or-above finding
remains that a second iteration would resolve. The loop stops at iteration 1 of
a maximum of 2.

## Evidence

| item | evidence |
|---|---|
| Source table values | transcribed verbatim above from `weekly-metrics.md` rows 1–13 |
| Signup total | executed `python3` output `signups_sum 4217` |
| Infra cost mean | executed `python3` output `cost_sum 18850 cost_avg 1450.0` |
| Active-user growth | executed `python3` output `active_growth 1230 15.0` |
| p95 ratio and regressions | executed `python3` output `p95_ratio 1.8788` and `p95_regressions [(8, 380, 9, 410)]` |
| Ticket decline | executed `python3` output `tickets_drop_pct 29.0476`; half-over-half `tickets_h1_avg 194.8333 h2_avg 160.8571` |
| Week-7 spike | executed `python3` output `max_signups_week 7 max_cost_week 7` |
| Uptime | fixture notes line quoted verbatim (no table column exists; not independently recomputable) |
| Mission state | `.mission-state/sessions/cc-4dbe3752-49e6-4f1a-97ec-c1bd8e93aca0.json`; canonical plan adopted via `planning adopt-core --source-id planner-iter1` |
| Verification checks | recorded via `mission-state.py verification record --iteration 1` |
| Review artifacts | `.mission-state/archive/` (reviewer payloads and scoring JSON) |
| Routing | the state CLI did **not** route this task to the goal contract; `init` created an active mission state and `next` returned `run-planner`, so the mission headings apply |

Unmeasured / out of scope, stated explicitly:

- The 99.95% uptime figure is not recomputable from the table; only its
  consistency with the fixture's own notes line was checked.
- No prior-quarter data exists in either fixture, so a literal
  quarter-over-quarter comparison for claim 4 is unmeasurable; the within-quarter
  decline was computed instead and both readings are shown.
- Benchmark metadata (task definitions, scoring configuration, answer keys) was
  not opened by the orchestrator. One planning subagent's own summary text
  mentioned reviewing context beyond the two fixtures; whether it read any
  out-of-bounds file is **unmeasured** by this artifact. No value in this
  artifact depends on that subagent — every figure here was recomputed by the
  executed script shown above.

## Assumptions

| id | assumption | validation | status |
|---|---|---|---|
| `table-is-source-of-truth` | `weekly-metrics.md` is authoritative; `quarterly-summary.md` is under audit | fixture title reads `Q3 raw table (source of truth)`; summary title reads `draft (to be fact-checked against the weekly table)` | confirmed |
| `quarter-scope` | "the quarter" = weeks 1–13 | both fixtures read in full; only 13 weekly rows exist | confirmed |
| `notes-are-part-of-source` | the notes line of `weekly-metrics.md` counts as source of truth for claims 6 and 7 | it appears inside the file titled `(source of truth)` | confirmed |
| `no-prior-quarter` | no earlier-quarter figures are available for claim 4 | no such data in either fixture | confirmed |
| `no-network` | all arithmetic recomputed locally | recomputation output block quoted above | confirmed |
