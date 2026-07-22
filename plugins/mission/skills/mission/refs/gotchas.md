# /mission 既知のハマりポイント (session-review 由来)

> SKILL.md 本体から退避した詳細リファレンス (2026-06-10, 公式 Progressive Disclosure 準拠)。
> **いつ読むか**: 新規ミッション開始時 → §6 / halt 後の再開時 → §2 / Reviewer 起動時 → §1, §3 / サブエージェント Edit 後 → §4 / 複数セッション並行時 → §5 / 長時間 background ジョブ待機 → §8 / scorer internal error → §9。

session-review skill で蓄積された /mission 実運用上の落とし穴。新規ミッション開始時・halt 復帰時・Reviewer 起動時に必ず参照。

### 1. mission-reviewer 3 名同時並列で usage limit に到達するリスク

Iter1 で `mission-reviewer × 3` を単一メッセージで並列起動すると、Reviewer B/C が「You've hit your org's monthly usage limit」で取得不能になる事例あり (2026-05-26 観測)。Reviewer A だけ通って B/C エラーだと採点が偏り、Iter1 で誤った halt 判定に至る。

**対策 (P3-1 改訂, 2026-06-10: 並列がデフォルト / P4 追記, 2026-06-12)**:
- **デフォルトは単一メッセージでの N 名並列起動**。SKILL.md 本体の「mission-reviewer × N (並列)」が正であり、本節を理由に常時直列化しない
  - 根拠: 直列化の実測コスト = xai-cli PR #17 で 3 iter 合計 11.5 分 (run の 17%) の純損失
  - **並列動作は実測確認済み (2026-06-12 transcript 解析)**: Skill tool は別メッセージ・約 10 秒間隔の起動でも tool_result が時間重複し非同期並列実行される (listco 分析 iter1: Reviewer 3 名が 2.7/11.5/13.1 分で並走、直列なら 27 分 → 実測 13 分。単一メッセージ起動の実測: 2026-06-12 軽量 Explore 6 体で START 01:45:20-30 / END 01:45:27-37 の時間重複・全完了 51 秒・stall なし)。ただし別メッセージ分割の並列性は挙動保証がない (公式仕様は単一メッセージ内の複数ツールコールのみを並列実行と規定しており、メッセージ跨ぎの非同期化は実装依存の観測挙動) ため、**起動は単一メッセージに統一する**
- **Reviewer watchdog (P4)**: ハング耐性の**主手段は単一メッセージ並列起動そのもの** (1 体がハングしても他の Reviewer は完了する)。その上で、orchestrator に制御が戻った時点 (他 Reviewer 完了・API エラー返却等) で未返の Reviewer の経過を確認し、**起動から 15 分超なら完了を待たずに該当 Reviewer のみ再 spawn** する。ハング中の Reviewer の完了を漫然と待ち続けない (実害: 2026-06-11 ma_navi ランで ConnectionRefused により Reviewer A の完了を 50 分待機、レビューフェーズが健全ラン 8 分 → 73 分に悪化)
- **リトライ・再 spawn 後も並列を維持する (P4)**: ネットワークエラーやハングからの復旧時に直列起動へ切り替えない。直列フォールバックが許されるのは下記 usage limit 全滅時のみ (逸脱事例: 2026-06-11 ma_navi ランで復旧後 A 完了待ち → B 起動と完全直列化)
- usage limit エラーが**出た reviewer のみ** 60 秒以上空けて単発リトライ
- リトライも失敗し全滅した場合のみ、時差を入れた直列起動にフォールバック
- それでも取得不能なら mark-halt で「usage_limit_reviewer_X_unavailable」として halt し、ユーザー判断を仰ぐ。後刻リトライで取得できれば Iter+1 で再採点する

### 2. halt 後に Iter を再開したい (mark-halt 後の reactivate)

`mark-halt` 後 = `loop_active: false` でユーザー追加指示や新事実が出て Iter 継続したい場合、対象操作とstate再活性化の両方について明示承認を得てから **`reactivate` で再活性化する**。`--expected-category` は現在の `halt_category` と一致させる:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py reactivate \
  --approved-by-user \
  --expected-category awaiting-approval \
  --reason "<ユーザーが承認した再開理由>" \
  --phase executing
```

成功時は旧 `halt_reason` / `halt_category` と承認理由が `reactivation_history` に残り、current の停止フィールドはクリアされる。`stale` / `orphan` halt は `reactivate` ではなく `resume` を使う。state の手動直書きや汎用 `set` によるhalt解除は、承認・監査境界を失うため禁止。

**補足 (設計意図)**: orphan halt は `phase` を書き換えず保持する (refresh-pid で resume した際に進捗 phase から継続するため)。`cmd_cleanup_stale` は test_cleanup_stale.py::test_cleanup_stale_execute_preserves_phase で担保。stop hook の死PID検出 (mission-stop-guard.sh) も jq が phase に触れず同挙動 (bash のためテスト対象外)。明示停止の `mark-halt`/`halt --all` のみ phase='halted' を書く。

### 3. /mission 起動時に initial gitStatus の M マーク確認 (誤検知防止)

セッション開始時にすでに modified (M) 状態のファイルは **別セッションの未コミット変更**。本ミッションのスコープ外。これを Reviewer がミッション起因と誤検知して High 指摘を返すケースあり (2026-05-26 マスター本体 257 行差分の例)。

**対策**:
- Phase 0 直後に `git status -s` を実行し、modified ファイルを Assumption Registry に「本ミッションスコープ外」として明示記録
- Reviewer プロンプトに「以下のファイルは本ミッション開始時点で既に M 状態。スコープ外として除外して採点せよ」と明記
- Reviewer から該当ファイル差分の指摘が返ってきたら、`git log --oneline -1 -- <path>` で最終コミット時刻を確認し、本セッション前なら誤検知判定

### 4. サブエージェント Edit 後 git diff が空 = 前コミットで既更新済の可能性

「既存ファイル X を更新せよ」とサブエージェントに依頼しても、`git diff --stat HEAD -- X` が空なら **前コミットで既に同等内容に更新済** (実質 no-op) を意味する。サブエージェントの自己申告 (「composite 4.8 で大幅改善」等) を鵜呑みにせず、`git log --oneline -- <path>` で最終コミット内容を確認すること (2026-05-26 R7 で観測)。

**対策**:
- サブエージェント起動前に既存ファイルの最終コミット時刻と内容を Read で確認
- 「直近 X 時間以内のコミットで同等内容が既に反映されている可能性あり、その場合は Read のみで no-op 完了報告」と指示文に明記

### 5. 複数 /mission セッション並行時、終了時 git status に他セッション成果物が混在

同一 workspace で複数 Claude セッションが並行して /mission を実行している場合、本ミッション完了時の `git status` に **他セッションの untracked / M ファイルが混在** する。本ミッション起因の差分だけを識別する手順がないと、コミット範囲を誤る (2026-05-26 観測: v04 完了時に並行実行されていた v03 の R33-R36 が untracked で出現)。

**対策**:
- Phase 0 で本ミッション起因の出力ファイル名 (例: `R37-R40_*.md`) を Assumption Registry に明示
- 終了時 `git status -s` 出力を Assumption Registry の宣言と突合し、宣言外の untracked / M ファイルは「他セッション成果物」として識別
- ユーザーへの完了報告で「本ミッション起因の差分」と「他セッション成果物 (要別途判断)」を明確に分けて提示
- ユーザーが「全部コミット」を指示した場合のみ他セッション成果物も論理単位で別コミットに分けて投入


**コミット前ガード**: コミット実行前に `git diff --name-only --cached` を自セッションの変更宣言 (Assumption Registry / 計画の対象ファイル一覧) と突合し、宣言外のファイルが staged に含まれていたら即 unstage して原因を確認する。

### 6. 新規ミッション開始 (multi-session では init を呼ぶだけ)

legacy 廃止(2026-06-13)後、`cmd_init` は `skipped` を返さない。同一 `MISSION_SESSION_ID` の再 init は本人の上書き=resume、異なる sid は `sessions/<sid>.json` に自動分離される。**旧「mv state.json で手動退避」手順は不要**(legacy 前提・現行は state.json を読まない)。前回ミッションが loop_active のまま残るのが気になる場合のみ `mark-halt` か `cleanup-stale --root "$(pwd)" --execute` で整理する。

### 7. ~~multi-session 並行時 legacy state.json 汚染~~ (2026-06-13 legacy 完全廃止で消滅)

全 `cmd_*` が `sessions/<sid>.json` に統一され前提が消滅。state 更新は必ず `mission-state.py` の正規コマンド (直書き禁止)。詳細は `refs/changelog.md` の legacy 廃止節。

### 8. Stop hook 稼働中は background 待機をターン終了で行えない — TaskOutput block 待機を使う (2026-06-11)

`loop_active: true` の間は mission-stop-guard.sh がターン終了をブロックするため、「background fetch を起動 → ターンを終えて完了通知を待つ」が成立しない (Stop が block され続行を強制される)。

**対策**:
- 長時間ジョブは Bash `run_in_background` で起動し、**`TaskOutput(task_id, block=true, timeout=600000)` を繰り返して同期待機**する (1 回最大 10 分。fetch 83 分 = 約 8 回で待機できた実績)
- **ただし block 待機は累計 30 分まで (P4 追補, 2026-06-12)**: 残り見込みが 30 分を超えるジョブは TaskOutput 連打で待たない。①進捗非依存の他作業 (ドキュメント整備・次フェーズ準備・コミット等) を先に消化 → ②それも尽きたら `mark-halt --reason "scheduled_pause: <再開条件と時刻>"` で計画的待機にし、cron / ScheduleWakeup で再開する (実害: 2026-06-11 EDINET ランで 167 分中 123 分が 10 分超 ×12 回の TaskOutput ブロック待機。同日ユーザー指示で cron 再開方式に切替済み — 本追記はその恒久化)
- 併走の watchdog (stall 検知) を仕込む場合: ①完了マーカーの grep は **dry-run 時の古いログ行に誤反応**する (「fetch complete」が既にログにあった実害) → チェックポイント JSON の processed>=total 比較にする ②zsh は `set -- $var` で語分割しないため数値比較が壊れる (cli-cheatsheet の bash/zsh 表参照)
- executor サブエージェントに数時間ジョブを持たせない (タイムアウトする)。ジョブは orchestrator が background で持ち、サブエージェントには離散タスクのみ渡す

### 9. aggregate-reviews / fallback converter が失敗することがある

標準 Phase 5 は `aggregate-reviews` で reviewer JSON を決定論集計する。失敗原因の大半は reviewer の `mission-review/1` 契約違反か、fallback converter の出力 JSON 不備。

**復旧手順**:
1. `aggregate-reviews` の exit 2 メッセージを読み、どの reviewer JSON が契約違反か確認する。
2. `skills/mission-scorer/SKILL.md` の「Fallback 発動条件」に合致するか確認する。
3. 条件を満たす場合だけ、`mission-scorer` を fallback converter として呼び、散文レビューを `mission-review/1` JSON に変換させる。
4. fallback 後も `aggregate-reviews` → `push-score --scoring-json` の通常経路に戻す。fallback 使用は scoring JSON の notes に明記する。

**注意**: internal error 中も `loop_active: true` は維持される。Stop hook が継続を強制するため、エラーを理由に黙ってループを抜けない。

### 10. ~~multi-session の set/mark-halt が legacy state.json に誤書込~~ (2026-06-13 legacy 完全廃止で消滅)

同上 (§7)。ルーティングは `resolve_state_file()` に統一済み。詳細は `refs/changelog.md`。

### 11. ~~複数セッションが state.json を奪い合う~~ (2026-06-13 legacy 完全廃止で消滅)

全セッションが `sessions/<sid>.json` に自動分離され奪い合い構造が解消。詳細は `refs/changelog.md`。
