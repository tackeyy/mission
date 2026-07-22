# mission vs goal-only pilot benchmark

このディレクトリには、`mission` と goal-based execution baseline を
マーケティング利用してよい粒度で比較するための 10 タスク pilot benchmark と
公式 `/goal` smoke を置きます。

この benchmark は意図的に小さくしています。目的は、大きな性能主張を出す前に、
`mission` が意味を持つタスクタイプと、軽量な goal condition で十分なタスクタイプを
見分けることです。

最初に計測した cohort は `tasks.json` と local `goal_only` baseline です。
より複雑なタスクの検証は `tasks.complex.json` と
`complex-validation-plan.ja.md` に分けています。品質差を測る critical task は
`tasks.quality.json` に分けています。

用語を分けます。

- `goal_only` は `run_paired_pilot.py` が使う local lightweight baseline です。
  Claude Code 公式 `/goal` command ではありません。
- `claude_code_goal_command` は Claude Code 公式 built-in `/goal` command を
  `run_claude_goal_vs_mission.py` から実行する arm です。
- `mission` は `/mission` plugin workflow です。

## Research Question

同じ agent model に同じ task objective を与えたとき、`mission` の stateful な
plan / review / scoring / iteration loop は、goal-only baseline と比べて
複数ステップ作業の outcome を改善するか。

## Arms

| Arm | Setup | 検証するもの |
|---|---|---|
| `goal_only` | task を測定可能な goal に変換し、agent が goal 達成と判断するまで通常実行する。 | 軽量な completion-condition workflow。 |
| `claude_code_goal_command` | Claude Code built-in `/goal` command を print mode で実行する。 | local baseline とは別の、公式 Claude Code goal command path。 |
| `mission` | 同じ objective を `/mission` で実行し、state、review、scoring、threshold gate を使う。 | stateful な loop-engineering workflow。 |

両 arm では、同じ model、repository state、branch starting point、tool permission、
time budget、task prompt を使います。

## Metrics

| Metric | Definition |
|---|---|
| `run_status` | `completed`、`failed`、`blocked`。blocked は infrastructure/account state により comparable attempt が成立しなかった状態。 |
| `blocked_reason` | `run_status=blocked` の理由。現在は `api_usage_limit`、`max_budget_usd`、`timeout`。それ以外は null。 |
| `comparable_attempt` | fair な task-quality attempt の前に blocked された場合は false。 |
| `mission_profile` | official runner record の `/mission` prompt profile。`full` は通常 workflow、`light` は cost-controlled one-pass profile、`quality` は evidence map、rejected hypotheses、stop/proceed decision を重視する profile。 |
| `completion` | 必要な artifact または code change が作られ、未解決のまま停止していない。 |
| `validator_pass` | task 固有の validator が pass した。例: test、lint、schema check、file assertion、review checklist。 |
| `human_quality_score` | 下記 rubric に基づく 1-5 の blind reviewer score。 |
| `intervention_count` | 実行開始後に必要だった人間の clarification、correction、restart の回数。 |
| `resume_success` | interruption task で、task state を失わずに resume できたか。 |
| `evidence_completeness` | commands、artifacts、reviewer notes、final state など、「done」と言える証跡が残っているか。 |
| `elapsed_minutes` | agent の最初の action から final answer までの wall-clock runtime。 |
| `token_estimate` | 取得できる場合の token usage。取得できない場合は null。 |

## Automated Scoring (arm-blind)

自動 evaluator は score 決定時に arm label を一切参照しません。両 runner は
同じ純関数 `score_from_signals(validator_pass, marker_score)` を使います:

```
quality_score = 1.0 + 1.0 * validator_fraction + 3.0 * marker_score   # markered task (gradient v2, #247)
quality_score = 1.0 + 3.0 * validator_pass                            # marker なし task (legacy 二値)
```

marker なしの task は `1.0`（fail）または `4.0`（pass）に収束します。
markered task の `validator_fraction` は**両アーム共通の必須見出し**（Evidence / Assumptions）の充足率です（#248）。アーム固有見出し（goal 3 個 / mission 6 個）の欠落は `missing_arm_specific_headings` に記録されますが、validator gate・スコアには使いません — 見出し数の非対称が完走判定の難易度差と「冗長に書くほど有利」の歪みを生んでいたためです。新旧 record は `quality_score_method`（`..._gradient_v2_...`）で機械的に区別できます。

marker の評価は **form-stripped** な artifact 本文に対して行います（F-2）。
`strip_form` は markdown 見出し・ラベルだけの行・水平線・table separator 行を
マッチ前に取り除くため、テンプレセクションを多く出す arm（中身のない
`## Rejected Hypotheses` 見出しなど）は marker credit を得られません —
本文がその内容を実際に扱っている必要があります。これにより、2026-06-27
pilot の evidence score が mission 形式の artifact を優遇していた
structure-credit の循環を除去します。strip 前の score は過去 record との
比較用に `quality_marker_score_raw` として記録します。
`quality_score_method` は
`automated_heuristic_form_stripped_not_blind_human` になります — 自動 score は
screen であり、blind human judgement ではありません。

## Protocol

1. clean checkout または isolated worktree から開始する。
2. `tasks.json` の各 task について、同じ starting commit から `goal_only` arm と
   `mission` arm を実行する。
3. 可能なら run order を counter-balance する。task id ごとに先に実行する arm を
   交互にし、一方だけが operator learning の恩恵を受けないようにする。
4. 片方の arm に、もう片方の transcript を見せない。
5. `result.schema.json` に沿って、run ごとに JSONL record を 1 行保存する。
6. human quality score を付ける前に task validator を実行する。
7. 可能なら arm label を隠して human reviewer が採点する。
8. 個別例を出す場合は anonymized かつ reproducible にし、基本は aggregate results だけを要約する。

次の complex-task cohort では、明示的な task file と unique run id を指定して同じ
protocol を実行します。

```bash
python3 benchmarks/mission-vs-goal/run_paired_pilot.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id YYYY-MM-DD-codex-cli-complex-local \
  --starting-commit <commit> \
  --timeout 1800
```

full 20-run complex executions の前に、`--limit 2` で smoke run を実行します。

Claude Code 公式 `/goal` command と比較する場合は、別 runner を使います。

```bash
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id YYYY-MM-DD-claude-goal-vs-mission-smoke \
  --starting-commit <commit> \
  --limit-tasks 1 \
  --timeout 300 \
  --max-budget-usd 1.5 \
  --mission-max-iter 1
```

2026-06-28 の公式 `/goal` smoke では、complex task 1 件で `/goal` artifact は
作成されました。一方で comparable な `/mission` arm は、artifact 作成前に
Claude Code workspace API usage limit で停止しました。この run は blocked と扱い、
どちらかが優れている証拠にはしません。

API limit 引き上げ後、`2026-06-28-claude-goal-vs-mission-smoke-v3` は
1 comparable task を両 arm で完了しました。その後の 10 task full attempt
`2026-06-28-claude-goal-vs-mission-complex-v1` は、全 record が workspace API
usage limit で blocked されました。smoke は 1 task の comparable result として扱い、
full attempt は blocked と扱います。公式 runner は `run_status`、`blocked_reason`、
`comparable_attempt` を記録するため、API/account stop を task-quality failure と
誤読しないようになっています。

cost-controlled incremental run では、すでに測定済みの task を再実行しないよう
`--task-ids` を使い、blocked record が出たら止めるために `--stop-on-blocked` を使います。

```bash
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id YYYY-MM-DD-claude-goal-vs-mission-incremental \
  --starting-commit <commit> \
  --task-ids complex-failing-test-triage,complex-review-thread-resolution \
  --stop-on-blocked \
  --timeout 1200 \
  --max-budget-usd 3.0 \
  --mission-max-iter 2
```

2026-06-28 の incremental run では、USD 3.00 per-invocation cap 下で、公式 `/goal`
は selected tasks 2 件を完了しました。一方 `/mission` は 2 records とも configured
max-budget cap に到達しました。これは operational cost/runtime result として扱い、
`mission` の completed quality comparison としては扱いません。

低コストの `/mission` 比較では、`--mission-profile light` と single mission iteration を使います。

```bash
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.complex.json \
  --run-id YYYY-MM-DD-claude-goal-vs-mission-light \
  --starting-commit <commit> \
  --task-ids complex-failing-test-triage \
  --stop-on-blocked \
  --timeout 1200 \
  --max-budget-usd 5.0 \
  --mission-max-iter 1 \
  --mission-profile light
```

2026-06-28 の light-profile run では、未測定 task 1 件を両 arm で完了しました。
公式 `/goal` は 9.56 分、USD 3.00670750 で完了し、`/mission` light は 5.27 分、
USD 2.00569500 で完了しました。これは promising one-task result として扱い、
広い cost/runtime claim に使う前に fresh task 3-5 件で再実行します。

quality-first comparison では、fresh な `tasks.quality.json` cohort と
`--mission-profile quality` を使います。

```bash
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.quality.json \
  --run-id YYYY-MM-DD-claude-goal-vs-mission-quality \
  --starting-commit <commit> \
  --task-ids quality-critical-release-governance \
  --stop-on-blocked \
  --timeout 1800 \
  --max-budget-usd 6.0 \
  --mission-max-iter 2 \
  --mission-profile quality
```

2026-06-28 の quality attempt
`2026-06-28-claude-goal-vs-mission-quality-v1` は paired comparison を完了していません。
公式 `/goal` が success 前に `api_usage_limit` に到達し、`--stop-on-blocked` で
API budget を保全したため `/mission` は実行していません。これは blocked と扱い、
どちらかの品質 evidence にはしません。

## Tail Cohort (tail-first-failure)

これまで完了した paired run はすべて天井に張り付いています。両 arm が
validator を 100% 通過するため、review loop が何を足すかをどの cohort でも
測れませんでした。`tasks.tail.json`（cohort: `tail-first-failure`）は marker を
構造ではなく内容の recall にすることで、この天井を取り除きます。

- 各 task は、cross-file の矛盾・複数原因の重なり・数値誤り・記載漏れを
  仕込んだ fixture ファイル（commit 済み）と、正しいのに疑わしく見える
  candidate（checklist 的な指摘を罰する）を同梱します。
- `quality_markers` は、その問題を実際に見つけない限り artifact に現れない
  固有トークン（誤った値・識別子）です。`markers_hidden: true` により
  両 arm の prompt には列挙されません。
- `forbidden_markers` は false positive を減点します。正しい candidate を
  finding と明示的に主張すると net marker score が下がります。penalty は
  substring ベースで意図的に under-sensitive です — 言い換えた false claim は
  すり抜けられますが、planted marker に一致せずに score を上げる方法は
  ありません。
- `hidden_paths` は answer key（`tasks.tail.json` 自身）を列挙し、runner が
  どちらの arm の実行前にも clone 済み worktree から削除します。
  `prompt_rules` は benchmark metadata 全体を両 arm の out of bounds にします。

```bash
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.tail.json \
  --run-id YYYY-MM-DD-claude-goal-vs-mission-tail \
  --starting-commit <commit> \
  --model-id <model-id> \
  --stop-on-blocked \
  --timeout 1800 \
  --max-budget-usd 6.0 \
  --limit-tasks 5
```

tail cohort の run は 2026-07-07 に 1 件完了しました
（`2026-07-07-claude-goal-vs-mission-tail-v1`、5 tasks、両 arm の automated
content-recall metrics は同点、`/mission` の wall-clock は `/goal` 比で約 5.8 倍・
cost は約 7.4 倍；詳細は `report.md` の Tail Cohort Run セクションを参照）。
N=5、1 model、closed-world fixture であり、広範な主張は導けません。
`first_pass_failure_design` は引き続き設計仮説です。
この run では設計上の first-pass failure は再現しませんでした。

## Openworld Cohort (openworld-discovery)

tail cohort は closed-world fixture の planted defect recall をテストしますが、
`tasks.openworld.json`（cohort: `openworld-discovery`）は open-world discovery を
テストします。solver は事前に列挙された finding list なしで、divergence・
contradiction・root cause を独立に発見する必要があります。

3 タスク設計:

1. **constant-hunt** — canonical default に対するサービス横断 timeout 設定監査。
   事前チェックリストなしで divergence を独立に発見する必要がある。
2. **contradiction-chain** — 複数ドキュメント間の claim 整合性チェック。real
   contradiction と、一見矛盾するが注意深く読むと整合する decoy が共存する。
3. **incremental-reveal** — 時系列 incident log。最初の仮説（直近 deploy）が後の
   evidence で否定される。solver は evidence chain を最後まで追って actual root
   cause に到達する必要がある。

scoring は tail cohort と同じ `quality_markers` / `forbidden_markers` /
`hidden_paths` infrastructure を使います。

```bash
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py \
  --tasks-file benchmarks/mission-vs-goal/tasks.openworld.json \
  --run-id YYYY-MM-DD-claude-goal-vs-mission-openworld \
  --starting-commit <commit> \
  --model-id <model-id> \
  --stop-on-blocked \
  --timeout 1800 \
  --max-budget-usd 6.0 \
  --limit-tasks 3
```

## Human Quality Rubric

| Score | Meaning |
|---:|---|
| 5 | prompt、validator、evidence requirement を完全に満たし、実質的な cleanup が不要。 |
| 4 | prompt と validator を満たし、presentation または completeness に軽微な gap だけが残る。 |
| 3 | prompt を部分的に満たすが、重要な requirement を落としているか、evidence が弱い。 |
| 2 | 一部有用な作業はあるが、validator または core acceptance criteria が fail している。 |
| 1 | usable result がない、または未解決状態で停止している。 |

## Marketing Guardrails

raw evidence がそろった後に使ってよい表現:

- 「10 タスクの internal pilot では、review、resume、evidence tracking が必要な
  複数ステップ作業で `mission` が completion quality を改善した」
- 「`mission` は、主なリスクが stopping too early である作業で最も有用だった」
- 「小さく単一ステップの task では、goal-only execution でも十分なケースがあった」

この pilot からは言えない表現:

- general model intelligence に関する主張。
- `mission` が `/goal` より普遍的に優れているという主張。
- 2026-06-28 の smoke から、`mission` が Claude Code 公式 `/goal` より良い /
  悪いという主張。最初の smoke は `/mission` arm が API limit で blocked、
  rerun smoke は 1 comparable task のみ、full rerun は全 record が API limit で
  blocked、incremental rerun は `/mission` records が max-budget blocked、
  light-profile rerun は 1 comparable task のみ、quality-profile attempt は
  `/mission` arm 実行前に API-limit blocked のため。
- denominator、task mix、scoring method を出さない percent improvement。
- 10 個すべての paired task run が完了していない状態での性能主張。

## Files

| Path | Purpose |
|---|---|
| `tasks.json` | 計測済みの固定 10 タスク baseline pilot set。 |
| `tasks.complex.json` | 公式 smoke/full attempt に使う 10-task complex cohort。full comparable run はまだ完了していない。 |
| `tasks.quality.json` | evidence-depth と stop/proceed decision を測る fresh quality-critical cohort。 |
| `tasks.tail.json` | planted-defect fixture・decoy penalty（`forbidden_markers`）・answer-key sanitization（`hidden_paths`）を持つ tail cohort。2026-07-07 に最初の run が完了（詳細は `report.md` 参照）。 |
| `fixtures/tail/` | tail cohort 用の commit 済み fixture ドキュメント。 |
| `tasks.openworld.json` | 独立した finding 識別をテストする 3 タスクの open-world discovery cohort。 |
| `fixtures/openworld/` | openworld cohort 用の commit 済み fixture ドキュメント（constant-hunt、contradiction-chain、incremental-reveal）。 |
| `result.schema.json` | result record 1 件分の JSON Schema。 |
| `report.md` | 英語の current measured status と package-validation results。 |
| `report.ja.md` | 日本語の current measured status と package-validation results。 |
| `run_claude_goal_vs_mission.py` | Claude Code 公式 `/goal` と `/mission` の smoke runner。 |
| `official-goal-rerun-runbook.md` | 公式 `/goal` 比較の英語 rerun checklist。 |
| `official-goal-rerun-runbook.ja.md` | 公式 `/goal` 比較の日本語 rerun checklist。 |
| `report-template.md` | 英語の publishable report skeleton と claim guardrails。 |
| `report-template.ja.md` | 日本語の publishable report skeleton と claim guardrails。 |
| `complex-validation-plan.md` | 英語の complex task validation plan。 |
| `complex-validation-plan.ja.md` | 日本語の complex task validation plan。 |
| `README.md` | 英語版の benchmark protocol。 |
