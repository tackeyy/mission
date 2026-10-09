# Offline pool selection and preregistration verification

`bench_selection.py` implements selection and canonical attempts in I2d in [the evaluation preregistration](../../docs/design/884-evaluation-aggregation.md),
§5.0 and §5.1. `bench_cohort.py` depends on it for Det replay, C/D verification and
§3.2.1 checks 1–3. Selection never imports cohort; it can be shipped independently. Inputs are injected; no transport or worker run is started.

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
UTF-16. Identifier/key ties use UTF-8 bytes. A declares nonempty, unique §2 arm subsets as `pilot_arms` and `confirmatory_arms`.
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

`bench_cohort.verify_cohort(history, materials, used_number, beacon, records, proofs, replay)` regenerates B and picks
the earliest seed-independent V1–V5 candidate, then verifies C/D, package, timing and Det replays.
V4 failures permit later candidates; post-seed failures never do. Reusing identical invalid/withdrawn
pools is forbidden even with changed snapshot serialization/reference metadata. Trusted materials supply
snapshot, scope, loaded generator revision and pilot-derived `minimum_k`. Load this code from A's pinned
revision; a matching SHA string alone does not establish provenance. Records supply task/unit, arm,
package SHA/digest and integer start time. Check 2 recomputes the lineage record digest against B’s `lineage_digest`, bound before seed release.
Only status=valid with checks 1–3 true establishes selection evidence. I1 still checks run separation,
order, complete assignments and statistics; K planning, approval and acquisition remain caller duties.

## 解釈（owner確認待ち）

CC決定: G3は比較対象0件なら結合しない（G1・G2は適用）。
`chain_schedule(chain_hash)`はhashに結び付けて認証したgenesis・periodを返し、宣言値との不一致を拒否する。
V1不成立は`invalid_reason`を記録するが、読めるroundは再利用・非増加の検査に残す。
読めないroundはUNKNOWN（`attempt_round_unknown`）で後続へ繰り上げない。
より早い正準試行が決まった後のUNKNOWNは、その正準判定を変えない。round非増加・重なりはcohort全体を拒否する。
必要なmaterialsの欠落はUNKNOWN（`attempt_materials_unknown`）で後続へ繰り上げない。
Pは独立した宣言行`attempt_digest: sha256:<AのbytesのSHA-256、64桁小文字hex>`を1行持つ。部分一致・重複を認めない。
CC決定: 検査2の照合先はBの`lineage_digest`とする。§3.2.1の「事前登録の文書」は、
seedより前（`t_R − Δ`より前）にcommitされるBを含むと読む。Aと同じcommitのPでは、
後のpool収集で決まる系譜digestを宣言できないため。fixtureもA→pool収集→Bの順で作る。
CC決定: pilot・確認のarm集合はseedより前のAで宣言させ、C・Dと照合する。
§2の非空の部分集合を許容し、baselineを含めるかという§8.1の3のowner判断を先取りしない。
未定義（ownerの決定事項）: 撤回poolの部分集合・1task除去を再利用に含めるか。
実装はsnapshot digestとpool identityの完全一致による拒否を維持する。§9の凍結項目は変えない。
