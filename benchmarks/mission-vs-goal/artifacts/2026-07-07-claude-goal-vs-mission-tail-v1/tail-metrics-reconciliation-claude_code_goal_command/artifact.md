# Tail Metrics Reconciliation — `/goal` Command Benchmark Artifact

Arm: `claude_code_goal_command`
Task id: `tail-metrics-reconciliation`

## Goal

Fact-check every numbered claim in `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
against the raw table in `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`.
For each of the seven numbered claims: recompute the underlying figure directly from the raw weekly table, mark the
claim correct or incorrect, and — for every incorrect claim — give the corrected value with the arithmetic shown.
Claims that are correct must be confirmed explicitly, not merely left unflagged. This artifact must be written to
exactly one file (this file), scoped only to the two named fixture files plus this output file, with no other
`benchmarks/mission-vs-goal/` paths opened, read, or listed, and with no claim of benchmark-arm superiority.

## Result

| # | Claim (paraphrased) | Verdict | Corrected value |
|---|---|---|---|
| 1 | Total signups reached 4,127 | **INCORRECT** | 4,217 |
| 2 | Active users grew 8,200 → 9,430, a 15% increase | **CORRECT** | — (confirmed as stated) |
| 3 | p95 latency improved 3x over the quarter, every single week | **INCORRECT** | ~1.88x (620ms → 330ms); not monotonic — week 8→9 regressed |
| 4 | Support tickets down 42% quarter over quarter | **INCORRECT** | ~29.05% (210 → 149) |
| 5 | Average weekly infra cost held at ~USD 1,300 | **INCORRECT** | USD 1,450 |
| 6 | Quarterly uptime was 99.95% | **CORRECT** | — (confirmed as stated) |
| 7 | Week-7 signup/infra-cost spike explained by the paid campaign | **CORRECT** | — (confirmed as stated) |

4 of 7 claims are incorrect; 3 of 7 are correct as stated.

## Evidence

Raw source table (`weekly-metrics.md`, all 13 rows used for every recomputation below):

```
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
```

Notes line from the same file: "*the week-7 signup and cost spike coincides with the paid campaign that ran that week.
Uptime for the quarter was 99.95% (status page export).*"

### Claim 1 — "Total signups for the quarter reached 4,127." → **INCORRECT**

Fixture quote: `"1. Total signups for the quarter reached 4,127."` (quarterly-summary.md, line 3)

Arithmetic — sum of the Signups column, all 13 weeks:
```
290 + 310 + 325 + 301 + 340 + 355 + 410 + 298 + 362 + 330 + 342 + 276 + 278
= 600, 925, 1226, 1566, 1921, 2331, 2629, 2991, 3321, 3663, 3939, 4217
```
Recomputed total = **4,217**, not 4,127 (off by 90; the digits "1" and "2" appear transposed between the claimed
4,127 and the correct 4,217).

**Corrected value: 4,217 total signups.**

### Claim 2 — "Active users grew from 8,200 to 9,430, a 15% increase." → **CORRECT**

Fixture quote: `"2. Active users grew from 8,200 to 9,430, a 15% increase."` (quarterly-summary.md, line 4)

Table check: Week 1 "Active users (EOW)" = `8200`; Week 13 "Active users (EOW)" = `9430`. Both endpoints match the
claim exactly.

Arithmetic:
```
growth = 9430 - 8200 = 1230
percent = 1230 / 8200 = 0.15 = 15.0%
```
Exact match. **Confirmed correct as stated — no correction needed.**

### Claim 3 — "p95 latency improved 3x over the quarter, and improved every single week." → **INCORRECT**

Fixture quote: `"3. p95 latency improved 3x over the quarter, and improved every single week."` (quarterly-summary.md, line 5)

p95 latency column across all 13 weeks: `620, 600, 570, 545, 520, 490, 455, 380, 410, 395, 370, 350, 330`.

Part A — magnitude of improvement:
```
ratio = week1 / week13 = 620 / 330 = 1.879 (≈1.88x)
```
Not 3x. (A true 3x improvement from 620ms would land near 207ms; actual week-13 value is 330ms.)

Part B — "every single week": week-over-week deltas (negative = latency dropped = improved):
```
W1→W2 -20, W2→W3 -30, W3→W4 -25, W4→W5 -25, W5→W6 -30, W6→W7 -35,
W7→W8 -75, W8→W9 +30, W9→W10 -15, W10→W11 -25, W11→W12 -20, W12→W13 -20
```
Week 8 → Week 9 is **+30** (latency rose from `380` ms to `410` ms), i.e. it got *worse*, not better. So latency did
not improve every single week.

**Corrected value: p95 latency improved from 620ms to 330ms, a ~1.88x reduction (not 3x), and it was not monotonic —
it regressed from 380ms (week 8) to 410ms (week 9) before resuming its decline.**

### Claim 4 — "Support tickets are down 42% quarter over quarter." → **INCORRECT**

Fixture quote: `"4. Support tickets are down 42% quarter over quarter."` (quarterly-summary.md, line 6)

Support tickets column: Week 1 = `210`, Week 13 = `149` (strictly decreasing every week in between).

Arithmetic (start-value base, same convention as claim 2's verified 15% figure):
```
decrease = 210 - 149 = 61
percent = 61 / 210 = 0.29048 = 29.05%
```
Not 42%. Even checked against the alternate (end-value) denominator — `61 / 149 = 40.94%` — it still falls short of
42%, so the 42% figure does not match under either base convention.

**Corrected value: support tickets down ~29.05% (210 → 149), not 42%.**

### Claim 5 — "Average weekly infra cost was held at about USD 1,300." → **INCORRECT**

Fixture quote: `"5. Average weekly infra cost was held at about USD 1,300."` (quarterly-summary.md, line 7)

Infra cost column, all 13 weeks: `1400, 1420, 1380, 1450, 1500, 1480, 1620, 1440, 1460, 1430, 1410, 1450, 1410`.

Arithmetic:
```
sum = 1400+1420+1380+1450+1500+1480+1620+1440+1460+1430+1410+1450+1410 = 18,850
average = 18,850 / 13 = 1,450.00
```
Recomputed average = **USD 1,450**, not ~USD 1,300 (a difference of USD 150, ~11.5% higher than claimed). The
weekly range (min USD 1,380 in week 3, max USD 1,620 in week 7) also never dips near USD 1,300.

**Corrected value: average weekly infra cost = USD 1,450 (18,850 / 13 weeks), not ~USD 1,300.**

### Claim 6 — "Quarterly uptime was 99.95%." → **CORRECT**

Fixture quote: `"6. Quarterly uptime was 99.95%."` (quarterly-summary.md, line 8)

Source note in `weekly-metrics.md`: `"Uptime for the quarter was 99.95% (status page export)."` — the raw source
states the identical figure verbatim. **Confirmed correct as stated — no correction needed.** (There is no per-week
uptime column in the table to independently recompute from; the notes line is the only uptime source in the fixture,
and it matches exactly.)

### Claim 7 — "The week-7 spike in signups and infra cost is explained by the paid campaign that ran that week." → **CORRECT**

Fixture quote: `"7. The week-7 spike in signups and infra cost is explained by the paid campaign that ran that week."`
(quarterly-summary.md, lines 9-10)

Arithmetic — confirming week 7 is genuinely the spike week for both named metrics:
```
max(Signups, all 13 weeks) = 410, occurs at Week 7 (list: 290,310,325,301,340,355,410,298,362,330,342,276,278)
max(Infra cost, all 13 weeks) = 1620, occurs at Week 7 (list: 1400,1420,1380,1450,1500,1480,1620,1440,1460,1430,1410,1450,1410)
```
Both are unique maxima at week 7. Source note: `"the week-7 signup and cost spike coincides with the paid campaign
that ran that week."` — directly corroborates the claim. **Confirmed correct as stated — no correction needed.**

## Verified Claims (explicitly confirmed correct)

- **Claim 2** — Active users 8,200 → 9,430 is an exact 15.0% increase (`1230 / 8200 = 0.15`). Confirmed correct.
- **Claim 6** — Quarterly uptime 99.95% matches the raw fixture's own notes line verbatim: `"Uptime for the quarter
  was 99.95% (status page export)."` Confirmed correct.
- **Claim 7** — Week 7 is the unique maximum for both Signups (`410`) and Infra cost (`1620`) among all 13 weeks, and
  the fixture's notes line explicitly ties this to the paid campaign. Confirmed correct.

## Rejected Candidates

Discrepancies that looked suspicious during recomputation but were checked and are **not** real findings:

1. **Claim 2 alternate denominator.** Computing growth using the end value as the base (`1230 / 9430 = 13.05%`)
   looked like it might undercut the claimed 15%. Rejected: percentage-change convention uses the *starting* value as
   the base, and `1230 / 8200 = 15.0%` matches exactly — the claim is correct under the standard convention.
2. **Claim 4 alternate denominator "closeness."** Using the end value as the base for the ticket decline
   (`61 / 149 = 40.94%`) comes close to the claimed 42%, which could suggest the claim used that base by mistake.
   Rejected as an explanation: 40.94% still does not equal 42% under any rounding, and it is inconsistent with the
   start-value convention that produces claim 2's verified exact 15% — so the 42% figure remains simply incorrect,
   not a differently-based-but-valid calculation.
3. **Claim 7 broader "spike."** Checked whether Week 7 is also anomalous in Active users or p95 latency, which would
   suggest a broader, non-campaign-specific disruption. Rejected: Active users at week 7 (`8900`) sits on the
   dataset's steady monotonic increase with no discontinuity, and p95 latency at week 7 (`455`ms) sits on its steady
   decline with no discontinuity — the spike is specific to Signups and Infra cost only, exactly as claim 7 states,
   not evidence of a broader anomaly.
4. **Claim 6 derivability from other columns.** Considered whether uptime could be independently recomputed from
   support-ticket or infra-cost data (e.g., as a proxy for incidents). Rejected: the table defines no relationship
   between those columns and uptime, and the fixture provides no per-week uptime column — the notes line is the sole
   verifiable uptime source, and it matches the claim exactly.

## Assumptions

- "Quarter over quarter" in claim 4 is interpreted as first week vs. last week within this one quarter's table
  (matching the start-vs-end methodology explicitly used and verified in claim 2), since neither fixture provides
  data from a prior quarter to compare against.
- All percentage-change calculations use the earlier/start value as the denominator (the standard "percent change"
  convention), consistent with how claim 2's verified 15% figure is derived.
- Claim 7's phrase "is explained by" (causal) versus the source note's more cautious "coincides with"
  (correlational) is treated as consistent with the source's intent: the fixture offers no competing explanation for
  the week-7 anomaly, and the numeric coincidence (unique max Signups + unique max Infra cost, same week) directly
  matches the note. This wording-strength gap is noted here as an observation and does not change the claim's
  correct/incorrect verdict.
- No files under `benchmarks/mission-vs-goal/` other than the two named fixtures and this output file were opened,
  read, or listed, per the task's scoping rule.
- This artifact makes no comparison or superiority claim between benchmark arms; it addresses only the
  `tail-metrics-reconciliation` fact-checking task.

## Stop Condition

Met. This artifact:

- exists at `benchmarks/mission-vs-goal/run-output/2026-07-07-claude-goal-vs-mission-tail-v1/tail-metrics-reconciliation-claude_code_goal_command.md`;
- includes all five required headings: Goal, Result, Evidence, Assumptions, Stop Condition;
- recomputes all seven numbered claims from the raw weekly table with arithmetic shown for each;
- gives a corrected value with arithmetic for every incorrect claim (1, 3, 4, 5);
- explicitly confirms every correct claim (2, 6, 7) in a dedicated Verified Claims section;
- separates confirmed findings from rejected candidates with reasoning for each rejection;
- quotes the exact fixture identifiers/values relied on for every claim.

No further work is required for this task.
