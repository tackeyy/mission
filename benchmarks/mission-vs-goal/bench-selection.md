# Offline pool selection and preregistration verification

`bench_selection.py` implements selection and canonical attempts in I2d in [the evaluation preregistration](../../docs/design/884-evaluation-aggregation.md),
§5.0 and §5.1. `bench_cohort.py` depends on it for Det replay, C/D verification and
§3.2.1 checks 1–3. Selection never imports cohort; it can be shipped independently.
Inputs are injected. Selection starts no transport or worker; cohort replay can execute the I2c evaluator.

## Pool and assignments

`generate_pool(snapshot, config, scope, observations, commit_a=sha)` returns canonical JSON bytes.
Every snapshot task has a row with its first failure in Lic → Con → Cx → Det order, identifiers,
input/evidence digests, criterion values and evaluations. Descriptions/source/package contents are omitted.
The data-only snapshot supplies task/benchmark IDs, immutable revision, repository URL, base commit,
complete reachable roots, base path/text pairs, both licenses, natural-request provenance, immutable
image/command, named fail-to-pass/pass-to-pass checks, and reference path/added/deleted changes.
Preserve hunk separation. Snapshot normalization packs canonical `tasks.json` in fixed-mode USTAR;
acquisition must bind the data to the declared base commit and benchmark revision.

`config` supplies license rights (copy/execute, optionally store), `store_contents`, integer thresholds
`cx_files`, `cx_checks`, `cx_lines`, `con_length`, `con_lines`, `g3_percent`, snapshot/scope digests
and pinned `generator_sha`. `scope` groups all tracked texts at A and package texts as mission/packages.
Acquisition owns corpus completeness and license/natural-request provenance, beyond supplied booleans.
`observations[task_id]` contains three I2c results per starter/reference; missing early-filter observations
are rejected. Invalid Det JSON schema is excluded with its sorted JSON digest. `bench_cohort.collect_det` captures
observations after early filters. `bench_cohort.BundleReplay` uses I2c freeze/evaluate with a reference-applied tree;
real container jobs require execution approval.

`select(seed, manifest, arms)` produces C: 12 pilot units, remaining ranking, one primary per unit,
two repetitions per pilot arm, deterministic IDs and execution order. `confirm(seed, C, commit_c, K, arms)`
produces D: first K ranked units and ceil(K/2) controls. Canonical JSON allows strings, safe integers,
booleans, null, lists and string-keyed dictionaries, rejects floats/deep recursion, and orders keys by
UTF-16. Identifier/key ties use UTF-8 bytes. A declares unique §2 arm sets as `pilot_arms` and `confirmatory_arms`. Both require
`native_goal` and `mission_verified_complex`; only `mission_baseline` is optional.
C/D must match those declarations; pilot assignments number 24 times the declared arm count.

## Acquisition and evidence

`acquire_history(provider)` drains merged PR pages, checks stable totals/cursors and brackets acquisition
with `main_head`. `main_history` supplies all path-touching main commits, reachable SHAs, current bytes
and force-push/deletion protection. PRs supply SHA, main base, integer merge time, files and proofs.
A adds attempt.json and preregistration.md in the same merge; B/C/D/W use the frozen filenames.
All files must remain immutable. Integer Unix times use strict pre-cutoff/pre-run comparisons.
`verify_timestamp(digest, proof)` cryptographically binds the digest to a verified block header/time.
`verify_beacon(chain, round, beacon)` verifies the pinned chain/key/round signature and returns the
reported 32 randomness bytes. No fallback seed or boolean verification flag is accepted. Production
transport/cryptography is injected; offline fixtures use real RSA/SHA-256, not production proofs.

`bench_cohort.verify_cohort` accepts
`(history, materials, used_number, beacon, records, proofs, replay)`, regenerate B and pick
the earliest seed-independent V1–V5 candidate, then verifies C/D, package, timing and Det replays.
V4 failures permit later candidates; post-seed failures never do. Reusing identical invalid/withdrawn
pools is forbidden even with changed snapshot serialization/reference metadata. Trusted materials supply
snapshot, scope, loaded generator revision and pilot-derived `minimum_k`. Load this code from A's pinned
revision; a matching SHA string alone does not establish provenance. Records supply task/unit, arm,
package SHA/digest and integer start time. Check 2 recomputes the lineage record digest against B’s `lineage_digest`, bound before seed release.
Only status=valid with checks 1–3 true establishes selection evidence.
Checks 4–5 (actual run separation and record execution order), complete assignment accounting and
statistics belong to I1. K planning, approval and acquisition remain caller duties.

## Det collection and replay

`collect_det(snapshot, config, scope, number, replay)` calls
`replay(number, task_id, variant)` three times per starter/reference for every task passing Lic, Con and Cx.
Commit these per-case observations in B before the beacon is released. During verification, `all` replays
each such task once per variant, including Det rejections; early-filter rejections are skipped.
Every such task must have exactly three committed observations in a list for each variant.
Verification checks this throughout the Det pool, including tasks outside the audit sample;
any other count returns `invalid_cohort` with `det_observation_count_invalid`.
Only the named case booleans are compared, but case names/count, boolean types, status and reason must
form a valid I2c result. Unavailable evaluation and differing case outcomes invalidate the cohort.

Unexpected `Exception` subclasses raised by the replay callable return `invalid_cohort` with
`det_replay_exception` and `replay_error_type`, without exception text. This includes programming
errors such as `RuntimeError`, `AssertionError` and `TypeError`; callers must investigate the
evaluator/provider failure before retrying verification, and must not count the cohort as valid or
promote a later attempt. Only the adapter's known `ValueError` codes (`det_job_task_mismatch`,
`det_binding_invalid`, `det_bundle_mismatch`, `det_task_mismatch`, `det_candidate_mismatch`) retain
their existing reason codes; unknown `ValueError` diagnostics also become `det_replay_exception`.
Malformed returned I2c results remain schema failures. Process-control exceptions such as
`KeyboardInterrupt` and `SystemExit` propagate. During pre-seed `collect_det`, replay exceptions
also propagate so the caller aborts collection rather than committing partial B observations.

`audit_tasks(seed, manifest, selected)` takes the union of all selected pilot/confirmation primary tasks
(controls reuse those primary tasks) and the first 59 tasks from each accepted/rejected Det stratum.
Smaller strata are replayed in full. It uses the frozen length-prefixed `det-audit` key, resolves ties by
UTF-8 task ID bytes and returns a deduplicated list in UTF-8 order. `audit` is allowed only when declared
in A; its report must disclose that errors below 5% per stratum can be missed (§5.0, §3.3).

`BundleReplay(jobs)` looks up `(attempt_number, task_id, variant)` in the caller's frozen jobs. Each job
is `(I2c bundle, assignment, candidate_path)`; assignment includes matching `task_id` and `worker_export`.
Use a bundle authenticated by I2c `load_bundle`, not an unchecked object. Each snapshot task used by
this adapter supplies `det_binding`: `bundle_digest` (`sha256:<hex>`) plus `starter` and `reference`
candidate digests (`sha256-tree-exec-v1:<hex>`). These values are part of A's frozen snapshot digest;
acquisition must establish their base/reference provenance before A. This records existing frozen
evaluation inputs and does not change §9's criteria or selection rules.

Both `collect_det` and `verify_cohort` call `bind_snapshot(number, snapshot, expected_digest)` before
using this adapter. The adapter verifies the snapshot digest, rejects duplicate task IDs and retains a canonical copy. Direct
adapter calls must bind first. Before evaluation it compares bundle digest, task ID, benchmark/revision,
repository/base commit, image/command and named checks with the snapshot, then compares the genuine
I2c `freeze_candidate` envelope with the expected variant candidate digest. A wrong bundle/environment,
swapped starter/reference or changed candidate is rejected before `evaluate_assignment` is called.
I2c checks the envelope again during evaluation. Real container evaluation needs execution approval;
offline tests replace evaluation and also exercise the genuine I2c freeze/digest path without containers.
Other injected replay providers are trusted code and must honor the same frozen-input contract;
accepting arbitrary case booleans from an untrusted provider does not establish Det reproduction.

## Post-seed verification and evidence

C is recomputed from the verified seed, regenerated B and A's `pilot_arms`, including pilot units,
remaining ranking, primary tasks, assignment IDs and committed execution order. C must be merged at or
after beacon release and strictly before D. D is recomputed from seed, C's exact SHA, integer K and A's
`confirmatory_arms`; `materials[attempt_number].minimum_k` is a positive integer lower bound supplied by
the pilot power plan. This verifier enforces that bound but does not derive or authenticate it from
pilot outcomes or perform §6.3's power calculation; the caller must preserve that evidence. D must match canonical bytes, including controls, assignment IDs and execution
order. Changing the committed order, duplicating an assignment ID or overriding A's arm sets is rejected.

Both D's integer merge time and digest-bound timestamp proof must be strictly before the earliest
record on any confirmation unit. Every supplied record must be on a selected primary task and declared
arm, start strictly after C, and match A's package SHA/digest. Pilot runs between C and D are permitted.
These guards do not replace I1's accounting of missing runs, retry policy or observed record order.

The returned `checks` records (1) unique confirmation units from regenerated B and C/D, (2) the
recomputed lineage digest against B's pre-seed `lineage_digest`, and (3) complete selection/timing/replay
verification. A valid result includes `canonical_attempt`, `lineage_digest`, `det_replayed` and an
`evidence_digest` binding the attempt and committed B/C/D bytes. The digest identifies committed inputs;
it is not a signature or a digest of the supplied run records/replay transcript. Preserve those separately.
Missing/malformed evidence (including decoder recursion failures) returns `invalid_cohort` with a reason.
Any post-seed failure keeps the original canonical attempt and never promotes a later attempt.

## 解釈（owner確認待ち）

CC決定: G3は比較対象0件なら結合しない（G1・G2は適用）。
`chain_schedule(chain_hash)`はhashに結び付けて認証したgenesis・periodを返し、宣言値との不一致を拒否する。
V1不成立は`invalid_reason`を記録するが、読めるround・cutoff・撤回・snapshot・poolは連鎖と再利用の検査に残す。
B未作成は空のpoolとして扱うが、Aのsnapshot再利用は検査する。Bが存在して読めない場合や、後続候補の判定に必要な時刻境界・pool証拠が読めない場合はUNKNOWN（`attempt_history_unknown`・`attempt_pool_unknown`）で繰り上げない。
読めないroundはUNKNOWN（`attempt_round_unknown`）で後続へ繰り上げない。
より早い正準試行が決まった後のUNKNOWNは、その正準判定を変えない。round非増加・重なりはcohort全体を拒否する。
必要なmaterialsの欠落はUNKNOWN（`attempt_materials_unknown`）で後続へ繰り上げない。
Pは独立した宣言行`attempt_digest: sha256:<AのbytesのSHA-256、64桁小文字hex>`を1行持つ。部分一致・重複を認めない。
CC決定: 検査2の照合先はBの`lineage_digest`とする。§3.2.1の「事前登録の文書」は、
seedより前（`t_R − Δ`より前）にcommitされるBを含むと読む。Aと同じcommitのPでは、
後のpool収集で決まる系譜digestを宣言できないため。fixtureもA→pool収集→Bの順で作る。
CC決定: pilot・確認のarm集合はseedより前のAで宣言させ、C・Dと照合する。
§2の主比較arm（`native_goal`・`mission_verified_complex`）を両方必須とし、
`mission_baseline`を加えるかだけを宣言で選ぶ。§8.1の3のowner判断はbaselineの追加に限る。
未定義（ownerの決定事項）: 撤回poolの部分集合・1task除去を再利用に含めるか。
実装はsnapshot digestとpool identityの完全一致による拒否を維持する。§9の凍結項目は変えない。
