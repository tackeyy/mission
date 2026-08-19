# tail-metrics-reconciliation — claude_code_goal_command

## Goal

Fact-check every numbered claim (1–7) in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
against the raw table in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`,
recompute each figure from the table, mark each claim correct or incorrect,
and give the corrected value with arithmetic shown for every incorrect claim.
Correct claims are confirmed as correct, not flagged.

## Result

7 claims checked. **3 correct** (claims 2, 6, 7), **4 incorrect** (claims 1, 3, 4, 5).

| # | Claim (as written) | Verdict | Recomputed / corrected value |
|---|---|---|---|
| 1 | "Total signups for the quarter reached 4,127." | **Incorrect** | 4,217 |
| 2 | "Active users grew from 8,200 to 9,430, a 15% increase." | **Correct** | 8200 → 9430, +1230 = exactly 15.00% |
| 3 | "p95 latency improved 3x over the quarter, and improved every single week." | **Incorrect** (both parts) | ~1.88x (620 → 330); week 9 regressed vs week 8 |
| 4 | "Support tickets are down 42% quarter over quarter." | **Incorrect** | down 29.0% (210 → 149) |
| 5 | "Average weekly infra cost was held at about USD 1,300." | **Incorrect** | USD 1,450.00 average |
| 6 | "Quarterly uptime was 99.95%." | **Correct** | 99.95%, per the source note (not derivable from the table columns) |
| 7 | "The week-7 spike in signups and infra cost is explained by the paid campaign that ran that week." | **Correct** | Week 7 is the quarter max in both columns: 410 signups, USD 1620 |

## Evidence

### Confirmed findings (incorrect claims)

**Finding 1 — Claim 1 total signups is wrong by 90 (digit transposition).**

Claim quote: `Total signups for the quarter reached 4,127.`

Recomputation over the `Signups` column, weeks 1–13:

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

Corrected value: **4,217**. Difference vs claim: 4217 − 4127 = 90 (the claim reads
like a `217` → `127` transposition of the trailing digits).

**Finding 2 — Claim 3 overstates the latency improvement and the "every single week" monotonicity is false.**

Claim quote: `p95 latency improved 3x over the quarter, and improved every single week.`

Endpoints from the `p95 latency (ms)` column: week 1 = `620`, week 13 = `330`.

```
Ratio        = 620 / 330 = 1.8788...  → ~1.88x, not 3x
Absolute drop = 620 - 330 = 290 ms
Percent drop  = 290 / 620 = 0.46774 → 46.8%
For a true 3x, week 13 would have to be 620 / 3 = 206.67 ms
```

Monotonicity check, week by week (`620, 600, 570, 545, 520, 490, 455, 380, 410, 395, 370, 350, 330`):
every step decreases except week 8 → week 9, where the value rises from `380` to `410`
(+30 ms). So latency did **not** improve every single week.

Corrected value: **~1.88x improvement (620 ms → 330 ms, a 46.8% reduction), with one
regression week — week 9 (`410`) was worse than week 8 (`380`).**

**Finding 3 — Claim 4 overstates the support-ticket reduction.**

Claim quote: `Support tickets are down 42% quarter over quarter.`

Endpoints from the `Support tickets` column: week 1 = `210`, week 13 = `149`.

```
Absolute drop = 210 - 149 = 61
Percent drop  = 61 / 210 = 0.290476... → 29.05%
For a true 42%, week 13 would have to be 210 x 0.58 = 121.8
```

Corrected value: **down 29.0% (210 → 149).**

**Finding 4 — Claim 5 understates average weekly infra cost by USD 150.**

Claim quote: `Average weekly infra cost was held at about USD 1,300.`

Sum of the `Infra cost (USD)` column, weeks 1–13:

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

Average = 18850 / 13 = 1450.00
```

Corrected value: **USD 1,450.00 average per week.** Note also that the minimum weekly
value in the column is `1380` (week 3) — no single week was as low as USD 1,300, so
"held at about USD 1,300" is not defensible even as a loose approximation.

### Verified claims (confirmed correct — not flagged)

**Claim 2 — CORRECT.**

Claim quote: `Active users grew from 8,200 to 9,430, a 15% increase.`

Both endpoints appear verbatim in the `Active users (EOW)` column: week 1 = `8200`,
week 13 = `9430`.

```
Absolute growth = 9430 - 8200 = 1230
Percent growth  = 1230 / 8200 = 0.15 exactly → 15.00%
Cross-check: 8200 x 1.15 = 9430
```

The stated 15% is exact, not rounded. Confirmed correct.

**Claim 6 — CORRECT.**

Claim quote: `Quarterly uptime was 99.95%.`

The raw file's notes line states: `Uptime for the quarter was 99.95% (status page
export).` The figure matches the source exactly. It is not recomputable from the
table columns (there is no uptime column), so it is verified by direct match against
the stated source rather than by arithmetic — see Assumptions. Confirmed correct.

**Claim 7 — CORRECT.**

Claim quote: `The week-7 spike in signups and infra cost is explained by the paid
campaign that ran that week.`

Week 7 row: `| 7 | 410 | 8900 | 455 | 175 | 1620 |`

```
Signups:   410 is the maximum of the Signups column (next highest: 362, week 9)
           410 - 362 = 48 above the second-highest week
Infra cost: 1620 is the maximum of the Infra cost column (next highest: 1500, week 5)
           1620 - 1500 = 120 above the second-highest week
```

Both a signup spike and a cost spike do occur in week 7, and the raw file's notes line
states: `the week-7 signup and cost spike coincides with the paid campaign that ran
that week.` Confirmed correct.

### Rejected candidates (looked suspicious, but are not findings)

- **Claim 2's "15%" as a suspiciously round number.** Round headline percentages are a
  common rounding-error tell. Rejected: `1230 / 8200 = 0.15` exactly, and
  `8200 x 1.15 = 9430` reproduces the stated endpoint with no residual. Not a finding.
- **Claim 2's baseline being an end-of-week figure.** The column header is
  `Active users (EOW)`, so `8200` is the *end* of week 1, not the quarter's opening
  balance; one could argue the "grew from" baseline should predate the quarter. Rejected:
  no pre-quarter value exists in the table, and the claim quotes `8,200` and `9,430`,
  which are exactly the first and last values present. Under the only baseline the source
  supplies, the claim is arithmetically exact. Not a finding.
- **Claim 6 having no supporting column in the table.** A figure with no column looked
  unverifiable and therefore suspect. Rejected: the raw file's own notes line supplies it
  verbatim (`99.95%`) and labels its provenance (`status page export`), so the summary
  faithfully reproduces the source of truth. Absence of a column is a limitation of the
  verification method, not an error in the claim. Not a finding.
- **Week 8 signups (`298`) as a possible data error.** It breaks the otherwise upward
  signup trend and sits between `410` and `362`. Rejected: no numbered claim depends on
  week 8's signup value in isolation, and it is included at face value in the claim 1
  recomputation. Auditing the raw table for plausibility is out of scope — the table is
  designated the source of truth. Not a finding.
- **Week 12 signups (`276`) as the quarter minimum despite being late in the quarter.**
  Same reasoning as week 8: it is a source-of-truth value, used at face value in the
  claim 1 sum, and no claim asserts monotonic signup growth. Not a finding.
- **Claim 7's causal wording ("is explained by").** The summary asserts causation, while
  the notes line only says the spike `coincides with` the campaign. This is a real
  wording gap, but the task is arithmetic fact-checking of figures against the table, and
  the factual content the claim asserts — a week-7 spike in both signups and infra cost,
  concurrent with a paid campaign — is fully supported. Flagged here for transparency
  rather than counted as an incorrect claim. Not a finding.
- **Claim 5's hedge word "about".** "About USD 1,300" might be defended as an
  approximation. Rejected as a defense: the true mean is `1450`, which is 11.5% above the
  claim (`150 / 1300 = 0.1154`), and no single week in the column is below `1380`. The
  hedge does not cover a gap of that size. Claim 5 remains a confirmed finding.

## Assumptions

1. **"Quarter over quarter" endpoints.** The table contains only Q3 (weeks 1–13); no
   prior-quarter data exists in either fixture. For claims 3 and 4, the quarter-over-
   quarter comparison is therefore computed as week 1 vs week 13 within the table. Under
   any alternative baseline the corrected percentages would change, but no such baseline
   is available in the fixtures.
2. **Uptime is unmeasured from the table.** There is no uptime column; claim 6 is
   verified by exact string match against the raw file's notes line
   (`Uptime for the quarter was 99.95% (status page export).`), not by recomputation.
   The underlying status-page export was not accessed and is unmeasured here.
3. **Simple (unweighted) mean for claim 5.** Average weekly infra cost is computed as
   total cost ÷ 13 weeks. No weighting scheme is stated in either fixture.
4. **Latency "improved 3x" read as a ratio.** Claim 3's "3x" is evaluated as
   `week1 / week13`. It is also false under a percent-reduction reading (46.8%, not 200%).
5. **Claim 7's causal attribution is not independently verifiable.** Campaign spend,
   attribution data, and channel breakdowns are not present in the fixtures; the
   attribution is accepted because the raw table's own notes line asserts it. Whether the
   campaign *caused* the spike is unmeasured.
6. **Rounding convention.** Percentages are reported to two decimals of the raw quotient
   and then to one decimal in the summary table; no figure's verdict is sensitive to
   rounding (every incorrect claim is off by far more than a rounding step).
7. **Scope.** Only the two named fixture files were read. No benchmark metadata (task
   definitions, scoring configuration, answer keys) was opened, listed, or grepped. No
   commits, pushes, installs, or network access were performed. No claim is made about
   the relative performance of any benchmark arm.

## Stop Condition

Met. All seven numbered claims were checked against the raw table with arithmetic shown:

- [x] Claim 1 recomputed (4,217) — incorrect, corrected value given
- [x] Claim 2 recomputed (15.00% exactly) — confirmed correct in the verified-claims section
- [x] Claim 3 recomputed (~1.88x; week 9 regression) — incorrect, corrected value given
- [x] Claim 4 recomputed (29.0%) — incorrect, corrected value given
- [x] Claim 5 recomputed (USD 1,450.00) — incorrect, corrected value given
- [x] Claim 6 verified against the source note (99.95%) — confirmed correct
- [x] Claim 7 verified against week-7 maxima (410 signups, USD 1620) — confirmed correct
- [x] Confirmed findings and rejected candidates separated, each with an exact quoted
      identifier or value from the fixtures
- [x] Artifact written to the single required path with headings Goal, Result, Evidence,
      Assumptions, Stop Condition

No further work is required for this task.
