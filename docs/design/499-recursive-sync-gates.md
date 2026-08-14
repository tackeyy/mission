親 Issue: #473（Wave 3）
設計: `docs/adr/005-typed-mission-kernel-and-unit-of-work.md` / `docs/design/485-typed-kernel-migration-plan.md`（#485 で確定）

本 Issue は移行計画 Section 8 の **D1** に対応する。依存順は同 Section 9 を正とする。

Dependencies: #485 design only.

Current code: the explicit `SYNC_PAIRS` list in
`test_plugins_in_sync.py:85` onward and explicit `TARGETS` in
`test_issue99_py39_compat.py:19` onward do not discover new library modules.

Expected behavior: define one deterministic recursive inventory for canonical
Mission Python packages and their plugin mirrors. Every production `.py` module
in scope must have an identical mirror, parse with the supported Python grammar,
and be importable from both canonical and plugin roots without importing a
maintainer-local path.

TDD Red:

- add an unlisted canonical fixture module and prove sync fails for missing
  mirror rather than silently ignoring it;
- alter one mirrored byte and prove the reported pair is exact;
- add unsupported syntax/import to a discovered fixture and prove the Python
  compatibility gate fails;
- import the package from canonical and plugin roots in isolated subprocesses;
- reject symlinked or path-escaping inventory entries.

Acceptance:

- later kernel/application/persistence modules require no hand-maintained
  per-file test target to receive sync and compatibility coverage;
- current `SYNC_PAIRS` behavior and plugin distribution tests remain green;
- no kernel or persistence behavior is introduced by this Issue.

# 実装上の注意

- TDD（Red → Green → Refactor）。上記 TDD Red が実際に落ちることを先に確認する
- 設計（ADR-005 / 移行計画）に書かれていない仕様を足さない。必要と判断したら実装せず報告する
- 既存テストの期待値を書き換えて緑にしない。契約変更で変わる場合は理由を PR に明記する
- parametrize に lambda を値として入れない。入れる場合 ids は明示する
- `skills/mission/` を変更したら `plugins/mission/skills/mission/` へ `cp` でミラー同期する
- 現行の安全境界（fenced lease / strict file validation / content-addressed evidence / provider isolation / 機械的 pass gate）を弱めない
