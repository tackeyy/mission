# Discriminating cohort N>=10 adoption runbook

Purpose: run the adoption-decision benchmark for "quality > goal, speed ≈ goal"
on the `tasks.discriminating.json` cohort, which removes the quality ceiling
observed in openworld-v1 (marker 1.0 / variance 0). The decision contract
follows #236: calibration at N=3, adoption decisions at N>=10 paired records.

See `discriminating-cohort-runbook.ja.md` for the full procedure (Japanese is
the canonical version). Summary:

1. **Smoke (1 task)**: run `disc-config-sprawl` paired; require
   `mission_iterations >= 2`, a recorded `critic_has_new_scope`, no
   `mission_loop_not_initialized` records, and at least one marker score
   below 1.0 before proceeding.
2. **Main run**: 5 tasks x 2 arms x `--repeats 1` = 10 records (N=10);
   `--repeats 2` for variance depth. Estimated notional cost $35-60 for one
   repeat, wall clock 2-3 hours, model pinned to `claude-sonnet-5` via the
   PATH shim.
3. **Adoption gates**: measurement validity (no invalid records or
   comparable N >= 10), discrimination (`marker_score_variance` non-zero in
   both arms), at least one mission record with `mission_iterations >= 2`,
   then judge quality via `comparable_average_quality_score` / marker recall
   and speed via `comparable_average_elapsed_minutes` (target within 1.5x).

Main-run command:

```bash
PATH="<shim-dir>:$PATH" python3 run_claude_goal_vs_mission.py \
  --starting-commit <latest-main> \
  --tasks-file benchmarks/mission-vs-goal/tasks.discriminating.json \
  --run-id <date>-discriminating-v1 \
  --model-id claude-sonnet-5 \
  --limit-tasks 5 --repeats 1 --parallel 3 \
  --max-budget-usd-goal 3 --max-budget-usd-mission 10 \
  --mission-budget-minutes 30 --timeout 2400
```

### Measured budget recommendations

These are caps, not spending targets. Recalibrate them when the cohort, model,
or mission profile changes.

| Arm / cohort | Recommended cap | Measured basis |
|---|---:|---|
| goal (all portfolio tasks) | USD 3.0 | `2026-08-07-portfolio-v6-repeats3`, `portfolio-std-contract` goal rep 1 cost USD 2.6253. |
| mission Standard | USD 8.0 | `2026-08-07-portfolio-v6-repeats3`, `portfolio-std-contract` mission rep 1 completed at USD 5.9400; Standard reps capped at USD 6.0085 and USD 6.0259 show that a USD 6 cap is insufficient. |
| mission full profile (Standard + Complex) | USD 10.0 | `2026-08-02-portfolio-v5-speed`, `portfolio-std-contract-mission` reached iteration 2 at USD 4.8770; combined with the v6 USD 5.9400 completed maximum, USD 10 preserves iteration-2 headroom. |

### Benchmark audit KPI

Each runner summary now includes `benchmark_kpi` (`mission-benchmark-kpi/1`).
It reduces only synthetic result-record annotations; it does not read or
recompute any raw planning state. Interpret it alongside the normal arm
summary, not as a replacement for it.

- `score_buckets` separates below-pass (`<4.0`), pass-but-below-target
  (`4.0.. <4.3`), and target-met (`>=4.3`) records.
- `audit_events` must carry `root_event_id`, positive `attempt`, and a `kind`
  of `defect` or `expected-gate`. Defects dedupe by root event while retries
  remain separately visible; `expected-gate` is reported but excluded from
  defect totals.
- `audit_context.coverage` is an observed/eligible pair and `tier` is context
  only. Coverage with zero eligible items reports a null rate.
- Duration p50/p90/tail use `run_status=completed` records only. A `blocked`
  record is counted as censored (`blocked_censored_records`), while every other
  noncompleted record is counted separately (`noncompleted_excluded_records`);
  neither category can distort timing percentiles.

The `mission-planning-provider-kpi/1` producer belongs to Issue #399 and is
not consumed by this runner yet. `planning_provider_kpi.status=deferred` is an
intentional versioned seam: do not add a payload to benchmark records until
the #399 consumer contract is implemented and validated.

`scripts/mission-audit.py --json` separately reports calibrated mission-state
evidence. Its `score_calibration` population is pass sessions: scores below a
session's threshold emit `below-pass-threshold`, scores from threshold through
below 4.3 emit `pass-but-below-target`, and target-met scores are counted but
do not create a finding. The historical `low_score_pass_*` JSON fields remain
compatibility aliases and do not double-create findings. `command_outcome_defects`
dedupes non-gate defects by root event, exposes retries, and excludes
`expected-gate` while retaining its separate count. Slow-run findings include
the record's `activity_coverage_ratio` and `review_tier`.

The benchmark's `measurement_observations` explicitly lists
`artifact_observation_coverage`, `activity_coverage`,
`structured_score_provenance`, `reviewer_freshness`, `force_pass_rate`,
`expected_gate_retry_count`, and `group_closeout_completeness`. The mission
runner supplies versioned observations from typed mission-state evidence for
the first six fields; goal records explicitly mark them `not-applicable`.
`group_closeout_completeness` remains unavailable until its producer exists.
The benchmark reducer aggregates only each record's numerator/denominator or
counter (zero denominator gives a null rate); it must not reconstruct values
from raw mission state.

Record results in `report.md` / `report.ja.md` with the standard unsafe-
interpretation guard, then close #262 with the verdict.

Environment notes (2026-07-25, #292): on CC 2.1.219+ the
`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=0` opt-out no longer prevents permission-mode
degradation; the runner now declares `--allowedTools` explicitly for both arms.
Verify `permission_degraded_records` is 0 in the summary. Also launch the runner
with `env -u ANTHROPIC_API_KEY`: an invalid key inherited from the CC session
env takes precedence over the claude.ai login and 401s every child.

---

## Revision History
| Date | Change |
|------|--------|
| 2026-07-22 | Initial version (#262) |
| 2026-07-25 | CC 2.1.219 hardening: explicit --allowedTools + ANTHROPIC_API_KEY unset (#292) |
| 2026-08-07 | Added measured arm-specific budget caps and main-run flags (#358) |
