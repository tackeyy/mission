# #625 C1/A5 残り command の実測精査と設計

## 1. 結論

調査基準は `origin/main` の `14549975f40be5d559dfdfde2741d417deb1ff2d` である。

Issue 本文の「13 command」は誤りであり、parser の leaf command path は **14** である。内訳は C1 が 13、A5 が 1 である。parser と owner registry の閉集合一致は既存テストでも固定されている（`skills/mission/tests/test_command_inventory.py:84-96`, `skills/mission/tests/test_command_inventory.py:1737-1743`）。対象 14 path と `set_defaults` の対応は `skills/mission/bin/mission-state.py:18611-18668`, `skills/mission/bin/mission-state.py:19198-19213` にある。

裁定は次のとおりとする。

| 裁定 | 数 | command path |
|---|---:|---|
| MissionState kernel へ昇格 | **0** | なし |
| 読みのみとして降格 | **7** | `handoff await`, `handoff verify`, `queue next`, `queue status`, `pregate check`, `pregate digest`, `parallel-status` |
| 分離 aggregate writer として MissionState kernel 対象外 | **7** | `handoff publish`, `queue enqueue`, `queue mark`, `queue verify`, `pregate record`, `parallel-closeout`, `stop-guard-observe` |

14 path のどれも session state を永続化しない。`queue enqueue --from-state` と parallel 2 path は session state を読むが、前者は score revision scope の導出だけ（`skills/mission/bin/mission-state.py:8594-8620`, `skills/mission/lib/merge_queue.py:322-363`）、後者は child の状態・lease・coverage の集計だけである（`skills/mission/bin/mission-state.py:9076-9158`）。`stop-guard-observe` は session state ではなく、session ID の hash で名付けた `.stop-guard` sidecar を書く（`skills/mission/bin/mission-state.py:9196-9198`, `skills/mission/bin/mission-state.py:9231-9252`, `skills/mission/bin/mission-state.py:9255-9295`）。したがって MissionState の transition は新設しない。

ただし、「分離 aggregate だから対象外」で終えてはならない。ADR-005 は sidecar の独立 lifecycle を認める一方、各 aggregate 固有の validation / commit protocol を要求する（`docs/adr/005-typed-mission-kernel-and-unit-of-work.md:95-108`）。ADR-006/U5 はその最小要件を identity-checked read、validation、atomic publish、defined failure outcome としている（`docs/adr/006-kernel-reducer-adjudication.md:105-114`）。実測結果は次である。

- `parallel-closeout` と `stop-guard-observe` は現行の独自 protocol で U5 準拠。
- `handoff publish`, `pregate record`, `queue enqueue/mark/verify` は publish 直前の identity/absence witness がなく、現状は U5 未準拠。
- 全 command の予期しない例外で走り得る command-outcome telemetry も、安定読取後と publish 前の identity を結んでおらず U5 未準拠。
- #636 は legacy save の aggregate index 復旧だけを扱うため、上記の穴を #636 へ送らない。#625 の実装で protocol を補強し、fault injection が Green になった後にのみ「MissionState kernel 対象外・U5 準拠済み」と確定する。

## 2. command 数と parser route の確定

### 2.1 数え方

「command」は top-level parser 名や handler 関数数ではなく、`argparse` の **leaf path** で数える。`pregate` と `queue` は各 subparser が同じ handler を共有するため、top-level 名や handler 数で数えると mutation の差を失う。既存 inventory も leaf path を再帰列挙する（`skills/mission/tests/test_command_inventory.py:84-96`）。

実測値は次の三つに分かれる。

| 単位 | 数 | 根拠 |
|---|---:|---|
| leaf command path | **14** | parser の `set_defaults`（`skills/mission/bin/mission-state.py:18611-18668`, `skills/mission/bin/mission-state.py:19198-19213`） |
| owner | C1 **13** + A5 **1** | `COMMAND_OWNER_REGISTRY`（`skills/mission/lib/mission_application/command_owners.py:68-107`） |
| handler 関数 | **8** | `cmd_handoff_*` 3、`cmd_queue` 1、`cmd_pregate` 1、`cmd_parallel_*` 2、`cmd_stop_guard_observe` 1（各行は下表） |

調査時の parser probe は `argparse._SubParsersAction` を再帰列挙し、次を返した。

```text
count= 14
handoff await            cmd_handoff_await
handoff publish          cmd_handoff_publish
handoff verify           cmd_handoff_verify
parallel-closeout        cmd_parallel_closeout
parallel-status          cmd_parallel_status
pregate check            cmd_pregate
pregate digest           cmd_pregate
pregate record           cmd_pregate
queue enqueue            cmd_queue
queue mark               cmd_queue
queue next               cmd_queue
queue status             cmd_queue
queue verify             cmd_queue
stop-guard-observe       cmd_stop_guard_observe
```

### 2.2 command × aggregate 棚卸し

「session field」は session state への書込み field を示す。14 path はすべて「なし」である。読み field は authority の所在を誤認しやすい path だけ併記する。

| # | leaf command | `set_defaults` / handler | domain access | 書込み先 aggregate / path | session field | 裁定 |
|---:|---|---|---|---|---|---|
| 1 | `handoff await` | `cmd_handoff_await`（`skills/mission/bin/mission-state.py:19205-19209`, `skills/mission/bin/mission-state.py:14387-14402`） | newer envelope の待機・検証（`skills/mission/lib/evidence_handoff.py:244-263`） | なし。読取は `.mission-state/handoff/<topic>/<seq>-<digest8>.json`（`skills/mission/lib/evidence_handoff.py:58-78`, `skills/mission/lib/evidence_handoff.py:218-220`） | なし | 読みのみへ降格 |
| 2 | `handoff publish` | `cmd_handoff_publish`（`skills/mission/bin/mission-state.py:19200-19204`, `skills/mission/bin/mission-state.py:14376-14384`） | append | evidence handoff: `.mission-state/handoff/<topic>/<seq>-<digest8>.json`（`skills/mission/lib/evidence_handoff.py:188-230`） | なし | 分離 aggregate。U5 補強後に対象外 |
| 3 | `handoff verify` | `cmd_handoff_verify`（`skills/mission/bin/mission-state.py:19210-19213`, `skills/mission/bin/mission-state.py:14405-14412`） | envelope/digest 読取検証（`skills/mission/lib/evidence_handoff.py:266-276`） | なし | なし | 読みのみへ降格 |
| 4 | `queue enqueue` | `cmd_queue`（`skills/mission/bin/mission-state.py:18635-18643`, `skills/mission/bin/mission-state.py:8623-8639`） | queue append/supersede（`skills/mission/lib/merge_queue.py:413-464`）。`--from-state` のみ `score_history[-1].revision_scope` を読む（`skills/mission/lib/merge_queue.py:322-363`） | merge queue: `.mission-state/merge-queue.json`（`skills/mission/lib/merge_queue.py:63-80`） | 書込みなし。読取は `score_history`, `revision_scope.base_sha`, `revision_scope.head_sha` | 分離 aggregate。U5 補強後に対象外 |
| 5 | `queue mark` | `cmd_queue`（`skills/mission/bin/mission-state.py:18654-18658`, `skills/mission/bin/mission-state.py:8646-8647`） | queue entry terminalization（`skills/mission/lib/merge_queue.py:508-526`） | merge queue: `.mission-state/merge-queue.json` | なし | 分離 aggregate。U5 補強後に対象外 |
| 6 | `queue next` | `cmd_queue`（`skills/mission/bin/mission-state.py:18647-18649`, `skills/mission/bin/mission-state.py:8642-8643`） | queue candidate 読取（`skills/mission/lib/merge_queue.py:472-481`） | なし | なし | 読みのみへ降格 |
| 7 | `queue status` | `cmd_queue`（`skills/mission/bin/mission-state.py:18644-18646`, `skills/mission/bin/mission-state.py:8640-8641`） | queue 全件読取（`skills/mission/lib/merge_queue.py:467-469`） | なし | なし | 読みのみへ降格 |
| 8 | `queue verify` | `cmd_queue`（`skills/mission/bin/mission-state.py:18650-18653`, `skills/mission/bin/mission-state.py:8644-8645`） | base 一致時は read-only、不一致時は `invalidated` を書く（`skills/mission/lib/merge_queue.py:484-505`） | base 不一致時のみ merge queue: `.mission-state/merge-queue.json` | なし | 分離 aggregate。U5 補強後に対象外 |
| 9 | `pregate check` | `cmd_pregate`（`skills/mission/bin/mission-state.py:18624-18628`, `skills/mission/bin/mission-state.py:8583-8589`） | cache lookup（`skills/mission/lib/pregate_cache.py:315-331`） | なし。読取は `.mission-state/pregate/<issue_ref_key>.json`（`skills/mission/lib/pregate_cache.py:71-83`, `skills/mission/lib/pregate_cache.py:231-244`） | なし | 読みのみへ降格 |
| 10 | `pregate digest` | `cmd_pregate`（`skills/mission/bin/mission-state.py:18629-18631`, `skills/mission/bin/mission-state.py:8574-8581`） | 入力 JSON の canonical digest 計算（`skills/mission/lib/pregate_cache.py:105-113`） | なし | なし | 読みのみへ降格 |
| 11 | `pregate record` | `cmd_pregate`（`skills/mission/bin/mission-state.py:18620-18623`, `skills/mission/bin/mission-state.py:8565-8573`） | keyed cache replace | pregate cache: `.mission-state/pregate/<issue_ref_key>.json`（`skills/mission/lib/pregate_cache.py:261-290`） | なし | 分離 aggregate。U5 補強後に対象外 |
| 12 | `parallel-status` | `cmd_parallel_status`（`skills/mission/bin/mission-state.py:18611-18613`, `skills/mission/bin/mission-state.py:9161-9169`） | manifest と child session の集計（`skills/mission/bin/mission-state.py:9076-9158`） | なし | 書込みなし。読取は `logical_group_id`, `issue_ref`, `lease_expires_at`, `loop_active`, `passes`, `halt_reason`, artifact/activity/review provenance | 読みのみへ降格 |
| 13 | `parallel-closeout` | `cmd_parallel_closeout`（`skills/mission/bin/mission-state.py:18614-18616`, `skills/mission/bin/mission-state.py:9172-9192`） | child session を読んで manifest を terminalize | parallel-group manifest: `.mission-state/sessions/<group_id>.group.json`（`skills/mission/bin/mission-state.py:8683-8684`, `skills/mission/bin/mission-state.py:9176-9186`） | 書込みなし。読取 field は `parallel-status` と同じ | 分離 aggregate。現行 U5 準拠、対象外 |
| 14 | `stop-guard-observe` | `cmd_stop_guard_observe`（`skills/mission/bin/mission-state.py:18660-18668`, `skills/mission/bin/mission-state.py:9325-9345`） | digest/counter observation の CAS（`skills/mission/lib/mission_application/runtime_guard.py:190-220`） | stop observation telemetry: `.mission-state/sessions/.<sid-hash16>.stop-guard`（`skills/mission/bin/mission-state.py:9196-9198`, `skills/mission/bin/mission-state.py:9255-9295`） | なし | 分離 aggregate。現行 U5 準拠、対象外 |

### 2.3 共通 failure telemetry

上表の domain access とは別に、`main()` は handler から予期しない例外が漏れた場合、`command-outcome` telemetry を記録する（`skills/mission/bin/mission-state.py:19374-19383`）。これは session bytes を触らない materialized telemetry view と明記され（`skills/mission/bin/mission-state.py:12402-12415`）、保存先は `.mission-state/telemetry/command-outcomes/<sid-hash16>.json` である（`skills/mission/lib/command_outcomes.py:145-160`, `skills/mission/lib/command_outcomes.py:349-370`）。

したがって「読みのみへ降格」は **domain aggregate と session state に対して読みのみ**という意味である。予期しない例外時の共通 telemetry は別 aggregate として U5 判定し、no-write guard は「任意の filesystem write 禁止」ではなく「session state authority を持たないこと」を固定する。

## 3. U5 commit protocol の実測判定

### 3.1 判定基準

U5 の比較基準は `administrative_commit` の実装とする。同関数は captured record の strict read、validation、mutation、publish 直前の完全 identity 再確認、atomic writer の順で処理し、identity drift を `record-changed` として拒否する（`skills/mission/lib/mission_persistence/administrative.py:42-110`）。既存 inventory guard は `mission-state.py` 内の直接 `atomic_write_*` caller を閉集合化しているが（`skills/mission/tests/test_issue635_admin_commit_protocol.py:185-240`）、`evidence_handoff.py`, `pregate_cache.py`, `merge_queue.py`, `command_outcomes.py` の独自 writer はその scan 対象外である。よって #635 の Green だけでは本批の sidecar 準拠を証明しない。

「identity-checked read」は単に安全に読めることだけではなく、**その read witness が publish 直前まで同じであること**を含む。これは基準実装が `capture_record()` の identity と publish 直前の `lstat()` を比較するためである（`skills/mission/lib/mission_persistence/administrative.py:96-109`）。新規作成では present identity の代わりに「destination absent」を witness とし、publish が既存 path を上書きしないことを必要条件とする。

### 3.2 writer 別 matrix

| writer / 対象 path | identity witness | validation | atomic publish | defined failure / 不変 | 判定 |
|---|---|---|---|---|---|
| `handoff publish` | latest は filename 一覧だけで決め、destination absent を publish 時に固定しない（`skills/mission/lib/evidence_handoff.py:159-170`, `skills/mission/lib/evidence_handoff.py:194-220`） | payload は canonical 化するが、生成 envelope を `_validate_envelope` に通さず publish（`skills/mission/lib/evidence_handoff.py:81-89`, `skills/mission/lib/evidence_handoff.py:200-220`） | temp + fsync + `os.replace` + dir fsync（`skills/mission/lib/evidence_handoff.py:212-220`） | temp cleanup はあるが `os.replace` は同名 destination を上書き可能（`skills/mission/lib/evidence_handoff.py:218-228`） | **未準拠** |
| `pregate record` | 同じ issue record が存在しても読取/identity capture せず上書き（`skills/mission/lib/pregate_cache.py:261-280`）。同一 key 上書きは現行契約（`skills/mission/tests/test_issue421_pregate_cache.py:268-298`） | `_validate_envelope` 済み（`skills/mission/lib/pregate_cache.py:261-268`） | temp + fsync + `os.replace` + dir fsync（`skills/mission/lib/pregate_cache.py:270-280`） | temp cleanup はあるが stale writer の上書きを拒否しない | **未準拠** |
| merge queue (`enqueue/mark/verify`) | strict read は read 中の identity を検証する（`skills/mission/lib/merge_queue.py:117-150`）が、返り値に witness を持たず、publish 前再確認がない（`skills/mission/lib/merge_queue.py:242-246`, `skills/mission/lib/merge_queue.py:267-307`） | current document は `_validate_queue` 済み（`skills/mission/lib/merge_queue.py:230-246`） | temp + fsync + `os.replace` + dir fsync（`skills/mission/lib/merge_queue.py:267-280`） | lock は cooperating writer を直列化するが、read 後 path swap を検出しない | **未準拠** |
| `parallel-closeout` | strict read が完全 identity を返し（`skills/mission/bin/mission-state.py:8795-8837`）、replace 前に同 identity を 2 回照合（`skills/mission/bin/mission-state.py:8958-8973`） | read 時に schema/shape/status を閉じて検証（`skills/mission/bin/mission-state.py:8840-8899`） | exclusive temp + fsync + `os.replace` + dir fsync（`skills/mission/bin/mission-state.py:8902-8923`, `skills/mission/bin/mission-state.py:8968-8976`） | validation failure は manifest 不変。active lease 拒否時の bytes 不変も固定済み（`skills/mission/tests/test_parallel_group_manifest.py:182-206`） | **準拠**。race fault test を #625 で追加して固定 |
| `stop-guard-observe` | strict read identity を repository port へ返し（`skills/mission/bin/mission-state.py:9231-9252`, `skills/mission/bin/mission-state.py:9314-9322`）、create は link-only、replace は expected identity を比較（`skills/mission/bin/mission-state.py:9255-9287`） | request、previous、proposed を application で検証（`skills/mission/lib/mission_application/runtime_guard.py:153-187`, `skills/mission/lib/mission_application/runtime_guard.py:190-220`） | exclusive temp + link/replace + fsync + read-back（`skills/mission/bin/mission-state.py:9261-9291`） | hostile file は外部 bytes 不変、並行 8 update も lost update なし（`skills/mission/tests/test_stop_guard_dedupe.py:190-214`, `skills/mission/tests/test_stop_guard_dedupe.py:246-258`） | **準拠** |
| command-outcome telemetry | stable strict read は行う（`skills/mission/lib/command_outcomes.py:164-200`）が、その identity を返さず、publish は destination identity を再確認しない（`skills/mission/lib/command_outcomes.py:287-336`, `skills/mission/lib/command_outcomes.py:349-368`） | record/current document を検証（`skills/mission/lib/command_outcomes.py:106-124`, `skills/mission/lib/command_outcomes.py:203-216`, `skills/mission/lib/command_outcomes.py:349-353`） | exclusive temp + fsync + `os.replace` + dir fsync（`skills/mission/lib/command_outcomes.py:287-336`） | unsafe file は fail-closed だが read 後 swap の CAS がない。既存 hostile-file tests は read/publish 間 race を注入しない（`skills/mission/tests/test_command_outcomes.py:214-250`） | **未準拠** |

### 3.3 残作業の帰属

上記未準拠は **#625 で対応する**。

理由は三つである。

1. #625 の完了条件そのものが、対象 command の separate aggregate を U5 準拠確認してから対象外確定することである。未準拠 path を残したまま inventory だけ閉じると、その条件を満たさない。
2. #636 の対象は legacy save 後の aggregate index update であり、本批の sidecar writer と fault model が異なる。U5 基準実装の module docstring も legacy index を U5-2 として別扱いにしている（`skills/mission/lib/mission_persistence/administrative.py:1-8`）。
3. 本批の remediation は「separate aggregate commit witness」という一つの primary trust boundary にまとめられる。behavior group は (a) append/create、(b) keyed replace/create、(c) conformance inventory/fault injection の 3 群に収める。

現行 `administrative_commit()` を sidecar にそのまま使わない。同関数は既存の regular JSON object を `Path` で capture する janitor-style mutation 用であり、missing destination の create と descriptor-held directory chain を扱わない（`skills/mission/lib/mission_persistence/administrative.py:52-84`）。本批では既存 sidecar の no-follow descriptor/lock を維持し、次を各 protocol に追加する。

| protocol | 追加する commit witness |
|---|---|
| evidence handoff append | generated envelope を publish 前に閉じて検証する。final name の **absence** を witness とし、hard-link/create-only publish で既存 envelope を上書きしない。同名出現は stable conflict で拒否し、再採番を暗黙に行わない |
| pregate keyed replace | strict read が `(document or missing, identity or absent)` を返す。new envelope を検証し、publish 直前に present identity/absence を再確認。drift は既存 bytes 不変で拒否 |
| merge queue update | `_load_queue` が `(validated queue, witness)` を返す。mutation 後の proposed queue も `_validate_queue` に通し、present identity/absence を publish 前に比較。`verify` の base 一致 branch は write なしを維持 |
| command-outcome telemetry | `_read_regular_at` から identity/absence witness を返し、`_atomic_json_at` が publish 前に同 witness を照合。create は no-replace、update は CAS。telemetry failure は元 command の拒否結果を覆さず、既存の best-effort contract を維持（`skills/mission/bin/mission-state.py:12409-12415`） |

## 4. `stop-guard-observe` と #615 GuardDecision の整合

`stop-guard-observe` は MissionState transition に昇格させない。application use case は stop observation sidecar の allowlisted field だけを更新し（`skills/mission/lib/mission_application/runtime_guard.py:27-46`, `skills/mission/lib/mission_application/runtime_guard.py:190-220`）、既存テストは session authority field と交差しないことを固定している（`skills/mission/tests/test_issue510_a5_application.py:111-131`）。実 shell path でも command は block 前の counter/dedupe 観測として発火するだけである（`scripts/mission-stop-guard.sh:323-355`）。

#615 の設計は shell が `none / mark-halt / cleanup-stale / stop-guard-observe` の閉じた command union を decision のとおり dispatch し、observe の mode/retry も receipt として Python へ返す形である（`investigate/615@80ce494:docs/design/615-guard-decision.md:238-243`, `investigate/615@80ce494:docs/design/615-guard-decision.md:315-330`, `investigate/615@80ce494:docs/design/615-guard-decision.md:418-440`）。この設計と #625 の境界は次のとおり固定する。

- #615 が `StopGuardObserveCommand` の **選択 authority**、typed args、receipt 解釈、retry を所有する。
- #625 は選択を行わず、`observe_stop_guard()` と stop observation repository の **sidecar mutation/U5 contract** だけを所有する。
- shell は command 名・now・TTL・mode を自分で決めない。closed dispatch の固定は #615 の guard 対象である（`investigate/615@80ce494:docs/design/615-guard-decision.md:514-544`）。
- #625 の no-session-write guard は `stop-guard-observe` を separate writer allowlist に置くが、`mark-halt` や `cleanup-stale` の session mutation を当該 handler から reachable にしてはならない。

これにより #615 の「decision dispatch」と #625 の「separate aggregate commit」は直列に接続され、authority が重複しない。

## 5. transition 定義

### 5.1 MissionState transition

**新設なし。** 14 path に `mission_kernel.commands` の command class、transition rule、`decide()` route、`transition.new_state` persistence を追加しない。

理由は、session state の write field が 0 であり、ADR-005 が列挙する独立 aggregate に対して MissionState 統合を強制しないためである（`docs/adr/005-typed-mission-kernel-and-unit-of-work.md:104-108`）。`parallel-closeout` が child の `passes/loop_active/halt_reason` を読むことや、`queue enqueue --from-state` が `score_history` を読むことは、read projection であって authority mutation ではない。

### 5.2 separate aggregate operation

kernel transition ではないが、実装・テストで mutation 境界を曖昧にしないため、domain operation を次の閉集合として記録する。

| operation | precondition/read witness | proposed change | publish result |
|---|---|---|---|
| `AppendEvidenceHandoff` | topic directory identity + final destination absent | validated immutable envelope を 1 件追加 | created / conflict / invalid / publish-failed |
| `ReplacePregateRecord` | target identity または absent | validated issue-keyed cache record に置換 | committed / record-changed / invalid / publish-failed |
| `EnqueueMergeCandidate` | queue identity または absent + validated current queue | append と active predecessor supersede | committed / queue-changed / invalid / publish-failed |
| `MarkMergeCandidate` | queue identity + nonterminal entry | terminal status/reason/time | committed / queue-changed / invalid / publish-failed |
| `InvalidateMergeCandidateOnBaseMismatch` | queue identity + queued/ready entry | base mismatch 時だけ invalidated。match 時は no-op | committed / unchanged / queue-changed / invalid |
| `CloseParallelGroup` | manifest identity + running + child completion/lease gates | terminal/outcome/closed_at/coverage | committed / manifest-changed / gate-rejected / publish-failed |
| `RecordStopObservation` | sidecar identity または absent + valid previous | bounded counters/digest/detail epoch | committed / sidecar-changed / invalid / publish-failed |
| `AppendCommandOutcome` | telemetry identity または absent + valid bounded history | validated record append、128 件 cap | committed / telemetry-changed / invalid / publish-failed |

## 6. no-session-write guard 設計

### 6.1 目的

「対象外」の根拠を将来も維持するため、新規 `skills/mission/tests/test_issue625_c1_a5_remaining.py` に parser route と no-session-write の静的 guard を置く。既存の批1-d guard は completion-adjacent field の direct assignment と helper call を検出し、合成違反で analyzer 自身の検出力を確認している（`skills/mission/tests/test_issue620_kernel_a5_c1.py:138-208`）。本批は shared handler、local helper、alias、imported sidecar boundary があるため、それより一段強くする。

### 6.2 閉じる inventory

1. `SCOPED_LEAF_COMMANDS` を本書の 14 path exact set とする。
2. parser を再帰列挙し、各 path の `set_defaults(func=...)` が §2.1 の 8 handler mapping と一致することを assert する。
3. `SEPARATE_WRITE_BOUNDARIES` を次の exact set とする。
   - `publish_evidence_handoff`
   - `record_pregate_cache`
   - `enqueue_merge_queue`, `mark_merge_queue`, `verify_merge_queue`
   - `_replace_parallel_manifest`
   - `observe_stop_guard` → `_LegacyStopObservationRepository.save`
   - global exception path の `append_command_outcome_sidecar`
4. 上記以外の session/repository persistence sink が 8 handler から reachable なら fail する。

### 6.3 AST 検出対象

既存 `forbidden_calls_in_reachable()` は nested helper、alias、callable container、callback argument まで辿る（`skills/mission/tests/test_command_inventory.py:99-120`）。同 resolver を共通 helper へ抽出するか、テスト内で再利用し、次を検出する。

- `terminal_outcome`, `phase`, `passes`, `loop_active`, `halt_reason`, `halt_category`, `score_history` への `Assign / AnnAssign / AugAssign`。
- `.update({...})`, `.setdefault(...)`, `setattr(...)` による上記 key の書込み。
- `resolve_state_file`, `session_file`, `atomic_write_json`, `atomic_write_bytes` と session repository の `save / execute / stage`。
- alias、wrapper、lambda、dict/list/tuple から取り出した writer、callback として渡した writer。
- `Path.write_text/write_bytes` または `open(..., write mode)` で、target が session file constructor から導出される経路。
- `eval/exec/getattr` による動的 writer 解決。解析不能な動的 dispatch は許可せず violation とする。

sidecar-specific writer は method 名だけで blanket allow しない。例えば `_LegacyStopObservationRepository.save` だけを qualified name で許可し、その body が `_write_stop_guard_state` のみへ到達することを固定する（`skills/mission/bin/mission-state.py:9298-9322`）。

### 6.4 runtime bytes guard

AST の限界を補うため、v4/v5 session fixture の bytes を各 command 前後で比較する。

- 14 path の成功 path。
- `queue verify` の match/no-op と mismatch/write の両 branch。
- `queue enqueue --from-state`。
- `parallel-closeout` の success と gate rejection。
- `stop-guard-observe` の create/update。
- malformed/unsafe/race/publish failure。

expected は常に session file bytes 不変とする。sidecar writer の bytes 変化だけを operation ごとに明示して許す。`stop-guard-observe` については既に同じ性質を固定する regression test がある（`skills/mission/tests/test_stop_guard_dedupe.py:106-131`）。

### 6.5 合成違反 fixture と検出力実証

最低限、次の negative fixture を analyzer 自身へ直接渡し、各 expected code が 1 件以上出ることを assert する。

| fixture | 合成違反 | expected code |
|---|---|---|
| `direct-authority-field` | `state["loop_active"] = False` | `authority-field-write` |
| `alias-session-writer` | `writer = atomic_write_json; writer(resolve_state_file(...), state)` | `session-write-sink` |
| `wrapper-session-writer` | local helper 内で session writer を呼び、handler から helper を呼ぶ | `session-write-sink` |
| `repository-save` | `repository.save(state)` | `session-repository-write` |
| `path-write` | `session_file(...).write_text(payload)` | `session-path-write` |
| `dynamic-writer` | `getattr(repository, name)(state)` | `dynamic-writer-resolution` |
| `positive-read` | `state.get("loop_active")` | violation なし |
| `positive-sidecar` | allowlisted boundary だけを呼ぶ | violation なし |

2026-08-23 の一時 AST prototype では、代表 4 violation と positive read を次のとおり識別した。

```text
direct-field: ['authority-field-write']
alias-writer: ['session-write-sink']
repository-save: ['session-write-method']
path-write: ['session-write-method']
positive-read: []
```

production test は上記 prototype の shallow alias table ではなく、既存 `forbidden_calls_in_reachable()` の到達解析を使う。既存の合成違反先例も、analyzer が空実装でも Green にならない形を採る（`skills/mission/tests/test_issue620_kernel_a5_c1.py:204-208`）。

## 7. TDD テストリスト

実装は次の Red → Green 順に進める。

### Red 1: inventory と authority boundary

1. parser leaf が 14 exact set、handler mapping が 8 exact setである。
2. owner registry が C1 13 + A5 1 と一致する。
3. no-session-write AST guard が canonical source を Green、§6.5 の各 synthetic violation を Red にする。
4. 14 path の session bytes runtime guard が v4/v5 で不変になる。
5. `queue verify` を read-only と誤分類しない。base mismatch だけ queue write と判定する。
6. global internal-error path が command-outcome telemetry へ到達することを inventory に含める。

### Red 2: U5 append/create

7. handoff envelope を publish 前に validator へ通す。
8. destination が publish 直前に出現した race を注入し、既存 bytes を変えず conflict で拒否する。
9. crash/例外を temp write 前、fsync 後、link 前、link 後に注入し、0 または 1 個の完全 envelope だけが残る。
10. concurrent publish で seq の重複・上書き・lost envelope がない。

### Red 3: U5 keyed replace/create

11. pregate の existing identity swap、missing→appeared race、symlink/hardlink、publish failure を拒否し、旧 bytes を維持する。
12. merge queue の read 後 identity swap、missing→appeared race、invalid proposed queue、publish failure を拒否し、旧 bytes を維持する。
13. queue の concurrent enqueue/mark/verify を同じ commit witness に通し、既存の enqueue 直列化契約を維持する。現行 concurrent enqueue test は 6 件保持を固定している（`skills/mission/tests/test_issue424_merge_queue.py:447-459`）。
14. command-outcome telemetry の read 後 identity swap、missing→appeared race、publish failure を拒否し、元 command の exit classification を変えない。

### Red 4: 現行準拠 protocol の固定

15. `parallel-closeout` で manifest identity を read 後/first check 後/temp fsync 後に swap し、publish を拒否して外部/旧 bytes を維持する。
16. `stop-guard-observe` の create-only/CAS/read-back、hostile file、8 concurrent update の既存 tests を維持する。
17. U5 conformance matrix の writer exact set を静的 inventory として固定し、新 writer 追加時は判定行・fault test なしでは失敗させる。

### Red 5: #615 と distribution

18. `stop-guard-observe` は #615 の closed command receipt からのみ dispatch され、handler 自身は GuardDecision を選ばない。
19. observe receipt の `mode`/retry は Python が解釈し、shell policy を増やさない。
20. canonical/plugin の `mission-state.py`, `evidence_handoff.py`, `pregate_cache.py`, `merge_queue.py`, `command_outcomes.py`, `mission_persistence` recursive modules が byte/inventory 一致する。既存 sync inventory は対象 sidecar 3 module を明示し（`skills/mission/tests/test_plugins_in_sync.py:151-162`）、recursive Python inventory も固定する（`skills/mission/tests/test_plugins_in_sync.py:629-635`）。
21. Python 3.9 compatibility、shellcheck、full test suite を通す。

## 8. 受け入れ条件

- [ ] parser から対象を **14 leaf path** と再確定し、C1 13 + A5 1、handler 8 の閉集合を CI 固定した。
- [ ] 14 path の session state writer が 0 であり、session authority field の書込みが 0 であることを AST + v4/v5 bytes test で証明した。
- [ ] MissionState kernel command/transition を一件も追加していない。
- [ ] 読みのみ 7 path を降格し、`queue verify` の conditional write を読みのみへ誤分類していない。
- [ ] separate writer 7 path の target path、operation、failure behavior を inventory 化した。
- [ ] `handoff publish`, `pregate record`, `queue enqueue/mark/verify` の U5 gap を #625 内で閉じた。
- [ ] cross-cutting command-outcome telemetry の identity/CAS gap を #625 内で閉じた。
- [ ] `parallel-closeout` と `stop-guard-observe` の現行 U5 protocol を race/kill-point test で固定した。
- [ ] no-session-write guard が合成違反 fixture 全件を検出し、positive fixture を誤検出しない。
- [ ] `stop-guard-observe` が #615 の closed decision dispatch/receipt 設計と整合し、shell に新しい判断 authority を置いていない。
- [ ] source/plugin mirror と Python 3.9 compatibility が Green。
- [ ] 親 Issue #614 と Issue #625 の command 数・批3-c 裁定を更新した。

## 9. 親 Issue #614 の更新案

### 9.1 「批 3 対象」本文の修正

現行の「C1 残り 12」「C1 + A5 13」を、次へ置換する。

```markdown
批 3 対象のうち批3-cは、parser leaf の実測で **14 command path**
（C1.separate-aggregate 13 + A5.runtime-guard 1）。Issue #625 本文の
「13」および旧表の「C1 残り12」は off-by-one だった。
```

### 9.2 子 Issue 表の #625 行へ追記

```markdown
- [ ] #625 批3-c: C1 残り 13 + A5 残り 1 = **14 leaf command path**
  - 実測: MissionState writer 0 / kernel 昇格 0
  - 読みのみ降格 7: handoff await/verify, queue next/status,
    pregate check/digest, parallel-status
  - separate aggregate writer 7: handoff publish, queue enqueue/mark/verify,
    pregate record, parallel-closeout, stop-guard-observe
  - U5 実測: parallel-closeout と stop-guard-observe は準拠。
    handoff/pregate/queue と cross-cutting command-outcome telemetry の
    identity/CAS gap は #625 で補強後に対象外確定
  - stop-guard-observe の選択/retry authority は #615 GuardDecision、
    #625 は sidecar commit protocol のみを所有
```

### 9.3 Issue #625 本文の完了条件修正

```markdown
- [ ] 14 leaf command path（C1 13 + A5 1）の読み書き・対象 aggregate の棚卸し
- [ ] session state writer 0 を no-write AST guard と runtime bytes test で固定
- [ ] separate aggregate writer の U5 protocol を fault injection で確認し、
      未準拠の handoff/pregate/queue/command-outcome telemetry を本 Issue で補強
- [ ] stop-guard-observe を #615 の closed GuardDecision dispatch と整合
- [ ] source/plugin mirror
```

## 10. 調査時の検証

コード変更前の baseline characterization として次を実行した。

```text
PYTHONPATH=. pytest -q \
  skills/mission/tests/test_command_inventory.py::test_all_parser_commands_have_exactly_one_declared_owner \
  skills/mission/tests/test_evidence_handoff.py \
  skills/mission/tests/test_issue421_pregate_cache.py \
  skills/mission/tests/test_issue424_merge_queue.py \
  skills/mission/tests/test_parallel_group_manifest.py \
  skills/mission/tests/test_stop_guard_dedupe.py \
  skills/mission/tests/test_issue635_admin_commit_protocol.py \
  skills/mission/tests/test_plugins_in_sync.py::test_recursive_python_library_inventory_in_sync

81 passed in 62.92s
```

この Green は現行 behavior の characterization であり、§3 の U5 gap がないことを意味しない。既存 suite は handoff/pregate/queue/command-outcome の read→publish 間 identity swap を注入していないため、§7 の Red tests を先に追加する。
