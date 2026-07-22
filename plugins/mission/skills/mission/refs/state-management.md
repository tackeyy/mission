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

# 中断マーク (halt_reason 設定, loop_active=false)。
# --category (#190) は halt の種別を構造化する: blocked-external / awaiting-approval /
# partial-done / stagnation / user-abort / stale / other。省略・不正値は 'other' + WARN
# (halt 自体は緊急停止経路なので category 不正で halt そのものは失敗させない)。
# stats/audit の by_halt_category / halt_incomplete_breakdown で集計され、
# 「完了しました」等の完了風自由文と threshold 未達 halt (partial-done) を区別できる。
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py mark-halt --reason "<理由>" --category partial-done

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

# Codex startup health check (Issue #108 / #144):
# - active state があるか
# - Codex user hook に mission-stop-guard.sh が登録されているか
# - hook 無しでも `next` fallback で進行できるか
# を JSON で診断する。task setup / worktree / 実装前の開始ゲートは --strict のexit 0を必須とする。
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py codex-preflight --json --strict
# 非strictはread-onlyの診断専用。required actionがあってもexit 0を維持するため、開始ゲートには使わない。
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py codex-preflight --json
# Stop hook自体も必須にする場合は、開始時の--strictへ--require-stop-hookを追加する。
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py codex-preflight --json --strict --require-stop-hook

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

### Activity segment observability (#211)

`phase_durations_sec` の wall-clock 意味論を維持したまま、作業と待機を明示的に区別する。`activity start` は既存 open segment を閉じて新しい segment を開き、`activity end` は閉じる。どちらも state lock 内で atomic write され、同一操作の再実行は duration を二重加算しない。phase 遷移は open segment を同じ kind/reason のまま split し、`done` / `halted` は open segment を閉じる。

```bash
mission-state.py activity start --kind active --reason <work|implementation|planning|review|scoring|resumed-implementation|other> [--detail "..."]
mission-state.py activity start --kind external-wait --reason <external-response|external-command|other>
mission-state.py activity start --kind approval-wait --reason <user-approval|policy-approval|other>
mission-state.py activity start --kind reviewer-wait --reason <review-response|independent-review|other>
mission-state.py activity start --kind idle --reason <no-runnable-work|interrupted|other>
mission-state.py activity end
mission-state.py advance --phase <planning|executing|reviewing|scoring> --activity <kind>:<reason> [--detail "..."] [--at ISO]
```

**atomic `advance` (#237)**: phase 遷移と activity 切替を単一 lock・単一 write で行う。`set phase=` + `activity start` の 2 コマンド運用では「phase だけ進んで activity が空」の state を作れてしまい、activity coverage 欠損 (strict cohort 実測 9.96%) の構造要因になる。phase 境界では advance を優先する。検証 (phase 正規化 #188 / kind:reason enum) は lock 取得前に行い、不正入力では一切 write しない。terminal phase (`done`/`halted`) への遷移は `mark-passes` / `mark-halt` 専用であり advance は reject する (gate 迂回の防止)。同一 phase を指定した場合は activity 切替のみ行う (旧 segment を閉じて記録)。

reason は kind ごとの enum から明示し、未知の原因を推測しない。detail は制御文字と改行を除去し、空白を正規化して160文字に制限する。crash 後の `resume` / `refresh-pid` / 同一 mission の `init` と、自動stale/orphan cleanup・Stop hookは、open segment を最後の有効な `updated_at` まで一度だけ閉じる。その後の空白時間は `activity_unobserved_gap_sec` であり、work/idleには分類しない。明示的な `mark-passes` / `mark-halt` / `halt --all` は、現在観測中の遷移を宣言するため制御時刻まで閉じる。自動stale haltは停止前phaseを `resume_target_phase` に保存し、`refresh-pid` がそのphaseを復元してresume時刻から再開する。明示haltは自動復帰しない。

state は `activity_current`、直近32件の `activity_segments`、固定 map の `activity_rollup` を持つ。古い raw segment を落としても rollup が全期間の duration を保持する。`stats` と `mission-audit.py` は同じ reducer を使い、task/phase p50・p90（linear interpolation R7）、kind/reason totals、coverage、unclassified、open/invalid counts を同じ定義で返す。非terminal current phase は `phase_started_at` から persisted `updated_at` までを coverage denominator に含め、未遷移だけを理由に100%を超えない。live/archive duplicate は正規化した `(project_root, session_id, mission_id)` と status/newest/path の共通 precedence で1件へ正規化する。project_root欠落時はstate fileを所有するproject pathを補完し、別projectの同一sid/midを潰さない。task key は `mission_id`、欠落時は `unknown`。open、negative、non-finite、未知 enum、必須map欠落、不整合 rollup、有限値同士の加算overflowは percentile/coverage/aggregate から除外する。JSON出力は非標準の `Infinity` / `NaN` に依存しない。activity のない旧 state は phase duration を unclassified とし、理由は補完しない。

terminal state は新しい activity start を拒否する。pass/halt/cleanup/Stop hook の terminal writer は lock 内で state を再読・再検証し、open segment を閉じる。bulk writerの制御時刻はlock取得後に採番し、最新 `updated_at` より前へは戻さない。activity_current が壊れていても terminal control は失敗させず、current を除去して `activity_anomaly_counts.invalid-current-terminal` に記録する。phase timingが壊れている場合もterminal controlを継続し、既存durationを捏造・置換せず `invalid-phase-terminal` anomalyを記録する。同一 mission の init/resume は逆に不正な open measurement を検出したら no-write で拒否し、履歴をfresh stateで上書きしない。

収集とgroup化は state/segment件数に線形、exact R7 percentile は最大sample groupのsortにより `O(N log N)`。recent rawは32件、rollupは固定mapのため、session単位の読込サイズはbounded。

この観測は reviewer 数、threshold、findings evidence、agreement、`open_high`、pass/fail、自動 retry、watchdog を変更しない。速度改善は品質ゲートを維持したまま分布を比較して判断する。設計判断は `docs/adr/004-activity-segment-observability.md` を参照。

`mission-audit.py --current-since` は、検出関数が返したrecord/itemを共通`AuditFinding` modelへ変換した後、scope timestampでcurrent/historicalに分類する。scopeには`updated_at`を使い、specialist checkpoint rollout判定の`started_at`とは混同しない。`--since` / `--until` / `--current-since`は同じdate/ISO parserを使う。cutoffはUTCへ正規化し同値をcurrentとする。timestamp欠落・不正は安全側のcurrent。JSONは`all_findings` / `current_findings` / `historical_findings`をevidenceの基準一覧とし、severity・code別countとcode別のcompactなcount/indexを返す。Markdownはregistryから生成したCurrent Findingsの後にHistorical Risksを出力する。各priority/codeで`current + historical = all`を維持する。cutoff未指定時は`current = all`、`historical = []`として従来の全期間findingを維持する。historical riskは元priorityとprovenanceを保持するがself-improvement promptの現行blockerには渡さない。これはreportingのみの変更であり、current severity、required specialist result gate、force approval gateは変更しない。

`specialists accounting` は available candidate のうち selected / invoked / skipped / unavailable / failed 等の terminal decision trail がないものを表示する。`Critical` と high-risk は全 available candidate、`Complex` は security/testing/infra と、schema/migration/query/persistence 等の強いシグナルがある database/backend candidate を重点対象にする。これは optional provider を blanket hard gate にするものではなく、ハッカブルな plugin/provider 拡張性を保ちながら判断理由を監査可能にするための pre-completion warning である。ただし `required: true` provider は stricter gate で、`prepared` / `awaiting-input` / `skipped` / `failed` だけでは結果証跡と見なさず、`completed` / `inline-applied` / `skill-tool-applied` のいずれかが無い限り `mark-passes` が exit 2 で拒否する。

**#189 (自動 WARN)**: `mark-passes` は成功時、`specialists_selected` にあるが `specialist_invocations` に一件も (skipped/unavailable/failed 等どのステータスでも) 記録のない specialist を stderr WARN で列挙する (呼び出し不要・自動発火。`specialists accounting` の手動確認とは別。判定は `specialists_phase_plan` の providers を含まない `specialists_selected` のみを対象にし、phase_plan にしか登場しない specialist を誤検知しない)。非 `--force` 経路では required specialist は accounting_required/result_required gate がここに到達する前に exit 2 で止めるため、この WARN の対象は常に optional。`--force` はこれらの gate ごと skip するため、`--force` 経路ではこの WARN 自体を出さない (required specialist が混入していた場合に「optional のため」という文言が誤りになるのを避ける)。`mission-state.py next` も `mark-passes` action の `details.unclosed_specialists` に同じ一覧を含める。hard gate ではないため mark-passes 自体は成功するが、`specialists log-invocation --status skipped --reason "<理由>"` 等でクローズアウトしておくのが望ましい。

`progress update` は `.mission-state/archive/iter-<N>-<mission8>-progress.md` に checkpoint を保存し、state の `progress` に `total` / `completed` / `remaining` / `last_unit` / `artifact_path` / `evidence_path` を記録する。これは score や pass/fail を変更しない観測用データであり、長時間 batch が compaction や中断を挟んでも audit report の slow session 行から進捗を復元できるようにする。

### worktree state/evidence の整合性付き保存 (#212)

worktree を削除する前に、終端済みの現 session と、その state が参照する evidence を main checkout 側へ保存する。`loop_active=true` は拒否されるため、先に `mark-passes` または `mark-halt` を実行する。destination は存在する directory であり、source と同じ Git common directory に属する別 checkout の root でなければならない。未作成 path、非 Git directory、別 repository、source 自身は書き込み前に拒否する。

```bash
# worktree root で実行。書き込み前の検証だけなら --dry-run を付ける
python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py archive-worktree \
    --destination-root <main-checkout-path> \
    --json
```

保存先は `<main-checkout>/.mission-state/archive/worktree-<slug>-<source-hash>/`。`current.json` (`mission-worktree-current/1`) が `generations/<content-digest>/` の現行世代を指す。各 immutable generation は既存監査と互換の `sessions/<session_id>.json`、`archive/iter-<N>-<mission8>-scoring.{json,md}` / `reviews` 配置を維持し、`manifest.json` に次を記録する。

- schema、session ID、mission ID、iteration
- evidence type、`.mission-state/` 起点の source reference、bundle 内の archive relative path
- 各 evidence の SHA-256 と byte size

対象は現 session の state に明示された assumptions、artifact、score history の scoring/reviews、specialist invocation、progress evidence の allowlist のみ。元 state の identity や内容は書き換えず byte copy する。必須 evidence の欠落、`.mission-state` 外への path escape、symlink、重複 archive path、manifest/checksum 不整合は exit 2 で fail-closed になり、不完全な世代を current として公開しない。同一 filesystem 内で一時世代を完成させ、content digest 名の immutable generation として publish してから `current.json` だけを atomic replace する。更新中・pointer swap 失敗時も reader は旧世代を参照でき、同じ入力の再実行は `action=unchanged` となる。旧世代は reader safety のため自動削除しない。

`mission-audit.py` は discovery 時に pointer が示した generation を record snapshot として固定し、同じ audit run の途中で `current.json` が進んでも別世代を再読込しない。record をロードする前に bundle 単位で manifest と archived state の JSON・identity・path・size・checksum を preflight し、その state から導出した `(evidence type, iteration, source reference)` multiset との完全一致を検証する。valid manifest の scoring と specialist evidence は kind・iteration・source reference で generation 内の検証済み path を解決する。1 audit run では同じ record の検証結果を cache し、score history の iteration ごとに全 evidence を再 hash しない。`.mission-state` は walk の降下前に `lstat` と `scandir` で readiness を確認し、競合する access failure も walk error callback から収集する。`.mission-state` / `archive` が directory 以外の file type である場合、それらや bundle / `generations` ancestor の symlink、非symlink archive root 外へ解決される bundle、archive / pointer / generation の stat・scan・read access failure、pointer の malformed/symlink/参照先欠落、archived state の欠落/不正 JSON、または generation の manifest 欠落・不整合は `invalid-worktree-archive` finding として明示し、root 外や旧来の filename fallback に進まない。overlap する複数 root から同じ不正 bundle を発見しても canonical bundle path ごとに1件へまとめる。pointer が存在しないことを `lstat` で確認できた既存 bundle だけは #201 までの配置互換を維持する。

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

`review_tier`（`light` / `standard` / `full`）は `init` 時に complexity とミッション記述から自動導出される。導出根拠は `review_tier_source`（`"auto"` or `"user"`）、後方互換な `review_tier_signals`（エスカレータ理由の文字列リスト）、各候補の判断を示す `review_tier_signal_details` として state に記録する。

### ベースマッピング (REVIEW_TIER_BASE)

| complexity | base review_tier |
|---|---|
| Simple | light |
| Standard | standard |
| Complex | full |
| Critical | full |
| None / Unknown / 未知文字列 | standard（安全側フォールバック） |

### エスカレータ条件（いずれかの候補を採用すると `full` に昇格。ベース tier の降格なし）

| シグナル | トリガー |
|---|---|
| `task_profile.risk=high` | task_profile の risk フィールドが `"high"` |
| 不可逆系英語キーワード | `deploy` / `release` / `migration` / `drop` / `delete` / `publish` / `production`（大文字小文字を区別しない部分一致） |
| 不可逆系日本語キーワード | `本番` / `リリース` / `マイグレーション` / `データ削除` / `レコード削除` / `物理削除` / `公開` / `決済`（部分一致） |
| セキュリティ系英語キーワード | `secret` / `credential` / `password` / `api token` / `api-token` / `api_key` / `access token` / `access-token` / `bearer` / `authenticat` / `authoriz` / `oauth`（大文字小文字を区別しない部分一致） |
| セキュリティ系日本語キーワード | `認証` / `秘密` / `鍵` |

不可逆系キーワードはすべての出現を、文・対比接続詞で区切った clause と、段落・list item・blockquote・heading で区切った logical unit の文脈で評価する。`deploy しない`、`do not deploy`、`will not perform a production migration`、`対象外` など、実操作を明示的かつ単純に否定した候補だけを抑制する。否定は 48 文字以内に cue があるだけでは成立せず、キーワード直前の `do not` / `not` / `won't` / `cannot`、active な `not perform/execute`、passive な `will/should not be performed/executed`、直後の `しない` / `する予定はない` / `行われない` / `禁止` / `対象外` など、対象 operation への文法的な結び付きを確認する。未知の connector や別 operation をまたぐ場合は安全側で採用する。`unless` / `without approval` / `except when` / `until` / `pending approval` / `限り` / `以外` / `除き` / `原則` / `例外` / `緊急時` / `ことがある` の例外条件、否定方針そのものの否定、modal / contraction から始まる `not not`、`not the case that` / `not saying that` / `cannot say that` など外側から単純否定を反転する二重否定、不確実表現も安全側で採用する。同じ context の複数否定 cue は次の operation より前にあるものだけを反転否定として数え、別 operation の単純否定同士は結合しない。logical unit 内の「実操作は行わない」という global marker は位置だけでは候補を抑制せず、候補 context が手順の調査・確認・説明・文書化などの明確な meta/non-operation intent と証明できる場合だけ抑制する。同じ logical unit に `execute` / `run` / `perform` / `carry out` / `follow/apply + pronoun` / `実行` / `実施` / `行う` / `反映` / `適用` / `従う` などの execution cue があり、別の named operation への直接的な係り先を証明できない場合は `ambiguous-execution-reference` として安全側で採用する。引用だけが目的だと明示された場合のみ引用内の候補を抑制し、別 operation の `execute` や引用内の `execute` は実行 intent とみなさない。引用符の直前・直後から当該 command の実行を直接指示した場合、または引用直後の passive modal が実行を示す場合だけ採用する。境界、context flags、operation start、quote span、meta/non-operation span、否定 operation span、否定 cue position、global marker span は mission / context ごとに一度だけ索引化・cache し、出現ごとの判定で全文や同一 dense context を再走査しない。`task_profile.risk=high` と security キーワードは否定文脈でも常に採用する。Complex / Critical のベース tier も変えない。

`例外なく` / `緊急時にも` / `原則ではなく絶対に` は例外の存在を示さない強い単純否定として扱う。また、`実操作しないので問題/支障/懸念なし` のような因果的な安心表明は、対象 operation の否定を反転しない。

英語の否定 auxiliary は短縮形全体を同じ operation scope 文法で扱い、`never not` と be 短縮形の外側報告否定も二重否定に含める。approval gate は `before/prior to approval` と `while approval is pending` も条件付きとする。曖昧実行照応は `follow` / `apply` / `proceed with` が pronoun または named procedure を受ける場合を含む。因果的な安心表明の述語には `影響はない` も含む。
外側の不確実表現は `not true that` と `no guarantee/assurance/certainty that` も含み、内側 operation の modal 否定が短縮形でも二重否定として採用する。外側の reporting negation と内側の modal negation はどちらも auxiliary 短縮形を共通に扱う。

meta/non-operation の証明は context 全体が `review/analyze/document/inspect + procedure/settings/log/text` または `手順/設定/文言/ログ + 調査/確認/分析/説明/文書化` の strict meta-only 文法に一致することを要求する。meta span の外に未知の後段句が残る場合は抑制しない。`ambiguous-execution-reference` の判定では quote span 内の execution cue を除外し、引用外の cue だけを veto 対象にする。quote-only の抑制も quote span・quote-only 語句・無害な終端・別 named operation への明示 action を除いた外側残余が空であることを要求し、未知句や代名詞参照が残る場合は安全側で採用する。

採用したキーワードは従来どおり `review_tier_signals` に `irreversible-keyword:<kw>` / `security-keyword:<kw>` 形式で、定数順・同一キーワード 1 件として記録する。additive な `review_tier_signal_details` は採用・抑制を問わず各出現を記録し、`match` / `context` / `decision` / `reason` / `source` / `start` / `end` から判定を追跡できる。`get` はこの field を state の一部として出力する。field を持たない旧 state の `get` / `next` / `set` は引き続き動作し、auto source で complexity を再設定した時に details が生成される。

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

`init --review-tier <light|standard|full>` または `set review_tier=<値>` で上書き可能。auto 導出より低い tier を指定すると `stderr` に `WARNING` を出すが拒否しない（`review_tier_source` は `"user"` に設定）。`review_tier_source=auto` の状態で `complexity` を `set` で変更すると tier が再導出される。`review_tier_source=user` の場合は complexity 変更でも tier を維持する。既存 state を `set review_tier=` で上書きした後も signals / details は観測された候補の provenance として保持するが、source が `user` の場合、それらは適用 tier の根拠ではない。`init --review-tier` で最初からユーザー指定した state の signals / details は空になる。

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
