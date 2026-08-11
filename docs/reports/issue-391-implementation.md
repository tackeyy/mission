# Issue #391 implementation report

<!-- mission-artifact: generated-by=mission-state.py artifact render -->

## Metadata

- session_id: cx-019fe66e-4336-73b0-a1d3-427c1a2dbaa6
- mission_id: ed898c5baa067b36
- status: exported
- artifact_path: .mission-state/artifacts/cx-019fe66e-4336-73b0-a1d3-427c1a2dbaa6/mission-artifact.md
- redaction_status: checked
- updated_at: 2026-08-11T22:16:46Z

- required_for_pass: true

## Mission

<!-- artifact-block: timestamp=2026-08-11T22:16:42Z -->
Archive duplicateを削除せずcanonical pointer/superseded lineageでmaterializeし、CIとlocalで同一のmake test入口を提供する。

## Plan

No plan blocks recorded yet.

## Execution

<!-- artifact-block: timestamp=2026-08-11T22:16:43Z -->
resolve-archiveへcanonical pathとretention policyを追加し、content-addressed generation manifest/current pointerを公開。audit defaultはmaterialized、--forensicはfull lineage。Makefileはsmoke/full/e2eを分離しCIをmake testへ統一。

## Evidence

<!-- artifact-block: timestamp=2026-08-11T22:16:44Z -->
TDD Red: archive compaction 2 failures、make runner 2 failures。Green: new/resolve/audit 97 passed、snapshot/archive 175 passed、plugin/docs/hygiene 80 passed。Full make testはPR CIで実行する。

## Review

No review blocks recorded yet.

## Score Gate

No score has been recorded yet.

## Assumptions

<!-- artifact-block: timestamp=2026-08-11T22:16:44Z source=.mission-state/sessions/cx-019fe66e-4336-73b0-a1d3-427c1a2dbaa6-assumptions.md -->
# Issue #391 実装前提

## 目標

- archive duplicate を削除せず canonical pointer / superseded lineage で解決する。
- default audit は materialized state、`--forensic` は full lineage を読む。
- `make test-smoke` / `make test` / `make test-e2e` を決定論的な入口にし、CI も同じ entrypoint を使う。

## 実装境界

1. 既存 `worktree_archive.py` / `resolve-archive` / snapshot journal の schema と consumer を読み、generation manifest の最小追加点を固定する。
2. `test_archive_compaction.py` で duplicate canonicalization、materialized/forensic、retention 後 lineage を Red にする。
3. 共通 discovery と pointer/manifest を Green にし、source/plugin mirror を同期する。
4. Makefile とCI・README・CONTRIBUTINGを同一入口へ統一し、tree SHA / test manifest report を出す。
5. focused regression、Full CI、2名の bounded Checker、exact-head/base 確認後にmerge・cleanupする。

## 非目標

- archive ファイルの物理削除。
- system Python や開発者の既存 virtualenv への依存追加。
- Issue #391 外の schema 全面再設計。

## 停止条件

- live `origin/main` が移動した場合は統合後にfresh review。
- canonical pointer が一意に決まらない、または lineage digest が不整合な場合は fail-closed。

## Follow-ups

No follow-ups recorded.
