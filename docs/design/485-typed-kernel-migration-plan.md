# Issue #485: Typed Kernel / UnitOfWork migration plan

## 1. Purpose and evidence boundary

This document turns [ADR-005](../adr/005-typed-mission-kernel-and-unit-of-work.md)
into an interruption-safe strangler plan and implementation-Issue backlog. It
does not authorize production-code changes.

Facts in section 2 were verified at HEAD `f0ac6aea`. Section 3 onward is
proposed design. The parent Issue body could not be fetched during this
design session because the GitHub API was unavailable; the parent goals used
here are those stated in `docs/design/485-typed-kernel-design.md`, the completed
#475-#484 design records, and the task instructions.

## 2. Verified current state

### 2.1 Contracts established before Wave 3

| Source | Verified conclusion |
|---|---|
| #475, `cmd_artifact_init/render/export/publish` | public artifact bytes are now written through `_PublishedFilesTransaction` only after explicit lease validation; exceptions roll back files before the state write returns |
| #476, `cmd_freshness` and `mission_common.state_age_details` | Python is the authority for freshness; the Stop guard consumes a verdict rather than independently deriving age |
| #477, `_derive_next_action` | `completed_evidence` is a successful `report-terminal`, distinct from pass and blocker |
| #478, specialist invocation code/tests | specialist evidence is bound to invocation identity and digest; providers remain evidence providers |
| #479 | `MISSION_CLI_VERSION` is `2.5.0` and is consistency-tested with distribution manifests |
| #480 | FIFO test failures gained diagnosis without increasing timeout, retrying, or skipping |
| #481/#482 and ADR-003 | review-tier lexical boundaries are implemented; tier changes never change the pass gate |
| #483, `_validate_schema_version` | missing schema key is the only legacy fallback; integers 1-4 are accepted; future, bool, string, float, and null versions fail closed without write |
| #484, ADR-002/state reference | schema v4 and ADR implementation status are synchronized; Finding statuses beyond `open`/`resolved` are not implemented |

The #475 ordering is not yet universal evidence behavior. At this HEAD,
`review-import` and `push-score` enforce a lease before their publication, but
`manual-score-capture` and `specialists plan-import` can publish immutable
evidence without an explicit preceding lease check. The target invariant below
extends lease/fence/CAS-before-publication to every authoritative publication;
the gap is not reported as an existing guarantee.

### 2.2 Current code boundaries

Line references are anchors for the inspected HEAD and should be refreshed when
implementation Issues are opened.

| Concern | Current location | Observation |
|---|---|---|
| schema reader | `mission-state.py:241-270` | strict version guard and JSON loader are in the CLI module |
| fenced lease | `mission-state.py:560-908`, `1209-1288` | lease decision mutates dict state; final enforcement occurs immediately before atomic state publication |
| file lock and atomic state write | `mission-state.py:1125-1312` | `StateLock`, fsync, replace, lease stamp, and metadata effects are coupled |
| phase and terminal outcome | `mission-state.py:1786-1860` plus `mission_common.py` | closed phase values exist as strings; terminal outcome is a shared pure derivation |
| initial aggregate | `mission-state.py:6953-7075` | `cmd_init` constructs the aggregate as a large untyped dict |
| lifecycle mutation | `mission-state.py:8239-8405`, `9325-9534`, `13633-13881`, `13964-14206` | `advance`, generic `set`, pass, halt, and reactivation each contain their own guards and mutations |
| next guidance | `mission-state.py:8417-8450`, `8573-8852` | happy-path strings and the phase decision tree are handwritten separately from mutating command guards |
| artifact publication | `mission-state.py:6472-6715` | transaction rollback is in-process; no durable crash journal or generation CAS exists |
| strict publication primitive | `mission-state.py:11064-11746` | no-follow identity checks, rollback bytes, fsync, and recovery residues are reusable foundations |
| review import/score/pass | `mission-state.py:12099-12169`, `12825-13881` | untrusted evidence validation, publication, score mutation, and pass authority are intertwined in CLI handlers |
| plan/provider/handoff | `mission-state.py:12172-12543` and provider helpers | immutable plan evidence and provider isolation exist, but use-case and adapter logic are mixed |
| immutable generation example | `mission-state.py:6186-6248` and `worktree_archive.py` | worktree archive already publishes an immutable content-digest generation then advances a pointer |

The current suite has direct regression coverage for fenced lease races
(`test_issue354_session_lease.py`), artifact rollback (`test_artifact_cli.py`),
score publication rollback (`test_push_score.py`), schema compatibility
(`test_issue483_schema_compat_matrix.py`), next guidance
(`test_adr002_next_command.py`), phase transitions (`test_issue237_advance.py`),
planning lifecycle (`test_planning_provider_lifecycle.py`), plan publication
(`test_plan_import.py`), and provider isolation (`test_provider_preflight.py`).

### 2.3 Command-surface ownership

Every parser command must have an explicit owner before v5 cutover.

| Owner | Complete command families |
|---|---|
| Mission kernel through use cases | `init`; `set` for allowlisted domain properties; `advance`; `activity start/end`; `progress update/clear`; `artifact init/append/render/export/publish`; state-recording `specialists recommend/log-invocation/plan-import/reconcile-invocation`; `planning adopt-core/promote-provider-plan/reselect`; `executor-handoff begin/verify-step/record-step/complete`; `review-import`; `aggregate-reviews`; `review-finalize`; `push-score`; `manual-score-capture`; `closeout`; `context-manifest`; `mark-passes`; `mark-halt`; `reactivate`; `refresh-pid`; state-writing `permission-preflight` and `stop-guard-observe` |
| Mission queries | `get`; `next`; `freshness`; `codex-preflight`; `progress get`; `specialists accounting/summary`; `stats`; `learning brief`; `list`; `lane-report` |
| External execution saga adapters | `specialists consent/prepare-invocation/verify-approval/invoke-command/invoke-prepared`; provider process dispatch is outside local UnitOfWork, while each pre/post state transition uses it |
| Separate local aggregates | `parallel-init/status/closeout`; `pregate record/check/digest`; `queue enqueue/status/next/verify/mark`; `handoff publish/await/verify`; command-outcome sidecars |
| Administrative coordinators | `resume`; `cleanup-empty`; `cleanup-stale`; `halt --all`; `supersede-reviews`; `update-project-root`; `archive-worktree`; `resolve-archive` |

Administrative coordinators call typed per-aggregate operations. They do not get
a broad write escape hatch. `set` is progressively narrowed; fields that affect
authority become dedicated commands rather than generic dict assignments.

The Stage 0 ownership artifact starts with this exact child-Issue routing (CLI
options do not create a second owner):

```yaml
mission_routes:
  A1: [init, set, advance, activity.start, activity.end, mark-halt,
       reactivate, refresh-pid, resume, update-project-root, cleanup-stale,
       halt]
  A2: [review-import, aggregate-reviews, review-finalize, push-score,
       manual-score-capture, closeout, mark-passes, supersede-reviews]
  A3: [progress.update, progress.clear, artifact.init, artifact.append,
       artifact.render, artifact.export, artifact.publish, context-manifest]
  A4: [specialists.recommend.record-state, specialists.log-invocation,
       specialists.plan-import, specialists.reconcile-invocation,
       planning.adopt-core, planning.promote-provider-plan, planning.reselect,
       executor-handoff.begin, executor-handoff.verify-step,
       executor-handoff.record-step, executor-handoff.complete]
  A5: [permission-preflight, stop-guard-observe]
query_routes:
  R1: [get, next, freshness, codex-preflight, progress.get,
       specialists.recommend.dry-run, specialists.accounting,
       specialists.summary, stats, learning.brief, list, lane-report]
external_saga_routes:
  A4: [specialists.consent, specialists.prepare-invocation,
       specialists.verify-approval, specialists.invoke-command,
       specialists.invoke-prepared]
separate_aggregate_routes:
  C1: [parallel-init, parallel-status, parallel-closeout, pregate.record,
       pregate.check, pregate.digest, queue.enqueue, queue.status, queue.next,
       queue.verify, queue.mark, handoff.publish, handoff.await, handoff.verify]
administrative_routes:
  C1: [cleanup-empty, archive-worktree, resolve-archive]
```

`halt`, `resume`, and `cleanup-stale` are coordinators, but their per-session
Mission mutation belongs to A1. `archive-worktree` reads through R1 before its
separate archive publication. A Stage 0 test compares this inventory with
`_build_parser` and rejects omissions, duplicates, or an option-dependent
second writer.

## 3. Target boundaries

### 3.1 Proposed package shape

```text
skills/mission/lib/mission_kernel/
  model.py          closed unions and aggregate invariants
  commands.py       closed command union
  transitions.py    one declarative transition table and decide()
  guidance.py       derive_next() compiled from transition rules
  codec_v4.py       read-only/compatibility projection for missing/v1-v4
  codec_v5.py       strict v5 head, commit, and generation codecs

skills/mission/lib/mission_application/
  lifecycle.py      init/advance/halt/reactivate use cases
  review_score.py   review -> score -> pass trust boundary
  artifact.py       artifact/progress/context evidence use cases
  planning.py       plan/handoff/provider-evidence use cases
  runtime_guards.py permission and Stop-observation use cases
  ports.py          repository, clock, identity, evidence, provider protocols

skills/mission/lib/mission_persistence/
  legacy_v4.py      behavior-compatible MissionRepository, not crash UoW
  local_uow.py      v5 RecoverableUnitOfWork and repository implementation
  reader.py         versioned authoritative snapshot reader
  recovery.py       deterministic recovery state machine
  gc.py             mark/quarantine/purge
```

Names are proposals; dependency direction and responsibility separation are the
contract. The CLI remains a thin adapter and distribution mirror until a later
modular CLI Issue is explicitly approved.

Application use cases depend on `MissionRepository`, the common load/execute
port. Only the v5 implementation satisfies `RecoverableUnitOfWork`, whose
contract includes lease admission, staging, CAS, immutable generation, commit
record, crash recovery, idempotency, and GC. Calling the v4 compatibility
adapter a full UnitOfWork is prohibited because it does not provide those
guarantees.

### 3.2 State and reference rules

- All v5 closed objects reject unknown variants. `Finding.status` is only
  `open|resolved`; legacy missing/ignored status maps to `open`, v5
  `open_high` counts only open High findings, and no migration command produces
  `resolved`. Resolution behavior remains future work.
- Evidence references are `{kind, relative_path, digest, size, ...binding}` and
  are validated before entering the kernel. Raw bytes stay outside the
  aggregate.
- A provider result is untrusted input until a use case validates its exact
  contract and immutable reference. It never becomes pass, review, or score
  authority.
- The pass rule stays mechanically equivalent to the current rule: verified
  score provenance, findings evidence consistency, `open_high == 0`, composite
  threshold, minimum item, agreement, artifact gate when applicable, and
  required specialist results.
- Lease expiry is evaluated from an injected clock. The persisted lease is a
  complete fenced record; acquired/renewed/taken-over/rejected are typed
  admission results, not ad hoc dict mutation.
- `aggregate.json` is a rebuildable index, not an authority competing with a
  session head.
- Every v5 JSON authority uses the ADR-005 canonical, bounded,
  duplicate-rejecting, finite-only codec and strict no-follow/single-link
  snapshot reader.

## 4. Strangler migration

### Stage 0 — Freeze behavior and inventory

Add characterization tests and a machine-readable command ownership inventory.
No production routing changes.

Consistency: current CLI is the sole writer. Snapshot fixtures pin observable
JSON, exit codes, stderr categories, file bytes, schema behavior, and lease
carrier behavior for representative commands.

Safe interruption: stopping here leaves only tests/documents; runtime is
unchanged.

Exit gate: every parser command is classified as query, Mission aggregate,
separate aggregate, external saga, or administrative coordinator.
Plugin distribution and Python 3.9 checks are first made recursive so every new
module added by later stages is necessarily mirrored, parsed, and imported.

### Stage 1 — Typed read model and codecs

Introduce closed unions, aggregate invariants, and missing/v1-v4 decoders. Run
them in tests and optional read-only comparison only. Do not write v5.

Consistency: production readers remain `_load_state_json`; the typed decoder is
compared against #483 golden snapshots and terminal/pass projections.

Safe interruption: no command imports the new writer and no state is rewritten.

Exit gate: the current plan/handoff/review/score/lease/terminal fixture corpus
decodes without physical mutation or decision drift; unknown schema/union
values and partial leases fail closed. Arbitrary legacy finding status never
becomes resolved authority, and v5 strict-codec attacks are rejected.

### Stage 2 — Transition table and generated guidance

Implement `decide` and table-derived `derive_next` behind a pure shadow adapter.
For production `next`, compare legacy and new normalized decisions in tests or
diagnostic-only mode; do not change returned guidance yet.

Consistency: the shadow path receives a deep copy, has no ports, and cannot
emit effects. Mismatch is an observation, never an automatic write or fallback.

Safe interruption: legacy mutation and legacy `next` remain authoritative.

Exit gate: bounded exhaustive transition properties pass, all existing next
tests map to named rules, every reachable active nonterminal class has exactly
one ranked primary sequence, and every presented local sequence is executable
from the same table.

### Stage 3 — Extract application use cases on v4 persistence

Move one command capability at a time behind use-case ports, initially using
`LegacyV4Repository`. Keep the state bytes and public CLI behavior compatible.
Start with state-only lifecycle commands, then review/score/pass,
artifact/progress/context evidence, planning/provider handoff, and runtime guard
observations.

Consistency: a static routing registry assigns each command to exactly one
implementation. There is no dual write. Characterization tests compare complete
state and output; kernel-backed commands use the same `StateLock`, lease check,
strict validators, and publication helpers while the v4 repository remains
active.

Safe interruption: migrated commands stay kernel-backed; unmigrated commands
stay legacy-backed; both operate on the one v4 state representation. A command
cannot be partially routed.

Exit gate: no mutating parser command is unowned, and every kernel route passes
its old regression suite plus new use-case tests.

### Stage 4 — Build the v5 UnitOfWork behind ports

Implement staging, immutable generations, generation/head CAS, commit records,
operation idempotency, crash recovery, and GC without making v5 the default.
Exercise it with an isolated repository contract suite and fault injection at
every publish point.

Consistency: v4 sessions still use `LegacyV4Repository`. The new repository has
no production session unless created by an explicit test fixture.

Safe interruption: no existing session layout or command route changes.

Exit gate: the repository conformance suite proves lease-first publication,
single-winner CAS, immutable collision handling, recovery idempotence, and
reference-safe GC.

### Stage 5 — Route mutations through repositories and bind the v5 UnitOfWork

Change the extracted use cases to depend only on `MissionRepository`; select
the behavior-compatible v4 repository or the v5 `RecoverableUnitOfWork` from
the loaded session format. Separate aggregates keep their own repositories but
use the same strict filesystem primitives where applicable.

Consistency: writer selection is loaded-state-derived and immutable for the
session. An environment flag cannot change it. Production query/consumer routes
are not yet declared v5-capable and `init` remains v4. Administrative
coordinators call per-session repository operations rather than editing JSON
directly.

Safe interruption: `init` still creates v4, so all real sessions remain on the
proven compatibility path while v5 gets full command-surface tests.

Exit gate: the application/repository harness can run every v5 command happy
path and recovery path; the command inventory reports zero direct session JSON
writers outside the two repository implementations. CLI/consumer v5 support is
not claimed until Stage 6.

### Stage 6 — Migrate authoritative readers

Route the Python query layer, Stop-hook verdict, audit, worktree archive, and
state snapshot consumers through a version-aware authoritative reader. Shell
and consumers no longer interpret fields directly from `sessions/*.json`.

Consistency: the reader returns the same typed snapshot for missing/v1-v4 and
verifies v5 head, commit, and generation before exposing any field. A v4-only
reader still rejects v5 as future schema. The Stop hook displays the Python
verdict and does not independently infer loop/pass/freshness.

Safe interruption: `init` still creates v4; consumer conversion can ship before
any production v5 session exists.

Exit gate: mixed fixture tests prove loop blocking, audit, archive, snapshot,
and query behavior on v4/v5, including malformed-head fail-closed cases, and a
static test rejects direct shell/consumer reads of authoritative state fields.

### Stage 7 — New-session v5 cutover

Switch only new `init` to v5. Existing missing/v1-v4 sessions keep the v4 writer
for life. Readers accept missing/v1-v5; readers with maximum v4 reject v5 by the
#483 contract.

Consistency: no automatic migration and no dual write. The head record is the
only v5 authority; compatibility projections are verified/recoverable outputs.

Safe interruption: rollback changes the `init` default only; already-created v5
sessions remain readable by the new CLI and old v4 sessions remain unchanged.

Exit gate: mixed v4/v5 multi-session tests, full CLI tests, plugin-mirror tests,
artifact hygiene, and distribution documentation are green. A separate
approval is required before removing the v4 writer.

## 5. Coexistence invariants

The following invariants apply to every migration PR:

1. one session has one authoritative writer format;
2. one command has one active implementation route;
3. shadow logic is pure and cannot publish;
4. no public file is written before lease/fence validation;
5. stale head generation or stale fence rejects without state/effect change;
6. new evidence is immutable and content-addressed;
7. provider output is evidence only;
8. the mechanical pass gate is identical or stricter;
9. an incomplete migration stage can remain released indefinitely;
10. schema v5 is not emitted until all mutating command families have a v5
    route and recovery test;
11. v5 readers expose no state until head, commit, and generation lineage all
    validate, and shell hooks consume Python verdicts only;
12. a repeated v5 operation ID with the same normalized intent returns one
    commit result; reuse with different intent fails closed.

## 6. Transition property-test design

The test model uses representative values rather than unrestricted JSON:

- phase: every closed value;
- terminal/control combinations: active, pass, evidence completion, each halt
  class, invalid contradiction;
- score: absent, below/equal/above composite threshold, below/equal minimum,
  agreement pass/fail, current/stale iteration;
- findings: no evidence, matching zero High, matching positive High, count
  mismatch, invalid evidence; legacy missing/ignored status maps to open and
  `resolved` is a read-only unreachable v5 class in this migration;
- iteration: first, later, at maximum, unbounded;
- plan/handoff: absent, current valid, generation drift, consumed;
- lease: absent legacy, matching live, foreign live, expired takeover, stale
  retired token, partial invalid;
- provider: absent, prepared, approved, running, terminal, drifted.

For each valid initial fixture, BFS explores enabled representative commands up
to terminal or a bounded depth derived from `max_iter`. State identity for
deduplication is the tuple of rule-relevant enum/boolean/equivalence-class
fields, not timestamps or random IDs. Each transition is run twice to assert
determinism, and invariants run after every step.

`derive_next` returns structured `GuidanceStep` values before rendering text.
Only rules marked primary-guidance eligible participate; a total rank plus
stable rule ID must select exactly one primary recipe for each reachable active
nonterminal equivalence class. Progress/activity/property maintenance rules are
excluded. A missing primary or equal-rank tie fails table construction and the
BFS property suite.
For every `local-command` step, the test materializes its command factory and
calls `decide`; rejection is a failure. For every `external-observation` step,
the test first asserts rejection without the named observation, adds only a
valid typed observation fixture, and then asserts acceptance of the follow-up
command. Text rendering is snapshot-tested separately and cannot change
executability.

Adversarial tests mutate one field at a time, add unknown keys/Symbol-equivalent
non-string data where Python input permits, replace regular files with links or
FIFOs, and alter digest/size/generation/fence bindings. Every such case must
produce no Transition or staged public effect.

## 7. UnitOfWork crash matrix

Fault injection is required after each durable boundary:

| Fault point | Expected restart result |
|---|---|
| while staging, before prepare record | verified staging residue removed; head/effects unchanged |
| after prepare record, before immutable publish | rollback to base; no public projection change |
| after immutable generation, before projection | generation remains unreferenced; rollback/finalize record permits later GC |
| after one of several compatibility projections | all changed projections restored from verified backups; head remains base |
| after immutable commit record, before head replace | commit record/generation unreferenced; projections rolled back |
| after head replace, before index/projection verification or stdout | roll forward from immutable bytes; same operation-ID retry returns the recorded target result without applying the command twice |
| during rollback restore | bounded recovery residue retained; new writes blocked until explicit recovery succeeds |
| during GC quarantine | current roots re-read; referenced or ambiguous generation is never purged |

The suite sends process-termination-style faults, not only Python exceptions.
Recovery must be idempotent across repeated invocations and must not require a
provider, network, or cloud service.

## 8. Implementation child Issue proposals

Each proposal below is one capability or trust boundary and is intended to be
copied into a GitHub Issue after refreshing line numbers. GitHub Issues are not
created by this design task.

Every child Issue that adds or changes a distributed module must add the same
relative file under `plugins/mission/`, make the sync inventory cover it, and
pass the recursive Python 3.9 parse/import gate introduced by D1. This is an
acceptance condition of each Issue below, not an optional final-cutover cleanup.

### D1. Make distribution sync and Python compatibility gates recursive

Dependencies: #485 design only.

Current code: the explicit `SYNC_PAIRS` list in
`test_plugins_in_sync.py:85` onward and explicit `TARGETS` in
`test_issue99_py39_compat.py:19` onward do not discover new library modules.

Expected behavior: define one deterministic recursive inventory for canonical
Mission Python packages and their plugin mirrors. Every production `.py` module
in scope must have an identical mirror, parse with the supported Python grammar,
and be importable from both canonical and plugin roots without importing a
maintainer-local path.

TDD Red:

- add an unlisted canonical fixture module and prove sync fails for missing
  mirror rather than silently ignoring it;
- alter one mirrored byte and prove the reported pair is exact;
- add unsupported syntax/import to a discovered fixture and prove the Python
  compatibility gate fails;
- import the package from canonical and plugin roots in isolated subprocesses;
- reject symlinked or path-escaping inventory entries.

Acceptance:

- later kernel/application/persistence modules require no hand-maintained
  per-file test target to receive sync and compatibility coverage;
- current `SYNC_PAIRS` behavior and plugin distribution tests remain green;
- no kernel or persistence behavior is introduced by this Issue.

### K1. Add the strict versioned `MissionState` aggregate and v1-v4 decoder

Dependencies: D1.

Current code: schema/version loader at `mission-state.py:241-270`; lease dict at
`:560-908`; phase enum at `:1786-1816`; initial dict at `:6953-7075`;
terminal outcome in `mission_common.py`; schema snapshots in
`test_issue483_schema_compat_matrix.py`.

Expected behavior: define closed Phase, TerminalOutcome, Plan, Handoff, Review,
Finding, Score, and Lease types; decode missing/v1-v4 without write; reject
future/non-integer schemas, partial leases, unknown v5 variants, and v5 Finding
statuses other than `open|resolved`.

TDD Red:

- import the current plan/handoff/review/score/lease/terminal fixture corpus,
  not only #483's minimal version fixtures, into the canonical typed view;
- preserve `prepared|consuming|consumed|rejected` handoffs and all authoritative
  lineage fields; retain unowned legacy fields for v4 passthrough;
- map missing or arbitrary ignored legacy Finding status to `open`; accept only
  `open|resolved` in v5, require prior identity/evidence/time on `resolved`, and
  prove no migration command can emit `resolved`;
- reject `accepted-risk` and `not-reproducible` explicitly in v5;
- reject bool/string/float/null/future versions and partial leases;
- prove decoding does not change source bytes;
- round-trip representative v5 values through the canonical codec; reject
  duplicate keys, invalid UTF-8, `NaN`/`Infinity`, trailing data, oversize,
  unknown keys, links, FIFOs, hard links, and identity swaps.

Acceptance:

- new code is under `skills/mission/lib/` with no CLI route;
- all v1-v4 golden results and terminal outcomes match current behavior;
- no production state or evidence file changes;
- D1 recursive Python 3.9/import and plugin mirror gates pass.

### K2. Replace independent transition/next trees with one transition table

Dependencies: K1.

Current code: phase mutation in `_transition_phase`, `cmd_advance`
(`mission-state.py:8239-8405`), generic `cmd_set` (`:9325-9534`), terminal
writers (`:13633-14206`), and separate `_happy_path_sequence` /
`_derive_next_action` (`:8417-8852`).

Expected behavior: introduce the closed command union, named transition rules,
pure `decide`, structured `GuidanceStep`, and table-derived `derive_next`.
Primary-guidance eligibility and total rank exclude maintenance commands and
select exactly one recipe per reachable active nonterminal state. Typed
continuation edges derive each later step from the preceding result. External
work is represented as a required observation plus follow-up command, not a
falsely executable local command.

TDD Red:

- bounded BFS property suite described in section 6;
- each current `next_action` fixture maps to one named rule;
- all rendered local command sequences execute through `decide`, with every
  continuation enabled by the state produced by its previous step;
- pass guidance cannot be emitted when the pass command rejects;
- terminal commands, audited reactivation, and stale recovery are disjoint;
- duplicate matching command rules, missing primary guidance, and equal-rank
  primary ties fail tests.

Acceptance:

- kernel imports no I/O/process/environment module;
- `decide` is deterministic and returns no effects on rejection;
- existing next/advance/terminal tests pass through an adapter;
- production `next` changes only after exact parity is demonstrated;
- D1 distribution and compatibility gates cover every new module.

### U1. Implement private staging and immutable generation publication

Dependencies: K1.

Current code: `_atomic_write` (`mission-state.py:1156-1172`), strict publish
transaction (`:11064-11746`), review immutable publication (`:11746` onward),
and worktree generations (`:6186-6248`).

Expected behavior: same-filesystem private transaction directory, strict
manifest, fsync discipline, immutable `VerifiedBlobSet`, content-addressed
state/evidence generation, and no-overwrite collision validation. Effect
descriptors bind one logical blob ID/digest/size to one captured byte string;
nothing becomes authoritative in this Issue.

TDD Red:

- staged objects are private, regular, single-link, bounded, and digest-bound;
- links, FIFOs, hard links, path escapes, oversize input, and mutation races
  fail closed;
- missing, extra, duplicate, digest/size-mismatched, or source-mutated blob
  bindings reject the complete stage; staged bytes equal captured bytes;
- identical generation reuse is idempotent; same name/different bytes rejects;
- failure at every staging fsync leaves no public reference.

Acceptance:

- repository contract tests pass without changing `init` or session heads;
- existing strict publication tests remain green;
- staged bytes contain no unbounded raw provider input or secret-bearing
  command values;
- D1 distribution and compatibility gates cover every new module.

### U2. Add fenced generation CAS and immutable commit/head records

Dependencies: U1.

Current code: `StateLock` and `atomic_write_json` at
`mission-state.py:1125-1288`; lease admission at `:829-908`; session path at
`:596-605`; archive pointer pattern at `:6218-6248`.

Expected behavior: exact base generation/head-digest CAS, lease/fencing
pre-admission, durable prepare record, immutable commit record, caller-stable
operation ID, and atomic v5 head replacement. `ExecutionRequest` explicitly
carries session, command, blobs, operation identity/intent, and presented lease.
Lease acquire/renew/takeover is calculated as a pending decision on the base
snapshot and is published only in the same generation as an accepted domain
transition. Only `N -> N+1` commits; stale snapshots and stale fences publish
nothing.

TDD Red:

- two prepared commits from generation N yield one winner and one CAS reject;
- matching session without matching token rejects; pre-admission expired
  takeover increments the fence once and retires the old token;
- every preliminary domain-guard rejection leaves head, generation, lease, and
  public files byte-identical;
- a head/lease change between pending admission and commit rejects the stage,
  then reload/re-decide/restage produces at most one combined lease/domain
  generation;
- lease expiry/takeover after stage rejects all publication and requires fresh
  admission, reload, re-decide, and restage with the new fence;
- lease reject occurs before any projection/publication;
- head/commit/state duplicate keys, invalid UTF-8, non-finite numbers, bounds,
  file-type/identity, digest, and size violations fail closed;
- same operation ID and intent returns one result; operation-ID reuse with a
  different normalized intent rejects;
- crash before head replacement leaves base authoritative; after replacement
  target is authoritative.

Acceptance:

- one v5 test repository can commit and read a state generation;
- commit records contain bounded audit metadata but no lease token or raw
  provider secrets;
- no production session defaults to v5;
- D1 distribution and compatibility gates cover every new module.

### U3. Add deterministic UnitOfWork crash recovery

Dependencies: U2.

Current code: in-process `_PublishedFilesTransaction` rollback
(`mission-state.py:11556-11579`) and recovery residue handling
(`:11350-11495`); no durable transaction recovery currently exists.

Expected behavior: implement the section 7 recovery matrix. Recovery runs
before new writes, is idempotent, restores base projections when head is base,
rolls forward when head is target, recreates a missing bounded operation
tombstone from the committed record, and blocks on ambiguity.

TDD Red:

- process termination at every protocol boundary;
- repeated recovery produces the same head/files/report;
- kill immediately after head replacement, then retry the same operation ID
  and intent; return the original result without a second transition/effect;
- same operation ID with a different intent, or an operation record that
  disagrees with the head/commit, blocks rather than guessing;
- projection restore checks exact identity and bytes;
- rollback failure preserves a verifiable residue and blocks subsequent
  commits;
- foreign/newer head or malformed journal never triggers guessed repair.

Acceptance:

- all fault points converge to base or target, never a claimed mixed commit;
- no recovery path creates a domain Transition;
- current rollback and artifact regression suites remain green;
- D1 distribution and compatibility gates cover every new module.

### U4. Add reference-safe generation garbage collection

Dependencies: U3.

Current code: worktree immutable generations intentionally retain old versions
(`state-management.md`, worktree archive section); no session-generation GC
exists.

Expected behavior: mark current heads/archive pointers/open recovery records,
quarantine only aged unreferenced generations after locked revalidation, and
purge unchanged quarantine entries only on a later pass. Small operation
tombstone/results remain idempotency authority without rooting old state
generations. Dry-run is default.

TDD Red:

- retain current, prior safety generation, archive roots, and every open-journal
  reference;
- after multiple commits and grace, collect an old committed state generation
  that is outside current/prior/archive/recovery roots;
- after that collection, retry its old operation ID and return the tombstoned
  bounded result without a Transition/effect; different intent still rejects;
- quarantine only aged unreferenced regular generations;
- head movement between scan and quarantine cancels deletion;
- links, digest mismatches, incomplete scans, and ambiguous roots fail closed;
- interrupted quarantine/purge is idempotently recoverable.

Acceptance:

- GC never changes MissionState or outcomes;
- destructive mode reports exact generation IDs and requires an explicit flag;
- full recovery suite passes with GC interleavings;
- D1 distribution and compatibility gates cover every new module.

### A1. Extract lifecycle use cases on the v4 compatibility repository

Dependencies: K2.

Current code: `cmd_init` at `mission-state.py:6953`, activity commands at
`:8132-8182`, `cmd_advance` at `:8239`, `cmd_set` at `:9325`, halt/reactivate/
refresh/resume at `:13964-14295`, project-root/stale/bulk coordinators at
`:14296-14920`, terminal outcome helpers, and aggregate index updates.

Expected behavior: thin CLI adapters construct typed commands; lifecycle use
cases call `decide` and a repository port; existing missing/v1-v4 sessions keep
the current JSON shape and lease behavior. Generic `set` loses authority over
fields with dedicated commands.

TDD Red:

- byte/semantic characterization for init, advance, activity, halt,
  reactivation, refresh, and routed terminal outcomes;
- invalid phase/activity/halt/role changes leave bytes unchanged;
- aggregate index failure is reported/rebuildable without changing session
  authority;
- each routed command has exactly one registry owner; unmapped or duplicate
  mutators fail the inventory test.

Acceptance:

- listed commands contain adapter logic only and use the application port;
- all lifecycle, lease, activity, terminal, and resume tests pass;
- existing sessions remain v4 and can stop migration here safely;
- D1 distribution and compatibility gates cover every new module.

### A2. Extract the review/score/pass authority boundary

Dependencies: A1, K2.

Current code: `cmd_manual_score_capture` (`mission-state.py:10393` onward),
`cmd_review_import` (`:12099-12169`), review aggregation (`:12825` onward),
`cmd_push_score` (`:13304-13500`), finalize/closeout (`:13503-13608`), and
`cmd_mark_passes` / review supersede (`:13633-13963`).

Expected behavior: strict review/manual evidence adapters yield typed immutable
references; one use case reduces review to score; only the kernel pass rule can
produce completion. The ADR-003 gate is unchanged, and providers cannot provide
a pre-decided score/pass fact. Legacy findings map to `open`; `open_high` counts
only open High findings. `resolved` has no producing command in this migration,
so arbitrary legacy status text cannot lower the gate.

TDD Red:

- malformed/duplicate-key/mutated review evidence rejects before staging;
- foreign lease, stale fence, or expiry race leaves public bytes and state
  unchanged for manual capture, review import/aggregate, and score publication;
- score provenance, revision scope, findings count, agreement, threshold,
  minimum item, artifact, and required specialist gates match current tests;
- content-addressed evidence and state publish as a set or the v4 transaction
  restores their prior bytes on failure;
- force pass requires the existing pinned approval evidence and cannot replay;
- open High findings can never produce a pass Transition.

Acceptance:

- current review, score, provenance, findings, agreement, and forced-pass suites
  pass without weaker assertions;
- pass authority exists only in the kernel rule;
- v4 compatibility bytes remain readable and no unsupported Finding status is
  introduced;
- D1 distribution and compatibility gates cover every new module.

### A3. Extract artifact, progress, and context evidence use cases

Dependencies: A1.

Current code: artifact handlers at `mission-state.py:6472-6715`, progress
update/get/clear at `:6746-6809`, `cmd_context_manifest` at `:13247` onward,
and artifact/progress/context tests.

Expected behavior: artifact/evidence bytes become validated inert effect
requests. `LegacyV4Repository` executes them through the current strict
lease-first publication transaction, without generation CAS yet. `artifact
publish` continues to record consent and does not gain remote-send behavior.
Progress and context manifests remain observation/evidence only and cannot
alter score, pass, review, or provider authority.

TDD Red:

- retain every #475 foreign-lease and exception rollback assertion;
- retain exception rollback and multiple-output atomicity on the v4 adapter;
- a rejected Transition or lease check emits no public effect;
- mutation/link/FIFO/hard-link/digest attacks fail closed;
- normal output bytes and artifact gate behavior match current CLI;
- progress/context output and state bindings match current behavior, including
  clear/no-op and invalid evidence cases.

Acceptance:

- the extracted use case performs no direct filesystem write;
- the v4 adapter preserves current output bytes and rollback behavior;
- content-addressed evidence and strict artifact identity remain intact;
- D1 distribution and compatibility gates cover every new module.

### A4. Extract plan/handoff/provider evidence without moving authority

Dependencies: A1, K2.

Current code: `cmd_specialists` (`mission-state.py:4120`), invocation recording
and reconcile (`:5260-5694`, `:6811` onward), provider preflight/invocation
helpers, `cmd_plan_import` (`:12172-12259`), planning promotion
(`:12298-12498`), executor handoff (`:12504` onward), and
`planning_lifecycle.py`.

Expected behavior: plan and handoff are closed typed objects bound to generation,
digest, source, selection, invocation, and iteration. Provider dispatch remains
an external saga: commit `dispatch-unknown` intent before spawn, persist a
verified process/provider receipt later, and reconcile without automatic
redispatch. A provider cannot mutate state or decide review, score, or
completion.

TDD Red:

- preserve input regular-file bounds and every provider receipt/packet drift
  rejection;
- foreign lease, stale fence, or lease-expiry race publishes no raw/canonical
  plan bytes and changes no invocation/planning state;
- plan generation/source/invocation drift rejects before handoff;
- crash after dispatch intent but before spawn, after spawn but before
  PID/process receipt, and after receipt but before terminal commit are distinct
  fault cases; no running/unknown state automatically dispatches again;
- an unprovable process/result can only become fenced `abandoned-unknown`, while
  a recorded identity reconciles only that exact process;
- stale reconciliation epoch rejects; replayed approval/receipt rejects;
- provider exit zero without a validated result remains unvalidated evidence.

Acceptance:

- existing provider isolation, plan import, planning lifecycle, and handoff
  tests pass;
- no provider adapter imports the kernel repository implementation;
- external process failures cannot manufacture pass/review/score authority;
- D1 distribution and compatibility gates cover every new module.

### A5. Extract runtime guard observation writers

Dependencies: A1, K2.

Current code: `cmd_stop_guard_observe` at `mission-state.py:8068` onward and
`cmd_permission_preflight` at `:9291` onward. Both record bounded observations
that are later consumed by control logic.

Expected behavior: adapters validate observed runtime facts, then typed use
cases append only the allowlisted bounded observation. Neither command derives
freshness, pass, or terminal outcome independently, and neither receives a
generic state-write escape hatch. Python remains the freshness/verdict authority.

TDD Red:

- malformed, stale-generation, foreign-lease, stale-fence, and expiry-race
  observations leave state/evidence bytes unchanged;
- unknown observation fields and unbounded text reject before a Transition;
- a Stop-hook observation cannot set pass, halt, phase, score, or lease fields;
- permission results cannot weaken a denied/unknown capability into allowed;
- command ownership inventory assigns both writers exactly once.

Acceptance:

- both handlers are thin adapters over typed use cases and the repository port;
- current permission/Stop-guard/freshness regression suites remain green;
- D1 distribution and compatibility gates cover every new module.

### P1. Add format-pinned repository selection and v5 UnitOfWork binding

Dependencies: U3, A2, A3, A4, A5.

Current code: the `LegacyV4Repository` routes introduced by A1-A5; state
locking/writing at `mission-state.py:1125-1312`; artifact/review publication at
`:11064-12169`; and direct state writes remaining in the command inventory.

Boundary amendment (#511, 2026-08-18): P1 binds only the closed K2 command
subset (`AdvancePhase`, `MarkHalt`, `MarkPass`, `Reactivate`, and
`ResumeStale`) to the common `MissionRepository.read/execute` port. Extracted
A1-A5 compatibility mutations for which K2 does not yet define a closed command
remain on the narrower `LegacyMissionRepository` port and are mandatory C1
work in #513 before production v5 activation. This avoids inventing domain
semantics during persistence binding while keeping the deferred work explicit.

Expected behavior: use cases for the closed K2 subset depend only on
`MissionRepository`. The loaded session format selects either
`LegacyV4Repository` or the v5 `RecoverableUnitOfWork` once per session. v5
effects are staged, fenced, CAS committed, idempotently retried, and
crash-recovered as one protocol; v4 keeps its current layout and lease-first
transaction and is not advertised as a full UnitOfWork. Both receive the same
explicit typed `ExecutionRequest`; neither reads session, lease, command,
blobs, or operation identity from ambient state. No command dual-writes or
selects a writer from an environment flag. Compatibility-only use cases are
never routed to v5 before C1 closes their command and projection contracts.

TDD Red:

- closed typed request and sealed-transition repository binding passes against
  both v4 and v5. The complete five-command by two-repository application
  matrix is a mandatory C1 pre-cutover gate, while the stronger
  generation/recovery/GC suite is required only of v5;
- the same typed request satisfies v4/v5 lease-first contracts, and domain
  rejection changes neither repository's authoritative state;
- every artifact/review/score multi-file effect gets process-crash injection at
  each section 7 boundary on v5;
- stale snapshot, head, generation, lease token, or fencing epoch publishes no
  state, evidence, artifact, or compatibility projection;
- an open/ambiguous recovery record blocks both state-only and effectful writes;
- static inventory rejects repository-external session/evidence writers in the
  five closed-command routes and rejects compatibility-only use cases routed to
  v5. Repository-wide direct-writer elimination remains C1 work in #513.

Acceptance:

- all five closed K2 commands use only the common `MissionRepository` port;
- remaining A1-A5 compatibility mutations are isolated behind
  `LegacyMissionRepository`, cannot reach a v5 repository, and are listed as
  mandatory pre-cutover C1 work in #513;
- v4 sessions retain current bytes and never upgrade; isolated v5 fixtures
  converge to base or target after every injected crash;
- no existing CLI route creates a v5 production session yet;
- D1 distribution and compatibility gates cover every new module.

### R1. Migrate every authoritative state consumer to the versioned reader

Dependencies: K1, U2, P1.

Current code: direct session JSON interpretation in
`scripts/mission-stop-guard.sh:213-255`, `scripts/mission-audit.py:815` and
`:933-935`, `skills/mission/lib/worktree_archive.py:854` onward, the
`state_snapshot.py` reader, and CLI query scanners. These consumers currently
assume `sessions/<sid>.json` is the full aggregate.

Expected behavior: one version-aware Python reader returns a verified typed
snapshot. It reads missing/v1-v4 directly and resolves v5
head -> commit -> immutable generation only after strict lineage validation.
The Stop hook receives and displays the Python loop/freshness verdict; it never
reads authoritative fields with shell/JQ. Audit, archive, snapshot, and CLI
queries consume the same verified snapshot API. A legacy v4-only reader
encountering v5 still fails closed as required by #483.

TDD Red:

- active v5 sessions block Stop exactly like equivalent v4 sessions, while
  pass/halt/evidence-complete verdicts retain their current distinctions;
- mixed v4/v5 roots produce equivalent audit, archive, snapshot, list, stats,
  freshness, and next results;
- malformed/missing head, commit, or generation; digest/size/generation drift;
  duplicate/non-finite JSON; links/FIFO/hard links; and future schema all fail
  closed without fallback to a filename or empty/inactive state;
- a static guard rejects shell/JQ or consumer-local interpretation of
  `loop_active`, `passes`, `halt_reason`, lease, and freshness fields;
- old-v4-reader/new-v5-writer and new-reader/old-v4-writer compatibility tests
  cover both directions.

Acceptance:

- every consumer named above uses the authoritative reader or a Python verdict;
- Stop-hook behavior cannot treat an unreadable v5 session as inactive;
- audit/archive/snapshot preserve strict evidence lineage and current v4 output;
- D1 distribution, script mirror, and compatibility gates cover every change.

### C1. Complete CLI adapter inversion and cut new sessions to schema v5

Dependencies: U4, P1, R1.

Current code: `_build_parser` at `mission-state.py:16109-16798`, direct JSON
writers across `cmd_*`, query scanners, plugin mirror, and #483 fixtures. P1
binds the five closed K2 commands to the common repository port; other A1-A5
mutations still require the legacy compatibility sub-port.

Expected behavior: all commands in section 2.3 have one declared owner; direct
session writes occur only in `LegacyV4Repository` or v5
`RecoverableUnitOfWork`; queries read missing/v1-v5; new `init` chooses v5;
existing v1-v4 sessions keep v4.
Mutating v5 invocations carry a caller-stable operation ID and expose an
operation-result lookup; a caller that lacks the ID may issue a new operation
but may not label it an automatic retry of unknown outcome.
Before changing the production `init` default, C1 closes every remaining A1-A5
mutation as a typed command/decision/effect contract. The deferred inventory
includes at least activity start/end and their segment/rollup projection,
generic set authority mutations, permission timing/activity projection, and
every other use case that still requires `LegacyMissionRepository`.

TDD Red:

- static inventory fails for every unowned mutating parser command or direct
  session write outside repositories;
- a common-port-only conformance suite runs every A1-A5 mutating use case
  against v5; activity, generic set, and permission timing projections derive
  from typed decisions and reject unknown, missing, or type-confused authority
  fields;
- a five-command by v4/v5 matrix covers every closed P1 command's positive and
  domain-rejection paths, audit binding, lease admission, and authoritative-byte
  non-mutation;
- mixed concurrent v4/v5 sessions preserve independent lease/generation state;
- v4 reader rejects v5, v5 reader accepts fixed v1-v5, and existing v4 mutation
  never upgrades to v5;
- end-to-end sequences for every `next` branch run on v5;
- head-commit/response crash followed by the same operation-ID retry returns
  the original result, while a different intent with that ID rejects;
- recovery/GC, plugin sync, hygiene, doc consistency, and full suite pass.

Acceptance:

- every A1-A5 mutating command uses the common typed repository/UnitOfWork
  boundary; `LegacyMissionRepository` is confined to the internal v4
  projection;
- all five closed P1 commands pass the complete v4/v5 application conformance
  matrix;
- activity start/end, generic set, permission timing/activity projection, and
  all other mutations deferred by #511 execute and recover authoritatively on
  v5 before the production cutover;
- no dual-write or per-invocation writer switch exists;
- new-session v5 cutover has a documented rollback limited to the init default;
- all current safety gates are demonstrably equal or stricter;
- removal of the v4 writer and physical migration remain separate future work;
- D1 distribution and compatibility gates cover every new module.

## 9. Dependency order

```text
D1 -> K1
      ├-> K2 -> A1 ├-> A2
      │                ├-> A3
      │                ├-> A4
      │                └-> A5
      └-> U1 -> U2 -> U3 -> U4
U3 + A2 + A3 + A4 + A5 -> P1
K1 + U2 + P1 -> R1
U4 + P1 + R1 -> C1
```

Recommended issue order is D1, K1, K2, then A1 and U1 on separate branches.
A2-A5 follow A1 while U2 and U3 follow U1. After both tracks converge, P1 binds
the extracted use cases to the format-pinned repository. U4 can proceed after
U3 in parallel with P1. R1 follows the reader/UoW binding, and C1 waits for U4,
P1, and R1. This matches Stages 3-7 and keeps extraction and consumer migration
independently releasable before v5 activation.

## 10. Release and rollback boundaries

- D1 through Stage 6 may ship without activating v5.
- Each Issue keeps its own branch/worktree/PR and can be reverted without
  reverting unrelated capabilities.
- No Issue changes provider credentials, performs external sends, or adds cloud
  infrastructure.
- v5 activation is a separate release decision after full compatibility,
  recovery, and distribution tests. Version/tag/release work is outside #485.
- Removing legacy readers/writers, physically migrating existing sessions,
  adding a `resolved`-producing Finding transition, implementing additional
  Finding statuses, and changing pass thresholds are explicit non-scope.

## 11. Risks and controls

| Risk | Control |
|---|---|
| table and legacy behavior diverge | pure shadow parity, named rules, existing regression suite, table-derived guidance |
| two state authorities | per-session writer pin; no dual write; one atomic v5 head |
| crash leaves state/effect mismatch | durable prepare record, immutable generation, head commit point, deterministic recovery |
| stale writer publishes | lease/fence check plus generation/head CAS immediately before publication |
| GC removes recoverable data | complete root set, two-pass quarantine, grace periods, locked revalidation, fail-closed scan |
| old CLI reads v5 loosely | #483 future-schema rejection remains mandatory; R1 converts every supported consumer before cutover |
| provider gains authority through typed input | provider bytes remain untrusted evidence; pass/review/score commands are core-owned |
| migration stalls midway | every stage retains a complete single-writer path and can remain deployed indefinitely |

## 12. Non-scope and unresolved work

Not part of this migration:

- full rewrite, database/service split, complete event sourcing, or cloud
  dependency;
- changing review tier, score thresholds, findings vocabulary, or pass
  semantics;
- remote artifact publication or provider activation;
- automatic v4-to-v5 physical migration;
- deletion of the v4 compatibility writer;
- creating the proposed GitHub Issues.

The concrete on-disk field names and record-specific byte limits for v5
head/commit manifests should be fixed by K1/U1/U2 tests before implementation,
but their required semantics are decided by ADR-005. The retention grace
interval and number of prior safety generations remain operational constants to
calibrate in U4; the fail-closed root and two-pass quarantine rules are not
optional. A transition that produces `Finding.status=resolved` and its required
resolution evidence are intentionally deferred; until that separate decision,
all migration writers emit `open` and cannot reduce `open_high` via status.
Operation tombstone compaction is also deferred; U4 may collect old state
generations but must retain each bounded tombstone/result until a later design
preserves both operation-ID non-reuse and result lookup.
