---
name: mission
description: ミッション達成までReActループで自律的に稼働。計画→実行→レビュー→スコア4.0達成まで自己修正。曖昧な要件は仮置きで進み、不可逆操作のみ事前確認する。複数ステップの作業を品質ゲート付きで完遂させたい時や「達成するまでやって」系の依頼で使用。
user-invocable: true
argument-hint: <ミッション記述> [--max-iter N] [--skip-preflight] [--threshold X]
---

# /mission — 自律ミッション達成オーケストレーター

あなたは Mission Partner。state gate と `mission-state.py next` / `resume` を進行 oracle とし、`passes: true` または `halt_reason` まで実行を続ける。

## Compact Instructions

1. `.mission-state/sessions/<sid>.json` または `.mission-state/state.json` の `loop_active: true` 中は実行中。完了前に必ず `passes` / `halt_reason` / `score_history` を再取得する。
2. compaction 後の最初の操作は `mission-state.py resume`。返る `next_action` / `command_hint` に従い、state の `assumptions_path` を読む。固定 `.mission-state/assumptions.md` 決め打ちは禁止。
3. 新規開始時は `init` で active state を作り、Codex では `codex-preflight --json` と各 phase 境界の `next` で Stop hook なし環境を補完する。
4. state 更新は `mission-state.py` のみ。`sessions/<sid>.json` 直書き、inline `jq`、手計算の pass 判定は禁止。機械検証可能な action (`push-score` / `mark-passes` / `gh pr view` / `git push`) は直後に state 再取得または外部再照合し、捏造・転記ミスを潰す。
5. Phase 5 は reviewer の `mission-review/1` JSON を `aggregate-reviews` で集計し、直後に `push-score --scoring-json` へ渡す。標準フローで `mission-scorer` を spawn しない。
6. 完了報告前に `mark-passes` が exit 0 で返ったことを確認する。`findings_evidence_path` / `open_high` / `max_agreement_delta <= 1.5` / `threshold` / min item gate が未達なら継続。
7. `halt_reason` が空でなければ完了報告語彙は禁止し、先頭を `⏸️ 中断 / 未完了` にする。`mark-passes --force --approved-by-user` はユーザーが明示的に override を指示した場合のみ (#185: `--approved-by-user` は自律実行禁止のフラグであり、orchestrator が自己判断で付けてはならない)。
8. M6: Medium 以上の指摘を orchestrator がインライン修正したら、自己検証だけで合格にしない。差分 Reviewer 1 名の再確認を経てから scoring / pass 判定へ進む。
9. 質問は溜めて仮置きする。即時質問は Trigger 1 の不可逆操作と、Trigger 2 の中断条件だけ。
10. PR がある場合は pass 後に Phase 7 を実行する。自動 merge は明示 opt-in、CI/テスト pass、`gh pr checks` 1 件以上、禁止ルールなしの全条件を満たす時だけ。

## state.json 操作

リポジトリ root では `scripts/mission-state.py`、配布 skill では `${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py` を使う。

```bash
mission-state.py init "<mission>" --complexity Simple|Standard|Complex|Critical --issue-ref <ref> --files <csv>
mission-state.py resume
mission-state.py next
mission-state.py aggregate-reviews --iteration N --input a.json --input b.json --out /tmp/mission-scorer-N.json --json
mission-state.py push-score --iteration N --scoring-json /tmp/mission-scorer-N.json
mission-state.py mark-passes
mission-state.py mark-halt --reason "<reason>"
```

Artifact-required mission は `artifact init --required-for-pass` → `artifact append` → `artifact render --redaction-status reviewed` を使う。specialist は `specialists recommend --record-state`、完了前は `specialists accounting --json` と `specialists summary --json` で未処理候補を確認する。詳細は `refs/state-management.md`。

## 引数

`/mission <ミッション記述> [--max-iter N] [--skip-preflight] [--threshold X]`

| フラグ | 意味 | デフォルト |
|---|---|---|
| `--max-iter N` | 最大反復回数。`0` は上限なしだが 3 回停滞で halt | `3` |
| `--skip-preflight` | Phase 0 を短縮 | off |
| `--threshold X` | pass threshold | `4.0` |

## 全体フロー

```
Phase 0: 仮置きと質問 2 条件の確認
Phase 1: Issue 特定、複雑度、task_profile、specialist recommend
Phase 2: iter1 planner。iter2+ は Planner spawn 判定
Phase 3: executor 実行
Phase 4: reviewer N 名。iter2+ は差分レビュー
Phase 5: aggregate-reviews -> push-score --scoring-json
Phase 6: next / mark-passes / mark-halt / critic
Phase 7: pass 後の PR merge 判定
```

## Phase 0-1

不明点は質問せず、state の `assumptions_path` に仮置き・観測点・判定根拠を書く。例外は Trigger 1 の不可逆操作と `--require-confirm` 相当の明示指示だけ。

Phase 1 ではミッションを構造化し、触る/触らない範囲、完了条件、複雑度を決める。複雑度は Simple=単一ファイル/1ステップ、Standard=3-5ステップ、Complex=設計判断/横断、Critical=本番/セキュリティ/非可逆。過大見積もりは reviewer コストを増やすため、Simple でない判定根拠を assumptions.md に残す。

init 後、対象ファイル候補が見えた時点で `specialists recommend --task "<mission>" --files "<project-relative files>" --record-state --json` を実行する。ユーザーが skill を名指しした場合は `--user-specified` を付ける。Issue 連携 PR は本文に `Closes #N` を入れる。

## Phase 2-6

1 iter の標準フローは planner → executor → reviewer → `aggregate-reviews` → `push-score --scoring-json` → critic。Codex では Skill tool が無い場合、該当 skill 指示を同一コンテキストで適用し、`specialist_invocations` には `codex-inline` として実呼び出し証跡を記録する。

Reviewer 数は Simple=1、Standard=2、Complex/Critical=3。Claude Code では Reviewer N 名を単一メッセージ内で並列起動する。Codex は順次でよい。観点Dは採点させず、計画指示明瞭度の改善を Critic の実行計画に反映する。

**review_tier (#168)**: `init` が complexity とミッション記述から `review_tier`（light/standard/full）を auto 導出し state に記録する（`review_tier_source` / `review_tier_signals` で監査可能）。light: reviewer 1名・`required=true` specialist のみ・critic は fail 時のみ spawn。standard/full: 従来どおり。**ゲート意味論は tier によらず不変**（threshold / open_high / findings evidence / halt）。詳細（導出テーブル・エスカレータ一覧・override 規律）は `refs/state-management.md` の「review_tier 導出と Light Tier 運用」節を参照。

**Planner spawn 判定 (#124)**: iter1 は従来どおり planner 必須。iter2 以降は `mission-critic` の `### 実行計画 (次 iteration)` テーブルを見る。全ステップの `対応finding` が finding id のみなら、planner を spawn せず executor に直接渡す。`new` を含むステップが 1 つでもあるなら planner を spawn する。

**差分レビュー**: iter2+ の前 iter 指摘修正では Reviewer を検証担当 1 名に絞る。args に High/Medium 指摘、修正コミット、全 diff 再レビュー不要、採点は絶対評価、Low 残存で 5.0 禁止を明記する。`new` がある追加スコープだけ planner 後にフルレビューへ戻す。

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

## 実行例

```
/mission リファクタリングして
→ init、仮置き、specialists recommend、next に従って進行
→ aggregate-reviews / push-score --scoring-json / mark-passes
```
