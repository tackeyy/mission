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
