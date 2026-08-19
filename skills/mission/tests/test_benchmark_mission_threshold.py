"""#566 Step 2: /mission へ pass threshold を渡す配線のテスト。

背景 (#566 の実測分析): mission の pass gate は
`open_high == 0 かつ composite >= threshold` だが、保全済み run state の
12 観測すべてで composite は threshold 4.0 を下回らず、**一度も binding して
いなかった**。実効的な hard blocker は open_high だけであり、反復発生率は
High finding の発生率と一致する。

ベンチ側で composite を binding にするには、/mission が既に公開している
`--threshold X` を渡せばよい (mission 本体の gate 仕様は変えない)。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "mission-vs-goal"


def _load():
    spec = importlib.util.spec_from_file_location(
        "run_claude_goal_vs_mission", BENCH / "run_claude_goal_vs_mission.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()
TASK = {
    "id": "t1",
    "category": "c",
    "prompt": "p",
    "validator": "v",
}


def _mission_prompt(task=None, **kwargs):
    return MODULE.build_prompt(task or TASK, "mission", "out.md", **kwargs)


def test_threshold_absent_by_default():
    """未指定なら /mission の既定 threshold に委ねる (フラグを足さない)。"""
    assert "--threshold" not in _mission_prompt()


def test_cli_threshold_is_passed_to_mission():
    assert "--threshold 4.5" in _mission_prompt(mission_threshold=4.5)


def test_task_level_threshold_is_used_when_cli_absent():
    task = dict(TASK, mission_threshold=4.7)
    assert "--threshold 4.7" in _mission_prompt(task)


def test_cli_threshold_overrides_task_level():
    """明示的な運用者指定が勝つ (mission_max_iter と同じ規約)。"""
    task = dict(TASK, mission_threshold=4.7)
    assert "--threshold 4.2" in _mission_prompt(task, mission_threshold=4.2)


def test_threshold_never_leaks_into_goal_arm():
    """goal arm は /goal であり threshold の概念を持たない。"""
    prompt = MODULE.build_prompt(TASK, "claude_code_goal_command", "out.md",
                                 mission_threshold=4.5)
    assert "--threshold" not in prompt


def test_threshold_coexists_with_max_iter_and_budget():
    prompt = _mission_prompt(mission_threshold=4.5, mission_max_iter=3,
                             mission_budget_minutes=12.0)
    assert "--max-iter 3" in prompt
    assert "--budget-minutes 12.0" in prompt
    assert "--threshold 4.5" in prompt


@pytest.mark.parametrize("bad", [0, -1.0, float("nan"), float("inf")])
def test_non_positive_or_non_finite_threshold_is_rejected(bad):
    """壊れた値を黙って /mission へ流さない。

    budget-minutes は 0.0 を mission 側の検証へ届ける方針だが、threshold は
    ベンチの測定条件そのものなので、誤指定に気づけないまま 30 セル回すのは
    高くつく。ランナー側で fail-fast させる。
    """
    with pytest.raises(ValueError):
        _mission_prompt(mission_threshold=bad)


def test_summarize_records_the_effective_threshold():
    """どの threshold で測ったかが summary に残らないと run を再解釈できない。"""
    summary = MODULE.summarize(
        records=[],
        tasks=[],
        run_id="r",
        starting_commit="c",
        tasks_path=BENCH / "tasks.tail.json",
        mission_threshold=4.5,
    )
    assert summary["mission_threshold"] == 4.5


def test_summarize_threshold_defaults_to_none():
    summary = MODULE.summarize(
        records=[],
        tasks=[],
        run_id="r",
        starting_commit="c",
        tasks_path=BENCH / "tasks.tail.json",
    )
    assert summary["mission_threshold"] is None
