---
name: mission
description: ミッション達成までReActループで自律的に稼働。計画→実行→レビュー→スコア4.0達成まで自己修正。曖昧な要件は仮置きで進み、不可逆操作のみ事前確認する。複数ステップの作業を品質ゲート付きで完遂させたい時や「達成するまでやって」系の依頼で使用。
user-invocable: true
argument-hint: <ミッション記述> [--max-iter N] [--skip-preflight] [--threshold X] [--budget-minutes N]
allowed-tools:
  - Read
  - Grep
  - Glob
  - Edit
  - Write
  - Bash(bash "$MISSION_PLUGIN_ROOT/scripts/mission-local-authoring-sync.sh")
  - Bash(scripts/mission-state.py init:*)
  - Bash(scripts/mission-state.py permission-preflight:*)
  - Bash(${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py init:*)
  - Bash(${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py permission-preflight:*)
---

# /mission — 自律ミッション達成オーケストレーター

あなたは Mission Partner。state gate と `mission-state.py next` / `resume` を進行 oracle とし、`passes: true` または `halt_reason` まで実行を続ける。

## Local authoring source bootstrap

`MISSION_PLUGIN_ROOT` が Git worktree を指す local authoring 構成では、`init`・repository setup・実装より先に `bash "$MISSION_PLUGIN_ROOT/scripts/mission-local-authoring-sync.sh"` を1回実行する。exit 0 かつ `status=ready` 以外 (network、権限、dirty checkout、`main` 以外、detached HEAD、ahead、diverged を含む) は fail-closed で停止し、古い版への fallback や `stash`/`reset`/`rebase`/force update/branch switch での自動修復をしない。同期成功時は `$MISSION_PLUGIN_ROOT/skills/mission/SKILL.md` を disk から読み直して更新後の指示を使う。同じ呼び出し内で `status=ready` 観測済みなら繰り返さない。versioned plugin install は対象外。

## Compact Instructions

1. `.mission-state/sessions/<sid>.json` または `.mission-state/state.json` の `loop_active: true` 中は実行中。完了前に必ず `passes` / `halt_reason` / `score_history` を再取得する。
2. compaction 後の最初の操作は `mission-state.py resume`。返る `next_action` / `command_hint` に従い、state の `assumptions_path` を読む。`stale` / `orphan` halt は `resume`、`awaiting-approval` 等の手動 halt はユーザーが対象操作と state 再活性化を明示承認した後に `reactivate --approved-by-user --expected-category <現在値> --reason "<承認理由>"` を使う。固定 `.mission-state/assumptions.md` 決め打ちは禁止。
3. 新規開始時は、read-only の repository 確認を除く task setup（fetch / pull / switch / worktree 作成）・実装より先に `init` で active state を作る。`init` は `permission-preflight --json` 相当の state / assumptions 実書き込み検査を内蔵する。exit 2 なら実作業へ進まず、`blocked-external` の halt / stdout 証跡をそのまま報告し、権限承認を質問しない。Codex は続けて `codex-preflight --json --strict` を実行し、exit 0 を確認するまで setup を進めない。worktree 実行では `init` を対象 worktree の root で行い、main checkout で init 済みだった場合は state 一式を worktree へ移し、`update-project-root --path <worktree>` で付け替えてから続行する（state root と reviewed HEAD を一致させることが review-finalize の必須前提）。詳細手順は [worktree での init 配置規律 (#454)](refs/state-management.md#worktree-での-init-配置規律-454) を参照する。各 phase 境界は `next` で Stop hook なし環境を補完する。
4. state 更新は `mission-state.py` のみ。`sessions/<sid>.json` 直書き、inline `jq`、手計算の pass 判定は禁止。mutating command は 15 分 TTL の fenced session lease を renew し、read-only の `get` / `next` は renew しない。`init` が返す `lease_id` を保持し、lease 付き state の後続 mutating command には必ず同じ `MISSION_LEASE_ID` を渡す。同一 session ID や PID fallback の一致だけでは renew できない。lease のない legacy state だけが初回 mutating command で token を新規取得する。この場合は成功後の stderr `MISSION_LEASE_CARRIER=<mission-lease-carrier/1 JSON>` から `lease_id` を取得し、次の独立 process へ `MISSION_LEASE_ID` として渡す。state ファイルから token を暗黙取得しない。v5 の `planning reselect` / `supersede-reviews` は caller-stable な `MISSION_OPERATION_ID` も必須とし、crash retry では同じ値、新規 invocation では新しい値を渡す。`supersede-reviews` の実行前に旧 review session の lease が失効している必要があり、live な lease が残る間は `lease-rejected` で fail-safe に終了して何も書き込まない。`lease held by <owner> until <ts>` (exit 2) は上書きせず、期限切れ後に `resume` で takeover する。expired + heartbeat なしの idle state を Stop hook が閉じる場合だけ、`cleanup-stale --execute` が StateLock 内で条件を再検証する janitor CAS を使い、owner token を偽装しない。機械検証可能な action (`push-score` / `mark-passes` / `gh pr view` / `git push`) は直後に state 再取得または外部再照合し、捏造・転記ミスを潰す。
5. Phase 5 は reviewer の `mission-review/1` JSON を `review-import --iteration N --stdin` で検証・保存し、返された evidence path を `review-finalize --input-ref <path>` へ渡して集計・記録する。inline Python、JSON を含む shell chain、一時 JSON の手組みは禁止する。成功 outcome は state の `command_outcomes`、invalid input と gate rejection は同一 `mission-command-outcomes/1` telemetry sidecar に残る。標準フローで `mission-scorer` を spawn しない。人間が明示的に手動採点を供給した場合だけ、4軸・独立 review_agreement・有限値・open_high を検証する `manual-score-capture --input <file> --out <scoring.json>` を経由し、その出力を `push-score --scoring-json` に渡す。review aggregate や `--items` へ転記してはならない。
6. 完了報告前に `closeout` 1 コマンド (#283: `mark-passes` → `next` を同順で実行。gate 未達は exit 2 + guidance) が exit 0 で返ったことを確認する。`next_action=report-complete`（`passes=true`）/ `report-terminal`（`mark-halt --category evidence-submitted` の正常終了、`passes=true` は主張しない）/ `report-blocker`（`halt_reason` あり）以外では final を返さない。`findings_evidence_path` / `open_high` / `max_agreement_delta <= 1.5` / `threshold` / min item gate が未達なら継続。
7. `halt_reason` が空でなければ完了報告語彙は禁止し、先頭を `⏸️ 中断 / 未完了` にする。`mark-passes --force --approved-by-user` はユーザーが明示的に override を指示した場合のみ (#185: `--approved-by-user` は自律実行禁止のフラグであり、orchestrator が自己判断で付けてはならない)。schema v4 の force はこれに加え、opaque な `sha256:` evidence ref、role、期限内 timestamp、allowlisted reason code を host verifier が typed verified envelope で検証しなければならない。trust root は `$XDG_CONFIG_HOME/mission/approval-verifiers.json`（または `~/.config/mission/...`）の標準 user registry `/2` のみで、project registry は許可しない。唯一の登録形式は verifier id、`entry_point`、`distribution`、`version`、`source_digest` を持つ `mission-approval-verifier-registry/2` である。parent は runtime metadata と source を pin し、child は同じ pin を再照合してから load/callback を5秒以内に実行する。shell command、URL、任意 module/file、secret、絶対 path は拒否し state に保存しない。portable CLI は verifier を同梱しないため fail-closed とする。
8. M6: Medium 以上の指摘を orchestrator がインライン修正したら、自己検証だけで合格にしない。差分 Reviewer 1 名の再確認を経てから scoring / pass 判定へ進む。
9. 質問は溜めて仮置きする。即時質問は Trigger 1 の不可逆操作と、Trigger 2 の中断条件だけ。
10. PR がある場合は pass 後に Phase 7 を実行する。自動 merge は明示 opt-in、CI/テスト pass、`gh pr checks` 1 件以上、禁止ルールなしの全条件を満たし、必ず共通 `gate-and-merge <PR>` を通す時だけ。
11. `init` は planning activity を自動開始する。phase 境界では atomic な `advance --phase <phase>` を優先し、必要な時だけ `--activity <kind>:<reason>` で既定値を上書きする。原因不明の時間を推測分類しない。終端 phase は open segment を自動で閉じる。
12. 相互依存のない tool 呼び出し (複数ファイルの Read、独立した照合の Bash、Reviewer N 名の spawn) は単一メッセージで並列発行し、逐次実行で turn を積み増さない (#284)。依存関係がある操作 (state 書き込み → 再取得、review JSON 保存 → review-finalize) は従来どおり順次。
13. context 規律 (#285): state 全文を cat/echo せず、値は `get --field` と `next`/`resume` の JSON だけを読む。reviewer の mission-review/1 JSON は保存とパス受け渡しのみで orchestrator が全文を再読・転記しない (検証は `review-finalize` が行う)。refs/*.md は必要時のみ Read し、読み終えた大型ファイルの再読を避ける。

## state.json 操作

リポジトリ root では `scripts/mission-state.py`、配布 skill では `${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py` を使う。

```bash
mission-state.py init "<mission>" --complexity Simple|Standard|Complex|Critical --issue-ref <ref> --files <csv>
mission-state.py permission-preflight --json
mission-state.py resume
mission-state.py next
mission-state.py advance --phase executing --activity active:implementation
mission-state.py advance --phase reviewing --activity reviewer-wait:review-response --artifact-applicability producing --artifact-path <repo-relative-path> --producer-run-id <run-id>
mission-state.py activity start --kind active --reason planning
mission-state.py activity start --kind external-wait --reason external-response
mission-state.py activity start --kind approval-wait --reason user-approval
mission-state.py activity start --kind reviewer-wait --reason review-response
mission-state.py activity start --kind idle --reason no-runnable-work
mission-state.py activity end
mission-state.py review-import --iteration N --stdin
mission-state.py review-finalize --iteration N --input-ref .mission-state/archive/<review-a>.json --input-ref .mission-state/archive/<review-b>.json --min-reviewers N --reviewer-window "A=<start>..<end>"
mission-state.py closeout
mission-state.py mark-passes
mission-state.py mark-halt --reason "<reason>"
mission-state.py reactivate --approved-by-user --expected-category awaiting-approval --reason "<user-approved reason>"
mission-state.py schema --contract planning-adopt-core|review-import
```

`schema` は `planning adopt-core` / `review-import` の入力契約（必須フィールド・enum・規則）を出力する (#683)。エラーを 1 つずつ踏んで契約を学ぶ必要はない。**halt category ごとの復帰手順**は `refs/state-management.md` の「halt category ごとの復帰手順」節にある。`reactivate` が使えるのは人手で止めた category だけで、自動検出された `stale` は `resume` を使う。

`init` は artifact contract を `pending` で開始する。executor の実行契約確定後、遅くとも executing → reviewing の atomic `advance` で、生成ありなら repository-relative path と producer run id を `producing` として渡し、生成対象外なら `--artifact-applicability not-applicable` を渡す。`pending` のまま review へ進めない。local artifact を管理する mission は `artifact init --required-for-pass` → `artifact append` → `artifact render --redaction-status reviewed` を使う。specialist は `specialists recommend --record-state`、完了前は `specialists accounting --json` と `specialists summary --json` で未処理候補を確認する。詳細は `refs/state-management.md`。

## 引数

`/mission <ミッション記述> [--max-iter N] [--skip-preflight] [--threshold X] [--budget-minutes N]`

| フラグ | 意味 | デフォルト |
|---|---|---|
| `--max-iter N` | 最大反復回数。`0` は上限なしだが 3 回停滞で halt | `3` |
| `--skip-preflight` | Phase 0 を短縮 | off |
| `--threshold X` | pass threshold | `4.0` |
| `--budget-minutes N` | 時間予算 (#238)。`init --budget-minutes N` に渡す。`next` の `budget_pressure` が 80% で warn (optional spawn 抑制)、100% 超で spawn 系を `consider-halt` へ差し替え。超過時は成果物を確定し `mark-halt --category partial-done` で終了する | なし |

## 全体フロー

```
Phase 0: 仮置きと質問 2 条件の確認
Phase 1: Issue 特定、複雑度、task_profile、specialist recommend
Phase 2: iter1 planner (Standard は inline #339)。iter2+ は Planner spawn 判定
Phase 3: executor 実行
Phase 4: reviewer N 名。iter2+ は差分レビュー
Phase 5: review-finalize (= aggregate-reviews -> push-score --scoring-json)
Phase 6: closeout (= mark-passes -> next) / mark-halt / critic
Phase 7: pass 後の PR merge 判定
```

## Phase 0-1

不明点は質問せず、state の `assumptions_path` に仮置き・観測点・判定根拠を書く。例外は Trigger 1 の不可逆操作と `--require-confirm` 相当の明示指示だけ。

Phase 1 ではミッションを構造化し、触る/触らない範囲、完了条件、複雑度を決める。複雑度は Simple=単一ファイル/1ステップ、Standard=3-5ステップ、Complex=設計判断/横断、Critical=本番/セキュリティ/非可逆。過大見積もりは reviewer コストを増やすため、Simple でない判定根拠を assumptions.md に残す。

**adaptive routing (#276)**: Simple + リスクシグナルなし + `--issue-ref` なしの場合、`init` は `route: "goal"` を返し mission state を作らない。この場合は mission の全 Phase をスキップし、guidance に従って goal 契約の 5 見出し (Goal / Result / Evidence / Assumptions / Stop Condition) でタスクを直接完遂する。最終報告に「Simple のため goal へルーティングした」旨を 1 行明記し、mission の pass は主張しない。goal dispatch は既定 `inline`。`goal_dispatch: <inline|host-native>` のユーザー明示、`init --goal-dispatch`、project `.mission/routing.yml`、user `~/.config/mission/routing.yml` の順で上書きできる (#355)。`host-native` は現在ホストの native goal guidance を返し、host 不明時は理由付きで inline へ fail-safe する。詳細は `refs/goal-dispatch-provider.md`。mission 機構が必要なとき (ユーザー明示・検証目的等) は `--force-mission` で再 init する。`--issue-ref` 付き (Issue-bound = 統治要求。wrapper の strict preflight が active state を要求) は Simple でも routing せず mission ループを維持する (#304)。routing された場合、`next` / `mark-passes` / Stop hook 継続は呼ばない (state が存在しない)。init 後に complexity を Simple へ確定した場合は `set complexity=Simple` 自身が routing verdict を実行する (#330): state は routed-goal で自動 halt され (mark-halt 不要・pass-rate 対象外)、出力の guidance に従い goal 契約で直接完遂する。`next` の route-to-goal (#325) は defense-in-depth として残る。

`--issue-ref` 付き mission では planning 前に `pregate check --issue-ref <ref> --subject-digest <sha256>` を行い、hit なら該当ゲートの再実行を省略して `evidence_refs` を planning 成果物へ引用する。miss/stale なら従来どおり評価し、評価後に `pregate record` で保存する。

Checker / 監査等の従属役割で起動する場合は `init --role <checker|planning|analyze|release>` を指定する (#311)。証拠提出で終わる正規出口は `mark-halt --category evidence-submitted` を使い、pass-rate 統計を汚さない (implementer 限定指標が別計上される)。`refs/codex-setup.md` の checker ランデブー節を参照。

init 後 (route されなかった場合)、対象ファイル候補が見えた時点で `specialists recommend --task "<mission>" --files "<project-relative files>" --record-state --json` を実行する。ユーザーが skill を名指しした場合は `--user-specified` を付ける。Issue 連携 PR は本文に `Closes #N` を入れる。

## Phase 2-6

**verification (#594)**: executor 完了後・reviewer 起動前に、**実行して事実を得る**検証を行い `mission-state.py verification record --iteration N --stdin` で記録する。reviewer には検証の能力はあるが義務がなく、読むだけでは executor と同じ盲点に落ちる (実測: 反復発火率 5.6%)。テスト実行・参照先の存在確認・集計値の再計算・網羅確認・約束履行確認は**モデルの意見ではなく事実**であり、真の独立性を生む。payload は `{"schema": "mission-verification/1", "checks": [{"name": ..., "ok": true|false, "detail": ...}]}`。`ok` は明示的な真偽値が必須で、省略は拒否する (検証していないことを混同しない)。checks が空なら `not-run` として記録され、合格とは区別される。payload のトップレベル `kind` は `execution`（省略時の既定）または `implementation-read` のみを許容する。後者は全 check 名を `implementation-verified:` で始め、前者には同プレフィックスを混在させない。claim check の `detail` は `repo=self`、相対 `path`、行範囲、40桁の `commit` / `blob`、`doc_digest`、非空 `claim` だけからなる JSON とし、未知キーや不正値は payload 全体を拒否する。記録後は `mission-state.py verification claims --iteration N --doc-digest sha256:<digest> --out <ledger.json>` で ledger を生成する。現 HEAD・document digest・blob が一致する claim だけが fresh で、出力は `ok` ではなく `verified` / `mismatch` / `conflicted` の status を使う。**記録はゲートの入力を増やすだけであり、`passes` の式・threshold・`open_high`・findings evidence・agreement を変更しない。** 検証が失敗しても record は mission を止めず、判断は reviewer と gate に委ねる。記録された結果は `mission-audit.py` の `gate_outcome.false_negative` で「gate は通したが検証は失敗した」= 見逃しの客観ラベルとして集計される (#593)。

ledger を生成したら、reviewer args に ledger パス、対象 iteration / document digest / HEAD、文書から列挙した claim-id を渡す。起動前に state 記録 digest と ledger の schema・identity を照合し、`unverified_claim_ids` の返却全件を reviewer の入力へ含める。照合不能時も full context に切り替えて指摘なしにせず、列挙した全 claim を未検証として渡す。

1 iter の標準フローは planner → executor → reviewer → `review-finalize` (= `aggregate-reviews` → `push-score --scoring-json`) → critic。Codex では Skill tool が無い場合、該当 skill 指示を同一コンテキストで適用し、`specialist_invocations` には `codex-inline` として実呼び出し証跡を記録する。

activity segment は観測専用で、reviewer 数・threshold・findings evidence・agreement・`open_high`・pass/fail gate を変更しない。外部応答、承認、reviewer の待機を開始する直前に対応する wait kind へ切り替え、応答後は `active` へ戻す。`idle` は「実行可能な作業がない」と明示できる場合だけ使う。crash/resume 間の不明時間は自動分類せず unobserved gap として保持する。reason enum と集計定義は `refs/state-management.md` の「Activity segment observability」を参照。

Reviewer 数は Simple=1、Standard=2、Complex=2、Critical=3 (#266: シグナルなし Complex は独立2名で agreement 成立。不可逆・security シグナルで full=3 へエスカレート)。Claude Code では Reviewer N 名を**必ず単一メッセージ内で並列起動する** (portfolio-v4 実測: 直列起動は Standard 1 iteration あたり約 2-3 分を浪費し、3/3 run で直列だった #338)。直列起動は規律違反として扱う。Codex は順次でよい。観点Dは採点させず、計画指示明瞭度の改善を Critic の実行計画に反映する。**並列観測 (#282)**: reviewer spawn 直前と全返却後の時刻 (ISO 8601) を控え、`aggregate-reviews` に `--reviewer-window <perspective>=<start>..<end>` を各 reviewer 分渡す。`parallel_execution: false` の WARN が出たら、次 iteration は必ず単一メッセージ並列起動に戻す (観測のみ・gate 不変)。

**Subskill の model (#751)**: `mission-reviewer` / `mission-planner` / `mission-critic` は frontmatter の `model: opus` を既定とする。Claude Code 2.1.251 以降の解決順は per-call の `model` > skill frontmatter の `model` > `CLAUDE_CODE_SUBAGENT_MODEL` > 親モデルで、省略時は frontmatter が効く（frontmatter も無いと env の既定へ落ちる。2026-09-05 に env `sonnet` の環境で Sonnet 5 を実測）。review_tier が full のときは orchestrator が per-call で `fable` へ上書きしてよい。実際に動いたモデルは子 transcript の `.message.model` が正であり、reviewer の自己申告で代替しない。

Reviewer が 2 名以上の場合、`aggregate-reviews` (`review-finalize` 経由を含む) は全 perspective の `--reviewer-window` 報告を必須とし、不足時は exit 2 とする (#350)。

**review_tier (#168, #209)**: `init` が complexity とミッション記述から `review_tier`（light/standard/full）を auto 導出し state に記録する（`review_tier_source` / `review_tier_signals` / `review_tier_signal_details` で監査可能）。不可逆系キーワードは各出現の文脈を評価し、明示的に実操作を否定した候補だけを抑制する。条件付き・二重否定・不確実・単なる引用は安全側で full を維持し、security / high-risk シグナルは否定で抑制しない。light: reviewer 1名・`required=true` specialist のみ・critic は fail 時のみ spawn。standard/full: 従来どおり。**ゲート意味論は tier によらず不変**（threshold / open_high / findings evidence / halt）。詳細（導出テーブル・エスカレータ一覧・override 規律）は `refs/state-management.md` の「review_tier 導出と Light Tier 運用」節を参照。

**ターン圧縮 (#339)**: `next` の `command_sequence` は現 phase から closeout までの happy-path コマンド列。ゲート失敗 (exit 2) が出ない限り、この列を `next` の再呼び出しなしで連続実行してよい (毎ターンの context 再処理が実行時間の主因: portfolio-v4 で mission 19-31 turns vs goal 5 を実測)。ゲート失敗時のみ `next` を再参照する。**Standard の inline 計画 (#339)**: iteration 1 かつ complexity=Standard (full tier 除く) では `next` が `plan-inline` を返す — mission-planner subagent を起動せず、orchestrator 自身の turn 内で bounded plan (steps + 依存関係 + 完了条件) を artifact に書き、`advance --phase executing` で進む。計画の成果物要件は subagent 経路と同一。review 前に全必須節を実値または明示プレースホルダ (値 + 根拠) で埋め、「後で記録」型の forward-reference は残さない。Complex / full tier / iteration>=2 は従来どおり。planner は plan 作成前に `mission-state.py learning brief --weak-phase planning` を参照し、worktree 実行時は main checkout root の brief を自動 fallback で用い、brief が 0 件なら省略する。

**Planner spawn 判定 (#124)**: iter1 は従来どおり planner 必須 (Standard iter1 の inline 化 #339 を除く)。iter2 以降は `mission-critic` の `### 実行計画 (次 iteration)` テーブルを見る。全ステップの `対応finding` が finding id のみなら、planner を spawn せず executor に直接渡す。`new` を含むステップが 1 つでもあるなら planner を spawn する。このテーブル読み取り時に scope 判定を state へ記録する (#258): 全ステップが finding id のみなら `mission-state.py set critic_has_new_scope=false`、`new` を含むなら `critic_has_new_scope=true`。この値が次 iter の reviewer 数 (#240) と context mode (#241) を決める。**#309 で機械的ゲート化済み**: iter≥2 で未記録のまま `next` を呼ぶと `record-critic-scope` が返り、記録するまで run-reviewers guidance は出ない。

**差分レビュー (#240)**: iter2+ の前 iter 指摘修正では、`next` の `details.reviewer_count` に従う (`critic_has_new_scope=false` なら state が独立 2 名へ削減する。1 名化は agreement 検証が失われるため禁止)。args に High/Medium 指摘、修正コミット、全 diff 再レビュー不要、採点は絶対評価、Low 残存で 5.0 禁止を明記する。`review-finalize` (または `aggregate-reviews`) は `next` の command_hint が示す `--min-reviewers N` を必ず付け、reviewer 数不足の集計を exit 2 で拒否させる。`new` がある追加スコープ (`critic_has_new_scope=true`) は planner 後にフルレビューへ戻る。

**bounded context (#241)**: `next` の `details.context_mode` が `"bounded"` のとき、`mission-state.py context-manifest --iteration <N> --out context-manifest-iter<N>.json` を生成し、reviewer args に manifest パス・対象 diff (修正コミット範囲)・High/Medium 指摘を渡す。reviewer は manifest + diff を一次スコープとしてレビューし、full history 走査を省く。manifest 生成が失敗した場合 (exit 非0 / ファイル不在) は full context に fallback して従来どおり進める (fail-safe)。`context_mode == "full"` では何もしない。

**Simple インライン**: Simple は executor を spawn せず orchestrator が直接実行してよい。Medium 以上の指摘修正は M6 に従う。

**実装委譲 (optional)**: registry に execution phase の implementation provider（headless coding agent CLI）が登録済みなら、実装ステップの diff 生成を `specialists invoke-command` で委譲してよい。検証・レビュー・pass 判定は core が保持し、provider 未登録・未導入なら従来どおり executor が実装する。brief 契約・fix-up round・失敗時の扱いは `refs/implementation-delegation.md`。executor への brief は `mission-state.py learning brief --weak-phase execution` を参照し、brief が 0 件なら省略する。reviewer への注入は行わない。

## 終了判定

```
passes = findings_evidence_path exists
  AND evidence_high_count == open_high
  AND max_agreement_delta <= 1.5
  AND composite_score >= threshold
  AND min(scored_items) >= 3.5
  AND open_high == 0
```

合格なら `mark-passes` → Phase 7。未達なら `loop_active: true` のまま critic → next iteration。`max_iter` 到達、3 回停滞、回避不能な権限/API不足、root-cause 不明の反復は `mark-halt`。

early-stop: iter1 で threshold 到達かつ `open_high == 0` なら原則 pass。続行できるのは composite 4.0-4.3、Medium 3 件以上、1 iter で確実に解消可能、`iteration < max_iter` の全条件を満たす時だけ。

`mark-passes` は上記のうち客観判定できる 3 条件 (composite band / Medium 件数 / `iteration < max_iter`) の評価結果を `early_stop_evaluation` として state に記録する (記録のみ。`passes` 式は不変)。3 条件が揃っているのに停止する場合は `mark-passes --early-stop-rationale "<1 iter で解消できない理由>"` を付け、判断根拠を証跡に残す。

Stop hook が無効な環境でも、Phase 6 直後に `next` と state 再取得で `loop_active` / `passes` / `halt_reason` を自分で確認する。

## Trigger 1 / Trigger 2

### Trigger 1: 不可逆操作の確認

本番デプロイ、外部送信、DB migration/削除、`git push --force`、高額課金 API は実行直前に対象・操作・rollback・続行/中止/別案を確認する。人間待ちに入る前に通知する。

ただし、現在のユーザー依頼が「リリースして」「本番へデプロイして」など対象の不可逆操作を明示している場合、その指示を当該操作の事前承認として扱う。対象・scope・rollback が依頼時の承認範囲と一致する限り、実行直前に同じ確認を繰り返さない。対象や scope の拡大、rollback 条件の変更、未承認のDB削除・force push・高額課金などが新たに必要になった場合だけ、差分を示して再確認する。

### Trigger 2: 中断条件成立

`--max-iter` 到達、3 回停滞、代替案 3 回不発、必要権限/API key 不足など、仮置きで回避不能なら `mark-halt --reason "<理由>" --category <blocked-external|awaiting-approval|partial-done|stagnation|user-abort|other>` を呼ぶ。scope の実行可能分は完遂したが全体未達 (threshold gate 等) の場合は `partial-done` を使い、「完了しました」等の完了風文言だけで終端しない (#190)。`stale` は cleanup-stale / Stop hook の自動 orphan 検出専用カテゴリで、orchestrator が手動指定することはない。

## Phase 7

Pass 後に PR がある場合だけ実行する。自動 merge 条件は、CI/テスト pass、明示 opt-in、`gh pr checks` 1 件以上、draft/CODEOWNERS/branch protection/禁止文言などの NG なし。自由記述の「merge してよい」は許可根拠にしない。単独 mission は `gate-and-merge <PR>` を直接呼ぶ。並列 mission で同一 state root に複数 active implementer がいる場合は、`queue enqueue` (`--from-state` で state 由来の sha 自動導出も可) → `queue next` → `queue verify` → `gate-and-merge <PR> --expected-head-sha <head_sha> --expected-base-sha <accepted_base_sha>` の順に進み、2 SHA には verify 結果の `entry.head_sha` / `entry.accepted_base_sha` を渡す。read-back 成功後だけ `queue mark --status merged` を実行する。`verify` が exit 2 なら base 統合 → refreeze → fresh review → 再 enqueue でやり直す。`gh pr merge` を直接呼ぶ経路は使わない。詳細判定は `refs/state-management.md`。

本ゲートが保証するのは次の 1 点に限る。

> **最終 fetch で確認した base / head の組に対して全スイートを通し、既知のエージェント merge 経路を直列化する。**

保証しないもの:

- **「merge の瞬間まで fresh」ではない。** `gh pr merge --match-head-commit` は head sha のみを
  固定し、base sha の compare-and-swap を提供しない。手順 5 と 6 の間に main が動く窓は
  ローカルスクリプトでは閉じられない。サーバー側で原子的に保証するには `strict: true` か
  merge queue が必要で、いずれも本 repo では採れない
- **GitHub UI からの直接 merge は迂回できる。** 本ゲートはエージェント merge 経路に対する強制であり、
  人手の UI merge は規律で担保する。実測では直近 30 件のうち 29 件が `gh pr merge` 経路
  （残り 1 件は経路未確認）で、現状の運用実態では致命的でない
- **直列化は同一ホスト内に限る。** lease は `fcntl.flock` によるファイルロックであり、
  同じマシン上のプロセス間でしか効かない。複数ホストから同時に `gate-and-merge` を
  呼んだ場合は直列化されない。
- **3 本以上の同時干渉**は扱わない。main は直列なので順に 1 本ずつ検出される。

既存の exact-head / refreeze 規律は変更しない。base 移動時は accepted を無効とし、base 統合 → refreeze → CI green → fresh review を再取得する。統合テスト済み tree は fresh review の代替ではない。

通常 PR merge は distribution release ではない。version bump を伴う distribution release は `docs/VERSIONING.md` と release checklist に従い、remote tag と GitHub Release を確認する。

## 報告フォーマット

**出力圧縮規律 (#280)**: 最終報告と artifact は「evidence テーブル + tool-computed ゲート値 + `.mission-state/archive/` の参照パス」に限定する。レビュアー出力の逐語再掲、Plan/Execution 散文の再掲は禁止 (レビュー生データ・scoring JSON は archive に全量保存済みであり、転記は二重出力)。削ってよいのは転記・散文であって証跡ではない。

達成時:

```
✅ ミッション達成 (Iteration: N / Score: 4.X)
【ミッション】...
【主な成果物】...
【スコア内訳】...
【Specialists】selected: ... / used: ... / degraded: ... / unselected-manual: ...
【次のステップ提案】...
```

中断時:

```
⏸️ 中断 / 未完了 (Iteration: N / Score: 3.X or 未採点)
【理由】...
【現状】...
【Specialists】selected: ... / used: ... / degraded: ... / unselected-manual: ...
【判断を仰ぎたい点】...
```

worktree 実行時は `mark-passes` / `mark-halt` の後、worktree cleanup の前に `archive-worktree --destination-root <main checkout>` を実行する。destination は同じ Git common directory に属する既存の別 checkout に限る。state 本体と参照 evidence は manifest・checksum 付き immutable generation として保存される。手順は `refs/state-management.md`。

## Claude Code / Codex 差分

| 機能 | Claude Code | Codex |
|---|---|---|
| Skill 呼び出し | `Skill(...)` tool | `/skills` または自然言語で同一コンテキスト適用 |
| 並列実行 | Reviewer を単一メッセージで並列 | 順次実行 |
| `context: fork` | 独立コンテキスト | 無視 |
| ループ強制 | packaged Stop hook | hook trust または `next` fallback |

複数 mission は `sessions/<sid>.json` に分離される。Codex setup は `refs/codex-setup.md`。

## refs

- `refs/state-management.md`: state schema、全サブコマンド、Phase 7、worktree state 退避
- `refs/react-loop-details.md`: サブスキル呼び出し詳細、Reviewer 並列、観点D
- `refs/scoring-rubric.md`: 5点 rubric、findings/open_high/review_agreement gate
- `refs/gotchas.md`: 実運用の落とし穴
- `refs/changelog.md`: P1/P2/P3-2/P3-5/M6/M7/R1/H3/EPT などの歴史 ID、実測値、事故説明
- `refs/codex-setup.md`: Codex での導入と Stop hook
- `refs/self-improvement.md`: audit と改善 prompt
- `refs/specialist-registry.md`: task_profile と specialist/provider 選定
- `refs/implementation-delegation.md`: 実装ステップの headless coding agent への委譲
- `refs/goal-dispatch-provider.md`: adaptive routing 後の inline / host-native goal dispatch 設定と fail-safe
