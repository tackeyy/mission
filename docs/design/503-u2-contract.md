# Issue #503 U2: fenced generation CAS / immutable commit-head exact contract

Status: **implementation contract**

Scope: migration plan Section 8 U2. The original contract predated crash
recovery (U3), but the admission and error-code descriptions below reflect the
current U3-integrated repository. Garbage collection (U4), production routing,
and the v5 default remain outside this contract.

## 1. Conclusion

U2 adds an isolated local v5 persistence protocol on top of the K1 state codec
and U1 immutable generation publisher. It is not the ADR-005 public
`RecoverableUnitOfWork`: U2 intentionally has no K2 dependency and therefore
cannot prove that caller-supplied state bytes are the result of a typed
`Transition`. Its staging entry point is a private persistence seam. P1 must put
the ADR-compliant `stage(admitted, transition, blobs)` boundary in place of this
seam and accept the sealed transition result issued by the kernel before this
protocol can satisfy the complete ADR-005 UnitOfWork contract. Production
routing to this seam is prohibited until that P1 replacement is complete.

Within that private seam, a `PreparedCommit` is a one-shot capability of the
exact `LocalFencedRepository` instance that staged it. Its carried digest is an
integrity comparison, not self-authenticating commit authority.

The logical commit point is one atomic replacement of
`.mission-state/sessions/<session-id>.json` with a canonical `mission-head/1`
record. Before that replacement, the base head is authoritative; after it, the
target head is authoritative.

The fenced compare-and-swap (CAS) compares all of the following under the one
repository lock immediately before any durable prepare or public publication:

1. current head presence or absence;
2. current head generation;
3. SHA-256 of the exact current canonical head bytes;
4. the pending lease decision digest carried by the prepared commit;
5. the unchanged base lease and the still-valid time condition for that pending
   decision.

Only exact `N -> N + 1` is accepted. A rejection before durable prepare
publication publishes no generation, commit, operation record, head, or
compatibility projection. A lease expiry detected after prepare or immutable
publication retains the prepare and any already-published immutable orphan and
publishes no head or operation record. A later `begin()` must complete U3
recovery before admission can continue; ambiguity or incomplete verified
cleanup remains fail-closed. A cleanup failure before prepare is surfaced rather
than reported as rejection or replay success.

## 2. Sources and decision provenance

### 2.1 Derived from accepted upper-level design

The following are not new U2 choices.

- ADR-005 Sections 2 and 5 require canonical bounded strict JSON, a durable
  prepare record, immutable generation and commit records, an atomic head
  replacement, caller-stable operation identity, exact generation/head CAS,
  pending lease admission, and `N -> N + 1`.
- ADR-005 Section 5 defines the public stage boundary as
  `stage(admitted, transition, blobs)`. U2 cannot implement that typed boundary
  before K2; its bytes-oriented stage is therefore private and must not be
  described as the ADR-complete UnitOfWork.
- ADR-005 makes head replacement the commit point. It also excludes lease tokens,
  raw intent, and raw provider secrets from commit/audit records.
- Migration plan Sections 4, 5, 8, and 9 keep U2 behind ports with no production
  session and preserve lease-first publication, content-addressed evidence, and
  single-writer authority.
- K1 fixes newly encoded v5 state-generation JSON as UTF-8 canonical JSON,
  rejects duplicate keys, invalid UTF-8, non-finite numbers, trailing data,
  unknown v5 closed keys, and records over `STATE_LIMIT = 4 * 1024 * 1024`
  bytes. Its decoder intentionally still accepts current CLI v1-v4 bytes,
  including their non-canonical pretty encoding, so U1/U2 can preserve those
  exact producer bytes during migration. K1 also supplies the immutable
  `MissionState`, `FencedLease`, `FrozenJsonObject`, and bytes-only decoder APIs.
- U1 fixes the generation manifest (`mission-generation/1`), immutable object
  references, `objects/`, `generations/`, and `transactions/`, no-overwrite
  publication, `MAX_BLOB_COUNT = 64`, `MAX_TOTAL_BLOB_BYTES = 16 MiB`, token
  lengths of 128 characters for blob ID/kind, relative paths of 4,096 characters,
  and a 4 MiB manifest limit.
- The current lease contract supplies a 900-second default TTL, exact-token
  renewal even after nominal expiry, expired no-token self-recovery with a new
  token, expired takeover, monotonic `fencing_epoch`, and retirement of the old
  token. Matching owner/session without the matching token while the lease is
  live is not ownership.
- The current session path is `.mission-state/sessions/<sid>.json`; U2 changes
  only the contents for isolated v5 test repositories, not production routing.

### 2.2 New U2 decisions delegated by Issue #503

These choices are new and require owner review.

1. exact `mission-head/1`, `mission-commit/1`, `mission-prepare/1`, and
   `mission-operation/1` field names and nesting;
2. the additional `commits/`, `operations/`, and
   `transactions/prepared/` directories and their filename rules;
3. `null` as the exact absent-head digest for generation zero;
4. a 4 KiB head limit, 4 MiB prepare/commit limits, 4 KiB operation-record
   limit, and 8 KiB encoded audit-record limit;
5. accepting bounded command/event category tokens in in-memory
   `AuditMetadata`, but persisting only one command-category digest plus at most
   64 event-category digests so raw lease/provider values cannot cross through
   that channel;
6. making operation IDs session-local and hashing the canonical
   `mission-operation-key/1` envelope containing both `session_id` and
   `operation_id` for the flat operation-record filename;
7. recomputing a pending lease with its original admission time, followed by a
   commit-start validity check and another validity check immediately before
   head replacement;
8. treating any non-empty `transactions/prepared/` directory as a U2
   commit-start block; the current integrated `begin()` delegates it to U3 and
   proceeds only after full transaction validation and successful recovery;
9. deleting the exact prepare record only after head, operation record, and
   result validation have completed successfully;
10. using a canonical immutable JSON object for `ExecutionRequest.command`, but
    never copying that object into a prepare, commit, head, operation record, or
    audit record;
11. pinning and flocking the repository-root inode in addition to pinning the
    named lock inode and every authoritative publication directory, then
    revalidating the named identity before and after each read, link, replace,
    unlink, and directory fsync;
12. separating repository `session_id` from explicit
    `lease_owner_session_id` in `ExecutionRequest`, because current lease
    admission distinguishes the target session from the acting owner while the
    upper protocol only fixed that both identities be explicit;
13. defining the upper protocol's otherwise-unspecified “normalized intent
    digest” as SHA-256 of a versioned canonical `mission-intent/1` envelope. The
    envelope binds `session_id`, `lease_owner_session_id`, `operation_id`, the
    exact canonical command, and every blob's ID, kind, relative path, digest,
    and size in canonical order. Only `audit` and the presented lease token are
    outside the intent;
14. binding the exact canonical request, admission/precondition, and private U1
    stage through an instance-private capability registry owned by the same
    `LocalFencedRepository` that performed `_stage_persistence`. Commit requires
    the registry digest, carried `PreparedCommit.binding_digest`, and current
    canonical recomputation all to match. A caller cannot create authority by
    recomputing an unkeyed digest in a replaced dataclass;
15. using one commit-start clock sample for the initial expiry decision,
    `prepared_at`, and `committed_at`. At the authority boundary, U2 writes and
    fsyncs the same-directory head temporary, invokes the
    `before-head-replace` fault callback, then performs a fresh head CAS and a
    fresh clock/lease validity check immediately before `os.replace`. A callback
    delay or temporary-file write cannot carry an expired lease into the head;
16. fixing expired takeover retirement reason to the closed non-secret code
    `lease-expired-takeover`, independently of caller-controlled audit metadata;
17. revalidating the U1 16 MiB aggregate effect limit in prepare, commit,
    manifest, and authoritative lineage readers, rather than trusting a record
    merely because each individual effect is within 4 MiB;
18. surfacing failed private-stage cleanup as `stage-cleanup-failed`; a failure
    to clean up may not return rejection or idempotent replay success.

## 3. Observations and limit derivation

### 3.1 Actual CLI corpus

The measurements below use
`skills/mission/tests/mission_state_fixture_corpus.py`. The production
`mission-state.py` is executed in an isolated temporary project by subprocess;
the measured bytes are the resulting
`.mission-state/sessions/test.json`, not a hand-authored state fixture.

| CLI snapshot | state bytes | selected lease root-field envelope bytes |
| --- | ---: | ---: |
| `init` | 2,868 | 155 |
| expired takeover by `set phase=reviewing` | 3,468 | 285 |

The observed legacy CLI takeover changed epoch 1 to 2, retired the old lease
with reason `mutating-command`, and produced one combined state update. This is
corpus provenance, not the U2 retirement-reason rule: U2 always uses
`lease-expired-takeover`. The longest string in both observed states was 68
UTF-8 bytes. These values are observations from one run; timestamps, paths, and
process values make exact state length environment-dependent.

The third column is not U2's nested `_lease_document`. It is the compact
canonical encoding of these six current CLI root fields in this exact order-
independent object:
`session_id`, `owner_session_id`, `lease_id`, `fencing_epoch`,
`lease_expires_at`, and `lease_history`. A missing `lease_history` is represented
as JSON `null`, matching `dict.get` observation rather than inventing an empty
persisted field. The measurement is reproducible from production CLI subprocess
output with:

```sh
PYTHONPATH=skills/mission/lib:skills/mission/tests ../../.venv-ci/bin/python - <<'PY'
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from mission_state_fixture_corpus import generate_cli_state_bytes, _run_cli_with_clock

keys = (
    "session_id", "owner_session_id", "lease_id", "fencing_epoch",
    "lease_expires_at", "lease_history",
)
with TemporaryDirectory() as temporary:
    root = Path(temporary)
    path, initial = generate_cli_state_bytes(root)
    initial_document = json.loads(initial.decode("utf-8"))
    completed = _run_cli_with_clock(
        root, "set", "phase=reviewing",
        lease_id="replacement-lease", now="2099-01-01T00:00:00Z",
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stderr)
    takeover = path.read_bytes()
    for label, content in (("init", initial), ("takeover", takeover)):
        document = json.loads(content.decode("utf-8"))
        envelope = {key: document.get(key) for key in keys}
        encoded = json.dumps(
            envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        print(label, len(content), len(encoded))
PY
```

U1's broader 25-snapshot run observed a maximum state of 14,671 bytes, at most
one captured blob per operation, and generation manifests of 238-502 bytes for
the selected representative cases. U2 reuses those recorded observations rather
than claiming the two lease cases cover every CLI state.

### 3.2 Worst-case schema measurements

The proposed records were encoded with K1's canonical JSON options
(`ensure_ascii=False`, `sort_keys=True`, compact separators, `allow_nan=False`).
Using a 128-character session/operation/command token, 64 audit event tokens,
and fixed SHA-256 references produced:

| record | encoded bytes |
| --- | ---: |
| maximum-shaped persisted audit record | 4,856 |
| maximum-shaped head | 601 |
| maximum-shaped operation record | 815 |
| commit with 64 maximum U1 effect descriptors, including 4,096 NUL characters per path | 1,613,675 |

The NUL case is intentional: U1's current relative-path validator permits it,
and JSON expands each NUL to six ASCII bytes. The 64-effect record therefore
uses the same conservative worst case as
`docs/design/502-u1-blob-limits.md`.

### 3.3 Fixed limits

| constant | exact value | derivation |
| --- | ---: | --- |
| `MAX_HEAD_BYTES` | 4,096 B | 601 B maximum-shaped candidate, rounded to the next existing binary page-sized boundary; over 6.8x measured shape |
| `MAX_AUDIT_BYTES` | 8,192 B | 4,856 B exact maximum-shaped persisted audit, rounded to the next binary boundary |
| `MAX_COMMIT_BYTES` | 4,194,304 B | K1/U1 `STATE_LIMIT`; 1,613,675 B conservative maximum-shaped candidate leaves 2,580,629 B |
| `MAX_PREPARE_BYTES` | 4,194,304 B | prepare duplicates the bounded commit references and adds no unbounded payload |
| `MAX_OPERATION_BYTES` | 4,096 B | 815 B maximum-shaped candidate, under the same small-record boundary as head |
| `MAX_AUDIT_EVENT_TYPES` | 64 | U1 maximum effect/binding cardinality; no second unbounded per-commit collection |
| identifier / audit token length | 1-128 ASCII characters | K1 session-ID maximum and U1 blob ID/kind maximum |
| SHA-256 digest | exactly `sha256:` plus 64 lowercase hex characters | K1/U1 digest contract |
| default lease TTL | 900 seconds | existing production lease constant |

The record byte limits are hard rejection boundaries: `len == limit` is
accepted if the schema is otherwise valid; `limit + 1` is rejected before the
record becomes authoritative.

## 4. Common scalar and encoding contract

All four record types use the following rules.

- Encoding: UTF-8, no byte-order mark, no trailing newline.
- JSON: `ensure_ascii=False`, keys recursively sorted in ascending Unicode
  code-point order, separators `,` and `:`, `allow_nan=False`.
- Object key order on disk is therefore the canonical sorted order. Array order
  is semantic and preserved.
- Duplicate keys, invalid UTF-8, non-finite numbers, trailing non-whitespace,
  non-object roots, and non-canonical bytes are rejected.
- Closed records require the exact key set shown below; there are no optional
  or extension keys unless explicitly stated.
- `Token128` is an ASCII string matching
  `[A-Za-z0-9][A-Za-z0-9._:-]{0,127}`.
- `SessionId` is a `Token128` that additionally excludes `:` so its exact
  filename is safe: `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`.
- `TransactionId` is exactly 32 lowercase hexadecimal characters.
- `Digest` is exactly `sha256:[0-9a-f]{64}`.
- `Timestamp` is exact UTC seconds precision: `YYYY-MM-DDTHH:MM:SSZ`.
- `Generation` and `Size` are exact Python integers (`bool` is rejected);
  generation is non-negative and size is non-negative. Target generation and
  committed/head generation are positive.
- Every path is an exact normalized relative POSIX path. It is compared to the
  digest-derived expected path; it is not accepted merely because it is
  traversal-free.

## 5. Exact on-disk layout

For repository root `.mission-state`:

```text
.mission-state/
  .state.lock
  sessions/
    <SessionId>.json
  transactions/
    .stage-<32 lowercase hex>/       # U1 private staging; temporary
    prepared/
      <TransactionId>.json           # durable prepare; removed after finalize
  objects/
    <64 lowercase hex>.blob          # U1 immutable content object
  generations/
    <64 lowercase hex>.json          # U1 immutable generation manifest
  commits/
    <64 lowercase hex>.json          # immutable canonical commit record
  operations/
    <sha256(canonical operation key) hex>.json
                                      # immutable session-local idempotency record
```

Directory modes are `0700`; new files are `0600`. All directories and files
must remain on the repository filesystem. Required directories must be real
directories, never symbolic links. Every authoritative file read requires a
regular, single-link, stable identity. Immutable files are published with
no-overwrite semantics; an existing name is reusable only when exact bytes,
size, and digest match.

The pinned repository-root descriptor is itself exclusively flocked first, so
renaming `.state.lock` cannot create a second CAS authority for the same root
inode. `.state.lock` is then opened relative to that root descriptor with
`O_NOFOLLOW`, and must be a regular single-link file whose opened and named
identities match before, during, and after its additional `flock`. It is never
chmodded before initial identity validation. Each authoritative child directory
is opened with `O_DIRECTORY | O_NOFOLLOW` relative to its pinned parent. Its
descriptor identity and the name visible from the parent descriptor are
compared before and after every authoritative operation. A root, lock, or
child-directory rename/symlink/hard-link race fails closed; publication through
a now-detached descriptor is never accepted as repository publication.

`sessions/<SessionId>.json` is the only mutable authority and is replaced from
a same-directory private temporary file. The file is fsynced before replace and
the pinned `sessions/` directory is fsynced after replace.

## 6. Exact record schemas

Examples below show field names and nesting. Canonical byte order is sorted-key
order, not the presentation order in these examples.

### 6.1 Reference records

`StateRef` exact keys:

```json
{"digest":"sha256:<64hex>","path":"objects/<64hex>.blob","size":123}
```

`GenerationRef` exact keys:

```json
{"digest":"sha256:<64hex>","path":"generations/<64hex>.json","size":456}
```

`CommitRef` exact keys:

```json
{"digest":"sha256:<64hex>","path":"commits/<64hex>.json","size":789}
```

`EffectRef` exact keys, in the same order as U1 manifest `blobs[]`:

```json
{
  "blob_id":"review-evidence",
  "digest":"sha256:<64hex>",
  "kind":"review-input",
  "object":"objects/<64hex>.blob",
  "relative_path":"archive/review.json",
  "size":123
}
```

The state/effect records must equal the corresponding U1 generation-manifest
records after converting the manifest-local `objects/...` reference to the same
repository-relative public object path. No independent caller-supplied reference
is trusted.

### 6.2 `mission-head/1`

Exact required keys:

```json
{
  "commit":{"digest":"sha256:<64hex>","path":"commits/<64hex>.json","size":789},
  "generation":2,
  "schema":"mission-head/1",
  "session_id":"test",
  "state_generation":{"digest":"sha256:<64hex>","path":"generations/<64hex>.json","size":456}
}
```

Invariants:

- `generation >= 1`;
- `session_id` equals the filename stem and requested session;
- commit and generation paths are derived exactly from their digests;
- referenced record sizes equal strict-reader results;
- referenced commit's target generation, session, and generation digest equal
  this head;
- encoded length is at most `MAX_HEAD_BYTES`.

### 6.3 `mission-commit/1`

Exact required keys:

```json
{
  "audit":{"command_type_digest":"sha256:<64hex>","event_type_digests":["sha256:<64hex>"]},
  "base":{"generation":1,"head_digest":"sha256:<64hex>"},
  "committed_at":"2026-08-15T00:00:00Z",
  "effects":[],
  "fencing_epoch":2,
  "generation":{"digest":"sha256:<64hex>","path":"generations/<64hex>.json","size":456},
  "intent_digest":"sha256:<64hex>",
  "operation_id":"operation-2",
  "schema":"mission-commit/1",
  "session_id":"test",
  "state":{"digest":"sha256:<64hex>","path":"objects/<64hex>.blob","size":3468},
  "target_generation":2,
  "transaction_id":"0123456789abcdef0123456789abcdef"
}
```

For the first commit only, base is exactly:

```json
{"generation":0,"head_digest":null}
```

For every later commit, `head_digest` is a `Digest`. Invariants:

- `target_generation == base.generation + 1`;
- `fencing_epoch >= 1` and equals the committed state lease epoch;
- `generation` refers to the exact U1 manifest used by this commit;
- `state` and `effects` equal that manifest;
- `effects` count is at most 64 and blob IDs are unique;
- the sum of all `effects[].size` values is at most
  `MAX_TOTAL_BLOB_BYTES = 16 MiB`; the prepare/commit parser, manifest reader,
  and complete authoritative reader each enforce this independently;
- `operation_id`, `session_id`, and `transaction_id` satisfy their scalar types;
- the record contains no command payload, lease ID/token, lease owner, provider
  response, environment value, or free-form audit text;
- encoded length is at most `MAX_COMMIT_BYTES`.

### 6.4 Bounded audit metadata and persisted record

In-memory `AuditMetadata` has exact fields `command_type: Token128` and
`event_types: tuple[Token128, ...]`. The caller supplies categories, not a
message or arbitrary map. U2 validates immutability, uniqueness, and the maximum
count, then hashes each UTF-8 token independently with SHA-256 before any
prepare/commit encoding.

The persisted audit object has these exact required keys:

```json
{"command_type_digest":"sha256:<64hex>","event_type_digests":["sha256:<64hex>"]}
```

- `command_type_digest` is SHA-256 of the exact UTF-8 command-category token.
- `event_type_digests` is an array of 0-64 SHA-256 digests in input order.
  Duplicate input categories/digests are rejected because they add no audit
  information and permit accidental unbounded repetition.
- encoded canonical audit bytes are at most `MAX_AUDIT_BYTES`.
- There is no raw category, message, detail, arbitrary map, command argument,
  provider output, lease token, or extension field. Even a syntactically valid
  lease/provider value supplied as a category is persisted only as its digest.
- Lease retirement history is not an audit channel. Expired self-recovery or
  takeover appends the fixed closed reason `lease-expired-takeover`; it never
  copies `AuditMetadata.command_type`, an event category, a command value, or a
  presented/generated lease token into `LeaseHistoryEntry.reason`.

### 6.5 `mission-prepare/1`

The prepare record has the exact same `audit`, `base`, `effects`,
`fencing_epoch`, `generation`, `intent_digest`, `operation_id`, `session_id`,
`state`, `target_generation`, and `transaction_id` values as the future commit,
plus:

```json
{
  "prepared_at":"2026-08-15T00:00:00Z",
  "projections":[],
  "schema":"mission-prepare/1"
}
```

It does not contain `committed_at`. U2 has no compatibility projection, so
`projections` must be the exact empty array; non-empty values are rejected
rather than pretending U3 recovery exists. The record is bound to the staged
manifest before publication and is at most `MAX_PREPARE_BYTES`. Its parser also
enforces at most 64 unique effects and an aggregate effect size of at most
16 MiB. U2 does not use a shallow parse of this record to authorize another
write. The current `begin()` delegates a retained entry to U3, which validates
the complete transaction before rollback or finalization; admission cannot
continue while that recovery is ambiguous or blocked.

### 6.6 `mission-operation/1`

Exact required keys:

```json
{
  "commit_digest":"sha256:<64hex>",
  "intent_digest":"sha256:<64hex>",
  "operation_id":"operation-2",
  "result":{
    "commit_digest":"sha256:<64hex>",
    "generation":2,
    "head_digest":"sha256:<64hex>",
    "state_generation_digest":"sha256:<64hex>"
  },
  "schema":"mission-operation/1",
  "session_id":"test"
}
```

Operation identity is session-local. The flat filename is the lowercase hex
SHA-256, without a prefix, of these canonical bytes:

```json
{"operation_id":"operation-2","schema":"mission-operation-key/1","session_id":"test"}
```

The exact `session_id` and `operation_id` from the request form that closed
`mission-operation-key/1` object. This preserves a flat bounded directory while
preventing the same caller-stable operation ID in two sessions from aliasing.
`commit_digest` must equal `result.commit_digest`; all result fields are
validated against the committed lineage before this immutable record is
published. Replay validates the tombstone itself and does not dereference the
commit or state generation. The record is at most `MAX_OPERATION_BYTES` and
does not root a commit or state generation for U4 purposes.

## 7. In-memory request and protocol contract

### 7.1 `ExecutionRequest`

The frozen request has these exact fields:

| field | type | rule |
| --- | --- | --- |
| `session_id` | `SessionId` | explicit, never ambient |
| `lease_owner_session_id` | `SessionId` | actor/lease owner used by #475/#498 admission; explicit and may differ from the repository session |
| `command` | K1 `FrozenJsonObject` | canonical immutable normalized command; never persisted by U2 |
| `blobs` | U1 `VerifiedBlobSet` | immutable captured bytes |
| `operation_id` | `Token128` | caller-stable within `session_id`; no automatic retry ID generation |
| `intent_digest` | `Digest` | must equal SHA-256 of the canonical `mission-intent/1` envelope below |
| `presented_lease_id` | `Token128 \| None` | explicit token; `None` permits genesis acquisition or expired self-recovery/takeover, but never a live-lease mutation |
| `audit` | `AuditMetadata` | exact bounded schema above |

The normalized intent is the exact closed object:

```json
{
  "blobs":[
    {
      "blob_id":"review-evidence",
      "digest":"sha256:<64hex>",
      "kind":"review-input",
      "relative_path":"archive/review.json",
      "size":123
    }
  ],
  "command":{"name":"review-import"},
  "lease_owner_session_id":"worker-1",
  "operation_id":"operation-2",
  "schema":"mission-intent/1",
  "session_id":"test"
}
```

`command` is the thawed value of the exact K1 canonical command and is encoded
again only as part of the complete canonical envelope. Each blob entry is
derived from `VerifiedBlob.binding`; caller-supplied duplicate IDs or mismatched
captured bytes have already failed U1 validation. Blob entries are sorted by
ascending `blob_id` before envelope encoding; blob IDs are unique, so this is a
total canonical order and semantically identical immutable sets have one digest
independent of construction order. Changing blob bytes changes `digest` and
therefore the intent digest. `operation_id` is included even though it also
selects the session-local lookup key: it is part of the caller's exact stable
request identity, and the normalized envelope excludes only `audit` and
`presented_lease_id`. Changing any included field while reusing an existing
session-local operation key is an `operation-intent-collision`; changing audit
or the presented fence alone does not create a new domain intent.

The repository does not read session, command, operation identity, intent,
blobs, token, or clock from environment variables.

### 7.2 `begin(request)`

Under the state lock:

1. inspect `transactions/prepared/`; if it contains an entry, call U3
   `_recover_unlocked(request.session_id)` before admission, otherwise check for
   an operation replay and recover any verifiable orphan private stages;
2. after recovery, strictly lookup the operation record again by the canonical
   session-local `mission-operation-key/1` filename;
3. return its exact `CommitResult` when operation ID and intent both match, or
   reject operation-ID reuse when intent differs;
4. check the resolved-operation index so a rolled-back intent may retry, while
   an intent collision or finalized transaction missing its operation record
   fails closed;
5. strictly read the current head/commit/generation/state, or model exact genesis
   as generation 0 with absent head digest;
6. calculate one pending acquire/renew/takeover decision using the injected UTC
   clock and the 900-second TTL;
7. return `AdmittedSnapshot` without writing head, generation, lease, evidence,
   prepare, commit, or operation files.

Admission actions:

- Genesis or `LegacyAbsentLease`: acquire epoch 1. The presented token is used;
  if absent, a new 32-lowercase-hex token is generated and returned only in the
  admitted in-memory value.
- Same `lease_owner_session_id` and exact current token: preserve the #498 contract and
  renew without changing epoch even after nominal base expiry; target expiry is
  `max(base expiry, admitted_at + 900 seconds)`.
- While the base lease is live, an exact owner with no token is
  `lease-token-required`; every foreign owner or wrong-token case (including a
  foreign owner with no token) is `lease-rejected`.
- For an expired lease, reject a presented current or retired token unless it is
  the exact same-owner current-token renewal above. Otherwise, including
  `presented_lease_id=None`, generate/use the new token, append exactly one
  retirement history entry with fixed reason `lease-expired-takeover`, increment
  epoch once, and set target expiry to `admitted_at + 900 seconds`. A generated
  token is returned only in the admitted in-memory value.

### 7.3 Private `_stage_persistence(admitted, state_bytes, effects)`

This bytes-oriented function is an implementation-only persistence seam, not
the ADR-005 `stage(admitted, transition, blobs)` method. U2 can validate state,
lease, blob, and storage relationships, but without K2 it cannot validate the
semantic relationship between `ExecutionRequest.command` and the proposed
state/effects. Tests may invoke the seam, but production application code must
not route to it or treat it as the public UnitOfWork boundary. After K2 provides
typed `Transition`, P1 must replace this callable boundary with the ADR-005
`stage(admitted, transition, blobs)` boundary, which derives `state_bytes` and
effect descriptors from the sealed kernel-issued transition result.

Before U1 staging:

- `state_bytes` must decode through K1;
- state identity/session must equal the request session when present;
- target state's complete `FencedLease` must equal the pending target lease;
- `effects` must be the exact immutable bindings in `request.blobs`, including
  the aggregate 16 MiB bound;
- no head/generation/public record is changed.

U1 `stage_generation` then creates the private stage. The issuing
`LocalFencedRepository` computes the canonical `mission-prepared-binding/1`
digest and stores it in its instance-private `_stage_binding_registry`, keyed by
the issued private stage transaction identity. `PreparedCommit.binding_digest`
carries the same digest for comparison, but that caller-visible unkeyed digest
is not authority by itself. The binding covers:

- the exact canonical request binding: session and lease-owner IDs, command
  bytes, canonical `mission-intent/1` bytes and digest, operation ID, presented
  lease-token digest, persisted audit digests, and the complete verified blob
  bindings plus captured-byte digests and sizes;
- the exact admission binding: admitted base snapshot identity, pending lease
  decision and digest, `admitted_at`, target generation, and the complete
  `CommitPrecondition`;
- the exact U1 stage binding: normalized stage-root identity, transaction ID,
  manifest bytes/digest, state bytes/digest/size, ordered effect bindings, and
  the staged files' validated identities.

The transaction ID must be exactly the suffix of the stage root named
`.stage-<TransactionId>`; neither an arbitrary root with a matching manifest nor
an unrelated transaction ID is accepted.

Only the same repository object that issued the prepared value can commit it.
An unknown capability, a value issued by another `LocalFencedRepository`
instance for the same root, a registry/carried mismatch, or a forged
`PreparedCommit` whose attacker recomputed `binding_digest` is rejected. The
registry entry is invalidated when a pre-prepare rejection or replay discards
the stage; cleanup failure still invalidates the capability before surfacing
`stage-cleanup-failed`. After all preconditions pass, commit atomically consumes
the one-shot registry authority before durable prepare publication. A later
post-prepare fault is governed by durable U3 recovery and is never promoted or
retried through this process-local registry.

### 7.4 `commit(prepared, precondition)` fenced CAS

Under the same repository lock, before durable prepare publication:

1. find the stage capability in this exact repository instance's private
   registry, reconstruct the request, admission, and stage canonical binding,
   and require `registry digest == PreparedCommit.binding_digest == current
   recomputation`; this covers command, intent, operation ID, audit, blobs,
   state, effects, manifest, transaction, and every base/target field even if
   `PreparedCommit`, `AdmittedSnapshot`, or `ExecutionRequest` was replaced
   after stage. Unknown, foreign-instance, invalidated, or digest-only forged
   values fail before any operation replay can be returned;
2. require the supplied precondition, `PreparedCommit.precondition`, and
   `PreparedCommit.admitted.precondition` to be mutually equal, and require all
   three to derive from the bound base generation/head digest and pending lease
   decision rather than accepting agreement among self-inconsistent copies;
3. require `transaction_id` to equal the exact `.stage-<TransactionId>` suffix,
   require the U1 manifest/state/effects to equal the stage binding and request
   blobs, and re-enforce effect count, uniqueness, and 16 MiB aggregate size;
4. the current head absence/presence, generation, and exact-byte SHA-256 must
   equal the base precondition;
5. target generation must equal both the bound target and current generation
   plus one;
6. current state lease must equal the admitted and stage-bound base lease;
7. recomputing admission against the unchanged base at the original
   `admitted_at` must yield the identical pending decision; when admission
   generated a token, recomputation reuses the token already bound into the
   pending-decision digest and must not generate a second token;
8. sample the injected clock exactly once for commit start; use that same sample
   for the base-live and target-expiry checks and for both `prepared_at` and
   `committed_at`;
9. when renewal began while the base lease was live, that base lease must still
   be unexpired at the commit-start sample; an exact-token renewal admitted
   after nominal expiry follows #498 and does not retroactively require a live
   base;
10. for every action, including an expired-base exact-token renewal, the pending
    target lease must still be unexpired at the commit-start sample;
11. target state and the complete U1 stage are revalidated;
12. operation lookup by the session-local key must still be absent or the exact
    same completed result.

Any failure through step 12 invalidates and discards a capability known to this
repository instance, and publishes nothing. An unknown or foreign-instance
value is rejected without granting cleanup authority over its stage. A replay
also invalidates its known capability and discards the stage before returning
the recorded result. If discard fails, `stage-cleanup-failed` is surfaced and
the method must not return the original rejection or an idempotent replay
result. Only after all pass does commit:

1. consume/invalidate the exact one-shot entry in `_stage_binding_registry`;
2. atomically publish/fsync `mission-prepare/1`;
3. call U1 immutable generation publication;
4. publish/fsync the content-addressed immutable commit record;
5. construct the canonical head and write/fsync its same-directory private
   temporary file;
6. invoke the `before-head-replace` fault callback;
7. after the callback returns, freshly re-read and require the exact base head
   CAS, then freshly sample the clock and require the pending target lease still
   to be valid;
8. without another callback or blocking operation, call `os.replace` and fsync
   the head directory (logical commit point);
9. invoke the `after-head-replace` fault callback;
10. publish/fsync the immutable operation record;
11. verify head -> commit -> generation -> state lineage and result;
12. remove/fsync the exact prepare record.

A fault, CAS change, or expiry before step 8 leaves the base head authoritative.
If it occurs after step 2, the durable prepare and any already-published
generation/commit remain immutable recovery roots or orphans; they are not
rolled back or reported as success. A fault after step 8 leaves the target head
authoritative. The U2 commit path does not itself repair or classify retained
prepare records. In the current U3-integrated repository, `begin()` checks
`transactions/prepared/` before admission and calls `_recover_unlocked()` when
an entry exists. A single verifiable prepare is rolled back or finalized after
full transaction, stage, generation, state, effects, projection, and lineage
validation; admission then continues. U3-specific inability to inspect, order,
or classify recovery state fails closed with `recovery-ambiguous`; lower-level
strict readers, validators, and exact record removal retain their own stable
code families, including `record-invalid`, `record-missing`,
`record-write-failed`, `lineage-mismatch`, `repository-changed`, and
`projection-invalid`. Selected verified private-stage or projection cleanup and
restoration failures use `recovery-blocked`.

## 8. Exact rejection ownership

| rejection | layer | stable code family |
| --- | --- | --- |
| mutable/wrong request type, token/path/digest/timestamp/type bounds | request/record validator | `request-invalid`, `record-invalid`, `record-too-large` |
| canonical `mission-intent/1` envelope disagrees with intent digest | request validator | `intent-digest-mismatch` |
| duplicate audit event or audit count/bytes overflow | audit validator | `audit-metadata-invalid`, `record-too-large` |
| missing token for live same-session lease | admission | `lease-token-required` |
| live foreign/mismatched lease | admission | `lease-rejected` |
| current/retired token on expired takeover | admission | `stale-fencing-token` |
| partial/malformed lease | K1 decoder | existing K1 decode code |
| domain guard rejection | pure kernel/application caller | no repository write; U2 does not invent domain guards |
| target state lease differs from pending lease | U2 private persistence seam | `pending-lease-mismatch` |
| missing/extra/mutated blobs, path escape, stage race | U1 | existing `LocalUnitOfWorkError` code |
| request/admission/stage/transaction binding changed, capability unknown/foreign/invalidated, or caller recomputed a forged carried digest | instance-private commit capability registry and binding validator | `precondition-mismatch` or `stage-invalid` |
| effect count, uniqueness, or aggregate 16 MiB bound fails in a prepare, commit, manifest, or authoritative read | U1/U2 record and lineage validator | `blob-set-too-large`, `record-invalid`, or existing U1 code |
| base absence/presence, generation, or head digest differs before the prepare record and generation are published (the private stage already exists at this point) | commit precondition CAS | `head-cas-mismatch` (retryable: nothing durable or public has been written yet) |
| base absence/presence, generation, or head digest differs after the prepare record and generation are published, immediately before the head is replaced | commit final authority CAS | `final-authority-cas-mismatch` (not retryable: replaying would publish the generation twice) |
| precondition or pending-decision digest differs | commit CAS | `precondition-mismatch` |
| lease expires or decision changes after stage | commit CAS | `lease-precondition-changed` |
| operation ID reused with different intent | idempotency index | `operation-intent-collision` |
| private stage cannot be removed after a pre-prepare rejection or replay | stage cleanup | `stage-cleanup-failed`; never return rejection/replay success |
| immutable same-name/different-byte collision | U1/U2 immutable publisher | `immutable-*-collision` |
| duplicate key, UTF-8, finite number, trailing data, canonical mismatch | K1 JSON codec / U2 record codec | existing K1 code or `record-not-canonical` |
| symlink/FIFO/hard link/identity/size swap | K1/U1 readers plus U2 descriptor-pinned lock/reader/publisher | existing strict-read/U1 code or `repository-invalid`, `repository-changed`, `record-invalid` |
| head/commit/generation/state digest, size, path, or generation mismatch | authoritative U2 reader | `lineage-mismatch` |
| prepared entry found before admission | `begin()` -> U3 `_recover_unlocked()` | recover and continue when the durable transaction is fully verifiable; U3-specific ordering or classification ambiguity is `recovery-ambiguous`, while lower-level validators retain their codes |
| prepared entry appears after admission but before commit publication | U2 commit-start `_reject_open_prepare()` | `recovery-ambiguous`; the commit must not race an unresolved durable transaction |
| selected verified private-stage or projection cleanup/restoration failure | U3 recovery cleanup | `recovery-blocked`; lower-level exact read/remove/write failures retain codes such as `lineage-mismatch` or `record-write-failed` |

Lease rejection occurs in `begin`, before staging or any projection/publication.
Stale CAS and expiry at the commit-start sample occur before prepare
publication. At the authority boundary, the head temporary is already written
and fsynced before the `before-head-replace` callback. Only after that callback
does U2 perform the mandatory fresh CAS and clock/lease check immediately before
`os.replace`. A head change or lease expiry during temporary write, fsync, hook,
or hook-induced delay therefore fails after prepare/immutable publication but
before head authority changes; head and operation remain unchanged and a later
write must complete U3 recovery before admission can continue.
Strict authoritative read rejects the complete snapshot; it never falls back to
a filename, empty state, or legacy interpretation of the head record.

## 9. TDD list and acceptance mapping

The implementation tests start with actual CLI-produced state bytes and cover:

1. genesis commit/read and exact head -> commit -> generation -> state lineage;
2. two prepared `N -> N + 1` commits, one winner and one CAS reject;
3. same session without exact token, live foreign token/no-token, expired
   no-token self-recovery/takeover, expired exact-token renewal, retired token,
   and one-time fence increment;
4. preliminary domain rejection with byte-identical public state, plus a
   contract/API test that the bytes-oriented method is private, package
   `__all__` and attributes do not expose it or its repository types, and no
   production module under `skills/mission/bin/**` or `scripts/**` imports the
   seam. Semantic command-to-state binding requires K2's typed `Transition` and
   is deferred to P1; U2 pins production unreachability instead (High 1 gate);
5. mutation after private stage, one field family at a time, of command, intent,
   operation ID, audit, request blobs, admitted/prepared preconditions, base,
   target generation, transaction ID/root, state bytes, effects, and manifest;
   each forged `PreparedCommit` is rejected before prepare publication. Separate
   cases recompute the forged carried digest, submit the original value through
   another repository instance for the same root, and retry an invalidated
   capability, proving that a caller-held digest is not stage authority (High 2);
6. same session-local operation ID and command with different captured evidence
   bytes, digest, kind, path, or size produces an intent collision rather than
   replay; changing `operation_id` also changes the canonical intent digest,
   while audit and presented lease token do not (High 3);
7. expired takeover from an actual CLI-produced leased state creates history and
   persists only fixed `lease-expired-takeover`; caller audit/lease/provider-like
   tokens are absent in plaintext from state history, prepare, commit, head, and
   operation bytes (High 4);
8. deterministic clock sequences that cross target expiry between validation
   and timestamp creation, during head temporary write/fsync, and inside the
   `before-head-replace` hook. The hook is observed only after the temporary is
   durable; a hook-induced competing head change or expiry is caught by the
   post-hook fresh CAS/clock checks, leaving head/operation unchanged and
   retaining a prepare that must be recovered before the next admission
   (High 5);
9. a single fully verifiable prepare is recovered before admission, while a
   prepare for another session, malformed JSON, shallow-valid data with broken
   lineage, an unexpected filename, multiple prepares, or unrelated transaction
   residue fails closed as `recovery-ambiguous`; a known disposition whose
   verified cleanup cannot complete fails as `recovery-blocked` (High 6);
10. strict malformed head/commit/state attacks and immutable collisions;
11. same operation/intent result replay and different-intent collision, plus the
    same operation ID used independently by two sessions through distinct
    canonical `mission-operation-key/1` filenames (Medium 3);
12. effects-present end-to-end commit/read/replay validates the exact published
    blob bytes, manifest, commit, and lineage; constructed prepare, commit, and
    manifest records whose individually valid effects exceed 16 MiB in aggregate
    are rejected by every authoritative reader (Medium 1 and Medium 4);
13. forced private-stage discard failure on every pre-prepare rejection and
    replay path surfaces `stage-cleanup-failed` and never returns the original
    rejection or replay success (Medium 2);
14. injected short writes and failures of file fsync, immutable link, temporary
    unlink, head replace, and the directory fsync after head replace, in addition
    to faults immediately before and after head replacement; assertions
    distinguish the authoritative base/target head, operation publication, and
    retained prepare that must be recovered before admission. Medium 4
    identified missing test coverage rather than an implementation defect, so
    this test also passes the reviewed pre-fix implementation;
15. commit/audit schema absence of lease token, command body, raw provider
    fields, and free-form lease retirement reasons;
16. production `init` still emits v4 and no CLI imports the U2 repository;
17. lock symlink/hard-link/name-swap rejection, stable root-inode exclusion, and
    repository/session/commit directory replacement races against pinned
    descriptors;
18. recursive plugin mirror and Python 3.9 gates.

Regressions for implementation defects (High 2-6 and Medium 1-3) must fail
against the reviewed pre-fix implementation for the specific unsafe behavior,
not merely assert that a nearby happy path succeeds or that `begin()` itself is
read-only. High 1 instead defers semantic binding to P1 and pins the already
isolated seam's production unreachability. Medium 4 closes a test-coverage gap,
not an implementation defect; both gates are therefore expected to pass the
reviewed pre-fix implementation.

## 10. Original Issue #503 non-scope and remaining boundaries

- Issue #503 itself did not implement prepare-record recovery or a
  roll-forward/rollback state machine; later U3 work added both and the current
  behavior is described in Sections 7-9.
- No generation, commit, operation, or prepare garbage collection (U4).
- The original Issue #503 implementation wrote no compatibility projection and
  required prepare `projections` to be empty; later projection and U3 recovery
  work superseded that historical restriction.
- No production command selects or creates this repository.
- The U2 bytes-oriented stage is a private persistence seam, not the ADR-005
  Section 5 UnitOfWork. Production routing to it remains prohibited until K2
  supplies typed `Transition` and P1 replaces the seam with
  `stage(admitted, transition, blobs)`.
- No change to existing U1 API signatures or behavior. U2 adds three public
  validation/lifecycle wrappers owned by U1:
  `validate_staged_generation`, `validate_verified_blob_set`, and
  `discard_staged_generation`; they reuse U1's existing deep validation and
  private-stage removal rules rather than duplicating those contracts in U2.
- No K2 transition/command vocabulary, pass-gate change, provider execution,
  external send, or v4-to-v5 physical migration. Consequently U2 exposes only
  the private bytes-oriented persistence seam; P1 owns its replacement with the
  ADR-005 boundary over a sealed K2 transition result.

## 11. Design completeness and corrected deficiencies

The corrected U2 contract preserves ADR-005, migration-plan U2/Section 9, K1,
U1, and the #475/#498 lease-first contract. Independent review found that the
earlier U2 draft incorrectly claimed stronger semantics than its K2-independent
implementation could provide and left several persistence relationships open.
This document corrects the contract itself:

1. the first draft incorrectly treated every nominally expired current token as
   stale and permitted `None` only at genesis. #498 actually requires
   same-owner exact-current-token renewal after nominal expiry and expired
   no-token recovery with a fresh token;
2. the bytes-oriented U2 stage is explicitly private. The ADR public typed
   transition adapter is deferred to P1 because only K2 can seal the semantic
   command-to-transition result;
3. `PreparedCommit` carries a canonical request/admission/stage digest, while
   the issuing repository instance separately retains the authoritative private
   capability. Commit requires registry, carried, and recomputed digests to
   match, closing post-stage replacement, cross-instance use, and recomputed
   unkeyed-digest forgery;
4. normalized intent is the versioned `mission-intent/1` envelope, including
   operation ID, command, and canonical blob bindings. Only audit and the
   presented lease token are excluded, so changed evidence cannot replay an old
   result;
5. expired takeover history uses fixed `lease-expired-takeover`, preventing
   caller-controlled audit text from crossing into immutable state;
6. one commit-start sample drives initial expiry and both record timestamps. The
   head temporary is then written/fsynced before the `before-head-replace` hook;
   fresh post-hook CAS and clock/lease checks immediately before `os.replace`
   prevent hook delay, temporary-file I/O, or a competing writer from crossing
   the authority boundary;
7. every retained prepare entry blocks the U2 commit-start gate, while current
   admission invokes U3 and proceeds only after complete recovery transaction
   validation rather than trusting a shallow parser;
8. U1's 16 MiB effect aggregate is rechecked at every prepare, commit, manifest,
   and authoritative-reader boundary;
9. failed pre-prepare stage cleanup is a visible `stage-cleanup-failed`, never a
   swallowed secondary error or false replay success;
10. operation IDs are explicitly session-local and the flat filename hashes a
    canonical key containing both session and operation IDs;
11. the acceptance suite now requires effects-present end-to-end coverage and
    fault injection for short write, fsync, link, unlink, replace, and
    post-replace directory-fsync boundaries.

The migration plan intentionally did not specify the exact schemas, limits,
layout, CAS comparison, lease timing rule, or rejection ownership; Sections
3-8 fix those delegated choices. Later U3 work is reflected here only where it
changes admission and error-code behavior; U4 retention values remain outside
Issue #503. Production v5 routing also remains disabled, and no existing U1 API
signature or behavior is changed.
