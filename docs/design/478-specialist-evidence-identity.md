親 Issue: #473（Wave 1 / 依存順 4 番目・P1）

# 概要

specialist evidence の保存先パスが `iteration` + `mission_id` 先頭 8 文字 + skill slug だけで決まる。**同一 skill を同一 iteration 内で複数回 invoke すると同じパスになり、前の evidence が上書きされる**。state 側は path 文字列しか持たないため、上書き後は「どの invocation の evidence だったか」を一意に参照できない。

`invocation_id` フィールドは既に state に存在するのに、パスにも digest にも使われていないのが本質。

# 一次証拠（現在の main）

`skills/mission/bin/mission-state.py`:

- L4422–L4428 `_planned_specialist_archive_path`: `archive_dir / f"iter-{iteration}-{gid}-specialist-{skill_slug}.md"`。invocation identity なし
- 呼び出し元 2 箇所: L4496（`_commit_specialist_state_with_archive` 内、evidence 書き込み時）、L4917–L4920（`_preflight_specialist_invocation_state` 内、予約時点で `pending_entry["evidence_path"]` を確定）
- L4457–L4471 `_publish_staged_specialist_archive`: 既存ファイルを `.previous.` へ退避して `os.replace` で上書き。L4516–L4517 で成功後に `previous.unlink()` して旧 evidence を削除する
- L4797–L4808 / L5031–L5039: `specialist_invocations` の各エントリは `invocation_id`（`new_invocation_id()` 生成）を持つ。**`evidence_path` は path 文字列のみで digest フィールドが無い**（L4258 の summary item も同様）

複数 invoke が可能な根拠: `skills/mission/lib/provider_eligibility.py` L798–L814。`max_calls_per_iteration` が未設定なら回数制限は掛からない。planning 段階の `seen_skills` 集合（L3967 / L3974）は planning phase の dedup のみで、`log-invocation` 実行時には強制されない。

# 変更内容

evidence を invocation identity + content digest の immutable reference にする（親 Issue 設計原則 5）。

1. `_planned_specialist_archive_path` のファイル名に `invocation_id`（短縮形でよいが衝突しない長さ）を含める
2. `specialist_invocations` のエントリに evidence の content digest（`sha256:` 形式）を記録する。既存の `evidence_path` は残してよいが、**参照の一意性は digest が担保する**
3. 既存 evidence を上書きする経路（`_publish_staged_specialist_archive` の `.previous.` 退避 → `unlink`）を、上書きが起きない前提に合わせて見直す。同一 path への再書き込みが発生した場合は fail-closed にする
4. 既存 state（invocation_id なしの古い evidence_path）を読めること。過去の記録を rewrite しない

## やらないこと

- specialist の選定ロジック・eligibility 判定の変更
- `max_calls_per_iteration` の既定値変更や回数制限の追加
- evidence ファイルの中身・フォーマットの変更
- provider に pass/review/score authority を与える変更（親 Issue Non-scope）

# 受け入れ条件

- [ ] 同一 skill・同一 iteration で 2 回 invoke しても、evidence ファイルが別パスに保存され両方残る
- [ ] `specialist_invocations` の各エントリから、その invocation の evidence を digest で一意に特定できる
- [ ] 記録された digest が実ファイルの内容と一致する（検証経路があること）
- [ ] 既存の（invocation_id を含まない）evidence_path を持つ legacy state を読んでもエラーにならず、physical rewrite も起きない
- [ ] plugins ミラー一致・既存テスト全緑

# テストリスト

`skills/mission/tests/test_specialist_invocations.py` へ追加。

1. 同一 skill を同一 iteration で 2 回 invoke → evidence が 2 ファイル残り、パスが異なる（Red になるはず。再現テスト）
2. 各 invocation の state エントリの digest が、対応するファイル内容の sha256 と一致する
3. 同一 path への再書き込みが発生する状況を作った場合、fail-closed で拒否され既存ファイルが変化しない
4. legacy state（invocation_id なしの evidence_path）を読み込んで `specialists summary` / `accounting` が動作し、state が書き換わらない
5. 異なる iteration・異なる skill でパスが衝突しないこと

**既存テスト `test_log_invocation_archives_evidence_with_metadata`（L254）は `iter-1-abc12345-specialist-dev-code-reviewer.md` というパス形式を期待値にハードコードしている（L271 / L280）。パス形式を変えるとこのテストは必ず落ちるので、同一 PR で期待値を更新すること**（テストを消すのではなく、新形式に合わせて更新する）。

# 実装上の注意（過去のレビュー指摘より）

- TDD（Red → Green → Refactor）。再現テストが Red になることを先に確認する
- parametrize に lambda を値として入れない。入れる場合 `ids=str` は使わず明示 ids を書く（メモリアドレス入り id が pytest-xdist の collection 不一致を起こす）
- コードコメントは「何をしているか」ではなく、コードから読み取れない制約・意図だけを書く
- `skills/mission/` を変更したら `plugins/mission/skills/mission/` へ `cp` でミラー同期する
- CI Quality と同じセット（`test_artifact_hygiene` `test_vendor_fingerprint` `test_plugins_in_sync` `test_codex_wrapper_sync` `test_actions_cost_guard` `test_doc_consistency`）をローカルで回してから push する
