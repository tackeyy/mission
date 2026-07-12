# Oracle Browser Smoke Test — 2026-07-11

## 結論

Oracle の Chrome 認証経路は、既存 Chrome user-data root を `--copy-profile` で使い捨てコピーする方式で正常動作した。Browser smoke test は `completed` で終了し、GPT-5.5 Pro のモデル選択検証と固定応答を確認した。

## 実行条件

- 実行日時: 2026-07-11 21:56 JST
- Oracle: 0.15.1
- engine: `browser`
- model: `gpt-5.5-pro`
- session: `oracle-browser-smoke-20260711`
- attachments: なし
- API engine: 未使用
- Chrome認証: 通常のChrome user-data rootを実行時のみ一時コピー
- Chrome runtime: 永続profileではなくOracle生成の一時 `userDataDir` を使用
- Chrome profile selection: `Profile 4` を固定指定（`Local State.profile.last_used` の変動を回避）

実行コマンド（秘密情報を含まない再現用）:

```bash
oracle \
  --engine browser \
  --model gpt-5.5-pro \
  --copy-profile "$HOME/Library/Application Support/Google/Chrome" \
  --browser-chrome-profile "Profile 4" \
  --browser-cookie-wait 5s \
  --slug "oracle-browser-smoke-$(date +%Y%m%d-%H%M%S)" \
  -p 'Reply with exactly: ORACLE_BROWSER_SMOKE_OK_20260711'
```

## 検証結果

| 項目 | 結果 | 証跡 |
|---|---|---|
| Session status | Pass | `status: completed` |
| Engine | Pass | `mode: browser` |
| Model | Pass | `model: gpt-5.5-pro` / `effectiveModelId: gpt-5.5-pro` |
| UI model selection | Pass | `requested=Pro; resolved=Pro; status=switched; verified=yes` |
| Fixed response | Pass | `ORACLE_BROWSER_SMOKE_OK_20260711` |
| Authentication | Pass | `status: completed`、`browser.warnings: []`、正常応答を確認。トップレベルに `error` keyなし |
| Temporary copy cleanup | Pass | 2026-07-11 22:04 JSTにruntimeの一時profile `oracle-browser-sD0wQi` が不存在であることを確認 |

Oracle session artifacts:

- `$HOME/.oracle/sessions/oracle-browser-smoke-20260711/meta.json`
- `$HOME/.oracle/sessions/oracle-browser-smoke-20260711/output.log`
- `$HOME/.oracle/sessions/oracle-browser-smoke-20260711/artifacts/transcript.md`

## 前回失敗との差分

2026-07-06 の失敗ではOracle用の既定Chrome profileにChatGPT Cookieが適用されず、Browser automationが開始できなかった。今回は `--copy-profile` を明示し、既にサインイン済みの通常Chrome user-data rootを使い捨て領域へコピーすることで認証を通過した。

旧失敗証跡: `$HOME/.oracle/sessions/sf-newsletter-review-2/meta.json`

## 運用判断

- Browser実行時は `--engine browser` と `--copy-profile` を明示する。
- 複数profile環境では `--browser-chrome-profile "Profile 4"` を明示し、`last_used`へ依存しない。
- ローカルでは `ORACLE_MISSION_COPY_PROFILE` と `ORACLE_MISSION_COPY_PROFILE_APPROVED=1` を永続設定し、各実行で使い捨てprofileを作る。
- API fallbackは自動で行わない。
- timeout/detach時は同じsessionへreattachし、同一promptを重複実行しない。
- 現行Oracle 0.15.1のモデル選択実測ラベルは `Pro`。過去ログの `Pro Extended` とは異なるが、`gpt-5.5-pro` のeffective model IDと選択検証は成功している。

## 事後検証

2026-07-11 22:04 JSTに、session metadataからruntimeの一時 `userDataDir` を取得して不存在を確認した。その後、ユーザー承認によりローカル設定の `ORACLE_MISSION_COPY_PROFILE_APPROVED` を `1` に変更した。

```text
temp_profile_basename=oracle-browser-sD0wQi
temp_profile_absent=true
persistent_copy_profile_approval=1
status=completed
mode=browser
effective_model=gpt-5.5-pro
browser_warnings_count=0
root_has_error_key=false
```

## 再実行の前提

- 通常Chrome profileでChatGPTへサインイン済みであること。
- 対象アカウントでProモデルを選択可能であること。
- GUI Chromeの起動・操作が許可されていること。
- `--slug` は実行日時を含む一意な値にすること。
- 失敗・detach時は `oracle status` を確認し、同一sessionへ `oracle session <id> --render` でreattachすること。

## 永続設定のMission wrapperテスト

2026-07-11 22:10–22:16 JSTに、`/Users/tackeyy/bin/oracle-mission-review` を実Browserで検証した。

1. Chrome profile未固定の永続設定では、`Local State.profile.last_used=Profile 3`が選ばれ、session `mission-oracle-review-4` はLoginボタン検出で失敗した。
2. wrapperへ `ORACLE_MISSION_BROWSER_CHROME_PROFILE` 対応を追加し、ローカル設定を `Profile 4` に固定した。
3. wrapper単体テストで `--browser-chrome-profile` / `Profile 4` の引数転送を確認した。
4. 実Browser session `mission-oracle-review-5` は `completed`。`gpt-5.5-pro`、`chromeProfile: Profile 4`、モデル選択verified、marker `ORACLE_PERSISTENT_AUTH_OK`を確認した。
5. 実行後、一時profile `oracle-browser-CiHG0K` は削除済みだった。

永続設定後の結果:

```text
session=mission-oracle-review-5
status=completed
mode=browser
model=gpt-5.5-pro
chrome_profile=Profile 4
copy_profile_set=true
model_selection_verified=true
marker=ORACLE_PERSISTENT_AUTH_OK
temp_profile_absent=true
```

未検証範囲: Mac再起動後の再実行。通常利用に必要なwrapper再起動と別Browser sessionでの永続設定読み込みは今回確認済み。

## 指示明瞭度フィードバック

- 不明瞭点: なし。
- 裁量補完: 固定応答に日付suffixを付け、過去sessionとの取り違えを防止した。
- 再試行: 0回。dry-run後の実行1回で成功した。
