# Issue #398 implementation

`planning_policy_version: 1` introduces a bounded planning lifecycle.  Its
pure derivation returns one safe action at a time; an absent version remains
`legacy-core` and never starts a provider flow by itself.

## Delivered contract

- Registry planning declarations accept `planning.mode: advisory|primary` and
  selected providers project a bound planning strategy and contract digest.
- Provider candidates are promoted only from the validated plan-import record.
  The canonical identity carries source, source ID, source digest, selection,
  iteration, and generation.
- `advance --phase executing` rejects a policy-v1 session without a canonical,
  source-bound plan.  The same state transaction prepares the executor
  handoff, derives its step IDs from canonical bytes, and transitions activity.
- `executor-handoff begin|verify-step|record-step|complete` revalidates bytes
  and lineage and records step lineage.  Mutation rejects before step work.
- Legacy migration is explicit (`planning reselect`), active-planning only, and
  drops unsafe raw specialist records without publishing them.

## Focused test list

- `test_planning_provider_lifecycle.py`: lifecycle mapping, one-action next,
  advance gate, lineage drift, mutation rejection, and legacy reselection.
- `test_planning_provider_eligibility.py`: registry planning-mode validation.
- `test_plan_import.py`: existing canonical provider-import contract remains
  the sole primary promotion input.
