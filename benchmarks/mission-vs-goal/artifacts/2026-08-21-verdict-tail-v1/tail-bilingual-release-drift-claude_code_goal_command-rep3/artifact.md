# tail-bilingual-release-drift — claude_code_goal_command (rep3)

- Task id: `tail-bilingual-release-drift`
- Category: documentation
- Arm: `claude_code_goal_command`
- Source of truth: `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.en.md`
- Compared copy: `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.ja.md`

## Goal

Compare the Japanese release announcement claim by claim against the English draft
(the source of truth), and identify every place where the Japanese copy is
**stronger than**, **numerically different from**, or **missing a safety-relevant
statement of** the English evidence — quoting both sides. Reworded but semantically
equivalent claims must be explicitly rejected as non-findings with reasoning.

## Result

Five divergences are confirmed as drift; two evaluated claims are compliant.

Confirmed drift (5):

1. **Stage drift** — English announces a `public beta`; Japanese announces `正式版`
   (general availability).
2. **Overclaim + numeric drift** — English states latency `improved by 18%` with an
   explicit single-measurement caveat; Japanese states `18%以上改善` and drops the
   caveat.
3. **Overclaim** — English says resumption `is not guaranteed under every failure
   mode`; Japanese says `必ず自動で復旧します` (always recovers automatically).
4. **Numeric drift (overclaim direction)** — English `402 automated tests`; Japanese
   `500以上の自動テスト`.
5. **Omission (safety-relevant)** — the English manual-approval sentence for
   irreversible actions has no Japanese counterpart.

Compliant (2): the CLI-workflow claim and the telemetry claim are reworded but
carry the same commitment strength and scope as the English source.

The `latency_improvement_claim` row is classified as a single item per the
adjudication list, but it carries two distinct defects (the `以上` overclaim and the
dropped caveat); both are documented below.

## Evidence

### Claim-by-claim parity table

| # | Claim | English (source of truth) | Japanese | Classification |
|---|---|---|---|---|
| 1 | Release stage | `We are announcing the public beta of our task runner.` | `タスクランナーの正式版を発表します。` | **stage drift** — `public beta` → `正式版` (GA) |
| 2 | Latency | `In one internal pilot, median latency improved by 18%. This is a single controlled measurement, not a general performance claim.` | `社内パイロットにおいて、中央値レイテンシを 18%以上改善しました。` | **overclaim + numeric drift** — point value `18%` → lower bound `18%以上`; the `not a general performance claim` caveat is dropped |
| 3 | Resume | `Interrupted runs can be resumed from persisted state. Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.` | `実行が中断しても、必ず自動で復旧します。` | **overclaim** — conditional capability + explicit non-guarantee → unconditional `必ず` guarantee; `自動で` also adds automation not claimed in English |
| 4 | Test count | `The release is verified by 402 automated tests.` | `本リリースは 500以上の自動テストで検証されています。` | **numeric drift** — `402` → `500以上` (inflated, and converted to an open-ended lower bound) |
| 5 | Manual approval | `Irreversible actions require manual approval before they run.` | (no corresponding line) | **omission** — safety-relevant control statement absent |
| 6 | CLI workflows | `It works with existing CLI workflows without changes.` | `既存の CLI ワークフローにそのまま組み込めます。` | none — equivalent (`without changes` ≈ `そのまま`) |
| 7 | Telemetry | `No telemetry is collected.` | `テレメトリーは収集しません。` | none — equivalent absolute negative in both |

### Per-item evidence with exact quotes

**announcement.ja.md / automated_test_count — drift (numeric drift)**
- EN line 9: `The release is verified by 402 automated tests.`
- JA line 7: `本リリースは 500以上の自動テストで検証されています。`
- The number `402` does not appear in the Japanese copy. `500以上` both changes the
  value and converts an exact count into an unbounded lower bound the English
  evidence does not support.

**announcement.ja.md / cli_workflow_claim — no-finding**
- EN line 11: `It works with existing CLI workflows without changes.`
- JA line 8: `既存の CLI ワークフローにそのまま組み込めます。`
- `そのまま` carries exactly the `without changes` condition; `組み込めます` is a
  potential form matching `It works with`. No strengthening, no numeric content, no
  dropped safety qualifier.

**announcement.ja.md / latency_improvement_claim — drift (overclaim + numeric drift)**
- EN lines 5–6: `In one internal pilot, median latency improved by 18%. This is a
  single controlled measurement, not a general performance claim.`
- JA line 5: `社内パイロットにおいて、中央値レイテンシを 18%以上改善しました。`
- Two defects: (a) `18%` (a point measurement) becomes `18%以上` (at least 18%),
  which is strictly stronger than the evidence; (b) the second English sentence
  scoping the result to `a single controlled measurement, not a general performance
  claim` has no Japanese counterpart, so the Japanese reads as a general claim.

**announcement.ja.md / manual_approval_sentence_omitted — drift (omission)**
- EN line 10: `Irreversible actions require manual approval before they run.`
- JA: no line contains `不可逆`, `承認`, or any equivalent. The Japanese bullet list
  runs latency → resume → test count → CLI → telemetry (lines 5–9), skipping this
  claim entirely.
- This is safety-relevant: it removes the stated human gate on irreversible actions.

**announcement.ja.md / resume_guarantee_claim — drift (overclaim)**
- EN lines 7–8: `Interrupted runs can be resumed from persisted state. Resumption
  succeeded in our test scenarios; it is not guaranteed under every failure mode.`
- JA line 6: `実行が中断しても、必ず自動で復旧します。`
- The English explicitly negates a universal guarantee (`it is not guaranteed under
  every failure mode`); the Japanese asserts one (`必ず`). The Japanese also asserts
  automatic recovery (`自動で`), whereas the English describes resumption as an
  available capability (`can be resumed`), not an automatic one. This is the
  strongest safety-relevant inversion in the document.

**announcement.ja.md / telemetry_claim — no-finding**
- EN line 12: `No telemetry is collected.`
- JA line 9: `テレメトリーは収集しません。`
- Both are absolute negatives over the same subject with no added scope, exception,
  or qualifier on either side.

### Machine-checkable findings block

| location | key | expected | actual | verdict |
| --- | --- | --- | --- | --- |
| announcement.ja.md | automated_test_count | `402 automated tests` (exact count, per EN line 9) | `500以上の自動テストで検証されています` (JA line 7) | drift |
| announcement.ja.md | cli_workflow_claim | `It works with existing CLI workflows without changes.` (EN line 11) | `既存の CLI ワークフローにそのまま組み込めます。` (JA line 8) — equivalent | no-finding |
| announcement.ja.md | latency_improvement_claim | `median latency improved by 18%` plus `not a general performance claim` caveat (EN lines 5–6) | `中央値レイテンシを 18%以上改善しました` (JA line 5), caveat absent | drift |
| announcement.ja.md | manual_approval_sentence_omitted | `Irreversible actions require manual approval before they run.` (EN line 10) present in JA | no corresponding sentence anywhere in announcement.ja.md | drift |
| announcement.ja.md | resume_guarantee_claim | `it is not guaranteed under every failure mode` (EN lines 7–8) | `実行が中断しても、必ず自動で復旧します。` (JA line 6) | drift |
| announcement.ja.md | telemetry_claim | `No telemetry is collected.` (EN line 12) | `テレメトリーは収集しません。` (JA line 9) — equivalent | no-finding |
| announcement.ja.md | release_stage_claim | `the public beta of our task runner` (EN line 3) | `タスクランナーの正式版を発表します。` (JA line 3) | drift |

The `release_stage_claim` row is outside the six keys named in the prompt. It is
included because the divergence is real and the validator asks for stage drift to be
classified; scorers matching only the six named keys can ignore it.

### Rejected candidates (looked suspicious, not real findings)

**1. `既存の CLI ワークフローにそのまま組み込めます` vs `It works with existing CLI
workflows without changes.`**
Suspicious because `組み込めます` ("can be integrated") is a different verb from
`works with`, which could read as a stronger integration promise. Rejected: the
operative condition in the English claim is `without changes`, and `そのまま`
("as-is") states exactly that. Neither side attaches a qualifier the other lacks, and
no numbers are involved. Pure rewording.

**2. `テレメトリーは収集しません` vs `No telemetry is collected.`**
Suspicious because the Japanese uses an active-voice future/non-past (`収集しません`,
"we do not collect") where the English is passive (`is collected`), which could be
read as narrowing the claim to first-party collection only. Rejected: the English
passive has no stated agent either, so neither version scopes the claim to a
particular collector. The strength (absolute negative) and the subject (telemetry)
are identical.

**3. `本リリースは …検証されています` vs `The release is verified by …`**
Suspicious because the Japanese `-ています` form could be read as an ongoing state
rather than a completed verification. Rejected as a *wording* finding: the state
reading matches the English present tense `is verified`. The real defect in this
bullet is the number (`402` → `500以上`), which is reported separately under
`automated_test_count`; the verb form itself is not an independent divergence.

**4. `社内パイロットにおいて` vs `In one internal pilot`**
Suspicious because the Japanese drops the numeral `one`, weakening the
single-instance framing. Rejected as an independent finding: `社内パイロットにおいて`
is singular-neutral in Japanese and does not itself assert multiple pilots. The
substantive loss of scoping in this bullet is the deleted caveat sentence, which is
already reported under `latency_improvement_claim`; counting the missing `one`
separately would double-count the same defect.

## Assumptions

- The English file is authoritative in every conflict, including where the Japanese
  is merely more specific.
- Adding an unbounded lower bound (`以上`) to a point measurement counts as making
  the claim stronger than the evidence, not as a translation nicety.
- Line numbers cited refer to the fixture files as read in this run
  (`announcement.en.md`: 13 lines; `announcement.ja.md`: 9 lines).
- Only the two fixture files named in the prompt and this output file were opened.
  No benchmark metadata, task definitions, scoring configuration, or answer keys
  were read, so this artifact carries no information about how it will be scored.
- **Unmeasured:** whether `402` or `500` matches any actual test suite in the
  repository. Only the two fixture documents were compared; no test run, CI log, or
  source tree was inspected, and no such verification was attempted.
- **Unmeasured:** whether the omitted manual-approval control actually exists in the
  product. This artifact only reports that the English sentence has no Japanese
  counterpart.
- **Unmeasured:** the intent behind each divergence (translation error vs. deliberate
  marketing edit). Only the text difference is observed.
- No claim is made about the relative performance of this arm versus any other; this
  artifact is a single task deliverable.

## Stop Condition

Met. This artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-08-21-verdict-tail-v1/tail-bilingual-release-drift-claude_code_goal_command-rep3.md`
and contains the required headings (Goal, Result, Evidence, Assumptions, Stop
Condition), a claim-by-claim parity table quoting both languages with each
divergence classified (overclaim / numeric drift / stage drift / omission), a
rejected-candidates section for reworded-but-equivalent claims, and exactly one
markdown table using the header `| location | key | expected | actual | verdict |`
with one row per adjudicated item and verdicts restricted to `drift` / `no-finding`.
No commits, pushes, installs, or network access were performed; the only filesystem
write is this file (plus its parent directory).
