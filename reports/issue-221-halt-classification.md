# Issue #221 halted-run 重要度分類

## 目的

監査の生の halt 観測を失わず、現在の P1 `halted-runs` と品質率を、実際に原因調査が必要な halt だけから導出する。

## 分類契約

| 監査分類 | 代表的な構造化カテゴリ・証跡 | P1 / 品質率への扱い |
| --- | --- | --- |
| `actionable` | `stagnation`、`stale`、未知・曖昧な halt、解消証跡のない `blocked-external` | 含める |
| `awaiting-external` | `awaiting-approval`、承認・資格情報・rate limit の明示的な外部待ち | 除外する |
| `delegated` | `partial-done` | 除外する |
| `superseded-resolved` | `resolution_status` が `resolved` / `superseded` / `closed` | 除外する |
| `user-aborted` | `user-abort` | 除外する |

未知の状態は誤って隠さないよう `actionable` に倒す。解消・置換は構造化された `resolution_status`、または `superseded by` など対象を限定した履歴互換 marker のみを根拠にし、曖昧な完了表現では除外しない。

## テスト一覧

1. 生の halt 数は全分類を保持する。
2. `partial-done`、`awaiting-approval`、`user-abort` は P1 と actionable 品質率の分母から除外する。
3. `stale`、`stagnation`、`other`、未知カテゴリは actionable に残す。
4. `blocked-external` は明示的な承認・資格情報・rate limit 待ちだけを除外し、競合ゲートや未知理由は actionable に残す。
5. `resolution_status` による解消・置換証跡は non-actionable とし、自由文だけの完了主張は actionable に残す。
6. current / historical の finding 区分を維持する。
7. JSON と Markdown に raw / actionable の件数・率・内訳を出す。
8. canonical / plugin mirror、artifact hygiene、vendor fingerprint を維持する。

## 検証ログ

- 変更前 baseline: `skills/mission/tests/test_mission_audit.py` は 58 passed。
- pytest の一時ディレクトリ cleanup warning は発生したが、テスト失敗はなし。
- Red: actionable 件数・期間区分・Markdown 表示の 3 回帰テストが期待どおり失敗。
- Green: `test_mission_audit.py` は 61 passed、finding registry を含む対象テストは 62 passed。
- 初回全体検証: registry fixture の旧 `pass_rate` 直書きにより 1 failed / 1207 passed。fixture を registry の source key 参照へ修正。
- 修正後全体検証: 1208 passed。pytest の一時ディレクトリ cleanup warning のみで失敗なし。
