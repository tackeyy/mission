# Design: Issue レベル並列 dispatch の調整機構と merge queue（Issue #424）

## 目的

兄弟 Issue の mission を並列完走させたとき、先行 merge で base（main）が動いて後続の accepted レビューが無効化され refreeze→fresh review の手戻りが発生する問題を、**merge 順序の直列化と base 移動の機械検出**で解消する。並列実行能力は既にある（複数 active session 同時稼働を実測済み）。足りないのは調整機構のみ。

## 設計方針

- OSS-portable: GitHub 固有 API に依存しない。`pr_ref` / sha は自由形式の文字列として扱い、実際の merge 操作・base sha 取得はオーケストレーター（ホスト）が行う。queue は「順序と検証状態の管理元」
- 既存 state 機構と独立した sidecar（canonical plan / lease / gate 意味論に触れない）。ただし `queue verify` の不一致だけは exit 2（merge 前の調整ゲートとして機能させる）
- evidence_handoff.py / pregate_cache.py の atomic write・flock・安全検査パターンを踏襲

## スコープ

やること:

1. **`skills/mission/lib/merge_queue.py`** 新規:
   - 置き場所: `<project>/.mission-state/merge-queue.json`（単一ファイル・flock 排他・tmp+rename atomic write）
   - schema `mission-merge-queue/1`:
     ```json
     {
       "schema": "mission-merge-queue/1",
       "entries": [
         {
           "queue_id": "<16hex>",
           "session_id": "<sid>",
           "issue_ref_key": "979",
           "pr_ref": "<自由形式: PR番号/URL/branch>",
           "head_sha": "<40hex>",
           "accepted_base_sha": "<40hex: レビューacceptedを取得した時点のbase>",
           "depends_on": ["<issue_ref_key>", "..."],
           "status": "queued|ready|merged|invalidated|superseded",
           "reason": "",
           "enqueued_at": "<ISO8601 UTC>",
           "updated_at": "<ISO8601 UTC>"
         }
       ]
     }
     ```
2. **`mission-state.py` にサブコマンド `queue` を追加**（登録とディスパッチの最小行）:
   - `queue enqueue --issue-ref <ref> --pr-ref <ref> --head-sha <sha> --base-sha <sha> [--depends-on <csv>] [--session <sid>]`: 追加して queue_id を返す。同一 issue_ref_key の既存 queued/ready entry は `superseded` に落として置き換える
   - `queue status [--json]`: read-only。全 entry を enqueue 順で出力
   - `queue next [--json]`: read-only。「depends_on の全 issue_ref_key が `merged` であり、かつ最も早く enqueue された queued entry」を1件返す（= 次に merge してよい候補）。候補なしは `{"status": "empty"}`（exit 0)
   - `queue verify --queue-id <id> --current-base-sha <sha>`: candidate の `accepted_base_sha == current-base-sha` なら exit 0。**不一致なら status を `invalidated` に更新して exit 2** + HINT（base 統合 → refreeze（--head-sha 更新で再 enqueue）→ fresh review の3手順を stderr に出力）
   - `queue mark --queue-id <id> --status merged|invalidated|superseded [--reason <text>]`: 状態遷移。`merged` への遷移は queued/ready からのみ許可（不正遷移は exit 2）
   - すべて lease 不要（session state の mutation ではない）。`.mission-state` 不在は exit 2
3. **SKILL.md Phase 7 の更新**（+ state-management.md に「Merge queue」節）:
   - 並列 mission（同一 state root に複数 active implementer）で PR を merge する場合: pass 後に `queue enqueue` → 自分の entry が `queue next` で返り、かつ merge 直前に live base sha で `queue verify` が exit 0 のときだけ merge を実行 → merge 後 `queue mark --status merged`
   - `verify` が exit 2（base 移動検出）なら: base 統合 → 新 head で refreeze → fresh review 取得 → `queue enqueue`（再登録）からやり直す
   - 単独 mission（queue に他 entry なし）は従来どおり queue を使わなくてよい（後方互換）
4. **テスト** `skills/mission/tests/test_issue424_merge_queue.py`:
   - enqueue → next → verify(一致) → mark merged の happy path
   - depends_on 未 merge の entry が next で返らない / merge 後に返る
   - verify 不一致 → invalidated + exit 2 + HINT 文言
   - 同一 issue_ref の再 enqueue で旧 entry が superseded
   - 不正遷移（merged → merged 等）exit 2
   - 並行 enqueue の排他（flock）・破損 queue ファイルは exit 2（fail-closed。**pregate と違い merge 調整は安全側 = 停止**）
   - sha 形式検証（40hex 以外拒否）・issue_ref sanitize
5. **plugins ミラー同期** + SYNC_PAIRS 登録

やらないこと:

- 実際の merge 実行・PR 状態取得（`gh` 呼び出し等）— オーケストレーター側の責務
- 兄弟 Issue の自動 dispatch（並列起動そのものは既存能力。起動戦略は SKILL.md の運用ガイダンスに留める）
- レビュー accepted の revision_scope（score_history 内）との自動突合（将来 Issue。v1 は enqueue 時に orchestrator が accepted_base_sha を申告する）

## 受け入れ条件（検証可能形式）

1. enqueue/status/next/verify/mark の5コマンドがテストで通る（上記テストリスト全件）
2. verify の base 不一致で entry が invalidated になり、exit 2 + refreeze 3手順の HINT が出る
3. depends_on による順序制御が機能する
4. 破損 queue ファイルで mutating 操作が fail-closed（exit 2）
5. 既存 gate / lease / state 意味論への影響ゼロ（既存テスト全緑）
6. SKILL.md Phase 7 / state-management.md 更新（修正履歴含む）、SYNC_PAIRS 登録・ミラー同期
7. `make test` 全緑（既知の stop-guard 系 14 件の環境失敗を除く）

## 実装メモ

- queue_id は entry 内容の sha256 先頭 16 hex + 時刻で衝突回避（Date 依存は evaluated 時刻フィールドのみ・テストは注入可能に）
- `MISSION_STATE_NOW` 環境変数によるテスト時刻注入の既存慣習に合わせる（pregate テストで使用済み）
- ドキュメント・fixture に実 home パス・ベンダー語彙を含めない
