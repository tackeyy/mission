# mission vs goal-only pilot report

Status: measured on 2026-06-27 as a controlled local Codex CLI pilot.
Additional Claude Code official `/goal` attempts were run on 2026-06-28 JST
and are reported separately below. A tail-cohort run
(`2026-07-07-claude-goal-vs-mission-tail-v1`, 5 planted-defect tasks, both arms)
completed on 2026-07-07 JST and is reported in the Tail Cohort section below.

This is not a general model benchmark and not a blind human evaluation. The
quality and evidence scores below are automated heuristic scores from the local
runner. Raw records and artifacts are checked into this directory so the result
can be audited.

## Executive Summary

We ran all 20 paired benchmark executions from starting commit `0148f16`: 10
`goal_only` runs and 10 `mission` runs.

Both arms completed every task and passed every local validator. In this
controlled pilot, the `mission` arm produced higher automated quality and
evidence scores, while taking more wall-clock time.

The safe takeaway is narrow:

> In this controlled local 10-task pilot, `mission` did not improve completion
> or validator pass rate because both arms reached 100%. It did improve the
> automated evidence/completion-quality score, at the cost of longer runtime.

## Measurement Scope

| Item | Value | Evidence |
|---|---:|---|
| Measurement date | 2026-06-27 | Current local Codex CLI run. |
| Run id | `2026-06-27-codex-cli-local` | `results/2026-06-27-codex-cli-local.jsonl`. |
| Starting commit | `0148f16` | `git ls-remote origin refs/heads/main` before pilot. |
| Fixed pilot tasks defined | 10 / 10 | `benchmarks/mission-vs-goal/tasks.json`. |
| Benchmark arms defined | 2 / 2 | `goal_only`, `mission`. |
| Expected paired runs | 20 | 10 tasks x 2 arms. |
| Paired benchmark runs completed | 20 / 20 | Raw JSONL has 20 records. |
| Goal-only runs completed | 10 / 10 | Raw JSONL records. |
| Mission runs completed | 10 / 10 | Raw JSONL records. |
| Result artifacts captured | 100 files | `artifacts/2026-06-27-codex-cli-local/`. |
| Quality score method | automated heuristic | `quality_score_method=automated_heuristic_not_blind_human`. |
| Comparative performance claim readiness | limited | Only valid for this controlled local task set and scoring method. |

## Results

| Metric | goal_only | mission | Delta |
|---|---:|---:|---:|
| Completion rate | 10 / 10 | 10 / 10 | 0 |
| Validator pass rate | 10 / 10 | 10 / 10 | 0 |
| Average quality score | 4.00 / 5 | 4.50 / 5 | +0.50 |
| Average intervention count | 0.00 | 0.00 | 0 |
| Resume success rate | 1 / 1 | 1 / 1 | 0 |
| Average evidence completeness | 3.80 / 5 | 4.70 / 5 | +0.90 |
| Average elapsed minutes | 1.28 | 2.99 | +1.71 |

## Claude Code Official `/goal` Smoke

Status: attempted on 2026-06-28 JST as a controlled Claude Code CLI print-mode
smoke. The comparison target is the official Claude Code built-in `/goal`
command, not the earlier local `goal_only` baseline. Claude Code command and
skill documentation are published at <https://code.claude.com/docs/en/commands>
and <https://code.claude.com/docs/en/skills>.

This smoke produced evidence, but it is **not** a completed performance
comparison: the `/mission` arm stopped before writing its task artifact because
Claude Code returned workspace API usage limit error 400. The raw error states
that access resumes on 2026-07-01 at 00:00 UTC.

| Item | Value | Evidence |
|---|---:|---|
| Measurement date | 2026-06-28 JST | Raw records completed at 2026-06-27T16:34:57Z. |
| Run id | `2026-06-28-claude-goal-vs-mission-smoke-v2` | `results/2026-06-28-claude-goal-vs-mission-smoke-v2.jsonl`. |
| Starting commit | `38cc7907e5e35fcd9fa23022a1fcf03f756df99b` | Runner argument. |
| Task file | `tasks.complex.json` | One smoke task: `complex-cross-file-feature`. |
| Arms | 2 | `claude_code_goal_command`, `mission`. |
| Records completed | 2 / 2 | Summary JSON has two records. |
| `/goal` artifact completion | 1 / 1 | Artifact exists and validator heuristic passed. |
| `/mission` artifact completion | 0 / 1 | No artifact; API usage limit stopped the run. |
| `/goal` cost | USD 0.93959825 | Raw Claude result JSON. |
| `/mission` cost before stop | USD 1.05234325 | Raw Claude result JSON. |
| Quality score method | automated heuristic | Not blind human review. |
| Marketing comparison readiness | blocked | The `/mission` arm did not receive a full comparable attempt. |

Smoke result:

| Metric | claude_code_goal_command | mission | Interpretation |
|---|---:|---:|---|
| Completion rate | 1 / 1 | 0 / 1 | `/mission` failed due to workspace API limit, not a validated task-quality failure. |
| Validator pass rate | 1 / 1 | 0 / 1 | The `/mission` validator could not run because no artifact was written. |
| Average quality score | 4.00 / 5 | 1.00 / 5 | Automated placeholder score from artifact presence; not a capability conclusion. |
| Average evidence completeness | 4.00 / 5 | 1.00 / 5 | Same limitation as above. |
| Average elapsed minutes | 1.97 | 2.06 | `/mission` elapsed time ended at API-limit stop. |

Safe interpretation:

> The official Claude Code `/goal` smoke harness ran one complex task and wrote
> an auditable artifact. The comparable `/mission` arm was blocked by Claude
> Code workspace API limits before artifact completion, so this smoke does not
> support a marketing claim that either arm is better.

In short, this smoke does not support a marketing claim that either arm is better.

Unsafe interpretation:

> `mission` lost to official `/goal`.

That would be unsupported because the failed `/mission` record is an
infrastructure/API-limit stop, not a completed task-quality measurement.

Follow-up preparation completed after this smoke:

- `run_claude_goal_vs_mission.py` now records `run_status`,
  `blocked_reason`, `failure_kind`, and `comparable_attempt` for future runs.
- `official-goal-rerun-runbook.md` defines the 2026-07-01 09:00 JST smoke gate
  and full paired pilot procedure.
- Future reports must separate `completed`, `failed`, and `blocked` records
  before making any capability claim.

## Claude Code Official `/goal` Rerun After API Limit Increase

Status: executed on 2026-06-28 JST after the Claude API limit was increased.
This produced one completed comparable smoke task, then a 10-task full attempt
that hit Claude Code workspace API usage limits. The full attempt is therefore
not a completed performance comparison.

### Smoke gate

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-06-28-claude-goal-vs-mission-smoke-v3` | `results/2026-06-28-claude-goal-vs-mission-smoke-v3.jsonl`. |
| Starting commit | `ed98b0e00169f0e0b35ce629a206ffcb7af4d0a3` | Runner argument. |
| Task file | `tasks.complex.json` | One smoke task: `complex-cross-file-feature`. |
| Records completed | 2 / 2 | Summary JSON has two records. |
| Blocked records | 0 / 2 | Both records have `run_status=completed`. |
| Quality score method | automated heuristic | Not blind human review. |
| Total Claude cost recorded | USD 3.78852475 | Raw Claude result JSON files. |
| Marketing comparison readiness | smoke-only | One comparable task is too small for broad performance claims. |

Smoke result:

| Metric | claude_code_goal_command | mission | Interpretation |
|---|---:|---:|---|
| Completed comparable records | 1 / 1 | 1 / 1 | Both arms completed the same smoke task. |
| Completion rate | 1 / 1 | 1 / 1 | Tie on the single measured task. |
| Validator pass rate | 1 / 1 | 1 / 1 | Tie on the single measured task. |
| Average quality score | 4.00 / 5 | 4.00 / 5 | Automated heuristic score; not blind human review. |
| Average evidence completeness | 4.00 / 5 | 4.00 / 5 | Tie on the single measured task. |
| Average elapsed minutes | 1.70 | 6.50 | `/mission` took 4.80 minutes longer in this smoke. |

Safe interpretation:

> After the API limit increase, a one-task Claude Code official `/goal` vs
> `/mission` smoke completed on both arms. Both arms passed completion and
> validator checks under the automated heuristic scorer; `/mission` took longer.

Unsafe interpretation:

> `mission` is better than official `/goal`.

This remains unsupported because the completed comparable sample is one task.

### Full 10-task attempt

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-06-28-claude-goal-vs-mission-complex-v1` | `results/2026-06-28-claude-goal-vs-mission-complex-v1.jsonl`. |
| Starting commit | `ed98b0e00169f0e0b35ce629a206ffcb7af4d0a3` | Runner argument. |
| Task file | `tasks.complex.json` | 10 complex tasks. |
| Expected records | 20 | 10 tasks x 2 arms. |
| Records written | 20 / 20 | Summary JSON has 20 records. |
| Completed comparable records | 0 / 20 | Every record has `run_status=blocked`. |
| Blocked records | 20 / 20 | Every record has `blocked_reason=api_usage_limit`. |
| Total Claude cost recorded | USD 0.81484175 | Raw Claude result JSON files. |
| Marketing comparison readiness | blocked | No comparable full-run task-quality attempt completed. |

Full attempt result:

| Metric | claude_code_goal_command | mission | Interpretation |
|---|---:|---:|---|
| Records | 10 | 10 | Full attempt wrote the expected record count. |
| Completed comparable records | 0 / 10 | 0 / 10 | All records were API-limit blocked. |
| Blocked records | 10 / 10 | 10 / 10 | Not a task-quality result. |
| Comparable completion rate | n/a | n/a | Denominator is zero after excluding blocked records. |
| Comparable validator pass rate | n/a | n/a | Denominator is zero after excluding blocked records. |

Safe interpretation:

> The one-task smoke became comparable after the API limit increase, but the
> full 10-task Claude Code official `/goal` vs `/mission` run still exhausted
> workspace API usage limits. The full run does not support a marketing claim
> about which arm is better.

Unsafe interpretation:

> Both arms failed all 10 complex tasks.

That would be unsupported because every full-run record was blocked by
workspace API usage limits before a comparable task-quality attempt.

## Cost-Controlled Incremental Rerun

Status: executed on 2026-06-28 JST after an additional Claude API budget increase.
To avoid rerunning the already measured first smoke task, the runner was updated
with `--task-ids` and `--stop-on-blocked`. The incremental run targeted two
previously unmeasured complex tasks and capped each Claude invocation with
`--max-budget-usd 3.0`.

This is a useful cost-capped operational comparison, but it is **not** a full
quality comparison for `mission`: both `/mission` records hit the configured
per-invocation budget cap before Claude Code returned success.

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-06-28-claude-goal-vs-mission-incremental-v1` | `results/2026-06-28-claude-goal-vs-mission-incremental-v1.jsonl`. |
| Starting commit | `d1cef1d5bd0166b5d61939c8d93ce0060c05507f` | Runner argument. |
| Task file | `tasks.complex.json` | Selected tasks only. |
| Selected tasks | 2 | `complex-failing-test-triage`, `complex-review-thread-resolution`. |
| Expected records | 4 | 2 tasks x 2 arms. |
| Records written | 4 / 4 | Summary JSON has 4 records. |
| Per-invocation cost cap | USD 3.00 | Runner argument `--max-budget-usd 3.0`. |
| API usage-limit blocked records | 0 / 4 | No record has `blocked_reason=api_usage_limit`. |
| Max-budget blocked records | 2 / 4 | Both `/mission` records have `blocked_reason=max_budget_usd`. |
| Total Claude cost recorded | USD 9.39057695 | Raw Claude result JSON files. |
| `/goal` cost recorded | USD 3.31969425 | Sum of `/goal` raw Claude result JSON files. |
| `/mission` cost recorded | USD 6.07088270 | Sum of `/mission` raw Claude result JSON files. |

Incremental result:

| Metric | claude_code_goal_command | mission | Interpretation |
|---|---:|---:|---|
| Records | 2 | 2 | Same two selected tasks were attempted. |
| Completed comparable records | 2 / 2 | 0 / 2 | `/mission` records are excluded from task-quality comparison because they hit the configured budget cap. |
| Completion rate | 2 / 2 | 0 / 2 | Under the USD 3.00 cap, `/goal` completed both tasks; `/mission` did not return success. |
| Validator pass rate | 2 / 2 | 0 / 2 | `/mission` artifacts existed, but Claude Code returned `error_max_budget_usd`, so the runner did not count them complete. |
| Average elapsed minutes | 3.87 | 7.56 | `/mission` ran longer before hitting the budget cap. |
| Average quality score | 4.00 / 5 | n/a | `/mission` comparable denominator is zero after excluding max-budget blocked records. |

Safe interpretation:

> In a cost-capped incremental run with a USD 3.00 per-invocation cap, official
> `/goal` completed and passed both selected complex tasks. `/mission` produced
> artifacts but hit the configured Claude Code max-budget cap on both selected
> tasks, so those records are budget-blocked rather than completed task-quality
> measurements.

Unsafe interpretation:

> `mission` produced lower-quality answers than official `/goal`.

That would be unsupported because the two `/mission` incremental records ended
with `error_max_budget_usd`, not a completed validator result.

## Lightweight Mission Profile Rerun

Status: executed on 2026-06-28 JST to test whether `/mission` can be compared
under a smaller cost envelope. The runner now supports `--mission-profile light`,
which keeps the `/mission` prompt to one concise plan/write/check pass and uses
`--mission-max-iter 1`.

This is the first completed cost-controlled comparison where both official
`/goal` and `/mission` completed the same previously unmeasured complex task.
It is still only N=1, so it supports a profile-level hypothesis, not a broad
performance claim.

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-06-28-claude-goal-vs-mission-light-v1` | `results/2026-06-28-claude-goal-vs-mission-light-v1.jsonl`. |
| Starting commit | `d6f12d739a521046e1dc1d198e671a162a803e9c` | Runner argument. |
| Task file | `tasks.complex.json` | Selected task only. |
| Selected task | 1 | `complex-failing-test-triage`. |
| Mission profile | `light` | `mission_profile=light`, `--mission-max-iter 1`. |
| Expected records | 2 | 1 task x 2 arms. |
| Records written | 2 / 2 | Summary JSON has 2 records. |
| Blocked records | 0 / 2 | Both records have `run_status=completed`. |
| Total Claude cost recorded | USD 5.01240250 | Raw Claude result JSON files. |
| `/goal` cost recorded | USD 3.00670750 | `/goal` raw Claude result JSON. |
| `/mission` light cost recorded | USD 2.00569500 | `/mission` raw Claude result JSON. |

Lightweight result:

| Metric | claude_code_goal_command | mission light | Interpretation |
|---|---:|---:|---|
| Completed comparable records | 1 / 1 | 1 / 1 | Both arms completed the same selected task. |
| Completion rate | 1 / 1 | 1 / 1 | Tie on this task. |
| Validator pass rate | 1 / 1 | 1 / 1 | Tie on this task. |
| Average quality score | 4.00 / 5 | 4.00 / 5 | Automated heuristic score, not blind human review. |
| Average evidence completeness | 4.00 / 5 | 4.00 / 5 | Tie on this task. |
| Average elapsed minutes | 9.56 | 5.27 | `mission` light was 4.29 minutes faster on this task. |
| Recorded Claude cost | USD 3.00670750 | USD 2.00569500 | `mission` light used USD 1.00101250 less on this task. |

Safe interpretation:

> On one previously unmeasured complex triage task, a lightweight `/mission`
> profile and official `/goal` both completed and passed the automated
> validator. In this single measured task, `mission` light was faster and lower
> cost than official `/goal`.

Unsafe interpretation:

> `mission` is cheaper and faster than official `/goal`.

That would be unsupported because the completed light-profile sample is one
task. The defensible next step is to run a 3-5 task light-profile pilot with the
same per-invocation cost cap and no recycled tasks.

## Lightweight Mission Profile Rerun on Quality Cohort

Status: executed on 2026-07-03 JST to extend the light-profile evidence from
N=1 to three additional paired tasks. This run used the quality-critical cohort
with `--mission-profile light`, not the heavier `quality` profile. The goal was
to compare official `/goal` and `/mission` light under a bounded budget while
preserving quality-marker scoring.

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-07-03-claude-goal-vs-mission-quality-light-v1` | `results/2026-07-03-claude-goal-vs-mission-quality-light-v1.jsonl`. |
| Starting commit | `1863e02b4cb49bc78399d423ad82c2176627ecdd` | Runner argument. |
| Task file | `tasks.quality.json` | Quality-critical cohort, run with light mission profile. |
| Selected tasks | 3 | `quality-multi-cause-regression-triage`, `quality-security-secret-handling-plan`, `quality-bilingual-claim-consistency`. |
| Mission profile | `light` | `--mission-profile light`, `--mission-max-iter 1`. |
| Expected records | 6 | 3 tasks x 2 arms. |
| Records written | 6 / 6 | Summary JSON has 6 records. |
| Blocked records | 0 / 6 | No record has `blocked_reason`. |
| Quality score method | automated heuristic | Not blind human review. |
| Total Claude cost recorded | USD 7.51945750 | Raw Claude result JSON files. |
| `/goal` cost recorded | USD 3.11103025 | Sum of `/goal` raw Claude result JSON files. |
| `/mission` light cost recorded | USD 4.40842725 | Sum of `/mission` raw Claude result JSON files. |

Task-level result:

| Task | Arm | Completion | Validator pass | Human quality score | Quality marker score | Cost | Elapsed | Blocked / failure reason |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `quality-multi-cause-regression-triage` | `claude_code_goal_command` | true | true | 5.00 | 1.00 | USD 1.17585050 | 3.10 min | none |
| `quality-multi-cause-regression-triage` | `mission` light | true | true | 5.00 | 1.00 | USD 1.66316525 | 3.58 min | none |
| `quality-security-secret-handling-plan` | `claude_code_goal_command` | true | true | 5.00 | 1.00 | USD 0.74217350 | 1.98 min | none |
| `quality-security-secret-handling-plan` | `mission` light | true | true | 5.00 | 1.00 | USD 1.35611750 | 3.06 min | none |
| `quality-bilingual-claim-consistency` | `claude_code_goal_command` | true | true | 5.00 | 1.00 | USD 1.19300625 | 2.37 min | none |
| `quality-bilingual-claim-consistency` | `mission` light | true | true | 5.00 | 1.00 | USD 1.38914450 | 2.78 min | none |

Aggregate result:

| Metric | claude_code_goal_command | mission light | Interpretation |
|---|---:|---:|---|
| Completed comparable records | 3 / 3 | 3 / 3 | Both arms completed all selected tasks. |
| Completion rate | 3 / 3 | 3 / 3 | Tie on completion. |
| Validator pass rate | 3 / 3 | 3 / 3 | Tie on validator pass. |
| Average quality score | 5.00 / 5 | 5.00 / 5 | Tie under automated heuristic scoring. |
| Average quality marker score | 1.00 | 1.00 | Tie; all tracked quality markers were matched. |
| Average elapsed minutes | 2.48 | 3.14 | `/mission` light was 0.66 minutes slower on average. |
| Recorded Claude cost | USD 3.11103025 | USD 4.40842725 | `/mission` light cost USD 1.29739700 more across the three tasks. |

Safe interpretation:

> On three additional quality-critical tasks run with the light profile, official
> `/goal` and `/mission` light both completed every task, passed every automated
> validator, and matched all configured quality markers. In this run, `/mission`
> light was slower and cost more than official `/goal`.

Unsafe interpretation:

> `/mission` light is always higher quality than official `/goal`.

That is unsupported. The automated quality and marker scores tied in this
three-task run, and the scorer is a heuristic rather than blind human review.

## Tail Cohort Run (tail-first-failure)

Status: executed on 2026-07-07 JST. This is the first completed run using the
`tasks.tail.json` cohort (5 planted-defect tasks). Both arms used model
`claude-sonnet-5` via a PATH shim that injected `--model claude-sonnet-5` into
every `claude` invocation; `modelUsage` in each `claude-result.json` confirms
`claude-sonnet-5` on all records.

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-07-07-claude-goal-vs-mission-tail-v1` | `results/2026-07-07-claude-goal-vs-mission-tail-v1.jsonl`. |
| Starting commit | `3591f9947cddb9028538f8d333a0eeb6545b726e` | Runner argument. |
| Task file | `tasks.tail.json` | Planted-defect tail cohort, 5 tasks. |
| Mission profile | `full` | Default workflow; no `--mission-profile` override. |
| Expected records | 10 | 5 tasks x 2 arms. |
| Records written | 10 / 10 | JSONL has 10 records. |
| Blocked records | 0 / 10 | All records have `run_status=completed`. |
| Quality score method | `automated_heuristic_form_stripped_not_blind_human` | Automated heuristic; not blind human review. |
| model_id | `claude-sonnet-5` | All 10 records. |
| Total Claude cost recorded | USD 23.6366052 | Sum of both arms across 5 tasks. |
| `/goal` cost recorded | USD 2.8077291 | Sum of 5 goal records. |
| `/mission` cost recorded | USD 20.8288761 | Sum of 5 mission records. |

Task-level result:

| Task | Arm | Completion | Validator pass | Quality score | Marker score | Forbidden hits | Cost | Elapsed |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `tail-config-spec-drift` | `claude_code_goal_command` | true | true | 4.86 | 0.86 | 0 | USD 0.49555495 | 1.88 min |
| `tail-config-spec-drift` | `mission` | true | true | 4.86 | 0.86 | 0 | USD 2.62527390 | 8.78 min |
| `tail-incident-log-triage` | `mission` | true | true | 5.00 | 1.00 | 0 | USD 5.53930740 | 18.87 min |
| `tail-incident-log-triage` | `claude_code_goal_command` | true | true | 5.00 | 1.00 | 0 | USD 0.51823745 | 1.90 min |
| `tail-bilingual-release-drift` | `claude_code_goal_command` | true | true | 5.00 | 1.00 | 0 | USD 0.56775820 | 2.22 min |
| `tail-bilingual-release-drift` | `mission` | true | true | 5.00 | 1.00 | 0 | USD 3.66627435 | 13.70 min |
| `tail-metrics-reconciliation` | `mission` | true | true | 5.00 | 1.00 | 0 | USD 3.63863340 | 11.58 min |
| `tail-metrics-reconciliation` | `claude_code_goal_command` | true | true | 5.00 | 1.00 | 0 | USD 0.66852725 | 3.26 min |
| `tail-dependency-upgrade-impact` | `claude_code_goal_command` | true | true | 5.00 | 1.00 | 0 | USD 0.55765120 | 2.37 min |
| `tail-dependency-upgrade-impact` | `mission` | true | true | 5.00 | 1.00 | 0 | USD 5.35938705 | 14.42 min |

Aggregate result:

| Metric | claude_code_goal_command | mission | Interpretation |
|---|---:|---:|---|
| Completed comparable records | 5 / 5 | 5 / 5 | Both arms completed all 5 tasks. |
| Completion rate | 5 / 5 | 5 / 5 | Tie on completion. |
| Validator pass rate | 5 / 5 | 5 / 5 | Tie on validator pass. |
| Average quality score | 4.97 / 5 | 4.97 / 5 | Tie under automated heuristic scoring. |
| Average quality marker score | 0.97 | 0.97 | Tie; one task drove both arms below 1.0. |
| Forbidden marker hits | 0 | 0 | No decoy false positives on either arm. |
| Total elapsed minutes | 11.63 | 67.35 | `/mission` ran ~5.8x longer in wall-clock time. |
| Average elapsed minutes | 2.33 | 13.47 | Per-task average. |
| Total recorded Claude cost | USD 2.8077291 | USD 20.8288761 | `/mission` cost ~7.4x more. |

The `/mission` arm ran the full review loop — plan, review, score, iteration gate — on
all five tasks. The internal composite score produced by `mission-state.py aggregate-reviews`
and `push-score` cleared the 4.0 pass gate at iteration 1 in all five runs:
`tail-config-spec-drift` (4.29), `tail-incident-log-triage` (4.53),
`tail-bilingual-release-drift` (4.54), `tail-metrics-reconciliation` (5.00), and
`tail-dependency-upgrade-impact` (4.28). No second iteration was triggered in any run.

**Marker false negative and secondary re-score (secondary analysis; primary JSONL unchanged):**
On `tail-config-spec-drift`, both arms' artifacts correctly identified the
`health_check_interval_s` drift (spec 15 vs beta 75). The goal artifact contains
`` `HEALTH_CHECK_INTERVAL_SECONDS=75` `` and the mission artifact contains
`` `75` (same unit, seconds) ``, but the six configured patterns
(`"75 seconds"`, `"75s"`, `"= 75"`, `": 75"`, `"75 vs 15"`, `"15 vs 75"`) failed to
substring-match either phrasing. The marker pattern has been extended in
`tasks.tail.json` to also include `"seconds=75"`, `"(75"`, and `` "75`" ``.
Applying the updated task definitions to the 10 saved artifact files gives:
`tail-config-spec-drift` both arms 0.86 → **1.0** (marker now matched); all other
eight records unchanged at 1.0. Re-scored averages: both arms 0.97 → **1.0**,
re-scored quality scores both 4.97 → **5.00**. The primary JSONL scores remain
as-recorded (0.86 / 4.86 for `tail-config-spec-drift`); these are the
secondary-analysis figures.

Safe interpretation:

> On five planted-defect tasks designed as first-pass recall challenges, both
> arms tied on all automated content-recall metrics under the primary scorer, with
> zero decoy false positives on either arm. The `/mission` arm ran its full
> plan-review-score loop, cleared its own 4.0 pass gate at iteration 1 in all
> five runs, and cost ~5.8x more wall-clock time and ~7.4x more in recorded USD
> than the `/goal` arm. N=5, one model, closed-world fixtures where all evidence
> fits in a few short files; production case studies (`docs/CASE_STUDIES.md`)
> document the open-world tail where the gate binds.

Unsafe interpretation:

> `mission` is worse than `/goal` because it cost more with the same score.

> The review loop is useless because it did not improve the automated score.

Neither is supported: N=5 on a closed-world fixture set where both arms could
find the same short files; the designed first-pass failures did not reproduce in
this run. The `/mission` arm demonstrably ran the full loop and passed its own
gate — the gate is a correctness guard that can bind on open-world work even
when it did not bind here.

### Pattern-Fix Validation Smoke (2026-07-10)

Status: run on 2026-07-10 JST. This is a single-task smoke to validate that the marker
pattern fix applied after the tail-v1 run (adding `"seconds=75"`, `"(75"`, and `` "75`" ``
to the `"Drift: health interval 75s"` patterns in `tasks.tail.json`) resolves the false
negative observed in v1. In v1, both arms correctly identified the
`health_check_interval_s` drift but none of the six original patterns matched the artifact
text. This smoke re-runs only `tail-config-spec-drift` against the updated pattern set.

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-07-10-claude-goal-vs-mission-tail-smoke-v2` | `results/2026-07-10-claude-goal-vs-mission-tail-smoke-v2.jsonl`. |
| Starting commit | `4fdb222b6073cef22676206625ccc61b83c9f658` | Runner argument. |
| Task file | `tasks.tail.json` | Planted-defect tail cohort. |
| Task | `tail-config-spec-drift` | Single task; N=1 per arm. |
| Mission profile | `full` | Default workflow. |
| Expected records | 2 | 1 task x 2 arms. |
| Records written | 2 / 2 | JSONL has 2 records. |
| Blocked records | 1 / 2 | Mission arm: `blocked_reason=api_usage_limit`. |
| Comparable records | 1 / 2 | Goal arm completed; mission arm excluded. |
| Quality score method | `automated_heuristic_form_stripped_not_blind_human` | Automated heuristic; not blind human review. |
| model_id | `claude-sonnet-5` | Both records. |
| Total Claude cost recorded | USD 4.27159565 | Sum of both arms. |

Result:

| Arm | Completion | Validator pass | Marker score | Forbidden hits | Cost | Elapsed | Comparable |
|---|---:|---:|---:|---:|---:|---:|---:|
| `claude_code_goal_command` | true | true | 1.00 | 0 | USD 0.57630950 | 1.76 min | yes |
| `mission` | false | false | null (blocked) | 0 | USD 3.69528615 | 11.80 min | no |

Artifact evidence (mission arm): although the `/mission` run ended with
`run_status=blocked` (`api_usage_limit`), the partially written `artifact.md` contains
`HEALTH_CHECK_INTERVAL_SECONDS=75`, which matches the new pattern `"seconds=75"`.
The marker was found in the artifact content; the block occurred before Claude Code
returned success.

v1 comparison (task: `tail-config-spec-drift`):

| Metric | v1 (2026-07-07) | smoke-v2 (2026-07-10) |
|---|---:|---|
| Goal arm marker score | 0.86 (6 / 7) | 1.00 (7 / 7) — pattern fix confirmed |
| Mission arm marker score | 0.86 (6 / 7) | null / blocked — not comparable |

Safe interpretation:

> The `/goal` arm completed the re-run and matched all 7 quality markers (score 1.00),
> including `"Drift: health interval 75s"`, which was missed by the primary scorer in v1.
> The pattern fix (`"seconds=75"`, `"(75"`, `` "75`" ``) resolves the false negative on
> the comparable arm. The `/mission` arm was blocked by a workspace API usage limit before
> returning success, so its marker score is not comparable; artifact inspection shows the
> health-interval drift was correctly identified in the partial output. N=1 task, one model,
> one comparable arm.

Unsafe interpretation:

> The pattern fix proves the false negative is fully resolved on both arms.

That is unsupported for the mission arm: the block stopped the mission run before a
comparable validator result, and a single run per arm does not guarantee the result on
future executions.

## Quality-Focused Critical Task Attempt

Status: attempted on 2026-06-28 JST after adding a `quality` mission profile and
a fresh `tasks.quality.json` cohort. This profile is designed to test the task
types where `/mission` should have the best chance to show higher quality:
evidence maps, rejected hypotheses, stop/proceed decisions, residual risks, and
unsafe-claim control.

The paired comparison did **not** complete. The official `/goal` arm hit Claude
Code workspace API usage limits before returning success, so the `/mission` arm
was not run. This is a blocked infrastructure/account result, not evidence that
either arm produced higher quality.

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-06-28-claude-goal-vs-mission-quality-v1` | `results/2026-06-28-claude-goal-vs-mission-quality-v1.jsonl`. |
| Starting commit | `e443bb421cdc58418085790b1fb6733dd3ef89f5` | Runner argument. |
| Task file | `tasks.quality.json` | Fresh quality-critical cohort. |
| Selected task | 1 | `quality-critical-release-governance`. |
| Mission profile | `quality` | `--mission-profile quality`, `--mission-max-iter 2`. |
| Expected records | 2 | 1 task x 2 arms. |
| Records written | 1 / 2 | stopped early after `/goal` blocked. |
| Blocked records | 1 / 1 | `/goal` has `blocked_reason=api_usage_limit`. |
| `/mission` records | 0 | Not run because `--stop-on-blocked` conserved API budget. |
| `/goal` cost before stop | USD 1.01481150 | raw Claude result JSON. |
| Quality-marker comparison | unavailable | blocked records are excluded from comparable quality-marker aggregates. |

Safe interpretation:

> A quality-focused benchmark profile and fresh critical task cohort now exist,
> but the first official `/goal` vs `/mission` quality attempt was blocked by
> Claude Code workspace API usage limits before a comparable pair completed.

Unsafe interpretation:

> `mission` quality is higher than official `/goal` on the quality-critical task.

That is unsupported. The task was not paired through both arms.

## Task-Level Findings

| Task | Stronger arm | Why |
|---|---|---|
| `docs-small-edit` | `mission` on evidence; tie on completion | Both passed; mission recorded more structured evidence and state. |
| `docs-cross-reference` | `mission` on evidence; tie on completion | Both passed; mission included plan/review/score sections. |
| `bug-regression-test` | `mission` on evidence; tie on completion | Both passed; mission had stronger completion trace but longer runtime. |
| `review-comment-batch` | `mission` on evidence; tie on completion | Both passed; mission made review/check evidence explicit. |
| `interrupted-doc-task` | tie on resume; `mission` on evidence | Both resume validators passed; mission recorded state-backed evidence. |
| `ambiguous-research-brief` | `mission` on evidence; tie on completion | Both passed; mission surfaced assumptions and review more explicitly. |
| `release-checklist-audit` | `mission` on evidence; tie on completion | Both passed; mission better matched evidence-tracking intent. |
| `small-refactor` | `mission` on evidence; tie on completion | Both passed; goal-only remained sufficient for validator success. |
| `quality-gate-failure` | `mission` on evidence; tie on completion | Both passed; mission represented the stop decision more explicitly. |
| `marketing-claim-draft` | `mission` on evidence; tie on completion | Both passed; mission made claim guardrails more explicit. |

## Validation Results

| Check | Result |
|---|---:|
| Benchmark + doc consistency tests | 30 passed / 30 |
| Full mission test suite | 402 passed / 402 |
| JSON parse checks | 2 passed / 2 |
| Scoped whitespace check | passed |

Commands used:

```bash
python3 benchmarks/mission-vs-goal/run_paired_pilot.py --starting-commit 0148f16 --timeout 900
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py --tasks-file benchmarks/mission-vs-goal/tasks.complex.json --run-id 2026-06-28-claude-goal-vs-mission-smoke-v2 --starting-commit 38cc7907e5e35fcd9fa23022a1fcf03f756df99b --limit-tasks 1 --timeout 300 --max-budget-usd 1.5 --mission-max-iter 1
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py --tasks-file benchmarks/mission-vs-goal/tasks.complex.json --run-id 2026-06-28-claude-goal-vs-mission-smoke-v3 --starting-commit ed98b0e00169f0e0b35ce629a206ffcb7af4d0a3 --limit-tasks 1 --timeout 900 --max-budget-usd 3.0 --mission-max-iter 2
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py --tasks-file benchmarks/mission-vs-goal/tasks.complex.json --run-id 2026-06-28-claude-goal-vs-mission-complex-v1 --starting-commit ed98b0e00169f0e0b35ce629a206ffcb7af4d0a3 --limit-tasks 10 --timeout 1800 --max-budget-usd 3.0 --mission-max-iter 2
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py --tasks-file benchmarks/mission-vs-goal/tasks.complex.json --run-id 2026-06-28-claude-goal-vs-mission-incremental-v1 --starting-commit d1cef1d5bd0166b5d61939c8d93ce0060c05507f --task-ids complex-failing-test-triage,complex-review-thread-resolution --stop-on-blocked --timeout 1200 --max-budget-usd 3.0 --mission-max-iter 2
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py --tasks-file benchmarks/mission-vs-goal/tasks.quality.json --run-id 2026-07-03-claude-goal-vs-mission-quality-light-v1 --starting-commit 1863e02b4cb49bc78399d423ad82c2176627ecdd --task-ids quality-multi-cause-regression-triage,quality-security-secret-handling-plan,quality-bilingual-claim-consistency --stop-on-blocked --timeout 1200 --max-budget-usd 4.0 --mission-max-iter 1 --mission-profile light --run-root /private/tmp/mission-vs-official-goal-2026-07-03-light-v1
python3 -m pytest skills/mission/tests/test_benchmark_package.py skills/mission/tests/test_doc_consistency.py -q
python3 -m pytest skills/mission/tests -q
python3 -m json.tool benchmarks/mission-vs-goal/tasks.json
python3 -m json.tool benchmarks/mission-vs-goal/tasks.complex.json
python3 -m json.tool benchmarks/mission-vs-goal/result.schema.json
python3 -m py_compile benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py
git diff --check -- README.md README.ja.md docs/LOOP_ENGINEERING.md benchmarks/mission-vs-goal/README.md benchmarks/mission-vs-goal/README.ja.md benchmarks/mission-vs-goal/report.md benchmarks/mission-vs-goal/report.ja.md benchmarks/mission-vs-goal/report-template.md benchmarks/mission-vs-goal/report-template.ja.md benchmarks/mission-vs-goal/complex-validation-plan.md benchmarks/mission-vs-goal/complex-validation-plan.ja.md benchmarks/mission-vs-goal/official-goal-rerun-runbook.md benchmarks/mission-vs-goal/official-goal-rerun-runbook.ja.md benchmarks/mission-vs-goal/run_paired_pilot.py benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py skills/mission/tests/test_benchmark_package.py
```

## Marketing Summary

Safe to say:

> In a controlled local 10-task Codex CLI pilot, both goal-only and `mission`
> completed and passed all validators. `mission` produced stronger automated
> evidence/completion-quality scores, while taking longer per run.

Safe to say about the official `/goal` smoke:

> A first Claude Code official `/goal` smoke was attempted on one complex task.
> `/goal` completed the artifact, while the comparable `/mission` arm was
> blocked by Claude Code workspace API limits. No marketing comparison should be
> made from this smoke until the `/mission` arm can complete.

Safe to say about the API-limit rerun:

> After the API limit increase, a one-task official `/goal` vs `/mission` smoke
> completed on both arms and both passed the automated validator. In that
> one-task smoke, the automated quality/evidence scores tied at 4.00 / 5, while
> `/mission` took longer. The subsequent 10-task full attempt was blocked by
> workspace API usage limits, so it cannot support a performance claim.

Safe to say about the cost-capped incremental rerun:

> With a USD 3.00 per-invocation cap on two additional complex tasks, official
> `/goal` completed both tasks and `/mission` hit the configured max-budget cap
> on both tasks. This supports an operational cost/runtime caution, not a claim
> that `/mission` answers are lower quality.

Safe to say about the light-profile rerun:

> On one previously unmeasured complex task, official `/goal` and `/mission`
> light both completed and passed. In that single task, `/mission` light was
> faster and lower cost, but the sample is too small for a broad claim.

Safe to say about the 2026-07-03 light-profile rerun:

> On three additional quality-critical tasks run with `--mission-profile light`,
> official `/goal` and `/mission` light both completed and passed all automated
> validators with matching automated quality-marker scores. In this run,
> `/mission` light was slower and cost more than official `/goal`.

Safe to say about the quality-profile attempt:

> A quality-focused profile and fresh critical task cohort were added, but the
> first paired attempt was blocked by Claude Code workspace API limits before
> `/mission` ran. No quality comparison can be made from that attempt.

Safe to say about the tail run:

> On five planted-defect tasks designed as first-pass recall challenges, both
> arms tied on all automated content-recall metrics (average quality 4.97 / 5,
> average marker score 0.97) with zero decoy false positives. The `/mission` arm
> ran its full review loop and cleared its own 4.0 pass gate at iteration 1 in
> all five runs, at ~5.8x wall-clock time and ~7.4x recorded USD cost compared
> with `/goal`. N=5, closed-world fixtures, one model; no broad quality claim
> follows from this run.

Do not say:

> The tail run shows `/mission` is worse than `/goal` (equal scores, different cost profile).

Do not say:

> `mission` is smarter than `/goal`.

Do not say:

> `mission` improves completion rate in this pilot.

Do not say:

> `mission` is better or worse than Claude Code official `/goal` based on the
> 2026-06-28 smoke.

## Raw Result Index

```text
results/2026-06-27-codex-cli-local.jsonl
results/2026-06-27-codex-cli-local-summary.json
artifacts/2026-06-27-codex-cli-local/
results/2026-06-28-claude-goal-vs-mission-smoke-v2.jsonl
results/2026-06-28-claude-goal-vs-mission-smoke-v2-summary.json
artifacts/2026-06-28-claude-goal-vs-mission-smoke-v2/
results/2026-06-28-claude-goal-vs-mission-smoke-v3.jsonl
results/2026-06-28-claude-goal-vs-mission-smoke-v3-summary.json
artifacts/2026-06-28-claude-goal-vs-mission-smoke-v3/
results/2026-06-28-claude-goal-vs-mission-complex-v1.jsonl
results/2026-06-28-claude-goal-vs-mission-complex-v1-summary.json
artifacts/2026-06-28-claude-goal-vs-mission-complex-v1/
results/2026-06-28-claude-goal-vs-mission-incremental-v1.jsonl
results/2026-06-28-claude-goal-vs-mission-incremental-v1-summary.json
artifacts/2026-06-28-claude-goal-vs-mission-incremental-v1/
results/2026-06-28-claude-goal-vs-mission-light-v1.jsonl
results/2026-06-28-claude-goal-vs-mission-light-v1-summary.json
artifacts/2026-06-28-claude-goal-vs-mission-light-v1/
results/2026-06-28-claude-goal-vs-mission-quality-v1.jsonl
results/2026-06-28-claude-goal-vs-mission-quality-v1-summary.json
artifacts/2026-06-28-claude-goal-vs-mission-quality-v1/
results/2026-07-03-claude-goal-vs-mission-quality-light-v1.jsonl
results/2026-07-03-claude-goal-vs-mission-quality-light-v1-summary.json
artifacts/2026-07-03-claude-goal-vs-mission-quality-light-v1/
results/2026-07-07-claude-goal-vs-mission-tail-v1.jsonl
results/2026-07-07-claude-goal-vs-mission-tail-v1-summary.json
artifacts/2026-07-07-claude-goal-vs-mission-tail-v1/
results/2026-07-10-claude-goal-vs-mission-tail-smoke-v2.jsonl
results/2026-07-10-claude-goal-vs-mission-tail-smoke-v2-summary.json
artifacts/2026-07-10-claude-goal-vs-mission-tail-smoke-v2/
```
