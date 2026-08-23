# #636: recoverable aggregate index update design

## Status

Proposed. Investigation and design only; no production code is changed by this
document. The measured base is `main` at `ba5a87c`.

## 1. Decision summary

Adopt option **(a), an ordered write with a durable intent and a recorded
recovery procedure**. For every `aggregate_action`, publish an intent before the
authoritative session write, publish the index update after the session write,
and remove the intent only after the index publish succeeds. Recovery must not
blindly replay the recorded `add` or `remove`: it must read the current
format-pinned V4/V5 session authority and reconcile that session's membership
idempotently. This resolves both possible states of an intent: the process may
have died before the session write, or after it.

This is the smallest option that satisfies ADR-006's explicit choice of an
ordered write with a recorded recovery step or one transaction
(`docs/adr/006-kernel-reducer-adjudication.md:105-114`). It also preserves the
existing application port `save(..., aggregate_action=...)`
(`skills/mission/lib/mission_application/ports.py:126-134`) and the contract that
an index failure occurs after the authoritative write
(`skills/mission/lib/mission_application/ports.py:13-15`).

## 2. Scope and terminology

- **Authority** is one session document: a flat missing/v1-v4 JSON document or
  the state selected through a V5 head. Format selection is made from the loaded
  session bytes and rejects format downgrade/drift
  (`skills/mission/lib/mission_persistence/repository_binding.py:52-102`,
  `skills/mission/lib/mission_persistence/repository_binding.py:128-158`).
- **Index** is `<project>/.mission-state/aggregate.json`
  (`skills/mission/bin/mission-state.py:881-890`). It is not session authority;
  the port itself calls it rebuildable
  (`skills/mission/lib/mission_application/ports.py:13-15`), and ADR-005 places
  it after the logical head commit under "Derived updates"
  (`docs/adr/005-typed-mission-kernel-and-unit-of-work.md:326-335`).
- This design covers the `LegacyV4Repository.save` and
  `V5CompatibilityRepository.save` compatibility paths. Initial creation is a
  different writer: `cmd_init` selects `_initialize_legacy_v4` for an existing
  V4 session and `_initialize_new_v5_session` otherwise
  (`skills/mission/bin/mission-state.py:8517-8559`). Its direct init transaction
  remains outside #636; it is not hidden as part of the legacy `save` fix.

## 3. Measured current behavior

### 3.1 V4 and V5 write order

| Path | Observed order | Failure outcome | Evidence |
|---|---|---|---|
| V4 `LegacyV4Repository.save` | format guard → optional backup → authoritative `_write_state` → select aggregate callback → callback | Any callback exception becomes `AggregateIndexError`; no restore or rollback call follows it. | `skills/mission/lib/mission_persistence/legacy_v4.py:314-345` |
| V5 `V5CompatibilityRepository.save` | format guard → prepare state bytes → `_stage_persistence` → fenced `commit` → clear admitted state → lease callback → aggregate callback | Any callback exception becomes `AggregateIndexError`; the already committed head is not rolled back. | `skills/mission/lib/mission_persistence/legacy_v4.py:516-560` |
| V5 commit point | Durable prepare/generation/commit are published before head replacement; head replacement occurs before operation publication, lineage verification, resolution, and finalize. | `recover()` classifies an outstanding durable prepare against base or target head. | `skills/mission/lib/mission_persistence/fenced_commit.py:4200-4339`, `skills/mission/lib/mission_persistence/fenced_commit.py:3527-3575` |

The call order was also exercised on 2026-08-23 with injected callbacks against
this base. V4 produced `backup, state-write, aggregate`; V5 produced
`stage, commit, lease-callback, aggregate`. The executable characterization in
the repository proves the consequential part: an injected aggregate failure is
reported after the V4 state is already halted
(`skills/mission/tests/test_lifecycle_usecases.py:625-656`), and the real CLI
leaves the corrupt index unchanged while retaining the halted authority
(`skills/mission/tests/test_lifecycle_usecases.py:971-1001`). These tests and the
U5-1 inventory test passed together on the measured base (`3 passed`).

### 3.2 Aggregate file, schema, and writers

The index is one JSON object. The lifecycle writer accepts only an object whose
`active_sessions` value is a list of strings; add is duplicate-free, remove is
idempotent, and a changed list also updates `updated_at`
(`skills/mission/bin/mission-state.py:1046-1078`). The older tolerant helpers use
the same two keys, but treat a corrupt file as empty on add and as a no-op on
remove (`skills/mission/bin/mission-state.py:1012-1043`). Production V4 and V5
compatibility repositories receive the strict helpers
(`skills/mission/bin/mission-state.py:9544-9588`).

The other production writers measured on this base are:

- init writes the session first, then merges `sid` into `active_sessions` and
  writes `updated_at` (`skills/mission/bin/mission-state.py:8236-8247`,
  `skills/mission/bin/mission-state.py:8394-8400`);
- `mission-migrate.py --execute` writes the migrated session, then merges the
  same two index fields (`skills/mission/bin/mission-migrate.py:67-85`).

### 3.3 Consumers

There is no production command on this base that uses `active_sessions` as its
selection authority. A tracked-source search finds reads only in the
read-modify-write helpers, init, and migration listed above. In particular:

- the state iterator yields legacy `state.json` and `sessions/*.json` directly
  (`skills/mission/bin/mission-state.py:901-935`);
- `cleanup-stale` iterates those state files
  (`skills/mission/bin/mission-state.py:16766-16786`);
- `list` iterates and reads the authoritative state for each file
  (`skills/mission/bin/mission-state.py:16972-17002`);
- `halt --all` uses the same iterator
  (`skills/mission/bin/mission-state.py:17171-17194`).

The operator reference agrees that init adds and terminal commands remove, and
that list/cleanup/halt also scan `sessions/*.json`
(`skills/mission/refs/state-management.md:312-322`). Therefore stale index
membership affects index integrity and tests, but not current command selection.
This observation does **not** authorize deleting the index: its documented and
tested compatibility contract remains in scope.

### 3.4 `AggregateIndexError` receivers

The receiver behavior is not uniform:

| Use case / adapter | Current behavior | Evidence |
|---|---|---|
| mark-halt | application catches the error in `aggregate_error`; CLI emits `WARNING: aggregate index update failed` and otherwise prints success | `skills/mission/lib/mission_application/lifecycle.py:671-681`, `skills/mission/bin/mission-state.py:16245-16250` |
| reactivate | same warning/result pattern | `skills/mission/lib/mission_application/lifecycle.py:815-821`, `skills/mission/bin/mission-state.py:16308-16313` |
| refresh-pid reactivation | same warning/result pattern | `skills/mission/lib/mission_application/lifecycle.py:918-935`, `skills/mission/bin/mission-state.py:16400-16412` |
| set routing/reactivation | same warning/result pattern | `skills/mission/lib/mission_application/lifecycle.py:1260-1286`, `skills/mission/bin/mission-state.py:11004-11017` |
| mark-passes | application does not catch the error after `save`; the command-specific adapter also has no `AggregateIndexError` branch, so the top-level generic handler emits an `internal-error` envelope and exits 1 after authority already committed | `skills/mission/lib/mission_application/review.py:514-522`, `skills/mission/bin/mission-state.py:15814-15870`, `skills/mission/bin/mission-state.py:19374-19383` |

#636 must keep these externally observable outcomes unless a separate API
decision explicitly changes them. Recoverability is added below the application
port; it does not silently turn a previously reported failure into success.

### 3.5 U5-1 inventory exclusion and mirrors

U5-1 documents the legacy aggregate update as the remaining U5-2 exclusion
(`skills/mission/lib/mission_persistence/administrative.py:1-9`). Its AST guard
currently permits the four direct aggregate writers
`_add_to_aggregate`, `_add_to_aggregate_strict`,
`_remove_from_aggregate`, and `_remove_from_aggregate_strict`
(`skills/mission/tests/test_issue635_admin_commit_protocol.py:211-240`). The
minimum administrative protocol established by U5-1 is identity-checked read,
validation, atomic publish, and a defined failure outcome
(`skills/mission/lib/mission_persistence/administrative.py:52-110`).

The requested `docs/design/*U5*` check found no tracked matching design file on
this base (`git ls-files 'docs/design/*U5*'` returned empty). The implemented
U5-1 record is therefore the module note and the executable guard above, not a
separate U5 design document.

The source/plugin files are byte-identical on the measured base (`cmp -s` exit
0 for each pair):

- `skills/mission/lib/mission_persistence/legacy_v4.py` and
  `plugins/mission/skills/mission/lib/mission_persistence/legacy_v4.py`
  (corresponding save implementations at lines 314-345 and 516-560);
- `skills/mission/lib/mission_application/lifecycle.py` and
  `plugins/mission/skills/mission/lib/mission_application/lifecycle.py`
  (corresponding catches at lines 671-681, 815-821, 918-935, and 1260-1286);
- `skills/mission/bin/mission-state.py` and
  `plugins/mission/skills/mission/bin/mission-state.py`
  (corresponding index helpers at lines 1012-1078);
- `skills/mission/lib/mission_persistence/administrative.py` and
  `plugins/mission/skills/mission/lib/mission_persistence/administrative.py`
  (corresponding exclusion note at lines 1-9).

## 4. Options considered

| Option | V4 path | V5 path | Crash window | Existing API | Decision |
|---|---|---|---|---|---|
| (a) Durable intent → authority → index → intent removal | Works without claiming V4 is a `RecoverableUnitOfWork`; V4 is explicitly excluded from that stronger protocol (`skills/mission/lib/mission_application/ports.py:102-139`). | Works around the existing V5 commit. Recovery reads the format-pinned current head rather than attempting to undo it. | A temporary physical divergence can exist after authority commit, but there is no **unrecorded** divergence once the intent is durably published. Kills before authority, after authority, and after index are distinguishable and idempotently recoverable. | Preserve `save(..., aggregate_action=...)`, `AggregateIndexError`, and current CLI rendering. | **Selected.** One common compatibility protocol closes both formats and matches ADR-006 (`docs/adr/006-kernel-reducer-adjudication.md:105-114`). |
| (b) Put index in the V5 fenced commit | Not available: V4 intentionally does not implement `RecoverableUnitOfWork` (`skills/mission/lib/mission_application/ports.py:102-155`). A second V4 protocol would still be required. | Technically possible by extending staged projections and the durable prepare/recovery machinery, which already publishes projections before the head and can roll them forward/back (`skills/mission/lib/mission_persistence/fenced_commit.py:4232-4297`, `skills/mission/lib/mission_persistence/fenced_commit.py:3527-3568`). | Can eliminate an unrecorded V5 window, but does nothing for V4. The project-wide index also crosses otherwise per-session commit domains. | Public application signature could remain, but commit/prepare records and recovery behavior would expand. | Rejected: two mechanisms, larger blast radius, and avoidable coupling of a shared derived index to each per-session transaction. |
| (c) Declare the index derived and provide full rebuild | Works: V4 session files are directly enumerable (`skills/mission/bin/mission-state.py:901-935`). | Works: the same iterator reaches V5 heads and authoritative loading is already format aware in list/cleanup (`skills/mission/bin/mission-state.py:16780-16786`, `skills/mission/bin/mission-state.py:16979-16985`). | Without a durable marker, a kill after authority commit leaves an unmarked divergence until a scan is requested. A manual rebuild alone therefore does not meet ADR-006's recorded-recovery choice. | A new repair command can be additive. | Rejected as the primary protocol. Retain full rebuild as a repair tool and corrupt-index fallback inside option (a). |

## 5. Proposed protocol

### 5.1 Records and invariants

Add a persistence-owned `RecoverableAggregateIndex` protocol. Production
adapters inject it into both compatibility repositories; existing constructor
arguments remain accepted for tests/third-party compatibility, but production
`aggregate_action` is statically required to use the recoverable coordinator.

One pending intent is stored per session under
`.mission-state/aggregate-index-intents/<sha256(session_id)>.json`. The filename
does not expose or trust session text. The bounded JSON record contains:

```json
{
  "schema": "mission-aggregate-index-intent/1",
  "session_id": "<exact session id>",
  "action": "add | remove",
  "authority_format": "legacy-v4 | v5",
  "base_authority_digest": "sha256:<64 hex>",
  "created_at": "<UTC timestamp>"
}
```

The record contains no state body, local absolute path, credential, or arbitrary
exception text. Intent publication and removal must fsync both the file and its
parent directory. Aggregate publication must satisfy the U5-1 minimum:
identity-checked read, strict schema validation, atomic publish, and a stable
failure code. A valid aggregate keeps unknown top-level keys and existing
relative order; add remains duplicate-free and remove remains idempotent, as on
main (`skills/mission/bin/mission-state.py:1062-1078`).

All intent-directory and `aggregate.json` read-modify-write operations are
serialized by a dedicated project-local `.aggregate-index.lock`. The lock is
held only for intent/index metadata operations. The global order is
**authority lock before index lock** when nesting is unavoidable: V4 already
holds its outer `StateLock` when it finalizes the index, while V5 must finish and
release `begin`/`commit`/`recover` locking before taking the index lock. Recovery
captures/decodes authority without the index lock, then takes the index lock and
rechecks only the already captured raw session/head identity; a mismatch
releases the index lock and restarts the read. No path may acquire an authority
lock while holding the index lock. This avoids both lost index updates and a
lock inversion with the existing project/session locks. The current V4 helper
relies on its caller holding `StateLock` for aggregate serialization
(`skills/mission/bin/mission-state.py:1012-1014`), while V5 commit acquires and
releases its own repository lock internally
(`skills/mission/lib/mission_persistence/fenced_commit.py:4200-4203`), so the
dedicated common boundary is required by the mixed-format path.

Membership is recovered from authority, not from the requested action:
`active := loop_active is True and passes is not True and halt_reason is empty`.
Those are the same control fields used by the active-session cleanup filter
(`skills/mission/bin/mission-state.py:16787-16790`) and by list output
(`skills/mission/bin/mission-state.py:16986-16999`). A conflicting or undecodable
authority fails closed and leaves the intent for explicit repair.

### 5.2 Ordered save

```text
repository transaction / existing format guard
  -> recover older pending intents for this project
  -> atomically publish + fsync this session's intent
  -> V4: backup then authoritative state write
     V5: stage then fenced commit, then existing lease callback
  -> identity-check, validate, and atomically update aggregate.json
  -> fsync and remove the intent
  -> return the existing result
```

If the state write fails, attempt immediate reconciliation against the still
current authority and remove the intent only after that succeeds. If either
reconciliation or intent cleanup fails, retain the intent and raise the original
state error; the pending marker makes later recovery deterministic.

If index publication fails after authority succeeds, retain the intent and raise
`AggregateIndexError` exactly as today. No authority rollback is attempted,
matching the existing port contract (`skills/mission/lib/mission_application/ports.py:13-15`)
and current characterization (`skills/mission/tests/test_lifecycle_usecases.py:625-656`).

### 5.3 Recovery procedure

Recovery runs before the next mutating repository transaction in the same
project and through an additive explicit command
`repair-aggregate-index [--check|--execute]`. It processes intents in stable
filename order:

1. Strictly validate intent identity and schema.
2. Load the current session through the existing format-pinned reader. Capture
   its session/head bytes digest and derive current membership.
3. Acquire `.aggregate-index.lock` and revalidate the captured raw authority
   identity without acquiring an authority lock. If it moved, release the index
   lock and retry from step 2; never clear the intent using a stale read.
4. While holding the index lock, identity-check and validate `aggregate.json`,
   apply one idempotent membership reconciliation, atomically publish, and
   fsync.
5. Remove and directory-fsync the exact intent that was read. A surviving intent
   after a crash merely causes the same reconciliation to run again.

If `aggregate.json` is missing, recovery creates it from all authoritative live
session records. If it is syntactically or structurally corrupt, automatic
recovery retains the intent and reports a stable error; explicit
`repair-aggregate-index --execute` rebuilds the two defined fields from all
authoritative sessions after validating every candidate. This preserves the
current immediate corrupt-index warning behavior
(`skills/mission/tests/test_lifecycle_usecases.py:971-1001`) while providing the
previously missing recorded repair path.

### 5.4 Kill-point classification

| Kill point | Durable state after restart | Recovery result |
|---|---|---|
| before/within atomic intent publish | No valid intent and no authority write has begun | No action; former authority/index remain. |
| after intent, before authority | Intent + former authority | Derive former membership, reconcile idempotently, remove intent. |
| after V4 state write or V5 head replacement, before index | Intent + new authority + former index | Derive new membership, publish index, remove intent. |
| after index publish, before intent removal | Intent + new authority + new index | Repeat the same idempotent update, remove intent. |
| during recovery after index publish | Same as the previous row | A second recovery converges to identical index bytes except for the deliberately refreshed `updated_at` only when membership changes. |
| after intent removal | New authority + new index, no intent | Nothing to recover. |

Thus option (a) retains a short physical gap but no unrecorded crash window. V4
and V5 converge through the same state-derived recovery rule; V5's existing
head recovery still owns incomplete fenced commits
(`skills/mission/lib/mission_persistence/fenced_commit.py:1984-2016`,
`skills/mission/lib/mission_persistence/fenced_commit.py:3527-3575`).

## 6. Planned file changes

Implementation must use TDD and update source/plugin mirrors in the same logical
change.

| File | Planned change |
|---|---|
| `skills/mission/lib/mission_persistence/aggregate_index.py` | New intent schema, safe capture/validation, ordered prepare/finalize, recovery, and rebuild protocol. |
| `plugins/mission/skills/mission/lib/mission_persistence/aggregate_index.py` | Byte-identical plugin mirror. |
| `skills/mission/lib/mission_persistence/legacy_v4.py` | Invoke prepare before V4 write/V5 commit; finalize after authority; recover on transaction entry; preserve `save` signature and error type. Current insertion points are `skills/mission/lib/mission_persistence/legacy_v4.py:314-345` and `skills/mission/lib/mission_persistence/legacy_v4.py:516-560`. |
| `plugins/mission/skills/mission/lib/mission_persistence/legacy_v4.py` | Byte-identical plugin mirror. |
| `skills/mission/bin/mission-state.py` | Construct the coordinator, replace production direct add/remove helpers, and add check/execute repair adapter. Current injection is `skills/mission/bin/mission-state.py:9544-9588`. |
| `plugins/mission/skills/mission/bin/mission-state.py` | Byte-identical plugin mirror. |
| `skills/mission/tests/test_issue636_recoverable_aggregate_index.py` | New Red→Green protocol, fault-injection, concurrency, recovery, repair, and mirror tests. |
| `skills/mission/tests/test_issue635_admin_commit_protocol.py` | Remove the four U5-2 direct-writer exclusions and assert production lifecycle wiring uses the protocol. Current exclusion is `skills/mission/tests/test_issue635_admin_commit_protocol.py:211-240`. |
| `docs/adr/006-kernel-reducer-adjudication.md` | Append an implementation note selecting ordered intent recovery; do not rewrite the Accepted decision. Current decision is `docs/adr/006-kernel-reducer-adjudication.md:105-114`. |
| `skills/mission/refs/state-management.md` and plugin mirror | Record automatic next-mutation recovery and explicit check/execute repair. Current aggregate contract is `skills/mission/refs/state-management.md:312-322`. |

No change to session-state schema, kernel commands, or application request/result
types is required.

## 7. TDD test list

### Red: current gap and contract capture

1. V4 and V5 parameterized call-order tests assert intent prepare precedes the
   authoritative write/commit and index finalize follows it.
2. Freeze current-main output for every `aggregate_action` producer: halt,
   reactivate, refresh-pid reactivation, routed set, loop-active set, and
   mark-passes. The producer inventory is evidenced at
   `skills/mission/lib/mission_application/lifecycle.py:671-675`,
   `skills/mission/lib/mission_application/lifecycle.py:815-820`,
   `skills/mission/lib/mission_application/lifecycle.py:918-926`,
   `skills/mission/lib/mission_application/lifecycle.py:1263-1281`, and
   `skills/mission/lib/mission_application/review.py:514-515`.
3. Assert V4 and V5 authority output has the exact same recursively compared
   key set and value for the same captured input, independently of index/intent
   metadata.

### Green: protocol and fault injection

4. Parameterize `(V4, V5) × (add, remove)` for success, state failure, and index
   failure. State failure retains/reconciles the former membership; index
   failure retains intent and preserves the current receiver behavior.
5. Use subprocess `os._exit(91)` kill injection at every point in section 5.4,
   then create a fresh process and run recovery twice. Assert both runs produce
   identical authority, membership, and pending-intent set. The repository's
   established kill harness uses this exact process-exit technique
   (`skills/mission/tests/test_issue504_crash_recovery.py:123-230`).
6. For V5, include kills at the existing fenced points around head replacement
   (`after-prepare`, `after-generation-publish`, `after-commit-publish`,
   `before-head-replace`, `after-head-replace`) and then aggregate points. Those
   hooks exist at `skills/mission/lib/mission_persistence/fenced_commit.py:4238-4291`.
7. Kill after index publish and during recovery; verify repeated reconciliation
   does not duplicate a sid, reorder unaffected entries, or change
   `updated_at` when membership is already correct.
8. Test missing, corrupt, array-shaped, wrong-element-type, symlinked, hardlinked,
   oversized, and identity-changing intent/index records. Automatic recovery
   must fail closed without clearing intent; explicit execute rebuild may
   replace only a corrupt derived index after all authorities validate.
9. Run concurrent V4/V5 add/remove operations for different session IDs and
   assert no lost membership update. The existing concurrency expectation is
   that simultaneous terminalization empties `active_sessions`
   (`skills/mission/tests/test_tier3_robustness.py:34-65`).
10. Assert a pending intent is recovered before the next mutating operation even
    when that operation has `aggregate_action=None`.
11. Assert `repair-aggregate-index` defaults to check-only; `--execute` is
    required for writes and repeated execute is idempotent.

### Refactor and distribution gates

12. Update the U5-1 AST inventory so all four direct aggregate writer names are
    absent, and add a negative fixture proving a newly introduced direct atomic
    aggregate write fails the guard.
13. Assert byte identity for every changed source/plugin mirror.
14. Run the existing lifecycle golden corpus, V4/V5 repository binding tests,
    crash recovery suite, session lifecycle/tier3/tier4 aggregate tests,
    artifact hygiene, vendor-fingerprint guard, and the repository's full
    required test command.

## 8. Exact current-main key/value compatibility

Before implementation, use `ba5a87c` to capture each input command's
authoritative output for both formats. For V4, read the flat session JSON after
the command. For V5, resolve the head with `LocalFencedRepository.read` and use
its `state_bytes`; the existing fixture conversion already obtains V5 state in
that way (`skills/mission/tests/mission_state_fixture_corpus.py:175-220`).

For each case:

1. Freeze input authority bytes, command arguments, clock, lease token, process
   metadata normalization, output/exit status, and expected authority bytes.
2. Run the new implementation from the identical input.
3. Decode both outputs and recursively assert exact key-set equality and exact
   value equality at every path; then compare normalized canonical bytes using
   the existing golden helpers
   (`skills/mission/tests/test_lifecycle_usecases.py:77-154`,
   `skills/mission/tests/test_lifecycle_usecases.py:194-196`).
4. Compare index semantics separately: unaffected top-level keys and unaffected
   sid order are exact; the target sid has the same add/remove result as main.
   Intent files are protocol metadata and must not appear in the session JSON.
5. On failure cases, assert authority bytes match current main, not merely phase
   or `loop_active`. The current corrupt-index CLI test already pins normalized
   full authority bytes (`skills/mission/tests/test_lifecycle_usecases.py:992-1001`).

This makes "same input produces all the same saved key/value pairs as current
main" a mechanical gate rather than a review assertion.

## 9. Acceptance criteria

- Every V4/V5 `save(..., aggregate_action="add"|"remove")` publishes a durable
  intent before authority and clears it only after a validated atomic index
  publish.
- Process kill at every listed point leaves either former consistent state or a
  valid pending intent; a fresh recovery converges, and a second recovery is a
  no-op.
- No authority rollback is attempted after its commit; current
  `AggregateIndexError` application/CLI behavior remains pinned.
- The explicit repair command can reconstruct `active_sessions` from validated
  authoritative session states without using the old index as authority.
- The four U5-2 names are removed from
  `ALLOWED_DIRECT_ATOMIC_WRITERS`; the known-exclusion comments in the guard and
  `administrative.py` are removed or replaced with the implemented protocol
  reference.
- Current-main authoritative output matches at every key/value for the same
  input in V4 and V5.
- All changed source/plugin pairs are byte-identical and the required local
  gates pass.

## 10. Residual risks and exit strategy

- A physical authority/index gap remains possible between ordered writes, but a
  durable intent makes it observable and recoverable. If future consumers begin
  treating the index as authority, they must first run pending-intent recovery
  or the storage design must move to a single project-wide transaction.
- Full rebuild is bounded by the number of session records and is reserved for
  explicit repair/corrupt fallback. Normal recovery touches only sessions with
  pending intents, avoiding a scan on every save.
- Once V4 support is removed, the intent coordinator can remain as the
  project-wide derived-index protocol or be migrated into a project-wide V5
  transaction. It must not be folded into one session's transaction while the
  index still aggregates multiple sessions.
