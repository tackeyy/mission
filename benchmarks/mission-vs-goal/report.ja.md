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

## 測定妥当性警告: permission-mode 汚染 (2026-07-23 監査で検出)

2026-07-21 以降の全 run (`tail-v2` / `tail-v2-retry` / fable5 系 / `openworld-v1` /
`discriminating-smoke` / `discriminating-v1`) は、CC セッションの Bash から runner を
起動したことにより `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` が子 `claude -p` に伝播し、
runner 指定の `--permission-mode acceptEdits` が **default に強制降格** された状態で
実行されている (各 record の stderr に警告あり)。`tail-v1` (2026-07-07) は正常モード。

影響の整理:

- **アーム内比較 (同一 run 内の goal vs mission) は有効** — 降格は両 arm に一様に
  適用された対称条件
- **run 間比較は交絡** — 「mission オーバーヘッド 5.8x (tail-v1) → 2.9x (tail-v2)
  → 1.07x (discriminating-v1)」の改善傾向は permission mode の環境変化と分離できず、
  mission 改善の効果と断定してはならない
- **discriminating-v1 の goal 予算全損 2 件の機序は未確定** — blocked goal は 76 turns・
  cache read 12.6M tokens で成果物ファイルを一度も作成していない。permission 摩擦が
  発散を助長した可能性を排除できず、task 難度に起因すると断定してはならない
- 検出ガードと再発防止は #268 (runner が `permission_mode_degraded` を record に記録、
  runner 起動時に `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=0` を明示)

クリーンな permission mode での再 run が完了するまで、下記 discriminating-v1 節の
completion-rate finding は preliminary として扱うこと。

## Portfolio + discriminating v3 run (v2.1.0 改善後、2026-08-02)

Status: v2.1.0 (adaptive routing #276/#304、critic scope 強制 #309、tier 2名化 #266、
実運用監査 findings F1-F6 全対応) を含む commit `73abd72` で、クリーン条件
(#268 ガード全 record `permission_mode_degraded=false`、`--parallel 3`、両 arm
同一条件 $10/30min) の 2 run を実行した。CLI は 2.1.220。

### portfolio-v1 (V1 判定: routing 込み実効オーバーヘッド)

8 tasks (Simple 3 / Standard 3 / Complex 2) x 2 arms = 16 records、全完走・品質は
全 record marker 1.0 で同点。

| 層 | goal 平均 | mission 平均 | 比 |
|---|---:|---:|---:|
| Simple (3) | 0.54 min | 3.03 min | 5.6x |
| Standard (3) | 0.84 min | 7.65 min | 9.1x |
| Complex (2) | 1.54 min | 5.75 min | 3.7x |
| **合計** | **7.23 min / $4.34** | **43.54 min / $16.41** | **6.0x 時間 / 3.8x コスト** |

**V1 (≤1.3x): 未達。** 根本原因を state で確認: Simple 3 tasks の mission arm は
**adaptive routing が発火せず** light tier のフルループ (iter1・passes) が走った。
routing は `init` の complexity 引数でのみ判定され、orchestrator が「init → 分類 →
`set complexity=Simple`」の順で動く経路では routing を通らない (#325)。

### discriminating-v3 (V2/V3 判定)

5 tasks x 2 arms = 10 records、全完走。品質は全 record marker 1.0 で同点 (V2 未達)。
goal 平均 1.45 min / $0.61 vs mission 平均 11.06 min / $3.12 = **7.6x 時間 / 5.1x コスト**
(V3 ≤2.5x 未達)。goal 側が CLI 2.1.220 + クリーン条件で大幅高速化しており
(v2 比 ~1.4x 高速)、mission の床は比例して下がらないため相対差は拡大した。

注目すべき 2 事実:

1. **品質ゲートの実働例**: release-ledger mission は iter1 composite 3.58 (threshold
   未達) → 修正 → iter2 4.25 で pass。marker では両 arm 1.0 同点だが、ゲートは
   marker が測らない内部品質の欠陥を検出・改善させた (代償: 23.95 min / $6.13 vs
   goal 1.83 min / $0.66)
2. **#309 の guidance 強制は bypass された**: 上記 iter2 で `critic_has_new_scope`
   は None のまま — orchestrator が review 前に `next` を呼ばない経路があり、
   guidance 層の強制は不完全。hard gate 化を #326 で追跡

### 判定 (2026-08-02 時点の確定)

- **V1 routing 込み ≤1.3x: 未達** (6.0x)。#325 修正後に再測が必要
- **V2 品質 marker 差 > 0: 未達** — 全 cohort・全条件で同点。sonnet-5 の天井は
  cohort 設計で解消できないと 3 回の独立設計で確定
- **V3 非 Simple ≤2.5x: 未達** (7.6x)。goal の高速化により相対差はむしろ拡大
- mission の実証済み価値は不変: completion gate (iter1 3.58 → iter2 4.25 の実働例) +
  テール保険 + ガバナンス + budget guard + resume

危険な解釈:

> 改善は無意味だった。

これは unsupported です。v2.1.0 の改善は絶対値では効いている (mission Complex が
v1 汚染時代の 13-21 min → 5-8 min)。相対差の拡大は goal (単発 CLI) 側の高速化が
mission の構造床 (複数パス) を上回ったため。「単発タスクの速度で goal に勝つ」は
アーキテクチャ上不可能であり、勝負軸は routing の完全化 (#325: 負けをなくす) と
ゲートが要る場面での完遂品質に確定した。

### portfolio-v2 (#325/#326 マージ後の V1 再測、2026-08-02)

commit `17cedf1` (#325 next 駆動 routing gate + #326 scope hard gate 込み) で再測。
16 records 全完走・degraded 0・品質は全 record marker 1.0 同点。

**V1 再判定: 未達 (7.6x 時間 / 4.4x コスト、v1 の 6.0x から実質横ばい — N=1/cell の
run 間分散内)。** state 実測で routing 発火は **1/3 のみ**: simple-typo は inline 完遂
(1.46 min = goal 0.63 + 約 0.8 分の入口オーバーヘッド、ただし halt カテゴリを
evidence-submitted と誤用)、simple-lookup / simple-diff は `next` を planning で
呼ばずに planner を spawn しフルループが走った。guidance 層の強制は #309/#326 と
同じ「next 未消費」類型で bypass される。発火した 1 件は routing が効けば Simple
mission ≈ goal + 0.8 分に収まることを実証しており、コマンド層での hard 化
(`set complexity=Simple` が routing verdict を直接実行) を #330 で追跡する。

危険な解釈:

> #325 は無効だった。

これは unsupported です。gate 自体は正しく動作する (単体テストで検証済み)。
未発火の原因は orchestrator の next 消費規律であり、コマンド層 hard 化 (#330) で
閉じられる見込み。発火例 1.46 min が到達可能性の証拠。

### portfolio-v3 (#330 マージ後の V1 最終判定、2026-08-02)

commit `3460c41` (#330 コマンド層 hard routing 込み)。16 records 全完走・degraded 0・
品質は全 record marker 1.0 同点。合計 goal 7.17 min/$4.11 vs mission 36.97 min/$13.83
= **5.16x / 3.37x** — 3 回のポートフォリオ測定で最良だが V1 (≤1.3x) は未達で確定。

**routing 発火は 0/3、そして根本原因が確定した。** state 実測で Simple 3 件すべて
`force_mission=True` — **ベンチの mission arm プロンプト自身**が「Use the /mission
plugin workflow with auditable state」と loop 機構を明示要求し、必須見出し
(Mission/Plan/Execution/Review/Score) が loop 形成果物を強制しているため、
orchestrator は指示に忠実に `--force-mission` していた。**測定設計が測定対象を
抑制する自己矛盾**であり、製品側 routing (init #276 / set #330 / next #325 の 3 層、
単体検証済み) の欠陥ではない。加えて本 cohort の Simple は goal 30 秒級のため、
routing の入口オーバーヘッド (~0.8-1 分) がある限り ≤1.3x は構造的に到達不能。
ベンチ改訂 (routing 許容プロンプト + 分単位 Simple) は #333 で追跡。

副次観測: std-contract / std-policy の mission が 1.85-1.9 min ($0.79-0.86) で完走
(orchestrator が benchmark-controlled single-pass を自律選択)。同一 run 内で
std-metrics は 6.37 min のフルループ — orchestrator 挙動の run 間分散は依然大きい。

**V1 の最終判定: この cohort・このプロンプト設計では未達で確定。** 製品の routing は
実運用 (force 指示のない通常利用) で機能する設計・実装・検証済み。ベンチでの実証は
#333 の改訂後に持ち越す。

危険な解釈:

> routing は機能しない。

これは unsupported です。3 層すべて単体検証済みで、v2 では 1 件の実発火 (1.46 min =
goal + 0.8 分) も観測されている。未発火はベンチプロンプトの `--force-mission` 誘導に
よるもので、指示なし環境では発火する。

### portfolio-v4 (#333 測定改訂後の V1 正当再測、2026-08-02) — routing パリティ達成

commit `623d4d7` (#333: routing 許容プロンプト + mission_routed 第一級記録 +
分単位 Simple)。16 records 全完走・degraded 0・品質は全 record marker 1.0 同点。

**routing 発火 3/3 (初の完全発火)、Simple 層は goal 2.90 min vs mission 3.42 min =
1.18x で事前宣言のパリティ帯 (≤1.3x) を達成。** コストも $0.52-0.57 vs $0.60-0.70 で
ほぼ同等。routed mission の内訳は goal + 0.1-0.3 分の入口オーバーヘッドのみで、
「Simple で mission を常用しても goal に負けない」が本プログラムで初めて実測成立した。

| 層 | goal | mission | 比 |
|---|---:|---:|---:|
| **Simple (routed 3/3)** | 2.90 min / $1.61 | **3.42 min / $1.93** | **1.18x / 1.20x** |
| Standard (full loop) | 2.55 min | 24.26 min | 9.5x |
| Complex (full loop) | 3.04 min | 8.19 min | 2.7x |
| 合計 | 8.49 min / $4.33 | 35.87 min / $12.25 | 4.22x / 2.83x |

ポートフォリオ全体の V1 (≤1.3x) は未達 (4.22x) だが、その残差は Standard/Complex が
**意図どおり品質ゲート付きループを回すコスト**であり、routed 層のパリティ達成により
「負けをなくす」戦略の実測検証は完了した。非 routed 層のオーバーヘッドは
ゲート価値 (iter1 3.58→4.25 の実働例) との交換で、これが mission の製品定義。

危険な解釈:

> mission は goal に速度で勝てるようになった。

これは unsupported です。達成したのは routed Simple 層の「同等」(1.18x) であり、
ゲート付きループを回す層は従来どおり 2.7-9.5x かかる。またオーケストレータの
挙動分散は大きく (同層内で 2.99-10.16 min)、N=1/cell の単発測定である。


### portfolio-v5-speed (#338/#339/#341 適用後の Standard+Complex 再測、2026-08-02)

commit `5c6a1d4` (#338 並列検証可能化 + #339 ターン圧縮/plan-inline + #341 bench
role 固定のマージ後)。routing 検証済みの Simple 3 tasks を除く Standard 3 +
Complex 2 の paired 10 records。全完走・degraded 0・品質は全 record marker 1.0 同点。

| task | goal | mission | 比 | 備考 |
|---|---:|---:|---:|---|
| std-contract | 0.72 min | 18.27 min | 25.4x | **iter=2 (ゲート再作業発火)** — 速度比較対象外 |
| std-metrics | 0.49 min | 6.02 min | 12.3x | iter=1・直列 |
| std-policy | 0.92 min | **4.84 min** | 5.3x | iter=1・**唯一の並列発火 (api 354s > wall 288s)** |
| cx-config | 1.09 min | 8.33 min | 7.6x | iter=1・直列 |
| cx-ledger | 1.61 min | 14.38 min | 8.9x | iter=1・**初の真正フルループ実測 (#341 効果)** |

観測事実:

1. **#341 は完全発火** (`evidence_only_records: 0`)。v4 で checker 挙動により
   2.99 min (1.53x) と過小測定されていた cx-ledger が、初めて full-tier ループとして
   実測された (14.38 min / 8.9x)。**Complex 層の正味フルループは 7.6-8.9x** であり、
   v4 の「2.7x」は無効測定を含む過小値だった。
2. **#338 (並列) の発火は 1/5**。発火した std-policy は 4.84 min と Standard 最速
   (v4 同 task 10.16 min の約 1/2)。残り 4 record は API 時間 ≒ wall 時間の直列のまま。
3. **#339 (ターン圧縮/plan-inline) は発火せず**。turns 26-53 で v4 (16-39) から
   削減なし。orchestrator が `next` を参照しない既知経路 (portfolio-v2 と同型)。
4. std-contract は iter=2 でゲート再作業が走り 18.27 min。速度比較からは外れるが、
   ゲートの実働 (閾値未達→修正→pass) がベンチ内で再現された。

危険な解釈:

> #338/#339 は効果がなかった。

これは unsupported です。正しくは「**guidance 層の指示は発火率が低い**」の 4 回目の
実測確認 (disc-v3 / portfolio-v1 / v2 に続く)。発火した唯一の record は約 2 倍高速で
あり機構自体は有効。ただし spawn の並列性やターン構造はコマンド層 (CLI) から強制
できず、ハーネス側の制約が上限になる。iter=1 同条件の 2 task (metrics/policy) 合算は
14.99→10.86 min (9.8x→7.7x) だが、オーケストレータ分散 (N=1) の範囲内。

一次データ: `results/2026-08-02-portfolio-v5-speed.jsonl` /
`results/2026-08-02-portfolio-v5-speed-summary.json` /
`artifacts/2026-08-02-portfolio-v5-speed/`

### portfolio-v6-repeats3 (repeats=3 再測の試行、2026-08-07) — spend limit により部分無効

commit `5ac06b3`。v5-speed と同一 5 タスク (Standard 3 + Complex 2) x 2 arms x repeats=3
= 30 records。**組織の月間 API spend limit 到達 (HTTP 429) により 12 records が即死**
(elapsed 0.04-0.08 min)、mission 3 records が `--max-budget-usd 6.0` の上限で blocked、
1 record が validator fail。summary の arm 集計 (goal completion 0.53 等) は 429 汚染を
含むため **arm 間比較には使用しない**。runner 側の対応を #357 (429 の blocked-external
分類 + 自動停止) / #358 (arm 別予算上限) として起票済み。

limit 到達前に完走した mission 6 records からの救済観測:

| task | v6 実測 (iter) | v5-speed (iter) | 解釈 |
|---|---|---|---|
| std-contract | 6.29 min (1) | 18.27 min (2) | **v5 の 18.27 は iter=2 外れ値と確認**。iter=1 なら v4 (9.27) より速い |
| std-metrics | 6.55 min (1) | 6.02 min (1) | 再現 (分散小) |
| std-policy | 5.08 min (1) | 4.84 min (1) | 再現 (分散小) |
| cx-ledger | 13.62 min (1, full) | 14.38 min (1, full) | **full-tier フルループのコストは再現性あり (13.6-14.4 min)** |

- v6 の 6 comparable mission records に iter=2 は 0 件。marker 1.0 は完走全 record で維持
- mission full-profile の record 単価は $5-6 に達し (v5 平均 $3.07 → v6 $4.71)、$6 上限が
  3 records を欠測させた。予算上限の較正は #358
- repeats=3 の分散確定は spend limit 解除後に #357/#358 適用済み runner で再走する

一次データ: `results/2026-08-07-portfolio-v6-repeats3.jsonl` /
`results/2026-08-07-portfolio-v6-repeats3-summary.json` /
`artifacts/2026-08-07-portfolio-v6-repeats3/`

### portfolio-v7 (repeats=3 分散確定、2026-08-07) — #357/#358 適用 runner

commit `1e8bdc3` (#357 spend-limit 自動停止 + #358 arm 別予算 + #362 適用後)。v5-speed と
同一タスクを 2 分割で再測: v7a = Standard 3 tasks x repeats 3 (18 records、完走)、
v7b = Complex 2 tasks x repeats 3 (12 中 10 records、spend limit で自動停止)、
v7c = 補完走の試行 (枠未回復のため record 1/4 で自動停止・消費 $0)。予算は
goal $3 / mission $10 (#358 較正値)。全完走 record で marker 1.0 品質同点。

Standard (v7a、mission n=9): 平均 9.98 min / 中央値 10.58 / 範囲 6.26-14.08。
iter=2 発火は 9 中 2 回 (いずれも std-metrics)。goal 平均 1.11 min → 約 9.0x。
v5-speed の各値 (metrics 6.02 / policy 4.84 / contract 18.27 iter=2) はいずれも
この分布内にあり、単発値の差は反復分散で説明できる。

Complex (v7b、mission comparable n=4): cx-config 11.04 / 12.99 min (standard tier)、
cx-ledger 17.41 / 17.58 min (full tier)。v5/v6 の 8.33-18.27 / 13.62-14.38 と整合し、
**full-tier フルループ 13.6-17.6 min は再現性のある実コスト**。goal 平均 1.59 min。

運用上の実測:

1. **#357 は 2 回実戦発火**した。v7b は record 10/12 で 429 を blocked-external 分類し
   未起動 record を停止、v7c は record 1/4 で即停止 (消費 $0)。v6 型の 12 連続無駄打ちは
   再発していない。
2. **#358 の $10 較正は必須だった**。mission の record 単価は $4.71-9.31 で、旧上限
   $6 なら v7a の 9 中 6 records が blocked になっていた。budget_blocked_records は 0。
3. cx タスクの n=3 化は枠回復待ちが必要なため見送り、n=2 (範囲 0.2-2.0 min) を
   確定データとする。

一次データ: `results/2026-08-07-portfolio-v7a-std-repeats3.jsonl` /
`results/2026-08-07-portfolio-v7b-cx-repeats3.jsonl` /
`results/2026-08-07-portfolio-v7c-cx-fill.jsonl` (各 -summary.json 併置)

### portfolio-v8a (Standard 再測、2026-08-07) — #350-#355 適用後の gate リグレッション確認

commit `05d08c5` (#350 reviewer-window 必須化 + #351 artifact lint + #352 bounded context
観測 + #353 reviewer 出力観測 + #354 session lease + #355 goal dispatch の全マージ後)。
v7a と同一の Standard 3 tasks x repeats 3 (18 records)、予算 goal $3 / mission $10。
mission は 7/9 完走、残り 2 records (std-metrics rep3 / std-policy rep3) は
api_spend_limit で #357 が自動停止 (blocked、非比較対象)。全完走 record で marker 1.0
品質同点。

Standard (mission comparable n=7): 平均 9.26 min / 中央値 8.46 / 範囲 6.78-13.28。
v7a (平均 9.98 / 中央値 10.58 / 範囲 6.26-14.08) と同水準で、**#350-#353 の
検証・観測ゲート追加による速度リグレッションはない**。iter=2 発火は 7 中 0 回
(v7a は 9 中 2 回。iter 分布の揺らぎの範囲内)。goal 平均 1.11 min → 約 8.4x。
mission record 単価 $5.25-9.31 で #358 の $10 較正内に収まる。

観測ゲートの発火実測 (state 検分、n=7):

0. **#350 reviewer-window 必須化の並列発火率は 7/7 = 100%** (last_parallel_execution
   全 true、unknown 0%。v5-speed 時点の機械観測 1/5 から gate 化で完全収束)。
1. **#353 reviewer 出力観測は全 run で記録された** (reviewer_output_records 2 件/run)。
   WARN 発火 (20KB/0.7 閾値超過) は 0 件。
2. **#354 session lease は全 run で付与された** (lease_id / fencing_epoch / 期限)。
   競合・fencing 拒否は 0 件。
3. **#351 artifact lint は全 run で `skipped`**。bench の mission session は state に
   `artifact_path` を記録しないため、lint は一度も走っていない (発火率 0 の意味は
   「違反なし」ではなく「観測機会なし」)。gate 化判断には artifact_path 記録の普及が
   前提になる。
4. **#352 bounded context は全 run で対象外**。完走 run が全て iter=1 で終了し、
   発火条件 (iter>=2 かつ critic 新規スコープなし) に到達しなかった。
5. **#357 は本 run でも 2 回実戦発火**し、無駄打ちなく停止した。

一次データ: `results/2026-08-07-portfolio-v8a-std-post-gates.jsonl`
(-summary.json 併置) / `artifacts/2026-08-07-portfolio-v8a-std-post-gates/`



## Discriminating cohort clean re-run (discriminating-v2) — v1 findings の訂正

Status: 2026-07-23 JST に実行。permission-mode 汚染 (#268) を排除したクリーン
条件での再測定。starting commit `3643943` (#266 Complex=standard 2名 + #268 ガード
+ #270 並列 込み)。`--parallel 3`、両 arm 同一条件 ($10 予算 / 30分 budget-minutes /
timeout 2400s)。**全 10 records で `permission_mode_degraded=false` を機械確認**
(#268 ガードの初運用)。壁時計は並列 3 で約 33 分。

### Task-level result

| Task | Arm | Status | Tier / iter | Marker | Cost | Elapsed |
|---|---|---|---|---:|---:|---:|
| `disc-config-sprawl` | goal | completed | — | 1.00 | USD 0.76 | 1.48 min |
| `disc-config-sprawl` | mission | completed | standard / 1 | 1.00 | USD 3.63 | 7.50 min |
| `disc-release-ledger` | goal | completed | — | 1.00 | USD 0.95 | 3.12 min |
| `disc-release-ledger` | mission | completed | (tier 未観測) / 1 | 1.00 | USD 2.17 | 7.68 min |
| `disc-contract-drift` | goal | completed | — | 1.00 | USD 0.84 | 2.38 min |
| `disc-contract-drift` | mission | completed | standard / 1 | 1.00 | USD 4.28 | 12.64 min |
| `disc-metrics-reconcile` | goal | completed | — | 1.00 | USD 0.68 | 1.18 min |
| `disc-metrics-reconcile` | mission | completed | standard / 1 | 1.00 | USD 4.03 | 11.09 min |
| `disc-policy-exceptions` | goal | completed | — | 1.00 | USD 0.73 | 1.63 min |
| `disc-policy-exceptions` | mission | completed | full (Critical) / 1 | 1.00 | USD 5.27 | 13.72 min |

### v1 findings の訂正 (クリーン条件で覆った結論)

1. **「goal は予算全損しやすい」→ 撤回**: v1 で blocked した release-ledger /
   contract-drift は、クリーン条件では goal がそれぞれ 3.12 min / USD 0.95、
   2.38 min / USD 0.84 で完走・validator pass。v1 の予算全損 (24.9 min / USD 10.05、
   26.5 min / USD 10.10) は permission-mode 汚染の人工物だった
2. **「速度≈goal (1.07x)」→ 撤回**: クリーン条件の comparable 平均は goal 1.96 min
   vs mission 10.53 min = **5.4x**。v1 の 1.07x は汚染による goal 側の実行時間膨張
   (クリーン比で 3-10x) が生んだ見かけの同速だった
3. **「アーム内比較は対称汚染のため有効」→ 訂正**: 汚染への曝露は対称でも影響は
   非対称だった (goal は 3-10x 膨張、mission は約 1.5x 膨張)。v1 のアーム内比較も
   無効。7/21-7/22 の全 run (tail-v2 / openworld-v1 / discriminating-v1) の
   アーム間結論は採用しない
4. **品質同点は維持**: 全 10 records が marker 1.0 / forbidden 0 / quality 5.0。
   品質のアーム差はクリーン条件でも存在しない
5. **#266 (Complex=standard 2名) の効果を初観測**: 5 mission 中 3 tasks が
   standard tier (2名) で走行し、v1 比で mission 実行時間は短縮
   (7.5-13.7 min vs v1 9.3-21.1 min)。ただし goal の高速化幅の方が大きい

### 確定した結論 (クリーン条件、tail-v1 と整合)

- 品質 (marker recall): **同点** — sonnet-5 では全 cohort・全条件で一貫
- 速度・コスト: mission は **5.4x 時間 / 4.9x コスト** (tail-v1 の 5.8x/7.4x から
  微改善。#266 の寄与を含むが N=5 で寄与分離は不可)
- 完走信頼性: クリーン条件では **差なし** (両 arm 5/5)
- mission の価値は従来結論どおり「completion gate + テール保険 + 不可逆
  ガバナンス + budget guard + resume」であり、平均品質・速度では goal に勝てない

危険な解釈:

> クリーン条件でも mission に価値がない。

これは unsupported です。本 cohort は単発完結の閉世界タスクであり、mission の
価値領域 (multi-iter テール改善・不可逆操作ゲート・セッション跨ぎ) を測定して
いません。実運用 451 mission のテール実績は本 run と矛盾しません。

## Discriminating cohort adoption run (discriminating-v1)

Status: 2026-07-23 JST に実行。#262 の採用判定 run。smoke (`disc-config-sprawl`
paired、run id `2026-07-23-discriminating-smoke`) → 本 run (5 tasks x 2 arms、
run id `2026-07-23-discriminating-v1`) の 2 段階。starting commit は #261 遵守
ガード + #262 cohort マージ後の `bbd7602`。両 arm とも PATH shim で
`--model claude-sonnet-5` を注入し、`--max-budget-usd 10` /
`--mission-budget-minutes 30` / timeout 2400s を両 arm 同一条件で適用した。

### Smoke gate 結果

| Gate | 結果 |
|---|---|
| mission_iterations >= 2 | **達成** (iter=2 発生。fail-first 設計が機能) |
| critic_has_new_scope 記録 | **達成** (False が記録。#258 配線 / #240-#241 経路の実運用初観測) |
| mission-loop 遵守 | 達成 (未初期化 record 0) |
| marker < 1.0 の存在 | 未達 (両 arm marker 1.0) |

smoke の mission は iter2 で 30.45 min / USD 9.43 (goal 7.9 min / USD 2.63)。
iter2 発火時の mission は goal の約 3.9x 遅いという新実測を得た。

### 本 run task-level result

| Task | Arm | Status | Mission iter / passes | Marker | Cost | Elapsed |
|---|---|---|---|---:|---:|---:|
| `disc-config-sprawl` | goal | completed | — | 1.00 | USD 2.98 | 10.70 min |
| `disc-config-sprawl` | mission | completed | 1 / true | 1.00 | USD 8.37 | 13.53 min |
| `disc-release-ledger` | goal | **blocked (max_budget_usd)** | — | — | USD 10.05 | 24.89 min |
| `disc-release-ledger` | mission | completed | 1 / false (budget graceful halt) | 1.00 | USD 9.35 | 21.08 min |
| `disc-contract-drift` | goal | **blocked (max_budget_usd)** | — | — | USD 10.10 | 26.49 min |
| `disc-contract-drift` | mission | completed | 1 / true | 1.00 | USD 5.54 | 17.77 min |
| `disc-metrics-reconcile` | goal | completed | — | 1.00 | USD 1.69 | 5.28 min |
| `disc-metrics-reconcile` | mission | completed | 1 / true | 1.00 | USD 9.20 | 17.06 min |
| `disc-policy-exceptions` | goal | completed | — | 1.00 | USD 8.01 | 21.15 min |
| `disc-policy-exceptions` | mission | completed | 1 / false (scoring 未完) | 1.00 | USD 5.78 | 9.28 min |

### Aggregate (summary より)

| Metric | goal | mission | 解釈 |
|---|---:|---:|---|
| Completion rate | 3 / 5 | **5 / 5** | 同一予算 USD 10 で goal は 2 tasks を完走できず予算全損 (USD 20.15)。mission は全完走 |
| Comparable records | 3 | 5 | blocked は比較集計から除外 |
| Comparable avg quality / marker | 5.0 / 1.0 | 5.0 / 1.0 | 完走同士は品質同点 (marker 天井継続、forbidden hit 0) |
| Comparable avg elapsed | 12.38 min | 15.74 min | 有効 3 ペアの合計時間比は mission 1.07x (config 1.26x / metrics 3.23x / policy 0.44x) |
| Comparable cost mean | USD 4.22 | USD 7.65 | 完走同士では mission が高コスト |
| Cost total (全損込み) | USD 32.82 | USD 38.25 | goal の全損 USD 20.15 を含めると総額差は縮小 |

### 採用判定 (runbook Step 3 ゲート)

1. **測定妥当性: 達成** — `mission_loop_not_initialized` 0 件
2. **判別力 (marker 分散 != 0): 未達** — 完走 record は全て marker 1.0。ただし
   cohort は「予算制約下の完走率」という別軸で初めて arm 差を生んだ
   (goal 3/5 vs mission 5/5)
3. **iter>=2 の実運用観測: smoke で達成** — 本 run は全 mission iter1
   (レビュー結果のばらつきにより fail-first は確率的)
4. **品質判定: marker recall では同点確定** — 「品質>goal」は本 cohort でも
   実証されず。一方、同一予算での完走信頼性は mission 5/5 vs goal 3/5
5. **速度判定: 達成** — comparable 3 ペア合計 1.07x (事前宣言の 1.5x 帯内)

### 確定した位置づけ (2026-07-23)

- **速度≈goal**: 達成 (iter1 完結時)。iter2 発火時は約 3.9x に劣化する
- **品質>goal**: marker recall では非実証 (sonnet-5 では両 arm とも天井)
- **新規優位軸**: 予算制約下の完走信頼性。goal は網羅要求の強い task で予算を
  使い切り全損する一方、mission は budget guard (#238) で成果物を確定して
  graceful halt する。「量が多く網羅要求が強い task ほど mission が有利」

危険な解釈:

> `/mission` は `/goal` より高品質。

これは unsupported です。完走同士の品質は同点であり、優位は completion
reliability (N=5、予算 USD 10 設定に依存) に限られます。

> goal の予算切れは goal が劣っている証拠。

これは部分的にしか supported されません。`/goal` は budget pressure シグナルを
持たないため予算内で成果物を確定する機構がなく、この差は「構造の有無」に
起因します。予算を USD 20 に上げれば goal も完走する可能性があります。

## Openworld cohort calibration run (openworld-v1)

Status: 2026-07-22 JST に実行。`tasks.openworld.json` cohort（open-world finding 発見
3 tasks、#251）の初回 paired 較正 run。starting commit は #239/#240/#241 + #258 配線
マージ後の `7f3118d`。両 arm とも PATH shim で `--model claude-sonnet-5` を注入し
（各 artifact の `modelUsage` で主モデル sonnet-5 を確認。goal arm は CLI 補助モデル
haiku-4.5、mission arm は fork 先 sonnet-4.6 の併用を記録）、`--max-budget-usd 8` /
`--mission-budget-minutes 25`（#238）を適用した。

| Item | Value | Evidence |
|---|---:|---|
| Run id | `2026-07-22-claude-goal-vs-mission-openworld-v1` | `results/2026-07-22-claude-goal-vs-mission-openworld-v1.jsonl` |
| Starting commit | `7f3118d` | #258 配線マージ後の main |
| Expected / written records | 6 / 6 | 3 tasks x 2 arms、blocked 0 |
| model_id | `claude-sonnet-5` | 全 6 records（shim 注入 + modelUsage 確認） |
| Total cost (名目) | USD 22.41 | goal 9.16 / mission 13.25 |

Task-level result:

| Task | Arm | Mission loop | Iter | Passes | Quality | Marker | Cost | Elapsed |
|---|---|---|---:|---|---:|---:|---:|---:|
| `openworld-constant-hunt` | goal | — | — | — | 5.00 | 1.00 | USD 2.95 | 11.80 min |
| `openworld-constant-hunt` | mission | **未初期化 (無効)** | — | — | 5.00 | 1.00 | USD 0.80 | 2.47 min |
| `openworld-contradiction-chain` | goal | — | — | — | 5.00 | 1.00 | USD 4.95 | 17.53 min |
| `openworld-contradiction-chain` | mission | full tier | 1 | false (agreement halt) | 5.00 | 1.00 | USD 7.37 | 13.01 min |
| `openworld-incremental-reveal` | goal | — | — | — | 5.00 | 1.00 | USD 1.26 | 4.84 min |
| `openworld-incremental-reveal` | mission | full tier | 1 | true | 5.00 | 1.00 | USD 5.08 | 16.51 min |

較正 run としての観測 (N=3、採用判定には N≥10 が必要):

1. **全損ゼロ**: blocked 0 / 6。#238 の budget guard は発動条件に達しず、graceful halt
   (contradiction-chain の agreement gate halt) でも成果物と validator pass を維持した。
   fable-5 で観測した「予算切れ・成果物ゼロ」構造は本較正では再現していない。
2. **品質は両 arm とも天井飽和**: 全 records が quality 5.0 / marker 1.0 / 分散 0。
   openworld cohort は sonnet-5 に対して判別力を失っており、「品質>goal」の実証には
   より難度の高い discriminating cohort が必要。
3. **mission-arm の prompt 遵守失敗を 1 件検出**: constant-hunt の mission record は
   `.mission-state` が存在せず、mission ループを初期化しないまま素で回答していた
   (2.47 min / USD 0.80)。この record は mission arm の速度・コスト集計に含めては
   ならない (aggregate の mission 平均 10.66 min はこの無効 record で希釈されている)。
4. **有効な paired 比較は 2 組のみ**: contradiction-chain は mission 0.74x time /
   1.49x cost (mission が速い)、incremental-reveal は mission 3.41x time / 4.03x cost。
   tail-v1 の一様な 3-6x 劣位から改善傾向はあるが、N=2 で断定不可。
5. **#240/#241 の diff-review 経路は未発火**: 全 mission run が iter1 で終了したため、
   `critic_has_new_scope` / bounded context / reviewer 削減の実運用観測はゼロ。
   iter≥2 を強制する fail-first task 設計が別途必要。

危険な解釈:

> `/mission` は `/goal` と同速度・同品質になった。

これは unsupported です。aggregate 平均の速度同等は無効 record による希釈であり、
品質同点は cohort 飽和による天井解釈です。

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
results/2026-07-21-claude-goal-vs-mission-tail-v2.jsonl
results/2026-07-21-claude-goal-vs-mission-tail-v2-summary.json
results/2026-07-21-claude-goal-vs-mission-tail-v2-retry.jsonl
results/2026-07-21-claude-goal-vs-mission-tail-v2-retry-summary.json
results/2026-07-22-fable5-tail-smoke.jsonl
results/2026-07-22-fable5-tail-smoke-summary.json
results/2026-07-22-fable5-tail-full.jsonl
results/2026-07-22-fable5-tail-full-summary.json
results/2026-07-22-claude-goal-vs-mission-openworld-v1.jsonl
results/2026-07-22-claude-goal-vs-mission-openworld-v1-summary.json
artifacts/2026-07-22-claude-goal-vs-mission-openworld-v1/
results/2026-07-23-discriminating-smoke.jsonl
results/2026-07-23-discriminating-smoke-summary.json
artifacts/2026-07-23-discriminating-smoke/
results/2026-07-23-discriminating-v1.jsonl
results/2026-07-23-discriminating-v1-summary.json
artifacts/2026-07-23-discriminating-v1/
results/2026-07-23-discriminating-v2.jsonl
results/2026-07-23-discriminating-v2-summary.json
artifacts/2026-07-23-discriminating-v2/
results/2026-08-02-portfolio-v1.jsonl
results/2026-08-02-portfolio-v1-summary.json
artifacts/2026-08-02-portfolio-v1/
results/2026-08-02-discriminating-v3.jsonl
results/2026-08-02-discriminating-v3-summary.json
artifacts/2026-08-02-discriminating-v3/
results/2026-08-02-portfolio-v2.jsonl
results/2026-08-02-portfolio-v2-summary.json
artifacts/2026-08-02-portfolio-v2/
results/2026-08-02-portfolio-v3.jsonl
results/2026-08-02-portfolio-v3-summary.json
artifacts/2026-08-02-portfolio-v3/
results/2026-08-02-portfolio-v4.jsonl
results/2026-08-02-portfolio-v4-summary.json
artifacts/2026-08-02-portfolio-v4/
```
