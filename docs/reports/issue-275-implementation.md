# Issue #275: Diff-review measurement implementation

## Test list

- [x] Discriminating tasks require `fail_first: true` and `mission_max_iter >= 3`.
- [x] The mission prompt adds the fail-first protocol only for eligible tasks; goal prompts do not contain it.
- [x] Activity segments persist a positive, state-owned iteration and split when the iteration changes; legacy segments remain readable.
- [x] Mission state extraction reports versioned per-iteration diff-review observations, including state-owned activity durations and wall-clock availability.
- [x] Evidence references use the existing descriptor-safe reader, verify digest, and yield unavailable annotations without breaking a run on malformed input.
- [x] Summary exposes mechanical gates: clean iteration-2 eligible records, record-cost total/mean, degraded records, uninitialized-loop records, and context-manifest iteration counts.
- [x] English and Japanese runbooks specify N>=5, parallel 3, budget caps, and stop conditions.
- [x] Canonical and plugin activity-segment implementations remain byte-identical.

## Evidence table

| Requirement | Test / verification | Result |
| --- | --- | --- |
| Fail-first cohort and mission-only prompt | `test_issue275_diff_review_measurement.py` | Passed |
| Iteration-owned activity segments | focused activity test | Passed |
| Per-iteration state observations | focused benchmark test | Passed |
| Safe context-manifest extraction | digest/path/symlink/hardlink negatives | Passed |
| Summary gates | focused benchmark test | Passed |
| Runbook contract | manual EN/JA review | Passed |
| Canonical/plugin sync and hygiene | sync + hygiene tests | Passed |

## Red/Green log

| Cycle | Red evidence | Green evidence |
| --- | --- | --- |
| 1 | fail-first task/prompt tests failed before implementation | Green: 2 passed |
| 2 | summary gate absent | Green: per-iteration observation and gate tests passed |
| 3 | adversarial context evidence tests added | Green: digest/path/symlink/hardlink fail open |
| 4 | lifecycle-equivalent iteration 0 -> 1 -> 2 segment test failed | Green: target iteration is state-owned `last_scored_iteration + 1` |
