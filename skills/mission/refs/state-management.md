# refs/state-management.md — state.json 操作の完全リファレンス

SKILL.md 本体から外出しした詳細リファレンス。普段の運用で参照すべきフルセット。
本体には「最低限の 5 コマンド」だけ残し、ここに **mission-state.py の全サブコマンド** と **Phase C multi-session 関連** を集約する。

参照タイミング (状況トリガー):
- 本体の5コマンド以外のサブコマンドが必要になった → 「全サブコマンド」
- multi-session を並列実行したくなった / session 競合が起きた → 「Phase C multi-session」
- 旧スキーマの state.json でエラーが出た → 「migration」
- dead-PID の active state が残った → 「cleanup-stale」
- 合格後に PR を自動マージしてよいか迷った → 「Phase 7 自動マージ — 詳細判定ロジック」

---

## state.json 操作: mission-state.py 経由 (推奨)

state.json の更新は **`${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py`** 経由で行うこと。リポジトリ root からは安定 wrapper の **`scripts/mission-state.py`** も同じ CLI に委譲する。inline `jq` 直接実行は schema 不整合・race condition の原因となるため禁止。

```bash
# 初期化 (起動時、mission_id 同一性チェック付き)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py init "<ミッション記述>" --threshold 4.0 --complexity <Simple|Standard|Complex|Critical> [--max-iter N]

# 値の取得
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py get [--field key]

# 値の更新 (複数 key=value 可、key 型は JSON 推論)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py set iteration=1 phase='"executing"'

# 採点結果を score_history に記録 (Phase 5 直後、orchestrator が必ず呼ぶ)
# scorer は context: fork で state.json に書き込めないため orchestrator が代行する
# 推奨 (ADR-002 Stage 1): scorer が items を JSON ファイルに書き、orchestrator はパスを渡すだけ。
# composite/min_item は CLI が items から再計算する (転記レイヤ排除)。
# strict 検証: 未知キー reject / 全 items <= 1.0 (0-1 正規化疑い) reject / 範囲外 reject。
# evidence は archive/iter-N-<mission8>-scoring.json に _meta 付きで自動保存され、
# score_history entry に score_source="scoring-json" と scoring_evidence_path が記録される。
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py push-score \
    --iteration <N> \
    --scoring-json /tmp/mission-scorer-iter-<N>-<mission_id先頭8>.json \
    --open-high <未解決High件数>
# JSON 形式: {"items": {"mission_achievement": 4.0, "accuracy": 3.5, "completeness": 4.2, "usability": 3.8, "reviewer_consensus": 4.0}, "notes": "<任意>", "open_high": 0}

# 従来経路 (非推奨・DeprecationWarning あり。scoring evidence なしは default reject。
# 移行専用の一時 escape hatch として MISSION_REQUIRE_SCORING_EVIDENCE=0 のみ許可):
# #122 の gate 強化:
#   - 自己申告 composite/min_item が items 明細より 0.1 超で上振れ (inflation) したら exit 2
#     (mark-passes gate はこの自己申告値を使うため、上振れは合格迂回になる。過小申告は保守側なので許容)
#   - 同一 iteration の再 push は --resubmit-reason "<理由>" が必須 (旧 entry は履歴として保持)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py push-score \
    --iteration <N> \
    --composite <総合スコア (items mean を 0.1 超で上回らないこと)> \
    --min-item <最低項目スコア (items min を 0.1 超で上回らないこと)> \
    --items '{"mission_achievement": 4.0, "accuracy": 3.5, "completeness": 4.2, "usability": 3.8, "reviewer_consensus": 4.0}' \
    --open-high <未解決High件数> \
    --scoring-output /tmp/mission-scorer-iter-<N>-<mission_id先頭8>.md \
    [--resubmit-reason "<同一 iteration 再 push 時のみ必須>"] \
    [--notes "<任意のメモ>"]

# 合格マーク (passes=true, loop_active=false)
# 重要: mark-passes を呼ぶ前に必ず push-score で採点結果を記録すること
# (これを怠ると「score_history が空のまま passes=true」になる既知バグを再発させる)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py mark-passes

# 中断マーク (halt_reason 設定, loop_active=false)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py mark-halt --reason "<理由>"

# #123 (推奨): 復帰を 1 コマンドに統合。refresh-pid → cleanup-empty → cleanup-stale → next を
# 正しい順序 (refresh-pid が先) で原子的に実行し、next の出力に resume サマリ
# ({"pid_refreshed","reactivated","cleaned_empty","halted_stale","dry_run"}) を添えて返す。
# refresh-pid が cleanup-stale より先に走るため、復帰直後の旧 (dead) PID でも自 state を orphan halt しない。
# cleanup-stale は常に cwd スコープ (MISSION_SEARCH_ROOTS は無視) で、他プロジェクトを巻き込まない。
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py resume            # [--dry-run] [--force]

# R1 (個別コマンド。通常は resume を使う): state.pid を現セッションの agent CLI PID に更新
# (これを怠ると hook が state.pid != 現 PID と判定して exit 0、ループ強制が機能しない)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py refresh-pid

# ADR-002 Stage 3: 次の 1 手を state から決定論的に取得 (read-only)
# 返り値: {"next_action": "run-planner|run-executor|run-reviewers|run-scorer|mark-passes|
#          report-complete|report-blocker|resume|await-user|consider-halt|init",
#          "summary": "...", "command_hint": "...", phase/iteration/... の snapshot}
# 用途: compaction 復帰・Codex (Stop hook なし) の各 iteration 区切りで呼び、散文の手順解釈より優先する
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py next

# Codex startup health check (Issue #108):
# - active state があるか
# - Codex user hook に mission-stop-guard.sh が登録されているか
# - hook 無しでも `next` fallback で進行できるか
# を JSON で診断する。既定は skills-only fallback を許容し、--require-stop-hook で hook 未設定を exit 2 にできる。
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py codex-preflight --json
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py codex-preflight --json --require-stop-hook

# 空 .mission-state/ ディレクトリの cleanup (skill 起動時に実行推奨)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py cleanup-empty <project_root_path>

# 全プロジェクト active 一覧 (C-4)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py list   # NOTE: MISSION_SEARCH_ROOTS (未設定なら cwd) 配下のみ検索。横断したい場合は MISSION_SEARCH_ROOTS を設定

# 全プロジェクト一括停止 (C-4)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py halt --all --reason "<理由>"

# 完了前の specialist/provider candidate accounting 確認 (warning-oriented)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py specialists accounting --json

# 長時間・大量 batch の進捗 checkpoint (state + archive に証跡を残す)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py progress update \
    --total <総件数> \
    --completed <完了件数> \
    [--batch-size <件数>] \
    [--last-unit <最後に処理した単位>] \
    [--artifact <成果物パス>] \
    --json
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py progress get --json
```

スクリプトが自動で stamp するフィールド (A-1/A-2/B-3/C-1):
- `project_root` (current cwd, A-1: hook の越境発火防止)
- `pid` (agent CLI プロセス PID, A-2: orphan 自動回収)
- `hostname`, `session_id` (uuid), `created_at_session` (B-3: owner 識別)
- `mission_id` (mission の SHA256[:16], C-1: 別ミッション検出)
- `schema_version` (現在 2)
- `cli_version` (実行中の mission-state.py のバージョン, #186: plugin cache 陳腐化の検出用)

**#186 バージョン skew 警告**: `codex-preflight --json` と `resume` の出力 (`resume.version_skew`) は、`~/.claude/plugins/cache/mission-marketplace/mission/*` および `${CODEX_HOME:-~/.codex}/plugins/cache/mission-marketplace/mission/*` を走査し、実行中の `MISSION_CLI_VERSION` より古いバージョンディレクトリが存在すれば `version_skew` (null 以外) を返す。古い cache は Wave 1/Wave 2 の修正を反映しない古い SKILL.md・gate ロジックで動作し続けるため、`stats --json` の `by_cli_version` と合わせて陳腐化を検出できる。検出のみで自動修復はしない (plugin update / cache 削除は手動)。cache root は `MISSION_CLAUDE_HOME` (Claude Code 側。未設定なら `~/.claude`) / `CODEX_HOME` (既存の hook 探索と同じ変数。未設定なら `~/.codex`) で override できる (主にテスト隔離用)。

更新前の `.bak` 自動生成 (A-4)、`fcntl.flock` ロック (B-1)、`fsync + os.replace` atomic write (B-2) もスクリプトが内包する。

`specialists accounting` は available candidate のうち selected / invoked / skipped / unavailable / failed 等の terminal decision trail がないものを表示する。`Critical` と high-risk は全 available candidate、`Complex` は security/testing/infra と、schema/migration/query/persistence 等の強いシグナルがある database/backend candidate を重点対象にする。これは optional provider を blanket hard gate にするものではなく、ハッカブルな plugin/provider 拡張性を保ちながら判断理由を監査可能にするための pre-completion warning である。ただし `required: true` provider は stricter gate で、`prepared` / `awaiting-input` / `skipped` / `failed` だけでは結果証跡と見なさず、`completed` / `inline-applied` / `skill-tool-applied` のいずれかが無い限り `mark-passes` が exit 2 で拒否する。

**#189 (自動 WARN)**: `mark-passes` は成功時、`specialists_selected` にあるが `specialist_invocations` に一件も (skipped/unavailable/failed 等どのステータスでも) 記録のない specialist を stderr WARN で列挙する (呼び出し不要・自動発火。`specialists accounting` の手動確認とは別。判定は `specialists_phase_plan` の providers を含まない `specialists_selected` のみを対象にし、phase_plan にしか登場しない specialist を誤検知しない)。非 `--force` 経路では required specialist は accounting_required/result_required gate がここに到達する前に exit 2 で止めるため、この WARN の対象は常に optional。`--force` はこれらの gate ごと skip するため、`--force` 経路ではこの WARN 自体を出さない (required specialist が混入していた場合に「optional のため」という文言が誤りになるのを避ける)。`mission-state.py next` も `mark-passes` action の `details.unclosed_specialists` に同じ一覧を含める。hard gate ではないため mark-passes 自体は成功するが、`specialists log-invocation --status skipped --reason "<理由>"` 等でクローズアウトしておくのが望ましい。

`progress update` は `.mission-state/archive/iter-<N>-<mission8>-progress.md` に checkpoint を保存し、state の `progress` に `total` / `completed` / `remaining` / `last_unit` / `artifact_path` / `evidence_path` を記録する。これは score や pass/fail を変更しない観測用データであり、長時間 batch が compaction や中断を挟んでも audit report の slow session 行から進捗を復元できるようにする。

### Phase C: multi-session 並列実行 (2026-06-13 デフォルト有効化)

同一プロジェクトで複数の /mission を並列実行する場合、各セッションは `sessions/<sid>.json` に独立した状態を持つ。**Claude Code/Codex から起動すれば自動的に有効** (env 不要)。

- **常に multi (2026-06-13 legacy 完全廃止)**: `is_multi_session`/`MISSION_MULTI_SESSION` は撤廃。全 `cmd_*` が常に `sessions/<sid>.json` を使う。既存 legacy `state.json` は読まれず無害に残る (手動 `mission-migrate.py` で sessions/ へ移行可)。
- **session_id (`resolve_session_id`)**: `MISSION_SESSION_ID` > `cc-<CLAUDE_CODE_SESSION_ID>` > `cx-<CODEX_THREAD_ID>` > `pid-<N>`。Claude Code/Codex の ID は安定 (resume・PID 再利用に強い)。ファイル名と session_id フィールドが一致。
- **aggregate.json**: init で `active_sessions` に追加、mark-passes/mark-halt で除去。`cmd_list`/`cleanup-stale`/`halt --all` は `sessions/*.json` も走査する。
- **migrate**: `mission-migrate.py` は loop_active=true の進行中 state を拒否 (`--force` で override)。

session_id は `MISSION_SESSION_ID` 未指定なら `cc-`/`cx-`/`pid-<N>` から自動採番される (上記 resolve_session_id 順)。状態は `.mission-state/sessions/<session_id>.json`、active 一覧は `.mission-state/aggregate.json` に保存される。

**assumptions の分離 (H3, 2026-06-10)**: multi-session init は `assumptions_path` を `.mission-state/sessions/<session_id>-assumptions.md` に自動設定する。並走セッションが `.mission-state/assumptions.md` を共有して相互上書きする事故 (2026-06-10 workspace で実害確認) を防ぐため、orchestrator は **必ず state.json の `assumptions_path` を読んでそのパスに書く** こと (固定パス直書き禁止)。

- Stop hook (`mission-stop-guard.sh`) は `sessions/*.json` を自動的にイテレートし、各 session に対して project_root + PID alive チェックを適用する
- dead pid の session は hook が自動的に halt する

### Phase C: 管理コマンド

```bash
# 全プロジェクトの active 一覧
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py list

# 全プロジェクトを一括 halt
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py halt --all --reason "<理由>"

# dead-PID の active state.json を検出 (dry-run デフォルト)。--root で対象を絞る
# (省略時は MISSION_SEARCH_ROOTS、未設定なら cwd を rglob する)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py cleanup-stale --root "$(pwd)"

# 実際に halt 実行 (--execute 明示が必要)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py cleanup-stale --root "$(pwd)" --execute
```

⚠️ `cleanup-stale --execute` は他 Claude セッションで進行中のミッションも halt する可能性がある。`--root "$(pwd)"` で対象を絞り、事前に dry-run で `would_halt` を確認すること。

### Phase C: 旧 state.json → sessions/ 移行 (任意)

既存の single state.json を multi-session 構造に変換するスクリプト:

```bash
# 全プロジェクト dry-run
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-migrate.py

# 特定プロジェクトのみ
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-migrate.py /path/to/project

# 実行 (元 state.json は state.json.pre-migration として保管)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-migrate.py --execute

# 元 state.json も削除して完全移行
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-migrate.py --execute --remove-legacy
```

移行は **過去セッションの stats 継続性のためのみ** (新規 init は常に `sessions/<sid>.json` を使用)。legacy `state.json` は読まれず無害に残る。

---

## phase フィールドの更新セマンティクス (M4, 2026-06-10 / 2026-06-25)

mission-state.py は開始・採点・終了の境界で `phase` を自動設定する。orchestrator は、実作業やレビューに入る境界を `set phase=...` で明示する。これを省略すると長時間 run が `planning` に粗く帰属し、audit の slow-session 分析で `coarse-phase-attribution` として検出される。

| コマンド | phase |
|---|---|
| `init` | `planning` |
| `codex-preflight` | 変更なし (read-only) |
| `set phase=executing` | Phase 3 実行開始前に orchestrator が明示 |
| `set phase=reviewing` | Phase 4 レビュー開始前に orchestrator が明示 |
| `push-score` | `scoring` |
| `mark-passes` | `done` |
| `mark-halt` / `halt --all` | `halted` |
| `cleanup-stale --execute` (orphan halt) | **変更しない** (refresh-pid 再活性化後に直前の進捗 phase を保持するため) |

標準的な Phase 2-6 の境界更新:

```bash
# Phase 3: 実装・調査開始
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py set phase=executing

# Phase 4: review 開始
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py set phase=reviewing

# Phase 5: scoring 完了時 (自動で phase=scoring)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py push-score ...
```

R1 復帰時は phase 単独ではなく `iteration` + `score_history` と組み合わせて現在地を判定する (phase は補助情報。2026-06-10 以前のランは phase が planning のまま放置されている)。

## progress checkpoint の運用

10分を超える作業、複数 batch、または compaction を挟みやすい調査では、`progress update` を観測用 checkpoint として使う。`progress` は pass/fail 判定を変更しないが、audit と resume 手順が実進捗を復元するための evidence になる。

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py progress update \
    --total 4 \
    --completed 2 \
    --last-unit "targeted pytest" \
    --artifact ".mission-state/archive/iter-1-<mission8>-progress.md" \
    --json
```

## review_tier 導出と Light Tier 運用 (Issue #168, 2026-07-10)

`review_tier`（`light` / `standard` / `full`）は `init` 時に complexity とミッション記述から自動導出される。導出根拠は `review_tier_source`（`"auto"` or `"user"`）と `review_tier_signals`（エスカレータ理由リスト）として state に記録する。

### ベースマッピング (REVIEW_TIER_BASE)

| complexity | base review_tier |
|---|---|
| Simple | light |
| Standard | standard |
| Complex | full |
| Critical | full |
| None / Unknown / 未知文字列 | standard（安全側フォールバック） |

### エスカレータ条件（いずれか該当で `full` に昇格。降格なし）

| シグナル | トリガー |
|---|---|
| `task_profile.risk=high` | task_profile の risk フィールドが `"high"` |
| 不可逆系英語キーワード | `deploy` / `release` / `migration` / `drop` / `delete` / `publish` / `production` / `push` / `merge`（小文字化して部分一致） |
| 不可逆系日本語キーワード | `本番` / `リリース` / `マイグレーション` / `削除` / `公開` / `決済`（そのまま部分一致） |
| セキュリティ系英語キーワード | `auth` / `secret` / `token` / `credential` / `password`（小文字化して部分一致） |
| セキュリティ系日本語キーワード | `認証` / `秘密` / `鍵` |

マッチしたキーワードは `review_tier_signals` に `irreversible-keyword:<kw>` / `security-keyword:<kw>` 形式で記録される。エスカレータは conservative 設計（`release`・`merge`・`push` 等の頻出語も full 昇格する）。実運用データに基づくキャリブレーションは後続タスク。

### tier と reviewer_count の対応 (TIER_REVIEWER_COUNT)

| review_tier | reviewer_count |
|---|---|
| light | 1 |
| standard | 2 |
| full | 3 |

**Light tier 追加制約**: `required=true` specialist のみ auto-select（optional は対象外）。critic は fail 時（High 指摘解消が必要な次 iteration）のみ spawn。

### ゲート意味論は不変

`review_tier` は pass/fail 判定を変更しない。threshold / open_high / findings_evidence_path / halt 条件はすべての tier で同じ。

### User override

`init --review-tier <light|standard|full>` または `set review_tier=<値>` で上書き可能。auto 導出より低い tier を指定すると `stderr` に `WARNING` を出すが拒否しない（`review_tier_source` は `"user"` に設定）。`review_tier_source=auto` の状態で `complexity` を `set` で変更すると tier が再導出される。`review_tier_source=user` の場合は complexity 変更でも tier を維持する。

### 効果測定 (Issue #180)

`mission-state.py stats` の JSON 出力（`--json` フラグ）に `by_review_tier`（tier 別 total/pass/halt/incomplete/abandoned）と `iteration_by_review_tier`（tier ごとの iteration ヒストグラム）が含まれる。`light` が `full` より平均 iteration を増やしていないかを `iteration_by_review_tier` で定期確認すること。`review_tier` フィールドを持たない旧 state は `"unknown"` キーに集計される。

---

## init --complexity (M7, 2026-06-10)

`init --complexity <Simple|Standard|Complex|Critical>` で Phase 1 の複雑度判定を state に記録し、`reviewer_count` を自動設定する (Simple:1 / Standard:2 / Complex:3 / Critical:3)。未指定時は `Unknown` のまま stderr に WARN が出る。Phase 1 完了時点で `Unknown` を残さないこと。

## stagnation_count / awaiting_user

- `stagnation_count` は `push-score` が自動更新する。初回 push または composite が 0.1 以上改善した場合は 0、改善幅が `0 <= delta < 0.1` の場合は +1、スコア低下時は 0 に戻す。orchestrator が `set stagnation_count=...` で手動補正しない。
- Trigger 1 などで人間確認待ちに入る場合は `mission-state.py set awaiting_user=true` を先に記録する。Stop hook は `awaiting_user=true` の session を stale auto-halt 対象から除外する。再開時は `awaiting_user=false` に戻してから作業を続ける。
- Stop hook が bash/jq で直接書く `orphan:` / `stale:` halt は macOS portable lock の制約上、`aggregate.json` から即時除去しない。次回 `cleanup-stale --root "$(pwd)" --execute` または `refresh-pid` の再活性化で遅延回収するのが正式仕様。

## スコア項目キーの正規化 (H2) / scoring archive 命名 (H1) — 2026-06-10

- push-score は items のキーを正規 5 キー (`mission_achievement` / `accuracy` / `completeness` / `usability` / `reviewer_consensus`) に正規化する。エイリアス (`usefulness`→`usability`, `practicality`→`usability`, `reviewer_agreement`→`reviewer_consensus`) は自動変換。未知キーは `--items` 経路では WARN 付きで受理 (後方互換) だが、`--scoring-json` 経路では reject (strict)
- push-score は経路を問わず「全 items が 1.0 以下」を 0-1 正規化スケール混入として reject する (実ログ回帰: xai-cli cx-019efece が composite 0.96 = 4.8/5 を push した事例)
- `--scoring-output` の保存先は `.mission-state/archive/iter-<N>-<mission_id先頭8>-scoring.md`。連続ランでの上書き消失 (2026-06-10 実害確認) を防ぐため mission_id を含む

### GitHub Flow (issue 連携)

GitHub issue に紐づくミッションは次のフローで進める:
issue 起票 → worktree feature ブランチ → PR (本文に `Closes #N` を記載) → Phase 7 マージ → issue 自動クローズ。

- `init --issue-ref <owner/repo#N>` で issue を state に記録する (S3 重複 WARN も兼ねる)。
- PR 作成時、本文に `Closes #N` を必ず入れる (N は issue 番号)。これによりマージで issue が自動クローズされる。
- Phase 7 のマージ前に PR 本文へ `Closes #N` が含まれることを確認し、欠けていれば `gh pr edit <PR番号> --body` で追記してからマージする。
- これは reject しない補助規律 (issue 連携がないミッションには影響しない・後方互換)。

## Phase 7 自動マージ — 詳細判定ロジック

> SKILL.md 本体の「## Phase 7」から退避 (2026-06-10)。合格判定後に PR を自動マージしてよいか迷ったとき参照。

### 自動マージ NG の判定ロジック (どれかに該当したら手動マージ待ち)

- **明示 opt-in がない**: ユーザー指示または `.mission/` 等のプロジェクト設定で自動マージが許可されていない場合、既定は手動マージ待ち。
- **CI チェック 0 件**: `gh pr checks` がチェックを 1 件も返さない場合、CI 不在として自動マージ不可。空集合を success と扱わない。
- **PR template / CLAUDE.md / CONTRIBUTING.md に明示的な禁止文言**: 「自動マージ禁止」「人手のみ」「manual merge only」「approval required」「自動修正禁止」等
  - 例: PR template に Lv (重要度) 判定があり「Lv1 のみ自動マージ可・Lv2 以上は人手 only」と明記しているリポジトリでは、Lv2 以上が選択されていたら自動マージ不可
- **PR body / PR コメント / commit message の禁止文言**: PR 由来の自由記述は「禁止」判定にだけ使う。「マージしてよい」「承認済み」等の許可文言は根拠にしない。
- **PR body の Lv 判定 / 重要度ラベルで「人手」相当が選択されている**: 当該リポジトリの慣習に従う
- **CODEOWNERS で必須レビュアー指定**: `.github/CODEOWNERS` が存在し、変更ファイルの owner レビューが未完了
- **branch protection の required reviewers > 0**: `gh api repos/<owner>/<repo>/branches/<base>/protection` で `required_pull_request_reviews.required_approving_review_count > 0` (エラーなら不問)
- **PR が draft 状態** or **必須チェック未完了**: `gh pr view <N> --json isDraft,mergeStateStatus`
- **不明な場合は保守的に「手動マージ待ち」を選ぶ** (false positive コストは低い、false negative コストは高い)

### マージコマンドの選び方

- 既存リポジトリの慣習を尊重: `gh pr list --state merged --limit 5 --json mergeCommit,title` で過去マージ方式 (squash / merge / rebase) を推定
- 不明なら `--squash` をデフォルトに
- CI pending 中なら `--auto` フラグで完了待ち
- 推奨形: `gh pr merge <N> --repo <owner/repo> --squash --auto --delete-branch`
