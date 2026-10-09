# Public benchmark bundle adapter

`public_benchmark.py` provides a data-only entry separate from H's generated
catalog. It uses G's regular-file tree digest and positive allowlist export,
and H's bounded process supervisor and result vocabulary. It is a standalone
benchmark tool, not a Mission session command.

## Bundle contract

Supply an **uncompressed tar** and the externally preregistered digest to
`load_bundle(path, expected_digest)`. `bundle_digest(path)` computes that digest
without parsing the manifest. Normalization is USTAR: sorted regular-file
paths, zero uid/gid/mtime, empty owner names, mode 0644 or 0755 according to the
executable bit. Directory metadata is excluded. Links, special entries,
duplicate/escaping paths and `.git` components are rejected. File content is
limited to 512 MiB per bundle. Long paths unsupported by USTAR are rejected.
The verified bytes are retained in memory; later source-tar changes cannot
replace the inputs. Only after digest matching is `manifest.json` parsed.

Every object has exactly the following keys; unknown and missing keys fail.
Duplicate JSON keys, task IDs, benchmark names and check names also fail.
Multiple tasks may have the same unit ID; selection of independent units is a
separate prerequisite, not an acceptance claim made by this adapter.

| Object | Keys / constraints |
|---|---|
| Manifest | `schema` = `mission-public-bundle/1`, nonempty `benchmarks`, nonempty `tasks` |
| Benchmark | `name`, immutable `revision` identifier, `license` |
| Task | `task_id`, `unit_id`, `benchmark` (declared name), `upstream`, `base_commit` (full SHA), `environment`, `requirement`, `checks`, `license`, `criteria`, `starter_digest`, `starter_root`, `evaluator_root` |
| Environment | `image` (name pinned with `@sha256:`), nonempty argv `command` |
| Check | `name`, `kind` = `fail_to_pass` or `pass_to_pass` |
| Criteria | `Lic`, `Con`, `Cx`, `Det`; each contains `accepted` (boolean), `reason` (null on acceptance, nonempty on rejection), `evidence_digest` |

`starter_digest` and evidence digests use `sha256:` plus 64 lowercase hex
characters. The starter digest is G's content-tree digest, excluding `.git`.
Starter and evaluator roots must be present, disjoint and non-nested. All files
other than the manifest belong to one of those roots. The evaluator root must
contain `test.patch`; reference files and a benchmark-specific result adapter
can also be stored there. No bundle code is imported into the harness.
The adapter does not verify dataset licensing, source-to-base-commit provenance,
criteria evidence, pool completeness or lineage deduplication itself; those
remain inputs established by cohort selection and verification.

## Assignment and evaluation

1. Call `prepare_assignment(bundle, task_id, destination)` before the worker
   starts. It writes only the accepted task's starter into a temporary repo,
   verifies its content digest, and passes every top-level path (including
   dotfiles) to G's export allowlist. Literal snapshot initialization retains
   ignored files and disables content/archive transformations from attributes.
   Export digest mismatch fails. Keep the returned initial worker digest as
   harness-owned evidence; do not recompute it from the worker's final tree.
2. After worker termination, call `freeze_candidate(bundle, assignment,
   candidate)`. The envelope binds task, unit, benchmark, base commit, bundle,
   starter and candidate digests. The caller owns this freeze and its storage.
3. Call `evaluate_assignment(bundle, assignment, initial_worker_export,
   candidate, envelope, timeout_seconds=...)`. It checks provenance before
   launching any environment, creates a fresh copy without `.git`, retains
   executable modes, and checks both source and copy digests after evaluation.

The Docker path uses a **locally available image only** (`--pull=never`), with
network disabled, a read-only root filesystem/input mounts, dropped
capabilities and no new privileges. The image must contain `/bin/sh`, `cp`,
`git` and the declared command. It copies the candidate to disposable `/work`,
applies `/evaluator/test.patch` there, then executes the evaluator argv. No
reference/tests enter the worker export. Per-container limits are 1 CPU, 2 GiB
memory, 256 processes, 1 GiB `/work` and 128 MiB `/tmp`. The image/result adapter
must have been prepared and verified offline before a benchmark run.

The command emits only a JSON array of `{ "name": "check-id", "passed": true }`
records on stdout; diagnostics belong on stderr. Only exact check identities,
exact count, strict booleans, stable candidate digests and all passes yield
`status=passed`. Other H statuses are `failed` and `blocked`; timeout is blocked.
Well-formed observations are retained even when their inventory mismatches.
Container terminal state is inspected to distinguish startup/engine failure
from a candidate command exit, as required by the [Docker CLI start
implementation](https://github.com/docker/cli/blob/f415da838bed6a0e12ca6cb86ba198fdac6beb9c/cli/command/container/start.go#L150-L187).
Environment startup/engine failure uses `evaluator_process_unavailable`, the
infrastructure reason used by H. Only this reason permits one reevaluation of
the same frozen candidate in a fresh environment. `evaluations` retains both
attempts; worker execution is never repeated. Forced container removal is
attempted after every launch, including timeout and exceptions.

Container integration, public dataset adapters and paid runs require separate
validation/authorization. Importing the module or loading a bundle starts no
container, model, download or network request.
