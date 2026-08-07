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
