# Issue #504 U3: deterministic crash-recovery exact contract

Status: **implementation contract**

Scope: migration plan Section 8 U3 only. Garbage collection (U4), the
ADR-005 public `stage(admitted, transition, blobs)` boundary (P1), production
routing, and the v5 default remain outside this contract.

## 1. Conclusion

U3 turns U2's global `recovery-required` stop into deterministic recovery of
one completely validated durable transaction. Recovery runs under the existing
repository lock before a new admission, never calls the kernel, and classifies
the current head by exact canonical bytes:

- exact base head: restore every compatibility projection, record
  `rolled-back`, and remove the open prepare;
- exact target head/commit/generation: finish or verify projections, recreate
  the operation tombstone when absent, record `finalized`, and remove the open
  prepare;
- anything else: preserve the prepare and private recovery bytes and fail
  closed.

The returned `RecoveryReport` describes the resulting authoritative head, not
the work performed by the current invocation. Repeating recovery after cleanup
therefore returns the same report and leaves the same authoritative files.

U3 extends U2's private persistence seam only. State bytes remain caller
supplied and cannot be claimed to be semantically bound to a K2 `Transition`.
No production module imports the repository or recovery implementation.

## 2. Decision provenance

### 2.1 Derived from accepted upper-level design

- ADR-005 Sections 5 and 6 make head replacement the commit point, require
  recovery before writes under `StateLock`, and define exact base rollback,
  exact target roll-forward, operation-tombstone repair, idempotence, and
  fail-closed ambiguity.
- Migration plan Sections 7 and 8 require process-termination faults at every
  durable boundary and convergence to base or target without a domain
  transition.
- U2 fixes the canonical head, commit, prepare, operation, generation, intent,
  lease, CAS, record limits, and immutable-publication contracts. U3 does not
  change those records except that `mission-prepare/1.projections`, previously
  fixed to an empty array for U2, now carries the recovery bindings required by
  ADR-005.
- U1 fixes each effect at at most 4 MiB, at most 64 effects, at most 16 MiB in
  aggregate, a safe relative path, immutable captured bytes, and the private
  same-filesystem generation stage. U3 uses the same bounds for compatibility
  projections and does not introduce a second byte/count limit.
- The current `_PublishedFilesTransaction` records whether a target existed,
  the exact previous bytes, and file/directory identity; rollback verifies both
  identity and bytes and preserves the only previous-content copy on failure.
  U3 retains those invariants durably across process termination.

### 2.2 New U3 decisions delegated by Issue #504

The upper design does not fix the following details. U3 fixes them here:

1. Every U1 effect binding is also the isolated repository fixture's
   compatibility-projection binding. Its `relative_path` is resolved beneath
   the project root that contains `.mission-state`; absolute paths, traversal,
   symlinked parents, and repository-internal targets reject. Missing parent
   directories are created one component at a time as mode `0700`, matching the
   observed legacy publisher behavior; they are non-authoritative residue and
   are not removed by transaction rollback.
   P1 may replace this private mapping when it introduces typed application
   effects, but production routing is still prohibited.
2. Projection recovery bytes live in
   `transactions/projections/<TransactionId>/`. The directory and files are
   mode `0700`/`0600`, on the repository filesystem, and strictly validated.
3. Each projection's `after` file is written and fsynced before prepare. Commit
   publishes it with a no-overwrite hard link after moving an existing base
   target into the same private bundle. Thus the prepare can bind the exact
   after inode before exposure, and recovery always retains a link from which a
   missing target can be recreated.
4. Projection identity is `(st_dev, st_ino, st_mode, st_size, st_mtime_ns)`.
   Link count and ctime are phase-dependent and are not identity fields. Exact
   content length and SHA-256 are checked in addition to this identity. The
   immediate target-parent identity `(st_dev, st_ino, st_mode)` is also bound
   in the durable prepare. Recovery pins that directory and rejects a renamed
   or replaced parent before accepting a projection write.
5. A bounded immutable resolution marker is written at
   `transactions/resolved/<TransactionId>.json` before the open prepare is
   removed. It records `rolled-back|finalized` plus transaction, operation,
   intent, base/target, and resulting-head identity. Its encoding reuses U2's
   4 KiB operation-record limit because it is an operation/result-sized
   tombstone and its maximum schema shape is smaller than `mission-operation/1`.
6. A rolled-back marker prevents reuse of that session-local operation ID with
   another intent. The same intent may be admitted again only from the current
   head. A committed operation record remains authoritative over a prior
   rolled-back marker for the same intent.
7. At most one open prepare is valid. Multiple entries, an unexpected entry,
   or an open prepare for a different requested recovery session is ambiguous
   and blocks without mutation.
8. `recover(session_id)` returns a report containing only `session_id`, current
   generation, current head digest, current commit digest, and readiness. It
   does not contain timestamps, action counters, or repaired path lists, so its
   value is stable across repeated recovery.

## 3. Exact projection record

`mission-prepare/1.projections[]` uses the U1 effect order and is bounded by the
same maximum count and aggregate bytes. Each record has the exact keys:

```json
{
  "after": {
    "digest": "sha256:<64hex>",
    "identity": [1, 2, 33152, 123, 456789],
    "name": "after-000.blob",
    "size": 123
  },
  "base": null,
  "blob_id": "review-evidence",
  "parent_identity": [1, 4, 16832],
  "relative_path": "evidence/review.json"
}
```

When the target existed at stage time, `base` is instead:

```json
{
  "digest": "sha256:<64hex>",
  "identity": [1, 3, 33152, 99, 456700],
  "name": "base-000.blob",
  "size": 99
}
```

The base file name is a reserved destination inside the bundle; it need not
exist until commit moves the exact target inode there. `after.name` exists from
stage until finalization. Names are derived from the zero-based effect order,
not accepted from a caller. `after.digest/size` equal the effect record and the
captured bytes. Base bytes use the same per-file 4 MiB and aggregate 16 MiB
bounds; the sum of after bytes is already the U1 aggregate.

The bundle has no caller-authored manifest. The canonical durable prepare is
its manifest and binds every name, path, digest, size, and identity.

## 4. Recovery classification and projection state machine

Recovery strictly parses the sole prepare, validates every referenced immutable
record or private stage/bundle, and then reads the current head.

### 4.1 Base head

For each projection, only these states are accepted:

- target is the exact base identity and bytes; publication did not start;
- target is absent and the exact base inode is in the bundle; publication was
  interrupted after moving the base;
- target is the exact after identity and bytes, while the exact base inode is
  in the bundle; publication completed before the head commit point;
- for a newly created target, target is absent or is the exact after identity.

Rollback removes only the exact after target and restores only the exact base
inode. Any different inode or bytes blocks. A failure leaves the open prepare,
after link, and any moved base inode as a bounded content-verifiable recovery
residue. Later admission invokes recovery again and cannot commit while that
residue remains unresolved.

### 4.2 Target head

The head, commit, generation, state, effects, transaction ID, operation ID,
intent, fence, and target generation must all equal the prepare lineage. Each
projection target must be the exact after inode and bytes. If it is missing,
recovery recreates the link from the exact private after file; a different
existing target blocks. The operation record is recreated from the verified
head/commit result only when absent. An existing operation record must agree in
every field.

### 4.3 Finalization order

After the selected recovery action verifies its postcondition:

1. publish or verify the immutable resolution marker;
2. remove the strictly verified projection bundle;
3. remove any still-valid private U1 stage for the transaction;
4. remove and fsync the exact prepare record.

Normal commit uses the same finalization helper. Therefore a crash in
finalization leaves the prepare as the recovery root. No open prepare is
deleted before the target/base postcondition is established. Once the exact
resolution marker exists, recovery also accepts a missing verified base backup,
private after link, or projection bundle as a completed prefix of cleanup, but
only after re-verifying the authoritative target/base projection state.

## 5. Fault points

The fault callback is invoked at these exact process-termination boundaries:

```text
after-stage
after-prepare
after-generation-publish
after-projection:<zero-based-index>
after-commit-publish
before-head-replace
after-head-replace
after-operation-publish
after-lineage-verify
after-resolution-marker
during-projection-cleanup-base:<zero-based-index>
during-projection-cleanup-after:<zero-based-index>
after-finalize
```

Tests run the committing code in a child process and terminate it with
`os._exit` at each point. `after-stage` has no prepare and may remove only a
fully validated private stage/bundle. `after-finalize` models loss after all
durable work but before caller output; the same operation retry returns the
recorded result.

## 6. Explicit non-scope

- No mark/quarantine/purge or retention policy (U4).
- No public ADR-005 `stage(admitted, transition, blobs)` or semantic
  command-to-state binding (P1).
- No v5 production session, CLI route, reader route, or default change.
- No domain transition, provider execution, external send, v4 migration, or
  pass-gate change.
