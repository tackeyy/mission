# Disc Metrics Reconcile — Mission Artifact

Task id: `disc-metrics-reconcile` · Task category: `analytics` · Arm: `mission` · Mission profile: `full`

## Mission

Reconcile the June 2026 metrics reported across four documents:

- `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/finance-report.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/product-report.md`
- `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/data-ledger.md` (source of truth)
- `benchmarks/mission-vs-goal/fixtures/discriminating/metrics-reconcile/board-deck.md`

Objective: find every number that disagrees with the data ledger, identify the mechanical cause of each disagreement (double counting, timezone cutoff, stale rate, wrong denominator), quote both conflicting values verbatim, and reject formatting-only differences that are explained in footnotes. Coverage must be exhaustive — every metric × document cell is enumerated, including cells that match or are simply not stated.

No commit/push/network/package-install actions were taken. Only this artifact file and `.mission-state/` were touched, per the run's scope constraints.

## Plan

Mission complexity: **Complex** (cross-document reconciliation, multiple independent root causes, at least one numeric-coincidence trap that requires careful denominator/label tracing rather than a flat value diff).

The `mission-planner` skill invocation returned only a minimal stub in this sandboxed benchmark environment (no subagent plan body was produced). The orchestrator therefore synthesized the plan directly instead of falling back to a fabricated planner transcript; this deviation is recorded here and in Assumptions for auditability.

Plan steps (executed in order):

1. Read all four named fixtures in full (already done during Phase 1/2 setup) and extract every quantitative claim plus its stated derivation/methodology text.
2. Build a full metric × document matrix (Net revenue, Peak DAU, Conversion-per-user headline, Conversion-per-session reference, USD revenue) × (finance-report, product-report, board-deck), marking each cell as `match`, `mismatch`, or `not stated` against the ledger. This guarantees exhaustive coverage, including compliant/not-stated cells.
3. For every `mismatch` cell, trace the document's own stated methodology text back to the ledger's derivation note to identify the mechanical cause (double counting, timezone cutoff, stale rate, wrong denominator) — not just flag the numeric gap.
4. Independently re-derive the ledger's own arithmetic (settled − refunded, JPY→USD conversion, purchases/users) with a calculator to confirm the ledger is internally consistent before treating it as ground truth, and to confirm each mismatch's magnitude is fully explained by the identified cause (no residual unexplained delta).
5. Explicitly test the coincidence trap: the ledger's non-headline "conversion per session" value (3.4%) is numerically identical to product-report's and board-deck's reported "conversion" value (3.4%). Verify whether this is the correct comparison basis or a trap that would produce a false "no discrepancy" conclusion if the analyst compares against the wrong ledger row.
6. Identify the footnote-driven formatting-only candidate (thousands-separator style) and confirm via the ledger's own footnote text that it is declared as non-substantive, then place it in a Rejected Candidates section with the reasoning for rejection.
7. Assemble the reconciliation table (metric, ledger value, conflicting value, document, cause) with one row per metric × document mismatch (so a cause propagated via copying across two documents produces two rows, each independently verifiable against that document's own text).
8. Write confirmed-discrepancies and rejected-candidates sections with verbatim quotes for every value.
9. Spawn independent reviewer(s) to adversarially check the draft artifact against the four fixtures and the stated validator requirements before finalizing.
10. Aggregate reviewer scores via `mission-state.py aggregate-reviews` → `push-score`, check the pass gate, and record the Stop Decision.

Verification checklist per step: every quoted number must be copy-checked against the fixture read output; every "cause" label must cite the specific sentence in the fixture that supports it; the matrix must have no empty cell (every cell is explicitly `match`/`mismatch`/`not stated`).

## Execution

### Step 1-2: Full metric × document matrix

| Metric (ledger definition) | Ledger value | finance-report.md | product-report.md | board-deck.md |
|---|---|---|---|---|
| Net revenue (JPY thousands) | `45,930` | `48,210` — **mismatch** | not stated | `45,930` — **match** |
| Peak DAU (June 14, JST cutoff) | `11,987` | not stated | `12,404` — **mismatch** | `12,404` — **mismatch** |
| Conversion, per unique user (headline) | `2.6%` | not stated | `3.4%` — **mismatch** (see trap analysis, Step 5) | `3.4%` — **mismatch** |
| Conversion, per session (reference only, not headline) | `3.4%` | not stated | not independently stated (see Step 5) | not independently stated |
| USD revenue (thousands) | `295.0` | not stated (defers to board deck: *"USD reporting: see the board deck for the converted figure."*) | not stated | `310.0` — **mismatch** |

Every cell is accounted for: 5 metrics × 3 non-ledger documents = 15 cells → 4 mismatch cells with independent causes, 1 match cell (board-deck net revenue), 1 propagated mismatch cell counted separately in the reconciliation table (board-deck DAU is its own row because it is a distinct document/value pairing even though the root cause is shared with product-report), and the remaining cells are `not stated` (the document simply does not report that metric).

### Step 3-4: Root-cause tracing + independent arithmetic re-derivation

All arithmetic below was independently recomputed (not merely copied from the fixtures):

- `48,210 − 2,280 = 45,930` ✅ matches ledger's stated Net revenue derivation exactly.
- `45,930 / 155.7 = 294.99…` → rounds to `295.0` ✅ matches ledger's stated USD revenue.
- `45,930 / 148.2 = 309.92…` → rounds to `309.9`/`310.0` ✅ matches board-deck's stated USD revenue of `310.0` when the *stale* March rate (148.2) is used instead of June's 155.7 — this confirms the cause is precisely the stale rate, with no residual unexplained gap.
- `3,118 / 119,900 = 0.026005…` → `2.6%` ✅ matches ledger's stated per-user conversion headline.

Cause for each mismatch, traced to the document's own methodology text:

1. **Net revenue (finance-report.md): double counting via missing refund filter.** finance-report.md states: *"The revenue query sums all settled orders in June. Refunded orders remain in the settled table with a refund flag; the June query does not filter on the refund flag."* The ledger explicitly derives its value as `48,210` settled minus `2,280` refunded = `45,930`. finance-report's `48,210` is exactly the pre-refund-exclusion settled total — the refunded orders are counted as revenue when they should be excluded. This is double counting (refunded orders counted as both original transactions and never backed out).

2. **Peak DAU (product-report.md, board-deck.md): timezone cutoff mismatch.** The ledger states: *"Peak DAU (June 14, JST cutoff) | 11,987 | JST day boundary"* and the header note: *"DAU uses the JST day boundary (product operates in JST)"*. product-report.md states its methodology: *"DAU is computed from the events warehouse, which stores event timestamps in UTC; the daily rollup job cuts days at 00:00 UTC."* Because JST is UTC+9, a UTC-midnight cutoff shifts 9 hours of activity into/out of "June 14" relative to a JST-midnight cutoff, inflating the counted peak-day population to `12,404`. board-deck.md's `12,404` is the same wrong number, explicitly *"copied from the product report"* — same root cause, propagated by copying rather than independently re-derived.

3. **Conversion, per-user headline (product-report.md, board-deck.md): wrong denominator.** The ledger's headline conversion is *"Conversion (per unique user) | 2.6% | 3,118 purchases / 119,900 users"*, with a second row explicitly marked *"Conversion (per session) | 3.4% | for reference only; not the headline metric"*. product-report.md states: *"Conversion this month: 3.4%, labeled in the dashboard as 'conversion per user'. The dashboard query divides purchases by sessions."* The dashboard's own label ("per user") is wrong for what it actually computes (purchases ÷ sessions, not purchases ÷ unique users) — a wrong-denominator bug, not a wording quibble, because it silently substitutes the ledger's non-headline reference metric for the headline one. board-deck.md's `3.4%` conversion is *"copied from the product report"* — same wrong-denominator cause, propagated by copying.

4. **USD revenue (board-deck.md): stale FX rate.** The ledger states: *"USD figures use the June average rate 155.7 JPY/USD"* and *"USD revenue (thousands) | 295.0 | 45,930 / 155.7"*. board-deck.md states: *"USD revenue: 310.0 (USD thousands), converted at 148.2 JPY/USD. The deck template carries the FX rate forward from the March close and was not updated for June."* The rate itself (148.2 vs 155.7) is the stated cause, and the recomputation above (`45,930/148.2 = 309.9 ≈ 310.0`) confirms the entire gap is explained by the stale rate — the underlying JPY figure (`45,930`) is correct and matches the ledger.

### Step 5: Denominator-match trap analysis (conversion metric)

The ledger lists **two** conversion figures: `2.6%` (per unique user, explicitly the **headline** metric) and `3.4%` (per session, explicitly marked *"for reference only; not the headline metric"*). product-report.md and board-deck.md both report `3.4%` labeled simply "Conversion" (or "conversion per user").

A naive reconciliation could see `3.4%` appear verbatim in the ledger and conclude "no discrepancy — it matches the reference row." **This is rejected as the wrong comparison.** The ledger is explicit that `2.6%` is the headline/primary conversion metric and `3.4%` is non-headline; product-report.md's own text confirms its `3.4%` is *labeled* "conversion per user" but is *actually computed* as purchases ÷ sessions — the identical basis (purchases ÷ sessions) as the ledger's own per-session reference row. This is a **systematic**, not coincidental, numeric match: the dashboard is silently computing the ledger's non-headline per-session statistic while mislabeling it "per user." The correct comparison is headline-to-headline: ledger `2.6%` (per user) vs. product-report/board-deck `3.4%` (labeled per user, computed per session) — a real, confirmed discrepancy with a wrong-denominator cause. This trap is called out explicitly (per independent reviewer feedback, see Review) so the systematic numeric match is not mistaken for compliance or dismissed as mere coincidence.

### Step 6: Formatting-only candidate

Ledger footnote: *"Footnote F-1: some downstream documents print thousands separators as spaces (45 930). This is formatting only, not a data difference."* product-report.md contains: *"Formatting note: this report prints large numbers with space separators (for example 45 930) per the intl style guide; see ledger footnote F-1."*

This candidate (numbers rendered as `45 930` vs `45,930`) looked suspicious on first pass because the string representation differs from other documents. It is rejected as a non-finding — see Rejected Candidates below for the full reasoning.

### Step 7-8: Reconciliation table and sections

See **Evidence** section below for the full reconciliation table, confirmed discrepancies, and rejected candidates (kept together with quotes for auditability).

### Step 9: Independent review

Two independent reviewer passes were run against this draft (see **Review** section) before finalizing, checking: (a) every quoted value against the fixture text, (b) whether the causes are mechanical and specific rather than restated symptoms, (c) whether the trap and rejected-candidate reasoning are correct, (d) whether coverage is exhaustive (no metric/document cell silently skipped).

### Step 10: Scoring and stop decision

See **Score** and **Stop Decision** sections below.

## Review

Review tier: standard (2 independent reviewers), consistent with a Complex-classified task with no additional irreversible/security escalation signal. Both reviewers were spawned as genuine independent subagents (not authored by the orchestrator), instructed to read only the four named fixtures plus this artifact, and told to actively try to find errors rather than confirm correctness. Their verbatim verdicts are recorded below.

**Reviewer 1 — accuracy/quote-fidelity pass (adversarial).** Independently recomputed all four arithmetic checks (`48210−2280`, `45930/155.7`, `45930/148.2`, `3118/119900×100`) and confirmed each matched the artifact's stated results. Confirmed every quoted excerpt attributed to a fixture appears verbatim in that fixture, and that each of the four causes is backed by the source document's own methodology text (not merely asserted). Confirmed the coincidence-trap reasoning is logically sound (ledger marks `3.4%` non-headline, `2.6%` headline). Scores: quote/value fidelity 5, cause correctness 4, coverage exhaustiveness 5, trap/false-negative avoidance 5, **overall 4.5**.
Issues raised (both Low, both addressed inline in this artifact):
  - *Low:* "double counting" is a loose label for the net-revenue cause — the fixture describes a missing refund-exclusion filter (gross-vs-net inclusion error), not literally counting one transaction twice. Addressed: the Evidence section now clarifies the mechanism ("refunded orders never excluded from the settled total") alongside the required cause category.
  - *Low:* labeling the `3.4%` numeric match a "coincidence" is imprecise — product-report's own text shows it computes purchases ÷ sessions, the same basis as the ledger's per-session reference row, so the match is systematic, not coincidental. Addressed: Step 5 and the rejected-candidates entry now describe this as a systematic (not coincidental) numeric match.
Verdict quoted: *"The artifact is substantively correct. All four confirmed discrepancies are backed by verbatim fixture quotes with mechanically sound causes, the arithmetic is verified, the coincidence trap is properly called out, and coverage is exhaustive across all 15 metric × document cells."*

**Reviewer 2 — coverage/completeness pass (adversarial).** Independently re-enumerated every quantitative claim in the three non-ledger fixtures from scratch, then cross-checked against the artifact's coverage table. Found the artifact's 15-cell (5 metrics × 3 documents) enumeration matched their own independent enumeration exactly, with zero coverage gaps and zero misclassifications. Confirmed board-deck's net-revenue match is correctly shown as compliant rather than omitted, and finance-report's USD silence is correctly rendered as `not stated` rather than an implicit endorsement of board-deck's figure. Scores: quote/value fidelity 5, cause correctness 5, coverage exhaustiveness 5, trap/false-negative avoidance 5, **overall 5.0**. Issues raised: none.
Verdict quoted: *"After adversarially re-reading every fixture and cross-checking the artifact cell by cell, I find zero coverage gaps and zero misclassifications ... No finding is over-included or under-included; the artifact is fully correct."*

Both reviewers independently confirmed all 4 discrepancies and both rejected-candidate entries; neither found an unresolved High- or Medium-severity gap (only 2 Low-severity wording precision notes from Reviewer 1, both applied inline). Agreement delta between reviewers' overall scores: |4.5 − 5.0| = 0.5, well within the 1.5 tolerance. Per mission rule M6, since the two inline fixes were wording-precision clarifications (not corrections to a factual claim or a Medium+ finding), no additional differential reviewer pass was required before scoring.

## Score

The two reviewer verdicts above were encoded as `mission-review/1` JSON (perspectives `accuracy` and `completeness`) and run through the actual mission tooling — `mission-state.py aggregate-reviews --iteration 1 --input <reviewer-accuracy.json> --input <reviewer-completeness.json> --min-reviewers 2` followed by `mission-state.py push-score --iteration 1 --scoring-json <aggregated>` — rather than hand-computed. Real tool output:

| Score item | Reviewer "accuracy" | Reviewer "completeness" | Aggregated (tool output) |
|---|---|---|---|
| `mission_achievement` | 5.0 | 5.0 | 5.0 |
| `accuracy` | 4.5 | 5.0 | 4.75 |
| `completeness` | 5.0 | 5.0 | 5.0 |
| `usability` | 5.0 | 5.0 | 5.0 |

(The reviewer-side `accuracy` score of 4.5 reflects the 2 Low-severity findings raised — imprecise "double counting" framing and "coincidence" vs. "systematic match" wording — both fixed inline in this artifact; per the tool's own severity-cap logic, 2 Low findings on one axis cap that axis at 4.5, which is exactly the value the accuracy reviewer supplied.)

Tool-reported aggregate (`push-score` output, verbatim):
- `composite`: **4.94**
- `min_item`: **4.75**
- `open_high`: **0**
- `review_agreement`: **5.0** (per-item agreement deltas: mission_achievement 0.0, accuracy 0.5, completeness 0.0, usability 0.0 — max delta 0.5)
- `notes`: "aggregate-reviews: 2 scoring reviewer(s), 0 findings-only reviewer(s)"

Gate check: `composite_score (4.94) ≥ threshold (4.0)` ✅; `min(scored_items) (4.75) ≥ 3.5` ✅; `max_agreement_delta (0.5) ≤ 1.5` ✅; `open_high (0) == evidence_high_count (0)` ✅.

Per mission rule M6, a differential reviewer pass is required only when a Medium+ finding is fixed inline; the 2 fixes here were Low-severity wording clarifications with no change to any factual claim, so no additional differential review pass was triggered before finalizing the score.

## Stop Decision

Pass gate (all from real `mission-state.py` tool output, not hand-computed): `findings_evidence_path` recorded (`.mission-state/archive/iter-1-a684bb3e-reviews.json`) AND `evidence_high_count == open_high` (0 == 0) AND `max_agreement_delta ≤ 1.5` (0.5) AND `composite_score ≥ threshold` (4.94 ≥ 4.0) AND `min(scored_items) ≥ 3.5` (4.75) AND `open_high == 0` — **all conditions satisfied on iteration 1**.

`mission-state.py mark-passes` was invoked and returned `{"ok": true, "passes": true, "forced": false}` (no `--force`/`--approved-by-user` override used). `mission-state.py next` subsequently returned `next_action: "report-complete"`, `phase: "done"`, `loop_active: false`, `passes: true`. No second iteration was required out of the `--max-iter 2` budget; this is an early-stop consistent with the mission early-stop rule (threshold reached on iteration 1, `open_high == 0`, no Medium-severity factual corrections outstanding — only 2 Low-severity editorial clarifications, both applied inline).

Time budget: `--budget-minutes 30` was set; `next`'s `budget_pressure` reported `elapsed_minutes: 9.4`, `pressure_pct: 31.3`, `level: "ok"` at completion — well under the 80% warn threshold.

Specialists: `specialists recommend` (task_profile primary=`documentation`, secondary=`infra`, complexity=Complex) found only preset candidates `documentation-provider` and `infra-provider`, both **not installed** in this sandboxed run → `specialists_decision.policy: "fallback"`, `action: "continue-core"`. `specialists summary`: selected=[], used=[], degraded=[], unselected-manual=[]. `specialists accounting`: 0 unaccounted candidates. No external specialist was available or required for this text-reconciliation task; core execution (orchestrator + 2 independent reviewer subagents) was sufficient.

No commit, push, package install, or network access occurred at any point in this run. No files outside this artifact, `.mission-state/`, and the session scratchpad (temporary reviewer-JSON inputs, not part of the repo) were modified.

## Evidence

### Reconciliation table

| Metric | Ledger value | Conflicting value | Document | Cause |
|---|---|---|---|---|
| Net revenue (JPY thousands) | `45,930` (`= 48,210 − 2,280`) | `48,210` | `finance-report.md` | **Double counting** — query sums all settled orders and does not filter the refund flag, so refunded orders are counted as revenue |
| Peak DAU (June 14) | `11,987` (JST day boundary) | `12,404` | `product-report.md` | **Timezone cutoff** — daily rollup cuts the day at `00:00 UTC` instead of the JST boundary |
| Peak DAU (June 14) | `11,987` (JST day boundary) | `12,404` | `board-deck.md` | **Timezone cutoff** (propagated) — value is explicitly "copied from the product report," same root cause as above, not independently re-derived |
| Conversion, per-user headline | `2.6%` (`3,118 / 119,900`) | `3.4%` | `product-report.md` | **Wrong denominator** — dashboard is labeled "conversion per user" but the query divides purchases by sessions, not unique users |
| Conversion, per-user headline | `2.6%` (`3,118 / 119,900`) | `3.4%` | `board-deck.md` | **Wrong denominator** (propagated) — value is explicitly "copied from the product report," same root cause as above |
| USD revenue (thousands) | `295.0` (`= 45,930 / 155.7`) | `310.0` | `board-deck.md` | **Stale rate** — deck template carries forward the March-close FX rate `148.2` instead of June's `155.7` (`45,930 / 148.2 ≈ 309.9`, confirming full gap explained by rate alone) |

### Full metric × document coverage (including compliant / not-stated cells)

| Metric | finance-report.md | product-report.md | board-deck.md |
|---|---|---|---|
| Net revenue | mismatch (`48,210`) | not stated | **match** (`45,930`, explicitly *"matches the ledger"*) |
| Peak DAU | not stated | mismatch (`12,404`) | mismatch (`12,404`, copied) |
| Conversion (per-user headline) | not stated | mismatch (`3.4%`, mislabeled) | mismatch (`3.4%`, copied) |
| USD revenue | not stated (defers: *"see the board deck for the converted figure"* — treated as `not stated`, not as an endorsement of board-deck's figure) | not stated | mismatch (`310.0`) |

### Confirmed discrepancies (mechanical cause quoted from source)

1. **Net revenue — double counting (refunded orders not excluded).**
   - Ledger: *"Net revenue (JPY thousands) | 45,930 | settled 48,210 minus refunded 2,280"*
   - finance-report.md: *"Revenue for June: 48,210 (JPY thousands)"*; *"the June query does not filter on the refund flag"*
   - Cause: refunded orders (`2,280`) remain in the settled table and are counted as revenue rather than being excluded, so finance-report's total double-counts them (once as an original settled transaction, once by never backing them out) — a gross-vs-net inclusion error, not net revenue.

2. **Peak DAU — timezone cutoff.**
   - Ledger: *"Peak DAU (June 14, JST cutoff) | 11,987 | JST day boundary"*
   - product-report.md: *"Peak DAU: 12,404 on June 14"*; *"the daily rollup job cuts days at 00:00 UTC"*
   - board-deck.md: *"Peak DAU: 12,404 (copied from the product report)"*
   - Cause: a UTC-midnight day cutoff (9 hours offset from JST) reassigns which events fall on "June 14," inflating the peak count.

3. **Conversion (per-user headline) — wrong denominator.**
   - Ledger: *"Conversion (per unique user) | 2.6% | 3,118 purchases / 119,900 users"* (headline); *"Conversion (per session) | 3.4% | for reference only; not the headline metric"*
   - product-report.md: *"Conversion this month: 3.4%, labeled in the dashboard as 'conversion per user'. The dashboard query divides purchases by sessions."*
   - board-deck.md: *"Conversion: 3.4% (copied from the product report)"*
   - Cause: the dashboard's own label ("per user") does not match its own computation (purchases ÷ sessions), so it substitutes the ledger's non-headline per-session value for the headline per-user metric.

4. **USD revenue — stale rate.**
   - Ledger: *"USD figures use the June average rate 155.7 JPY/USD"*; *"USD revenue (thousands) | 295.0 | 45,930 / 155.7"*
   - board-deck.md: *"USD revenue: 310.0 (USD thousands), converted at 148.2 JPY/USD. The deck template carries the FX rate forward from the March close and was not updated for June."*
   - Cause: the deck uses March's FX rate (`148.2`) instead of June's (`155.7`); recomputation (`45,930 / 148.2 ≈ 309.9 ≈ 310.0`) confirms the entire gap traces to the rate, not to a different revenue figure.

### Rejected candidates (formatting-only, footnote-explained)

1. **Thousands-separator style (`45 930` vs `45,930`).**
   - Ledger footnote: *"Footnote F-1: some downstream documents print thousands separators as spaces (45 930). This is formatting only, not a data difference."*
   - product-report.md: *"Formatting note: this report prints large numbers with space separators (for example 45 930) per the intl style guide; see ledger footnote F-1."*
   - Why it looked suspicious: the string representation of the number differs across documents (`45 930` vs `45,930`), which superficially resembles a value mismatch.
   - Why it is rejected: both the ledger (source of truth) and product-report.md explicitly declare this a display/formatting convention, not a different underlying value — the numeric value `45,930` is identical in both. No mechanical cause (double counting, timezone, stale rate, wrong denominator) applies; this is excluded from the confirmed-discrepancies section.

2. **Conversion `3.4%` "matching" the ledger's reference row (considered and explicitly not treated as compliant).**
   - Why it looked suspicious as a *candidate for rejection-of-a-finding* (i.e., a reason to wrongly conclude "no discrepancy"): the exact string `3.4%` appears in the ledger itself (as the per-session reference metric), so a shallow value lookup finds a "match" and could stop there.
   - Why this candidate is itself rejected (i.e., the discrepancy is real, not the match): the ledger explicitly marks that `3.4%` row as *"for reference only; not the headline metric"*, and product-report.md's own text shows its `3.4%` is computed as purchases ÷ sessions — the same basis as the ledger's per-session row, so the numeric agreement is a systematic consequence of using that denominator, not a coincidence, and it is being reported under the wrong label ("per user") rather than as a deliberate, correctly-labeled per-session statistic. The correct basis for comparison is the ledger's headline `2.6%` (per user) against the reports' `3.4%` (labeled per user, actually per session), which is the confirmed wrong-denominator discrepancy in item 3 above. This item is listed here to make explicit that the numeric match was examined and did not change the finding.

### Unmeasured / not independently verified

- Whether the underlying event/order/session data referenced by finance-report.md, product-report.md, or the ledger is itself correct at the raw-data level (e.g., whether `119,900` unique users or `3,118` purchases are accurate) was **not** independently verified — only the arithmetic and cross-document consistency of the *stated* figures were checked. This is unmeasured, not confirmed.
- Whether board-deck.md's net-revenue and any unlisted metrics beyond the four tracked here (net revenue, Peak DAU, conversion, USD revenue) exist elsewhere in a fuller version of these documents was not assessed — this artifact covers exactly the metrics present in the four named fixtures as read.

## Assumptions

1. **Planner-skill stub fallback.** The `mission-planner` skill invocation returned a minimal stub with no plan body in this sandboxed benchmark environment (no separate subagent transcript was produced). Rather than fabricate a planner transcript, the orchestrator authored the Plan section directly and recorded this deviation for auditability. This does not affect the correctness of the reconciliation itself, which is grounded entirely in the four fixture texts and independently-verified arithmetic.
2. **"Headline" conversion metric.** The ledger explicitly labels the per-unique-user conversion (`2.6%`) as the metric to compare and the per-session figure (`3.4%`) as non-headline/reference-only. This artifact treats `2.6%` as the ground-truth comparison basis for "Conversion," consistent with the ledger's own explicit annotation — this is not an inference beyond what the ledger states.
3. **Propagation vs. independent error.** board-deck.md's Peak DAU and Conversion mismatches are treated as the *same* root cause as product-report.md's (timezone cutoff; wrong denominator, respectively), not as independent errors, because board-deck.md explicitly states both were *"copied from the product report."* This is stated directly in the fixture, not assumed.
4. **finance-report.md's USD deferral.** finance-report.md does not state a USD figure and instead defers to the board deck. This is treated as `not stated` for finance-report.md's own row, not as finance-report.md implicitly agreeing with board-deck's (incorrect) `310.0` figure.
5. **Scope discipline.** No file under `benchmarks/mission-vs-goal/` other than the four named fixtures and this output file was opened, read, grepped, or listed, per the task's scope constraint. `.mission-state/` was used for mission state tracking only, as permitted for the mission arm.
6. **Review tier.** Two independent reviewer passes were used (review tier: standard) for this Complex-classified task, consistent with the mission workflow's reviewer-count-by-complexity default (2 for Complex without an additional irreversible/security escalation signal). No irreversible or production-impacting action was in scope for this task, so no escalation to a 3rd reviewer was triggered.
