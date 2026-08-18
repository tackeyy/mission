# Issue 528 implementation record

## Scope

- Replace the unbounded resolved-transaction lookup with a deterministic operation index while retaining legacy markers.
- Align the open-prepare rejection API and U3 error classification.
- Cover important post-head projection crash points with real process termination.

## Evidence and decisions

- The U4 collector traverses generations, quarantine entries, current heads, commit facts, and prepared recovery roots. It does not collect resolved transaction markers.
- Resolved markers therefore grow without a GC bound. A deterministic digest index is required; a bounded-scan-only test would not reflect the current implementation.
- Durable prepares are repository-wide exclusive state. The existing session parameter does not narrow the invariant and should be removed.
- Post-head crash coverage will prioritize operation publication, lineage verification, and resolution publication with a real projection. Finalize occurs after projection cleanup and is already covered by the no-projection replay matrix.

## Verification log

- Baseline: `64 passed` for the Issue 504 and Issue 505 test modules.
- Red: the deterministic-index tests failed because the operation-index path and directory did not exist; the open-prepare contract test failed because the old method required an unused session argument. The initial post-head matrix also exposed and corrected a test-fixture mistake that counted the prior initialization marker.
- Green: `6 passed` for the new index, legacy migration, error contract, and post-head projection cases.
- U2/U3/U4 regression set: `164 passed`.
- Mirror and hygiene gates: canonical/plugin `cmp` succeeded; plugin sync, artifact hygiene, and neutral-vocabulary tests reported `42 passed`.
- Independent Checker: `ACCEPTED`, with no blocking findings.
- Required full suite: `4041 passed in 365.96s`, failed 0.

## Deferred fault combinations

- `after-finalize` with a projection was not duplicated because projection cleanup and prepare removal have already completed at that point; the existing same-operation replay matrix covers the fault itself.
- Multiple projections at each post-head point were not multiplied across the new matrix. Existing tests cover interruption after the first of two projection publications, while the added matrix covers exact-inode roll-forward for one projection at the operation, lineage, and resolution boundaries.
