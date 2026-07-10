# mission vs goal-only pilot report

Status: 2026-06-27 に controlled local Codex CLI pilot として計測済み。
追加で、2026-06-28 JST に Claude Code 公式 `/goal` の試行を実施し、
別セクションに分けて記録しています。Tail cohort run
（`2026-07-07-claude-goal-vs-mission-tail-v1`、planted-defect 5 tasks、両 arm）は
2026-07-07 JST に完了し、以下の Tail Cohort セクションに記録されています。

これは general model benchmark ではなく、blind human evaluation でもありません。
下記の quality / evidence score は local runner による automated heuristic score です。
raw records と artifacts はこのディレクトリに保存しており、後から監査できます。

## Executive Summary

starting commit `0148f16` から、20 件すべての paired benchmark execution を実行しました。
内訳は `goal_only` 10 件、`mission` 10 件です。

両 arm とも、すべての task を完了し、すべての local validator に pass しました。
この controlled pilot では、`mission` arm は automated quality / evidence score が高く、
一方で wall-clock runtime は長くなりました。

現時点で安全に言えることは狭く、以下です。

> この controlled local 10-task pilot では、completion rate と validator pass rate は
> 両 arm とも 100% だったため、`mission` の改善は出ていません。一方で、
> automated evidence / completion-quality score は `mission` が高く、runtime は長くなりました。

## Measurement Scope

| Item | Value | Evidence |
|---|---:|---|
| Measurement date | 2026-06-27 | current local Codex CLI run。 |
| Run id | `2026-06-27-codex-cli-local` | `results/2026-06-27-codex-cli-local.jsonl`。 |
| Starting commit | `0148f16` | pilot 前の `git ls-remote origin refs/heads/main`。 |
| Fixed pilot tasks defined | 10 / 10 | `benchmarks/mission-vs-goal/tasks.json`。 |
| Benchmark arms defined | 2 / 2 | `goal_only`, `mission`。 |
| Expected paired runs | 20 | 10 tasks x 2 arms。 |
| Paired benchmark runs completed | 20 / 20 | raw JSONL に 20 records。 |
| Goal-only runs completed | 10 / 10 | raw JSONL records。 |
| Mission runs completed | 10 / 10 | raw JSONL records。 |
| Result artifacts captured | 100 files | `artifacts/2026-06-27-codex-cli-local/`。 |
| Quality score method | automated heuristic | `quality_score_method=automated_heuristic_not_blind_human`。 |
| Comparative performance claim readiness | limited | この controlled local task set と scoring method に限定。 |

## Results

| Metric | goal_only | mission | Delta |
|---|---:|---:|---:|
| Completion rate | 10 / 10 | 10 / 10 | 0 |
| Validator pass rate | 10 / 10 | 10 / 10 | 0 |
| Average quality score | 4.00 / 5 | 4.50 / 5 | +0.50 |
| Average intervention count | 0.00 | 0.00 | 0 |
| Resume success rate | 1 / 1 | 1 / 1 | 0 |
| Average evidence completeness | 3.80 / 5 | 4.70 / 5 | +0.90 |
| Average elapsed minutes | 1.28 | 2.99 | +1.71 |

## Claude Code 公式 `/goal` smoke

Status: 2026-06-28 JST に controlled Claude Code CLI print-mode smoke として試行。
比較対象は、前回の local `goal_only` baseline ではなく、Claude Code 公式の
built-in `/goal` command です。Claude Code の commands / skills の公式 docs は
<https://code.claude.com/docs/en/commands> と
<https://code.claude.com/docs/en/skills> にあります。

この smoke は evidence を残しましたが、**完了した性能比較ではありません**。
`/mission` arm は task artifact を書く前に、Claude Code の workspace API usage
limit error 400 で停止しました。raw error には、2026-07-01 00:00 UTC に access
が戻ると記録されています。
原因は Claude Code workspace API usage limit であり、task-quality failure ではありません。

| Item | Value | Evidence |
|---|---:|---|
| Measurement date | 2026-06-28 JST | raw records は 2026-06-27T16:34:57Z に完了。 |
| Run id | `2026-06-28-claude-goal-vs-mission-smoke-v2` | `results/2026-06-28-claude-goal-vs-mission-smoke-v2.jsonl`。 |
| Starting commit | `38cc7907e5e35fcd9fa23022a1fcf03f756df99b` | runner argument。 |
| Task file | `tasks.complex.json` | smoke task は `complex-cross-file-feature` 1 件。 |
| Arms | 2 | `claude_code_goal_command`, `mission`。 |
| Records completed | 2 / 2 | summary JSON に 2 records。 |
| `/goal` artifact completion | 1 / 1 | artifact が存在し、heuristic validator pass。 |
| `/mission` artifact completion | 0 / 1 | artifact なし。API usage limit で停止。 |
| `/goal` cost | USD 0.93959825 | raw Claude result JSON。 |
| `/mission` cost before stop | USD 1.05234325 | raw Claude result JSON。 |
| Quality score method | automated heuristic | blind human review ではない。 |
| Marketing comparison readiness | blocked | `/mission` arm が comparable attempt を完了していない。 |

Smoke result:

| Metric | claude_code_goal_command | mission | Interpretation |
|---|---:|---:|---|
| Completion rate | 1 / 1 | 0 / 1 | `/mission` は workspace API limit による停止であり、task-quality failure と検証されたわけではない。 |
| Validator pass rate | 1 / 1 | 0 / 1 | `/mission` は artifact がないため validator を満たせない。 |
| Average quality score | 4.00 / 5 | 1.00 / 5 | artifact presence に基づく automated placeholder score。能力差の結論ではない。 |
| Average evidence completeness | 4.00 / 5 | 1.00 / 5 | 上と同じ制約。 |
| Average elapsed minutes | 1.97 | 2.06 | `/mission` は API-limit stop までの時間。 |

安全な解釈:

> Claude Code 公式 `/goal` smoke harness は、complex task 1 件で auditable artifact
> を作成できた。一方、comparable な `/mission` arm は Claude Code workspace API
> limit により artifact completion 前に blocked された。そのため、この smoke から
> どちらが優れているという marketing claim は出せない。

危険な解釈:

> `mission` は公式 `/goal` に負けた。

これは unsupported です。`/mission` の failed record は infrastructure/API-limit stop
であり、完了した task-quality measurement ではありません。

この smoke 後に完了した次回準備:

- `run_claude_goal_vs_mission.py` は future runs で `run_status`、
  `blocked_reason`、`failure_kind`、`comparable_attempt` を記録する。
- `official-goal-rerun-runbook.ja.md` は 2026-07-01 09:00 JST 以降の smoke gate と
  full paired pilot 手順を定義する。
- future reports では、capability claim の前に `completed`、`failed`、`blocked`
  records を分離する。

## API limit 引き上げ後の Claude Code 公式 `/goal` 再実行

Status: Claude API limit 引き上げ後の 2026-06-28 JST に実行。
1 task の comparable smoke は完了しました。その後に 10 task full attempt を実行しましたが、
Claude Code workspace API usage limit に到達したため、full attempt は完了した性能比較ではありません。

### Smoke gate

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-06-28-claude-goal-vs-mission-smoke-v3` | `results/2026-06-28-claude-goal-vs-mission-smoke-v3.jsonl`。 |
| Starting commit | `ed98b0e00169f0e0b35ce629a206ffcb7af4d0a3` | runner argument。 |
| Task file | `tasks.complex.json` | smoke task は `complex-cross-file-feature` 1 件。 |
| Records completed | 2 / 2 | summary JSON に 2 records。 |
| Blocked records | 0 / 2 | 両 record が `run_status=completed`。 |
| Quality score method | automated heuristic | blind human review ではない。 |
| Total Claude cost recorded | USD 3.78852475 | raw Claude result JSON files。 |
| Marketing comparison readiness | smoke-only | 1 comparable task だけなので広い性能主張には不足。 |

Smoke result:

| Metric | claude_code_goal_command | mission | Interpretation |
|---|---:|---:|---|
| Completed comparable records | 1 / 1 | 1 / 1 | 両 arm とも同じ smoke task を完了。 |
| Completion rate | 1 / 1 | 1 / 1 | 1 task では tie。 |
| Validator pass rate | 1 / 1 | 1 / 1 | 1 task では tie。 |
| Average quality score | 4.00 / 5 | 4.00 / 5 | automated heuristic score。blind human review ではない。 |
| Average evidence completeness | 4.00 / 5 | 4.00 / 5 | 1 task では tie。 |
| Average elapsed minutes | 1.70 | 6.50 | この smoke では `/mission` が 4.80 分長い。 |

安全な解釈:

> API limit 引き上げ後、Claude Code 公式 `/goal` vs `/mission` の 1 task smoke は
> 両 arm とも完了した。automated heuristic scorer では両 arm とも completion と
> validator に pass し、`/mission` はより時間がかかった。

危険な解釈:

> `mission` は公式 `/goal` より良い。

これは unsupported です。comparable に完了した sample が 1 task しかないためです。

### 10 task full attempt

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-06-28-claude-goal-vs-mission-complex-v1` | `results/2026-06-28-claude-goal-vs-mission-complex-v1.jsonl`。 |
| Starting commit | `ed98b0e00169f0e0b35ce629a206ffcb7af4d0a3` | runner argument。 |
| Task file | `tasks.complex.json` | complex tasks 10 件。 |
| Expected records | 20 | 10 tasks x 2 arms。 |
| Records written | 20 / 20 | summary JSON に 20 records。 |
| Completed comparable records | 0 / 20 | 全 record が `run_status=blocked`。 |
| Blocked records | 20 / 20 | 全 record が `blocked_reason=api_usage_limit`。 |
| Total Claude cost recorded | USD 0.81484175 | raw Claude result JSON files。 |
| Marketing comparison readiness | blocked | full-run の task-quality attempt は comparable に完了していない。 |

Full attempt result:

| Metric | claude_code_goal_command | mission | Interpretation |
|---|---:|---:|---|
| Records | 10 | 10 | expected record count は保存済み。 |
| Completed comparable records | 0 / 10 | 0 / 10 | 全 record が API-limit blocked。 |
| Blocked records | 10 / 10 | 10 / 10 | task-quality result ではない。 |
| Comparable completion rate | n/a | n/a | blocked records を除くと denominator が 0。 |
| Comparable validator pass rate | n/a | n/a | blocked records を除くと denominator が 0。 |

安全な解釈:

> API limit 引き上げ後、1 task smoke は comparable に完了した。しかし 10 task full run は
> Claude Code workspace API usage limit に到達したため、full run からどちらが優れている
> という marketing claim は出せない。

危険な解釈:

> 両 arm とも complex 10 task に失敗した。

これは unsupported です。full-run の全 record は comparable な task-quality attempt 前に
workspace API usage limit で blocked されています。

## Cost-controlled incremental rerun

Status: Claude API budget を追加した後、2026-06-28 JST に実行。
すでに測定済みの first smoke task を再実行しないため、runner に `--task-ids` と
`--stop-on-blocked` を追加しました。incremental run は未測定の complex task 2 件だけを対象にし、
各 Claude invocation を `--max-budget-usd 3.0` で cap しました。

これは cost cap 下の operational comparison として有用ですが、`mission` の full quality
comparison ではありません。`/mission` record 2 件はいずれも、Claude Code が success を返す前に
こちらで設定した per-invocation budget cap に到達しました。

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-06-28-claude-goal-vs-mission-incremental-v1` | `results/2026-06-28-claude-goal-vs-mission-incremental-v1.jsonl`。 |
| Starting commit | `d1cef1d5bd0166b5d61939c8d93ce0060c05507f` | runner argument。 |
| Task file | `tasks.complex.json` | selected tasks のみ。 |
| Selected tasks | 2 | `complex-failing-test-triage`, `complex-review-thread-resolution`。 |
| Expected records | 4 | 2 tasks x 2 arms。 |
| Records written | 4 / 4 | summary JSON に 4 records。 |
| Per-invocation cost cap | USD 3.00 | runner argument `--max-budget-usd 3.0`。 |
| API usage-limit blocked records | 0 / 4 | `blocked_reason=api_usage_limit` の record はなし。 |
| Max-budget blocked records | 2 / 4 | `/mission` 2 records が `blocked_reason=max_budget_usd`。 |
| Total Claude cost recorded | USD 9.39057695 | raw Claude result JSON files。 |
| `/goal` cost recorded | USD 3.31969425 | `/goal` raw Claude result JSON files の合計。 |
| `/mission` cost recorded | USD 6.07088270 | `/mission` raw Claude result JSON files の合計。 |

Incremental result:

| Metric | claude_code_goal_command | mission | Interpretation |
|---|---:|---:|---|
| Records | 2 | 2 | 同じ selected tasks 2 件を試行。 |
| Completed comparable records | 2 / 2 | 0 / 2 | `/mission` records は configured budget cap に到達したため task-quality comparison から除外。 |
| Completion rate | 2 / 2 | 0 / 2 | USD 3.00 cap 下では `/goal` は両 task を完了、`/mission` は success を返せなかった。 |
| Validator pass rate | 2 / 2 | 0 / 2 | `/mission` artifacts は存在するが、Claude Code が `error_max_budget_usd` を返したため complete 扱いしない。 |
| Average elapsed minutes | 3.87 | 7.56 | `/mission` は budget cap に到達するまでより長く走った。 |
| Average quality score | 4.00 / 5 | n/a | max-budget blocked records を除くと `/mission` の denominator は 0。 |

安全な解釈:

> 追加の complex task 2 件に対し、USD 3.00 per-invocation cap を置いた incremental run では、
> 公式 `/goal` は両 task を完了し validator に pass した。`/mission` は artifacts を生成したが、
> 両 task とも configured Claude Code max-budget cap に到達したため、completed task-quality
> measurement ではなく budget-blocked records として扱う。

危険な解釈:

> `mission` の回答品質は公式 `/goal` より低い。

これは unsupported です。incremental run の `/mission` 2 records は completed validator result
ではなく、`error_max_budget_usd` で終了しています。

## Lightweight mission profile rerun

Status: cost を抑えた比較が可能かを検証するため、2026-06-28 JST に実行。
runner に `--mission-profile light` を追加し、`/mission` prompt を 1 回の簡潔な
plan/write/check pass に寄せ、`--mission-max-iter 1` で実行しました。

これは、公式 `/goal` と `/mission` の両方が同じ未測定 complex task を完了した
最初の cost-controlled comparison です。ただし N=1 なので、広い性能主張ではなく
profile-level hypothesis として扱います。

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-06-28-claude-goal-vs-mission-light-v1` | `results/2026-06-28-claude-goal-vs-mission-light-v1.jsonl`。 |
| Starting commit | `d6f12d739a521046e1dc1d198e671a162a803e9c` | runner argument。 |
| Task file | `tasks.complex.json` | selected task のみ。 |
| Selected task | 1 | `complex-failing-test-triage`。 |
| Mission profile | `light` | `mission_profile=light`, `--mission-max-iter 1`。 |
| Expected records | 2 | 1 task x 2 arms。 |
| Records written | 2 / 2 | summary JSON に 2 records。 |
| Blocked records | 0 / 2 | 両 record が `run_status=completed`。 |
| Total Claude cost recorded | USD 5.01240250 | raw Claude result JSON files。 |
| `/goal` cost recorded | USD 3.00670750 | `/goal` raw Claude result JSON。 |
| `/mission` light cost recorded | USD 2.00569500 | `/mission` raw Claude result JSON。 |

Lightweight result:

| Metric | claude_code_goal_command | mission light | Interpretation |
|---|---:|---:|---|
| Completed comparable records | 1 / 1 | 1 / 1 | 両 arm とも同じ selected task を完了。 |
| Completion rate | 1 / 1 | 1 / 1 | この task では tie。 |
| Validator pass rate | 1 / 1 | 1 / 1 | この task では tie。 |
| Average quality score | 4.00 / 5 | 4.00 / 5 | automated heuristic score。blind human review ではない。 |
| Average evidence completeness | 4.00 / 5 | 4.00 / 5 | この task では tie。 |
| Average elapsed minutes | 9.56 | 5.27 | この task では `mission` light が 4.29 分速い。 |
| Recorded Claude cost | USD 3.00670750 | USD 2.00569500 | この task では `mission` light が USD 1.00101250 少ない。 |

安全な解釈:

> 未測定の complex triage task 1 件では、lightweight `/mission` profile と公式 `/goal`
> の両方が完了し、automated validator に pass した。この 1 件では、`mission` light は
> 公式 `/goal` より速く、recorded cost も低かった。

危険な解釈:

> `mission` は公式 `/goal` より安くて速い。

これは unsupported です。completed light-profile sample は 1 task だけです。
次に defensible なのは、同じ per-invocation cost cap で、再利用 task なしの
3-5 task light-profile pilot を実行することです。

## Quality cohort の light-profile 再実行

Status: 2026-07-03 JST に実行。light-profile の evidence を N=1 から追加3件へ
広げるため、`tasks.quality.json` の quality-critical cohort を使いつつ、
`quality` profile ではなく `--mission-profile light` で実行しました。目的は、
品質 marker を残したまま、公式 `/goal` と `/mission` light を bounded budget で
比較することです。

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-07-03-claude-goal-vs-mission-quality-light-v1` | `results/2026-07-03-claude-goal-vs-mission-quality-light-v1.jsonl`。 |
| Starting commit | `1863e02b4cb49bc78399d423ad82c2176627ecdd` | runner argument。 |
| Task file | `tasks.quality.json` | quality-critical cohort を light mission profile で実行。 |
| Selected tasks | 3 | `quality-multi-cause-regression-triage`, `quality-security-secret-handling-plan`, `quality-bilingual-claim-consistency`。 |
| Mission profile | `light` | `--mission-profile light`, `--mission-max-iter 1`。 |
| Expected records | 6 | 3 tasks x 2 arms。 |
| Records written | 6 / 6 | summary JSON に 6 records。 |
| Blocked records | 0 / 6 | `blocked_reason` 付き record はなし。 |
| Quality score method | automated heuristic | blind human review ではない。 |
| Total Claude cost recorded | USD 7.51945750 | raw Claude result JSON files。 |
| `/goal` cost recorded | USD 3.11103025 | `/goal` raw Claude result JSON files の合計。 |
| `/mission` light cost recorded | USD 4.40842725 | `/mission` raw Claude result JSON files の合計。 |

Task-level result:

| Task | Arm | Completion | Validator pass | Human quality score | Quality marker score | Cost | Elapsed | Blocked / failure reason |
|---|---|---:|---:|---:|---:|---:|---:|---|
| `quality-multi-cause-regression-triage` | `claude_code_goal_command` | true | true | 5.00 | 1.00 | USD 1.17585050 | 3.10 min | none |
| `quality-multi-cause-regression-triage` | `mission` light | true | true | 5.00 | 1.00 | USD 1.66316525 | 3.58 min | none |
| `quality-security-secret-handling-plan` | `claude_code_goal_command` | true | true | 5.00 | 1.00 | USD 0.74217350 | 1.98 min | none |
| `quality-security-secret-handling-plan` | `mission` light | true | true | 5.00 | 1.00 | USD 1.35611750 | 3.06 min | none |
| `quality-bilingual-claim-consistency` | `claude_code_goal_command` | true | true | 5.00 | 1.00 | USD 1.19300625 | 2.37 min | none |
| `quality-bilingual-claim-consistency` | `mission` light | true | true | 5.00 | 1.00 | USD 1.38914450 | 2.78 min | none |

Aggregate result:

| Metric | claude_code_goal_command | mission light | Interpretation |
|---|---:|---:|---|
| Completed comparable records | 3 / 3 | 3 / 3 | 両 arm とも selected tasks をすべて完了。 |
| Completion rate | 3 / 3 | 3 / 3 | completion は tie。 |
| Validator pass rate | 3 / 3 | 3 / 3 | validator pass は tie。 |
| Average quality score | 5.00 / 5 | 5.00 / 5 | automated heuristic scoring では tie。 |
| Average quality marker score | 1.00 | 1.00 | tie。tracked quality markers はすべて matched。 |
| Average elapsed minutes | 2.48 | 3.14 | `/mission` light が平均 0.66 分遅い。 |
| Recorded Claude cost | USD 3.11103025 | USD 4.40842725 | `/mission` light が3件合計で USD 1.29739700 高い。 |

安全な解釈:

> light profile で追加実行した quality-critical task 3 件では、公式 `/goal` と
> `/mission` light の両方が全 task を完了し、automated validator に pass し、
> 設定済み quality marker もすべて満たした。この run では `/mission` light は
> 公式 `/goal` より遅く、cost も高かった。

危険な解釈:

> `/mission` light は公式 `/goal` より常に高品質。

これは unsupported です。今回の3件では automated quality / marker score は同点であり、
scoring も blind human review ではなく automated heuristic です。

## Tail cohort run (tail-first-failure)

Status: 2026-07-07 JST に実行。`tasks.tail.json` cohort（planted-defect 5 tasks）を
使った最初の完了 run です。両 arm とも PATH shim 経由で `--model claude-sonnet-5` を
注入し、各 `claude-result.json` の `modelUsage` が `claude-sonnet-5` を全 10 records
で確認しています。

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-07-07-claude-goal-vs-mission-tail-v1` | `results/2026-07-07-claude-goal-vs-mission-tail-v1.jsonl`。 |
| Starting commit | `3591f9947cddb9028538f8d333a0eeb6545b726e` | runner argument。 |
| Task file | `tasks.tail.json` | planted-defect tail cohort、5 tasks。 |
| Mission profile | `full` | デフォルト workflow。`--mission-profile` 指定なし。 |
| Expected records | 10 | 5 tasks x 2 arms。 |
| Records written | 10 / 10 | JSONL に 10 records。 |
| Blocked records | 0 / 10 | 全 records が `run_status=completed`。 |
| Quality score method | `automated_heuristic_form_stripped_not_blind_human` | automated heuristic。blind human review ではない。 |
| model_id | `claude-sonnet-5` | 全 10 records。 |
| Total Claude cost recorded | USD 23.6366052 | 両 arm 5 tasks の合計。 |
| `/goal` cost recorded | USD 2.8077291 | goal 5 records の合計。 |
| `/mission` cost recorded | USD 20.8288761 | mission 5 records の合計。 |

Task-level result:

| Task | Arm | Completion | Validator pass | Quality score | Marker score | Forbidden hits | Cost | Elapsed |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `tail-config-spec-drift` | `claude_code_goal_command` | true | true | 4.86 | 0.86 | 0 | USD 0.49555495 | 1.88 min |
| `tail-config-spec-drift` | `mission` | true | true | 4.86 | 0.86 | 0 | USD 2.62527390 | 8.78 min |
| `tail-incident-log-triage` | `mission` | true | true | 5.00 | 1.00 | 0 | USD 5.53930740 | 18.87 min |
| `tail-incident-log-triage` | `claude_code_goal_command` | true | true | 5.00 | 1.00 | 0 | USD 0.51823745 | 1.90 min |
| `tail-bilingual-release-drift` | `claude_code_goal_command` | true | true | 5.00 | 1.00 | 0 | USD 0.56775820 | 2.22 min |
| `tail-bilingual-release-drift` | `mission` | true | true | 5.00 | 1.00 | 0 | USD 3.66627435 | 13.70 min |
| `tail-metrics-reconciliation` | `mission` | true | true | 5.00 | 1.00 | 0 | USD 3.63863340 | 11.58 min |
| `tail-metrics-reconciliation` | `claude_code_goal_command` | true | true | 5.00 | 1.00 | 0 | USD 0.66852725 | 3.26 min |
| `tail-dependency-upgrade-impact` | `claude_code_goal_command` | true | true | 5.00 | 1.00 | 0 | USD 0.55765120 | 2.37 min |
| `tail-dependency-upgrade-impact` | `mission` | true | true | 5.00 | 1.00 | 0 | USD 5.35938705 | 14.42 min |

Aggregate result:

| Metric | claude_code_goal_command | mission | Interpretation |
|---|---:|---:|---|
| Completed comparable records | 5 / 5 | 5 / 5 | 両 arm とも 5 tasks を完了。 |
| Completion rate | 5 / 5 | 5 / 5 | completion は tie。 |
| Validator pass rate | 5 / 5 | 5 / 5 | validator pass は tie。 |
| Average quality score | 4.97 / 5 | 4.97 / 5 | automated heuristic scoring では tie。 |
| Average quality marker score | 0.97 | 0.97 | tie。1 task が両 arm を 1.0 以下にした。 |
| Forbidden marker hits | 0 | 0 | どちらの arm も decoy false positive はなし。 |
| Total elapsed minutes | 11.63 | 67.35 | `/mission` は wall-clock で ~5.8 倍かかった。 |
| Average elapsed minutes | 2.33 | 13.47 | per-task 平均。 |
| Total recorded Claude cost | USD 2.8077291 | USD 20.8288761 | `/mission` は ~7.4 倍の cost。 |

`/mission` arm は全 5 tasks で plan-review-score loop（full workflow）を実行しました。
`mission-state.py aggregate-reviews` と `push-score` が算出した内部 composite score は
全 5 runs で 4.0 の pass gate を iteration 1 でクリアしました：
`tail-config-spec-drift`（4.29）、`tail-incident-log-triage`（4.53）、
`tail-bilingual-release-drift`（4.54）、`tail-metrics-reconciliation`（5.00）、
`tail-dependency-upgrade-impact`（4.28）。
どの run でも 2 回目の iteration は発生しませんでした。

**Marker false negative と secondary re-score（secondary analysis；primary JSONL は不変）：**
`tail-config-spec-drift` では、両 arm の artifact が `health_check_interval_s` の
drift（spec 15 vs beta 75）を正しく特定していました。goal artifact には
`` `HEALTH_CHECK_INTERVAL_SECONDS=75` ``、mission artifact には
`` `75` (same unit, seconds) `` という記述がありましたが、設定済みの 6 patterns
（`"75 seconds"`、`"75s"`、`"= 75"`、`": 75"`、`"75 vs 15"`、`"15 vs 75"`）は
どちらの表記にもマッチしませんでした。`tasks.tail.json` の marker pattern を
`"seconds=75"`、`"(75"`、`` "75`" `` で拡張しました。
更新後の task 定義を 10 件の保存済み artifact.md に適用した secondary re-score 結果：
`tail-config-spec-drift` 両 arm 0.86 → **1.0**（marker 一致）、他の 8 records は
1.0 のまま変化なし。再集計 marker 平均：両 arm 0.97 → **1.0**、
再集計 quality score 両 arm 4.97 → **5.00**。
primary JSONL の記録スコアは変更せず、これらは secondary analysis の数値です。

安全な解釈:

> first-pass recall challenge として設計した planted-defect 5 tasks では、両 arm の
> automated content-recall metrics は primary scorer で同点（average quality 4.97 / 5、
> average marker score 0.97）となり、どちらの arm も decoy false positive はゼロでした。
> `/mission` arm は全 5 runs で full plan-review-score loop を実行し、iteration 1 で
> 自身の 4.0 pass gate をクリアしました。wall-clock は `/goal` 比で約 5.8 倍、
> 記録 cost は約 7.4 倍です。N=5、1 model、証拠が短い数ファイルに収まる
> closed-world fixture 環境であり、本 run から広範な品質主張は導けません。

危険な解釈:

> tail run の結果から `/mission` は `/goal` より劣る（score 同点、cost 差がある）。

> review loop は score を改善しなかったため不要。

どちらも unsupported です。N=5 の closed-world fixture であり、両 arm が同じ短い
ファイルを見ることができました。設計上の first-pass failure はこの run では再現しませんでした。
`/mission` arm は実際に full loop を実行し、自身の gate を通過しました。
gate は open-world 環境では有効に機能します（`docs/CASE_STUDIES.md` 参照）。

### Pattern-Fix Validation Smoke (2026-07-10)

Status: 2026-07-10 JST に実行。tail-v1 run 後に `tasks.tail.json` に適用した marker
pattern 修正（`"Drift: health interval 75s"` marker の patterns に `"seconds=75"`、
`"(75"`、`` "75`" `` を追加）が、v1 で観測された false negative を解消するかを
検証する single-task smoke です。v1 では両 arm の artifact が `health_check_interval_s`
の drift を正しく特定していましたが、元の 6 patterns はどちらの artifact text にも
マッチしませんでした。この smoke は `tail-config-spec-drift` のみを拡張後の
pattern set で再実行します。

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-07-10-claude-goal-vs-mission-tail-smoke-v2` | `results/2026-07-10-claude-goal-vs-mission-tail-smoke-v2.jsonl`。 |
| Starting commit | `4fdb222b6073cef22676206625ccc61b83c9f658` | runner argument。 |
| Task file | `tasks.tail.json` | planted-defect tail cohort。 |
| Task | `tail-config-spec-drift` | single task；N=1 per arm。 |
| Mission profile | `full` | デフォルト workflow。 |
| Expected records | 2 | 1 task x 2 arms。 |
| Records written | 2 / 2 | JSONL に 2 records。 |
| Blocked records | 1 / 2 | mission arm：`blocked_reason=api_usage_limit`。 |
| Comparable records | 1 / 2 | goal arm は完了、mission arm は除外。 |
| Quality score method | `automated_heuristic_form_stripped_not_blind_human` | automated heuristic。blind human review ではない。 |
| model_id | `claude-sonnet-5` | 両 records。 |
| Total Claude cost recorded | USD 4.27159565 | 両 arm の合計。 |

Result:

| Arm | Completion | Validator pass | Marker score | Forbidden hits | Cost | Elapsed | Comparable |
|---|---|---:|---:|---:|---:|---:|---:|
| `claude_code_goal_command` | true | true | 1.00 | 0 | USD 0.57630950 | 1.76 min | yes |
| `mission` | false | false | null（blocked） | 0 | USD 3.69528615 | 11.80 min | no |

Artifact evidence（mission arm）：`/mission` の run は `run_status=blocked`
（`api_usage_limit`）で終了しましたが、部分的に書き込まれた `artifact.md` には
`HEALTH_CHECK_INTERVAL_SECONDS=75` が含まれており、新 pattern `"seconds=75"` に
マッチします。marker は artifact content 内で特定されており、block は Claude Code が
success を返す前に発生しました。

v1 との比較（task: `tail-config-spec-drift`）:

| Metric | v1 (2026-07-07) | smoke-v2 (2026-07-10) |
|---|---:|---|
| goal arm marker score | 0.86 (6 / 7) | 1.00 (7 / 7) — pattern fix 確認 |
| mission arm marker score | 0.86 (6 / 7) | null / blocked — comparable 外 |

安全な解釈:

> `/goal` arm は再実行を完了し、7 つの quality marker をすべてマッチ（score 1.00）
> しました。v1 で primary scorer が見逃した `"Drift: health interval 75s"` も含まれています。
> pattern 修正（`"seconds=75"`、`"(75"`、`` "75`" ``）は comparable arm での false
> negative を解消します。`/mission` arm は workspace API usage limit で success を
> 返す前に blocked されたため、marker score は comparable 外です。artifact 検査では、
> partial output 内に health interval drift が正しく特定されていることを確認しています。
> N=1 task、1 model、comparable arm は 1 本です。

危険な解釈:

> pattern 修正により、両 arm での false negative が完全に解消された。

これは unsupported です：mission arm の block により comparable な validator result が
得られず、per arm 1 回の実行では将来の実行を保証しません。

## Quality-focused critical task attempt

Status: `quality` mission profile と fresh な `tasks.quality.json` cohort を追加した後、
2026-06-28 JST に実行。これは `/mission` が品質差を出しやすい task type、つまり
evidence map、rejected hypotheses、stop/proceed decision、residual risk、
unsafe-claim control を測るための profile です。

paired comparison は **完了していません**。公式 `/goal` arm が Claude Code workspace
API usage limit に到達し、success を返す前に停止したため、`/mission` arm は実行して
いません。これは infrastructure/account result であり、どちらの arm の品質が高いかを
示す evidence ではありません。

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-06-28-claude-goal-vs-mission-quality-v1` | `results/2026-06-28-claude-goal-vs-mission-quality-v1.jsonl`。 |
| Starting commit | `e443bb421cdc58418085790b1fb6733dd3ef89f5` | runner argument。 |
| Task file | `tasks.quality.json` | fresh quality-critical cohort。 |
| Selected task | 1 | `quality-critical-release-governance`。 |
| Mission profile | `quality` | `--mission-profile quality`, `--mission-max-iter 2`。 |
| Expected records | 2 | 1 task x 2 arms。 |
| Records written | 1 / 2 | `/goal` blocked 後に stop-on-blocked で停止。 |
| Blocked records | 1 / 1 | `/goal` が `blocked_reason=api_usage_limit`。 |
| `/mission` records | 0 | API budget 保全のため未実行。 |
| `/goal` cost before stop | USD 1.01481150 | raw Claude result JSON。 |
| Quality-marker comparison | unavailable | blocked records は comparable quality-marker aggregate から除外。 |

安全な解釈:

> quality-focused benchmark profile と fresh critical task cohort は整備された。
> ただし最初の公式 `/goal` vs `/mission` quality attempt は、comparable pair 完了前に
> Claude Code workspace API usage limit で blocked された。

危険な解釈:

> quality-critical task では `mission` の品質が公式 `/goal` より高い。

これは unsupported です。両 arm の paired run が完了していません。

## Task-Level Findings

| Task | Stronger arm | Why |
|---|---|---|
| `docs-small-edit` | evidence は `mission`、completion は tie | 両方 pass。mission はより構造化された evidence と state を残した。 |
| `docs-cross-reference` | evidence は `mission`、completion は tie | 両方 pass。mission は plan/review/score section を含んだ。 |
| `bug-regression-test` | evidence は `mission`、completion は tie | 両方 pass。mission は completion trace が強いが runtime は長い。 |
| `review-comment-batch` | evidence は `mission`、completion は tie | 両方 pass。mission は review/check evidence を明示した。 |
| `interrupted-doc-task` | resume は tie、evidence は `mission` | 両方 resume validator pass。mission は state-backed evidence を残した。 |
| `ambiguous-research-brief` | evidence は `mission`、completion は tie | 両方 pass。mission は assumptions と review をより明示した。 |
| `release-checklist-audit` | evidence は `mission`、completion は tie | 両方 pass。mission は evidence tracking intent により合っていた。 |
| `small-refactor` | evidence は `mission`、completion は tie | 両方 pass。validator success だけなら goal-only も十分だった。 |
| `quality-gate-failure` | evidence は `mission`、completion は tie | 両方 pass。mission は stop decision をより明示した。 |
| `marketing-claim-draft` | evidence は `mission`、completion は tie | 両方 pass。mission は claim guardrails をより明示した。 |

## Validation Results

| Check | Result |
|---|---:|
| Benchmark + doc consistency tests | 30 passed / 30 |
| Full mission test suite | 402 passed / 402 |
| JSON parse checks | 2 passed / 2 |
| Scoped whitespace check | passed |

使用した commands:

```bash
python3 benchmarks/mission-vs-goal/run_paired_pilot.py --starting-commit 0148f16 --timeout 900
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py --tasks-file benchmarks/mission-vs-goal/tasks.complex.json --run-id 2026-06-28-claude-goal-vs-mission-smoke-v2 --starting-commit 38cc7907e5e35fcd9fa23022a1fcf03f756df99b --limit-tasks 1 --timeout 300 --max-budget-usd 1.5 --mission-max-iter 1
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py --tasks-file benchmarks/mission-vs-goal/tasks.complex.json --run-id 2026-06-28-claude-goal-vs-mission-smoke-v3 --starting-commit ed98b0e00169f0e0b35ce629a206ffcb7af4d0a3 --limit-tasks 1 --timeout 900 --max-budget-usd 3.0 --mission-max-iter 2
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py --tasks-file benchmarks/mission-vs-goal/tasks.complex.json --run-id 2026-06-28-claude-goal-vs-mission-complex-v1 --starting-commit ed98b0e00169f0e0b35ce629a206ffcb7af4d0a3 --limit-tasks 10 --timeout 1800 --max-budget-usd 3.0 --mission-max-iter 2
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py --tasks-file benchmarks/mission-vs-goal/tasks.complex.json --run-id 2026-06-28-claude-goal-vs-mission-incremental-v1 --starting-commit d1cef1d5bd0166b5d61939c8d93ce0060c05507f --task-ids complex-failing-test-triage,complex-review-thread-resolution --stop-on-blocked --timeout 1200 --max-budget-usd 3.0 --mission-max-iter 2
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py --tasks-file benchmarks/mission-vs-goal/tasks.quality.json --run-id 2026-07-03-claude-goal-vs-mission-quality-light-v1 --starting-commit 1863e02b4cb49bc78399d423ad82c2176627ecdd --task-ids quality-multi-cause-regression-triage,quality-security-secret-handling-plan,quality-bilingual-claim-consistency --stop-on-blocked --timeout 1200 --max-budget-usd 4.0 --mission-max-iter 1 --mission-profile light --run-root /private/tmp/mission-vs-official-goal-2026-07-03-light-v1
python3 -m pytest skills/mission/tests/test_benchmark_package.py skills/mission/tests/test_doc_consistency.py -q
python3 -m pytest skills/mission/tests -q
python3 -m json.tool benchmarks/mission-vs-goal/tasks.json
python3 -m json.tool benchmarks/mission-vs-goal/tasks.complex.json
python3 -m json.tool benchmarks/mission-vs-goal/result.schema.json
python3 -m py_compile benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py
git diff --check -- README.md README.ja.md docs/LOOP_ENGINEERING.md benchmarks/mission-vs-goal/README.md benchmarks/mission-vs-goal/README.ja.md benchmarks/mission-vs-goal/report.md benchmarks/mission-vs-goal/report.ja.md benchmarks/mission-vs-goal/report-template.md benchmarks/mission-vs-goal/report-template.ja.md benchmarks/mission-vs-goal/complex-validation-plan.md benchmarks/mission-vs-goal/complex-validation-plan.ja.md benchmarks/mission-vs-goal/official-goal-rerun-runbook.md benchmarks/mission-vs-goal/official-goal-rerun-runbook.ja.md benchmarks/mission-vs-goal/run_paired_pilot.py benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py skills/mission/tests/test_benchmark_package.py
```

## Marketing Summary

言ってよい:

> controlled local 10-task Codex CLI pilot では、goal-only と `mission` は
> どちらも全 task を完了し、全 validator に pass しました。`mission` は automated
> evidence / completion-quality score が高く、一方で run あたりの時間は長くなりました。

公式 `/goal` smoke について言ってよい:

> complex task 1 件で Claude Code 公式 `/goal` smoke を試行した。`/goal` は artifact
> を完了したが、comparable な `/mission` arm は Claude Code workspace API limit で
> blocked された。`/mission` arm が完了するまで、この smoke から marketing comparison
> は出せない。

API limit 引き上げ後の再実行について言ってよい:

> API limit 引き上げ後、公式 `/goal` vs `/mission` の 1 task smoke は両 arm とも
> 完了し、automated validator に pass した。この 1 task smoke では automated
> quality / evidence score は 4.00 / 5 で同点、`/mission` はより時間がかかった。
> その後の 10 task full attempt は workspace API usage limit で blocked されたため、
> 性能主張には使えない。

Cost-capped incremental rerun について言ってよい:

> 追加の complex task 2 件を USD 3.00 per-invocation cap で実行したところ、公式 `/goal`
> は両 task を完了し、`/mission` は両 task で configured max-budget cap に到達した。
> これは cost/runtime 上の注意材料であり、`/mission` の回答品質が低いという主張ではない。

Light-profile rerun について言ってよい:

> 未測定の complex task 1 件では、公式 `/goal` と `/mission` light の両方が完了し
> validator に pass した。この 1 件では `/mission` light が速く、cost も低かったが、
> broad claim には sample が小さすぎる。

2026-07-03 の light-profile rerun について言ってよい:

> `--mission-profile light` で追加実行した quality-critical task 3 件では、公式 `/goal`
> と `/mission` light の両方が完了し、全 automated validator に pass し、
> automated quality-marker score も同点だった。この run では `/mission` light は
> 公式 `/goal` より遅く、cost も高かった。

Quality-profile attempt について言ってよい:

> quality-focused profile と fresh critical task cohort は追加された。ただし最初の paired
> attempt は `/mission` 実行前に Claude Code workspace API limit で blocked されたため、
> 品質比較には使えない。

Tail run について言ってよい:

> first-pass recall challenge として設計した planted-defect 5 tasks では、両 arm の
> automated content-recall metrics は同点（average quality 4.97 / 5、average marker
> score 0.97）で、どちらの arm も decoy false positive はゼロでした。`/mission` arm は
> full review loop を実行し、全 5 runs で iteration 1 の 4.0 pass gate をクリアしました。
> wall-clock は `/goal` 比で約 5.8 倍、記録 cost は約 7.4 倍です。N=5、
> closed-world fixture、1 model であり、この run から広範な品質主張は導けません。

言ってはいけない:

> tail run の結果から `/mission` は `/goal` より劣る（score 同点、cost 差がある）。

言ってはいけない:

> `mission` は `/goal` より賢い。

言ってはいけない:

> この pilot で `mission` は completion rate を改善した。

言ってはいけない:

> 2026-06-28 の smoke に基づいて、`mission` は Claude Code 公式 `/goal` より良い /
> 悪い。

## Raw Result Index

```text
results/2026-06-27-codex-cli-local.jsonl
results/2026-06-27-codex-cli-local-summary.json
artifacts/2026-06-27-codex-cli-local/
results/2026-06-28-claude-goal-vs-mission-smoke-v2.jsonl
results/2026-06-28-claude-goal-vs-mission-smoke-v2-summary.json
artifacts/2026-06-28-claude-goal-vs-mission-smoke-v2/
results/2026-06-28-claude-goal-vs-mission-smoke-v3.jsonl
results/2026-06-28-claude-goal-vs-mission-smoke-v3-summary.json
artifacts/2026-06-28-claude-goal-vs-mission-smoke-v3/
results/2026-06-28-claude-goal-vs-mission-complex-v1.jsonl
results/2026-06-28-claude-goal-vs-mission-complex-v1-summary.json
artifacts/2026-06-28-claude-goal-vs-mission-complex-v1/
results/2026-06-28-claude-goal-vs-mission-incremental-v1.jsonl
results/2026-06-28-claude-goal-vs-mission-incremental-v1-summary.json
artifacts/2026-06-28-claude-goal-vs-mission-incremental-v1/
results/2026-06-28-claude-goal-vs-mission-light-v1.jsonl
results/2026-06-28-claude-goal-vs-mission-light-v1-summary.json
artifacts/2026-06-28-claude-goal-vs-mission-light-v1/
results/2026-06-28-claude-goal-vs-mission-quality-v1.jsonl
results/2026-06-28-claude-goal-vs-mission-quality-v1-summary.json
artifacts/2026-06-28-claude-goal-vs-mission-quality-v1/
results/2026-07-03-claude-goal-vs-mission-quality-light-v1.jsonl
results/2026-07-03-claude-goal-vs-mission-quality-light-v1-summary.json
artifacts/2026-07-03-claude-goal-vs-mission-quality-light-v1/
results/2026-07-07-claude-goal-vs-mission-tail-v1.jsonl
results/2026-07-07-claude-goal-vs-mission-tail-v1-summary.json
artifacts/2026-07-07-claude-goal-vs-mission-tail-v1/
results/2026-07-10-claude-goal-vs-mission-tail-smoke-v2.jsonl
results/2026-07-10-claude-goal-vs-mission-tail-smoke-v2-summary.json
artifacts/2026-07-10-claude-goal-vs-mission-tail-smoke-v2/
```
