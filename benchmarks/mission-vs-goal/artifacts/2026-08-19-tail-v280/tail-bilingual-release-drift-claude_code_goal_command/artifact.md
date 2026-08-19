# tail-bilingual-release-drift — claude_code_goal_command

## Goal

Compare `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.en.md` (source of truth) against `benchmarks/mission-vs-goal/fixtures/tail/bilingual-release-drift/announcement.ja.md` claim by claim, and identify every place where the Japanese copy is **stronger than**, **numerically different from**, or **missing a safety-relevant statement of** the English evidence — quoting both sides. Reworded-but-equivalent claims must be explicitly rejected as non-findings with reasoning.

Task id: `tail-bilingual-release-drift`. Category: documentation. Arm: `claude_code_goal_command` (Claude Code's built-in `/goal` command used as the completion controller).

## Result

**6 confirmed divergences** and **3 rejected candidates**.

Confirmed divergences by class:

| Class | Count | IDs |
|---|---|---|
| Stage drift | 1 | D1 |
| Numeric drift | 2 | D2, D5 |
| Overclaim | 2 | D3, D4 |
| Omission (safety-relevant) | 3 | D3-b, D4-b, D6 |

(D3 and D4 each produce two distinct defects — a strengthened assertion **and** a dropped hedge — so they are listed once in the parity table and split into the overclaim / omission counts above.)

The English draft has 6 bullets plus a title line; the Japanese draft has 5 bullets plus a title line. One English bullet has no Japanese counterpart at all (D6, the manual-approval safety gate).

## Evidence

### Claim-by-claim parity table

| # | Claim | English (source of truth) | Japanese | Classification |
|---|---|---|---|---|
| D1 | Release stage | `We are announcing the public beta of our task runner.` | `タスクランナーの正式版を発表します。` | **Stage drift** — `public beta` → `正式版` (general availability / official release). The Japanese removes the beta qualifier entirely and announces a GA release. |
| D2 | Latency figure | `median latency improved by 18%` | `中央値レイテンシを 18%以上改善しました。` | **Numeric drift** — a point estimate `18%` becomes a lower bound `18%以上` ("18% or more"). The English states an exact measured value; the Japanese asserts everything at or above it. |
| D3 | Latency scope caveat | `In one internal pilot, median latency improved by 18%. This is a single controlled measurement, not a general performance claim.` | `社内パイロットにおいて、中央値レイテンシを 18%以上改善しました。` | **Overclaim + Omission** — the whole second sentence (`This is a single controlled measurement, not a general performance claim.`) has no Japanese counterpart. With the scope disclaimer gone and `以上` added, the Japanese reads as a general performance claim, which is exactly what the English forbids. |
| D4 | Resumption guarantee | `Interrupted runs can be resumed from persisted state. Resumption succeeded in our test scenarios; it is not guaranteed under every failure mode.` | `実行が中断しても、必ず自動で復旧します。` | **Overclaim + Omission** — capability (`can be resumed`, requiring `persisted state`) becomes an unconditional guarantee (`必ず自動で復旧します` = "always recovers automatically"). The English hedge `it is not guaranteed under every failure mode` is absent, and `自動` (automatic) is not asserted anywhere in the English. |
| D5 | Test count | `The release is verified by 402 automated tests.` | `本リリースは 500以上の自動テストで検証されています。` | **Numeric drift** — `402` → `500以上` ("500 or more"). Both the magnitude and the form (exact count → lower bound) differ; `500以上` is not true of `402`. |
| D6 | Manual approval for irreversible actions | `Irreversible actions require manual approval before they run.` | *(no corresponding line)* | **Omission (safety-relevant)** — the Japanese draft has no statement about irreversible actions or manual approval. A reader of the Japanese copy is not told that destructive operations are gated. |
| R1 | CLI compatibility | `It works with existing CLI workflows without changes.` | `既存の CLI ワークフローにそのまま組み込めます。` | **No divergence** (rejected — see below) |
| R2 | Telemetry | `No telemetry is collected.` | `テレメトリーは収集しません。` | **No divergence** (rejected — see below) |
| R3 | Document title / status | `# Release announcement — draft (English, source of truth)` | `# リリース告知 — ドラフト（日本語訳・レビュー前）` | **No divergence** (rejected — see below) |

### Exact quoted values used as evidence

- Stage tokens: `public beta` (en:3) vs `正式版` (ja:3).
- Latency tokens: `18%` (en:5) vs `18%以上` (ja:5); dropped sentence `This is a single controlled measurement, not a general performance claim.` (en:5–6).
- Resumption tokens: `can be resumed from persisted state` / `it is not guaranteed under every failure mode` (en:7–8) vs `必ず自動で復旧します` (ja:6).
- Test-count tokens: `402 automated tests` (en:9) vs `500以上の自動テスト` (ja:7).
- Missing safety line: `Irreversible actions require manual approval before they run.` (en:10) — no token in `announcement.ja.md`.

Line numbers refer to the two fixture files as read during this run.

### Rejected candidates (looked suspicious, not real findings)

- **R1 — "It works with existing CLI workflows without changes." vs "既存の CLI ワークフローにそのまま組み込めます。"**
  Why it looked suspicious: the Japanese uses `そのまま組み込めます` ("can be dropped in as-is"), which sounds like a stronger integration promise than the English, and the explicit `without changes` has no literal word-for-word counterpart.
  Why it is not a finding: `そのまま` *is* the rendering of `without changes` — it carries the "no modification required" condition rather than dropping it. `組み込めます` is potential form ("can be integrated"), matching the English capability phrasing `It works with`. No numeric value, no stage claim, and no safety hedge is involved. Reworded but equivalent.

- **R2 — "No telemetry is collected." vs "テレメトリーは収集しません。"**
  Why it looked suspicious: English is passive/stative while Japanese is an active first-party assertion (`収集しません` = "we do not collect"), which could in principle narrow the claim to first-party collection only.
  Why it is not a finding: the English sentence has no stated agent either, and in a first-party release announcement both read as "this product does not collect telemetry." The scope, polarity, and absence of qualifiers are identical on both sides. Reworded but equivalent.

- **R3 — Title line: "draft (English, source of truth)" vs "ドラフト（日本語訳・レビュー前）"**
  Why it looked suspicious: the parenthetical content differs outright, so a naive diff flags it as a mismatch.
  Why it is not a finding: both mark the document as a `draft` / `ドラフト`. The parentheticals are per-file provenance metadata — the English states it is the source of truth, the Japanese states it is a translation pending review. These are correct statements *about each file*, not competing claims about the product. If anything the Japanese label is more conservative (`レビュー前`), so it is not an overclaim.

### Not evaluated / unmeasured

- Whether the numbers in the English draft (`18%`, `402`) are themselves accurate against any underlying test or benchmark data: **unmeasured** — no such source was consulted, and the task defines the English draft as the source of truth.
- Tone, register, formatting conventions, and translation quality beyond claim strength/number/safety parity: **not evaluated** — out of scope for this task.
- Any comparison against other benchmark arms or scoring configuration: **not performed**. Benchmark metadata was treated as out of bounds and was not opened, read, grepped, or listed.

## Assumptions

1. The English file is authoritative for every claim; any Japanese-side strengthening is a defect in the Japanese copy, never a correction of the English.
2. `以上` ("or more") converts a point estimate into a lower bound and is therefore treated as a numeric divergence even where the base number is identical (D2), independent of the separate magnitude change in D5.
3. `正式版` is read as general availability / official release rather than as a loose synonym for "release", making the loss of `public beta` a stage drift rather than a wording choice.
4. A dropped hedge is classified as an omission only when the hedge is safety- or scope-relevant (D3-b, D4-b); the missing manual-approval bullet (D6) is treated as the most severe omission because it removes a stated control on irreversible actions.
5. Line numbers cited above correspond to the fixture files in their state at read time during this run; no fixture file was modified.
6. Only the two named fixture files and this output file were accessed under `benchmarks/mission-vs-goal/`.

## Stop Condition

This run stops when all of the following hold:

- [x] This artifact exists at `benchmarks/mission-vs-goal/run-output/2026-08-19-tail-v280/tail-bilingual-release-drift-claude_code_goal_command.md` and is the only task artifact written.
- [x] It contains the headings **Goal**, **Result**, **Evidence**, **Assumptions**, and **Stop Condition**.
- [x] It contains a claim-by-claim parity table quoting both the English and the Japanese text for every compared claim.
- [x] Every divergence is classified as one of: overclaim, numeric drift, stage drift, omission.
- [x] A rejected-candidates section exists, covering reworded-but-equivalent claims with reasoning for each rejection.
- [x] Every confirmed finding quotes the exact identifier or value from the fixture.
- [x] Unmeasured items are explicitly labelled unmeasured.
- [x] No commits, pushes, package installs, or network access were performed; no benchmark metadata was accessed.

No claim of benchmark superiority is made here; this artifact records only the outcome of this single task.
