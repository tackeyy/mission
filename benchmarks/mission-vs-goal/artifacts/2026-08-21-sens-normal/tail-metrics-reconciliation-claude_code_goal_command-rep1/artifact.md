# tail-metrics-reconciliation — claude_code_goal_command — rep1

## Goal

Fact-check all seven numbered claims in `quarterly-summary.md` against the raw
table in `weekly-metrics.md`, recomputing every figure from the table, marking
each claim correct or incorrect, giving the corrected value with arithmetic for
each incorrect claim, and explicitly confirming the correct claims.

## Result

Four of the seven claims are incorrect (claims 1, 3, 4, 5) and three are
correct (claims 2, 6, 7).

Source data used for every recomputation (from `weekly-metrics.md`, weeks 1–13):

- Signups: 290, 310, 325, 301, 340, 355, 410, 298, 362, 330, 342, 276, 278
- Active users (EOW): 8200, 8310, 8420, 8500, 8610, 8730, 8900, 8990, 9080, 9170, 9260, 9340, 9430
- p95 latency (ms): 620, 600, 570, 545, 520, 490, 455, 380, 410, 395, 370, 350, 330
- Support tickets: 210, 205, 198, 190, 186, 180, 175, 170, 165, 160, 155, 152, 149
- Infra cost (USD): 1400, 1420, 1380, 1450, 1500, 1480, 1620, 1440, 1460, 1430, 1410, 1450, 1410

### Incorrect claims (with corrected values)

**Claim 1 — "Total signups for the quarter reached 4,127." → INCORRECT.**
Corrected value: **4,217**.
Arithmetic (running sum of the Signups column):
290 + 310 = 600; + 325 = 925; + 301 = 1226; + 340 = 1566; + 355 = 1921;
+ 410 = 2331; + 298 = 2629; + 362 = 2991; + 330 = 3321; + 342 = 3663;
+ 276 = 3939; + 278 = **4217**.
The summary's `4,127` is off by 90 and appears to be a digit transposition of
4,217.

**Claim 3 — "p95 latency improved 3x over the quarter, and improved every single week." → INCORRECT (both halves).**
Corrected value: **≈1.88x improvement (620 ms → 330 ms, a 46.8% reduction), and
latency did NOT improve every week — it regressed in week 9.**
Arithmetic:
- Ratio: 620 ÷ 330 = 1.8787… ≈ **1.88x** (not 3x). A true 3x improvement would
  require an end-of-quarter p95 of 620 ÷ 3 ≈ 206.7 ms.
- Reduction: (620 − 330) ÷ 620 = 290 ÷ 620 = 0.4677… = **46.8%**.
- Week-over-week monotonicity: week 8 p95 is `380` and week 9 p95 is `410`, an
  increase of 30 ms (410 − 380 = +30). Every other week-over-week step is a
  decrease, so exactly one week (week 9) breaks the "improved every single
  week" claim.

**Claim 4 — "Support tickets are down 42% quarter over quarter." → INCORRECT.**
Corrected value: **down 29.0%** (measuring week 1 → week 13, the only reduction
the supplied table can support).
Arithmetic: (210 − 149) ÷ 210 = 61 ÷ 210 = 0.29047… = **29.05% ≈ 29.0%**.
Note on interpretation: the table contains only Q3 weekly data, so no prior
quarter exists in the source of truth. A literal "quarter over quarter"
comparison is **unmeasured** from this fixture; the 29.0% figure is the
within-quarter first-week-to-last-week decline. Under either reading, 42% is
not supported by any pair of values in the table.

**Claim 5 — "Average weekly infra cost was held at about USD 1,300." → INCORRECT.**
Corrected value: **USD 1,450.00 per week**.
Arithmetic (sum of the Infra cost column):
1400 + 1420 = 2820; + 1380 = 4200; + 1450 = 5650; + 1500 = 7150; + 1480 = 8630;
+ 1620 = 10250; + 1440 = 11690; + 1460 = 13150; + 1430 = 14580; + 1410 = 15990;
+ 1450 = 17440; + 1410 = **18850**.
18850 ÷ 13 = **1450.0**.
Also, no single week is as low as 1,300 — the minimum weekly infra cost in the
table is `1380` (week 3), so "about USD 1,300" is below the entire observed
range (1380–1620).

### Verified claims (confirmed correct)

**Claim 2 — "Active users grew from 8,200 to 9,430, a 15% increase." → CORRECT.**
Both endpoints match the table exactly: week 1 Active users (EOW) = `8200`,
week 13 Active users (EOW) = `9430`.
Arithmetic: 9430 − 8200 = 1230; 1230 ÷ 8200 = 0.15 exactly = **15.0%**.
No rounding slack is needed — the percentage is exact. Confirmed correct.

**Claim 6 — "Quarterly uptime was 99.95%." → CORRECT.**
`weekly-metrics.md` states in its Notes: "Uptime for the quarter was 99.95%
(status page export)." The value matches the source of truth verbatim.
Recomputation caveat: the table has no per-week uptime column, so this figure
cannot be independently recomputed from weekly rows — it is **verified by exact
match against the source-of-truth note, not by arithmetic**. Confirmed correct.

**Claim 7 — "The week-7 spike in signups and infra cost is explained by the paid campaign that ran that week." → CORRECT.**
Both the spike and the attribution check out:
- Week 7 signups = `410`, the maximum of the Signups column (next highest is
  `362` in week 9); 410 − 362 = 48 above the runner-up.
- Week 7 infra cost = `1620`, the maximum of the Infra cost column (next
  highest is `1500` in week 5); 1620 − 1500 = 120 above the runner-up.
- Attribution: the Notes line in `weekly-metrics.md` reads "the week-7 signup
  and cost spike coincides with the paid campaign that ran that week."
Confirmed correct.

### Rejected candidates (looked suspicious, but are not findings)

- **Claim 2's "15%" rounding.** Suspicious because summary percentages are
  usually rounded and the two neighbouring percentage claims (4 and 3) are both
  wrong. Rejected: 1230 ÷ 8200 = 0.15 exactly, so there is no rounding error to
  report.
- **Claim 2's baseline choice (8,200 is an end-of-week figure, not a start-of-quarter figure).** Suspicious because the column is labelled "Active users
  (EOW)", so `8200` is the *end* of week 1, and a strict "grew from" baseline
  would be the pre-quarter value. Rejected: the fixture contains no pre-quarter
  value, so the week-1 EOW figure is the only defensible baseline, and the claim
  quotes it exactly (`8200`). The alternative baseline is **unmeasured**, which
  is not grounds for asserting a defect.
- **Claim 6's uptime not being derivable from the table.** Suspicious because
  every other claim is recomputable and this one is not. Rejected: the value
  `99.95%` appears verbatim in the source-of-truth file's Notes, so the summary
  faithfully reproduces the source. Non-recomputability is a property of the
  fixture, not a drift in the summary.
- **Claim 7 being a causal claim ("is explained by").** Suspicious because the
  source note says only "coincides with", which is weaker than causation.
  Rejected: the note explicitly ties the week-7 signup and cost spike to the
  paid campaign, and the numeric spike is real in both columns (`410`, `1620`).
  The causal-vs-coincidental wording gap is too thin to assert as a defect, and
  flagging it would contradict the instruction to confirm correct claims.
- **Week 4 signups dipping (301 after 325).** Suspicious as a possible
  contradiction of a growth narrative. Rejected: no numbered claim asserts
  monotonic signup growth, so there is nothing to fact-check against it.

## Evidence

Every value quoted below is copied from the fixture files.

- `weekly-metrics.md` header: `| Week | Signups | Active users (EOW) | p95 latency (ms) | Support tickets | Infra cost (USD) |`
- `weekly-metrics.md` week 1 row: `| 1 | 290 | 8200 | 620 | 210 | 1400 |`
- `weekly-metrics.md` week 7 row: `| 7 | 410 | 8900 | 455 | 175 | 1620 |`
- `weekly-metrics.md` week 8 row: `| 8 | 298 | 8990 | 380 | 170 | 1440 |`
- `weekly-metrics.md` week 9 row: `| 9 | 362 | 9080 | 410 | 165 | 1460 |`
- `weekly-metrics.md` week 13 row: `| 13 | 278 | 9430 | 330 | 149 | 1410 |`
- `weekly-metrics.md` notes: `Notes: the week-7 signup and cost spike coincides with the paid campaign that ran that week. Uptime for the quarter was 99.95% (status page export).`
- `quarterly-summary.md` claim 1: `Total signups for the quarter reached 4,127.`
- `quarterly-summary.md` claim 2: `Active users grew from 8,200 to 9,430, a 15% increase.`
- `quarterly-summary.md` claim 3: `p95 latency improved 3x over the quarter, and improved every single week.`
- `quarterly-summary.md` claim 4: `Support tickets are down 42% quarter over quarter.`
- `quarterly-summary.md` claim 5: `Average weekly infra cost was held at about USD 1,300.`
- `quarterly-summary.md` claim 6: `Quarterly uptime was 99.95%.`
- `quarterly-summary.md` claim 7: `The week-7 spike in signups and infra cost is explained by the paid campaign that ran that week.`

Arithmetic verification method: the four aggregate computations (signups total
18,850-style running sums, infra-cost total and mean, the 15% growth ratio, the
620/330 latency ratio, and the 61/210 ticket reduction) were each computed by
hand above and independently re-checked with a local `python3 -c` evaluation of
the same column lists, which returned `4217 18850 1450.0 0.15
1.878787878787879 0.2904761904761905`. Both methods agree.

### Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| quarterly-summary.md | claim 1 — total signups for the quarter | 4217 | 4,127 | drift |
| quarterly-summary.md | claim 3 — p95 latency improvement factor and weekly monotonicity | ~1.88x (620→330); not monotonic (week 8 380 → week 9 410) | 3x, improved every single week | drift |
| quarterly-summary.md | claim 4 — support ticket reduction | 29.0% (210→149) | 42% | drift |
| quarterly-summary.md | claim 5 — average weekly infra cost | USD 1450.00 (18850/13) | about USD 1,300 | drift |
| quarterly-summary.md | claim 2 — active user growth 8200→9430 | 15% (1230/8200 = 0.15) | 15% | no-finding |
| quarterly-summary.md | claim 6 — quarterly uptime | 99.95% | 99.95% | no-finding |
| quarterly-summary.md | claim 7 — week-7 signup and infra cost spike attributed to paid campaign | week 7 max signups 410 and max infra cost 1620; note attributes to paid campaign | spike explained by the paid campaign that ran that week | no-finding |

## Assumptions

1. `weekly-metrics.md` is the source of truth, as its title states
   (`Q3 raw table (source of truth)`); where the summary and the table disagree,
   the table wins.
2. "The quarter" means weeks 1–13 inclusive — all rows in the table.
3. "From 8,200 to 9,430" (claim 2) is interpreted as week-1 EOW → week-13 EOW,
   because the fixture provides no pre-quarter active-user value.
4. "Quarter over quarter" (claim 4) is evaluated as week 1 → week 13 within Q3,
   because the fixture contains no prior-quarter data. A true prior-quarter
   comparison is unmeasured.
5. "Improved" for p95 latency means a lower millisecond value.
6. "Improved 3x" is read as a ratio of start p95 to end p95 (620 ÷ 330).
7. "About USD 1,300" (claim 5) is treated as a claim about the arithmetic mean
   of the 13 weekly infra-cost values; it is wrong under that reading and also
   below the full observed weekly range, so the verdict does not depend on the
   tolerance implied by "about".
8. Uptime (claim 6) is verified by exact string match against the source note,
   not by recomputation, since no uptime column exists.

Not measured in this run: any data outside the two named fixture files; prior
quarter figures; per-week uptime; whether the paid campaign caused rather than
merely coincided with the week-7 spike. No benchmark comparison of any kind was
performed or is asserted.

## Stop Condition

This artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-21-sens-normal/tail-metrics-reconciliation-claude_code_goal_command-rep1.md`
and contains the headings Goal, Result, Evidence, Assumptions, and Stop
Condition; all seven numbered claims are verified with recomputed arithmetic;
every incorrect claim (1, 3, 4, 5) has a corrected value with the arithmetic
shown; every correct claim (2, 6, 7) is explicitly confirmed in the
verified-claims section; confirmed findings and rejected candidates are
separated; and exactly one machine-checkable findings table with the required
header is present with one row per evaluated claim. No commits, pushes,
installs, or network access were performed, and no files outside this artifact
were modified.
