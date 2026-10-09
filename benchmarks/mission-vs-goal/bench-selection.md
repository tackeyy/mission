# Offline pool selection and preregistration verification

`bench_selection.py` implements the I2d boundary in
[the evaluation preregistration](../../docs/design/884-evaluation-aggregation.md),
§5.0, §5.1 and §3.2.1 checks 1–3. It does not start worker runs.

## Pure inputs and outputs

`generate_pool(snapshot, config, scope, observations, commit_a=sha)` returns
canonical JSON bytes. Every snapshot task has a row, including exclusions with
the first failed criterion in Lic → Con → Cx → Det order. Rows contain source
identifiers, input/evidence digests, criterion values, and recorded evaluations;
they do not contain task descriptions, source code, or package contents.

The snapshot is a list of data-only task definitions. Each task supplies
`task_id`, `benchmark`, immutable `revision`, repository URL, `base_commit`,
complete reachable `roots`, and `base_files` as path/text pairs. It also supplies
benchmark/upstream license identifiers, `natural_request`, immutable evaluation
image/command, named checks with `fail_to_pass`/`pass_to_pass` kinds, and reference
changes with path/added/deleted lines. Added lines must preserve hunk separation;
do not concatenate disconnected hunks into a fictitious continuous sequence.
The normalization is canonical `tasks.json` in a sorted, fixed-mode USTAR
archive. Source acquisition must bind these data to the declared upstream base
commit and distribution revision.

`config` provides confirmed license rights (`copy`, `execute`, optionally
`store`), `store_contents`, owner-approved integer thresholds `cx_files`,
`cx_checks`, `cx_lines`, `con_length`, `con_lines`, `g3_percent`, the snapshot and
scope digests, and pinned `generator_sha`. `scope` contains all tracked repository
texts at A and all package texts, grouped as `mission` and `packages`. Corpus
completeness and license/natural-request provenance belong to the acquisition
provider; supplied booleans alone do not establish their external truth.

`observations[task_id]` has three I2c result dictionaries for each of `starter`
and `reference`. `collect_det` captures these through an injected replay provider
only after the early filters pass. `BundleReplay` calls I2c's `freeze_candidate`
and `evaluate_assignment`; reference jobs supply a reference-applied candidate.
Calling it with real jobs can run containers and requires execution approval.

`select(seed, manifest, arms)` produces C: 12 pilot units, remaining unit ranking,
one primary task per unit, and two pilot repetitions per arm in seeded order.
`confirm(seed, C, commit_c, K, arms)` produces canonical D content with the first
K ranked units, ceil(K/2) controls, deterministic IDs and execution order.
Canonicalization supports strings, safe integers, booleans, null, lists and
string-keyed dictionaries; it rejects floats and uses RFC 8785 UTF-16 key order.
Task/unit/assignment ordering ties use UTF-8 byte order as preregistered.

## Injected acquisition and proof adapters

`acquire_history(provider)` drains `merged_pr_page(path, cursor)` until `next`
is null, checking stable totals and detecting repeated cursors. `main_history`
returns all path-touching main commits, reachable merge SHAs, current file bytes
and force-push/deletion protection. `main_head` brackets acquisition. These
methods perform transport only in the caller's provider; tests use local data.

History PRs supply `sha`, main `base`, integer `merged_at`, file bytes, and
per-file proofs. A adds `attempt.json` and `preregistration.md` in the same merge;
the document binds A's content digest. B/C/D/withdrawal use the preregistered
filenames. History must account for all these merged files and preserve bytes.
Times are integer Unix seconds, with strict pre-cutoff/pre-run comparisons.

`verify_timestamp(content_digest, proof)` must cryptographically verify the
attestation and bind its digest to a verified block header; it returns the block
time. Unavailable/invalid attestations cannot satisfy V2 or V5.
`verify_beacon(chain, round, beacon)` must verify the signature for the pinned
chain/key/round and return 32 randomness bytes. These bytes must match the
reported randomness. There is no fallback seed or boolean verification flag.
Production transport, beacon-specific cryptography, and timestamp-chain
verification are injected, not bundled here. Offline fixtures exercise actual
RSA/SHA-256 signatures; they are not production beacon or timestamp proofs.

## Verification evidence

`verify_cohort(history, materials, used_number, beacon, records, proofs, replay)`
regenerates B, determines the earliest seed-independent V1–V5 candidate, then
checks that C/D, packages, timing and Det replays match. Later candidates are
never promoted after a post-seed failure. Withdrawn/invalid pools cannot be
reused by changing only their snapshot serialization or reference metadata.

Each attempt's trusted `materials` contains its snapshot, scope, loaded
`generator_sha`, preregistered `lineage_digest`, and pilot-derived `minimum_k`.
Load this module from A's pinned generator revision; passing a matching SHA
string does not verify source provenance. The generator provider owns that
binding. Records use planned arm names, selected task/unit IDs, package SHA and
digest, and integer start times; adapters normalize host-specific records first.

The result contains `status`, `canonical_attempt`, checks `1`–`3`, lineage and
evidence digests, replayed task IDs and failure reason. Only `status=valid` with
all three checks true is selection evidence. I1 must still establish run
separation and execution order (checks 4–5), complete assignment accounting and
statistical validity. K computation, execution approvals and actual acquisition
are caller responsibilities; this module does not change §9's frozen choices.
