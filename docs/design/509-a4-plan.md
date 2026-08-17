# Issue 509: A4 plan / handoff / provider evidence extraction

## Outcome

ADR-005 and the typed-kernel migration plan define A4 as the application owner
for plan, executor handoff, and provider-evidence use cases.  This change moves
those decisions out of the CLI without moving authority to a provider or to an
adapter.  The kernel remains the only transition authority and the repository
remains the only persistence authority.

## Scope boundary

| Area | A4 responsibility | Explicitly excluded |
| --- | --- | --- |
| Plan | Validate a closed document and bind source, selection, invocation, iteration, generation, and digest | Provider-authored state mutation |
| Handoff | Prepare and verify a closed handoff against the current plan before phase advance | CLI-local authority or unverified plan reuse |
| Provider evidence | Record selection, dispatch intent, process receipt, result evidence, and reconciliation observations | Provider score, review, completion, or redispatch decisions |
| Persistence | Return typed transition/effect requests through ports | Importing a concrete repository from provider adapters |

P1 public UnitOfWork activation, schema-v5 rollout, review/scoring extraction,
and release work are outside this issue.

## Delivery plan

1. Add failing tests for closed plan and handoff bindings, plan drift, stale
   generation/iteration, malformed values, file bounds, and rollback on lease or
   publish faults.
2. Add failing saga tests for crashes before spawn, after dispatch intent, after
   spawn but before receipt, and after receipt; reconciliation must never infer a
   successful dispatch from an absent receipt.
3. Add immutable A4 request/result objects and application use cases that consume
   typed snapshots and ports.  Every returned decision is revalidated before it
   can become an effect.
4. Make the CLI a thin parse/render/side-effect adapter.  Before spawning it must
   commit `dispatch-unknown`; only a verified receipt may advance the invocation
   to running.  Receipt-less reconciliation may only become
   `abandoned-unknown`, never automatic redispatch.
5. Route plan import, provider-plan promotion, executor handoff, and provider
   reconciliation through A4 while preserving the v4 wire format.
6. Mirror source and plugin trees byte-for-byte and run focused, D1 parity,
   distribution, Python 3.9, and full-suite gates.

## Required invariants

- Plan and handoff objects reject unknown keys and non-canonical bindings.
- Generation, digest, source, selection, invocation, and iteration are verified
  against authoritative inputs, not against values returned by a provider.
- A zero exit code with malformed or unbound provider output remains
  unvalidated evidence.
- Lease failure, stale fencing epoch, replay, and publish failure leave no
  authoritative partial result.
- The provider can supply evidence only; it cannot decide review, score, pass,
  completion, phase, or persistence.
- No provider adapter imports a concrete kernel repository implementation.

## Verification

- Each fail-closed branch has an independent counterexample test.
- Existing plan, lifecycle, handoff, provider-isolation, and provider-preflight
  suites remain green.
- Source/plugin mirror parity and D1 recursive discovery remain green.
- Two independent Checkers review the frozen exact head and search for
  counterexamples across one hop outside the diff.
