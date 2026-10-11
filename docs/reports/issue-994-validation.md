# Issue994: CI suite reuse validation

## Test list

- [x] An explicitly configured CI run whose merge commit, tree, jobs, commands, and receipts agree is accepted.
- [x] A receipt that claims the wrong shard is rejected and leaves the local suite as the fallback.
- [x] An absent or malformed opt-in is not usable.
- [x] Gate integration skips the local suite only after accepted evidence.
- [x] API or job/log inconsistency remains a local-suite fallback.

## Decision boundary

The existing base-side full-suite command remains authoritative. CI reuse is
optional and is accepted only for a completed, successful pull-request run on
the exact PR head, whose two-parent merge commit has the gate's integrated tree
and whose complete shard set records the configured command and receipt. All
other outcomes retain the local suite.

## Evidence

The initial targeted baseline had 64 passing tests and two pre-existing
environmental failures: the sandbox disallowed `git write-tree` in tests that
exercise the real suite reporter. The new evidence tests begin with the missing
module failure and now pass. The parser also accepted the supplied CI job
fixture after timestamp and terminal-control normalization, yielding its merge
checkout and shard receipt without retaining the log in this report.

The gate entrypoint is also covered with an injected Actions response and real
temporary Git history: a matching two-shard merge receipt skips the declared
local suite, while a merge-tree mismatch invokes that suite. These tests retain
the existing integrated-tree digest and cleanup path.

The repository-wide AST inventories classify `verify_ci_suite`'s `run`
references as decoded Actions metadata, rather than process creation, and
`_normalise_log`'s string replacement as in-memory normalization rather than a
state writer. Both classifications are pinned in their fixtures.

Review remediation rejects all GNU makefile discovery names and binds the
receipt command to the single Actions `Run make test-shard` launch header. The
workflow also treats incomplete or ambiguous changed-file enumeration as full
scope before selecting shard targets.

The workflow's extracted scope script is executed under Node with mocked
Actions inputs. A complete documentation-only list keeps the fast target;
incomplete, missing-count, duplicate, and API-error lists select the full
target.

The extracted-workflow regression fails against the previous workflow and
passes against the revised workflow. The combined gate, CI-scope, shard,
compatibility, inventory and wrapper checks pass: 767 tests in 40.49 seconds.
