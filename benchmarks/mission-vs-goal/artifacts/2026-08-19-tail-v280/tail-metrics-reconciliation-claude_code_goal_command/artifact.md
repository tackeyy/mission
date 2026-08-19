# tail-metrics-reconciliation — claude_code_goal_command

## Goal

Fact-check every numbered claim (1–7) in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
against the raw table in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`.
Recompute each figure from the table, mark each claim correct or incorrect, and give the
corrected value with arithmetic shown. Correct claims must be confirmed as correct, not flagged.

Only these two fixture files and this output file were opened. No other file under
`benchmarks/mission-vs-goal/` was read, grepped, or listed.

## Result

| # | Claim (abbreviated) | Verdict | Corrected value |
|---|---|---|---|
| 1 | Total signups reached `4,127` | **Incorrect** | **4,217** |
| 2 | Active users `8,200` → `9,430`, a `15%` increase | **Correct** | — (15.0%) |
| 3 | p95 latency improved `3x` and improved **every single week** | **Incorrect** (both parts) | ~**1.88x**; improvement was **not** monotonic (week 8 → 9 regressed) |
| 4 | Support tickets down `42%` | **Incorrect** | **≈29.0%** |
| 5 | Average weekly infra cost about `USD 1,300` | **Incorrect** | **USD 1,450** |
| 6 | Quarterly uptime `99.95%` | **Correct** | — |
| 7 | Week-7 signup + infra cost spike explained by the paid campaign | **Correct** | — |

Totals: 3 claims confirmed correct (2, 6, 7); 4 claims incorrect (1, 3, 4, 5).

## Evidence

Source rows quoted from `weekly-metrics.md` (columns: `Week | Signups | Active users (EOW) | p95 latency (ms) | Support tickets | Infra cost (USD)`):

```
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
```

Fixture note quoted verbatim:

> "Notes: the week-7 signup and cost spike coincides with the paid campaign that
> ran that week. Uptime for the quarter was 99.95% (status page export)."

### Incorrect claims (with recomputation)

#### Claim 1 — "Total signups for the quarter reached 4,127." — INCORRECT

Sum of the `Signups` column, 13 weeks:

```
290 + 310 = 600
600 + 325 = 925
925 + 301 = 1226
1226 + 340 = 1566
1566 + 355 = 1921
1921 + 410 = 2331
2331 + 298 = 2629
2629 + 362 = 2991
2991 + 330 = 3321
3321 + 342 = 3663
3663 + 276 = 3939
3939 + 278 = 4217
```

Recomputed total = **4,217**. The claim states `4,127`, off by 90 (a transposition of the
last two digits: `4,2**17**` vs `4,1**27**` — 4217 − 4127 = 90).
**Corrected value: 4,217.**

#### Claim 3 — "p95 latency improved 3x over the quarter, and improved every single week." — INCORRECT

Part A (magnitude): week 1 `620` → week 13 `330`.
`620 / 330 = 1.8787…` → **≈1.88x** (equivalently a 46.8% reduction: `(620 − 330) / 620 = 290 / 620 = 0.4677`).
A 3x improvement from `620` would require an end value of `620 / 3 ≈ 206.7` ms; the table's
lowest value is `330` (week 13). Not 3x.

Part B (monotonicity): week-over-week deltas are
`620 → 600 → 570 → 545 → 520 → 490 → 455 → 380 → 410 → 395 → 370 → 350 → 330`.
Week 8 → week 9 goes `380` → `410`, a **regression of +30 ms**. So latency did **not** improve
every single week; 11 of the 12 week-over-week transitions improved, 1 regressed.

**Corrected value: p95 improved ≈1.88x (620 → 330, a 46.8% reduction), and improvement was
not monotonic — week 9 (`410`) was worse than week 8 (`380`).**

#### Claim 4 — "Support tickets are down 42% quarter over quarter." — INCORRECT

Using the first and last weeks of the `Support tickets` column, week 1 `210` → week 13 `149`:

```
210 − 149 = 61
61 / 210 = 0.29047…  → 29.05%
```

Recomputed decline = **≈29.0%** (29.05%). A 42% decline from `210` would end at
`210 × 0.58 = 121.8`; the table's final value is `149`.
**Corrected value: down ≈29.0% (210 → 149).**

#### Claim 5 — "Average weekly infra cost was held at about USD 1,300." — INCORRECT

Sum of the `Infra cost (USD)` column, 13 weeks:

```
1400 + 1420 = 2820
2820 + 1380 = 4200
4200 + 1450 = 5650
5650 + 1500 = 7150
7150 + 1480 = 8630
8630 + 1620 = 10250
10250 + 1440 = 11690
11690 + 1460 = 13150
13150 + 1430 = 14580
14580 + 1410 = 15990
15990 + 1450 = 17440
17440 + 1410 = 18850
```

Mean = `18850 / 13 = 1450` exactly.
Additional check: the **minimum** weekly value in the column is `1380` (week 3), so no
weekly figure is even as low as USD 1,300 — the stated average is below every observation.
**Corrected value: USD 1,450 per week (total USD 18,850 / 13 weeks).**

## Verified claims (confirmed correct)

These claims were recomputed and are **correct as written**. They are confirmed, not flagged.

#### Claim 2 — "Active users grew from 8,200 to 9,430, a 15% increase." — CORRECT

Endpoint values match the table exactly: week 1 `Active users (EOW)` = `8200`, week 13 = `9430`.

```
9430 − 8200 = 1230
1230 / 8200 = 0.15  → 15.0%
```

The growth rate is exactly 15.0%, not merely a rounding-friendly approximation. **Confirmed correct.**

#### Claim 6 — "Quarterly uptime was 99.95%." — CORRECT

The fixture's own note states: "Uptime for the quarter was 99.95% (status page export)."
The claimed `99.95%` matches the quoted source value character for character. **Confirmed correct.**

Scope note: uptime is not a column in the weekly table, so it cannot be independently
recomputed from the per-week rows — it is verified by exact match against the note in the
same source-of-truth file. Any figure beyond that note is unmeasured here.

#### Claim 7 — "The week-7 spike in signups and infra cost is explained by the paid campaign that ran that week." — CORRECT

Both spikes are real and both are maxima of their columns:

- Week 7 `Signups` = `410` — the largest value in the Signups column (next highest: `362` in week 9).
- Week 7 `Infra cost (USD)` = `1620` — the largest value in the Infra cost column (next highest: `1500` in week 5).

The fixture note attributes exactly this pairing to the campaign: "the week-7 signup and cost
spike coincides with the paid campaign that ran that week." **Confirmed correct.**

## Rejected candidates

Things that looked suspicious during the pass but are **not** real findings:

1. **Week-7 attribution treated as a causal overclaim.** The summary says the spike "is
   explained by" the campaign while the fixture note says it "coincides with" it —
   correlation vs causation. Rejected as a finding: the task is arithmetic reconciliation
   against the table, the two spikes are numerically confirmed as column maxima (`410`,
   `1620`), and the source file itself links them to the campaign. Downgrading claim 7 would
   flag a claim the source supports.
2. **Claim 2's "15%" as a rounding artifact.** Percentage claims in this fixture are mostly
   wrong, so `15%` looked like another loose round number. Recomputation shows
   `1230 / 8200 = 0.15` **exactly**, so there is no rounding error at all. Not a finding.
3. **Week-4 signups dip (`301`, down from `325` in week 3) and week-12 low (`276`).**
   Non-monotonic signup weeks look anomalous, but no numbered claim asserts monotonic signup
   growth — only the total (claim 1) and the latency monotonicity (claim 3) are asserted. The
   dips affect neither verdict beyond the sum already computed. Not a finding.
4. **Week-8 signups (`298`) as a "data error" following the week-7 campaign peak (`410`).**
   Looked like a possible typo or post-campaign artifact, but the table is defined as the
   source of truth; there is no second source to contradict it and no claim depends on week 8
   alone. Treated as a real observation, not an error.
5. **Claim 4's "quarter over quarter" wording.** The phrase literally implies comparison to a
   *previous* quarter, but the fixture supplies only Q3 weeks. Rejected as a separate finding
   and folded into claim 4: claim 4 is already marked incorrect on the recomputed
   within-quarter figure (29.0% vs 42%). See Assumptions.
6. **Week-8 p95 (`380`) as the quarter minimum rather than week 13 (`330`).** `380` sits
   below its neighbours `455` and `410`, which briefly looked like the low point; scanning the
   full column confirms week 13 `330` is the minimum. This shaped the claim-3 arithmetic but
   is not an independent defect in the summary.

## Assumptions

1. **`weekly-metrics.md` is the source of truth.** Its own header says "Q3 raw table (source
   of truth)"; where the summary and the table disagree, the table wins.
2. **"Over the quarter" / "from … to …" / "quarter over quarter" comparisons use week 1 as the
   start and week 13 as the end**, since only Q3 weeks 1–13 are supplied. For claim 4 this is
   the only computable reading; a true prior-quarter comparison is **unmeasured** — no
   prior-quarter data exists in either fixture. Claim 4 is marked incorrect on the
   within-quarter reading, which is also the reading claim 2 uses successfully (`8,200` →
   `9,430` are exactly the week-1 and week-13 values), so the reading is consistent with the
   summary's own convention.
3. **"Average weekly infra cost" means the unweighted arithmetic mean of the 13 weekly
   values.** No weighting basis (e.g. by active users) is given in either fixture.
4. **"Improved 3x" for latency means the ratio start/end** (lower p95 is better). The
   alternative reading — a 3-percentage-style reduction — is not supported by the wording;
   under either reading the claim fails, since the observed reduction is 46.8% / 1.88x.
5. **Uptime (99.95%) is verified by string match against the fixture note, not recomputed.**
   There is no uptime column, so independent recomputation is **unmeasured**.
6. **All 13 rows are complete and in-quarter.** No week is excluded as an outlier; the week-7
   campaign week is included in the signup total and the cost mean.
7. **No timing, token, or comparative-performance measurement was taken for this run.** Any
   such figure would be unmeasured; no benchmark-superiority claim is made here.

## Stop Condition

Met. All of the following hold:

- All **seven** numbered claims were checked against the raw table, each with explicit arithmetic.
- Every **incorrect** claim (1, 3, 4, 5) has a stated corrected value: 4,217 signups; ≈1.88x
  latency improvement with a week 8→9 regression; ≈29.0% ticket decline; USD 1,450 average
  weekly infra cost.
- Every **correct** claim (2, 6, 7) is confirmed in the "Verified claims (confirmed correct)"
  section rather than flagged.
- Confirmed findings and rejected candidates are in separate sections, with a stated reason
  for each rejection.
- Exactly one artifact was written, at
  `benchmarks/mission-vs-goal/run-output/2026-08-19-tail-v280/tail-metrics-reconciliation-claude_code_goal_command.md`,
  containing the headings Goal, Result, Evidence, Assumptions, and Stop Condition.
- No commit, push, package install, or network access was performed; no benchmark metadata
  (task definitions, scoring configuration, answer keys) was opened.
