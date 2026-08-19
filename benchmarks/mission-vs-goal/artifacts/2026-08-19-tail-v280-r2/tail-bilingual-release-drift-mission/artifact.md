# tail-bilingual-release-drift — mission arm

Task id: `tail-bilingual-release-drift` · Category: documentation · Arm: mission · Mission profile: full · Complexity: Complex · `--max-iter 2`

## Mission

Audit the Japanese release announcement against the English draft (the source of truth) claim by claim, and produce one auditable artifact that:

1. quotes both sides of every claim pair,
2. classifies each divergence as **overclaim**, **numeric drift**, **stage drift**, or **omission**,
3. separates confirmed findings from rejected candidates, stating for each rejected candidate why it looked suspicious and why it is not a real finding.

Files under audit (read-only):

- `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.en.md` (12 newline-terminated lines, 597 bytes, source of truth)
- `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.ja.md` (9 newline-terminated lines, 486 bytes, translation under audit)

Single output file: `benchmarks/mission-vs-goal/run-output/2026-08-19-tail-v280-r2/tail-bilingual-release-drift-mission.md` (this file).

Out of scope: any other path under `benchmarks/mission-vs-goal/`, git operations, package installation, network access. No claim of benchmark superiority is made anywhere in this artifact.

## Plan

Adopted as canonical `mission-plan/1` via `mission-state.py planning adopt-core`; digest `sha256:06ff61c1ae82fdf01b3560a61818b545f3e7b3a2d834639bd2264ab7c61f1193`, stored at `.mission-state/plans/06ff61c1ae82fdf0.json`.

| Step | Action | Output | Acceptance check |
|---|---|---|---|
| S1 | read | Verbatim claim inventory for both files | Every EN/JA heading, lede and bullet enumerated with line numbers |
| S2 | analyze | Classified divergence set + rejected-candidate set | Each EN claim mapped to a JA counterpart or marked absent; each divergence labelled with one of the four classes; each rejection reasoned |
| S3 | write | This artifact | Eight mandated headings present; parity table quotes both languages; rejected-candidates section present |
| S4 | decide | Three reviewer verdicts, aggregated score, pass decision | 3 reviewers in one parallel message; `review-import` → `review-finalize --min-reviewers 3`; `closeout` |

Stop conditions: composite ≥ 4.0 with `open_high == 0` and min scored item ≥ 3.5; or `--max-iter 2` exhausted; or a fixture/output path becomes unreadable.

## Execution

### Claim inventory

English (`announcement.en.md`), 1 heading + 1 lede + 6 bullets:

| id | line | verbatim |
|---|---|---|
| EN-0 | 1 | `# Release announcement — draft (English, source of truth)` |
| EN-1 | 3 | `We are announcing the public beta of our task runner.` |
| EN-2 | 5–6 | `In one internal pilot, median latency improved by 18%. This is a single controlled measurement, not a general performance claim.` |
| EN-3 | 7–8 | `Interrupted runs can be resumed from persisted state. Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.` |
| EN-4 | 9 | `The release is verified by 402 automated tests.` |
| EN-5 | 10 | `Irreversible actions require manual approval before they run.` |
| EN-6 | 11 | `It works with existing CLI workflows without changes.` |
| EN-7 | 12 | `No telemetry is collected.` |

Japanese (`announcement.ja.md`), 1 heading + 1 lede + 5 bullets:

| id | line | verbatim |
|---|---|---|
| JA-0 | 1 | `# リリース告知 — ドラフト（日本語訳・レビュー前）` |
| JA-1 | 3 | `タスクランナーの正式版を発表します。` |
| JA-2 | 5 | `社内パイロットにおいて、中央値レイテンシを 18%以上改善しました。` |
| JA-3 | 6 | `実行が中断しても、必ず自動で復旧します。` |
| JA-4 | 7 | `本リリースは 500以上の自動テストで検証されています。` |
| JA-5 | 8 | `既存の CLI ワークフローにそのまま組み込めます。` |
| JA-6 | 9 | `テレメトリーは収集しません。` |

Bullet-count delta: EN 6 bullets vs JA 5 bullets. The single missing bullet is accounted for by finding **F7** below; it is not an unexplained residue.

### Claim-by-claim parity table

| # | English (source of truth) | Japanese | Verdict | Class |
|---|---|---|---|---|
| P0 | `# Release announcement — draft (English, source of truth)` | `# リリース告知 — ドラフト（日本語訳・レビュー前）` | Equivalent (see R1) | — |
| P1 | `We are announcing the public beta of our task runner.` | `タスクランナーの正式版を発表します。` | **Divergent → F1** | stage drift |
| P2 | `median latency improved by 18%` | `中央値レイテンシを 18%以上改善しました` | **Divergent → F2** | **overclaim** (secondary: numeric drift) |
| P3 | `This is a single controlled measurement, not a general performance claim.` | *(no counterpart in JA-2 or anywhere in the file)* | **Divergent → F3** | omission |
| P4 | `Interrupted runs can be resumed from persisted state.` | `実行が中断しても、必ず自動で復旧します。` | **Divergent → F4** | overclaim |
| P5 | `Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.` | *(no counterpart anywhere in the file)* | **Divergent → F5** | omission |
| P6 | `The release is verified by 402 automated tests.` | `本リリースは 500以上の自動テストで検証されています。` | **Divergent → F6** | **numeric drift** (secondary: overclaim) |
| P7 | `Irreversible actions require manual approval before they run.` | *(no counterpart anywhere in the file)* | **Divergent → F7** | omission (safety-relevant) |
| P8 | `It works with existing CLI workflows without changes.` | `既存の CLI ワークフローにそのまま組み込めます。` | Equivalent (see R2) | — |
| P9 | `No telemetry is collected.` | `テレメトリーは収集しません。` | Equivalent (see R3) | — |

Each divergent row carries exactly one **primary** class from the required set. P2 and P6 additionally note a secondary class because the two mechanisms genuinely co-occur; the primary label is the one that would stand alone if only one could be recorded. P2's primary is *overclaim* because the digits `18` are unchanged and the strengthening is carried entirely by the added `以上`. P6's primary is *numeric drift* because the count itself changes (`402` → `500`).

### Confirmed findings

**F1 — stage drift: public beta presented as general availability.** Severity: High.
- EN: `We are announcing the public beta of our task runner.`
- JA: `タスクランナーの正式版を発表します。`
- `public beta` is a pre-GA stage carrying an implied stability caveat. `正式版` is the standard Japanese term for the official/general-availability release and carries no beta qualifier. The Japanese lede therefore promotes the release one stage beyond the English evidence. There is no occurrence of `ベータ`, `beta`, or `公開ベータ` anywhere in `announcement.ja.md`.

**F2 — overclaim (secondary: numeric drift): an exact figure becomes a lower bound.** Severity: High.
- EN: `median latency improved by 18%`
- JA: `中央値レイテンシを 18%以上改善しました`
- The English states a point measurement of `18%`. The Japanese `18%以上` means "18% or more", converting a single observed value into an open-ended lower bound. Every result above 18% is asserted without evidence. The digit string `18` is identical on both sides; the drift is entirely carried by the added `以上`.

**F3 — omission: the measurement-scoping caveat is dropped.** Severity: High.
- EN: `This is a single controlled measurement, not a general performance claim.`
- JA: *absent* — `announcement.ja.md` contains no counterpart sentence; JA-2 ends at `改善しました。`
- The English sentence exists specifically to prevent the pilot number from being read as a general performance claim. Dropping it, in combination with F2, leaves `18%以上改善しました` standing as an unqualified product-wide performance assertion.

**F4 — overclaim: conditional resumption becomes a guarantee.** Severity: High.
- EN: `Interrupted runs can be resumed from persisted state.`
- JA: `実行が中断しても、必ず自動で復旧します。`
- Two independent strengthenings in one sentence: (a) `can be resumed` (capability) → `必ず…復旧します` (`必ず` = always/without fail, a universal guarantee); (b) `resumed from persisted state` (an available operation) → `自動で復旧します` (`自動で` = automatically, i.e. no operator action). The English never claims automatic recovery and never claims it always succeeds.

**F5 — omission: the failure-mode limitation is dropped.** Severity: High (safety-relevant).
- EN: `Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.`
- JA: *absent* — no counterpart to either clause; the words `テストシナリオ`, `保証`, or any negation of guarantee do not appear in `announcement.ja.md`.
- The English explicitly scopes success to test scenarios and explicitly denies a universal guarantee. The Japanese not only drops that denial but asserts its opposite via F4's `必ず`. F4 and F5 are separate findings — one is a strengthened statement, the other is a deleted limitation — but they compound.

**F6 — numeric drift (secondary: overclaim): 402 tests reported as 500+.** Severity: High.
- EN: `The release is verified by 402 automated tests.`
- JA: `本リリースは 500以上の自動テストで検証されています。`
- The count is inflated from the exact `402` to `500以上` ("500 or more"). This is both a numeric change (402 → 500) and a direction change (exact → lower bound). The overstatement is approximately 98 tests at minimum; the string `402` does not appear anywhere in `announcement.ja.md`.

**F7 — omission: the manual-approval safeguard for irreversible actions is missing entirely.** Severity: High (safety-relevant).
- EN: `Irreversible actions require manual approval before they run.`
- JA: *absent* — this is the one EN bullet with no JA bullet at all. The tokens `承認`, `不可逆`, and `手動` do not appear anywhere in `announcement.ja.md`.
- This is the most safety-relevant divergence in the pair: readers of the Japanese announcement are given no indication that irreversible operations are gated on human approval, which affects both their risk expectations and their operating procedure.

### Rejected candidates (reworded but equivalent, or not supported)

**R1 — heading wording differs.** `# Release announcement — draft (English, source of truth)` vs `# リリース告知 — ドラフト（日本語訳・レビュー前）`. *Why it looked suspicious:* the parenthetical text differs entirely, so a naive string diff flags the very first line. *Why it is not a finding:* both headings retain the `draft` / `ドラフト` status marker, and the parentheticals describe each document's own role (source of truth vs. pre-review translation). `レビュー前` is if anything more cautious than the English, not stronger. No claim about the product is affected.

**R2 — "without changes" vs 「そのまま」.** `It works with existing CLI workflows without changes.` vs `既存の CLI ワークフローにそのまま組み込めます。` *Why it looked suspicious:* the English negates a requirement (`without changes`) while the Japanese uses a positive adverb, and `組み込めます` ("can be integrated") is not a literal rendering of `works with`. *Why it is not a finding:* `そのまま` means "as-is / unchanged", which is the exact semantic content of `without changes`; and `組み込めます` is a potential form (capability), matching the English's non-guaranteed register. Neither strength nor scope is increased.

**R3 — telemetry sentence.** `No telemetry is collected.` vs `テレメトリーは収集しません。` *Why it looked suspicious:* it is an absolute privacy claim, exactly the category where drift matters most, so it warrants explicit clearance rather than silent omission. *Why it is not a finding:* the Japanese is a direct, scope-identical negation with no added qualifier and no removed one. Equivalent.

**R4 — announcing verb.** `We are announcing …` vs `… を発表します。` *Why it looked suspicious:* it sits inside the lede that does contain a real divergence (F1). *Why it is not a finding:* the drift in that sentence is entirely in the object (`public beta` → `正式版`, tracked as F1). The verb itself is a standard equivalent, and counting it separately would double-count F1.

**R5 — singular marking of "one internal pilot".** `In one internal pilot,` vs `社内パイロットにおいて、`. *Why it looked suspicious:* the English numeral `one` has no explicit counterpart, so the Japanese does not state that only a single pilot was run. *Why it is not a finding here:* Japanese has no obligatory number marking, and `社内パイロットにおいて` reads as a single unspecified pilot rather than as "all pilots"; it adds no strength. The substantive singularity claim that *was* deleted is the explicit sentence `This is a single controlled measurement, not a general performance claim.`, which is already recorded as F3. Recording R5 separately would double-count that omission.

**R6 — passive-voice verification phrasing.** `The release is verified by … automated tests.` vs `本リリースは … 自動テストで検証されています。` *Why it looked suspicious:* it is the carrier sentence for a confirmed numeric drift (F6), so the whole line reads as suspect. *Why it is not a finding:* the sentence frame preserves subject, passive voice and evidence type exactly. Only the count diverges, which is F6.

**R7 — bullet-count mismatch (6 EN vs 5 JA).** *Why it looked suspicious:* a structural asymmetry usually signals hidden content loss beyond the sentence level. *Why it is not an independent finding:* the delta of exactly one is fully explained by F7 (the manual-approval bullet). The other two deleted English sentences (F3, F5) were trailing clauses inside bullets, not standalone bullets, so they do not change the count. No unexplained structural residue remains.

## Review

Three independent reviewers (full tier) were launched in a single parallel message and their `mission-review/1` verdicts were imported and aggregated by the mission state CLI. Reviewer verdicts, per-item scores and comments are stored in full under `.mission-state/archive/`; they are not transcribed here (output-compression discipline).

| Perspective | Imported evidence path | Digest |
|---|---|---|
| A — evidence/quote fidelity | `.mission-state/archive/iter-1-94c38ccc-review-input-7fa895ca95b2dea5.json` | `sha256:7fa895ca95b2dea5…` |
| B — classification correctness & validator conformance | `.mission-state/archive/iter-1-94c38ccc-review-input-cbd2e505e181b3f8.json` | `sha256:cbd2e505e181b3f8…` |
| C — rejected-candidate reasoning & scope discipline | `.mission-state/archive/iter-1-94c38ccc-review-input-9643266d704c685d.json` | `sha256:9643266d704c685d…` |

Aggregate: `.mission-state/archive/iter-1-94c38ccc-reviews-7cbd0f87e3092cf4.json` (`sha256:7cbd0f87e3092cf4…`), 3 scoring reviewers, 0 findings-only reviewers.

**Outcome that matters for this task: no reviewer reported a missed divergence, and no reviewer reported a false-positive finding or an unsound rejection.** Reviewer B (validator conformance) confirmed all seven divergences F1–F7 were found; reviewer C confirmed all seven rejections R1–R7 are sound and that F1–F7 contain no rewording false positives; reviewer A confirmed every quoted string is verbatim and every absence claim holds.

Six defects were raised, all outside the parity analysis itself:

| Finding | Severity | Substance | Disposition |
|---|---|---|---|
| A-1 / A-2 | Low | Fixture line counts in Mission and Evidence were wrong (EN stated as 13; both stated as having a trailing blank line) | **Fixed.** Re-measured: EN `wc -l`=12 / 597 B, JA `wc -l`=9 / 486 B, neither with a trailing blank line. Per-claim line attributions were already correct and were unchanged. |
| A-3 / B-1 / C-1 | Low / Medium / Medium | Score and Stop Decision held placeholder text while the Evidence table asserted outputs were recorded there | **Fixed.** Both sections now carry tool-computed values; the Evidence row was reworded to point at the state paths that actually hold them. |
| B-2 | Low | P2 and P6 carried two class labels where the validator says "one of" | **Fixed.** Each divergent row now designates one primary class, with the co-occurring mechanism noted as secondary and justified. |

Per the M6 rule (orchestrator-applied fixes to Medium-or-above findings are not self-certified), a differential reviewer (perspective D) re-reviewed the fixed artifact before the pass decision. It confirmed all six fixes hold, all eight headings remain, no superiority claim was introduced, and no verbatim quote was corrupted. It raised one new Low finding:

| Finding | Severity | Substance | Disposition |
|---|---|---|---|
| D-1 | Low | The B-2 fix inserted an explanatory paragraph between the P6 and P7 rows; the preceding blank line closed the Markdown table, leaving P7 outside it | **Fixed.** The paragraph was moved below the final table row (after P9); the parity table is again one contiguous block from P0 to P9. |

Reviewer D's scores (`mission_achievement` 4.5, `accuracy` 4.8, `completeness` 4.3, `usability` 4.2) are a differential re-confirmation of the fixes and were deliberately **not** merged into the iteration-1 aggregate, which remains the three-reviewer full-tier score of record.

Full reviewer JSON — per-item scores, verbatim finding text and summaries — is stored under `.mission-state/archive/` and is deliberately not transcribed here.

## Score

All values below are tool-computed by `mission-state.py review-finalize` (= `aggregate-reviews` → `push-score --scoring-json`) and read back from `score_history[0]` in the session state. None is hand-derived.

| Gate | Required | Iteration 1 actual | Verdict |
|---|---|---|---|
| composite_score | ≥ 4.0 (threshold) | **4.33** | pass |
| min scored item | ≥ 3.5 | **4.17** (accuracy / mission_achievement) | pass |
| open_high | == 0 | **0** | pass |
| max_agreement_delta | ≤ 1.5 | **1.3** (completeness: max 4.8, min 3.5) | pass |
| findings_evidence_path | present | `.mission-state/archive/iter-1-94c38ccc-reviews-7cbd0f87e3092cf4.json` | pass |
| reviewer count | ≥ 3 (full tier) | 3 scoring reviewers, enforced by `--min-reviewers 3` | pass |

Per-axis aggregate: `mission_achievement` 4.17, `accuracy` 4.17, `completeness` 4.33, `usability` 4.67. `review_agreement` 3.0. Scoring artifact: `.mission-state/archive/iter-1-94c38ccc-scoring-99e6b229fcbca93a.json` (`sha256:99e6b229fcbca93a…`), recorded at `2026-08-19T07:48:07Z`, `score_source: "scoring-json"`.

Agreement spread by axis (max − min across the three reviewers): accuracy 0.5, completeness 1.3, mission_achievement 0.5, usability 0.5.

## Stop Decision

**Stop after iteration 1 of a `--max-iter 2` budget. Pass.**

Every gate in the Score table is met, and the early-stop rule applies: the threshold was reached at iteration 1 with `open_high == 0`. The continuation carve-out (composite 4.0–4.3 *and* three or more Medium findings) does not apply here — composite is 4.33, above that band, and only two Medium findings were raised (B-1 and C-1), which are the same defect seen from two perspectives and are now fixed.

No divergence was left unclassified and no reviewer reported a missed divergence, so a second iteration would have no finding to act on.

Sequence executed: `review-import` ×3 → `review-finalize --iteration 1 --min-reviewers 3` → M6 differential re-review of the applied fixes → `closeout` (`mark-passes` → `next`). The exact command outputs are recorded in the session state and the archive paths cited above.

## Evidence

| Claim in this artifact | Evidence |
|---|---|
| EN fixture size | `wc -l` = 12, `wc -c` = 597 for `announcement.en.md`; `tail -c 3 \| xxd -p` = `642e0a`, i.e. the file ends `d.` + newline with **no** trailing blank line |
| JA fixture size | `wc -l` = 9, `wc -c` = 486 for `announcement.ja.md`; `tail -c 3 \| xxd -p` = `80820a`, i.e. the file ends `。` + newline with **no** trailing blank line |
| All quoted strings | Copied verbatim from the two reads above; no paraphrase appears inside backticks |
| Absence claims (F3, F5, F7 and the token checks `ベータ` / `402` / `承認` / `不可逆` / `手動` / `テストシナリオ` / `保証`) | Verified by inspecting the full 9-line Japanese file, which is short enough to be read in its entirety; no sampling was involved |
| Plan is canonical | `mission-state.py planning adopt-core --input .mission-state/plan-core-iter1.json` → `sha256:06ff61c1ae82fdf01b3560a61818b545f3e7b3a2d834639bd2264ab7c61f1193` |
| Mission state is auditable | `.mission-state/sessions/cc-6d5d578c-7830-4805-b14c-b5deae3b2943.json`, mission id `94c38ccce62cc2f8`, `permission_preflight: passed`, `review_tier: "full"` |
| Scored review iteration occurred | `review-import` (×3, evidence paths listed under Review) → `review-finalize --iteration 1 --min-reviewers 3 --reviewer-window A/B/C` → `closeout`. Tool-computed gate values were read back from `score_history[0]` in `.mission-state/sessions/cc-6d5d578c-7830-4805-b14c-b5deae3b2943.json` and are reproduced in the Score table; the aggregate and scoring artifacts are at `.mission-state/archive/iter-1-94c38ccc-reviews-7cbd0f87e3092cf4.json` and `.mission-state/archive/iter-1-94c38ccc-scoring-99e6b229fcbca93a.json` |
| Reviewed revision scope | `revision_scope` = git, `base_sha` = `head_sha` = `f8a4b983b6d0e49e58d8cc530f5eb93bf97ed70e`. The artifact is uncommitted working-tree content (this run must not commit), so base and head are the same commit and the reviewed bytes are pinned instead by the recorded artifact digest |
| Fixes applied after review were not self-certified | Reviewer findings A-1/A-2 (line counts), A-3/B-1/C-1 (placeholder sections) and B-2 (dual labels) were fixed by the orchestrator; a differential reviewer then re-confirmed the fixed artifact before `mark-passes`, per M6 |
| Local authoring sync was attempted | `bash "$MISSION_PLUGIN_ROOT/scripts/mission-local-authoring-sync.sh"` printed `error: local Mission source must be clean before syncing origin/main`; no `status=ready` was observed, so the repository-root `scripts/mission-state.py` at the checked-out revision was used and no network operation was performed |
| Scope discipline | No path under `benchmarks/mission-vs-goal/` other than the two named fixtures and this output file was read, listed or searched |

### Explicitly unmeasured

- **Translator intent.** Whether the Japanese divergences are deliberate marketing changes or translation errors is unmeasured; only the text difference is observed.
- **External truth of the numbers.** Whether the real figures are `18%`/`402` or the Japanese `18%以上`/`500以上` is unmeasured. The English draft is treated as the source of truth by task definition, not by independent verification.
- **Any cross-arm comparison.** No comparison against a goal arm or any other run was performed, and no superiority claim of any kind is made.
- **Wall-clock and token cost of this run.** Not instrumented here.
- **Downstream publication impact.** Whether the Japanese copy was published or is still pre-review beyond its own `レビュー前` label is unmeasured.

## Assumptions

| id | Assumption | How it was validated |
|---|---|---|
| A1 | Only the two named fixtures and this output file may be touched under `benchmarks/`; benchmark metadata is out of bounds | No tool call read, listed or searched any other path under `benchmarks/mission-vs-goal/` |
| A2 | The English draft is authoritative; JA text that is stronger, numerically different, or drops a safety-relevant qualifier is a divergence regardless of translation idiom | Every parity row cites the exact EN and JA substring supporting its verdict |
| A3 | Divergence classes are limited to overclaim / numeric drift / stage drift / omission; each divergent row carries exactly one **primary** label, and P2/P6 additionally note a secondary mechanism | Every divergent row carries exactly one primary label from the four-class set; the primary choice is justified inline under the parity table |
| A4 | No commit, push, install or network access; local authoring sync was not `status=ready`, so the checked-out repo CLI is used | Sync stdout quoted in Evidence; no git write or network command was issued during this run |
| A5 | `必ず` / `自動で` / `以上` / `正式版` are read with their standard Japanese meanings (always / automatically / or more / official release) | Each finding states the reading used, so a reviewer can contest it directly |

Recorded in the mission state at `.mission-state/sessions/cc-6d5d578c-7830-4805-b14c-b5deae3b2943-assumptions.md`.
