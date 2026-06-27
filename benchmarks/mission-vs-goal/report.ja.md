# mission vs goal-only pilot report

Status: 2026-06-27 に controlled local Codex CLI pilot として計測済み。
追加で、2026-06-28 JST に Claude Code 公式 `/goal` smoke を試行し、
別セクションに分けて記録しています。

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
| Benchmark + doc consistency tests | 29 passed / 29 |
| Full mission test suite | 394 passed / 394 |
| JSON parse checks | 2 passed / 2 |
| Scoped whitespace check | passed |

使用した commands:

```bash
python3 benchmarks/mission-vs-goal/run_paired_pilot.py --starting-commit 0148f16 --timeout 900
python3 benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py --tasks-file benchmarks/mission-vs-goal/tasks.complex.json --run-id 2026-06-28-claude-goal-vs-mission-smoke-v2 --starting-commit 38cc7907e5e35fcd9fa23022a1fcf03f756df99b --limit-tasks 1 --timeout 300 --max-budget-usd 1.5 --mission-max-iter 1
python3 -m pytest skills/mission/tests/test_benchmark_package.py skills/mission/tests/test_doc_consistency.py -q
python3 -m pytest skills/mission/tests -q
python3 -m json.tool benchmarks/mission-vs-goal/tasks.json
python3 -m json.tool benchmarks/mission-vs-goal/tasks.complex.json
python3 -m json.tool benchmarks/mission-vs-goal/result.schema.json
python3 -m py_compile benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py
git diff --check -- README.md README.ja.md docs/LOOP_ENGINEERING.md benchmarks/mission-vs-goal/README.md benchmarks/mission-vs-goal/README.ja.md benchmarks/mission-vs-goal/report.md benchmarks/mission-vs-goal/report.ja.md benchmarks/mission-vs-goal/report-template.md benchmarks/mission-vs-goal/report-template.ja.md benchmarks/mission-vs-goal/complex-validation-plan.md benchmarks/mission-vs-goal/complex-validation-plan.ja.md benchmarks/mission-vs-goal/run_paired_pilot.py benchmarks/mission-vs-goal/run_claude_goal_vs_mission.py skills/mission/tests/test_benchmark_package.py
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
```
