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

## Executed-count remediation

Each shard now writes JUnit to a temporary directory and emits one receipt from
`write_suite_report.py`. The receipt carries the JUnit-derived `executed`
count; a count of zero makes the shard command fail, even though pytest itself
exits zero for an all-skipped selection. The verifier also treats missing, zero,
negative, and boolean counts as a local-suite fallback, including historical
logs that lack the new field.

The focused Makefile check runs a one-test all-skipped temporary suite and
observes a nonzero shard exit. A separate two-file, two-shard run observes one
positive count per receipt and keeps the original target expression in each
manifest. It uses a one-worker wrapper only to bound local xdist load; pytest
and JUnit generation remain real. These tests detect a regression where CI
would reuse a green exit that executed nothing; existing receipt tests retain
the merge-tree, job, and command binding checks.

An independent boundary exploration supplied 84 inputs (JUnit 60 and receipt
24) to the new count requirement and found 0 unexpected acceptances.

The revised count, local-gate contract, integration gate, workflow, scope,
shard, inventory and mirror checks pass together: 675 tests in 43.69 seconds.
The two git-index integration checks require a writable index; their earlier
sandbox-only failures are not counted as passes.
