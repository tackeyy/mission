# ADR-006: Adjudication of Kernel, Stop-Hook, UnitOfWork, and Adapter Divergences

## Status

Accepted

## Date

2026-08-22

## Relationship to earlier decisions

This ADR extends [ADR-005](./005-typed-mission-kernel-and-unit-of-work.md).
ADR-005 remains Accepted: its boundary model, UnitOfWork protocol, crash
recovery, and rejected alternatives are unchanged. This ADR records how four
divergences between ADR-005's stated principles and the shipped implementation
are adjudicated, and it commits the implementation to converge on the
principles in a fixed batch order instead of revising the principles downward.

## Context

An independent external refutation (run 2026-08-22 against `main 7e50255` by a
different-vendor model, recorded in the tracking issue for this ADR) measured
four divergences. None is a defect on its own; each is a gap between what
ADR-005 promises and what the code does. Left unrecorded, future reviews would
reason from promises the code does not keep.

1. **Stop hook is not display-only.** ADR-005 directs the stop guard to render
   a typed decision produced by the application layer. In the shipped code no
   `GuardDecision` type exists; the shell hook itself selects sessions,
   branches on stale/awaiting-user/lease conditions, and triggers state
   mutation (`mark-halt`, `cleanup-stale --execute`).
2. **`decide()` is a partial gate, not the reducer.** The kernel command union
   covers 5 lifecycle commands while the CLI owns roughly 65 mutating commands
   (per `command_owners.py`). Even for the covered 5, application code
   discards `transition.new_state` and re-implements the mutation as dict
   edits; the legacy repository applies a mutation callback and never applies
   the transition it is handed. The kernel also updates a process-global
   issued-transition registry, so `decide` is deterministic but not pure, and
   that seal duplicates the persistence layer's decide-replay comparison.
3. **Writers outside the committed protocol.** `resolve-archive` mutates
   archived state with a bare lock plus `atomic_write_json`, and the legacy
   save updates the aggregate index after the state write without rollback on
   index failure. ADR-005 allows separate administrative aggregates, but it
   requires each aggregate to have its own validation and commit protocol;
   these writers have none.
4. **The host adapter is not thin.** `mission-state.py` still holds about
   19.3k lines, 525 functions, and the largest business-logic spans
   (approximately: `_build_parser` 724, `cmd_invoke_command_provider` 487,
   `cmd_aggregate_reviews` 435 lines). ADR-005's direction is correct and the C1
   dependency inversion is complete, but "thin" has not been reached.

Cost premise for this adjudication: agent execution is subscription-priced, so
marginal labor cost is excluded from the trade-offs. The constraints that
remain are regression risk, calendar time (CI and review round-trips), and
collision with concurrent work.

## Decision

### 1. Stop hook: decide in Python, fire from shell, closed command list

The principle is amended, not abandoned. A typed `GuardDecision` is introduced
in the application layer and becomes the single authority for every judgment
the hook needs: session selection, staleness, lease expiry, orphan detection,
and which follow-up command (if any) is warranted. The shell hook is reduced
to (a) rendering the decision and (b) invoking exactly the CLI command the
decision names, chosen from a closed list whose canonical copy lives in the
tracking issue for this ADR (at adjudication time: `mark-halt`,
`cleanup-stale --execute`, `stop-guard-observe`). The
hook must not compute thresholds, compare timestamps, or choose between
commands on its own. A guard test pins that the hook script contains no
judgment logic beyond decision dispatch.

Rationale: a hook that never executes anything cannot do its job in the host's
hook mechanism, so "display only" was never implementable as written. What the
principle actually protects — one authority for guard policy — is preserved by
moving the judgment into typed Python and enumerating the shell's permitted
actions.

### 2. Kernel: converge on "every mutation goes through `decide()`", in batches

The owner ruled (2026-08-22) that the target state is the original principle:
all mutating commands pass through the kernel, and the kernel's
`transition.new_state` is what gets persisted. The implementation converges in
this order, one batch per tracking wave, each child issue one PR with one
independent review:

- **Batch 1 — completion-adjacent commands.** Extend the command union to the
  mutating commands that touch `terminal_outcome`, `phase`, `passes`, or
  `loop_active` and are not yet kernel commands. These sit closest to the pass
  gate, so kernel rejection has the highest value here.
- **Batch 2 — transitions become the write.** For commands already covered by
  `decide()`, replace the post-decision dict mutations with application of
  `transition.new_state`; the repository applies transitions instead of
  mutation callbacks. The process-global issued-transition registry is removed
  in this batch if the persistence-layer decide-replay comparison is confirmed
  to subsume it, keeping exactly one duplication-free enforcement point.
- **Batch 3 — remaining families.** Evidence commands, then
  planning/specialist commands, then administrative commands, using the same
  one-capability-per-child discipline as the ADR-005 migration.

Batch boundaries and the full command inventory live in the tracking issue,
not in this ADR, so the ADR does not go stale as commands are added.

### 3. UnitOfWork: no informal writers; administrative aggregates get a protocol

The exception category "administrative writer without a protocol" is rejected.
`resolve-archive` and any other separate-aggregate writer must go through a
commit protocol that provides at minimum: identity-checked read, validation,
atomic publish, and a defined failure outcome. The legacy save's aggregate
index update is made explicitly recoverable (ordered write with a recorded
recovery step) or moved into the same transaction. ADR-005's allowance for
separate aggregates stands; what changes is that "separate" no longer implies
"informal".

### 4. Adapter: extraction continues; "thin" is defined by an allowlist

The thin-adapter goal is retained, not rewritten to match the present state.
"Thin" is defined as: `mission-state.py` may contain argparse wiring,
conversion of parsed arguments into typed requests, invocation of application
use cases, and rendering of results — nothing else. Extraction proceeds from
the largest spans downward, one function per PR, with unchanged tests as the
acceptance bar for each step. Progress is tracked in the tracking issue; this
ADR records only the definition.

## Consequences

- Guard policy gains a single typed authority; divergence between shell and
  Python judgments (the class of defect fixed for freshness in the ADR-005
  wave) becomes structurally impossible rather than individually patched.
- The kernel's transition table becomes the actual reducer, so the pass gate
  and `derive_next` guidance are backed by the same object that writes state.
  Until a batch lands, the commands in that batch keep their current behavior;
  the batches are ordered so the highest-risk gap closes first.
- Two enforcement mechanisms (issued-transition seal and decide-replay
  comparison) collapse to one, removing a source of false confidence.
- Administrative aggregates keep their independence but lose the ability to
  write without a protocol, closing the pattern behind the lease-first
  findings in the ADR-005 wave.
- The migration accepts a long calendar tail for Batch 3 and the adapter
  extraction; the risk controls are batch isolation and per-PR independent
  review, not scope reduction.

## Rejected alternatives

### Revise ADR-005's principles down to the shipped implementation

Rejected for the kernel and adapter principles by owner decision: the
subscription cost premise removes the main argument for keeping the kernel a
partial gate, and a principle rewritten to match the code stops exerting
pressure on the code.

### Kernelize all 65+ mutating commands in one wave

Rejected: labor is cheap but regression risk and collision with concurrent
work are not. Batching preserves reviewability and keeps `main` releasable
between batches.

### Keep the process-global transition seal alongside decide-replay

Rejected pending Batch 2 verification: two overlapping enforcement points
invite the assumption that each covers the other's gaps. One verified
mechanism is stricter than two unverified halves.

### An "administrative writer" exemption list

Rejected: every exception list of this kind in this repository's history
(artifact publish, plan-import, aggregate-reviews) eventually produced a
lease-first violation. Protocols scale; exemptions accumulate.
