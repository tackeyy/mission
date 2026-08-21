# tail-metrics-reconciliation — claude_code_goal_command (rep2)

## Goal

Fact-check all seven numbered claims in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
against the raw table in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`,
recompute every figure from the table, mark each claim correct or incorrect,
and give the corrected value with arithmetic shown for each incorrect claim.

## Result

Four claims are incorrect (claims 1, 3, 4, 5) and three are correct
(claims 2, 6, 7).

| Claim | Summary text (abbreviated) | Verdict | Corrected value |
|---|---|---|---|
| 1 | "Total signups for the quarter reached 4,127." | incorrect | 4,217 |
| 2 | "Active users grew from 8,200 to 9,430, a 15% increase." | correct | — |
| 3 | "p95 latency improved 3x … and improved every single week." | incorrect | ≈1.88x; week 9 regressed |
| 4 | "Support tickets are down 42% quarter over quarter." | incorrect | −29.0% |
| 5 | "Average weekly infra cost … about USD 1,300." | incorrect | USD 1,450 |
| 6 | "Quarterly uptime was 99.95%." | correct | — |
| 7 | "The week-7 spike … explained by the paid campaign." | correct | — |

## Evidence

### Source-of-truth table (as read)

All figures below are recomputed from the 13 data rows of
`weekly-metrics.md`, which are quoted here verbatim as the basis for every
arithmetic step:

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

### Confirmed findings (incorrect claims)

**Finding 1 — Claim 1 total signups is understated by 90.**

Quoted claim: `1. Total signups for the quarter reached 4,127.`

Recomputation of the `Signups` column:

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

Corrected value: **4,217**. The claimed `4,127` differs by 90; the digit
pattern is consistent with a transposition of `4,217`, but the arithmetic
above is the basis for the verdict, not the transposition hypothesis.

**Finding 2 — Claim 3 overstates the p95 improvement factor and asserts a
monotonicity that the table contradicts.**

Quoted claim: `3. p95 latency improved 3x over the quarter, and improved
every single week.`

Two separate defects:

1. Improvement factor. Week 1 p95 is `620` and week 13 p95 is `330`.
   `620 / 330 = 1.8787…`, i.e. **≈1.88x**, not 3x. Stated as a reduction:
   `(620 − 330) / 620 = 290 / 620 = 0.4677… = 46.8%`. A true 3x improvement
   would require a week-13 value of `620 / 3 = 206.67 ms`, which the table
   does not contain.
2. Monotonicity. Week 8 p95 is `380` and week 9 p95 is `410`. `410 > 380`,
   so p95 got **worse** in week 9 by `410 − 380 = 30 ms`. Every other
   week-over-week step is a decrease, so this is the single exception, and it
   is sufficient to falsify "improved every single week".

Corrected value: **≈1.88x (46.8% reduction) over the quarter, with a
regression in week 9 (380 ms → 410 ms)**.

**Finding 3 — Claim 4 overstates the support-ticket reduction.**

Quoted claim: `4. Support tickets are down 42% quarter over quarter.`

Week 1 tickets `210`, week 13 tickets `149`:

```
210 − 149 = 61
61 / 210 = 0.290476…
→ 29.0% reduction
```

Corrected value: **−29.0%** (from `210` to `149`). A 42% reduction would
imply a week-13 value of `210 × (1 − 0.42) = 121.8`, which the table does not
contain. See Assumptions for the interpretation of "quarter over quarter".

**Finding 4 — Claim 5 understates the average weekly infra cost.**

Quoted claim: `5. Average weekly infra cost was held at about USD 1,300.`

Sum of the `Infra cost (USD)` column:

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

18850 / 13 = 1450.0
```

Corrected value: **USD 1,450 per week**. Additionally, the minimum weekly
value in the table is `1380` (week 3), so no week is at or below the claimed
"about USD 1,300" level — the claim is not merely a rounding difference.

### Verified claims (confirmed correct — not findings)

**Claim 2 — correct.** Quoted claim: `2. Active users grew from 8,200 to
9,430, a 15% increase.` The `Active users (EOW)` column starts at `8200`
(week 1) and ends at `9430` (week 13), matching both endpoints verbatim.

```
9430 − 8200 = 1230
1230 / 8200 = 0.15 exactly
→ 15% increase
```

The percentage is exact, not merely rounded. Confirmed correct.

**Claim 6 — correct.** Quoted claim: `6. Quarterly uptime was 99.95%.` The
source-of-truth file states in its Notes block: `Uptime for the quarter was
99.95% (status page export).` The value matches character-for-character. It
is not recomputable from the six table columns (there is no uptime column);
it is verified by direct match against the stated source-of-truth note.

**Claim 7 — correct.** Quoted claim: `7. The week-7 spike in signups and
infra cost is explained by the paid campaign that ran that week.` Three
independent checks:

- Week 7 signups `410` is the maximum of the signup column (next highest is
  `362` in week 9), so a signup spike exists in week 7.
- Week 7 infra cost `1620` is the maximum of the cost column (next highest is
  `1500` in week 5), so a cost spike exists in week 7.
- The Notes block states: `the week-7 signup and cost spike coincides with
  the paid campaign that ran that week.`

Both the spike's existence and its attribution are supported. Confirmed
correct.

### Rejected candidates (looked suspicious, not findings)

- **Claim 2's "15%" as a suspiciously round number.** A clean round
  percentage next to four demonstrably wrong figures invites suspicion of
  back-filling. Rejected: `1230 / 8200` is exactly `0.15`, so the roundness
  is a property of the data, not an error.
- **Claim 2's "from 8,200" as a possible wrong baseline.** The column is
  labelled `Active users (EOW)`, so `8200` is an end-of-week-1 value, and one
  could argue the quarter's true starting point (start of week 1) is not in
  the table. Rejected as a finding: the table contains no earlier value, so
  `8200` is the only defensible baseline available, and the claim's two
  endpoints match table cells verbatim.
- **Claim 6's uptime not being derivable from the table.** A figure that
  cannot be recomputed from the six columns initially looks unverifiable and
  therefore flag-worthy. Rejected: the same fixture designated as source of
  truth states `99.95%` explicitly in its Notes, so the claim is verified by
  match rather than unverifiable.
- **Claim 7's causal wording ("is explained by") versus the note's
  correlational wording ("coincides with").** The summary asserts causation
  where the source asserts coincidence, which is a genuine epistemic
  softening. Rejected as a *numeric* finding: the task is a figure-level
  fact-check, both spikes are confirmed maxima, and the attribution matches
  the only explanation the source of truth offers. Flagged here as a wording
  nuance only, not asserted as drift.
- **Week 12 signups (`276`) being lower than week 1 (`290`).** A late-quarter
  dip below the starting week looks like it could contradict a growth
  narrative. Rejected: no numbered claim asserts monotonic signup growth, so
  there is nothing to fault.

### Machine-checkable findings block

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| quarterly-summary.md | claim 1 — total quarterly signups | 4217 | 4127 | drift |
| quarterly-summary.md | claim 3 — p95 improvement factor and weekly monotonicity | 1.88x improvement (620→330), with a week-9 regression 380→410 | 3x improvement, improved every single week | drift |
| quarterly-summary.md | claim 4 — support ticket reduction | -29.0% (210→149) | -42% | drift |
| quarterly-summary.md | claim 5 — average weekly infra cost | USD 1450 (18850/13) | about USD 1300 | drift |
| quarterly-summary.md | claim 2 — active user growth 8200→9430 | 15% (1230/8200) | 15% | no-finding |
| quarterly-summary.md | claim 6 — quarterly uptime | 99.95% | 99.95% | no-finding |
| quarterly-summary.md | claim 7 — week-7 spike attributed to paid campaign | week-7 signups 410 and infra cost 1620 are both column maxima, campaign noted | week-7 spike explained by paid campaign | no-finding |

## Assumptions

- **"Quarter over quarter" in claim 4 is evaluated within the table.** No
  prior-quarter data exists in either fixture, so the reduction is computed
  from week 1 (`210`) to week 13 (`149`). If the claim intended a comparison
  against an actual previous quarter, that comparison is **unmeasured** —
  no source in scope contains prior-quarter ticket counts.
- **"Average weekly infra cost" is the arithmetic mean of the 13 weekly
  values.** No weighting scheme is specified in either fixture. Under any
  reasonable weighting the result stays above 1,380 (the column minimum), so
  the verdict on claim 5 does not depend on this assumption.
- **Quarter endpoints are week 1 and week 13.** The table contains exactly
  13 rows and is labelled "Q3 raw table (source of truth)".
- **"3x improved" means latency reduced to one third.** The alternative
  reading (a 3-percentage-point or 3 ms change) is not plausible for a
  latency ratio claim; under the stated reading the factor is 1.88x.
- **Claim 6 is verified against the Notes block, not recomputed.** There is
  no uptime column; independent recomputation of uptime is **unmeasured**.
- **Scope.** Only the two named fixture files and this output file were
  opened. No other file under `benchmarks/mission-vs-goal/` was read, listed,
  or searched. No commits, pushes, installs, or network access occurred.
- **No benchmark-superiority claim is made.** This artifact reports only the
  reconciliation result for this single task.

## Stop Condition

Met when all of the following hold, and all do:

1. This artifact exists at
   `benchmarks/mission-vs-goal/run-output/2026-08-21-sens-normal/tail-metrics-reconciliation-claude_code_goal_command-rep2.md`
   and contains the headings Goal, Result, Evidence, Assumptions, and Stop
   Condition. ✅
2. All seven numbered claims are verified with recomputed arithmetic shown
   from the raw table. ✅ (claims 1–7 above)
3. Every incorrect claim states a corrected value: claim 1 → 4,217;
   claim 3 → ≈1.88x with a week-9 regression; claim 4 → −29.0%;
   claim 5 → USD 1,450. ✅
4. Correct claims (2, 6, 7) are confirmed explicitly in a verified-claims
   section rather than flagged. ✅
5. Exactly one machine-checkable table with the required header is present,
   one row per evaluated claim, verdicts limited to `drift` / `no-finding`. ✅
   (7 rows: 4 drift, 3 no-finding)
6. Confirmed findings and rejected candidates are separated, with a reason
   given for each rejection. ✅
