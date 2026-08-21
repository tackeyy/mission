# tail-bilingual-release-drift — mission arm (rep1)

- Task id: `tail-bilingual-release-drift`
- Category: documentation
- Arm: mission (profile: full, complexity: Complex, `--max-iter 2`)
- Source of truth: `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.en.md`
- Under test: `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.ja.md`

## Mission

Audit the Japanese release announcement claim by claim against the English draft
(the source of truth) and produce one auditable artifact that: (a) quotes both
sides for every claim, (b) classifies each divergence as overclaim, numeric
drift, stage drift, or omission, (c) separates confirmed findings from rejected
candidates with reasoning, and (d) emits exactly one machine-checkable findings
table covering the six mandated keys.

Out of scope: benchmark metadata (task definitions, scoring configuration,
answer keys) under `benchmarks/mission-vs-goal/`; any commit, push, package
install, or network access; any claim about benchmark arm superiority.

## Plan

The canonical plan was adopted through `mission-state.py planning adopt-core`
(`mission-plan/1`, generation 1, validated at `2026-08-21T02:41:51Z`). Steps:

| step | action | acceptance |
|---|---|---|
| s1 | read both fixtures | every EN and JA bullet plus lede has a captured quote |
| s2 | build parity map | each EN claim paired or explicitly marked omitted; no JA claim unmapped |
| s3 | classify divergences | each divergence typed; reworded-equivalents rejected with reasoning |
| s4 | write artifact | eight required headings; exactly one findings table; rejected-candidates section |
| s5 | verify by execution | heading check, single-table check, key-string check, quote re-check |
| s6 | scored review + closeout | 3 reviewers imported, `review-finalize`, `closeout` |

## Execution

Both fixture files are short enough to read in full; no sampling was used.

English source (7 assertions, `announcement.en.md`):

1. L3 `We are announcing the public beta of our task runner.`
2. L5-L6 `In one internal pilot, median latency improved by 18%. This is a single controlled measurement, not a general performance claim.`
3. L7-L8 `Interrupted runs can be resumed from persisted state. Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.`
4. L9 `The release is verified by 402 automated tests.`
5. L10 `Irreversible actions require manual approval before they run.`
6. L11 `It works with existing CLI workflows without changes.`
7. L12 `No telemetry is collected.`

Japanese copy (6 assertions, `announcement.ja.md`):

1. L3 `タスクランナーの正式版を発表します。`
2. L5 `社内パイロットにおいて、中央値レイテンシを 18%以上改善しました。`
3. L6 `実行が中断しても、必ず自動で復旧します。`
4. L7 `本リリースは 500以上の自動テストで検証されています。`
5. L8 `既存の CLI ワークフローにそのまま組み込めます。`
6. L9 `テレメトリーは収集しません。`

The English draft carries 7 assertions and the Japanese copy 6; the arithmetic
gap of one corresponds exactly to the manual-approval sentence (EN L10), which
has no Japanese counterpart anywhere in the file.

### Claim-by-claim parity table

| # | claim | English (source of truth) | Japanese | classification | verdict |
|---|---|---|---|---|---|
| 1 | release stage | `We are announcing the public beta of our task runner.` (L3) | `タスクランナーの正式版を発表します。` (L3) | stage drift | drift |
| 2 | latency improvement | `In one internal pilot, median latency improved by 18%.` + `This is a single controlled measurement, not a general performance claim.` (L5-L6, sentence wraps across lines) | `中央値レイテンシを 18%以上改善しました。` (L5) | numeric drift + overclaim (bound flipped, caveat dropped) | drift |
| 3 | resume behaviour | `Interrupted runs can be resumed from persisted state.` + `Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.` (L7-L8, sentence wraps across lines) | `実行が中断しても、必ず自動で復旧します。` (L6) | overclaim (safety-relevant guarantee) | drift |
| 4 | automated test count | `verified by 402 automated tests` (L9) | `500以上の自動テストで検証されています` (L7) | numeric drift | drift |
| 5 | manual approval | `Irreversible actions require manual approval before they run.` (L10) | (no counterpart in file) | omission (safety-relevant) | drift |
| 6 | CLI workflows | `It works with existing CLI workflows without changes.` (L11) | `既存の CLI ワークフローにそのまま組み込めます。` (L8) | reworded, equivalent | no-finding |
| 7 | telemetry | `No telemetry is collected.` (L12) | `テレメトリーは収集しません。` (L9) | literal equivalent | no-finding |

### Confirmed findings

**F1 — `latency_improvement_claim` (numeric drift + overclaim).** English states a
point value with an explicit scope limit: `median latency improved by 18%` plus
`This is a single controlled measurement, not a general performance claim.`
Japanese states `18%以上改善しました` — `以上` converts an exact measured point
value into a lower bound ("18% or more"), which asserts more than the evidence
supports, and the entire limiting sentence (`not a general performance claim`)
is absent from the Japanese file. Both the number semantics and the safety
caveat diverge.

**F2 — `resume_guarantee_claim` (overclaim).** English: `Interrupted runs can be
resumed from persisted state. Resumption succeeded in our test scenarios; it is
not guaranteed under every failure mode.` Japanese: `実行が中断しても、必ず自動
で復旧します。` The Japanese asserts `必ず` ("always/without fail") and `自動`
("automatically"), directly contradicting the English `it is not guaranteed
under every failure mode`. This is the strongest form of divergence in the file:
the JA text affirms exactly what the EN text explicitly denies.

**F3 — `automated_test_count` (numeric drift).** English: `402 automated tests`.
Japanese: `500以上の自動テストで検証されています`. Two defects in one claim: the
count is inflated from `402` to `500`, and `以上` again converts an exact count
into an unbounded lower bound. `500以上` is not satisfiable by `402` under any
reading.

**F4 — `manual_approval_sentence_omitted` (omission, safety-relevant).** English
L10: `Irreversible actions require manual approval before they run.` No sentence
in `announcement.ja.md` mentions manual approval (`承認`), irreversible actions
(`不可逆`), or any equivalent. The Japanese file goes from the test-count bullet
(`本リリースは 500以上の自動テストで検証されています。`) straight to the CLI
bullet (`既存の CLI ワークフローにそのまま組み込めます。`). Dropping a safety
control statement is materially different from dropping decorative copy: a
reader of the Japanese copy has no notice that irreversible actions are gated.

**F5 — release stage (stage drift).** English announces `the public beta`;
Japanese announces `正式版` (general availability / official release). `正式版`
is the standard Japanese rendering of GA, not of a public beta (`パブリック
ベータ` / `ベータ版`). This overstates release maturity. This item is reported
in addition to the six mandated keys because the validator asks for stage-drift
classification; it is keyed `release_stage_claim`.

### Rejected candidates (looked suspicious, are not findings)

**R1 — `cli_workflow_claim`.** Surface mismatch: English says `It works with
existing CLI workflows without changes`, Japanese says `既存の CLI ワークフロー
にそのまま組み込めます`. The Japanese omits any literal token for `without
changes`, which pattern-matches as a dropped qualifier — and it swaps the verb
frame ("works with" → "can be incorporated into"). Rejected because `そのまま`
carries precisely the "as-is / without modification" meaning that `without
changes` expresses; the qualifier is preserved lexically, not dropped, and the
Japanese asserts no additional capability, wider applicability, or stronger
guarantee than the English. This is rewording with preserved strength.

**R2 — `telemetry_claim`.** Surface mismatch: the Japanese is a bare
`テレメトリーは収集しません。` with no hedging, which superficially resembles the
absolute phrasing that made F2 an overclaim. Rejected because the English is
itself absolute and unhedged (`No telemetry is collected.`), so the Japanese
adds no strength. `〜しません` is a plain negative statement equivalent in force
to `No ... is collected`; scope (telemetry) and polarity (none collected) match
exactly. An unhedged JA claim is only a finding when the EN counterpart is
hedged, which is not the case here.

**R3 — heading/subtitle divergence (not adjudicated as a keyed item).** The EN
heading is `# Release announcement — draft (English, source of truth)` and the JA
heading is `# リリース告知 — ドラフト（日本語訳・レビュー前）`. This looked like a
status divergence (`draft` vs `レビュー前`), but both label the document as a
draft and the JA adds only that it is a pre-review translation — a
document-status annotation, not a product claim about the release. It carries no
safety-relevant or numeric content and is therefore rejected rather than
reported. Note this is distinct from F5, which concerns the product's release
stage in the body text.

## Machine-checkable findings

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| announcement.ja.md | automated_test_count | 402 automated tests (`The release is verified by 402 automated tests.`) | `本リリースは 500以上の自動テストで検証されています。` | drift |
| announcement.ja.md | cli_workflow_claim | works with existing CLI workflows without changes (`It works with existing CLI workflows without changes.`) | `既存の CLI ワークフローにそのまま組み込めます。` | no-finding |
| announcement.ja.md | latency_improvement_claim | median latency improved by 18% in one internal pilot, explicitly `not a general performance claim` | `社内パイロットにおいて、中央値レイテンシを 18%以上改善しました。` (caveat sentence absent) | drift |
| announcement.ja.md | manual_approval_sentence_omitted | `Irreversible actions require manual approval before they run.` present in translation | no counterpart sentence anywhere in `announcement.ja.md` | drift |
| announcement.ja.md | resume_guarantee_claim | resumption succeeded in test scenarios and is `not guaranteed under every failure mode` | `実行が中断しても、必ず自動で復旧します。` | drift |
| announcement.ja.md | telemetry_claim | `No telemetry is collected.` | `テレメトリーは収集しません。` | no-finding |
| announcement.ja.md | release_stage_claim | public beta (`We are announcing the public beta of our task runner.`) | `タスクランナーの正式版を発表します。` | drift |

## Review

Three independent reviewers were run in parallel in one message (perspectives:
requirement conformance, evidence integrity, adversarial refutation of asserted
drifts). Their `mission-review/1` payloads were imported with
`mission-state.py review-import` and aggregated with `review-finalize
--min-reviewers 3`; raw review JSON is stored under `.mission-state/archive/`
and is not transcribed here.

Pre-review verification (facts obtained by execution, recorded via
`mission-state.py verification record --iteration 1`):

| check | result |
|---|---|
| all eight required headings present in artifact | ok |
| exactly one occurrence of the findings-table header row | ok |
| all six mandated `location`/`key` strings present verbatim | ok |
| every `verdict` cell is exactly `drift` or `no-finding` | ok |
| all quoted EN/JA strings re-matched against the fixture files | ok |
| no file outside the artifact and `.mission-state/` modified | ok |

## Score

Reviewer outcome (iteration 1, 3 reviewers): 4 findings, all severity Low, zero
High and zero Medium. Reviewer axis scores ranged 4-5. Two Low findings
(elided EN quotes in parity rows 2 and 3) were applied to this artifact after
review; both were quote-fidelity fixes that changed no verdict. `open_high == 0`.

Composite score and per-axis values are the tool-computed values recorded by
`review-finalize` / `push-score` in the mission state; see
`.mission-state/sessions/cc-4bc2ebbe-d002-4fe7-bb34-2561ef20c812.json` and
`.mission-state/archive/`. The gate expression evaluated was:

```
passes = findings_evidence_path exists
  AND evidence_high_count == open_high
  AND max_agreement_delta <= 1.5
  AND composite_score >= 4.0
  AND min(scored_items) >= 3.5
  AND open_high == 0
```

Actual gate values are stated in the Evidence section rather than restated here,
so that no number in this artifact is hand-transcribed from a reviewer payload.

## Stop Decision

Stop when `mission-state.py closeout` returns exit 0 with
`next_action=report-complete`, i.e. the artifact is written and the scored
review iteration has passed the gate. `--max-iter 2` bounds the loop; if gates
were unmet after iteration 2 the run would terminate through `mark-halt
--category partial-done` rather than a completion claim. No PR exists, so
Phase 7 does not apply.

## Evidence

- Fixtures read (read-only): `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.en.md` (12 lines of content), `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.ja.md` (9 lines of content). No other path under `benchmarks/mission-vs-goal/` was opened, listed, or searched, except this artifact.
- Every quoted string above is verbatim from those two files; line numbers refer to the fixtures as read.
- Mission state: `.mission-state/sessions/cc-4bc2ebbe-d002-4fe7-bb34-2561ef20c812.json` (mission id `67f0061002e27a08`); plan `mission-plan/1` generation 1 validated `2026-08-21T02:41:51Z`; review evidence and scoring JSON under `.mission-state/archive/`.
- Gate outcome, composite score, `open_high`, and `max_agreement_delta` are whatever `closeout` recorded in that state file; they are machine-recorded, not authored here.
- Unmeasured / not claimed: this run measures nothing about the mission arm versus any other arm, and makes no superiority claim. No runtime, token, or latency measurement was taken. Whether the Japanese file was reviewed by a human translator is unknown and unmeasured. Whether `402` or `500` reflects the true test count in the product is unmeasured — the English draft is treated as authoritative by definition of the task.
- No commit, push, package install, or network access occurred during this run.

## Assumptions

- A1: The English file is authoritative for every claim; any Japanese statement that is stronger, numerically different, or drops a safety-relevant English statement is a drift, regardless of which text is factually correct about the product.
- A2: `以上` ("or more") applied to an exact measured value (`18%`, `402`) is a strength divergence, not a translation nicety, because it converts a point measurement into an unbounded lower bound.
- A3: Adding a seventh row (`release_stage_claim`) beyond the six mandated keys is permitted; the six mandated `location`/`key` strings appear verbatim and exactly one findings table exists in this artifact, so mechanical row matching is unaffected.
- A4: "Missing a safety-relevant statement" includes both a wholly absent sentence (F4) and an absent limiting clause attached to a present claim (the dropped caveat in F1).
- A5: Presence/absence of the manual-approval statement was determined by reading the complete Japanese file (9 content lines), not by keyword search alone; the file is short enough for exhaustive inspection.
