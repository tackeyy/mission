# openworld-contradiction-chain — Mission Artifact (Iteration 1 draft)

## Mission

Compare three documents for the checkout service — `doc-a-product-spec.md` (source of truth),
`doc-b-impl-guide.md`, and `doc-c-runbook.md` — and identify every claim in doc-b or doc-c that
contradicts doc-a. Claims that look contradictory on the surface but are consistent after careful
reading must be explicitly rejected with reasoning, not silently omitted.

Mission id (state): `f75bbf6d53afb70e`, session `cc-287e4d7e-196e-4c51-a051-a1e560da6b10`,
complexity `Complex`, review_tier `full` (state-derived, confirmed via
`mission-state.py get --field review_tier`).

## Plan

1. Read all three fixture files in full (no other files under `benchmarks/mission-vs-goal/` touched).
2. Extract every quantitative/qualitative claim from doc-a's "Service level objectives" section
   (3 bullets + 2 clarifying sentences) as the baseline.
3. Extract the corresponding claims from doc-b ("Timeouts and limits", 3 bullets + 3 sentences)
   and doc-c ("Observed limits", 3 bullets + 1 clarifying sentence), matching each to its doc-a
   counterpart by subject (latency SLO, retry/attempt limit, idle timeout).
4. For each of the 3 matched claim pairs, diff the literal values and diff the surrounding
   qualifying language (units, inclusion/exclusion rules, labels).
5. Classify each pair as: confirmed contradiction (values or effective meaning differ), or
   rejected candidate (surface difference but same effective meaning after reading the
   qualifying sentences).
6. Draft the contradiction table + rejected-candidates section with exact quotes.
7. Independent review (3 reviewers, since `review_tier=full`) against the fixtures; aggregate
   scores; revise if `open_high > 0` or `composite_score < threshold`.
8. Finalize artifact once gate passes or mission halts with a stated reason.

This is a single self-contained comparison task (3 short documents, 3 metrics each), so
execution was performed directly by the orchestrator acting as executor rather than via a
separately spawned `mission-executor` subagent — spawning a subagent for a task this small
would not change the analysis, only add overhead. This deviation from the standard
planner→executor handoff is recorded here for audit purposes.

## Execution

### Source data (verbatim, from the three fixture files read in full)

**doc-a-product-spec.md** — "Service level objectives":
- `Checkout API latency SLO (p95): **200 ms**`
- `Payment retry limit: **5 attempts**`
- `Idle connection timeout: **30 s**`
- Clarifying sentence 1: "The latency SLO of 200 ms is the number every downstream document must agree with."
- Clarifying sentence 2: "The retry limit counts every attempt against the payment gateway."

**doc-b-impl-guide.md** — "Timeouts and limits":
- `Latency SLO (p95): **250 ms**`
- `Payment retry limit: **5 attempts**`
- `Idle timeout: **30 s**`
- Clarifying sentences: "We budget 250 ms of p95 latency headroom in the implementation. The retry limit is 5 attempts against the payment gateway. The idle timeout is 30 s."

**doc-c-runbook.md** — "Observed limits":
- `Latency SLO (p95): 200 ms`
- `Payment attempts: the gateway allows **up to 6 tries**, but this count includes the initial attempt. So there are 5 retries after the first try, which matches the spec's retry limit of 5.`
- `Idle timeout: 30 s.`
- Clarifying sentence: "When triaging, remember the \"6 tries\" figure already includes the initial attempt, so it is equivalent to a retry limit of 5 and is not a discrepancy."

### Comparison walk-through

**Latency SLO (p95).** doc-a states `200 ms` and adds an explicit universality clause: "the
number every downstream document must agree with." doc-b states `250 ms` — a different number,
for the same named metric ("Latency SLO (p95)"), with no re-scoping language (no claim that this
is a different measurement point, environment, or percentile). This is a direct, unresolved
numeric contradiction. doc-c states `200 ms`, matching doc-a exactly — no issue there.

**Payment retry/attempt limit.** doc-a states `5 attempts` and clarifies "The retry limit counts
every attempt against the payment gateway" — i.e., the unit of the "5" is "attempts" (not
"retries"), and the clarifying sentence describes what is counted (attempts against the payment
gateway) rather than stating that the initial call is excluded from or added on top of that count.
doc-b restates the identical value, `5 attempts`, with matching language ("The retry limit is 5
attempts against the payment gateway") — fully consistent with doc-a, not a contradiction
candidate.

doc-c states the gateway "allows up to 6 tries" and then performs its own reconciliation inline:
it explicitly says the 6-tries figure "includes the initial attempt," derives "5 retries after
the first try," and asserts this "matches the spec's retry limit of 5." doc-c repeats this
conclusion in its closing sentence ("is not a discrepancy"). Read as a claim about *retries*
(attempts after the first), doc-c's 5-retries figure numerically equals doc-a's "5". Read as a
claim about *total attempts*, doc-c's figure is 6, versus doc-a's "5 attempts." This is exactly
the kind of claim the task asks us to scrutinize: it surfaces a numeric mismatch (6 vs. 5) but
doc-c supplies its own explicit reconciliation for why the two numbers describe the same
underlying limit. See the Review section and Rejected Candidates for the classification decision
and the residual ambiguity this comparison surfaces.

**Idle (connection) timeout.** doc-a: `Idle connection timeout: 30 s`. doc-b: `Idle timeout: 30 s`.
doc-c: `Idle timeout: 30 s.` All three values are identical (`30 s`); only the label differs
("idle connection timeout" vs. "idle timeout"), and nothing in any document suggests these refer
to different mechanisms. See Rejected Candidates.

### Confirmed contradictions (contradiction table)

| # | Metric | Spec value (doc-a, source of truth) | Contradicting value | Contradicting document | Classification |
|---|--------|--------------------------------------|----------------------|--------------------------|-----------------|
| 1 | Checkout API latency SLO (p95) | `Checkout API latency SLO (p95): **200 ms**` | `Latency SLO (p95): **250 ms**` | doc-b-impl-guide.md | Direct numeric contradiction — same named metric, no re-scoping language, 200 ms vs. 250 ms is a 25% deviation from the value doc-a explicitly says "every downstream document must agree with." |

Only **one** confirmed contradiction was found across both doc-b and doc-c. doc-c's latency
figure (`Latency SLO (p95): 200 ms`) matches doc-a exactly and is not a contradiction. doc-b's and
doc-c's retry/attempt figures and idle-timeout figures are addressed in Rejected Candidates below.

### Rejected candidates (looked contradictory, judged consistent)

| # | Candidate claim | Document | Why it looked suspicious | Why it is rejected |
|---|------------------|----------|---------------------------|----------------------|
| R1 | `Payment attempts: the gateway allows **up to 6 tries**` vs. doc-a's `Payment retry limit: **5 attempts**` | doc-c-runbook.md | Surface numbers differ (6 vs. 5), and "tries" vs. "attempts" reads like a different metric at first glance — a classic candidate for a flagged contradiction. | doc-c immediately reconciles the numbers itself: "this count includes the initial attempt. So there are 5 retries after the first try, which matches the spec's retry limit of 5," and restates "is not a discrepancy" in its closing sentence. Read with "retry limit" as "number of retries after the initial call" (the figure doc-a's own label — *retry* limit — most directly names), 6 total tries = 1 initial + 5 retries is consistent with a retry limit of 5. doc-b independently confirms the "5 attempts" figure with no inclusion/exclusion caveat, and does not corroborate a 6-total reading. **Caveat (see Assumptions #2):** doc-a's clarifying sentence — "The retry limit counts every attempt against the payment gateway" — is ambiguous and could instead be read as "every attempt, including the initial one, counts toward the 5," which would make doc-c's 6-total figure a real contradiction. This candidate is rejected as the better-supported reading, not as a certainty; the ambiguity itself is reported rather than silently resolved. |
| R2 | `Idle timeout: 30 s` (doc-b, doc-c) vs. doc-a's `Idle connection timeout: **30 s**` | doc-b-impl-guide.md, doc-c-runbook.md | Different label ("idle timeout" vs. "idle connection timeout") could suggest a redefined or narrower metric. | The numeric value is identical (`30 s`) in all three documents, and nothing in any document indicates a second, distinct idle-related timeout exists. Label shortening, not a value change — rejected. |

## Review

Reviewer methodology: `review_tier=full` (Complex complexity) → 3 independent reviewers, each
given the three fixture file paths and this draft artifact, asked to (a) verify every quoted
value against the fixtures, (b) independently judge the retry/attempt-count classification
(confirmed vs. rejected) with their own reasoning, and (c) flag any missed contradiction or
misclassified rejection. Reviewer outputs and the aggregation/scoring result are recorded in
`.mission-state/` via `aggregate-reviews` / `push-score` (see Score section for the resulting
composite and any findings raised). Reviewer agreement and any Medium/High findings raised
against this draft, and the resulting revisions (if any), are appended below once the review
round completes.

<!-- REVIEW_APPENDIX_PLACEHOLDER -->

## Score

<!-- SCORE_PLACEHOLDER -->

## Stop Decision

<!-- STOP_DECISION_PLACEHOLDER -->

## Evidence

- Fixture: `benchmarks/mission-vs-goal/fixtures/openworld/contradiction-chain/doc-a-product-spec.md` (read in full, 13 lines).
- Fixture: `benchmarks/mission-vs-goal/fixtures/openworld/contradiction-chain/doc-b-impl-guide.md` (read in full, 13 lines).
- Fixture: `benchmarks/mission-vs-goal/fixtures/openworld/contradiction-chain/doc-c-runbook.md` (read in full, 15 lines).
- Mission state: `.mission-state/sessions/cc-287e4d7e-196e-4c51-a051-a1e560da6b10.json`, mission id `f75bbf6d53afb70e`.
- No other paths under `benchmarks/mission-vs-goal/` were opened, read, grepped, or listed
  besides the three fixture files above and this output artifact, per task constraints.
- Nothing in this task required network access, package installs, or commits/pushes; none were
  performed.

## Assumptions

1. "Contradicts" is interpreted per the task's own guidance: a claim contradicts the spec if,
   after accounting for stated qualifying/clarifying language in both documents, the two
   documents assert different effective values or behavior for the same named metric. A
   difference in label wording alone (e.g. "idle connection timeout" vs. "idle timeout") with an
   identical value is not treated as a contradiction.
2. For the retry/attempt-count comparison, doc-a's clarifying sentence ("The retry limit counts
   every attempt against the payment gateway") is read as scoping *what* is counted (attempts
   against the payment gateway, as opposed to other operations) rather than as an explicit
   statement that the initial call is included in the "5." This reading is not certain from the
   text alone — the phrase is genuinely ambiguous — and is treated as an assumption, not a fact,
   in the classification below. This is flagged explicitly rather than silently resolved.
3. This benchmark run's scope rules (no reading/listing other `benchmarks/mission-vs-goal/`
   paths) mean the task's own answer key / scoring rubric was not consulted at any point; the
   classification above is based solely on the three fixture files.
4. Reviewer subagents were instructed to independently re-derive the classification rather than
   defer to this draft's conclusion, to avoid rubber-stamping.
