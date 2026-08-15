# ADR-005: Typed Mission Kernel and Recoverable Unit of Work

## Status

Accepted

## Date

2026-08-14

## Relationship to earlier decisions

This ADR extends [ADR-002](./002-typed-mission-state-objects.md). ADR-002 remains
Accepted, including its typed Finding, Score, Decision, and grounded `next`
direction. This ADR supersedes only ADR-002's physical-storage decision for
schema v5 writers: v1-v4 sessions continue to use one JSON session file, while
v5 uses an atomic head record that refers to an immutable state generation and
an immutable commit record. It does not change the status or review-tier
contract in [ADR-003](./003-adaptive-review-gating.md).

## Context

At inspected HEAD `f0ac6aea`, `skills/mission/bin/mission-state.py` is 16,898
lines and combines four different responsibilities:

- domain decisions such as phase, terminal outcome, score, and pass gates;
- use-case orchestration such as review import/finalize and provider handoff;
- local persistence, locking, lease enforcement, and file publication;
- command-line parsing, environment access, clocks, random identifiers, Git,
  subprocesses, and user-facing output.

The current implementation already has safety mechanisms that must remain
effective throughout migration:

- schema v1-v4 are readable, a missing key alone is legacy-compatible, and
  future or non-integer schema versions fail closed;
- mutating session commands use a fenced lease with an explicit token,
  monotonic `fencing_epoch`, expiry, and takeover history;
- the four artifact publication commands hardened by #475 publish files only
  after lease validation and roll them back on an in-process failure;
- review, score, specialist, and plan evidence use strict file validation and
  content digests;
- providers supply evidence but do not own mission state, review, score, or
  completion authority;
- `mark-passes` mechanically enforces provenance, findings evidence,
  `open_high`, score, agreement, artifact, and required-specialist gates.

The lease-first rule is the required target boundary for every authoritative
file publication, but it is not yet universal: for example, the inspected
`manual-score-capture` and `specialists plan-import` paths can publish evidence
without first enforcing the session lease. The migration closes those gaps; it
does not describe them as already implemented.

The current rollback transaction cannot by itself establish a complete Unit of
Work contract. It has no durable state generation CAS, no commit record, no
cross-process crash decision, operation idempotency, or collection protocol for
unreferenced generations. A pure kernel also cannot be extracted safely if
`derive_next` and the command validators remain independent decision trees.

## Decision

### 1. Boundary and dependency direction

The dependency direction is:

```text
CLI / stop guard / provider adapters
                |
                v
        application use cases
                |
          +-----+------+
          |            |
          v            v
   typed pure kernel   ports
                       |
                       v
       local repository / UnitOfWork / provider adapters
```

The typed kernel has no dependency on `argparse`, environment variables,
filesystem paths, file descriptors, clocks, random-number generation, Git,
subprocesses, provider registries, or output formatting. Those values are read
by adapters, strictly validated by an application use case or port, and passed
to the kernel as typed facts.

The kernel owns:

- the versioned canonical `MissionState` aggregate;
- closed state-object unions and aggregate invariants;
- `decide(state, command) -> Transition(new_state, events, effects)`;
- the transition table used by both `decide` and `derive_next`;
- completion authority, including the machine-enforced pass gate.

Application use cases own transaction sequencing, evidence validation through
ports, and conversion from verified observations to commands. They depend on a
small `MissionRepository` port. `LegacyV4Repository` implements that port with
the current behavior-compatible transaction and is explicitly not claimed to
implement the crash-recoverable protocol. Schema v5 is implemented by the
stronger `RecoverableUnitOfWork`, which also implements `MissionRepository` and
owns durable local publication. Adapters own CLI syntax, local I/O, and external
provider execution.

Sidecars with independent lifecycles are not forced into `MissionState`.
Pregate cache, merge queue, evidence handoff, parallel-group manifests, command
outcome telemetry, worktree archive, and archive compaction remain separate
aggregates. Administrative commands may coordinate several aggregates but may
not bypass each aggregate's own validation and commit protocol.

### 2. Versioned typed aggregate

Schema v5 defines a canonical aggregate whose closed unions include:

| Object | Closed variants or values |
|---|---|
| `Phase` | `planning`, `executing`, `reviewing`, `scoring`, `done`, `halted` |
| `TerminalOutcome` | the nine values currently defined by `mission_common.py` |
| `Plan` | `absent`, `core`, `provider`; a present plan contains the current source, selection, immutable reference, iteration, and generation |
| `Handoff` | `absent`, `prepared`, `consuming`, `consumed`, `rejected`; each non-empty value is bound to one plan identity |
| `Review` | `input-evidence` or `aggregate-evidence`, each with its existing immutable lineage; absence is an empty collection rather than an invented lifecycle status |
| `Finding` | severity `High`, `Medium`, `Low`; `open`, or `resolved` with prior-finding identity plus immutable resolution evidence and time |
| `Score` | `none` or a provenance-bearing score bound to review/manual evidence and revision scope |
| `Lease` | `legacy-absent` for read compatibility, or one complete fenced lease record |

`accepted-risk` and `not-reproducible` are deliberately absent from the
`Finding` status union because ADR-002 documents them but the inspected code
does not implement them. Legacy review findings have no enforced status: the
canonical decoder maps a missing or ignored legacy status to `open`, and never
interprets an arbitrary legacy string as a resolution. `open_high` counts only
`High` findings whose canonical status is `open`. Schema v5 can represent
`resolved` only with a prior finding ID/generation, immutable resolution
evidence reference, and resolution time, but no command in this migration
creates it. A future `ResolveFinding` transition must define how that evidence
is produced in a separate ADR/Issue. Thus all v4 behavior remains equivalent
and v5 writers in this migration emit `open` only.

Legacy decoding is field-explicit and audit-lossless without pretending to be
a physical migration:

| v1-v4 field family | Canonical read mapping |
|---|---|
| phase and terminal flags | preserve the current phase; derive the existing nine-value terminal outcome with `mission_common.py` |
| canonical plan/provider plan | `absent`, `core`, or `provider`, preserving current source, source ID, selection source, iteration, generation, path, and digest |
| executor handoff | preserve exactly `prepared`, `consuming`, `consumed`, or `rejected`; absent stays `absent` |
| review input/aggregate refs | preserve kind, path, digest, size, iteration, perspective, group, generation, and revision bindings that are present |
| finding | preserve finding payload and evidence reference; assign canonical `open` because legacy status has no enforced authority |
| score history | preserve score values, iteration, agreement, `open_high`, provenance, evidence refs, revision scope, and force-approval lineage |
| lease | all fields absent becomes `legacy-absent`; otherwise every lease field plus history must validate as one complete fenced record |

Unknown v1-v4 fields are retained in a `legacy_passthrough` map used only when
the v4 compatibility writer rewrites that same session. They are never treated
as typed authority and are never promoted into v5. Golden fixtures are built
from the current plan, handoff, review, score, lease, and terminal test corpus;
typed projections must produce the same current decisions and v4 writes must
preserve unowned legacy fields.

The decoder accepts a missing schema key and schema v1-v4 under their fixed
compatibility rules, producing an in-memory canonical view without rewriting
the source file. It accepts v5 only after validating the complete v5 envelope.
Values above the reader's maximum version, booleans, strings, floats, null, and
partial lease objects fail closed. Unknown keys inside closed v5 objects also
fail closed; explicitly declared extension maps remain the only open surface.

Every v5 head, state generation, commit record, prepare record, and manifest is
canonical JSON: UTF-8, bounded by a record-specific constant, duplicate-key
rejecting, finite-number-only, and encoded with one deterministic key/number
form. Readers open with no-follow semantics, require a regular single-link file,
and compare `lstat`/`fstat` identity and exact size before and after the bounded
read. Invalid UTF-8, `NaN`/`Infinity`, duplicate keys, trailing data, unknown
closed keys, links, FIFOs, hard links, oversize input, and identity swaps all
fail closed before a head or generation becomes authoritative.

The writer is selected by the loaded session, not by each invocation:

- a missing/v1-v4 session remains on the v4 compatibility writer for its
  lifetime unless a separately approved migration is introduced;
- after final cutover, a newly initialized session uses the v5 writer;
- a v4 reader encountering v5 continues to reject it as a future schema;
- there is no automatic in-place v4-to-v5 rewrite.

This gives dual-read/single-write behavior per session and prevents two storage
formats from being authorities for the same state.

### 3. One transition table for decisions and guidance

Each transition rule has a stable identifier, command type, allowed source
state, guard, reducer, emitted event types, requested effect types, and optional
guidance metadata. `decide` finds exactly one matching command rule or returns a
typed rejection. Guidance metadata declares `eligible`, a total integer rank,
a stable tie-break identifier, and typed continuation edges for multi-step
recipes. Maintenance commands such as progress,
activity, and generic property updates are not primary-guidance eligible.
`derive_next(state: MissionState, guidance: GuidanceFacts)` selects the unique
lowest-ranked enabled primary rule and never uses a second handwritten phase
tree; equal rank is a table-definition error, not runtime fallback. `GuidanceFacts`
is used only to choose guidance and must not be used for command accept/reject;
command accept/reject remains governed by the transition-table command rules and
`MissionState`.

Guidance distinguishes local commands from external work. A command template
is emitted only when the table proves that a representative valid command of
that type is accepted from the current state. An external step such as running
a planner or reviewer names the typed observation required by its follow-up
command; it is not claimed to be an immediately executable local command.

The transition-table property suite uses finite equivalence classes for score
thresholds, finding counts, agreement, iteration/max-iteration, plan/handoff,
lease, and terminal state. It performs bounded exhaustive graph traversal from
valid initial states and checks these properties:

1. every reached state satisfies aggregate invariants;
2. each accepted `(state, command)` has exactly one rule and deterministic
   output;
3. each local command sequence presented by `derive_next` can be materialized
   with valid typed fixtures and executed by repeated `decide` calls, selecting
   each continuation from the state produced by the preceding step;
4. each external step's declared follow-up command is accepted after adding
   only the named verified observation;
5. terminal states expose no ordinary mutating transition; explicit audited
   reactivation and stale recovery remain distinct;
6. pass guidance is impossible unless the same pass rule accepts the state;
7. invalid evidence, missing authority, stale fences, and unknown union values
   are rejected without a new state or effects;
8. every reachable active nonterminal state has exactly one primary guidance
   sequence after ranking, and no terminal state has one.

This is a property test of the table rather than a second list of example
sequences. It uses deterministic pytest generators/BFS so no new runtime or
test dependency is required.

### 4. Effect model

Kernel effects are inert requests such as publishing a validated blob,
materializing a compatibility projection, or updating the active-session
index. They do not execute I/O. Each effect contains a logical kind and verified
`blob_id`, digest, size, and reference identity, not bytes or an unchecked
absolute path. A strict adapter captures bounded source input into an immutable
`VerifiedBlobSet` of byte strings before `decide`. `stage(snapshot, transition,
blobs)` requires a one-to-one match between effect descriptors and that set and
rehashes the immutable bytes while writing staging. Missing, extra, changed, or
mis-bound blobs reject the entire stage. Mutation of the original source path
after capture therefore cannot change the staged bytes.

Non-rollbackable external execution is not put inside the local UnitOfWork.
Provider use cases retain a prepare/approve/record-running/dispatch/reconcile
protocol:

1. commit the exact outbound packet and approval binding;
2. commit a fenced dispatch intent with a stable operation ID before process
   dispatch; its state is `dispatch-unknown` until a receipt is committed;
3. dispatch through the isolated provider adapter and persist its PID/process
   identity or provider receipt in a later commit;
4. import returned bytes as untrusted evidence;
5. commit a terminal invocation through a later UnitOfWork.

A crash after dispatch intent but before a receipt cannot distinguish
"not spawned" from "spawned but receipt not persisted". It therefore never
automatically retries; fenced reconciliation must prove an external identity or
record `abandoned-unknown`. A crash after a receipt reconciles only that exact
identity. This is an explicit saga state, not a false claim that a local
filesystem transaction rolled back an external action.

### 5. UnitOfWork protocol

The local UnitOfWork is a protocol, not merely an exception handler:

```python
class MissionRepository(Protocol):
    def read(self, session_id: str) -> Snapshot: ...
    def execute(self, request: ExecutionRequest) -> CommitResult: ...

class RecoverableUnitOfWork(MissionRepository, Protocol):
    def begin(self, request: ExecutionRequest) -> AdmittedSnapshot: ...
    def stage(self, admitted: AdmittedSnapshot, transition: Transition,
              blobs: VerifiedBlobSet) -> PreparedCommit: ...
    def commit(self, prepared: PreparedCommit, precondition: CommitPrecondition) -> CommitResult: ...
    def recover(self, session_id: str) -> RecoveryReport: ...
    def collect(self, policy: RetentionPolicy) -> GCReport: ...
```

`ExecutionRequest` explicitly contains session ID, typed command, immutable
`VerifiedBlobSet`, caller-stable operation ID, normalized intent digest, and
presented lease identity; neither repository reads them from ambient process
state. `Snapshot` is the read-side canonical view and includes `MissionState`,
`GuidanceFacts`, `SnapshotProvenance`, the head generation, and the digest.
`AdmittedSnapshot` contains `base: Snapshot` plus a *pending* acquire/renew/
verify/takeover decision and the complete target lease/fence values. Admission
is a read-only calculation, not a lease-only commit. `CommitPrecondition`
includes the exact base
generation/head digest and pending lease decision. Neither the lease token nor
raw intent is copied to command outcome logs or public artifacts; commit records
retain only the normalized intent digest.

The protocol is:

1. **Recover, load, and provisionally admit**: under `StateLock`, recover earlier
   transactions, strictly load the current head/generation, check the operation
   tombstone index, and calculate acquire/renew/verify/takeover against that
   snapshot. Return an `AdmittedSnapshot` whose canonical view already contains
   the pending target lease. No head, generation, or public file changes.
2. **Decide**: outside any public-write step, run the pure kernel against the
   admitted canonical view. A rejection discards the pending grant and changes
   no authoritative state or effect.
3. **Stage**: create a private same-filesystem transaction directory with mode
   `0700`; write proposed state, evidence, artifact bytes, projection backups,
   manifest, and digests as mode `0600`, non-symlink, single-link regular files;
   fsync files and directories. Nothing is yet an authoritative reference.
4. **Re-enter and recover**: under `StateLock`, resolve any earlier transaction
   for the session before admitting the prepared commit.
5. **Preconditions**: re-read the head and require exact base generation/head
   digest CAS; recompute and require the identical still-valid pending lease
   decision; revalidate every staged object and destination identity. Commit
   makes no new lease decision. If the base moved, lease expired, or takeover
   result changed during decide/stage, discard staging and repeat begin, decide,
   and stage against the new snapshot.
6. **Prepare record**: atomically publish and fsync a durable transaction record
   bound to base generation, target generation, fence, staged manifest, and
   projection backups, plus operation ID and normalized intent digest.
7. **Immutable publication**: publish state and authoritative evidence as an
   immutable content-addressed generation using no-overwrite semantics. An
   existing name is accepted only when bytes, size, and digest are identical.
8. **Compatibility projections**: while v4 paths still have consumers, apply
   them with the existing strict no-follow/regular-file validation and retained
   rollback bytes. This step occurs only after lease/fence/CAS validation and a
   durable prepare record.
9. **Commit point**: publish an immutable commit record and atomically replace
   `.mission-state/sessions/<sid>.json` with a v5 head record referring to the
   commit and state generation; fsync the containing directory. The target
   generation contains both the already-decided lease update and domain
   transition. Generation is monotonic and only exact `N -> N+1` is allowed.
10. **Derived updates**: update rebuildable indexes such as `aggregate.json` and
    verify all projections. A command reports success only after this finishes.
11. **Finalize**: mark the transaction finalized, publish the bounded operation
    tombstone/result, and remove staging/backups that are no longer recovery
    roots.

The head replacement is the logical commit point. Immutable objects published
before it are unreferenced and invisible to authoritative readers. A legacy
compatibility path can be transiently visible before the head moves, so the
durable record and recovery rules are mandatory until all consumers resolve
immutable references.

The immutable commit record contains transaction ID, operation ID, normalized
intent digest, base and target generation, state/effect references, their
digests and sizes, fencing epoch, and commit time. It contains neither arbitrary
command input nor provider secrets. Reusing an operation ID with the same
intent returns the recorded `CommitResult` even after recovery; reuse with a
different intent fails closed. Thus a crash after head replacement and a retry
of the same operation cannot append twice. Mutating v5 CLI calls must receive a
stable operation ID from their caller; absence never triggers an automatic
retry. Events remain bounded audit facts inside state/commit data and are not a
replay authority.

Each successful operation also has a small immutable tombstone containing
operation ID, intent digest, commit digest, and a bounded `CommitResult`, but no
state-generation reference. A matching retry returns that result; a different
intent fails closed. Tombstones remain non-reusable idempotency authority even
after retention permits the old commit record and state generation to be
collected. The commit digest is an audit identifier, not a GC liveness
reference; only fields explicitly typed as retention roots keep an object live.

### 6. Crash recovery

Recovery runs under `StateLock`, is idempotent, and makes no new domain
decision. It uses the durable transaction record and current head:

| Durable observation | Recovery decision |
|---|---|
| no prepare record | remove only verified private staging residue |
| head still equals base | restore/delete compatibility projections from verified backups, leave state at base, and mark rolled back |
| head equals target commit | verify or finish missing derived projections/indexes and operation tombstone from immutable bytes, then mark finalized |
| head is neither base nor target, or any identity/digest is ambiguous | fail closed; preserve evidence and block new writes |

Rollback failure preserves a bounded, content-verifiable recovery residue as
the current publication code does. Recovery never guesses which bytes are
correct, never follows links, and never allows a new transition while an
ambiguous transaction remains.

After recovery, an operation lookup is performed before a new domain decision.
An already committed matching operation returns its prior result; a rolled-back
operation may be restaged only from the current head, and an operation/intent
collision blocks. This covers the caller-unknown interval after the head commit
but before derived updates, finalize, or stdout.

### 7. Garbage collection

Immutable generations are collected by mark, quarantine, and later purge:

- roots are every current session head, current archive pointer, explicit
  current/prior safety retention, and every prepared/head-committed recovery
  record; operation tombstones are retained but do not root state generations;
- a generation is a candidate only if it is unreferenced, older than a grace
  interval, strictly validated, and absent from all open transaction manifests;
- under the relevant lock, roots and head generation are re-read before a
  candidate is atomically renamed into a quarantine directory;
- a later pass purges only an unchanged quarantined object after a second grace
  interval;
- malformed paths, links, hard links, digest mismatches, concurrent head
  changes, or incomplete scans stop collection without deletion.

GC does not rewrite state, compact events, or decide domain outcomes. A dry-run
report precedes destructive mode, and current plus one prior generation may be
retained as an operational safety margin even when the prior generation is not
otherwise rooted. Old committed generations may be collected after grace once
they leave those roots; their small immutable operation tombstones remain so an
operation ID can never be forgotten and reapplied. Any tombstone compaction
requires a separate decision preserving non-reuse and result lookup semantics.

## Migration decision

Use a strangler migration. First add types and table-driven pure behavior with
no production route. Then extract application use cases while persisting the
existing v4 shape through a compatibility repository. Build and fault-test the
v5 UnitOfWork behind ports. Only after every mutating CLI family is explicitly
routed and every state consumer resolves a version-aware authoritative snapshot
does `init` start writing v5 for new sessions. Stop hooks consume only a Python
verdict; audit, archive, snapshot, and query readers verify head-to-generation
lineage. Existing v1-v4 sessions stay on their compatibility writer. There is
no dual write, no automatic physical rewrite, and no flag that can switch
writer format midway through a session.

Every stage must leave the current CLI usable if work stops there. Routing is
per command and single-owner: a command is either legacy or kernel-backed, never
both. Shadow comparison is allowed only for pure decisions and may not publish
files or state.

## Consequences

Positive:

- one table becomes the authority for both transitions and next-step guidance;
- stale snapshots and stale fencing tokens cannot commit even when a local lock
  was released during staging;
- state, evidence, and artifacts gain a durable crash decision and bounded
  cleanup path;
- typed use cases can be extracted incrementally without changing all CLI
  behavior at once;
- provider isolation and the mechanical pass gate remain core-owned.

Negative:

- v5 persistence has more local files and a recovery journal;
- small operation tombstones grow monotonically until a separately designed
  compaction can preserve non-reuse and result lookup;
- the CLI must support both v4 compatibility sessions and v5 sessions for a
  migration window;
- fault-injection, reader, recovery, and GC tests become mandatory release
  gates;
- external compatibility projections are only eventually repaired after a
  process crash until their consumers migrate to immutable references.

## Rejected alternatives

### Revise ADR-002 in place

Rejected because ADR-002's typed-object stages are already implemented and
Accepted. Rewriting its historical storage decision would hide that v1-v4 and
v5 deliberately use different persistence protocols. A new, narrowly
superseding ADR keeps both decisions auditable.

### Full rewrite of `mission-state.py`

Rejected because it would move phase, lease, evidence, provider, and pass-gate
boundaries at once, eliminate a safe comparison point, and make interruption or
rollback an all-or-nothing event. The strangler path keeps each command usable
and testable throughout migration.

### Database or background service split

Rejected because mission is a portable local tool. A database/service adds
availability, migration, authentication, deployment, and process-lifecycle
failure modes without removing the need to validate local evidence and fenced
writers. Ports leave a future repository replacement possible without paying
that cost now.

### Complete event sourcing

Rejected because replaying every event as the state authority would require
event-version migration, replay determinism for years of commands, compaction,
and new corruption recovery. Mission needs typed transitions and auditable
commit facts, not an event log as the source of truth. Events remain bounded
evidence attached to snapshots and commit records.

### Cloud-dependent persistence or recovery

Rejected because network, credentials, account state, and remote availability
would weaken offline portability and introduce a new authority outside the
local fenced session. The protocol remains local and filesystem-backed.

### Keep exception-only rollback

Rejected because an exception handler cannot run after process termination or
power loss and does not resolve stale-snapshot writes. Durable prepare records,
generation CAS, immutable generations, recovery, and GC are all required parts
of the protocol.

### Dual-write v4 and v5

Rejected because two independently writable representations can diverge and
make recovery ambiguous. Each session has exactly one writer format and one
authoritative head.

### Put filesystem and provider logic in the kernel

Rejected because nondeterministic I/O would prevent pure transition tests and
would allow provider behavior to leak into mission authority. The kernel emits
inert effects; adapters execute them only through application and UnitOfWork
boundaries.

### Relax existing safety boundaries during migration

Rejected. PID-only ownership, permissive file reads, mutable evidence,
publication before lease validation, provider-owned review/score/pass, or a
score-only completion gate would all reduce current protection. Compatibility
means preserving or strengthening fenced leases, strict file validation,
content-addressed evidence, provider isolation, and the mechanical pass gate.
