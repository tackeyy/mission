# Design: Pre-Gate — planning gate/checker 評価の事前計算キャッシュ（Issue #421）

## 目的

mission 実行時にクリティカルパス上で走る planning ゲート評価（size gate / planning checker 等の外部 evidence provider 評価）を、Issue 起票時などに**事前計算**し、mission init/planning 時はキャッシュ済み evidence を参照するだけで先へ進めるようにする。実測では mission 開始前の儀式（Issue 精査・gate 実行・split 対応・再 init）に約15分かかっていた（2026-08-13 実運用ログ）。

## 設計方針（重要）

- **canonical plan の provenance 機構（plan-import / promote / `_trusted_canonical_plan_binding` / `canonical_plan_identity`）には一切触れない。** pregate は plan とは別オブジェクト（ゲート評価の evidence 記録）であり、独立した sidecar として導入する
- **fail-safe**: キャッシュ miss / stale / 破損時は従来どおり実行時評価に落ちる（キャッシュは高速化のみ、正しさはゲート本体が保証）
- **OSS-portable**: 特定リポジトリのゲート名・コマンドをコアに焼き込まない。evaluation の中身（verdict / evidence 参照）は汎用 JSON

## スコープ

やること:

1. **`skills/mission/lib/pregate_cache.py`** 新規（ロジックは全てここ）:
   - 置き場所: `<project>/.mission-state/pregate/<issue_ref_key>.json`（issue_ref キー・上書き更新。tmp+rename の atomic write）
   - envelope schema `mission-pregate-evaluation/1`:
     ```json
     {
       "schema": "mission-pregate-evaluation/1",
       "issue_ref": "979",
       "subject_digest": "sha256:<ゲート対象スナップショット（Issue本文等）の正規化digest>",
       "evaluated_at": "<ISO8601 UTC>",
       "ttl_hours": 72,
       "verdict": "accepted" | "split-required" | "rejected",
       "gate_id": "<評価したゲートの識別子（自由形式slug）>",
       "evidence_refs": [{"kind": "url|path", "value": "..."}],
       "producer_session": "<session id または空文字>",
       "payload": {"任意のJSON": "ゲート固有の詳細"}
     }
     ```
   - `record(cwd, evaluation)` / `lookup(cwd, issue_ref, subject_digest, now)` → `{status: "hit"|"miss"|"stale", record?}`。stale 条件: subject_digest 不一致 **または** `evaluated_at + ttl_hours` 超過。破損 JSON・schema 違反は "miss" 扱い（fail-safe、例外にしない）
   - symlink / 非正規ファイル拒否は evidence_handoff.py と同じ検査を適用
2. **`mission-state.py` にサブコマンド `pregate` を追加**（登録とディスパッチの最小行）:
   - `pregate record --issue-ref <ref> --input <evaluation.json|-` : lease 不要（state mutation ではない）。`.mission-state` 不在は exit 2。検証済み envelope を書いて `{path, subject_digest}` を stdout
   - `pregate check --issue-ref <ref> --subject-digest sha256:<hex> [--json]` : read-only。hit なら exit 0 + record 出力、miss/stale なら **exit 0** で `{"status": "miss"|"stale"}`（gate ではないので非0にしない）
   - `init --issue-ref <ref>` 時: fresh な pregate record があれば state に `pregate: {path, subject_digest, verdict, gate_id, evaluated_at}` を記録する（**参照のみ。verdict によって init の挙動・routing は変えない**）。lookup には subject_digest が必要だが init 時点では不明なため、init では digest 照合なしの「存在参照」に留め、`pregate.subject_digest` を orchestrator が後で `pregate check` により照合する旨を設計上明記
3. **refs / SKILL.md 更新**:
   - `refs/state-management.md` に「Pre-Gate evaluation cache」節を追加（schema・stale 規則・fail-safe・「verdict はゲート本体の代替ではなく再評価スキップの根拠」という位置付け）
   - `SKILL.md` の Phase 0-1 節に 1〜2 行: issue_ref 付き mission では planning 前に `pregate check` を行い、hit なら該当ゲートの再実行を省略して evidence_refs を planning 成果物に引用する。miss/stale なら従来どおり評価し、評価後に `pregate record` で保存する
4. **テスト** `skills/mission/tests/test_issue421_pregate_cache.py`（conftest の `run_cli` / `state_dir` fixture、中立な fixture 名）:
   - hit / miss（未記録）/ stale（digest 不一致）/ stale（TTL 超過）の4経路
   - record→check 往復、上書き更新（同 issue_ref の再 record）
   - 破損 JSON は miss（クラッシュしない）
   - init が pregate 参照を state に記録する／pregate 不在でも init は従来どおり
   - path traversal 拒否（issue_ref key のサニタイズ: 英数と `-_` 以外は `_`）
5. **plugins ミラー同期** + `test_plugins_in_sync.py` の SYNC_PAIRS に `pregate_cache.py` を登録

やらないこと:

- ゲート評価そのものの実行・スケジューリング（事前実行のトリガーはホスト側の運用に委ねる。cron / issue 起票 hook 等はこの Issue の範囲外）
- canonical plan / plan-import / promote / executor-handoff の変更
- verdict に基づく init の自動 routing・split 処理（将来 Issue。v1 は参照記録と再評価スキップの根拠提供まで)
- fencing epoch との連動（pregate は invocation 結果ではなく evidence 記録であり、stale 判定は digest + TTL で行う）

## 受け入れ条件（検証可能形式）

1. `pregate record` → `pregate check` の hit / miss / stale（digest・TTL）4経路がテストで通る
2. 破損キャッシュで check が miss を返しクラッシュしない（fail-safe）
3. `init --issue-ref` が fresh record を state の `pregate` フィールドに記録し、不在でも挙動不変
4. gate 意味論・exit code・routing への影響ゼロ（check は常に exit 0、record の入力不正のみ exit 2）
5. SYNC_PAIRS 登録・plugins ミラー同期（test_plugins_in_sync.py green）
6. refs / SKILL.md 更新（修正履歴テーブルへの追記を含む）
7. `make test` 全緑（既知の stop-guard 系 14 件の環境失敗を除く）

## 実装メモ

- issue_ref key のサニタイズは state 側の `issue_ref_key` 生成ロジック（mission-state.py の init 周辺）と整合させる
- evidence_handoff.py（#422）の atomic write / 安全検査パターンを踏襲する
- `evaluated_at` / TTL 比較は UTC。`now` はテスト注入可能にする（引数 default `None` → 現在時刻）
- ドキュメント・fixture に実 home パス・ベンダー語彙を含めない（hygiene / fingerprint テストが全 tracked file を走査）
