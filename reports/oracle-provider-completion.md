# Oracle Provider Completion Mission

## Mission

Make the oracle command provider complete end-to-end under ADR-001 without giving oracle special authority inside mission core.

## ADR Boundary

- Keep oracle as a command provider configured outside OSS defaults.
- Do not hard-code private paths, browser automation, or pass/fail authority into mission core.
- Treat command providers as evidence sources only.
- Keep preparation-only output classified as `prepared`, not `completed`.

## Plan

1. Add generic command-provider configuration for environment variables and provider-specific timeout.
2. Verify a provider can wait for external review output and produce `completed` evidence through `invoke-command`.
3. Keep preparation banners rejected by the result contract.
4. Mirror canonical files into `plugins/mission`.
5. Run focused tests and mission gates before PR merge.

## Evidence Log

- 2026-07-01: Previous smoke showed the local oracle wrapper is executable and can be invoked by `mission-state.py`, but without wait configuration it exits with `Oracle Browser Review Prepared` and is correctly logged as `prepared`.
- 2026-07-01: ADR-001 requires command providers to remain external evidence providers; this fix targets generic command-provider runtime configuration rather than oracle-specific mission logic.
- 2026-07-01: Implemented generic `env` and `timeout` normalization for command-provider candidates and pass those values into `subprocess.run`.
- 2026-07-01: Preserved command-provider `command`, `args`, `env`, and `timeout` in confirmed selection metadata so future invocations do not lose runnable configuration.
- 2026-07-01: Added tests for registry env propagation, provider timeout recording, and repeated invocation after confirmed selection.
- 2026-07-01: Validation passed:
  - `python3 -m pytest skills/mission/tests/test_specialist_invocations.py -q` -> 31 passed
  - `python3 -m pytest skills/mission/tests/test_plugins_in_sync.py -q` -> 9 passed
  - `python3 -m pytest skills/mission/tests -q` -> 409 passed
