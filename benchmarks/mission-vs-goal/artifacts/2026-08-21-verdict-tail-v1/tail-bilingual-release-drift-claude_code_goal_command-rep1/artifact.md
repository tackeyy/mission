# tail-bilingual-release-drift — claude_code_goal_command — rep1

## Goal

Compare `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.en.md`
(source of truth) against `announcement.ja.md` claim by claim, and report every place
where the Japanese copy is **stronger than**, **numerically different from**, **at a
different release stage than**, or **missing a safety-relevant statement of** the English
evidence — quoting both sides. Reworded-but-equivalent claims must be explicitly rejected
as non-findings with reasoning.

## Result

Both fixture files were read in full (EN: 13 lines, JA: 9 lines). Seven claims were
adjudicated. **Five are drift; two are compliant.**

Confirmed drift:

1. **Release stage** — EN `public beta`, JA `正式版` (general availability). Classification: **stage drift**.
2. **Latency** — EN `improved by 18%` + an explicit scope caveat; JA `18%以上改善` ("18% or more") with the caveat deleted. Classification: **overclaim** (numeric bound loosened upward + safety-relevant caveat omitted).
3. **Resume** — EN `can be resumed` / `not guaranteed under every failure mode`; JA `必ず自動で復旧します` ("always recovers automatically"). Classification: **overclaim** (a hedged capability became an absolute guarantee).
4. **Automated tests** — EN `402`, JA `500以上`. Classification: **numeric drift**.
5. **Manual approval for irreversible actions** — present in EN, absent from JA entirely. Classification: **omission** (safety-relevant).

Compliant (no finding): the CLI-workflow claim and the telemetry claim.

Per the run rules, this artifact makes no claim about the relative merit of any benchmark
arm; it only reports the fixture comparison.

## Evidence

### Claim-by-claim parity table

| # | Claim | English (source of truth) | Japanese | Classification |
|---|---|---|---|---|
| 1 | Release stage | `We are announcing the public beta of our task runner.` | `タスクランナーの正式版を発表します。` | **stage drift** — beta → GA/正式版 |
| 2 | Latency improvement | `In one internal pilot, median latency improved by 18%. This is a single controlled measurement, not a general performance claim.` | `社内パイロットにおいて、中央値レイテンシを 18%以上改善しました。` | **overclaim** — `18%` → `18%以上`; caveat sentence deleted |
| 3 | Resume behaviour | `Interrupted runs can be resumed from persisted state. Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.` | `実行が中断しても、必ず自動で復旧します。` | **overclaim** — conditional capability → absolute guarantee (`必ず`), and `自動` (automatic) is not asserted in EN |
| 4 | Automated test count | `The release is verified by 402 automated tests.` | `本リリースは 500以上の自動テストで検証されています。` | **numeric drift** — `402` → `500以上` |
| 5 | Manual approval gate | `Irreversible actions require manual approval before they run.` | *(no corresponding sentence in `announcement.ja.md`)* | **omission** — safety-relevant statement dropped |
| 6 | CLI compatibility | `It works with existing CLI workflows without changes.` | `既存の CLI ワークフローにそのまま組み込めます。` | none — equivalent (see rejected candidates) |
| 7 | Telemetry | `No telemetry is collected.` | `テレメトリーは収集しません。` | none — equivalent (see rejected candidates) |

### Exact quoted identifiers/values per confirmed finding

- Stage: EN literal `public beta`; JA literal `正式版`. 正式版 denotes a general-availability
  release in Japanese product usage, which is a later stage than public beta.
- Latency: EN literal `18%`; JA literal `18%以上`. `以上` means "or more", turning a point
  estimate into an open-ended lower bound. The EN sentence `This is a single controlled
  measurement, not a general performance claim.` has no counterpart anywhere in the JA file.
- Resume: EN literal `it is not guaranteed under every failure mode`; JA literal
  `必ず自動で復旧します`. `必ず` = "always/without fail" — the direct negation of the EN hedge.
- Test count: EN literal `402`; JA literal `500以上`. Both the magnitude and the form differ
  (exact count vs. open-ended lower bound), and `500以上` is not supported by `402`.
- Manual approval: EN literal `Irreversible actions require manual approval before they run.`
  The JA file's five bullets cover latency, resume, test count, CLI workflows, and telemetry;
  none mentions 承認 / 手動 / 不可逆. The statement is absent, not reworded.

### Rejected candidates (looked suspicious, but are not findings)

- **CLI compatibility (`cli_workflow_claim`).** JA `そのまま組み込めます` ("can be incorporated
  as-is") initially reads as stronger than EN `works with ... without changes`, because
  「組み込める」 (can be embedded/integrated) is a different verb from "works with". On
  inspection the semantic content is identical: both assert compatibility with *existing*
  CLI workflows conditioned on *no modification* (`without changes` ≒ `そのまま`). No new
  capability, no numeric change, no dropped caveat — the EN sentence carries no caveat to
  drop. Rejected as reworded-but-equivalent.
- **Telemetry (`telemetry_claim`).** The EN sentence is passive (`No telemetry is collected.`)
  while the JA is active (`テレメトリーは収集しません。`), and a voice change around a privacy
  claim is worth checking, since active voice could narrow the actor ("*we* don't collect,
  but a third party might"). In context both are unqualified blanket denials of telemetry
  collection with the same scope, and the JA adds no exception or qualifier. Rejected as
  reworded-but-equivalent.
- **Bullet count 6 vs 5.** The EN file has six bullets and the JA five, which looks like it
  might indicate more than one omission. Mapping them one-to-one shows exactly one missing
  bullet (manual approval); the other five map cleanly. This is not a separate finding — it
  is the same omission already recorded as row 5, so it is not double-counted.
- **`ドラフト（日本語訳・レビュー前）` vs `draft (English, source of truth)`.** The heading
  qualifiers differ, but this is document metadata describing the translation's own status,
  not a product claim about the release. Not treated as a divergence in the product copy.

### Machine-checkable findings block

| location | key | expected | actual | verdict |
| --- | --- | --- | --- | --- |
| announcement.ja.md | automated_test_count | 402 automated tests (`verified by 402 automated tests`) | `500以上の自動テストで検証されています` | drift |
| announcement.ja.md | cli_workflow_claim | works with existing CLI workflows without changes | `既存の CLI ワークフローにそのまま組み込めます` | no-finding |
| announcement.ja.md | latency_improvement_claim | median latency improved by 18% in one internal pilot, explicitly not a general performance claim | `中央値レイテンシを 18%以上改善しました` with the "single controlled measurement" caveat omitted | drift |
| announcement.ja.md | manual_approval_sentence_omitted | `Irreversible actions require manual approval before they run.` present | sentence absent from the Japanese copy | drift |
| announcement.ja.md | resume_guarantee_claim | interrupted runs can be resumed from persisted state; not guaranteed under every failure mode | `実行が中断しても、必ず自動で復旧します` | drift |
| announcement.ja.md | telemetry_claim | no telemetry is collected | `テレメトリーは収集しません` | no-finding |
| announcement.ja.md | release_stage_claim | public beta | `正式版` (general availability) | drift |

## Assumptions

- The two fixture files named in the prompt are the complete comparison scope. No other
  file under `benchmarks/mission-vs-goal/` was opened, listed, or searched, other than
  writing this output file.
- `正式版` is read as general availability / final release, a stage later than `public beta`.
  This is a lexical judgement about ordinary Japanese product terminology, not a measurement.
- `18%以上` is read as "18% or more" and `500以上` as "500 or more"; `必ず` as
  "always / without fail". Same basis as above.
- The extra row `release_stage_claim` is included beyond the six mandated items because the
  task validator requires stage drift to be classifiable; it uses the same `announcement.ja.md`
  location string. The six mandated keys appear verbatim and are unaffected by it.
- **Unmeasured:** nothing was executed, benchmarked, or verified against any running system.
  No claim is made about whether the English draft's own numbers (18%, 402 tests) are true —
  only that the Japanese copy diverges from them. Whether the divergences were intentional
  editorial choices or translation errors is also unmeasured; only the textual divergence
  is observed.

## Stop Condition

Met. This artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-bilingual-release-drift-claude_code_goal_command-rep1.md`
and contains the headings Goal, Result, Evidence, Assumptions, and Stop Condition; a
claim-by-claim parity table quoting both languages with each divergence classified
(overclaim / numeric drift / stage drift / omission); a rejected-candidates section for
reworded-but-equivalent claims; and exactly one findings table with the header
`| location | key | expected | actual | verdict |` containing one row per adjudicated item.
No commits, pushes, installs, or network access were performed, and no file outside this
artifact was modified.
