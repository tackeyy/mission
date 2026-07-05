# refs/react-loop-details.md — ReAct ループ詳細リファレンス

SKILL.md 本体から外出しした Phase 2-6 ループの詳細。本体には「重要フィールド」「観点 D 運用」「終了判定ロジック」だけ残し、ここに **state.json スキーマフル** / **更新コマンド例** / **サブスキル呼び出し詳細** を集約する。

参照タイミング:
- state.json のフィールド名・型を厳密に確認したい
- サブスキル呼び出しのフル例 (観点 D 含む) を引きたい

---

## state.json スキーマ全体

`.mission-state/state.json` を作成・更新する。**Stop hook が参照するため、フィールド名は厳守する**。

```json
{
  "mission": "<構造化ミッション>",
  "subtasks": ["...", "..."],
  "complexity": "Standard",
  "reviewer_count": 2,
  "max_iter": null,
  "threshold": 4.0,
  "iteration": 0,
  "phase": "executing",
  "score_history": [
    { "iteration": 1, "composite": 3.5, "min_item": 3.0, "items": {}, "review_agreement": 4.0, "agreement_detail": {} }
  ],
  "stagnation_count": 0,
  "// stagnation_count": "push-score が自動更新する (改善幅 < 0.1 で +1、>= 0.1 で 0)。手動 set 禁止",
  "decisions": [],
  "loop_active": true,
  "passes": false,
  "halt_reason": "",
  "assumptions_path": ".mission-state/sessions/<sid>-assumptions.md",
  "// 自動付与": "schema_version/project_root/pid/hostname/session_id/agent/created_at_session は stamp_metadata、mission_id は cmd_init が直接セット",
  "started_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

`max_iter: null` は「上限なし」を表す state 値。CLI で `--max-iter` を省略した場合は `SKILL.md` の既定どおり 3 が保存され、`--max-iter 0` のときだけ null になる。

各イテレーションごとに `iteration++` と `updated_at` を更新。クラッシュ時は state.json から復旧可能。

## state.json の更新

更新は必ず `mission-state.py` 経由 (`init`/`set`/`push-score`/`mark-passes`/`mark-halt`)。jq・Python heredoc での直接書き換えは schema 不整合・threshold gate 迂回の温床のため禁止 (詳細 `refs/state-management.md`)。

## サブスキル呼び出しのフル例 (Skill tool)

```
Skill(skill="mission-planner",  args="<構造化ミッション> + 制約 + state.json要約")
Skill(skill="mission-executor", args="<計画ステップN>")
# 並列レビュー (Reviewer数に応じて)
Skill(skill="mission-reviewer", args="観点A: ミッション達成度 — <成果物要約>")
Skill(skill="mission-reviewer", args="観点B: 正確性・論理整合性")
Skill(skill="mission-reviewer", args="観点C: 実用性・抜け漏れ")
# オプション: 観点D (Complex/Critical のみ推奨。採点対象外でフィードバックのみ)
Skill(skill="mission-reviewer", args="観点D: 計画指示明瞭度 — Executor の指示明瞭度フィードバックを評価")
# 各 Reviewer の末尾にある mission-review/1 JSON を orchestrator が /tmp/mission-reviewer-iter-N-<mission8>-<slot>.json に保存する。
# 保存後、aggregate-reviews が reviewer JSON を決定論集計して push-score 互換 JSON を生成する。標準フローで mission-scorer は spawn しない。
# Bash(command="python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py aggregate-reviews --iteration N --input /tmp/mission-reviewer-iter-N-<mission8>-a.json --input /tmp/mission-reviewer-iter-N-<mission8>-b.json --out /tmp/mission-scorer-iter-N-<mission8>.json --json")
# aggregate-reviews の出力を受け取ったら orchestrator が必ず push-score を呼ぶ (score_history 記録)。
# Bash(command="python3 ${CLAUDE_PLUGIN_ROOT}/skills/mission/bin/mission-state.py push-score --iteration N --scoring-json /tmp/mission-scorer-iter-N-<mission8>.json")
Skill(skill="mission-critic",   args="スコア結果 + 成果物 + 観点D フィードバック → 改善案 + Planner 申し送り")
```

fallback 条件は `skills/mission-scorer/SKILL.md` の「Fallback 発動条件」を正とする。fallback 後も平均・合意度・合否判定は必ず `aggregate-reviews` と `push-score --scoring-json` が担う。

**並列実行 (P4 強化)**: Phase 4 のレビュー呼び出しは、Claude Code では**必ず 1 つの assistant メッセージ内に複数 Skill 呼び出しを並べる**。別メッセージに分割しても観測上は非同期並列になるが (実測 2026-06-12、gotchas §1)、挙動保証がないため単一メッセージに統一する。watchdog: 制御が戻った時点で 15 分超未返の Reviewer は待たずに再 spawn (gotchas §1)。Codex では順次実行で代替。

**観点D の運用 (EPT 由来)**: 観点D は Reviewer に **採点させず**、Executor の指示明瞭度フィードバックを「次イテレーション Planner への改善案」に変換させる。Critic はこれを「Planner 申し送り」枠で受け取り、次イテレーションの planner 呼び出し args に含める。Simple/Standard では省略可（Reviewer 数増加コストと釣り合わない）。
