# tail-bilingual-release-drift — mission arm (rep2)

## Mission

Compare the bilingual release announcement pair claim by claim, treating
`benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.en.md`
as the source of truth, and report every place where
`benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.ja.md`
is stronger than, numerically different from, or missing a safety-relevant
statement of the English evidence. Reworded-but-equivalent claims must be
rejected as non-findings with reasoning.

- Task id: `tail-bilingual-release-drift`
- Category: documentation
- Arm: mission (profile `full`), complexity `Complex`, `--max-iter 2`
- Scope: read the two named fixtures; write exactly one artifact at
  `benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-bilingual-release-drift-mission-rep2.md`;
  mission bookkeeping under `.mission-state/`. No commits, no network, no
  package installs. No benchmark-superiority claim is made anywhere in this
  artifact.

## Plan

Adopted canonical plan: `.mission-state/plans/930a434defe02b4a.json`
(digest `sha256:930a434defe02b4a5f3b99028abf217e60504e03fd2c92366d18d3d2c04cbdad`,
source `core`, source_id `core-0-c9933f54e39d`).

| step | action | acceptance check |
|---|---|---|
| S1 | read both fixtures in full | every EN line has a JA counterpart or an explicit omission note |
| S2 | classify each divergence | overclaim / numeric drift / stage drift / omission, quoting both sides |
| S3 | write the artifact | eight required headings, exactly one findings table, six mandated keys verbatim |
| S4 | mechanically verify the written artifact | heading presence, single findings header, key rows, verdict vocabulary |

## Execution

Both fixtures are short enough to quote in full for auditability.

`announcement.en.md` (13 lines, source of truth):

```
# Release announcement — draft (English, source of truth)

We are announcing the public beta of our task runner.

- In one internal pilot, median latency improved by 18%. This is a single
  controlled measurement, not a general performance claim.
- Interrupted runs can be resumed from persisted state. Resumption succeeded
  in our test scenarios; it is not guaranteed under every failure mode.
- The release is verified by 402 automated tests.
- Irreversible actions require manual approval before they run.
- It works with existing CLI workflows without changes.
- No telemetry is collected.
```

`announcement.ja.md` (9 lines, translation under test):

```
# リリース告知 — ドラフト（日本語訳・レビュー前）

タスクランナーの正式版を発表します。

- 社内パイロットにおいて、中央値レイテンシを 18%以上改善しました。
- 実行が中断しても、必ず自動で復旧します。
- 本リリースは 500以上の自動テストで検証されています。
- 既存の CLI ワークフローにそのまま組み込めます。
- テレメトリーは収集しません。
```

Structural observation: the English body carries six bullets, the Japanese
body carries five. The missing bullet is the manual-approval sentence
(`- Irreversible actions require manual approval before they run.`), which has
no counterpart anywhere in `announcement.ja.md`.

### Claim-by-claim parity table

| # | EN (source of truth, quoted) | JA (quoted) | classification | verdict |
|---|---|---|---|---|
| C1 | `We are announcing the public beta of our task runner.` | `タスクランナーの正式版を発表します。` | stage drift — `public beta` (pre-GA) is presented as `正式版` (general availability / official release) | drift |
| C2 | `In one internal pilot, median latency improved by 18%. This is a single controlled measurement, not a general performance claim.` | `社内パイロットにおいて、中央値レイテンシを 18%以上改善しました。` | overclaim + numeric drift — a point estimate `18%` becomes the lower bound `18%以上` ("18% or more"), and the scoping caveat (`a single controlled measurement, not a general performance claim`) is dropped | drift |
| C3 | `Interrupted runs can be resumed from persisted state. Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.` | `実行が中断しても、必ず自動で復旧します。` | overclaim — a conditional capability with an explicit non-guarantee becomes an unconditional guarantee (`必ず` = always) plus an unsupported automation claim (`自動で`) | drift |
| C4 | `The release is verified by 402 automated tests.` | `本リリースは 500以上の自動テストで検証されています。` | numeric drift — `402` becomes `500以上` ("500 or more"), which is both a different number and a stronger (unbounded-below-at-500) form | drift |
| C5 | `Irreversible actions require manual approval before they run.` | (no counterpart line in `announcement.ja.md`) | omission — a safety-relevant control statement about irreversible actions is absent | drift |
| C6 | `It works with existing CLI workflows without changes.` | `既存の CLI ワークフローにそのまま組み込めます。` | reworded but equivalent — `そのまま` carries the same "without changes" condition; no strengthening, no numbers | no-finding |
| C7 | `No telemetry is collected.` | `テレメトリーは収集しません。` | literal equivalent negative claim | no-finding |
| C8 | heading `# Release announcement — draft (English, source of truth)` | heading `# リリース告知 — ドラフト（日本語訳・レビュー前）` | reworded but equivalent — both mark the document as a draft; the JA additions (`日本語訳`, `レビュー前`) are weaker/self-descriptive, not stronger | no-finding |

### Confirmed findings (with both-side quotes)

1. **Stage drift — release stage.** EN: `We are announcing the public beta of
   our task runner.` JA: `タスクランナーの正式版を発表します。` `正式版` is the
   standard Japanese term for a general-availability/official release, which is
   a later stage than `public beta`. This changes the maturity promise made to
   readers.
2. **Overclaim + numeric drift — latency.** EN: `median latency improved by
   18%` plus `This is a single controlled measurement, not a general
   performance claim.` JA: `中央値レイテンシを 18%以上改善しました。` The
   suffix `以上` converts an exact measured value into a floor, and the entire
   scoping caveat sentence has no JA counterpart.
3. **Overclaim — resume behaviour.** EN: `Interrupted runs can be resumed from
   persisted state.` / `it is not guaranteed under every failure mode`. JA:
   `実行が中断しても、必ず自動で復旧します。` `必ず` asserts an unconditional
   guarantee that the EN evidence explicitly denies, and `自動で` asserts
   automatic recovery that the EN text does not state.
4. **Numeric drift — automated test count.** EN: `The release is verified by
   402 automated tests.` JA: `本リリースは 500以上の自動テストで検証されていま
   す。` `500以上` is neither equal to nor implied by `402`; it is a higher and
   open-ended figure.
5. **Omission — manual approval for irreversible actions.** EN: `Irreversible
   actions require manual approval before they run.` JA: no corresponding
   sentence exists in the file (the JA body has five bullets against the EN
   six). This is the safety-relevant omission of a control statement.

### Rejected candidates (looked suspicious, not findings)

- **`既存の CLI ワークフローにそのまま組み込めます。` vs `It works with
  existing CLI workflows without changes.`** Suspicious because `組み込めます`
  ("can be integrated") is a different verb from `works with`, and the JA
  sentence reads more actively. Rejected: `そのまま` preserves the "without
  changes" condition exactly, and neither side attaches a number, guarantee
  adverb, or scope expansion. The potential form `組み込めます` is the idiomatic
  Japanese register for feature capability assertions and carries the same
  factual weight as the English indicative `works with`; it neither weakens nor
  strengthens the claim. This is a rewording, not a strengthening.
- **`テレメトリーは収集しません。` vs `No telemetry is collected.`**
  Suspicious because the JA uses an active-voice negation where the EN uses a
  passive one, and blanket privacy statements are a common drift site.
  Rejected: both assert the identical absolute negative with no qualifier on
  either side; voice alone changes nothing about the claim's strength.
- **Heading `ドラフト（日本語訳・レビュー前）` vs `draft (English, source of
  truth)`.** Suspicious because the parenthetical text differs substantially.
  Rejected: both label the document a draft; the JA parenthetical describes the
  file's own status (`日本語訳`, `レビュー前` = pre-review) and is weaker, not
  stronger, than the EN label. A translation is not expected to reproduce the
  "source of truth" self-designation.
- **`社内パイロットにおいて` vs `In one internal pilot`.** Suspicious because
  the JA drops the numeral `one`, which could read as "internal pilots" in
  general. Rejected as a standalone finding: Japanese does not mark number
  here, and the singular `パイロット` framing is not a strengthening on its
  own. The genuine drift in this bullet is `18%以上` and the dropped caveat,
  which are reported as finding 2 rather than double-counted here.

## Review

Scored review iteration 1 ran three independent reviewers in parallel
(A = evidence fidelity, B = contract/validator compliance, C = adversarial
false-positive/false-negative hunting), reviewer window
2026-08-21T03:06:10Z..2026-08-21T03:09:15Z. Their raw `mission-review/1`
payloads are not transcribed here (output-compression discipline); the exact
stored paths are:

- A: `.mission-state/archive/iter-1-a6df3a90-review-input-bce81a61ca070681.json`
- B: `.mission-state/archive/iter-1-a6df3a90-review-input-bb845834b5529021.json`
- C: `.mission-state/archive/iter-1-a6df3a90-review-input-1a75b2b98bf9b5c1.json`
- aggregate: `.mission-state/archive/iter-1-a6df3a90-reviews-c3d554e957d1e64f.json`
- scoring: `.mission-state/archive/iter-1-a6df3a90-scoring-07d7955aa4c3d8e8.json`

Outcome: zero High and zero Medium findings; two Low findings (A-1: the Review
section originally asserted reviewer bookkeeping without citing the concrete
archive paths — fixed above; C-1: the `cli_workflow_claim` rejection did not
address Japanese potential-form modality — fixed in the rejected-candidates
section). No reviewer contested any `drift` or `no-finding` verdict.

Mechanical verification executed against the written artifact before review
(recorded via `mission-state.py verification record --iteration 1`):

| check | result (executed, not asserted) |
|---|---|
| all eight required headings present | pass — 8/8 matched |
| exactly one `\| location \| key \| expected \| actual \| verdict \|` header line | pass — count = 1 |
| all six mandated `location`/`key` strings present verbatim | pass — 6/6; 7 rows total (extra row: `release_stage_claim`) |
| every findings verdict is exactly `drift` or `no-finding` | pass — 7/7 in vocabulary |
| EN bullet count vs JA bullet count | pass — EN = 6, JA = 5; JA contains neither `承認` nor `手動` |
| every quoted string exists verbatim in its fixture | pass — `402`, `public beta`, `Irreversible actions require manual approval` in EN; `500以上`, `18%以上`, `必ず自動で復旧`, `正式版` in JA |

Recorded as `mission-verification/1` at 2026-08-21T03:05:54Z with
`failed_count: 0`.

## Score

Composite score and per-axis values are produced by
`mission-state.py review-finalize` (aggregate-reviews → push-score) and are
recorded in the mission state; the gate values (`composite_score`,
`open_high`, `max_agreement_delta`, `findings_evidence_path`) are quoted in
Evidence below rather than restated by hand.

## Stop Decision

Stop when the mission gate reports `passes: true` (findings evidence present,
`open_high == 0`, `max_agreement_delta <= 1.5`, composite `>= 4.0`, minimum
scored item `>= 3.5`) at iteration 1 or 2, or when `--max-iter 2` is reached.
The exact terminal values are the tool-computed ones quoted in Evidence.

## Evidence

### Machine-checkable findings block

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| announcement.ja.md | automated_test_count | `The release is verified by 402 automated tests.` (402) | `本リリースは 500以上の自動テストで検証されています。` (500以上) | drift |
| announcement.ja.md | cli_workflow_claim | `It works with existing CLI workflows without changes.` | `既存の CLI ワークフローにそのまま組み込めます。` | no-finding |
| announcement.ja.md | latency_improvement_claim | `median latency improved by 18%` + `This is a single controlled measurement, not a general performance claim.` | `中央値レイテンシを 18%以上改善しました。` (caveat sentence absent) | drift |
| announcement.ja.md | manual_approval_sentence_omitted | `Irreversible actions require manual approval before they run.` present in translation | sentence absent from `announcement.ja.md` (5 JA bullets vs 6 EN bullets) | drift |
| announcement.ja.md | release_stage_claim | `We are announcing the public beta of our task runner.` (public beta) | `タスクランナーの正式版を発表します。` (正式版 = GA) | drift |
| announcement.ja.md | resume_guarantee_claim | `Interrupted runs can be resumed from persisted state.` + `it is not guaranteed under every failure mode` | `実行が中断しても、必ず自動で復旧します。` | drift |
| announcement.ja.md | telemetry_claim | `No telemetry is collected.` | `テレメトリーは収集しません。` | no-finding |

### Fixture evidence

- Every quoted English string above is taken verbatim from
  `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.en.md`
  (13 lines, quoted in full under Execution).
- Every quoted Japanese string above is taken verbatim from
  `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.ja.md`
  (9 lines, quoted in full under Execution).
- Bullet-count evidence for the omission finding: EN body lines beginning with
  `- ` number six; JA body lines beginning with `- ` number five. No JA line
  contains `承認` or any approval-related wording.

### Mission-state evidence

- Session state: `.mission-state/sessions/cc-506cda3d-9994-42fe-a02f-93ad42f51771.json`
  (mission_id `a6df3a902223e466`).
- Canonical plan: `.mission-state/plans/930a434defe02b4a.json`
  (`sha256:930a434defe02b4a5f3b99028abf217e60504e03fd2c92366d18d3d2c04cbdad`).
- Verification, review payloads, aggregate and scoring artifacts:
  `.mission-state/archive/` (paths recorded in the session state).
- Gate values are whatever `mission-state.py closeout` / `next` report at
  terminal time; see the Stop Decision heading. This artifact does not assert
  gate values that the tool did not produce.

### Explicitly unmeasured

- Whether `402` or `500以上` reflects the true test count in any repository is
  **unmeasured**; the only source of truth used here is the English fixture.
- Actual runtime latency, resume behaviour, telemetry behaviour, and approval
  enforcement of any real system are **unmeasured**; this is a text-parity
  audit only.
- Translation quality aspects beyond claim parity (register, terminology
  consistency, readability) are **unmeasured** and out of scope.
- No comparison against any other benchmark arm was performed; no superiority
  claim is made.

## Assumptions

- **A1** The English draft is the source of truth. Any JA statement stronger
  than, numerically different from, or omitting a safety-relevant EN statement
  is reported as `drift`. Validation: restated in the task prompt and applied
  per row.
- **A2** Rows beyond the six mandated keys are permitted, since the prompt asks
  for "one row per item you evaluated" while mandating that those six exact
  `location`/`key` strings appear. `release_stage_claim` is the one extra row;
  it is asserted as `drift` on the strength of the `public beta` → `正式版`
  quote pair, and the task validator explicitly lists "stage drift" as a
  classification to be used.
- **A3** `18%以上` is read as "18% or more" and `500以上` as "500 or more"
  (standard Japanese usage of `以上`, inclusive lower bound). Under this
  reading both are strengthenings/changes relative to the exact EN figures.
- **A4** The mission skill's local-authoring sync step
  (`mission-local-authoring-sync.sh`) was **not run**, because the benchmark
  rules forbid network access and that script performs a remote sync. The
  repository-root `scripts/mission-state.py` was used instead. This deviation
  is recorded rather than silently skipped.
- **A5** No files outside the two named fixtures, this artifact, and
  `.mission-state/` were opened, listed, or searched under
  `benchmarks/mission-vs-goal/`.
