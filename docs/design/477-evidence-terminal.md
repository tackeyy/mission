親 Issue: #473（Wave 0 / 依存順 3 番目・P1）

# 概要

`mark-halt --category evidence-submitted` は checker / planning / analyze ロールにとって**正規の成功終了**であり、`terminal_outcome` は `completed_evidence`（成功 terminal）になる。ところが `next` が返す `next_action` は `report-blocker` で、control-plane API 上は失敗扱いになる。証拠提出で終わる従属役割の run が、成功したのに blocker として報告される。

# 一次証拠（現在の main）

## blocker 化している箇所

`skills/mission/bin/mission-state.py` L8491–L8510 `_derive_next_action`:

```python
halt_reason = data.get("halt_reason") or ""
if halt_reason:                       # halt_reason が空でなければ全て blocker
    halt_category = data.get("halt_category")
    ...
    return {"next_action": "report-blocker", ...}
```

`halt_category == "evidence-submitted"` の判定も `terminal_outcome` の参照も無い。`stale` だけが recovery hint を分岐しており、それ以外は一律 blocker。

## 成功 terminal である根拠

`skills/mission/lib/mission_common.py`:

- L42–L52: `terminal_outcome` の値集合。`completed_pass` と `completed_evidence` が成功 terminal
- L98–L103 `derive_terminal_outcome`: `category == "evidence-submitted"` かつ role が `EVIDENCE_COMPLETION_ROLES`（`checker` / `planning` / `analyze`）なら `completed_evidence`、それ以外なら `incomplete`

## 消費側

- `skills/mission/SKILL.md` L34: `report-complete` か `report-blocker` 以外では final を返さない、というルール
- `skills/mission/bin/mission-state.py` L8979: `fallback_available = state_active and next_action not in {"init", "report-blocker", "report-complete"}`
- `scripts/mission-stop-guard.sh` L420: block reason に `next` を案内

# 変更内容

`_derive_next_action` が `halt_reason` を一律 blocker 化する前に `terminal_outcome` を評価し、成功 terminal を別の `next_action` へ分岐させる。

- 新しい `next_action` の値として `report-terminal` を追加する（親 Issue の roadmap 3 に準拠）。`completed_evidence` のときこれを返す
- summary / command_hint は「証拠提出で正常終了した」ことが分かる文言にする。`reactivate --approved-by-user` の案内は出さない（正常終了に対する回復案内は誤誘導）
- `fallback_available`（L8979）の除外集合に新値を加え、終了済み state で fallback を提案しないこと
- `SKILL.md` の終了判定ルールを更新し、`report-terminal` でも final を返してよいことを明記する（ただし `passes=true` を主張しない）

## やらないこと

- `terminal_outcome` の値集合・導出ロジックの変更
- `EVIDENCE_COMPLETION_ROLES` の変更
- `completed_pass`（`report-complete`）の挙動変更
- `stale` / 手動 halt の分岐と recovery hint の変更
- pass-rate 統計の集計定義の変更

# 受け入れ条件

- [ ] role=checker/planning/analyze で `mark-halt --category evidence-submitted` した state の `next` が `report-terminal` を返す
- [ ] 同 state で `reactivate` の案内が出ない
- [ ] role がそれ以外（`incomplete` になるケース）では従来どおり `report-blocker`
- [ ] `stale` halt・手動 halt・`blocked_external`・`awaiting_approval` の挙動が不変
- [ ] `passes=true` の `report-complete` が不変
- [ ] `SKILL.md` の終了判定記述が新しい値を含む
- [ ] plugins ミラー一致・既存テスト全緑

# テストリスト

`skills/mission/tests/test_adr002_next_command.py` へ追加（**現在、成功 terminal が blocker 化する矛盾を突くテストが存在しない**）。

1. role=checker + `evidence-submitted` → `next_action == "report-terminal"`、hint に `reactivate` を含まない（Red になるはず。再現テスト）
2. role=planning / analyze でも同様
3. role=implementer + `evidence-submitted` → `terminal_outcome` は `incomplete` なので従来どおり `report-blocker`
4. `stale` halt → 従来どおり `report-blocker` + `resume` hint（既存 `test_next_stale_halt_suggests_resume_not_manual_reactivate` L68 の維持）
5. 手動 halt → 従来どおり `report-blocker` + reactivate hint（既存 L50 の維持）
6. `passes=true` → `report-complete`（既存 L84 の維持）
7. `fallback_available` が `report-terminal` の state で false になること

参考: `skills/mission/tests/test_terminal_outcome.py::test_role_and_halt_category_map_to_exclusive_terminal_outcomes`（L82）が role × category の対応表を既に検証している。next_action 側はこの表と整合する必要がある。

# 実装上の注意（過去のレビュー指摘より）

- TDD（Red → Green → Refactor）。再現テストが Red になることを先に確認する
- parametrize に lambda を値として入れない。入れる場合 `ids=str` は使わず明示 ids を書く（メモリアドレス入り id が pytest-xdist の collection 不一致を起こす）
- コードコメントは「何をしているか」ではなく、コードから読み取れない制約・意図だけを書く
- `skills/mission/` を変更したら `plugins/mission/skills/mission/` へ `cp` でミラー同期する
- CI Quality と同じセット（`test_artifact_hygiene` `test_vendor_fingerprint` `test_plugins_in_sync` `test_codex_wrapper_sync` `test_actions_cost_guard` `test_doc_consistency`）をローカルで回してから push する
