# Issue #543 C2 Stage A — iteration 2 verification

## Outcome

The iteration 2 review findings are addressed without changing
`docs/design/543-c2-plan.md` or weakening the fencing contract.

## High: transitive AST guard

- `forbidden_calls_in_reachable(entry_names)` builds a worklist from every
  supplied `cmd_*` / `_cmd_*` entry point and follows calls to module-level
  functions recursively.
- The two C2 inventory tests share the same helper.
- A minimal fixture routes `cmd_supersede_reviews` through
  `_supersede_reviews_locked`, where `atomic_write_json` is called.
- Before the helper existed, the new fixture failed with `NameError`.
- A mutation check against the current source virtually inserted the same call
  into `_supersede_reviews_locked`; the no-violation assertion failed and
  reported the reachable `atomic_write_json` call.

Known C1 initialization writes and non-session aggregate/lineage locks are
listed as exact call-site exceptions. The traversal still enters those
functions, so a different forbidden call added there is not hidden by a broad
function boundary.

## Medium and Low contracts

- B-2 uses option (a): a mixed v4/v5 review group may remain detectably
  inconsistent after a mid-publication failure. A caller-issued retry with the
  same `MISSION_OPERATION_ID` converges the group. The fault-injection test
  proves both the no-retry inconsistency and recovery after one retry.
- C-2 verifies that v5 `supersede-reviews` without
  `MISSION_OPERATION_ID` exits with code 2 and performs no write.
- A-1 documents that old review-session leases must be expired. A live lease
  produces `lease-rejected` and no write.
- C-3 observes `planning adopt-core` against a v5 session. Its category is
  **fail-safe**: the command exits non-zero with `planning-policy-not-active`,
  and the v5 head schema and generation remain unchanged.
- C-4 replaces fixed `sleep(0.2)` / `poll()` assertions with a child-process
  ready signal emitted immediately before lock acquisition.

## Verification

- Targeted ownership, Issue #543, plugin-mirror, documentation, module
  inventory, artifact-hygiene, and vocabulary tests: `130 passed`.
- Full suite command:
  `.venv/bin/python -m pytest -q -n 4 --dist loadfile skills/mission`
- Full suite result before this report-only addition: `4106 passed`, failed 0.
- Canonical `skills/mission/` files and their plugin mirrors are byte-identical.
- No git commit was created.
