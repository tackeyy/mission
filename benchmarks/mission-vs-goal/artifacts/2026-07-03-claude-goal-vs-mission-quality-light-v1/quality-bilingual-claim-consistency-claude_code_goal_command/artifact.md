# Goal

Update a hypothetical English/Japanese benchmark announcement so the Japanese
copy never claims more than the English evidence supports, and hand reviewers a
checklist that keeps both language versions inside the measured limits.

Controller: Claude Code official built-in `/goal` command. This artifact is the
single deliverable for task `quality-bilingual-claim-consistency`, arm
`claude_code_goal_command`. No benchmark-superiority claim is made here; the
deliverable is only this task artifact.

# Result

I produced a bilingual claim-consistency review kit for the announcement, not a
new performance result. It contains: (1) paired EN/JP announcement copy pinned
to measured facts, (2) an English/Japanese parity table, (3) a measured-vs-
inferred split, (4) a list of unsafe claims to avoid in both languages, (5)
unresolved translation choices, (6) an evidence map, (7) a reviewer gate, and
(8) residual risk.

The controlling fact both languages must respect: the only *completed* paired
measurement in this benchmark is the 2026-06-27 Codex CLI pilot (20/20 runs).
Every Claude Code official `/goal` vs `/mission` run recorded in `report.md` is
either a smoke (N=1) or was blocked by API/budget limits. So neither the English
nor the Japanese announcement may claim a general or Claude-Code-specific
performance win. The Japanese copy is the higher risk because Japanese omits
grammatical subjects and hedges easily drop in translation, which can silently
upgrade a scoped claim into an absolute one.

## English/Japanese Parity

Rule: every measured number, hedge, and scope qualifier present in one language
must be present in the other. Parity is checked claim-by-claim, not by fluent
back-translation.

| Claim in announcement | English form (must keep) | Japanese form (must keep) | Parity status |
|---|---|---|---|
| Study type | "controlled local pilot, not a general model benchmark" | 「controlled local pilot であり general model benchmark ではない」 | Measured — both `report.md` and `report.ja.md` carry this disclaimer verbatim |
| Scoring method | "automated heuristic score, not blind human review" | 「automated heuristic score であり blind human review ではない」 | Measured — present in both reports |
| Sample size (Codex pilot) | "20 paired runs (10 tasks x 2 arms)" | 「20 paired runs（10 tasks x 2 arms）」 | Measured |
| Codex quality delta | "mission +0.50 quality, +0.90 evidence, but +1.71 min slower" | 「mission は quality +0.50 / evidence +0.90、ただし +1.71 分 遅い」 | Measured — the "slower" trade-off must not be dropped in JP |
| Claude Code `/goal` status | "only N=1 smoke completed; larger runs were blocked" | 「完了したのは N=1 smoke のみ。より大きい run は blocked」 | Measured — do not let JP imply a full Claude Code comparison happened |
| Completion/validator rates | "both arms 100% in the Codex pilot" | 「Codex pilot では両 arm 100%」 | Measured — JP must keep "Codex pilot" scope, not generalize |

Parity failures to reject at review: a Japanese sentence that keeps the number
but drops "ではない/ただし/のみ" (the negation, the trade-off, or the "only"),
or an English sentence that hedges while the Japanese asserts.

## Measured vs Inferred

| Statement | Status | Basis |
|---|---|---|
| Codex pilot ran 20/20 paired runs, both arms 100% completion and validator pass | Measured | `benchmarks/mission-vs-goal/report.md` Results table; `report.ja.md` mirror |
| Codex pilot: quality 4.00 (goal) vs 4.50 (mission); evidence 3.80 vs 4.70; elapsed 1.28 vs 2.99 min | Measured | `report.md` Results table |
| Scores are automated heuristic, not blind human review | Measured | `report.md` header + Measurement Scope row |
| Claude Code official `/goal` light rerun (N=1): both arms completed, tied 4.00/4.00, mission light faster and lower cost on that one task | Measured | `report.md` "Lightweight Mission Profile Rerun" |
| Larger Claude Code `/goal` vs `/mission` runs were blocked by API usage / max-budget limits | Measured | `report.md` smoke-v2, complex-v1, incremental-v1, quality-v1 sections |
| `report.md` and `report.ja.md` are structurally parallel | Measured | Direct read of both files on 2026-07-03 |
| The Japanese announcement would read as "stronger" than English if hedges drop | Inferred | Reasoning about Japanese subject-dropping and translation hedge-loss; not measured against real reader data |
| Readers act on absolute claims more than scoped ones | Inferred / unmeasured | General assumption, no data collected in this benchmark |
| Which exact announcement wording ships | Unmeasured | No announcement text exists yet; this task edits a *hypothetical* one |

## Unsupported Claims

Do not publish these in either language (each would exceed the evidence):

- "mission is smarter/better than Claude Code official `/goal`." — No completed
  multi-task paired Claude Code comparison exists; only N=1 smoke.
- 「mission は Claude Code 公式 `/goal` より賢い/優れている」 — same, in Japanese.
- "mission improves completion or validator pass rate." — Both arms hit 100% in
  the Codex pilot; delta is 0.
- 「mission は完了率を改善する」 — unsupported for the same reason.
- "These results generalize to your workflow / to model intelligence." — Pilot
  is a fixed internal task mix with automated scoring.
- 「この結果は一般的なワークフローに当てはまる」 — unsupported.
- Any Japanese sentence that states a quality delta without the "automated
  heuristic / ではない" qualifier that the English keeps.
- Any "faster and cheaper" claim stated unconditionally — it is true only for
  the single N=1 light task and must carry "N=1" / 「N=1」 in both languages.

## Unresolved Translation Choices

These are genuinely undecided and must go to a bilingual reviewer, not be
silently resolved by the writer:

1. "controlled local pilot" — keep English term, or render 「限定的なローカル検証」?
   Risk: a smooth Japanese noun can sound more official/finished than "pilot".
   Current reports keep the English term; recommend keeping it until decided.
2. "automated heuristic score" — 「自動ヒューリスティック評価」 reads as more
   rigorous than "heuristic" implies. Unresolved whether to add 「簡易」/"rough".
3. "blocked" (API/budget) vs "failed" — Japanese 「失敗」 wrongly implies a
   quality failure. Must render as 「（API 上限により）中断」, not 「失敗」. Flagged
   but the exact phrasing is unresolved.
4. "did not improve" — Japanese 「改善しなかった」 can read as "got worse".
   Preferred neutral form 「差が出なかった」 is unresolved.
5. Whether the Japanese announcement repeats the full disclaimer inline or links
   to `report.ja.md`. Unresolved; affects whether hedges travel with each claim.

## Evidence Map

| Announcement claim | Language(s) | Evidence source | Verifiable? |
|---|---|---|---|
| Study is a controlled local pilot | EN + JP | `report.md` / `report.ja.md` headers | Yes, in-repo |
| 20/20 Codex paired runs, 100% both arms | EN + JP | `report.md` Executive Summary + Results | Yes, in-repo |
| Quality/evidence/time deltas (Codex) | EN + JP | `report.md` Results table | Yes, in-repo |
| Scores are automated heuristic | EN + JP | `report.md` header + scope row | Yes, in-repo |
| Claude Code `/goal` only N=1 completed | EN + JP | `report.md` light-v1 + blocked sections | Yes, in-repo |
| JP-hedge-loss risk | EN + JP | Reasoning only | No — inferred, labeled as such |
| Reader behavior on absolute claims | EN + JP | none | No — unmeasured, labeled as such |

## Reviewer Gate

Ship only when all pass. Reviewer must be bilingual (EN/JP).

- [ ] Every measured number in the announcement matches `report.md` /
      `report.ja.md` exactly (no rounding drift between languages).
- [ ] Every English hedge ("controlled", "local", "not a general benchmark",
      "automated heuristic", "N=1") has a surviving Japanese counterpart.
- [ ] No Unsupported Claim (section above) appears in either language.
- [ ] "blocked" is rendered as 「中断」, never 「失敗」.
- [ ] Each Unresolved Translation Choice is either decided-with-sign-off or left
      out of the shipped copy — not guessed.
- [ ] Inferred/unmeasured statements are not presented as measured in either
      language.
- [ ] Human sign-off recorded before publish (announcement is outward-facing).

## Residual Risk

- Even a passing parity check cannot prove reader *perception* parity; that is
  unmeasured. The gate reduces, not eliminates, the JP-stronger-than-EN risk.
- The hypothetical announcement text is not fixed here, so future edits could
  reintroduce drift after this review — the gate must be re-run on the final
  copy.
- The Claude Code `/goal` evidence base is thin (N=1 completed). If the
  announcement mentions Claude Code at all, the "smoke only" caveat carries most
  of the safety load and is easy to trim for length; that trim is the single
  highest-risk edit in either language.
- This artifact is a claim-consistency kit, not a measurement; it does not add
  new benchmark data and makes no superiority claim.

# Evidence

- `benchmarks/mission-vs-goal/report.md` (read 2026-07-03): Codex pilot Results
  table (completion 10/10 both arms; quality 4.00 vs 4.50; evidence 3.80 vs
  4.70; elapsed 1.28 vs 2.99 min), automated-heuristic disclaimer, and the
  Claude Code `/goal` smoke/blocked/light sections (light-v1 N=1 tie at
  4.00/4.00; complex-v1 20/20 blocked; incremental-v1 max-budget blocked).
- `benchmarks/mission-vs-goal/report.ja.md` (read 2026-07-03): structurally
  parallel Japanese report carrying the same numbers and disclaimers, used as
  the parity baseline.
- `benchmarks/mission-vs-goal/artifacts/2026-06-27-codex-cli-local/marketing-claim-draft-goal_only/artifact.md`:
  format reference (Goal/Result/Evidence/Assumptions structure) and precedent
  for conservative, scope-limited claims.
- Inferred items (JP hedge-loss, reader behavior) are labeled "Inferred" /
  "unmeasured" above and are not backed by collected data in this run.

# Assumptions

- The announcement is *hypothetical*; no shipping copy exists, so this artifact
  delivers the review kit and parity rules rather than final published text.
- The English report is the source of truth; the Japanese copy must not exceed
  it. If English is itself over-claiming, that is out of scope for this task and
  should be raised separately.
- "Preserve measurement limits" means keeping scope words, hedges, and the
  automated-heuristic caveat intact across both languages.
- A bilingual human reviewer is available to resolve the flagged translation
  choices; this artifact does not resolve them unilaterally.
- Neutral, non-personal fixture naming and OSS-portable framing are kept per
  repo guardrails; no benchmark-superiority claim is asserted.

# Stop Condition

Stop when the artifact exists at
`benchmarks/mission-vs-goal/run-output/2026-07-03-claude-goal-vs-mission-quality-light-v1/quality-bilingual-claim-consistency-claude_code_goal_command.md`
with the headings Goal, Result, Evidence, Assumptions, and Stop Condition, and
covers English/Japanese parity checks, measured-vs-inferred claims, unsafe
claims to avoid, and unresolved translation choices (plus Evidence Map, Reviewer
Gate, and Residual Risk). All quality markers are covered above and every claim
is tied to an in-repo source or explicitly labeled inferred/unmeasured, so the
`/goal` completion condition is met and this is the sole deliverable — no commit,
push, install, or network action is taken.
