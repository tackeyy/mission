親 Issue: #473（Wave 2 / 依存順 8 番目・P2）

# 概要

state schema は v4 まで進んでいるが、未知バージョンの扱いが **fail-open** になっている。将来 v5 を導入するとき、旧 reader が新形式を「legacy」と誤認して静かに緩い経路へ倒れる危険がある。v1〜v4 と未知バージョンの互換範囲を golden snapshot で固定し、未知バージョンは fail-closed にする。

# 一次証拠（現在の main）

- `skills/mission/bin/mission-state.py` L241: `SCHEMA_VERSION = 4`（v4 = structured scoring provenance が必須）
- L9980–L9982 `_is_new_provenance_state`:

```python
def _is_new_provenance_state(data: dict) -> bool:
    return isinstance(data.get("schema_version"), int) and data["schema_version"] >= 4
```

`schema_version` が非 int または欠損だと `False` を返して legacy 経路へ落ちる。**未知の将来バージョン（例: 文字列 `"5"`、あるいは想定外の型）も legacy 扱いになる = fail-open**

- L1478: `data.setdefault("schema_version", SCHEMA_VERSION)`（新規 init は v4）
- v1/v2 の互換導出は `derive_terminal_outcome`（`mission_common` から import、L73）が読み取り時に行い、物理 rewrite しない

既存テスト:

- `skills/mission/tests/test_terminal_outcome.py::test_legacy_states_are_derived_without_physical_rewrite`（L266、schema_version 1/2/3 を parametrize、L268–L336）
- `skills/mission/tests/test_stats.py::test_stats_reads_legacy_state_json`（L413）
- `skills/mission/tests/test_issue209_review_tier_negation.py::test_legacy_state_without_details_supports_set_next_and_get`（L1063）

いずれも**未知の将来バージョンや不正な型のケースを検証していない**。

# 変更内容

1. v1 / v2 / v3 / v4 それぞれの代表 state を **golden snapshot** としてフィクスチャ化する
2. 各バージョンの読み込み結果（read-normalize 後の派生値: terminal_outcome、pass 判定に使う値、stats 集計対象など）を snapshot と突合するテストを作る
3. **未知バージョンを fail-closed にする**
   - `schema_version` が `SCHEMA_VERSION` より大きい整数 → 明示的に拒否し、何が起きたか分かるエラーを返す（黙って legacy 扱いにしない）
   - `schema_version` が非 int（文字列・null・浮動小数）→ 拒否
   - `schema_version` 欠損は既存 legacy 互換を維持する（v1 相当。ここを壊すと既存 state が読めなくなる）
4. v4 reader が v5 writer の出力に遭遇した場合の期待挙動を、テストで明文化する

## やらないこと

- v5 スキーマの設計・導入（本 Issue は v4 までの固定と未知の拒否まで）
- 既存 state の物理 rewrite / マイグレーション実行
- `derive_terminal_outcome` の導出ルール変更
- schema_version 欠損時の挙動変更（legacy 互換を維持する）

# 受け入れ条件

- [ ] v1 / v2 / v3 / v4 の golden snapshot が存在し、読み込み結果が固定されている
- [ ] `schema_version` が 5 以上の state が fail-closed で拒否され、理由が分かるエラーになる
- [ ] `schema_version` が非 int の state が fail-closed で拒否される
- [ ] `schema_version` 欠損の legacy state は従来どおり読める
- [ ] 拒否時に state が書き換わらない
- [ ] 既存テスト全緑・plugins ミラー一致

# テストリスト

1. v1 / v2 / v3 / v4 の golden snapshot 読み込み → 派生値が期待どおり（parametrize。**lambda を値に入れない**）
2. `schema_version = 5` → 拒否され、state 未変更（Red になるはず。現状は legacy 扱いで通ってしまう）
3. `schema_version = "4"`（文字列）→ 拒否
4. `schema_version = null` / 欠損 → legacy として従来どおり読める
5. 拒否が起きるコマンドの範囲を明示的に検証する（read-only の `get` / `next` と mutating command で挙動が違うなら、その差も固定する）

# 実装上の注意（過去のレビュー指摘より）

- TDD（Red → Green → Refactor）。再現テストが Red になることを先に確認する
- parametrize に lambda を値として入れない。入れる場合 `ids=str` は使わず明示 ids を書く（メモリアドレス入り id が pytest-xdist の collection 不一致を起こす）
- コードコメントは「何をしているか」ではなく、コードから読み取れない制約・意図だけを書く
- `skills/mission/` を変更したら `plugins/mission/skills/mission/` へ `cp` でミラー同期する
- CI Quality と同じセット（`test_artifact_hygiene` `test_vendor_fingerprint` `test_plugins_in_sync` `test_codex_wrapper_sync` `test_actions_cost_guard` `test_doc_consistency`）をローカルで回してから push する
