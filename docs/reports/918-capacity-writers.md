# E0b-3: state writer capacity gate

Scope: [Issue #918](https://github.com/tackeyy/mission/issues/918), based on
`16edae3eabf86be0dec9f33b995356943aa1363b`. No dispatch/withdraw command or
budget policy activation is added. The working changes are not committed.

## Implementation and acceptance evidence

All tests below are in [test_issue918_capacity_writers.py](../../skills/mission/tests/test_issue918_capacity_writers.py).

| Obligation | Evidence |
|---|---|
| All writers and genesis/reinit share the verdict and four refusal codes | `test_every_writer_propagates_verdict_code_without_publication`, `test_fenced_genesis_reserves_system_space_before_any_objects`, `test_legacy_full_status_and_reinit_are_read_only` |
| Reserved progress after halt near the limit, including pre-E0 history beyond N_L | `test_fenced_writer_keeps_reserved_progress_after_halt_and_old_history`; coherent history fixtures at N_L/N_L+1, actual fenced admission/halt/reserve/consume |
| Takeover N_L−1/N_L/N_L+1 boundary | `test_legacy_takeover_writer_enforces_the_history_budget` |
| Tombstones shrink in both save encodings, preserve criterion IDs and prevent nonce reuse | `test_withdrawn_tombstone_shrinks_through_both_writers` |
| Absent/empty halt slots and administrative stop routes | `test_empty_and_absent_halt_slots_keep_over_capacity_stop_behavior`; `next`, `halt --all`, `cleanup-stale` |
| ResumeStale is gated | `test_resume_stale_cannot_release_a_halt_slot_without_capacity` |
| Post-E0 state with a written halt is normal, never legacy-full | actual halted generation in the reserved-progress test; existing #933 boundary tests |
| Raw halt/dispatch bounds, new lease tokens and epochs | shared v4/v5 field table, raw padded halt across three writers, partial-lease and admission cases |
| Historical lease compatibility without allowing forged new history | historical renewal/stop and genuine takeover tests; missing-epoch and fake-copy rejection |
| Read-only remaining-capacity status | `test_status_reports_capacity_of_authoritative_bytes`, legacy-full status case |
| New publication mouths fail inventory | all bin/lib AST sinks and counts in [state-writer-inventory.json](../../skills/mission/tests/fixtures/state-writer-inventory.json); direct I/O, import aliases, bound methods and literal dynamic lookup |

Coverage/missing semantics remain in D3. These tests establish criterion inclusion
and one-time consumption in the existing projection; no future dispatch/terminal
receipt CLI or E-attempt/disposition writer is introduced.

## Validation and test value

- Writer/lease/fresh-review selection: **158 passed**, 100.65 s; changed near-limit halt integration: **2 passed**, 6.84 s; final inventory/alias selection: **15 passed**, 1.58 s.
- Capacity kernel, budget pins, strict stage inputs and layer boundaries: **300 passed**, 8.93 s.
- Thin-adapter tests, mirror sync, module inventory, wrapper compatibility and artifact/vendor hygiene: **107 passed**, 6.86 s.
- Ratchet against `16edae3e`: passed, 457 functions; `git diff --check`: passed.
- Independent exploration tried 86 writer inputs before enforcement and 80 malformed inputs during review, then 80 historical-entry modifications. Concrete findings were fixed with failing-before/passing-after regressions; final local independent recheck found no blocking counterexample. This does not replace the formal review gate.

Without the new tests a writer could bypass a correct pure kernel, publish bytes
without stop reservations, or normalize an invalid new lease into an unbounded
history allocation. The shared table tests bounds cheaply; subprocess cases
retain real command routing and publication checks. Existing #936 takeover tests
now distinguish historical-token migration from rejected new epochs, retaining
measured reservation bounds. Existing #895 status assertions allow the additive
capacity field. CI discovers the new file in the existing suite shards.
Janitor rejection preserves authoritative session bytes; pre-save recovery may
already have created derived index/backup files.

## Parent handoff

GitHub in-flight reconciliation and fallback PR listing both failed to connect;
remote ownership remains unverified. No Git mutation, GitHub write, review CLI,
or full suite was run. The parent must integrate the dependency after merge,
obtain same-head formal review/Checker, run the full suite and required CI, then
commit/publish. Initial [planning-test](../../skills/mission/tests/test_issue501_k2_transitions.py) `internal-error` did not recur (1 pass, 19.25 s); cause unknown.
