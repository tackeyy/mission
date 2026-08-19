# tail-metrics-reconciliation — mission arm artifact

Run: `2026-08-19-tail-v280-r2` · Arm: `mission` · Profile: `full` · Complexity: `Complex` · `--max-iter 2`

## Mission

Fact-check all seven numbered claims in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md`
against the raw table in
`benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md`,
recomputing every figure from the table, marking each claim correct or incorrect,
and stating the corrected value with arithmetic for every incorrect claim.

- **In scope:** the two fixture files named above; this single output artifact; `.mission-state/`.
- **Out of scope:** any other file under `benchmarks/mission-vs-goal/` (task definitions,
  scoring configuration, answer keys were not opened, listed, or grepped); commits, pushes,
  package installs, network access. No claim of benchmark superiority is made here.
- **Completion condition:** all 7 claims verified with shown arithmetic; corrected value for each
  incorrect claim; correct claims explicitly confirmed in a verified-claims section; confirmed
  findings separated from rejected candidates; every confirmed finding quotes the exact fixture value.

### Source of truth (transcribed from `weekly-metrics.md`)

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

The fixture also carries two prose lines: *"the week-7 signup and cost spike coincides with the
paid campaign that ran that week"* and *"Uptime for the quarter was 99.95% (status page export)."*

## Plan

Canonical plan adopted via `mission-state.py planning adopt-core`
(digest `sha256:563c92bbf41b9f49f04483e399657a7ab4a21ad52889248b2d236aa51f3f39db`,
stored at `.mission-state/plans/563c92bbf41b9f49.json`, source `core`, generation 1).

| Step | Action | Depends on | Acceptance check |
|---|---|---|---|
| S1 | Read `weekly-metrics.md`, transcribe all 13 rows × 5 metric columns | — | Row/column counts match the fixture (13 weeks, 5 metrics) |
| S2 | Read `quarterly-summary.md`, extract the 7 numbered claims verbatim | — | Exactly 7 numbered claims enumerated |
| S3 | Recompute each claim's figure from the transcribed table, with arithmetic | S1, S2 | Each of 7 claims carries a recomputed number and a correct/incorrect verdict |
| S4 | Write the single artifact with the 8 required headings, verified-claims section, and confirmed-vs-rejected separation | S3 | Validator conditions satisfied; exactly one output file written |

Arithmetic was executed independently in Python (`python3` one-shot, no network, no installs)
rather than by mental math, so every number below is machine-computed from the transcribed table.

## Execution

Recomputation, claim by claim. Every quoted figure is copied verbatim from the fixtures.

### Claim 1 — "Total signups for the quarter reached 4,127." → **INCORRECT**

```
290 + 310 + 325 + 301 + 340 + 355 + 410 + 298 + 362 + 330 + 342 + 276 + 278
= 600 + 325 + 301 + 340 + 355 + 410 + 298 + 362 + 330 + 342 + 276 + 278
running: 290, 600, 925, 1226, 1566, 1921, 2331, 2629, 2991, 3321, 3663, 3939, 4217
total = 4217
```

- Claimed: `4,127`. Recomputed: **4,217**. Delta: `4217 − 4127 = 90` (a digit transposition, 217 ↔ 127).
- **Corrected value: 4,217 total signups.**

### Claim 2 — "Active users grew from 8,200 to 9,430, a 15% increase." → **CORRECT**

```
endpoints: week 1 EOW = 8200, week 13 EOW = 9430   (both quoted from the table)
absolute growth = 9430 − 8200 = 1230
relative growth = 1230 / 8200 = 0.15 exactly = 15.0%
```

- Both endpoints and the percentage match the table exactly; `1230/8200` is exactly `0.15`, not a
  rounding artifact. Confirmed correct.

### Claim 3 — "p95 latency improved 3x over the quarter, and improved every single week." → **INCORRECT (both halves)**

```
(a) magnitude: week 1 p95 = 620 ms, week 13 p95 = 330 ms
    ratio = 620 / 330 = 1.8788...  ->  1.88x  (not 3x)
    reduction = (620 − 330) / 620 = 290 / 620 = 46.77%
    a true 3x improvement would require an end value of 620 / 3 = 206.67 ms

(b) monotonicity: week-over-week p95 deltas
    620->600 (−20), 600->570 (−30), 570->545 (−25), 545->520 (−25), 520->490 (−30),
    490->455 (−35), 455->380 (−75), 380->410 (+30 ** REGRESSION **), 410->395 (−15),
    395->370 (−25), 370->350 (−20), 350->330 (−20)
    weeks where p95 did not improve: week 9 only (380 -> 410)
```

- Claimed: `3x` and `improved every single week`. Recomputed: **1.88x** improvement (46.77% reduction),
  and p95 **regressed in week 9**, from `380` to `410`.
- **Corrected value: p95 improved ~1.88x (620 ms → 330 ms, a 46.8% reduction), and it improved in
  11 of the 12 week-over-week transitions — the week-8 → week-9 transition was a regression
  (380 ms → 410 ms). (13 weeks yield 12 transitions, not 13.)**

### Claim 4 — "Support tickets are down 42% quarter over quarter." → **INCORRECT**

```
within-quarter endpoints: week 1 = 210 tickets, week 13 = 149 tickets
absolute decline = 210 − 149 = 61
relative decline = 61 / 210 = 0.290476... = 29.05%
a 42% decline from 210 would end at 210 × 0.58 = 121.8 tickets
(quarter total, for completeness: 210+205+198+190+186+180+175+170+165+160+155+152+149 = 2295)
```

- Claimed: `42%`. Recomputed: **29.0%** on the only decline the fixture supports (week 1 → week 13).
- **Corrected value: support tickets are down 29.0% across the quarter (210 → 149).**
- Scope caveat, stated as unmeasured: the fixture contains **no prior-quarter data**, so a literal
  *quarter-over-quarter* comparison **cannot be computed from these fixtures**. The corrected figure
  is the within-quarter first-week-to-last-week decline. Either way, 42% is not supported.

### Claim 5 — "Average weekly infra cost was held at about USD 1,300." → **INCORRECT**

```
1400 + 1420 + 1380 + 1450 + 1500 + 1480 + 1620 + 1440 + 1460 + 1430 + 1410 + 1450 + 1410
running: 1400, 2820, 4200, 5650, 7150, 8630, 10250, 11690, 13150, 14580, 15990, 17440, 18850
sum = 18850 over 13 weeks
mean = 18850 / 13 = 1450.0 exactly
for the mean to be 1300, the sum would have to be 1300 × 13 = 16900, but it is 18850
minimum weekly cost in the table = 1380 (week 3), which already exceeds 1300
```

- Claimed: `about USD 1,300`. Recomputed: **USD 1,450.00** average. Every single week is above 1,300;
  the cheapest week is `1380`.
- **Corrected value: average weekly infra cost was USD 1,450 (18,850 / 13).**

### Claim 6 — "Quarterly uptime was 99.95%." → **CORRECT**

```
no uptime column exists in the table; the fixture's own notes line states:
"Uptime for the quarter was 99.95% (status page export)."
99.95% (claim) == 99.95% (fixture notes)  -> match
```

- The claim matches the only uptime figure the source of truth provides. Confirmed correct.
- Stated as unmeasured: uptime is **not independently recomputable** from the weekly table — it is
  attested by the status-page export line, which is accepted here as the fixture's source of truth.

### Claim 7 — "The week-7 spike in signups and infra cost is explained by the paid campaign that ran that week." → **CORRECT**

```
week 7 signups = 410 = max(signups column)  (next highest: 362 in week 9)
week 7 infra cost = 1620 = max(infra cost column)  (next highest: 1500 in week 5)
fixture notes: "the week-7 signup and cost spike coincides with the paid campaign
                that ran that week"
```

- Both a signup spike and a cost spike genuinely occur in week 7, and both are the column maxima.
  The attribution matches the fixture's own note. Confirmed correct.
- Wording caveat (not a defect): the note says the spike *"coincides with"* the campaign; the summary
  says it is *"explained by"* it. The fixture provides no causal test, so the causal strength is
  **unmeasured**. The claim is not marked incorrect, because the fixture itself asserts the linkage.

## Review

Reviewed against the task validator, one condition at a time.

| Validator condition | Status | Where satisfied |
|---|---|---|
| All seven claims verified | Met | Execution §Claim 1–7; each has an explicit verdict |
| Recomputed arithmetic shown | Met | Fenced arithmetic block under every claim, machine-computed |
| Corrected value for every incorrect claim | Met | Claims 1 (4,217), 3 (1.88x + week-9 regression), 4 (29.0%), 5 (USD 1,450) |
| Correct claims confirmed, not flagged | Met | Verified-claims section below lists claims 2, 6, 7 as correct |
| Confirmed findings vs rejected candidates separated | Met | Two distinct sections below |
| Exact fixture identifiers/values quoted | Met | Every finding quotes raw cell values (e.g. `620`, `380`, `410`, `1620`) |
| Exactly one artifact written, scope respected | Met | Only this file plus `.mission-state/`; no other benchmark file opened |

The table above is the author's self-check against the validator, written before any independent
review. It is not a review verdict. The independent scored review (2 reviewers, launched in
parallel — see Evidence and the Score section) ran against this artifact afterwards; its findings
and the resulting gate values are recorded in `.mission-state/`, and the three Low-severity findings
it raised (a garbled arithmetic aside under Claim 5, an off-by-one in the week-transition count
under Claim 3, and this very self-referential wording) were applied to the artifact before closeout.

### Verified claims (confirmed correct — not flagged)

| # | Claim (verbatim) | Recomputed check | Verdict |
|---|---|---|---|
| 2 | "Active users grew from 8,200 to 9,430, a 15% increase." | `(9430 − 8200) / 8200 = 1230/8200 = 0.15` exactly | **Correct** |
| 6 | "Quarterly uptime was 99.95%." | Matches fixture notes: `"Uptime for the quarter was 99.95% (status page export)"` | **Correct** |
| 7 | "The week-7 spike in signups and infra cost is explained by the paid campaign that ran that week." | Week 7 is the max of both columns (`410` signups, `1620` USD); fixture notes assert the campaign linkage | **Correct** |

### Confirmed findings (claims that are wrong)

| # | Claimed | Recomputed / corrected | Exact fixture evidence |
|---|---|---|---|
| 1 | Total signups `4,127` | **4,217** (sum of the 13 weekly signups) | Signups column: `290, 310, 325, 301, 340, 355, 410, 298, 362, 330, 342, 276, 278` |
| 3a | p95 improved `3x` | **1.88x** (`620 / 330 = 1.8788`), i.e. a 46.8% reduction | Week 1 p95 `620`, week 13 p95 `330` |
| 3b | p95 "improved every single week" | **False** — one regression week | Week 8 p95 `380` → week 9 p95 `410` |
| 4 | Tickets down `42%` QoQ | **29.0%** (`(210 − 149) / 210`); true QoQ is uncomputable (no prior-quarter data in fixture) | Week 1 tickets `210`, week 13 tickets `149` |
| 5 | Avg weekly infra cost `about USD 1,300` | **USD 1,450** (`18850 / 13`) | Infra cost column sums to `18850`; cheapest week is `1380` (week 3) |

### Rejected candidates (looked suspicious, but are not findings)

| Candidate | Why it looked suspicious | Why it is not a finding |
|---|---|---|
| Claim 2's "15%" being a rounded/fudged figure | Round percentages in summaries are a common cover for drift | `1230 / 8200 = 0.15` **exactly**; no rounding is involved, so there is nothing to correct |
| Claim 2 using week-1 EOW `8200` as the quarter's *starting* value | The column is labelled "Active users (EOW)", so `8200` is really end-of-week-1, not the quarter's opening balance | The claim says "grew from 8,200 to 9,430" and both numbers appear verbatim in the table; the fixture provides no pre-quarter baseline, so the endpoints as stated are the best available and the claim is not misstating the table |
| Claim 6 being unverifiable from the table | Uptime has no column in the weekly table, so it initially reads as an unsourced number | The fixture's own notes line supplies `99.95%` from a status-page export; the claim matches it exactly. "Not recomputable" is not the same as "wrong" — it is recorded above as unmeasured |
| Claim 7 asserting causation ("explained by") from a note that says "coincides with" | Correlation-to-causation upgrades are a classic summary defect | The fixture itself attributes the spike to the campaign, and the spike is real (both columns peak in week 7). Marking this incorrect would contradict the source of truth; the causal-strength gap is instead recorded as unmeasured |
| Week-12/13 signup dip (`276`, `278`) as a possible unreported problem | The two lowest signup weeks close the quarter, which the summary never mentions | An *omission* from the summary is not a false numbered claim. No numbered claim covers it, so it is out of scope for this fact-check |
| Week-8 p95 (`380`) looking anomalously low versus week 9 (`410`) — possible transcription error in the fixture | An out-of-order pair in an otherwise monotone column suggests swapped cells | The task is to fact-check the summary against the table as given, treating the table as the source of truth. Reading `380`/`410` as swapped would be an unfounded edit to the source; taken as written, it yields the genuine week-9 regression reported in finding 3b |
| Claim 5's "held at about" as elastic hedging that might tolerate 1,450 | "about" could arguably absorb a modest miss | The gap is 11.5% and **every** week (min `1380`) exceeds 1,300; no reading of "about" covers a mean that no single week approaches. This stays a confirmed finding, not a rejection |

## Score

Composite gate values are tool-computed by `mission-state.py review-finalize` /
`push-score`; the authoritative values live in `.mission-state/` and are reproduced here without
re-transcribing reviewer prose (see Evidence for archive paths).

| Gate | Value | Pass condition | Met |
|---|---|---|---|
| Iteration | 1 (of `--max-iter 2`) | ≤ max-iter | yes |
| Reviewers | 2 (`arith`, `contract`), independent, launched in a single parallel message | `--min-reviewers 2` | yes |
| Threshold | 4.0 | — | — |
| **Composite score** | **4.89** | ≥ 4.0 | yes |
| `mission_achievement` | 5.0 | ≥ 3.5 | yes |
| `accuracy` | 4.85 | ≥ 3.5 | yes |
| `completeness` | 5.0 | ≥ 3.5 | yes |
| `usability` | 4.7 | ≥ 3.5 | yes |
| `open_high` | 0 | == 0 | yes |
| `review_agreement` | 5.0 (max axis delta 0.3, on `accuracy`) | max delta ≤ 1.5 | yes |
| Findings evidence | `.mission-state/archive/iter-1-14e011d0-reviews-316f8a99ee058549.json` | must exist | yes |
| Scoring evidence | `.mission-state/archive/iter-1-14e011d0-scoring-a104557a14b54760.json` | must exist | yes |

All values above are tool-computed by `mission-state.py review-finalize` (aggregate + `push-score`)
and re-verified by `mission-state.py closeout`, which returned `passes: true, forced: false` and
`next_action: report-complete`. No `--force` / `--approved-by-user` override was used.

Reviewer findings: 3 Low, 0 Medium, 0 High. All three were applied to this artifact before closeout
(garbled arithmetic aside in Claim 5; week-transition off-by-one in Claim 3's corrected value;
self-referential review wording in the Review section). Under the absolute-evaluation rule the
residual Low findings are why `accuracy` and `usability` are capped below 5.0.

**Specialists:** selected: none · used: none · degraded: none · unselected-manual: none.
Recorded decision `unavailable` / `provider-unavailable` — the benchmark forbids network and
external providers, so no external specialist was consulted; core reviewers were used instead.

## Stop Decision

**Decision: STOP after iteration 1 — gate passed.**

The stop rule was: stop after an iteration if and only if the tool-computed gate reports
`passes = true` (`composite_score ≥ 4.0`, `open_high == 0`, `max_agreement_delta ≤ 1.5`,
`min(scored_items) ≥ 3.5`, findings evidence present). `mission-state.py closeout` returned
`{"mark_passes": {"ok": true, "passes": true, "forced": false}, "next_action": "report-complete",
"loop_active": false}` at iteration 1, so the second permitted iteration was not needed.

The early-stop caveat in the loop rules (continue despite passing when composite is 4.0–4.3 with
3+ Medium findings) does not apply: composite is 4.89 and there are zero Medium and zero High
findings — only 3 Low, all already applied.

The mission's substantive completion condition — all 7 claims verified with arithmetic, 4 corrected
values stated, 3 correct claims confirmed in a verified-claims section, confirmed findings separated
from rejected candidates — is satisfied by this artifact. Had the gate reported `passes = false`,
a second (final) iteration would have run; at `--max-iter 2` exhaustion the run would have
terminated via `mark-halt --category partial-done` rather than a completion claim.

No irreversible operation was performed or requested: no commit, no push, no network call,
no package install. Exactly one artifact file was written.

## Evidence

| Item | Reference |
|---|---|
| Claim source fixture | `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/quarterly-summary.md` (7 numbered claims, lines 3–10) |
| Raw table fixture | `benchmarks/mission-vs-goal/fixtures/tail/metrics-reconciliation/weekly-metrics.md` (13 data rows, 5 metric columns, plus 2 notes lines) |
| Mission state session | `.mission-state/sessions/cc-9d165f25-52ec-4642-a666-ce7f9e742d67.json` (mission id `14e011d004b479fa`) |
| Canonical plan | `.mission-state/plans/563c92bbf41b9f49.json`, digest `sha256:563c92bbf41b9f49f04483e399657a7ab4a21ad52889248b2d236aa51f3f39db`, adopted via `planning adopt-core` |
| Reviewer A input (`arith`) | `.mission-state/archive/iter-1-14e011d0-review-input-4d064f4caa627295.json`, digest `sha256:4d064f4caa627295…`, imported via `review-import --iteration 1` |
| Reviewer B input (`contract`) | `.mission-state/archive/iter-1-14e011d0-review-input-51106ceb77c8c851.json`, digest `sha256:51106ceb77c8c851…`, imported via `review-import --iteration 1` |
| Review aggregate | `.mission-state/archive/iter-1-14e011d0-reviews-316f8a99ee058549.json`, digest `sha256:316f8a99ee058549…` |
| Scoring record | `.mission-state/archive/iter-1-14e011d0-scoring-a104557a14b54760.json`, digest `sha256:a104557a14b54760…` |
| Reviewer parallelism window | both reviewers `2026-08-19T08:03:31Z..2026-08-19T08:08:26Z` (single parallel dispatch), passed to `review-finalize --reviewer-window` |
| Reviewed revision scope | git, base `a722a058f9cd1ecc5a4a9b67175e30e97a7a1eb0`, head `f8a4b983b6d0e49e58d8cc530f5eb93bf97ed70e` (this artifact is an uncommitted working-tree file at that head; nothing was committed) |
| Arithmetic recomputation | Executed in a single local `python3` process over the transcribed table; results: signups `4217`, active growth `1230 / 8200 = 15.0%`, p95 ratio `620/330 = 1.8788`, p95 regression at week 9 only, tickets `61 / 210 = 29.05%`, infra sum `18850`, infra mean `1450.0`, week-7 maxima `410` / `1620` |
| Scope compliance | No file under `benchmarks/mission-vs-goal/` was opened, listed, or grepped except the two named fixtures and this artifact. Benchmark task definitions, scoring configuration, and answer keys were never read |

Unmeasured / not established (explicitly stated rather than asserted):

- **Prior-quarter support-ticket volume** — absent from the fixtures, so a literal quarter-over-quarter
  delta for claim 4 is uncomputable; only the within-quarter decline is measurable.
- **Uptime derivation** — no uptime data exists in the weekly table; claim 6 is verified only against
  the fixture's status-page-export note, not recomputed.
- **Causal link between the week-7 campaign and the week-7 spike** — the fixture asserts coincidence;
  no counterfactual or attribution data is present, so causal strength is unmeasured.
- **Whether the week-8/week-9 p95 values (`380`, `410`) are a fixture transcription artifact** — not
  determinable from the fixtures; treated as authoritative as written.
- **Any comparison against the other benchmark arm** — not measured and not claimed here.

## Assumptions

| ID | Assumption | Validation / basis |
|---|---|---|
| A1 | `weekly-metrics.md` is the source of truth; where the summary and the table disagree, the table wins | The fixture header states "Q3 raw table (source of truth)" |
| A2 | The quarter consists of exactly the 13 weeks in the table; no weeks are missing | 13 consecutive rows numbered 1–13, no gaps |
| A3 | "Total signups for the quarter" means the sum of the Signups column | Only signup data available; no alternative aggregation is defined in the fixtures |
| A4 | "Average weekly infra cost" means the unweighted arithmetic mean over the 13 weeks | All weeks are equal-length periods; no weighting basis is given |
| A5 | "improved Nx" for latency means the ratio of starting to ending p95 (lower is better) | Standard reading; the alternative (percent reduction, 46.8%) is also reported so the verdict does not hinge on this reading — neither reading yields 3x |
| A6 | The prose notes lines in `weekly-metrics.md` are part of the source of truth, not commentary | They appear inside the source-of-truth fixture and carry the only uptime figure |
| A7 | Claims are evaluated for factual accuracy against the table, not for completeness of the summary | The task asks to fact-check numbered claims; omissions are logged as rejected candidates instead |

---

## 修正履歴

| 日時 | 内容 |
|------|------|
| 2026-08-19 | 初版作成（mission arm, iteration 1, run 2026-08-19-tail-v280-r2） |
