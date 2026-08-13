# Design: invalid-input エラーの自己修復ガイダンス（Issue #423）

## 目的

`mission-state.py` の invalid-input / expected-gate 失敗時に、エージェントが**1回で正しい再呼び出しに到達できる**エラー出力にする。実測（command-outcomes telemetry, 2026-08-12 実運用）では 17 呼び出し中 10 件が invalid-input（advance ×5 / review-finalize ×4 / review-import ×3 相当）で、失敗ごとにモデルターンが浪費されている。

## スコープ

やること:

- 共通ガイダンス経路の新設: `skills/mission/lib/error_guidance.py`（新規）+ `mission-state.py` の失敗終端ヘルパー
- 頻出失敗サイトへのガイダンス付与（下表）
- `--json` 失敗出力（`_emit_json_command_failure`، 行 10137 付近）への `guidance` フィールド追加
- session-id の pid フォールバック時の stderr 警告追加
- command-outcome レコードへの `guidance: true` 付与（前後比較用）
- テスト `skills/mission/tests/test_issue423_error_guidance.py` 新規

やらないこと:

- exit code 規約の変更（1=前提欠落 / 2=バリデーション・ゲートは不変）
- ゲート意味論・lease 検証ロジックの変更（メッセージのみ強化。**lease token の値は出力しない**）
- サブコマンドの追加・引数仕様の変更
- 既存エラーメッセージ本文の削除（末尾に HINT / 例を追記する方式。既存テストの部分一致を壊さない）

## インターフェース定義

### lib/error_guidance.py

```python
def build_guidance(command: str, reason: str, context: dict) -> list[str]:
    """失敗理由コードから HINT 行（1〜3行）を返す。context には state 由来の実値
    (session_id, iteration, phase, 既知の archive path 等) を渡し、例示コマンドに埋め込む。"""
```

- 出力形式（stderr、既存 ERROR 行の直後）:

```
ERROR: <既存メッセージそのまま>
HINT: <原因の1行説明>
HINT: 正しい呼び出し例: mission-state.py review-finalize --iteration 1 --input-ref .mission-state/archive/iter-1-xxxx-review-input-yyyy.json --min-reviewers 2
```

- `--json` 時は `{"ok": false, "outcome_kind": ..., "guidance": ["<HINT行>", ...]}` を追加

### ガイダンス対象サイト（優先順・実測頻度順）

| コマンド | 失敗ケース（行番号は現行 main） | 追加する HINT の内容 |
|---|---|---|
| advance | terminal phase 遷移（7804-7810） | mark-passes / mark-halt の実行例（現 phase 実値入り） |
| advance | `--activity` 形式不正（7820-7826） | 現 phase の既定 activity での正しい例 |
| advance | canonical plan なし（7848-7849） | plan-import または planning 経路の次の一手 1 行 |
| advance | producing の artifact 引数欠落（7874-7878） | `--artifact-applicability not-applicable` と producing 両方の完全例 |
| review-finalize | input-ref 欠落（11893-11895） | state / archive から発見できた最新 `review-input-*.json` パスを埋めた完全例（発見不能ならプレースホルダ例） |
| review-finalize | min-reviewers 不足（11929-11935） | 現 state の reviewer_count 実値入りの `--min-reviewers` 例 |
| review-finalize | resubmit-reason 欠落 | `--resubmit-reason` 付き例 |
| review-import | スキーマ違反（11325-11336） | `mission-review/1` の必須トップレベルキー一覧 1 行 + `--stdin` 渡しの例 |
| lease | `LeaseRejectedError` / MISSION_LEASE_ID 不一致（776-778） | 「init が返した lease_id を `MISSION_LEASE_ID` として渡す」定型 1 行（**token 値は出力しない**） |
| set | reviewer_count / halt_category / halt_reason の直接 set（8759-8785） | 正しい代替コマンド例（既存メッセージに例が薄いもののみ） |
| session 解決 | pid フォールバック（`resolve_session_id`، 664-677） | 初回フォールバック時に stderr へ `WARNING: MISSION_SESSION_ID 未設定のため pid フォールバックを使用...` 1 行 |

### telemetry 前後比較

- 新経路で emit した失敗の command-outcome レコード（sidecar / state 両方）に `"guidance": true` を付与する
- 既存レコード（フィールドなし）= 改善前、`guidance: true` = 改善後として、invalid-input 率の前後比較が `stats` / sidecar 走査で可能になる
- `command_outcomes.py` の schema は `mission-command-outcomes/1` のまま、optional フィールド追加のみ（後方互換）

## 受け入れ条件（検証可能形式)

1. 上表の各失敗ケースで、stderr に `HINT:` 行と実行可能な例示コマンドが出る（テストで文字列アサート）
2. 例示コマンドには可能な範囲で state の実値（iteration、archive パス、phase）が埋まる。state 不在時はプレースホルダ例に fallback してクラッシュしない
3. `--json` 失敗出力に `guidance` 配列が含まれる
4. lease 系 HINT に lease token の値が含まれない（テストで否定アサート）
5. pid フォールバック時に WARNING が 1 回だけ出る（正常系の stdout 汚染なし）
6. 新経路の command-outcome レコードに `guidance: true` が付く
7. 既存テストが全緑（`make test`）— 既存メッセージは削除せず追記のみのため
8. exit code は全ケースで従来と同一

## テストリスト（test_issue423_error_guidance.py）

- test_advance_terminal_phase_hint_includes_mark_commands
- test_advance_activity_format_hint_includes_valid_example
- test_advance_missing_canonical_plan_hint
- test_advance_producing_artifact_hint_shows_both_forms
- test_review_finalize_missing_input_ref_hint_embeds_archive_path
- test_review_finalize_min_reviewers_hint_uses_state_value
- test_review_import_schema_rejection_hint_lists_required_keys
- test_lease_mismatch_hint_never_prints_token
- test_pid_fallback_emits_warning_once
- test_json_failure_output_contains_guidance_array
- test_command_outcome_record_marks_guidance_true
- test_guidance_fallback_when_state_missing

## 実装メモ

- テストは conftest.py の `run_cli` fixture（MISSION_SESSION_ID/MISSION_LEASE_ID 自動注入）を使う。lease 不一致テストは env を明示上書きして再現する
- 変更サイトが分散するため、`_fail_with_guidance(args, message, *, command, reason, context, outcome_kind="invalid-input", exit_code=2)` のような単一終端ヘルパーを mission-state.py に置き、各サイトは 1〜2 行の置換に抑える
- 文言は既存の日本語 ERROR 慣習に合わせる。HINT のコマンド例は `mission-state.py <sub> ...`（パス無し）表記で統一
- 個人パス・実 home パスを含めない（test_artifact_hygiene.py が全 tracked file を走査する）
