"""Issue #262: discriminating cohort — 品質天井の解消 + iter>=2 強制.

openworld-v1 (2026-07-22) で全 records が marker 1.0 / 分散 0 となり cohort が
sonnet-5 に対して判別力を失った実害への対策。Contract under test:

1. `tasks.discriminating.json` は 5 tasks / answer-key 自己隠蔽 / prompt_rules を持つ
2. 判別力: 各 task は quality_markers >= 6 (recall が分布する) + forbidden_markers >= 1
3. fail-first: `fail_first: true` の task が 2 件以上あり、mission_max_iter >= 3
4. fixture 自己整合: 各 quality_marker は最低 1 pattern が当該 task の fixture 本文に
   実在する (答えの実在保証 = marker が fixture から発見可能)
5. N>=10 採用判定 runbook が存在しコマンドとゲート基準を含む
"""

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCHMARK_DIR = REPO_ROOT / "benchmarks" / "mission-vs-goal"
TASKS_PATH = BENCHMARK_DIR / "tasks.discriminating.json"


def _data() -> dict:
    return json.loads(TASKS_PATH.read_text(encoding="utf-8"))


def test_discriminating_cohort_structure():
    data = _data()
    assert data["benchmark"] == "mission-vs-goal-pilot"
    assert data["cohort"] == "discriminating"
    assert data["arms"] == ["claude_code_goal_command", "mission"]
    assert data["task_count"] == 5
    assert len(data["tasks"]) == 5
    assert len({t["id"] for t in data["tasks"]}) == 5
    assert "benchmarks/mission-vs-goal/tasks.discriminating.json" in data["hidden_paths"]
    assert data["prompt_rules"]
    required = {
        "id", "category", "difficulty", "prompt", "validator", "primary_metric",
        "hypothesis", "fixtures", "first_pass_failure_design",
        "quality_markers", "forbidden_markers", "markers_hidden",
    }
    for task in data["tasks"]:
        assert required <= task.keys(), f"{task.get('id')} missing keys"
        assert task["id"].startswith("disc-")
        assert task["markers_hidden"] is True
        assert task["mission_complexity"] in {"Complex", "Critical"}


def test_discrimination_marker_density():
    """天井飽和対策: 各 task は marker >= 6、decoy (forbidden) >= 1."""
    for task in _data()["tasks"]:
        assert len(task["quality_markers"]) >= 6, task["id"]
        assert len(task["forbidden_markers"]) >= 1, task["id"]


def test_fail_first_tasks_present():
    """iter>=2 強制: fail_first task が 2 件以上、mission_max_iter >= 3."""
    fail_first = [t for t in _data()["tasks"] if t.get("fail_first") is True]
    assert len(fail_first) >= 2
    for task in fail_first:
        assert task["mission_max_iter"] >= 3, task["id"]


def test_fixtures_exist_and_referenced():
    """全 fixture が実在し、prompt から参照されている."""
    for task in _data()["tasks"]:
        assert task["fixtures"], task["id"]
        for rel in task["fixtures"]:
            path = REPO_ROOT / rel
            assert path.is_file(), f"{task['id']}: fixture missing {rel}"
            assert rel in task["prompt"], f"{task['id']}: fixture not in prompt {rel}"


def test_quality_marker_patterns_discoverable_in_fixtures():
    """各 quality_marker は最低 1 pattern が fixture 本文に実在する (答えの実在保証)."""
    for task in _data()["tasks"]:
        corpus = "".join(
            (REPO_ROOT / rel).read_text(encoding="utf-8").lower()
            for rel in task["fixtures"]
        )
        for marker in task["quality_markers"]:
            patterns = [p.lower() for p in marker["patterns"]]
            assert any(p in corpus for p in patterns), (
                f"{task['id']}: marker '{marker['name']}' の pattern が fixture に無い"
            )


def test_no_trivially_short_patterns():
    """1 文字 pattern は偶然一致するため禁止."""
    for task in _data()["tasks"]:
        for group in (task["quality_markers"], task["forbidden_markers"]):
            for marker in group:
                for p in marker["patterns"]:
                    assert len(p) >= 2, f"{task['id']}: pattern too short: {p!r}"


def test_adoption_runbook_exists():
    """N>=10 採用判定 runbook がコマンドとゲート基準を含む."""
    runbook = BENCHMARK_DIR / "discriminating-cohort-runbook.ja.md"
    assert runbook.is_file()
    text = runbook.read_text(encoding="utf-8")
    assert "tasks.discriminating.json" in text
    assert "--repeats" in text
    assert "N≥10" in text or "N>=10" in text
    # 採用ゲート: 分散の解消と iter>=2 の観測を要求
    assert "marker_score_variance" in text
    assert "iteration" in text or "iter" in text
