# Issue 508: A3 artifact / progress / context evidence extraction

## Outcome

Extract artifact, progress, and context evidence decisions into an application
use case while keeping filesystem publication in `LegacyV4Repository`. The CLI
remains a parse/render adapter, the kernel remains the only mission authority,
and the v4 wire bytes and lease-first rollback behavior stay compatible.

## Scope boundary

| Area | A3 responsibility | Explicitly excluded |
| --- | --- | --- |
| Artifact | Validate commands and produce inert state/effect requests for init, append, render, export, and consent-only publish | Remote publication, pass decisions, or schema-v5 activation |
| Progress | Produce update and clear decisions plus content-addressed checkpoint evidence | Guidance, score, review, provider, or completion authority |
| Context | Produce bounded manifest evidence from captured observations | State mutation or filesystem access inside the use case |
| Persistence | Execute validated byte effects through the current lease-first v4 transaction with rollback | Generation CAS or public UnitOfWork activation |

## Delivery plan

1. Add failing application and adapter tests proving that no A3 use case writes
   files directly and that rejected transitions or leases publish no bytes.
2. Add failing adversarial tests for mutated effect payloads, digest mismatch,
   symlink, FIFO, hard-link, identity swap, and multi-output failure rollback.
3. Introduce closed A3 request/result/effect types. Inputs are captured values;
   outputs are immutable bytes and state mutations with no persistence calls.
4. Extend `LegacyV4Repository` with a lease-first effect transaction that
   validates every request before publishing any file and rolls all outputs
   back when state publication or any later effect fails.
5. Route artifact init/append/render/export/publish, progress update/clear, and
   context manifest through A3 while preserving existing JSON, markdown,
   digest, artifact-gate, consent-recording, and no-op behavior.
6. Mirror every production module and run focused rollback/security suites,
   D1 distribution and Python compatibility gates, then the full suite.

## Risks and controls

- Publication before lease validation could leak bytes from a rejected command.
  The adapter test injects a foreign lease and asserts that the publication
  callback is never entered.
- Mutable or locator-bearing effect inputs could redirect output after
  validation. Effects therefore capture immutable bytes and closed relative
  identities; the repository revalidates digest, size, and target identity.
- Splitting state and files could weaken rollback. The v4 adapter keeps them in
  one existing `_PublishedFilesTransaction` and restores every output if the
  state write or a later output fails.
- Observation commands could accidentally gain authority. A3 results expose no
  score, review, pass, phase, completion, or provider decision fields.

## Verification

- Demonstrate the new tests fail before implementation and pass afterward.
- Preserve all #475 foreign-lease and exception rollback assertions.
- Compare existing artifact/progress/context outputs and state bindings.
- Verify source/plugin mirrors byte-for-byte and recursive module discovery.
- Freeze exact head, obtain independent counterexample reviews, wait for CI,
  and merge only while base/head remain unchanged.
