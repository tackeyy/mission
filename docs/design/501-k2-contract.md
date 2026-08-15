# Issue #501 K2 exact contract

## 1. Conclusion

K2 adds a bound read-side `Snapshot`, a closed `GuidanceFacts` projection, a
paired decoder, and a transition-table shadow implementation. It does **not**
switch production `next`.

The exact provenance choice is to carry the same **non-wire**
`SnapshotProvenance` value and the same reader-issued opaque binding identity
on both `MissionState` and `GuidanceFacts`, in addition to the enclosing
`Snapshot.provenance`. Rejecting recombination only
when constructing `Snapshot` is insufficient: the accepted ADR signature
separates the pair again as `derive_next(state, guidance)`, so a later caller
could otherwise combine two individually valid values. `derive_next` requires
both bindings to be present and exactly equal and returns no guidance on a
mismatch. The binding is computed by the paired decoder and is never encoded as
a v5 state field.

The v5 generation document adds one required closed `guidance` object. The
state aggregate remains the command authority; guidance is query-selection
authority only. Missing/v1-v4 documents keep their bytes and are projected by
the paired decoder from the same single parse.

K2 owns only command semantics that can be decided from the K1 `MissionState`
without inventing an A1-A5 observation contract. Cases whose executability
still depends on A1-A5 authority or goal-dispatch/host observation are
explicitly classified rather than counted as exact parity. The clock-dependent
budget wrapper is excluded before parity classification, as required by the
upper design. These are production-switch blockers, not permissive fallbacks.

## 2. Decision provenance

### 2.1 Derived from accepted upper-level design

The following are not new K2 choices:

1. ADR-005 Sections 1 and 3 own the pure kernel, one named transition table,
   closed command union, typed rejection, ranked primary guidance, typed
   continuations, and the separation between local commands and external
   observations.
2. The accepted #523 amendment fixes
   `derive_next(state: MissionState, guidance: GuidanceFacts)`, restricts
   `GuidanceFacts` to guidance selection, and keeps command acceptance on
   `MissionState` plus typed command input.
3. The accepted #523 amendment also fixes the read-side `Snapshot` and the
   write-side `AdmittedSnapshot(base: Snapshot, ...)` containment relation.
   `AdmittedSnapshot` and the U2 private persistence seam are not read-side
   guidance inputs.
4. The #501 authority decision fixes option 2, the 24-field allocation
   (11 existing `MissionState` fields, 13 `GuidanceFacts` fields, zero fields
   sourced only from `legacy_passthrough`), the guidance-only authority
   contract, a required v5 `guidance` object, and `terminal_outcome` as a
   computed property checked against stored v5 control.
5. K1 fixes the `MissionState` model, strict JSON codec, missing/v1-v4
   normalization, v5 closed objects, canonical encoding, and 4 MiB state limit.
6. U1 fixes 128-character blob tokens, 4,096-character relative paths,
   64 blobs, 16 MiB aggregate blob bytes, and private staging. U2 fixes the
   head/commit lineage and leaves its bytes-oriented staging call private until
   a kernel-issued `Transition` exists.
7. The migration plan assigns lifecycle adapters to A1, review/score/pass to
   A2, artifact/progress/context to A3, plan/handoff/provider evidence to A4,
   and runtime observations to A5.
8. Production switching is forbidden here. Clock-dependent budget override is
   outside K2 parity. Persisted `goal_dispatch_*` plus live host observation
   remain legacy authority.

### 2.2 New K2 decisions delegated by #501

The following choices are new and require owner review:

1. both pair members carry the same non-wire provenance value and opaque
   reader-issued identity; an enclosing `Snapshot` alone and value equality
   alone are not complete recombination defenses;
2. the exact `SnapshotProvenance`, `GuidanceFacts`, nested v5 wire fields,
   variants, legacy defaults, and bounds in Sections 4 and 5;
3. paired codec names and the rule that standalone `decode_mission_state`
   returns an unbound state that `derive_next` cannot consume;
4. collections are capped at 128 entries; identifiers/signals at 128
   characters; portable descriptive text at 2,048 characters;
5. provider invocations use four closed lifecycle variants (`selected`,
   `reserved`, `running`, `terminal`) derived from the existing status and
   lifecycle contracts;
6. parity has two result values: `exact` and `legacy-required`; only `exact`
   can support a future switch. The clock-dependent wrapper has no result value
   because it is outside the compared equivalence classes;
7. K2 implements the state-only command subset in Section 7. A1-A5 commands
   are not represented by generic dictionaries or prematurely accepted stubs;
8. malformed known legacy guidance fields fail paired decode instead of being
   silently replaced with defaults. Defaults apply only to absent fields.
9. all command and primary-guidance definitions live in one
   `TRANSITION_TABLE`, and every primary-guidance rule has both
   `command_guard(state, command)` and `guidance_guard(state, guidance)` on the
   same named rule object. A1-A5-owned follow-up commands remain outside the K2
   `Command` union; until their typed command authority exists, their rules use
   a typed non-executable deferred-command guard plus explicit
   observation/follow-up metadata rather than a guidance-only row.
10. `AdvancePhase(planning -> executing)` accepts a caller-supplied typed
    `PreparedHandoff` bound to the current plan; K2 validates it, while A4 owns
    producing that verified payload.
11. every accepted transition clears the read-side snapshot provenance and
    opaque binding; only a later paired repository read/admission may bind the
    resulting state to authoritative bytes again.
12. every provider-plan import ID must bind an existing `planning` invocation
    whose closed variant is `terminal` with status `completed`; this is derived
    from the existing `plan-import` admission contract rather than inferred
    from ID syntax.

## 3. Observations and bounds

### 3.1 Actual CLI corpus

`skills/mission/tests/mission_state_fixture_corpus.py` was executed against the
production CLI in isolated resolved temporary directories. The 36 returned
snapshots had a maximum compact canonical state size of 12,081 bytes in the
provider-plan case. Relevant observed maxima were:

| legacy field family | observed maximum items | observed maximum encoded bytes |
| --- | ---: | ---: |
| `specialists_selected` | 1 | 1,367 |
| `provider_plan_imports` | 1 | 472 |
| `specialist_invocations` | 1 | 346 |
| `review_tier_signals` | 0 | 2 |
| `planning_provider_binding` | one object | 190 |
| `canonical_plan` | one object | 421 |
| one observed projection string | n/a | 71 |

The production review-tier classifier has 31 possible unique signal types: 15
irreversible-operation keywords, 15 security keywords, and one task-profile
risk signal. It emits each signal type at most once even when the source text
contains repeated matches.

The corpus measurement is reproducible with the required bounded worker count:

```sh
/Users/<user>/dev/mission/.venv-ci/bin/python -m pytest -q -n 4 --dist loadfile \
  -rP skills/mission/tests/test_measure_k2_tmp.py::test_measure
```

This is a lower-bound observation, not a license to derive small production
limits from one run. The schema limits below align with already-enforced
repository limits and keep every collection bounded.

### 3.2 Fixed guidance limits

| constant | exact value | derivation |
| --- | ---: | --- |
| whole v5 generation | 4,194,304 bytes | K1 `STATE_LIMIT`; guidance shares the same generation document |
| review tier signals | 128 | more than 4x the production producer universe of 31 signal types |
| provider selections | 1,024 | observed maximum 1; preserve the existing provider public-contract ceiling |
| provider invocations | 10,000 | observed maximum 1; preserve the existing provider public-contract ceiling |
| provider plan import IDs | 10,000 | observed maximum 1; IDs are a validated subset of provider invocations |
| token / signal | 1-128 characters | K1/U1 identifier and provider public-contract token limits |
| portable text / reason / source | 0-2,048 characters | existing provider public-contract safe-text bound |
| digest | `sha256:` plus 64 lowercase hex | K1/U1 digest contract |
| iteration | exact integer 0..1,000,000 | existing specialist invocation CLI bound |
| timestamp | UTC seconds `YYYY-MM-DDTHH:MM:SSZ` | K1 v5 canonical time contract |

The 4 MiB limit is checked before JSON authority. Collection and scalar limits
are checked after duplicate-key/UTF-8/finite-number validation. `bool` is never
accepted as an integer.

## 4. Provenance and paired codec

### 4.1 Exact in-memory records

```python
@dataclass(frozen=True)
class SnapshotProvenance:
    schema_origin: SchemaOrigin
    session_id: Optional[str]
    document_digest: str
    generation: Optional[int]
    commit_digest: Optional[str]

@dataclass(frozen=True)
class Snapshot:
    state: MissionState
    guidance: GuidanceFacts
    provenance: SnapshotProvenance
```

Rules:

- `document_digest` is SHA-256 of the exact bytes passed to the paired decoder.
- missing/v1-v4 use `generation=None`, `commit_digest=None`.
- a raw v5 generation decode also remains unbound to repository lineage and
  uses both values as `None`; the future authoritative R1/U2 reader must replace
  them with the verified head generation and commit digest before routing.
- when present, `generation` is a non-negative exact integer and
  `commit_digest` is a strict digest. They are both present or both absent.
- `Snapshot.__post_init__` requires
  `state.snapshot_provenance == guidance.provenance == provenance`, matching
  session identity, and one non-null opaque binding object shared by both pair
  members. Two independent decodes of byte-identical input therefore cannot be
  recombined.
- `MissionState.snapshot_provenance` is the only K1 model addition. It defaults
  to `None`, is ignored by v4 projection and v5 encoding, and is populated only
  by the paired decoder or the future authoritative reader.
- every accepted `decide` result is unbound: its `new_state` has
  `snapshot_provenance=None` and no opaque binding. A transition cannot retain
  the source document digest as if its changed state had been read from those
  bytes.
- `GuidanceFacts` has no public constructor. Only the paired decoder's private
  factory can create it, and only the paired decoder attaches the opaque
  binding.
- `derive_next` rejects `None`, unequal values, unequal opaque identities,
  session mismatch, and invalid
  v5 lineage with stable code `snapshot-provenance-mismatch` and no recipe.
- `decide` does not read provenance or `GuidanceFacts`; repository CAS/fence
  remains the write authority.

### 4.2 Exact codec API

```python
def decode_mission_state(source: bytes) -> MissionState: ...  # compatibility, unbound
def decode_snapshot(source: bytes) -> Snapshot: ...           # one parse, bound pair
def encode_v5_snapshot(snapshot: Snapshot) -> bytes: ...
```

`decode_snapshot` performs one `decode_json_object` call and dispatches the
same immutable parsed object to the state and guidance decoders. It does not
thaw `legacy_passthrough` to obtain guidance. `encode_v5_snapshot` requires a
v5 bound pair and writes state plus the closed guidance projection; provenance
itself is not written. K1's private `encode_v5_state` becomes a two-input helper
used by `encode_v5_snapshot`; a state alone can no longer create a complete v5
generation.

## 5. Exact v5 `guidance` schema

The v5 top-level exact key set becomes:

```text
schema_version, identity, control, plan, handoff,
reviews, findings, scores, lease, guidance, extensions
```

`guidance` has exact keys `schema`, `routing`, `planning`, `review`,
`advisories`, and `providers`. `schema` is exactly `mission-guidance/1`.

### 5.1 Nested field table

| path | exact type / enum | bound | missing/v1-v4 default |
| --- | --- | --- | --- |
| `routing.awaiting_user` | exact bool | n/a | `false` |
| `routing.complexity` | `Unknown|Simple|Standard|Complex|Critical` | token | `Unknown` |
| `routing.force_mission` | exact bool | n/a | `false` |
| `routing.issue_ref` | null or trimmed text | 2,048 | `null` |
| `planning.policy_version` | null or exact integer `1` | n/a | `null` |
| `planning.provider_required` | exact bool | n/a | `false` |
| `planning.strategy` | null or `core|provider-primary|provider-advisory` | token | `null` |
| `review.critic_has_new_scope` | null or exact bool | n/a | `null` |
| `review.tier` | `light|standard|full` | token | `standard` |
| `review.tier_source` | null or `auto|user` | token | `null` |
| `review.tier_signals` | array of unique non-empty strings | 128 items, 128 chars each | `[]` |
| `advisories.pregate` | null or `PregateProjection` | one object | `null` |
| `providers.primary_binding` | null or `PrimaryProviderBinding` | one object | `null` |
| `providers.selections` | array of `ProviderSelection` | 1,024 items | `[]` |
| `providers.invocations` | array of closed invocation variants | 10,000 items | `[]` |
| `providers.imported_invocation_ids` | unique sorted invocation IDs, each bound to a completed terminal planning invocation | 10,000 items | `[]` |

All six nested objects use exact key sets. Arrays must be JSON arrays on wire
and immutable tuples in memory. Duplicate identities/signals reject.
Imported IDs must match `inv_` identity syntax, selection IDs are unique when
present, and canonical timestamps must denote a real calendar instant rather
than merely matching a digit pattern. A present legacy collection/object with
the wrong type rejects; only absence selects its default.

### 5.2 Pregate and provider records

`PregateProjection` exact keys:

| key | type |
| --- | --- |
| `issue_ref` | trimmed non-empty text, max 2,048 |
| `subject_digest` | digest |
| `verdict` | `accepted|split-required|rejected` |
| `gate_id` | token |
| `evaluated_at` | canonical timestamp |

The legacy persisted `path` is not projected because guidance warning does not
use it and it is not authority. A present pregate projection must bind the same
normalized `routing.issue_ref`; otherwise decode rejects.

`PrimaryProviderBinding` exact keys are `provider_id`, `selection_id`, and
`planning_contract_digest`. IDs are tokens and the contract value is a digest.

`ProviderSelection` exact keys are `skill`, `provider_id`, `selection_id`,
`planning_mode`, `planning_contract_digest`, and `required`. `skill` is required
portable text of 1-128 characters (the existing selection-candidate bound),
the IDs are nullable tokens, and the contract value is a nullable digest;
`planning_mode` is null or `primary|advisory`; `required` is exact bool. A
non-null primary binding must match exactly one `planning_mode=primary`
selection.

`providers.imported_invocation_ids` is derived only from the keys of a valid
legacy `provider_plan_imports` object. Each ID must identify one projected
`planning` invocation with `status=completed` and `lifecycle_state=terminal`,
matching the existing `plan-import` gate. It carries identity, not untrusted
result payload or paths.

### 5.3 Invocation variants

Every invocation has common exact keys `variant`, `invocation_id`, `phase`,
`iteration`, `status`, `lifecycle_state`, `required`, `skill`, `provider_id`,
and `selection_id`.

| variant | allowed `status` | required relationship |
| --- | --- | --- |
| `selected` | `selected|started` | lifecycle is `selected` for selected, `invoked` for started |
| `reserved` | `reserved` | lifecycle is `reserved` |
| `running` | `running` | lifecycle is `running` |
| `terminal` | `rejected|failed-before-start|abandoned-unknown|completed|unvalidated-evidence|prepared|awaiting-input|inline-applied|skill-tool-applied|skipped|unavailable|failed` | lifecycle is `terminal` |

`invocation_id` is `inv_` plus 32 lowercase hex. `phase` is one of
`planning|execution|review|scoring|critic|synthesis`; `iteration` is bounded as
in Section 3. `required` is exact bool. `skill` is required portable text of
1-2,048 characters (the existing invocation-record bound). `provider_id` and
`selection_id` are nullable tokens, with non-null `selection_id` required to be
`sel_` plus 32 lowercase hex. Invocation IDs are unique. An imported invocation
ID that is syntactically valid but absent, non-planning, non-completed, or
non-terminal rejects the complete paired decode.

For legacy decode, absent `required` becomes `false`; absent nullable identity
becomes `null`. A present invocation must otherwise satisfy the complete
variant. Non-object entries, unknown status, status/lifecycle disagreement,
partial opaque IDs, and duplicates reject the paired decode. No raw evidence,
command, environment, process ID, host ID, path, reason, or provider output is
copied into `GuidanceFacts`.

## 6. Authority rules

`GuidanceFacts` may select and render a primary recipe, warning, reviewer count,
context mode, or typed external observation request. It may not:

- accept a command rejected by `MissionState` plus its typed command input;
- produce a reducer, event, effect, pass, score, finding, review, lease, plan,
  handoff, or terminal outcome;
- read `extensions`, `legacy_passthrough`, raw provider output, live pregate
  cache, clock, environment, process, or host state;
- convert missing/corrupt guidance to an empty projection.

The legacy paired decoder is field-explicit. For the 11 state-owned fields,
guidance rules read `MissionState` only. For the 13 query fields, rules read
`GuidanceFacts` only.

### 6.1 Exact guidance result records

`derive_next` returns a deeply immutable `GuidanceRecipe`:

```python
@dataclass(frozen=True)
class NormalizedGuidance:
    next_action: Optional[str]
    details: Optional[FrozenJsonObject]
    command_sequence: Optional[tuple[str, ...]]

@dataclass(frozen=True)
class GuidanceStep:
    kind: str       # local-command | report | legacy-command | external-observation
    owner: str      # K2 | A1 | A2 | A3 | A4 | A5 | legacy
    action: str
    required_observation: Optional[str]
    follow_up_command: Optional[str]

@dataclass(frozen=True)
class GuidanceRecipe:
    rule_id: str
    parity_status: str
    legacy_dependency_ids: tuple[str, ...]
    steps: tuple[GuidanceStep, ...]
    advisories: tuple[str, ...]
    normalized: Optional[NormalizedGuidance]
```

`NormalizedGuidance` contains only the legacy parity fields `next_action`,
`details`, and `command_sequence`; renderer-only prose is intentionally not
authority. Nested details use `FrozenJsonObject`, and sequences are tuples, so
the shadow result cannot be mutated after selection. Each transitive dependency
has its own owner-bearing step naming both the observation required and the
follow-up command. Non-accepted pregate verdicts appear in immutable
`advisories`; they never become a command gate. Simple goal routing has no normalized guessed
result and instead returns the two explicit `legacy.goal-dispatch` and
`legacy.host-observation` steps.

## 7. Command union and parity boundary

### 7.1 K2-owned command subset

K2 defines these closed command variants because their command guards and pure
state reductions are fully expressible from K1 state:

| command | K2 guard/reducer boundary | later adapter owner |
| --- | --- | --- |
| `AdvancePhase(target, prepared_handoff=None)` | only `planning->executing` and `executing->reviewing`; terminal targets reject; entering execution requires exactly one `PreparedHandoff` whose plan equals current typed plan | A4 produces the verified handoff payload; A1 supplies activity/artifact observations and persistence |
| `MarkHalt(category, reason)` | active nonterminal to typed halted control/outcome; no goal-dispatch rendering | A1 supplies audit/time and persistence |
| `Reactivate(expected_category, reason, approved_by_user, target)` | only non-stale halted, not passed, explicit approval and exact category | A1 supplies audit/time and persistence |
| `ResumeStale(target)` | only stale/legacy-stale halted and explicit valid target | A1 supplies PID/clock/aggregate handling |

The K2 union has no generic property command and no opaque mapping command.
Progress/activity/property maintenance remains non-primary and outside this
subset.

### 7.2 Reserved A1-A5 ownership

K2 does not invent placeholder command acceptance for these cases:

| future owner | authority left to that Issue |
| --- | --- |
| A1 | init, activity, artifact-applicability handoff, refresh PID, index updates, full v4 adapter routing |
| A2 | critic-scope record, review import/finalize, score materialization, findings/artifact/specialist pass gates, forced pass |
| A3 | artifact/progress/context effect descriptors and publication |
| A4 | provider selection/invocation/import/promotion, canonical plan/handoff lineage, external saga |
| A5 | Stop/permission/host observations and their freshness/source contract |

Guidance steps for these cases are structured as `external-observation` or
`legacy-command` with an explicit owner and follow-up command name. They are not
reported as an immediately accepted local `Command` until the named owner adds
the typed observation/payload.

All command and primary-guidance definitions are built by one
`build_transition_table` call into `TRANSITION_TABLE`. There is no separate
guidance table or second phase list. Each primary-guidance row is one named rule
object carrying both `command_guard(state, command)` and
`guidance_guard(state, guidance)`. A K2-owned command rule carries its reducer
on that same object. A follow-up reserved to A1-A5 instead uses a typed
non-executable deferred command whose guard identifies the named rule;
`decide` rejects it as requiring external command authority, while the rule's
continuation metadata names the required observation and future follow-up
command. The deferred type is not added to the K2 `Command` union and does not
claim placeholder acceptance. Duplicate rule IDs, duplicate reducer ownership,
missing command/guidance guards, incomplete guidance metadata, and equal
primary ranks fail table construction.

### 7.3 Parity result

Parity compares normalized legacy `_derive_next_action` output directly with
the new table result for the same production CLI bytes. Expected output is:

| result | meaning |
| --- | --- |
| `exact` | normalized recipe equals the actual legacy function output and every K2-local step is accepted by `decide` |
| `legacy-required` | a named transitive dependency still reads authority outside the exact K2 contract; legacy remains authoritative |

Required `legacy-required` cases include:

- Simple goal routing that reads persisted `goal_dispatch_*` or live host;
- provider lifecycle branches that require A4 selection/import/dispatch
  authority not present in the state aggregate;
- `record-critic-scope`, review aggregation, and `mark-passes` until A2
  materializes their command authority;
- optional-specialist closeout display until its A2/A4 ownership is complete.

Within those cases, normalized recipe selection preserves three legacy
semantics: a `provider-primary` strategy without a valid primary binding falls
back to core planning; score lookup treats control iteration `0` as iteration
`1` without changing K1's accepted stored value; and optional-specialist
closeout uses `TERMINAL_SPECIALIST_INVOCATION_STATUSES`, where `rejected`
remains unclosed.

A transitive case is therefore expected to return `legacy-required` with the
exact dependency IDs, never `exact` and never a guessed recipe. An inventory
test fails if a legacy direct/transitive read is neither projected nor listed
as a named legacy dependency. Budget override remains outside this result
space, while its existing integration tests stay green.

The non-K2 parity inputs are a closed named inventory:

| dependency ID | boundary | authority inputs |
| --- | --- | --- |
| `application.clock-budget-override` | outside parity result space | `budget_minutes`, `started_at`, `iso_now()` |
| `legacy.goal-dispatch` | `legacy-required` | `goal_dispatch_requested`, `goal_dispatch_source`, `goal_dispatch_resolution_fallback_reason` |
| `legacy.host-observation` | `legacy-required` | live `detect_host()` result |

The CLI corpus fixes the expected rule ID, parity status, and dependency tuple
for each of its 36 named cases. This includes separate Simple inline-routing
and host-observation cases. Adding an unclassified case, removing a case, or
changing every case to `legacy-required` therefore fails before recipe parity
is compared.

## 8. TDD gates

The first gate is the paired contract:

1. actual CLI bytes decode to one `Snapshot` whose three provenance values are
   identical;
2. recombining state/guidance from different bytes rejects before guidance;
3. standalone state decode is unbound and cannot call `derive_next`;
4. v5 guidance requires every nested key and rejects unknown keys, variants,
   over-limit values, duplicates, malformed legacy known fields, and missing
   guidance;
5. v5 paired canonical round-trip is byte-stable and provenance is not wire
   encoded.

The transition/parity gate then requires:

1. every `exact` case compares with the actual `_derive_next_action` return,
   not a handwritten expected dictionary;
2. every state comes from `mission_state_fixture_corpus.py` production CLI
   output or from an additional command sequence in that generator;
3. K2-local commands execute through `decide`, produce immutable deterministic
   transitions whose resulting state is unbound from the source snapshot, and
   rejection has no state/events/effects;
4. duplicate command rules, duplicate primary matches, equal-rank ties,
   missing primary guidance, forged provenance, terminal ordinary mutation,
   and stale/manual reactivation confusion fail tests that would fail before
   the fix;
5. transitive dependencies have exact `legacy-required` results and therefore
   cannot accidentally authorize a production switch.
6. a bounded deterministic traversal covers the K2-owned planning, executing,
   reviewing, halted, and rejection equivalence classes; A2/A4 evidence graph
   expansion remains `legacy-required` until those command contracts exist.

## 9. Non-scope and release boundary

- no production `next`, mutation, repository, or new-session v5 route switch;
- no clock-dependent budget equivalence class;
- no new goal-dispatch or host-observation authority;
- no public replacement for U2 `_stage_persistence` before a complete typed
  `Transition` is available;
- no weakening of lease, CAS, evidence, provider isolation, pass, terminal,
  or strict-reader contracts;
- no v4 physical rewrite and no automatic v4-to-v5 migration.
