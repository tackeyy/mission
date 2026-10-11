# Issue1011 validation

Scope: [private operation copies and evidence](https://github.com/tackeyy/mission/issues/1011).
[Guardian/process custody](https://github.com/tackeyy/mission/issues/1012) is a
separate dependent change. Parent Issues1009/897 remain tracking issues. This
inactive primitive adds no provider, process, SDK, model, credential, budget
writer or Mission terminal entrypoint. Local fixtures are not host observations.

## Contract and trust boundary

The trusted caller selects request/snapshot bytes, their root-relative paths and
digests. Digest stability does not redact secrets in arbitrary bytes; callers
must exclude capability/credential material and unselected native dumps. Evidence
is a closed projection of session/boot/operation identifiers, request/context
digests and custody enumeration. Unknown/private fields are rejected.

The caller selects an owner-private trusted parent; ancestors above this root
belong to the OS-user boundary. Each operation pins its directory identity,
requires0700 directories/0600 single-link regular files, and checks owner,
device/inode and stable reader output. Copy bytes total at most8MiB separately.
TTL is created+600 seconds; backward/invalid clocks preserve data, and force must
be an actual boolean. Operation cleanup never proves process success or custody.

Evidence plus non-input metadata/journals reserve at most16MiB logical bytes:
257 immutable manifest generations at48KiB each plus515 cleanup journals at1KiB
reserve13,159,424 bytes, leaving3,617,792 bytes for selected evidence.256 payload
files cap both kinds together. Journals fit their actual encoded-byte bound;
manifest generations use O_EXCL rather than replacing a fixed metadata path.
These are stricter internal caps; filesystem block overhead is not measured.
The fixed evidence projection normally reaches the file cap before its byte cap.

## Identity-bound deletion and recovery

Cooperative writers/GC serialize through the original operation lock. Public
names are never directly unlinked: a closed0600 journal is fsynced before atomic
rename into a random exclusive0700 quarantine. Captured owner/mode/link/device/
inode must match before the private slot is deleted. A replacement is retained;
there is no overwriting rename restore or destructive adoption.

After payload and older manifest cleanup, retirement journals bind the parent,
operation directory and final manifest/lock identities. While holding the
operation lock, cleanup captures the directory itself, revalidates it, then
removes the final two files inside its private slot. Startup can resume a verified
slot after its source lock disappeared. A source still present requires its
original lock; pending retirement refuses new writes.

Known missing files are idempotent; each known identity is attempted even after
another fails. Unknown files, source+slot together, substitutions or malformed
journals remain preserved with cleanup False. Retries observe the existing bound
journal rather than allocating another. The operator must inspect unknown
quarantine material outside this API; automatic restore/adopt is unavailable.

If interrupted after journal deletion but before quarantine rmdir, the empty
quarantine lacks durable ownership proof and remains False. Current named/open
inode equality alone cannot authorize its deletion. Initial manifest failure
likewise retains unknown operation material: no complete manifest authorizes
adopting even its known-looking lock. Repeated startup preserves these bounded
residuals without allocation. Recovery is deliberately conservative, not
unconditional crash reclamation.

The portability guarantee covers concurrent same-user replacement of public
names beneath the trusted root. Arbitrary same-user chasing/tampering inside
private random quarantine namespaces is outside this guarantee.

## Test value and execution

`test_issue1011_private_operation.py` exercises actual filesystem input removal,
cumulative/exact copy boundaries, closed evidence projection, identical-byte
inode substitution, partial cleanup retry, immutable-generation collisions and
expiry/clock boundaries. It preserves the existing strict-reader/private-job
primitive tables rather than multiplying them at this wrapper.

Deterministic syscall seams expose stat-to-capture public replacement, operation
directory exchange, journal interruption before/after capture and slot deletion,
and directory retirement interruption around final metadata deletion. The
journal-deletion case explicitly retains the unbound empty directory. A reopened
writer case exposes writes after a durable retirement decision. Integer-only manifest/journal identities reject float-equivalent bindings. A
maximum-length256-record case exercises all257 complete generations, refuses
record257 without mutation and leaves recovery journal budget before full
cleanup. A separate descriptor exercises actual flock contention. No sleep or
subprocess is added. The fixed snapshot3 suite passed46 tests in1.14s locally, not CI cost.

`test_issue1008_host_observation.py` and `test_issue1008_host_ipc.py` retain identity,
parser/deadline/replay/FD protections. `test_issue920_exec.py` and
`test_issue921_verifier_deadline_exec.py` retain process cleanup. The tracked-tree shard selector places this file in shard8/8 at this checkpoint
(via `scripts/ci_shard_targets.py` and `.github/workflows/ci.yml`). Writer inventory
fragments classify bounded file opens/writes and quarantine rename/unlink sinks;
no spawn candidate or inventory exemption is added. Source lib mirrors are
byte-identical; tests are excluded by the existing distribution-sync contract.

## Independent exploration and remaining gates

Immutable snapshot1 used actual persistence/reader dependencies.136 inputs
observed45 accepts,91 typed rejections, zero uncaught exceptions. A synchronized
real-filesystem counterexample exchanged a public name between stat/unlink,
deleting another inode. Quarantine capture now replaces that mechanism for
payload, immutable metadata and operation-directory retirement.

The initial campaign reached256 files at162304 evidence bytes; direct16MiB body
acceptance was not measured or promised. A lowered-ceiling test verifies quota
accounting with selected records. Copy boundaries use8MiB minus one/exact/plus
one. Invalid force flags now reject before deletion.

Independent exploration against immutable snapshot3 completed324 inputs:
136 retained cases,98 journal/manifest/retirement cases,18 crash/quota/source-race
cases and72 exact-integer guard cases. Expected-result differences, false
accepts/rejects and uncaught production exceptions were all zero. All snapshot
hashes matched before/after. Real filesystem/persistence calls and deterministic
trace checkpoints were used without stubs or limit/counter injection.

Six float/bool bindings accepted by snapshot2 were rejected by snapshot3:
manifest directory components, manifest lock size and journal parent components.
The root independently reran snapshot3 with actual dependencies (46 passed
in1.05s). Its separate snapshot2 public-path exchange reproduction observed
cleanup False, replacement retained in quarantine and original inode preserved.
Cleanup True means known private namespace targets are absent/reclaimed, not
proof of physical inode deletion or process custody.

Snapshot3 maximum selected256 records and257 complete generations used5,279,801 logical
bytes: manifests5,117,497 plus evidence162,304. The campaign did not measure
other-UID ownership, other-device substitution, I/O/fsync fault coverage,
physical crash durability, exact raw16MiB payload/48KiB manifest boundaries,
filesystem block overhead or host/guardian/model/SDK/auth/production behavior.
Trace cuts emulate instruction checkpoints, not power loss; the unit suite's
initial metadata fsync refusal is separate from broad durability validation.
The schema/file caps reject before the raw byte boundaries above.

Independent session review, Checker and required CI remain separate pending
gates. This private safety boundary is one concern split from Issue1009;
further separating writer/recovery would leave cleanup without its durable
identities. Guardian/process guarantees remain in Issue1012 and are not replaced
by these model-free file tests.

Final store/inventory/module/mirror verification passed632 tests in11.67s;
tracked-tree artifact/vendor hygiene passed9 tests in3.93s. Mirror lines are excluded only by the repository
allowlist; reviewed size is measured on each committed candidate. The600-line explanation is the indivisible
private writer/recovery identity boundary above; total remains below1400.
New modules add no syntactic spawn sink. Public-content precheck found no
credential-like value, concrete secret URI, personal contact or private path.

## Review-r1 corrections

The first review found capacity reuse after write/rollback both failed, and
startup adoption of a same-bytes replacement manifest inode. Real-filesystem
faults reproduced six failing cases before correction. Any private create/write
failure now seals the live instance against further input/evidence additions;
existing reads and conservative cleanup remain available. Every immutable
manifest records its own exact-integer device/inode from the O_EXCL descriptor
before encoding; startup requires that binding for every accepted generation.
Initial/older/latest replacement files remain unknown. Snapshot4 freezes these
guards; snapshot3 exploration does not validate this correction. The new cases
use actual filesystem calls with synchronous fault seams, not physical crashes.
Root reran snapshot4 with actual dependencies:57 passed in1.33s; its independent
same-bytes manifest exchange retained both replacement and original inode and
reported no operation removal. Related tests/inventories/mirror/hygiene passed
652 tests in16.96s locally. Independent new-guard/fault exploration is pending.
