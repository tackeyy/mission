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
3. 新規開始時は、read-only の repository 確認を除く task setup（fetch / pull / switch / worktree 作成）・実装より先に `init` で active state を作る。`init` は `permission-preflight --json` 相当の state / assumptions 実書き込み検査を内蔵する。exit 2 なら実作業へ進まず、`blocked-external` の halt / stdout 証跡をそのまま報告し、権限承認を質問しない。Codex は続けて `codex-preflight --json --strict` を実行し、exit 0 を確認するまで setup を進めない。各 phase 境界は `next` で Stop hook なし環境を補完する。
4. state 更新は `mission-state.py` のみ。`sessions/<sid>.json` 直書き、inline `jq`、手計算の pass 判定は禁止。機械検証可能な action (`push-score` / `mark-passes` / `gh pr view` / `git push`) は直後に state 再取得または外部再照合し、捏造・転記ミスを潰す。
5. Phase 5 は reviewer の `mission-review/1` JSON を `review-finalize` 1 コマンドで集計・記録する (#283: 内部は `aggregate-reviews` → `push-score --scoring-json` と同一 validator。分割実行も後方互換で可)。標準フローで `mission-scorer` を spawn しない。
6. 完了報告前に `closeout` 1 コマンド (#283: `mark-passes` → `next` を同順で実行。gate 未達は exit 2 + guidance) が exit 0 で返ったことを確認する。`next_action=report-complete`（`passes=true`）または `report-blocker`（`halt_reason` あり）以外では final を返さない。`findings_evidence_path` / `open_high` / `max_agreement_delta <= 1.5` / `threshold` / min item gate が未達なら継続。
7. `halt_reason` が空でなければ完了報告語彙は禁止し、先頭を `⏸️ 中断 / 未完了` にする。`mark-passes --force --approved-by-user` はユーザーが明示的に override を指示した場合のみ (#185: `--approved-by-user` は自律実行禁止のフラグであり、orchestrator が自己判断で付けてはならない)。
8. M6: Medium 以上の指摘を orchestrator がインライン修正したら、自己検証だけで合格にしない。差分 Reviewer 1 名の再確認を経てから scoring / pass 判定へ進む。
9. 質問は溜めて仮置きする。即時質問は Trigger 1 の不可逆操作と、Trigger 2 の中断条件だけ。
10. PR がある場合は pass 後に Phase 7 を実行する。自動 merge は明示 opt-in、CI/テスト pass、`gh pr checks` 1 件以上、禁止ルールなしの全条件を満たす時だけ。
11. `init` 後は `activity start --kind active --reason planning` を開始し、実作業・外部応答・承認・reviewer・実行可能作業なしの境界で明示的に切り替える。phase 境界では `set phase=` + `activity start` の 2 コマンドではなく atomic な `advance --phase <phase> --activity <kind>:<reason>` を優先する (#237: phase だけ進んで activity が空の state を作らない)。原因不明の時間を推測分類しない。終端 phase は open segment を自動で閉じる。
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
mission-state.py activity start --kind active --reason planning
mission-state.py activity start --kind external-wait --reason external-response
mission-state.py activity start --kind approval-wait --reason user-approval
mission-state.py activity start --kind reviewer-wait --reason review-response
mission-state.py activity start --kind idle --reason no-runnable-work
mission-state.py activity end
mission-state.py review-finalize --iteration N --input a.json --input b.json --min-reviewers N --reviewer-window "A=<start>..<end>"
mission-state.py aggregate-reviews --iteration N --input a.json --input b.json --out /tmp/mission-scorer-N.json --json
mission-state.py push-score --iteration N --scoring-json /tmp/mission-scorer-N.json
mission-state.py closeout
mission-state.py mark-passes
mission-state.py mark-halt --reason "<reason>"
mission-state.py reactivate --approved-by-user --expected-category awaiting-approval --reason "<user-approved reason>"
```

Artifact-required mission は `artifact init --required-for-pass` → `artifact append` → `artifact render --redaction-status reviewed` を使う。specialist は `specialists recommend --record-state`、完了前は `specialists accounting --json` と `specialists summary --json` で未処理候補を確認する。詳細は `refs/state-management.md`。

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

Checker / 監査等の従属役割で起動する場合は `init --role <checker|planning|analyze|release>` を指定する (#311)。証拠提出で終わる正規出口は `mark-halt --category evidence-submitted` を使い、pass-rate 統計を汚さない (implementer 限定指標が別計上される)。

init 後 (route されなかった場合)、対象ファイル候補が見えた時点で `specialists recommend --task "<mission>" --files "<project-relative files>" --record-state --json` を実行する。ユーザーが skill を名指しした場合は `--user-specified` を付ける。Issue 連携 PR は本文に `Closes #N` を入れる。

## Phase 2-6

1 iter の標準フローは planner → executor → reviewer → `review-finalize` (= `aggregate-reviews` → `push-score --scoring-json`) → critic。Codex では Skill tool が無い場合、該当 skill 指示を同一コンテキストで適用し、`specialist_invocations` には `codex-inline` として実呼び出し証跡を記録する。

activity segment は観測専用で、reviewer 数・threshold・findings evidence・agreement・`open_high`・pass/fail gate を変更しない。外部応答、承認、reviewer の待機を開始する直前に対応する wait kind へ切り替え、応答後は `active` へ戻す。`idle` は「実行可能な作業がない」と明示できる場合だけ使う。crash/resume 間の不明時間は自動分類せず unobserved gap として保持する。reason enum と集計定義は `refs/state-management.md` の「Activity segment observability」を参照。

Reviewer 数は Simple=1、Standard=2、Complex=2、Critical=3 (#266: シグナルなし Complex は独立2名で agreement 成立。不可逆・security シグナルで full=3 へエスカレート)。Claude Code では Reviewer N 名を**必ず単一メッセージ内で並列起動する** (portfolio-v4 実測: 直列起動は Standard 1 iteration あたり約 2-3 分を浪費し、3/3 run で直列だった #338)。直列起動は規律違反として扱う。Codex は順次でよい。観点Dは採点させず、計画指示明瞭度の改善を Critic の実行計画に反映する。**並列観測 (#282)**: reviewer spawn 直前と全返却後の時刻 (ISO 8601) を控え、`aggregate-reviews` に `--reviewer-window <perspective>=<start>..<end>` を各 reviewer 分渡す。`parallel_execution: false` の WARN が出たら、次 iteration は必ず単一メッセージ並列起動に戻す (観測のみ・gate 不変)。

**review_tier (#168, #209)**: `init` が complexity とミッション記述から `review_tier`（light/standard/full）を auto 導出し state に記録する（`review_tier_source` / `review_tier_signals` / `review_tier_signal_details` で監査可能）。不可逆系キーワードは各出現の文脈を評価し、明示的に実操作を否定した候補だけを抑制する。条件付き・二重否定・不確実・単なる引用は安全側で full を維持し、security / high-risk シグナルは否定で抑制しない。light: reviewer 1名・`required=true` specialist のみ・critic は fail 時のみ spawn。standard/full: 従来どおり。**ゲート意味論は tier によらず不変**（threshold / open_high / findings evidence / halt）。詳細（導出テーブル・エスカレータ一覧・override 規律）は `refs/state-management.md` の「review_tier 導出と Light Tier 運用」節を参照。

**ターン圧縮 (#339)**: `next` の `command_sequence` は現 phase から closeout までの happy-path コマンド列。ゲート失敗 (exit 2) が出ない限り、この列を `next` の再呼び出しなしで連続実行してよい (毎ターンの context 再処理が実行時間の主因: portfolio-v4 で mission 19-31 turns vs goal 5 を実測)。ゲート失敗時のみ `next` を再参照する。**Standard の inline 計画 (#339)**: iteration 1 かつ complexity=Standard (full tier 除く) では `next` が `plan-inline` を返す — mission-planner subagent を起動せず、orchestrator 自身の turn 内で bounded plan (steps + 依存関係 + 完了条件) を artifact に書き、`advance --phase executing` で進む。計画の成果物要件は subagent 経路と同一。Complex / full tier / iteration>=2 は従来どおり。

**Planner spawn 判定 (#124)**: iter1 は従来どおり planner 必須 (Standard iter1 の inline 化 #339 を除く)。iter2 以降は `mission-critic` の `### 実行計画 (次 iteration)` テーブルを見る。全ステップの `対応finding` が finding id のみなら、planner を spawn せず executor に直接渡す。`new` を含むステップが 1 つでもあるなら planner を spawn する。このテーブル読み取り時に scope 判定を state へ記録する (#258): 全ステップが finding id のみなら `mission-state.py set critic_has_new_scope=false`、`new` を含むなら `critic_has_new_scope=true`。この値が次 iter の reviewer 数 (#240) と context mode (#241) を決める。**#309 で機械的ゲート化済み**: iter≥2 で未記録のまま `next` を呼ぶと `record-critic-scope` が返り、記録するまで run-reviewers guidance は出ない。

**差分レビュー (#240)**: iter2+ の前 iter 指摘修正では、`next` の `details.reviewer_count` に従う (`critic_has_new_scope=false` なら state が独立 2 名へ削減する。1 名化は agreement 検証が失われるため禁止)。args に High/Medium 指摘、修正コミット、全 diff 再レビュー不要、採点は絶対評価、Low 残存で 5.0 禁止を明記する。`review-finalize` (または `aggregate-reviews`) は `next` の command_hint が示す `--min-reviewers N` を必ず付け、reviewer 数不足の集計を exit 2 で拒否させる。`new` がある追加スコープ (`critic_has_new_scope=true`) は planner 後にフルレビューへ戻る。

**bounded context (#241)**: `next` の `details.context_mode` が `"bounded"` のとき、`mission-state.py context-manifest --iteration <N> --out .mission-state/context-manifest-iter<N>.json` を生成し、reviewer args に manifest パス・対象 diff (修正コミット範囲)・High/Medium 指摘を渡す。reviewer は manifest + diff を一次スコープとしてレビューし、full history 走査を省く。manifest 生成が失敗した場合 (exit 非0 / ファイル不在) は full context に fallback して従来どおり進める (fail-safe)。`context_mode == "full"` では何もしない。

**Simple インライン**: Simple は executor を spawn せず orchestrator が直接実行してよい。Medium 以上の指摘修正は M6 に従う。

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

Stop hook が無効な環境でも、Phase 6 直後に `next` と state 再取得で `loop_active` / `passes` / `halt_reason` を自分で確認する。

## Trigger 1 / Trigger 2

### Trigger 1: 不可逆操作の確認

本番デプロイ、外部送信、DB migration/削除、`git push --force`、高額課金 API は実行直前に対象・操作・rollback・続行/中止/別案を確認する。人間待ちに入る前に通知する。

ただし、現在のユーザー依頼が「リリースして」「本番へデプロイして」など対象の不可逆操作を明示している場合、その指示を当該操作の事前承認として扱う。対象・scope・rollback が依頼時の承認範囲と一致する限り、実行直前に同じ確認を繰り返さない。対象や scope の拡大、rollback 条件の変更、未承認のDB削除・force push・高額課金などが新たに必要になった場合だけ、差分を示して再確認する。

### Trigger 2: 中断条件成立

`--max-iter` 到達、3 回停滞、代替案 3 回不発、必要権限/API key 不足など、仮置きで回避不能なら `mark-halt --reason "<理由>" --category <blocked-external|awaiting-approval|partial-done|stagnation|user-abort|other>` を呼ぶ。scope の実行可能分は完遂したが全体未達 (threshold gate 等) の場合は `partial-done` を使い、「完了しました」等の完了風文言だけで終端しない (#190)。`stale` は cleanup-stale / Stop hook の自動 orphan 検出専用カテゴリで、orchestrator が手動指定することはない。

## Phase 7

Pass 後に PR がある場合だけ実行する。自動 merge 条件は、CI/テスト pass、明示 opt-in、`gh pr checks` 1 件以上、draft/CODEOWNERS/branch protection/禁止文言などの NG なし。自由記述の「merge してよい」は許可根拠にしない。詳細判定は `refs/state-management.md`。

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
- `refs/goal-dispatch-provider.md`: adaptive routing 後の inline / host-native goal dispatch 設定と fail-safe
