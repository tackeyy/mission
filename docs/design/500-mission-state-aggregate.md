親 Issue: #473（Wave 3）
設計: `docs/adr/005-typed-mission-kernel-and-unit-of-work.md` / `docs/design/485-typed-kernel-migration-plan.md`（#485 で確定）

本 Issue は移行計画 Section 8 の **K1** に対応する。依存順は同 Section 9 を正とする。

Dependencies: D1.

Current code: schema/version loader at `mission-state.py:241-270`; lease dict at
`:560-908`; phase enum at `:1786-1816`; initial dict at `:6953-7075`;
terminal outcome in `mission_common.py`; schema snapshots in
`test_issue483_schema_compat_matrix.py`.

Expected behavior: define closed Phase, TerminalOutcome, Plan, Handoff, Review,
Finding, Score, and Lease types; decode missing/v1-v4 without write; reject
future/non-integer schemas, partial leases, unknown v5 variants, and v5 Finding
statuses other than `open|resolved`.

TDD Red:

- import the current plan/handoff/review/score/lease/terminal fixture corpus,
  not only #483's minimal version fixtures, into the canonical typed view;
- preserve `prepared|consuming|consumed|rejected` handoffs and all authoritative
  lineage fields; retain unowned legacy fields for v4 passthrough;
- map missing or arbitrary ignored legacy Finding status to `open`; accept only
  `open|resolved` in v5, require prior identity/evidence/time on `resolved`, and
  prove no migration command can emit `resolved`;
- reject `accepted-risk` and `not-reproducible` explicitly in v5;
- reject bool/string/float/null/future versions and partial leases;
- prove decoding does not change source bytes;
- round-trip representative v5 values through the canonical codec; reject
  duplicate keys, invalid UTF-8, `NaN`/`Infinity`, trailing data, oversize,
  unknown keys, links, FIFOs, hard links, and identity swaps.

Acceptance:

- new code is under `skills/mission/lib/` with no CLI route;
- all v1-v4 golden results and terminal outcomes match current behavior;
- no production state or evidence file changes;
- D1 recursive Python 3.9/import and plugin mirror gates pass.

# 実装上の注意

- TDD（Red → Green → Refactor）。上記 TDD Red が実際に落ちることを先に確認する
- 設計（ADR-005 / 移行計画）に書かれていない仕様を足さない。必要と判断したら実装せず報告する
- 既存テストの期待値を書き換えて緑にしない。契約変更で変わる場合は理由を PR に明記する
- parametrize に lambda を値として入れない。入れる場合 ids は明示する
- `skills/mission/` を変更したら `plugins/mission/skills/mission/` へ `cp` でミラー同期する
- 現行の安全境界（fenced lease / strict file validation / content-addressed evidence / provider isolation / 機械的 pass gate）を弱めない

