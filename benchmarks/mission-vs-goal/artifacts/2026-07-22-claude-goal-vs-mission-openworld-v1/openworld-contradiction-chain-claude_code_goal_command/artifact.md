# openworld-contradiction-chain — claude_code_goal_command

## Goal

Compare the three fixture documents —
`benchmarks/mission-vs-goal/fixtures/openworld/contradiction-chain/doc-a-product-spec.md` (source of truth),
`benchmarks/mission-vs-goal/fixtures/openworld/contradiction-chain/doc-b-impl-guide.md`, and
`benchmarks/mission-vs-goal/fixtures/openworld/contradiction-chain/doc-c-runbook.md` —
and find every claim in the implementation guide or runbook that contradicts the product spec, quoting
exact values from each document. Claims that look contradictory but are actually consistent after careful
reading must be rejected as non-findings with reasoning. Completion is controlled by Claude Code's built-in
`/goal` command (this artifact + its required headings satisfy the goal condition).

## Result

Three metrics are defined in doc-a (checkout service: latency SLO, payment retry limit, idle timeout) and
each is restated in doc-b and doc-c. One of the three is a direct, unambiguous numeric contradiction. The
other two initially look like they might diverge but resolve to being consistent once the full sentence
(not just the headline number) is read.

### Contradiction table

| # | Metric | doc-a (spec, source of truth) | Contradicting doc | Contradicting value | Classification |
|---|--------|-------------------------------|--------------------|----------------------|-----------------|
| 1 | Checkout API latency SLO (p95) | "Checkout API latency SLO (p95): **200 ms**" | doc-b-impl-guide.md | "Latency SLO (p95): **250 ms**" (also restated as "We budget 250 ms of p95 latency headroom in the implementation.") | **Direct numeric contradiction** — same metric, same units, different number (200 ms vs 250 ms), with no qualifying language in doc-b that would reconcile the two figures. |

No other contradictions were confirmed. See Evidence section for the full walk-through of the two other metrics, both of which were investigated as candidates and rejected.

## Evidence

All quotes below are copied verbatim from the three named fixture files.

### 1. Latency SLO — CONFIRMED contradiction

- doc-a-product-spec.md: `Checkout API latency SLO (p95): **200 ms**` and "The latency SLO of 200 ms is the number every downstream document must agree with."
- doc-b-impl-guide.md: `Latency SLO (p95): **250 ms**` and "We budget 250 ms of p95 latency headroom in the implementation."
- doc-c-runbook.md: `Latency SLO (p95): 200 ms` (matches doc-a).

doc-a explicitly states 200 ms is the number "every downstream document must agree with," and doc-b instead documents 250 ms with no caveat, alternate scope, or unit difference that would explain the gap. This is a straightforward, high-confidence contradiction between the implementation guide and the spec.

### 2. Payment retry limit / attempt count — investigated, REJECTED as non-finding (with a noted caveat)

- doc-a-product-spec.md: `Payment retry limit: **5 attempts**` and "The retry limit counts every attempt against the payment gateway."
- doc-b-impl-guide.md: `Payment retry limit: **5 attempts**` and "The retry limit is 5 attempts against the payment gateway." — this is an exact match to doc-a, not a candidate at all.
- doc-c-runbook.md: `Payment attempts: the gateway allows **up to 6 tries**, but this count includes the initial attempt. So there are 5 retries after the first try, which matches the spec's retry limit of 5.` Also: "the '6 tries' figure already includes the initial attempt, so it is equivalent to a retry limit of 5 and is not a discrepancy."

Why this looked suspicious: the headline number in doc-c ("up to 6 tries") does not match the headline number in doc-a ("5 attempts"). A surface-level scan that only compares the two bolded numbers (6 vs 5) would flag this as a contradiction.

Why it is rejected as a non-finding: doc-c does not stop at "6 tries" — it immediately explains that the 6 includes the initial attempt, leaving 5 retries after the first try, and explicitly states this "matches the spec's retry limit of 5" and "is not a discrepancy." The runbook performs its own reconciliation and states the conclusion in plain language, so on its face this is consistent, not contradictory.

Caveat (flagged, not treated as a confirmed finding): doc-a's clarifying sentence — "The retry limit counts every attempt against the payment gateway" — is ambiguous. It can be read as "every attempt, including the initial one, counts toward the cap of 5" (i.e., 5 total gateway calls), which would put doc-a's total at 5 and doc-c's total at 6 (1 initial + 5 retries) — an actual mismatch. It can equally be read as merely clarifying that all attempt types count toward the retry counter (as opposed to only certain failure types), without asserting anything about whether the initial call is included in the "5." Because doc-a's sentence does not unambiguously state which reading is correct, and because doc-c explicitly performs the reconciliation and states the result is consistent, this is reported as a rejected candidate rather than a confirmed contradiction. This is the weakest of the three metrics to fully verify and depends on how the spec's clarifying sentence is interpreted.

### 3. Idle timeout — investigated, REJECTED as non-finding

- doc-a-product-spec.md: `Idle connection timeout: **30 s**`
- doc-b-impl-guide.md: `Idle timeout: **30 s**` and "The idle timeout is 30 s."
- doc-c-runbook.md: `Idle timeout: 30 s.`

Why this looked suspicious: doc-a uses the label "Idle connection timeout" while doc-b and doc-c both shorten it to "Idle timeout," which could suggest they refer to a different (narrower or broader) timeout setting.

Why it is rejected as a non-finding: the numeric value is identical (30 s) across all three documents, and all three appear under a "Timeouts/limits" section about the same checkout service with no other idle-timeout value introduced anywhere. There is no textual evidence of a scope difference — only a shortened label. Treated as a naming/paraphrase variation, not a value contradiction.

## Assumptions

- The three fixture files are read in full and are the only source of truth for this analysis; no other files under `benchmarks/mission-vs-goal/` were opened, grepped, or listed, per the task rules.
- doc-a-product-spec.md is treated as authoritative for all three metrics, per the task prompt ("The product spec (doc-a) is the source of truth").
- Where doc-a's prose is ambiguous (the retry-limit clarifying sentence), that ambiguity is stated explicitly rather than resolved by assumption in either direction, and the corresponding candidate is classified as rejected (not confirmed) because the runbook's own explicit reconciliation is the stronger, unambiguous piece of evidence in the fixture text.
- No external context (e.g., real-world conventions for what "retry limit" usually means in payment systems) was used to adjudicate the ambiguous clause — only the text present in the three fixtures.
- This artifact does not compare formatting, section ordering, or any content outside the three named numeric SLO/limit fields, since the task scope is contradictions in claims, not stylistic differences.

## Stop Condition

This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-07-22-claude-goal-vs-mission-openworld-v1/openworld-contradiction-chain-claude_code_goal_command.md` and contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition, satisfying the condition registered with Claude Code's `/goal` command for this run. No commit, push, package install, or network access was performed. No files were read, grepped, or listed under `benchmarks/mission-vs-goal/` other than the three named fixture files and this output file.
