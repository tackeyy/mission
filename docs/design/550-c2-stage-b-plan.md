# #550 C2 Stage B 設計方針（オーケストレータ作成）

## ゴール
残 15 mutating command を repository 経由へ移行し、`C2_DIRECT_WRITE_ALLOWLIST` を**空**にする。

## バッチ分割（1バッチ=1委譲。#542 の反復地獄を避けるため一括禁止）

### Batch 1: executor-handoff 系（4コマンド）
- `executor-handoff begin` / `verify-step` / `record-step` / `complete`
- 選定理由: 4コマンドが1ドメイン・状態遷移が直線的で相互に似た構造。最初のバッチで移行の「型」を確立する。
- 完了時: allowlist から4件削除

### Batch 2: specialists 系（8コマンド）
- `recommend` / `log-invocation` / `verify-approval` / `prepare-invocation` / `invoke-command` / `invoke-prepared` / `reconcile-invocation` / `plan-import`
- 選定理由: 最多だが同一ドメイン。Batch 1 で確立した型を適用できる。provider dispatch の検証が固有。
- 完了時: allowlist から8件削除

### Batch 3: planning + manual（3コマンド）
- `planning adopt-core` / `planning promote-provider-plan` / `manual-score-capture`
- 選定理由: plan authority と scoring authority という別ドメイン。最後に残して allowlist 空化で締める。
- 完了時: **allowlist 空** + 空であることを強制するテスト追加

## 各バッチ共通の必須要件（#542 / Stage A の教訓）

1. **実 CLI・別プロセス起動テストが必須**。同一プロセス完結／時刻固定で経路を潰さない（#542 の本番 blocker 5 件はこれで漏れた）。
   - v5 session に対し `get` / `set` / `resume` など周辺コマンドも壊れていないことを確認する
2. **operation_id 冪等性の検証**: 同一 operation_id → 元の結果を replay / 異 intent → operation-intent-collision
3. **v5 で MISSION_OPERATION_ID 未指定 → exit 2 の fail-closed** テスト
4. **既存 v4 挙動が不変**であることのテスト
5. 各コマンド固有のドメイン検証（specialist 選定・plan authority・provider dispatch）を保つ
6. fencing 契約を緩めない / 既存テストの期待値を書き換えない
7. `skills/` → `plugins/mission/skills/mission/` へ byte-identical ミラー（bin/lib/refs/SKILL.md。tests/ 除外）
8. Python 3.9 互換

## 移行の型（Stage A の cmd_planning_reselect に準拠）
```
sf = resolve_state_file(cwd) → target_bytes 読取 → inspect_repository_bytes
→ _compatibility_operation_arguments(require_caller = format is V5)
→ _canonical_compatibility_operation(session_id, "<command-kind>", ...)
→ _legacy_lifecycle_repository(...) または _select_repository_for_cli
→ with repository.transaction(): load() → (replay 判定) → 検証 → save()
```

## リスク
- specialists 系は provider 呼び出しの副作用があり、transaction 内での外部 dispatch の扱いに注意（外部呼び出しは transaction の外か、冪等性を operation_id で担保）
- `planning adopt-core` は canonical plan の authority を持つため、検証順序を壊すと plan 汚染につながる

## Acceptance（issue #550 準拠）
- allowlist が空（+ 空を強制するテスト）
- 15 コマンドが v5 で fenced 構造を壊さず動く
- operation_id retry が冪等
- 既存 v4 挙動が不変
- フルスイート green
