# /mission 改修履歴・実測根拠 (changelog)

SKILL.md 本文の token 節約のため、日付付き改修注記と実測データをここに集約する。本文には ID (P1/P2/M6 等) のみ残す。

## P1: Early-Stop Sweet Spot (2026-05)
過去 98 セッションの実測:
- iter 続行時の改善率 63%、不変 16%、悪化 21% (Δ composite 中央値 +0.225)
- iter=1 PASS の平均 composite 4.50、iter=2 PASS 4.48、iter=4-5 PASS は 4.07/4.02 と低下
- iter 1 で 4.0+ 合格に達したのに iter 2 へ進んだ 14 件のうち 7 件が不変 or 悪化

結論: 合格時は基本即打ち止め。stagnation 判定を 5 連続 → 3 連続に短縮。`--max-iter` デフォルト 3 の根拠も同データ (3 超の追加 iter は ROI 低下)。

## P2: 差分レビュー (2026-06-10)
フルレビュー (Reviewer N 名) は iter 1 のみ、iter 2 以降は検証 1 名。根拠: xai-cli PR #17 実測 — 69 分中レビュー 27.4 分 (40%)、3 名 × 3 iter の全量再レビューが主因。

## P3 系 (2026-06)
- **P3-2**: worktree 実行時の state 退避。worktree 削除と共に採点履歴が消え stats・監査から漏れる実害を xai-cli PR #17 で観測
- **P3-4**: cleanup-stale の `--root` 必須化。省略すると ~/dev 全域 rglob で 12 秒超
- **P3-5**: Simple 級は executor インライン可。R2 実測 8 分 vs spawn 中央値 23.4 分

## M 系 (2026-06-10 検査レポート)
- **M6**: インライン修正の Maker-Checker。orchestrator の自己検証のみで合格にした事例への対策
- **M7**: init `--complexity` 記録の必須化。全ランで Unknown 放置され P3-5 と差分レビューが機能しなかった

## M-audit 系 (2026-06-11 skill-auditor 監査)
- **M-audit-2**: `--max-iter` 未指定デフォルトを code 側で 3 に統一 (0 = 上限なし)。doc/code 不整合の解消
- **HIGH-1**: planner/reviewer/scorer/critic に allowed-tools 付与 (Write/Edit 剥奪 = Maker-Checker 強制)
- **Phase 7 injection ガード**: リポジトリ内文書は禁止判定にのみ使用
- **iter2 整合修正**: gotchas scorer error 項を §9 に配置 (並走セッションが同日 §7/§8 を追加していた番号衝突の解消)、push-score 規約を §state.json 操作 に一本化
- **Issue #151 (2026-07-06)**: archived worktree の `mission-archive/iter-N-<mission8>-scoring.{json,md}` を scoring evidence として認識し、worktree cleanup 後の missing-scoring-evidence false positive を解消
- **Issue #159 (2026-07-07)**: `mission-audit.py --since` / `--until` の ISO timestamp cutoff を UTC datetime として比較し、同日更新 state が日付文字列比較で監査から漏れる問題を解消

## P4: 並列処理強化 (2026-06-12)
直近 6 ラン (6/10-6/12) の transcript 実測レビューに基づく 4 改修:
- **実測知見**: Reviewer 並列は機能 (listco iter1: 3 名 2.7/11.5/13.1 分並走、直列なら 27 分 → 13 分)。弱点は (1) executor fan-out の 2 体ペアバリア (BMR クロール 19 バッチ約 2 時間 10 分)、(2) Reviewer ハング無検知 (ma_navi ランで ConnectionRefused 50 分ハング → レビューフェーズ 73 分)、(3) 復旧後の直列化逸脱、(4)「単一メッセージ N 名並列」が 6 ラン中 0 回実行
- **P4-A**: mission-executor に並列 fan-out 指針 (ローリングウィンドウ・W=4-6/2-3 のタスク種別分岐)
- **P4-B**: claude-config.md の Background Agent 並列上限を条件分岐化 (軽量読解系 4-6 並列可)。実測: 軽量 Explore 6 体単一メッセージ起動で全完了 51 秒・stall なし (2026-06-12)
- **P4-C**: Reviewer watchdog (15 分無応答で再 spawn) + リトライ後の並列維持を gotchas §1 に明文化
- **P4-D**: Reviewer 起動を単一メッセージ複数 Skill 呼び出しに統一 (SKILL.md / react-loop-details.md)、並列動作の実測を gotchas §1 に追記
- **P4-E** (追補): TaskOutput block 待機は累計 30 分まで。超える見込みは scheduled_pause + cron/ScheduleWakeup (実測: 2026-06-11 EDINET ラン 167 分中 123 分が block 待機)
- **P4-F** (追補): 人間アクション待ちブロック時は PushNotification 必須 (実測: 2026-06-11 BMR ラン reCAPTCHA 待ち 58 分無通知放置)
- **P4-G** (追補): scorer 合意度算定からインライン修正前の採点値を除外 (実測: 2026-06-12 ラン採点都合のみの iter2 で約 10 分損失)

## EPT 系 (2026-06)
- **EPT**: 判定文言マッピングと観点Dの計画指示明瞭度フィードバック。Critic は rubric の具体的な判定文言に紐づけて改善案を出し、観点Dは採点ではなく次 iteration の実行計画へ反映する。

## その他
- **R1**: refresh-pid。`claude --resume` 後に hook の owner check が別セッション判定する問題への対策
- **H3** (2026-06-10): multi-session の assumptions 分離。並走セッションの相互上書き実害対策
- **Issue #1** (2026-05-24): 完了済 state の `.mission-state/archive/` 自動退避
- **threshold gate** (2026-05): mark-passes が合格条件未達なら exit 2 で reject

## Codex 対応 (2026-06-13)

**背景**: mission を Codex CLI でも使えるようにする。PID owner 判定 (Stop hook と state.json) が `claude` プロセス名をハードコードしており、Codex (`codex`) では owner 照合が崩れ、Stop hook のループ強制が機能しなかった。

**改修**:
- `bin/mission-state.py`: 共通ヘルパー `_comm_is_agent()` を新設。`find_agent_pid()` / `_pid_is_agent()` の判定式 (2箇所) を集約し、`claude` / `claude.exe` / `codex` / `codex.exe` を認識。
- `${CLAUDE_PLUGIN_ROOT}/scripts/mission-stop-guard.sh`: `find_agent_proc()` の `case` に `*codex` / `codex` を追加。
- `tests/test_agent_pid.py`: `_comm_is_agent()` の境界テスト 12 ケース (claude/codex 系を True、bash/python/node/空文字を False)。
- ドキュメント: SKILL.md「Claude Code/Codex 差分」を更新、`refs/codex-setup.md` を新設。

**スコープ外** (意図的): Codex の Stop hook 自動有効化。Codex の non-managed command hook は review/trust 承認が必須で、plugin root 変数も Claude Code と同じ前提にできない。手順は `refs/codex-setup.md` に opt-in user hook として記載。指示ベースのループ (loop_active 監視) は hook 無しでも Codex で機能するため、実用上の支障はない。

## 廃止履歴: 複数セッション奪い合いガード (2026-06-13)

この節は廃止済み legacy single-state 時代の履歴であり、現役機能ではない。現行は `sessions/<sid>.json` に完全統一され、`_is_foreign_live_owner` / `--force-override` / `MISSION_MULTI_SESSION` は存在しない。

**背景**: 同一プロジェクトで Claude と Codex が multi-session 未使用で /mission を起動し、`.mission-state/state.json` を奪い合う実害を確認 (smart-social, Claude のミッションが Codex の init に上書き・archive 退避された)。`cmd_init` に「別 owner の live state を守るガード」が欠如していたのが根因。

**改修**:
- `bin/mission-state.py`: 純粋関数 `_is_foreign_live_owner(existing, current_pid, pid_is_agent)` を新設。`cmd_init` の legacy ブロックで、既存 state が別の生きているエージェント (pid 判定) 所有の進行中ミッションなら **exit 3** で停止 (`--force-override` で上書き可)。exit 2 (mission_id 不一致) も force-override で突破可能に。
- `tests/test_live_owner_guard.py`: 純粋関数の境界 7 ケース + cmd_init 統合 3 ケース (foreign live owner→exit3 / force-override→上書き / 非agent owner→従来 exit2)。
- ドキュメント: gotchas §11 新設、SKILL.md ポインタ拡張、state-management.md に exit3/--force-override 追記。

**設計判断**: 自動 multi-session 化ではなくエラー停止 (exit 3)。MISSION_SESSION_ID 未設定だと session_id が pid fallback で不確定なため、明示的な `MISSION_MULTI_SESSION=1` 設定を促すほうが安全。`_comm_is_agent` (codex 対応済み) により Codex セッションも正しく owner 検出する。

## multi-session 並列実行対応 (2026-06-13, P1-P4)

Claude Code/Codex のいずれでも複数ミッションを安全に並列実行可能にする改修。背景: multi-session 機構は存在したが `cmd_init` 以外が legacy `state.json` 固定で、採点が legacy に書かれ `mark-passes` が「採点未実施」で失敗する等 (§7/§10) 未完成だった。

- **P1 ルーティング統一**: `resolve_state_file`/`is_multi_session`/`resolve_session_id`(env) を新設。全 `cmd_*` を sessions/ 解決に統一。session_id を `CLAUDE_CODE_SESSION_ID`/`CODEX_THREAD_ID` で安定化 (PID 再利用非依存・resume 対応)。デフォルト multi (legacy 自動検出で後方互換)。`cmd_init` の mission_id 重複チェック除去で異なる mission の並列可。session_id パストラバーサルサニタイズ。`mark_passes`/`mark_halt` に exists ガード。
- **P2 状態管理**: `cmd_list`/`cmd_cleanup_stale`/`cmd_halt --all` が `sessions/*.json` も走査。`_project_root_of` で proj 計算統一 (sessions で `sf.parent.parent` が狂う罠を回避)。`mark_passes`/`mark_halt` 完了時に aggregate `active_sessions` から除去 (dead entry 防止)。
- **P3 Stop hook**: `HOOK_SID` を env から算出し sessions owner 照合を sid 優先に (AGENT_PID プロセス遡及非依存。Codex で解決失敗時に全セッションを誤 block する穴を解消)。
- **P4 後方互換**: `mission-migrate.py` に loop_active ガード (`--force` で override) + pid 補完。

**判定基準**: `is_multi_session` は MISSION_MULTI_SESSION=0/1 明示 > legacy state 保有なら legacy 継続 > Claude Code/Codex env で multi > それ以外 legacy。ユーザーは env を意識せず Claude Code/Codex 起動だけで並列が有効になる。

テスト: 126 → 154 (新規28: routing/session-id env/multi-default/サニタイズ/lifecycle/migrate/hook)。

### multi-session: 完了済み legacy の自動 multi 移行 (2026-06-13 追補)

`is_multi_session` が「legacy `state.json` があれば常に legacy 継続」だったため、**完了済み (passes=true) の legacy state が残っているだけで Claude Code/Codex の並列が無効化される**問題 (smart-social 実機検証で発覚: 完了済み GitHub Actions ミッションの state.json が残り、Claude Code/Codex 起動が sessions/ に分離されなかった)。進行中 (`loop_active` かつ passes/halt なし) の legacy のみ後方互換で継続し、完了/中断済みは multi 移行可に修正。読めない legacy は安全側で継続。

## legacy single-state 廃止・multi-session 完全統一 (2026-06-13)

legacy single-state モードを廃止し、全て `sessions/<sid>.json` に統一。背景: legacy 分岐が副作用バグの温床(完了済み legacy で並列無効化 / §7 §10 / 半移行 / hook 誤block)だった。

- `resolve_state_file` を `session_file(cwd, resolve_session_id())` に単純化。**`is_multi_session`/`_is_foreign_live_owner`/`--force-override`/`--multi-session`/`MISSION_MULTI_SESSION` を削除**。
- `cmd_init` は常に sessions/<sid>.json + aggregate。**legacy state.json は読まない・書かない**(既存は無害に放置、手動 `mission-migrate.py` 可)。
- hook の legacy state.json fallback 削除(262→154行)。sessions/ の owner 照合(HOOK_SID)のみ。
- テスト: conftest を sessions/ ベースに(run_cli デフォルト sid="test")、legacy 前提テスト削除、統一保証テスト追加。159→143。

session_id 不一致や奪い合いは構造的に発生しない(各セッション独立 sid、同一 sid 再 init は本人の上書き=resume)。env 無し環境は `pid-<N>` fallback(resume 非対応は既知制約、`refresh-pid` でカバー)。

### agent フィールド: 起動元(Claude Code/Codex/CLI)の独立記録 (2026-06-13)

ログでの cc/codex 判別が `session_id` の prefix (cc-/cx-) のみに依存しており、`MISSION_SESSION_ID` 明示指定や旧 uuid4 形式では**起動元が判別不能**だった (直近5回中4回が不明)。`stamp_metadata` に `agent` フィールドを追加 (`resolve_agent`: `CLAUDE_CODE_SESSION_ID`→`claude-code` / `CODEX_THREAD_ID`→`codex` / なし→`cli`)。**session_id 非依存**で起動元を確実に記録し、`cmd_list` 出力にも `agent` を表示。既存 state には agent なし (今後の実行から記録)。

### ログ調査由来の改善: 直書き禁止明文化 / stats agent別集計 / scoring メタ付与 (2026-06-13)

直近5回の実行ログ調査で発覚した5点を反映。

- **#1 直書き禁止の明文化**: gotchas #7/#10 が legacy 廃止後も「session JSON を直接書け」と能動指示し続け、orchestrator が踏襲して `push-score`/`mark-passes` の threshold gate を迂回していた (stats の `ungated` 4件 = PASS の16% が gate 未通過の合格)。#7/#10 に「現在は正規コマンド必須・直書き禁止」誘導を挿入し旧手順を取り消し線化。SKILL.md にも直書き禁止を明記。
- **#2 stats の agent 別集計**: 前回追加した `agent` を `cmd_stats` が未活用だった。`_aggregate` に `by_agent` (total/pass/halt/incomplete) を追加、テキスト出力に内訳行。Claude Code/Codex/CLI 別の成績が見える。
- **#3 scoring ログへの起動元メタ付与**: `push-score --scoring-output` が archive する md 冒頭に `<!-- mission-meta: session_id=... agent=... mission_id=... -->` を前置。並列実行時に scoring md 単独で起動元を追える。`shutil.copyfile` を read+write に置換 (未使用 import 削除)。
- **#4 legacy 残骸退避**: 統一後の現役 root `state.json` 残骸を archive へ退避 (現役は aggregate.json + sessions/ のみに)。
- **#5 scoring 命名整理**: 非規約 scoring md を archive/legacy-scoring/ へ集約。archive 直下は規約準拠 (iter-N-<mission8>-scoring.md) のみ。
- テスト: 150 → 153 (+3: by_agent×2, scoring メタ×1。完全一致アサート2件を包含に更新)。


### アーキテクチャリファクタ (品質+速度) — /mission で自律実行 (2026-06-13)

mission-state.py(1048行単一ファイル)を /mission スキル自身で3イテレーション(3.40→4.48→4.86)リファクタ。166テスト全pass・後方互換完全維持。

- **速度: stats 86秒→0.85秒(100倍)**。原因は `--root` 省略時の `Path.home()` 全体 rglob(~/dev に12万ディレクトリ)。対策: (a)`cmd_stats` のデフォルト root を `_default_search_roots()`(~/workspace, ~/dev, ~/dotfiles)に限定し list/cleanup-stale/halt と統一 (b)`_iter_state_files` を os.walk + `_PRUNE_DIRS`(node_modules/.git/target/.gradle/Pods 等)プルーニングに書き換え、巨大ツリーを走査前にスキップ。`.mission-state` 発見後は `dirnames[:]=[]` で子降下も停止。`followlinks=False` 明示。
- **品質(S1-S5)**: 関数内 import(re/subprocess)を top-level 巻き上げ / dead code `state_file()` 削除 / `_collect_states` の3 glob を `_iter_state_files(include_archive=True)` に統合 / `cmd_push_score` の archive 書込を `_archive_scoring_output()` 抽出 / `_aggregate` の by_agent を `_build_agent_summary()` 抽出 + `_classify` 3N→N最適化。
- **モジュール分割は見送り**: importlib 直import 5ファイル・symlink 配布・単一ファイル実行の3制約でリスク>メリットと判断(1048行は grep で管理可能、import 30ms も良好)。単一ファイル維持で品質改善。
- エラーメッセージ本文を日本語統一(ERROR:/WARNING: プレフィックス維持)。抽出関数・プルーニング・include_archive の単体テスト13件追加(tests/test_refactor_iter2.py)。
- 後方互換: CLIコマンド名/引数/exit code・schema v2・hook 参照フィールド・importlib 公開シンボル すべて不変。
