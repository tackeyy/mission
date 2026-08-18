# Issue #542 v5 independent-process blocker fix

## Outcome

The public `get` command now resolves a v5 head through the verified
authoritative reader and returns the mission-state document. The returned
`lease_id` can be carried into a separate mutating CLI process, and `resume`
can take over an expired lease and return the next action.

## Root cause

`cmd_get` parsed the selected session file as a legacy JSON state document.
For v5, that file is an internal `mission-head/1` record, so public state fields
were absent. Moving `cmd_get` under the existing authoritative reader exposed a
second issue: the old outer state lock nested with the v5 reader's repository
lock. The final implementation follows the established `next` read boundary,
where stable read, format inspection, and v5 lineage verification are owned by
the authoritative reader.

## T13 evidence

- Baseline before the production fix: v4 passed; v5 failed because
  `get --field phase` returned `null` instead of `"planning"`.
- After the fix: both parameter values pass.
- Every `init`, `get`, `set`, stale `mark-halt`, and `resume` call is a separate
  subprocess.
- The test obtains `lease_id` from `get`, carries it via `MISSION_LEASE_ID` to
  `set`, then runs token-free `resume` after lease expiry to exercise takeover.
- The final public key set and stable resume output are compared with an
  identically exercised v4 reference, and internal head keys are rejected
  explicitly.

## Follow-up: live-lease rejection diagnostics

The fencing contract is unchanged. A matching session without its lease token
is still rejected, and the token is never recovered implicitly from persisted
state. The fix only translates the v5 lease admission rejection at the CLI
boundary into the established v4 diagnostic shape.

- T14 parameterizes v4/v5 and proves that the lease returned by `init` permits
  `resume` in a separate process.
- T15 parameterizes v4/v5 and proves that token-free `resume` during a live
  lease returns exit 2 with the owner/expiry diagnostic and the
  `MISSION_LEASE_ID` recovery hint.
- Before the production fix, the final T15 shape passed for v4 and failed for
  v5: v5 returned exit 1, emitted no stderr, and reported only
  `internal-error` on stdout.
- Direct token-free checks for `refresh-pid` and `mark-halt` now preserve the
  same v4/v5 exit and stderr contract. Other compatibility mutations share the
  same centralized v5 lease rejection translator.
- Iteration 2 closes the `cleanup-stale` gap completely. The bulk scanner now
  reads both v4 and v5 through the authoritative reader, while execution uses a
  format-pinned repository transaction. A v5 janitor presents no stale writer
  token: the repository admits only an expired-lease takeover, then the command
  revalidates the remaining stale predicates before publishing the halt.

## Iteration 2 review findings

- A-1: v5 `cleanup-stale` dry-run detection and `--execute` halt are both
  covered. Before the fix, the new test observed an empty `would_halt` array.
- B-1: unclassified fenced commit errors now produce `ERROR: <detail>` and
  exit 2 without a traceback or an internal-error downgrade. Before the fix,
  the injected CLI process exited 1.
- B-2: iteration-2 critic scope and minimum-reviewer gates are rechecked after
  the transaction-bound load. Before the fix, an injected scope change bypassed
  the gate and published aggregate output.
- C-1: valid provenance-bound `push-score` is exercised through both v4 and v5
  format-pinned repositories.
- A-2: v5 compatibility `load()` rejects calls outside an active transaction
  before repository admission. Before the fix, the call succeeded and retained
  an admitted request.
- C-2: token-free v5 `review-import` during a live lease is explicitly covered
  at exit 2 with the established lease diagnostic and no state change.
- C-3: rollback coverage now reads the pre-existing v5 session through a fresh
  CLI `get` subprocess.
- C-4: T3 documents its intentionally fixed clock and assigns cross-TTL
  coverage to T13.

## Scope audit

The fixed core paths are `get`, `set`, `resume`, `cleanup-stale`, and the
already-authoritative `next`. The existing full-lifecycle test also invokes
each lifecycle command as a separate subprocess.

Static inspection found direct session-file reads in commands outside the C1
owner set, including specialist/provider administration, progress display,
startup preflight, context manifest, and bulk halt paths.
The design plan explicitly assigns complete ownership migration for those
commands to #543 (C2), so this blocker fix does not broaden into that migration.
Dynamic sampling confirmed that startup preflight currently returns an internal
error for a v5 session, while fresh-session progress and specialist summaries
can silently observe head defaults instead of the state payload. These are
recorded as residual #543 work, not as working C1 paths.

## Verification

- Original targeted gates: T13 `2 passed`; #542 plus artifact hygiene and neutral
  vocabulary checks `27 passed`.
- Follow-up targeted gates: #542, lease regression, error guidance, artifact
  hygiene, and neutral vocabulary checks `78 passed`; Python 3.9 AST and the
  plugin mirror also passed.
- Manual CLI: live lease-bearing `resume` returned exit 0 and
  `next_action=run-planner`; token-free `resume` during the renewed live lease
  returned exit 2 with the owner/expiry diagnostic and `MISSION_LEASE_ID` hint.
  The earlier manual flow also covered `init`, `get phase`, `get lease_id`, lease-bearing `set`, expired
  token-free `resume`, and full `get` all returned exit code 0. Resume emitted
  `action=taken-over`, fencing epoch 2, and a next action.
- Required follow-up full suite with `-n 4 --dist loadfile`:
  `4064 passed in 320.40s`, failed 0.
- Independent checker: accepted after T13 was strengthened to cover explicit
  stale halt recovery and stable v4/v5 resume-output equivalence.
- Iteration 2 targeted gates: issue #542 and critic-race coverage `37 passed`;
  cleanup regressions `53 passed`; hygiene, neutral vocabulary, and Python 3.9
  coverage `67 passed`.
- Iteration 2 manual v5 cleanup: dry-run reported one
  `expired-session-lease` in `would_halt`; execute reported it in `halted`;
  independent `get` returned `loop_active=false` and `halt_category=stale`.
- Iteration 2 independent checker: accepted after one Low test-fidelity finding
  was fixed by running the rollback `get` subprocess with the v4 default.
- Iteration 2 required full suite with `-n 4 --dist loadfile`:
  `4073 passed in 325.24s`, failed 0.
