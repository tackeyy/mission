# Issue1016 validation

Scope: [fixed private cleanup anchor](https://github.com/tackeyy/mission/issues/1016).
[Store integration](https://github.com/tackeyy/mission/issues/1017) retains the
copy/evidence/TTL/capacity obligations of parent Issue1011. This primitive remains
unconnected to store/runtime/provider/process/budget/Mission terminal behavior.
PR1013 retains its original review ledger, two consumed rounds and the prior
quarantine-retirement finding; splitting does not create review rounds.

## Contract and trust

Only explicit `initialize_anchor(parent)` creates the fixed `cleanup-anchor`.
The caller must durably retain its returned immutable `AnchorBinding` before
relying on restart recovery. `open_anchor(parent, binding)` never allocates or
adopts on missing/mismatched binding. A failed bootstrap/handoff leaves an unknown
fixed anchor; retry refuses to create another. The primitive does not infer a
binding storage location. Marker/journal/binding have no credential/capability
input fields; the caller owns source selection.

The closed binding records parent, anchor and marker device/inode. Marker bytes
record parent/anchor plus marker self identity from its O_EXCL descriptor.
Caller-bound values must match current owner-private directories0700 and regular
single-link files0600; nofollow opens, exact integer pairs, duplicate/unknown
fields and same-bytes inode substitutions are checked. Marker flock serializes
cooperative cleanup handles without another lock file.

The caller selects a trusted owner-private parent; ancestors above that trust
root belong to the OS-user boundary. Public source/anchor names may change
concurrently. Dedicated anchor internals are exclusively managed here; arbitrary
same-user tracking/tampering inside that namespace remains outside the guarantee.
Root anchor itself is fixed residual and is never removed by its public name.

## Capture, recovery and bounds

`CleanupAnchor.remove_owned(name, expected, expires, now, kind, force, inner)`
first persists a bounded closed journal inside its anchor, then atomically
captures the public source into that Q's slot. Captured owner/mode/link/device/
inode are revalidated before deleting the private slot. Slot, journal and Q
retirement use dedicated private directory FDs; a public Q basename or root
anchor replacement is untouched. Ordinary overwrite-restore is unavailable.

`recover(now, force=False)` reuses existing journals. Known missing targets are
idempotent namespace cleanup, not proof of physical inode deletion or process
custody. Unknown entries, substitutions, malformed/partial journals and source+
slot coexistence remain False; other known journals can still be attempted.
Journal deletion interrupted before Q rmdir leaves an empty unbound Q retained
False. Retries do not allocate another Q for that bound request. Deadlines are
caller-supplied; the later store is responsible for its600-second TTL policy.

An optional inner binding is durably recorded in the outer journal. Only after
outer directory capture and slot identity verification may inner registration
and pending Qs be disposed through the captured parent FD. Missing inner marker
or anchor can be resumed from the outer binding after partial retirement. An
unknown inner inode remains preserved. Inner disposal does not recursively enter
another inner-binding journal: one captured-inner level bounds this contract.
Final store manifests/locks are not wired by this primitive.

Per anchor: marker at most1024 encoded bytes; at most515 pending Qs/journals and
one slot per Q; each journal at most1024 encoded bytes. The writer's logical
metadata ceiling is528,384 bytes per anchor, excluding existing captured payload
bytes and filesystem block overhead. Source bytes are renamed, not copied.
Normal repetitions leave only the fixed root anchor and its marker. Retained
unknown Qs consume the same cap. All root/operation anchor accounting must be
included by the later store; this does not reuse its previous16MiB reservation.

## Test value and evidence

The new suite uses actual local filesystem calls and deterministic syscall
fault seams, without subprocesses or sleeps. Explicit bootstrap/open and outer
capture/inner disposal began with missing-API Red. Recursive inner disposal and
noncanonical Q acceptance each had a failing-before/passing-after regression.

Public Q-basename/anchor exchange fixtures preserve replacement inode(s), while
source exchange preserves both original bytes and the unknown captured slot.
Restart cuts cover before/after capture, slot/journal deletion and inner marker/
anchor deletion. Partial journal writes and bootstrap fsync failure preserve
bounded unknown material without repeated allocation. Closed registration,
symlink/hardlink/mode, real flock contention, actual515-Q capacity and normal
repetition/missing-target cases exercise distinct authorization/persistence stages.
These replace the full-store suite in this PR; later store tests must retain its
own capacity, manifest-self-identity and write-failure seal protections.

Primitive tests:35 passed in0.24s; related writer/spawn/module inventory, mirror
and artifact/vendor hygiene checks passed630 cases in30.00s under local load.
These are local elapsed times, not CI cost. Independent50-input exploration
remains pending at this checkpoint. The old57-case full-store
baseline passed in52.66s under local load; it is historical, not anchor coverage.
Independent session review, Checker, exact-head CI and latest-base integration
remain separate gates. Other-UID/device behavior, physical crash durability,
filesystem block overhead and real SDK/host/model/auth/production behavior are
unmeasured. Synchronous instruction cuts do not prove power-loss durability.

The single concern is the caller-bound anchor/journal/capture/disposal contract;
its tests and persistence proof travel together. This explains the600-line band.
Store integration is already separated rather than reducing its guarantees.
