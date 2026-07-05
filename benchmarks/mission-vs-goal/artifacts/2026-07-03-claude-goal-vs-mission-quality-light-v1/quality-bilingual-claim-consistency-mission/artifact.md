# quality-bilingual-claim-consistency — Mission Arm (light profile)

- Task id: `quality-bilingual-claim-consistency`
- Task category: documentation
- Arm: mission · Mission profile: light · Complexity: Complex
- Run: `2026-07-03-claude-goal-vs-mission-quality-light-v1`
- Mission state: `.mission-state/sessions/cc-72749ae0-97cc-4ebf-ae7a-112e89991edc.json` (mission_id `bb6918b7a275f53f`)

> Scope note: This artifact completes one controlled benchmark task only. It makes **no** claim that the
> mission arm is superior to any other arm. The "announcement" edited below is a **hypothetical**
> scenario supplied by the task; its numbers are scenario inputs, not measurements produced by this run.
> Where a value is unmeasured, it is labeled unmeasured.

---

## Mission

Update a hypothetical bilingual (English / Japanese) benchmark **announcement** so that the Japanese copy
never asserts more than the English evidence supports. Deliver an auditable artifact that a reviewer can
use to (a) verify EN↔JA claim parity, (b) separate measured from inferred claims, (c) list unsafe claims to
avoid, and (d) surface unresolved translation choices — without introducing superiority claims.

Definition of done (validator):
1. English/Japanese parity checks present.
2. Measured vs inferred claims separated.
3. Unsafe claims to avoid listed.
4. Unresolved translation choices listed.
5. Required headings present: Mission, Plan, Execution, Review, Score, Stop Decision, Evidence, Assumptions.

---

## Plan

Single plan → check → write pass (light profile; no repo-wide scan, no test suite).

| Step | Action | Output |
|---|---|---|
| P1 | Fix the hypothetical announcement's source-of-truth text (EN) and enumerate its atomic claims | Claim inventory |
| P2 | Classify each claim as **Measured** or **Inferred**; flag any **Unsupported** claim | Measured-vs-inferred table |
| P3 | Produce JA copy constrained so each JA sentence maps 1:1 to an EN claim of equal-or-weaker strength | EN/JA parity table |
| P4 | List unsafe claims to avoid and unresolved translation choices | Guardrail + open-questions lists |
| P5 | Self-review against validator; record score, stop decision, evidence map | Review / Score / Evidence |

---

## Execution

### Hypothetical announcement — English source of truth (frozen)

> **Announcing the v1 mission-vs-goal benchmark.** Across 24 documentation-style tasks in a single local
> run, the mission arm completed every required artifact. In this sample the mission arm produced more
> explicit evidence sections than the goal arm on the tasks where both arms ran. Results are from one
> unaudited local run and are not a general performance claim; per-task numbers and limits are in the repo.

Atomic EN claims extracted (C1–C6):

- **C1** — The benchmark is named "v1 mission-vs-goal" and covers documentation-style tasks.
- **C2** — Sample size = 24 tasks, one local run.
- **C3** — The mission arm completed every required artifact in this sample.
- **C4** — On tasks where both arms ran, the mission arm produced more explicit evidence sections than the goal arm.
- **C5** — Results are from one unaudited local run; not a general performance claim.
- **C6** — Per-task numbers and limits live in the repo.

### English/Japanese Parity

Each JA line is written to be **equal or weaker** than its EN source. No JA line adds a claim absent from EN.

| Claim | English (source) | Japanese (constrained) | Parity check |
|---|---|---|---|
| C1 | "benchmark … covers documentation-style tasks" | 「ドキュメント系タスクを対象とするベンチマーク」 | ✅ 1:1, same scope |
| C2 | "24 tasks, one local run" | 「1 回のローカル実行で 24 タスク」 | ✅ same number + same "one run" qualifier retained |
| C3 | "completed every required artifact **in this sample**" | 「**この標本では**必要な成果物をすべて完成」 | ✅ "in this sample / この標本では" scope kept; not generalized |
| C4 | "**more** explicit evidence sections … **on tasks where both arms ran**" | 「**両アームが実行されたタスクにおいて**、より多くの明示的な根拠セクションを生成」 | ⚠️ see Unresolved T1 (comparative wording risk) |
| C5 | "one unaudited local run; **not a general performance claim**" | 「監査前のローカル実行 1 回であり、**一般的な性能を主張するものではない**」 | ✅ disclaimer preserved verbatim in force |
| C6 | "per-task numbers and limits are in the repo" | 「タスクごとの数値と制約はリポジトリに記載」 | ✅ 1:1 |

Parity rules enforced:
- **No stronger quantifiers in JA**: EN "more/より多く" must not become JA 「最も/圧倒的に/大幅に」.
- **No dropped qualifiers**: every EN scope-limiter ("in this sample", "where both arms ran", "unaudited",
  "one run") has a JA counterpart in the same sentence.
- **No new subjects**: JA introduces no arm, metric, or timeframe not present in EN.

### Measured vs Inferred

| # | Claim | Status | Basis / gap |
|---|---|---|---|
| C1 | Naming & documentation scope | **Measured (definitional)** | Set by task/run metadata; not a performance figure. |
| C2 | 24 tasks, one run | **Measured** *within the hypothetical scenario* | Scenario input. In this real run, actual N across the suite is **unmeasured** — do not restate 24 as a fact of this run. |
| C3 | Mission arm completed every required artifact in sample | **Measured (scenario input)** | Would require a per-task completion log to hold in reality; that log is **unmeasured here**. |
| C4 | "more explicit evidence sections than goal arm" | **Inferred / comparative** | A relative comparison. Real per-arm section counts are **unmeasured in this run**; treat as illustrative only. |
| C5 | "unaudited, not a general claim" | **Measured (self-evident constraint)** | True by construction of a single local run. |
| C6 | "numbers and limits in repo" | **Measured (pointer)** | Verifiable by locating the files; pointer only, asserts no result. |

### Unsupported Claims (unsafe claims to avoid)

Claims that must **not** appear in either language:

- ❌ "Mission is better / faster / more reliable than goal" (**superiority claim** — out of scope and unmeasured).
- ❌ "State-of-the-art", "業界最高", "圧倒的", "最も優れた" — absolute superlatives with no measurement.
- ❌ Generalizing the sample: "always", "in all cases", "常に", "あらゆるケースで".
- ❌ Turning C4's *comparative-in-sample* into a *general* claim ("Mission produces more evidence" without "in this sample / where both arms ran").
- ❌ Dropping the "unaudited / one run / 監査前・1 回" qualifier in the JA headline while keeping the positive verb.
- ❌ Implying statistical significance from N with no variance/CI (**unmeasured** — no confidence interval computed).

### Unresolved Translation Choices

| ID | Item | Options | Risk if wrong | Recommendation (pending reviewer) |
|---|---|---|---|---|
| T1 | "more explicit evidence sections" | 「より多くの根拠セクション」 vs 「根拠セクションが多い」 | 「多い」can read as an inherent property → over-strong | Use 「両アームが実行されたタスクにおいて、より多くの…を生成」to keep the comparison bounded |
| T2 | "completed every required artifact" | 「すべて完成」 vs 「すべて完了」 | 「完了」can imply pass/quality, not just presence | Use 「すべて完成（存在を確認）」to mean *produced*, not *passed* |
| T3 | "unaudited local run" | 「監査前」 vs 「未検証」 | 「未検証」is broader/stronger (implies nothing verified) | Prefer 「監査前のローカル実行」 to match EN precisely |
| T4 | "benchmark" | 「ベンチマーク」 vs 「性能評価」 | 「性能評価」implies validated performance measurement | Keep loanword 「ベンチマーク」; avoid implying certified performance |

All four are **open**; none resolved unilaterally. A bilingual reviewer gate (below) is required before publishing.

---

## Review

Self-review (maker) against the validator, plus an explicit reviewer gate for the parts a maker cannot self-certify.

**Validator checklist**

- [x] EN/JA parity checks — present (parity table + enforced rules).
- [x] Measured vs inferred — present (6-row table with per-claim basis/gap).
- [x] Unsafe claims to avoid — present (Unsupported Claims list).
- [x] Unresolved translation choices — present (T1–T4).
- [x] Required headings — all 8 present.
- [x] Quality markers — Parity, Measured vs Inferred, Unsupported Claims, Unresolved Translation Choices,
      Evidence Map, Reviewer Gate, Residual Risk all covered.

**Reviewer Gate** (must pass before this hypothetical announcement could ship — NOT satisfied by this maker pass):

1. Native JA reviewer confirms no JA sentence is stronger than its EN source (esp. C4 / T1).
2. Reviewer resolves T1–T4 or documents a decision.
3. Reviewer confirms every EN scope-limiter survives in JA.
4. Reviewer confirms zero superiority claims in either language.

Status: **maker pass complete; independent bilingual reviewer sign-off is UNMEASURED / pending.**

---

## Score

Absolute self-assessment against the validator (not a comparison to any other arm):

| Dimension | Score /5 | Rationale |
|---|---|---|
| Mission achievement | 4.4 | All 5 definition-of-done items and 8 headings delivered. |
| Accuracy | 4.5 | Measured vs inferred separated; hypothetical numbers explicitly flagged, not passed off as run results. |
| Completeness | 4.3 | All 7 quality markers present; T1–T4 left open by design. |
| Practicality | 4.4 | Parity table + guardrail list are directly reusable by a bilingual reviewer. |
| Reviewer agreement | 4.0 | Single maker pass; independent bilingual sign-off still pending (bounded by light profile / max-iter 1). |

Composite (mean): **4.32** — recorded via `mission-state.py push-score`. min item 4.0 ≥ 3.5 gate.

---

## Stop Decision

- Budget: light profile, `--max-iter 1`. One plan→check→write pass executed.
- Gate at iter 1: composite 4.32 ≥ threshold 4.0; min item 4.0 ≥ 3.5; open High = 0.
- Per early-stop rule (composite ≥ 4.3 → mandatory stop) and `--max-iter 1`, **stop after iteration 1**.
- Residual items (T1–T4, reviewer sign-off) are deliberately left open, not defects — they are recorded as
  Residual Risk for the downstream bilingual reviewer, consistent with the "preserve measurement limits" mandate.
- Decision: **STOP — artifact written and validator-complete.** `mark-passes` applied.

---

## Evidence

**Evidence Map** — every claim in this artifact traces to a source:

| Artifact claim | Evidence source | Verifiable how |
|---|---|---|
| Artifact written to required path | This file at `benchmarks/…/quality-bilingual-claim-consistency-mission.md` | File exists at the mandated path |
| Mission state exists (auditable) | `.mission-state/sessions/cc-72749ae0-97cc-4ebf-ae7a-112e89991edc.json` | `mission-state.py get` |
| mission_id / threshold / max-iter | `mission-state.py init` output (mission_id `bb6918b7a275f53f`, threshold 4.0, max-iter 1) | Re-read state JSON |
| EN source claims C1–C6 | "English source of truth (frozen)" block above | Direct quotation |
| Parity / measured-vs-inferred / unsafe / unresolved | Tables in Execution | Reviewer inspection |
| Score composite 4.32 | Score table + `push-score` scoring JSON | `mission-state.py get` → `score_history[-1]` |
| Superiority claim absent | grep this file for absolute superlatives → none asserted as fact | Text search |

**Explicitly unmeasured in this run** (stated, not hidden):
- Real per-arm "explicit evidence section" counts (C4) — unmeasured; comparison is illustrative.
- Real suite N for this actual run (C2's "24" is a scenario input) — unmeasured.
- Statistical significance / confidence intervals — unmeasured (not computed).
- Independent bilingual reviewer sign-off — unmeasured (pending Reviewer Gate).

**Residual Risk**
- R1 — JA C4 could drift stronger than EN if T1 is mistranslated; mitigated by fixed wording, unresolved until reviewer confirms.
- R2 — Readers may over-read the hypothetical "24 tasks" as a real result; mitigated by scope note + Measured-vs-Inferred flag.
- R3 — Loss of a qualifier in future edits would re-introduce an unsupported claim; mitigated by the enforced parity rules.

---

## Assumptions

| # | Unknown | Working assumption | Contradiction trigger |
|---|---|---|---|
| A1 | Announcement content | Hypothetical, task-supplied; numbers are scenario inputs, not this-run measurements | If real measured numbers are provided, replace and re-label as Measured |
| A2 | "Preserve measurement limits" means | JA must be ≤ EN in claim strength; all qualifiers survive translation | Reviewer flags a JA line stronger than EN → revise |
| A3 | Superiority framing | Forbidden in both the announcement and this artifact | Any comparative-as-general claim appears → remove |
| A4 | Pass threshold | composite ≥ 4.0 and every item ≥ 3.5 | User-specified threshold overrides |
| A5 | Reviewer availability | No independent bilingual reviewer in this light single-pass run | Sign-off recorded as pending/unmeasured, not assumed done |

---

### Revision history
| Date | Change |
|---|---|
| 2026-07-03 | Initial artifact — single mission plan/check/write pass, light profile, iteration 1 |
