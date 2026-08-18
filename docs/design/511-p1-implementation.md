Issue #511 binds the already implemented U1-U3 persistence protocol to the
typed application boundary fixed by ADR-005. It does not enable v5 creation in
the production CLI.

## Verified starting point

- `LocalFencedRepository` already owns lease admission, immutable generation
  publication, fenced head CAS, deterministic recovery, operation replay, and
  generation collection.
- Its only staging entry point is `_stage_persistence(admitted, state_bytes,
  effects)`. That is the private U2 seam called out by the owner comment on
  Issue #511; bytes supplied there have not been proven to come from a sealed
  kernel `Transition`.
- The closed K2 commands can use the common `MissionRepository` port. A1-A5
  compatibility mutations that have no closed K2 command still use the
  narrower legacy `load`, callback `execute`, and `save` capability. Repository
  construction is repeated at CLI call sites and is always legacy-specific.
- No production command creates a v5 head. Existing v5 tests build isolated
  repositories directly.

## Implementation boundary

1. Add the ADR-005 public `stage(admitted, transition, blobs)` method. It
   accepts only a sealed kernel `Transition`, binds its effects to the exact
   immutable blob set in the admitted `ExecutionRequest`, requires the target
   lease already decided by `begin`, independently re-derives the canonical
   decision output from the admitted state and typed command, and performs
   canonical state encoding internally before entering the existing private
   persistence machinery.
2. Add one typed command execution boundary shared by compatibility v4 and v5.
   The request carries session, lease owner/token, immutable command and blobs,
   operation identity, intent digest, and audit categories. A pure decision is
   evaluated after provisional lease admission; rejection performs no save or
   publication. The common `MissionRepository` surface is only typed
   `read/execute`; the v4 `transaction/load/save` compatibility capability is
   a narrower sub-port and the v5 repository does not advertise it.
3. Add a format-pinned selector. It strictly reads the loaded session once,
   accepts only missing/v1-v4 state documents or `mission-head/1`, constructs
   exactly one matching repository, and rejects format/identity drift,
   including removal of a session identity observed on the first selection.
   Versioned compatibility documents must still carry a bounded legacy
   identity/control envelope before a factory can be constructed. It has no
   environment-variable writer switch and never dual-writes.
4. Route the A1-A5 CLI repository factories through the selector's legacy
   assertion now. This preserves exact v4 bytes while preventing an existing
   v5 head from accidentally reaching a legacy writer. This is a runtime
   format guard, not application-use-case integration with the common port.
5. Add retained-selector runtime tests for the legacy factories and cover the
   new modules with recursive distribution, Python 3.9 parsing, mirror, and
   artifact-hygiene gates.

Application connection for all five closed K2 commands, the complete v4/v5
matrix, route ownership inventory, and every remaining compatibility mutation
(including activity, generic set, and permission timing/activity projection)
are mandatory C1 work in #513 before production v5 creation.

## Red to Green sequence

1. Red: public v5 staging is unavailable and bytes can bypass a typed
   transition.
2. Green: sealed transition staging, effect/blob equality, lease binding,
   canonical encoding, commit, replay, and rejection non-mutation.
3. Red: there is no strict format-pinned selector and a v5 head can be handed
   to a legacy repository factory.
4. Green: strict selection, single construction, format drift rejection, and
   neutral portable errors.
5. Red: the two repositories do not satisfy one request/decision/result suite.
6. Green: common conformance tests plus existing U3 crash/recovery and GC
   suites; preserve every existing v4 behavior test unchanged.

## Acceptance and stop conditions

- A rejected domain decision changes neither authoritative state nor effects.
- A stale head, generation, lease, fence, open recovery record, or mismatched
  blob binding remains fail-closed in the existing U3 protocol.
- A loaded v4 session stays v4 byte-for-byte; no command upgrades it and no
  current init path emits v5.
- A loaded v5 head is never sent to a legacy writer.
- Stop rather than invent a new state schema, guidance derivation rule, v5
  production initialization route, environment flag, dual-write path, or
  non-cooperative writer threat model.
