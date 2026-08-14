親 Issue: #473（Wave 0 / 依存順 2 番目・P1）

# 概要

state の「新しさ（freshness）」を判定する権威が Python CLI と Stop hook で分裂している。Python は 4 段階のフォールバックチェーンを見るが、Stop hook は `updated_at` だけを見る。その結果、**Python が「新鮮」と判断する state を Stop hook が stale と判断して auto-halt できる**。auto-halt は state を実際に書き換える破壊的操作なので、判定の食い違いは実害に直結する。

# 一次証拠（現在の main）

## Python 側（4 段階チェーン）

`skills/mission/bin/mission-state.py` L1530–1541 `_state_age_since_update_sec`:

```
heartbeat_at → last_progress_at → last_activity_at → updated_at
```

L1246–1247 のコメントに理由が明記されている: 「stale 判定は `last_activity_at` を `updated_at` より優先する（`updated_at` は resolution batch 書き込みで汚染される）」。

**同一ロジックが `skills/mission/lib/mission_common.py` L173–175 `state_age_since_update_sec` にも重複実装されている**（同じ 4 段階）。本 Issue の一元化はこの重複解消も含む。

## Shell 側（`updated_at` のみ）

`scripts/mission-stop-guard.sh` L278:

```sh
UPDATED_AT=$(jq -r '.updated_at // empty' "$SESSION_FILE_TO_BLOCK" 2>/dev/null || echo "")
```

L283 で現在時刻との差分を計算し、`STALE_HALT_SEC`（既定 10800 秒）超で auto-halt、3600 秒超で WARN。`heartbeat_at` / `last_progress_at` / `last_activity_at` は一切参照しない。

## 実害の経路

`heartbeat_at` が 30 分前に更新されているのに `updated_at` が 3 時間以上前、という state は実際に作れる（`updated_at` は resolution batch 書き込みで更新されないことがある）。このとき:

- Python: 新鮮と判定
- Shell: stale auto-halt を実行 → L296 で reason 設定 → L304 `_mission_halt_session` → L154 で `python3 mission-state.py mark-halt --reason ... --category stale` を実行し、state に `halt_reason` / `halt_category=stale` を書き込む → L315–316 で block せず exit 0

つまり作業中のセッションが Stop hook によって停止させられる。

# 変更内容

freshness 判定の**唯一の管理元を Python 側に置き**、Stop hook は判定結果を受け取って表示・分岐するだけにする（親 Issue 設計原則 4）。

1. Python CLI に、freshness 判定結果を機械可読で返す read-only の経路を用意する。既存の `next` / `get` に相乗りするか、専用サブコマンドを足すかは実装者判断でよいが、**判定に使ったタイムスタンプの種別（どのフィールドが採用されたか）と経過秒数、stale/warn/fresh の verdict を含める**こと
2. `scripts/mission-stop-guard.sh` の L278–L304 付近から、`jq` による独自の `updated_at` 計算を削除し、上記 Python の verdict を使う
3. `mission-state.py` と `mission_common.py` の重複した age 計算を 1 つに集約する（`mission_common.py` 側を正典にして CLI から呼ぶ形が自然）

## やらないこと

- `STALE_HALT_SEC` / WARN 閾値の既定値変更（10800 秒 / 3600 秒を維持）
- auto-halt する条件そのものの変更（同じ入力に対する結論は不変。**変えるのは「誰が判定するか」だけ**）
- `MISSION_STALE_ACTIVE_SECONDS` の意味変更
- lease 期限切れ時の `cleanup-stale` 呼び出し経路（L159–170）の変更
- orphan 検出ロジックの変更

## fail-safe 要件

Python 呼び出しが失敗した場合（CLI 不在・タイムアウト・非 0 終了・パース不能）、Stop hook は **auto-halt しない**こと。判定不能を stale と見なして破壊的操作へ倒してはならない。

# 受け入れ条件

- [ ] `heartbeat_at` / `last_progress_at` / `last_activity_at` が新しく `updated_at` だけが古い state で、Stop hook が auto-halt しない
- [ ] 4 フィールドすべてが古い state では、従来どおり auto-halt する
- [ ] WARN 閾値の挙動も Python の verdict に従い、従来と同じ入力で同じ結果になる
- [ ] `mission-stop-guard.sh` に `updated_at` を直接読む freshness 計算が残っていない
- [ ] Python 呼び出し失敗時に auto-halt しない（fail-safe）
- [ ] age 計算の実装が 1 箇所に集約されている
- [ ] `plugins/` 配下のミラー（hook スクリプトを含む）が正典と一致
- [ ] `shellcheck scripts/mission-stop-guard.sh` が通る
- [ ] 既存テスト全緑

# テストリスト

`skills/mission/tests/test_stop_hook.py` へ追加。**既存の stale テストは全て `updated_at` のみを設定しており、チェーン分裂を突くケースが存在しない**（これが本 finding の核心的な検出漏れ）。

1. `heartbeat_at` = 30 分前 / `updated_at` = 4 時間前 → auto-halt されない（Red になるはず。これが本 Issue の再現テスト）
2. `last_activity_at` = 30 分前 / `updated_at` = 4 時間前 → auto-halt されない
3. `last_progress_at` = 30 分前 / `updated_at` = 4 時間前 → auto-halt されない
4. 全フィールドが 4 時間前 → 従来どおり auto-halt され `halt_category=stale` になる
5. 全フィールドが 2 時間前 → WARN のみ（auto-halt しない）
6. Python CLI が非 0 終了・不正 JSON を返す状況を作る → auto-halt せず、hook は安全側に倒れる
7. Python 側 age 計算の優先順位テストが 1 箇所の実装に対して行われている（`test_issue310_last_activity.py::test_age_chain_uses_last_activity_before_updated` L93 の観点を維持）

参考にする既存テスト:
- `skills/mission/tests/test_stop_hook.py::test_hook_warns_on_stale_state`（L348）
- 同 `::test_hook_autohalts_on_very_stale_state`（L359）
- 同 `::test_hook_does_not_autohalt_awaiting_user_state`（L391）
- `skills/mission/tests/test_issue310_last_activity.py::test_age_chain_uses_last_activity_before_updated`（L93）

# 実装上の注意（過去のレビュー指摘より）

- TDD（Red → Green → Refactor）。テスト 1 が Red になることを先に確認すること
- Stop hook は Python を呼ぶたびに実行時間が増える。既存の呼び出し（`mark-halt` / `cleanup-stale`）と合わせて呼び出し回数が増えすぎないようにする
- `plugins/` ミラーへ `cp` 同期する
- `.github/workflows/ci.yml` の shellcheck 対象にミラーも含まれている点に注意
- コードコメントは「何をしているか」ではなく、コードから読み取れない制約・意図だけを書く
