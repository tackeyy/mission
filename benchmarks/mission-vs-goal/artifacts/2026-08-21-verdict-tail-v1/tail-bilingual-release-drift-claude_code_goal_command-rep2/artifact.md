# tail-bilingual-release-drift — claude_code_goal_command (rep2)

## Goal

Produce this single task artifact at
`benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-bilingual-release-drift-claude_code_goal_command-rep2.md`,
containing a claim-by-claim parity comparison between
`benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.en.md`
(source of truth) and
`benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.ja.md`,
classifying every divergence (overclaim / numeric drift / stage drift / omission),
separating confirmed findings from rejected candidates, and emitting the required
machine-checkable findings table.

## Result

Seven claims were adjudicated. Five are drift, two are compliant.

Confirmed findings (drift):

1. **Release stage — stage drift.** EN announces a `public beta`; JA announces
   `正式版` (general availability / official release). JA promotes the release
   stage beyond the English evidence.
2. **Latency — overclaim + numeric drift + omitted caveat.** EN: `median latency
   improved by 18%` in `one internal pilot`, explicitly qualified as `a single
   controlled measurement, not a general performance claim`. JA: `18%以上改善`
   ("improved by 18% or more"), turning an exact point measurement into an
   open-ended lower bound, and dropping the qualifier sentence entirely.
3. **Resume — overclaim + omitted caveat.** EN: `Interrupted runs can be resumed
   from persisted state. Resumption succeeded in our test scenarios; it is not
   guaranteed under every failure mode.` JA: `実行が中断しても、必ず自動で復旧します。`
   ("even if a run is interrupted, it will always recover automatically"). JA
   asserts an unconditional guarantee that the English text explicitly denies,
   and adds "automatically", which the English never claims.
4. **Automated test count — numeric drift.** EN: `402 automated tests`. JA:
   `500以上の自動テスト` ("500 or more automated tests"). The number is both
   different and inflated.
5. **Manual approval — omission (safety-relevant).** EN: `Irreversible actions
   require manual approval before they run.` There is no corresponding sentence
   anywhere in the JA file. This is the safety-relevant statement whose loss most
   changes reader behaviour around irreversible actions.

Compliant (no-finding): the CLI-workflow claim and the telemetry claim. Both are
reworded but semantically equivalent — see Rejected candidates.

## Evidence

### Claim-by-claim parity table

| # | EN (source of truth) | JA | Classification |
|---|---|---|---|
| 1 | `We are announcing the public beta of our task runner.` | `タスクランナーの正式版を発表します。` | stage drift (beta → GA) |
| 2 | `In one internal pilot, median latency improved by 18%. This is a single controlled measurement, not a general performance claim.` | `社内パイロットにおいて、中央値レイテンシを 18%以上改善しました。` | numeric drift (`18%` → `18%以上`) + overclaim + omission of the scoping caveat |
| 3 | `Interrupted runs can be resumed from persisted state. Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.` | `実行が中断しても、必ず自動で復旧します。` | overclaim (`not guaranteed under every failure mode` → `必ず`) + omission of the caveat |
| 4 | `The release is verified by 402 automated tests.` | `本リリースは 500以上の自動テストで検証されています。` | numeric drift (`402` → `500以上`) |
| 5 | `Irreversible actions require manual approval before they run.` | *(no corresponding sentence in `announcement.ja.md`)* | omission (safety-relevant) |
| 6 | `It works with existing CLI workflows without changes.` | `既存の CLI ワークフローにそのまま組み込めます。` | none — reworded, equivalent |
| 7 | `No telemetry is collected.` | `テレメトリーは収集しません。` | none — near-literal, equivalent |

### Rejected candidates (reworded but equivalent)

- **`cli_workflow_claim`.** JA `既存の CLI ワークフローにそのまま組み込めます。`
  looked suspicious because `そのまま組み込めます` ("can be dropped in as-is")
  is phrased as an affirmative capability, whereas EN `without changes` is
  phrased as an absence of required work. On inspection the propositional
  content is identical: `そのまま` ("as-is") is exactly the translation of
  `without changes`, and neither side attaches a scope, a caveat, or a number.
  No strengthening, no numeric content, no dropped safety statement. Not a
  finding.
- **`telemetry_claim`.** JA `テレメトリーは収集しません。` looked worth checking
  because a privacy statement is the kind of claim where a translation could
  silently narrow or widen scope (e.g. "no telemetry *by default*", or adding an
  opt-in clause). Neither side adds a qualifier: EN `No telemetry is collected.`
  is an unqualified negative and the JA sentence is an unqualified negative with
  the same subject. Equivalent. Not a finding.
- **Document title / status line.** EN heading `# Release announcement — draft
  (English, source of truth)` vs JA `# リリース告知 — ドラフト（日本語訳・レビュー前）`.
  This looked like a divergence because the parenthetical differs, but both mark
  the document as a draft; the parentheticals are document-role metadata
  (`source of truth` vs `日本語訳・レビュー前`), not product claims about the
  release. Correctly describing the JA file as a pre-review translation is not a
  drift in the release evidence. Not a finding. (Note: this is distinct from
  item 1 above — the *body* sentence `正式版` is a real stage drift and is
  reported as such.)

### Findings block

| location | key | expected | actual | verdict |
|---|---|---|---|---|
| announcement.ja.md | automated_test_count | `402 automated tests` | `500以上の自動テスト` | drift |
| announcement.ja.md | cli_workflow_claim | `It works with existing CLI workflows without changes.` | `既存の CLI ワークフローにそのまま組み込めます。` (equivalent) | no-finding |
| announcement.ja.md | latency_improvement_claim | `median latency improved by 18%` in `one internal pilot`, qualified as `a single controlled measurement, not a general performance claim` | `中央値レイテンシを 18%以上改善しました` with the qualifier sentence absent | drift |
| announcement.ja.md | manual_approval_sentence_omitted | `Irreversible actions require manual approval before they run.` present | no corresponding sentence in the JA file | drift |
| announcement.ja.md | resume_guarantee_claim | `Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.` | `実行が中断しても、必ず自動で復旧します。` | drift |
| announcement.ja.md | telemetry_claim | `No telemetry is collected.` | `テレメトリーは収集しません。` (equivalent) | no-finding |
| announcement.ja.md | release_stage_claim | `the public beta of our task runner` | `タスクランナーの正式版` | drift |

The `release_stage_claim` row is additional to the six items named in the task
prompt; it is included because the validator requires stage drift to be
classified and this is the only stage-level divergence in the fixture.

## Assumptions

- "Source of truth" means the English file's claims bound the Japanese file:
  a JA claim that is stronger, numerically different, or drops a safety-relevant
  English statement is drift; a JA claim that is weaker or merely reworded is not
  reported as drift here (no weaker-than-English claim was observed).
- `18%以上` is read as "18% or more" (a lower bound) rather than as a rendering of
  the exact value `18%`; `500以上` is read as "500 or more". Both are therefore
  treated as diverging from the exact English figures.
- The omission check for the manual-approval sentence was performed by reading the
  whole JA file (9 lines) rather than a keyword search, so "absent" means absent
  from the entire file, not merely from the matching bullet position.
- Only the two named fixture files and this output file were opened; no other
  path under `benchmarks/mission-vs-goal/` was read, listed, or searched.

### Unmeasured

- Whether these divergences reflect an intentional editorial decision, a
  translation error, or a newer JA-only data source is **unmeasured** — no
  changelog, issue, review history, or authoring metadata was consulted.
- The factual correctness of the English claims themselves (e.g. whether 402 tests
  actually exist, whether latency really improved 18%) is **unmeasured**; the
  English file was accepted as the source of truth by task definition.
- No runtime, build, or test execution was performed; this artifact is a
  text-comparison result only.
- No comparison against any answer key or scoring configuration was made, and no
  claim about benchmark performance or arm superiority is made here.

## Stop Condition

Met. This artifact exists at the required path and contains the headings Goal,
Result, Evidence, Assumptions, and Stop Condition; it contains the claim-by-claim
parity table quoting both languages with each divergence classified, a
rejected-candidates section for reworded-but-equivalent claims, and exactly one
markdown table using the header `| location | key | expected | actual | verdict |`
with one row per adjudicated item and verdicts restricted to `drift` /
`no-finding`. No files outside this output path were created or modified; nothing
was committed, pushed, installed, or fetched over the network.
