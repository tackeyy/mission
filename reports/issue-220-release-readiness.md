# Issue #220 Release Readiness

## Scope

Prevent non-interactive mission runs from ending with an approval question when state or assumptions writes are unavailable. The startup path must fail closed with a structured `blocked-external` halt and evidence before task execution.

## Test list

- [x] A writable state and assumptions location passes the Phase 0 permission preflight.
- [x] An assumptions write failure records `halt_category=blocked-external` before task execution.
- [x] A state evidence write failure exits with structured fallback evidence and never requests approval.
- [x] The mission skill grants only the state CLI commands needed by the non-interactive startup path.
- [x] Canonical and packaged copies stay byte-for-byte synchronized.
- [x] The tail cohort mission arm executes at least three same-condition runs without an approval-question exit.

## Evidence log

| Gate | Status | Evidence |
|---|---|---|
| Targeted Red | passed | Missing command, undetected assumptions denial, missing init integration, and absent command allowance each failed for the intended reason. |
| Targeted Green | passed | `test_issue220_permission_preflight.py`: 8 passed. |
| Canonical/package sync | passed | `test_plugins_in_sync.py` and direct byte comparison passed. |
| Full test suite | passed | `python3 -m pytest -q skills/mission/tests`: 1,190 passed in 145.73s. |
| Tail cohort N>=3 | passed | Same commit `c2fa1d3`, `tail-incident-log-triage`, full profile, and non-interactive permission hardening: r1/r2/r3 all reached mission state `passes: true`; approval-question exits were 0/3. r1 and r2 also returned runner completion/validator pass. r3 reached `passes: true`, then the runner exhausted its $5 response budget (`$5.078`) before returning completion. A fourth diagnostic run also had no approval-question exit but stopped on the external Claude Code weekly limit, so it is not counted as a successful completion. |
| Pull request CI | pending | — |
| Merge | pending | — |

## Review notes

The independent Checker found two High and two Medium issues across the initial and follow-up reviews: unstructured initial state-write failure, overly broad state CLI permission, assumptions path escape, and unstructured lock-write failure. Each finding received a dedicated regression test and was fixed before the final full-suite run. The final review reported High 0 / Medium 0 / Low 0 and approved the code subject to the benchmark and pull-request CI gates below.

The tail runs were intentionally executed under the host's non-write-user hardening, which forced `acceptEdits` to default permission behavior. Tool denials occurred in every run, but none produced the prohibited approval-question final message. The mission startup preflight wrote `.mission-state` successfully in all counted runs, and the workflow recovered without an interactive respondent.
