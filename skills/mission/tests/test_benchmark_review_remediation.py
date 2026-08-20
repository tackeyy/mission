"""#585: 独立レビュー (Fable 5) で検出した merge 済みコードの欠陥に対する回帰テスト。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "mission-vs-goal"


def _load():
    spec = importlib.util.spec_from_file_location(
        "run_claude_goal_vs_mission", BENCH / "run_claude_goal_vs_mission.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()
TASKS_PATH = BENCH / "tasks.tail.json"


def _record(arm, *, marker=1.0, task_id="t1"):
    return {
        "arm": arm, "run_status": "completed", "comparable_attempt": True,
        "completion": True, "validator_pass": True, "human_quality_score": 5.0,
        "intervention_count": 0, "evidence_completeness": 5.0,
        "quality_marker_score": marker, "elapsed_minutes": 10.0,
        "total_cost_usd": 1.0, "task_id": task_id,
        "permission_mode_degraded": False, "mission_iterations": None,
        "mission_review_tier": None, "failure_kind": None, "mission_routed": False,
        "mission_evidence_only": False, "diff_review_observations": None,
        "measurement_observations": MODULE.unavailable_measurement_observations(),
    }


def _summarize(records, **kw):
    return MODULE.summarize(
        records=records, tasks=[], run_id="r", starting_commit="c",
        tasks_path=TASKS_PATH, **kw
    )


GOAL = "claude_code_goal_command"
MISSION = "mission"


# --- A: タスク定義由来の threshold が実効値として記録される -------------------

def test_task_level_threshold_is_recorded_as_effective_value():
    """どの pass gate で測ったかが事後に復元できなければ run を再解釈できない。"""
    task = {"id": "t", "category": "c", "prompt": "p", "validator": "v",
            "mission_threshold": 4.7}
    prompt = MODULE.build_prompt(task, "mission", "out.md")
    assert "--threshold 4.7" in prompt

    summary = _summarize([], tasks_override=None) if False else MODULE.summarize(
        records=[], tasks=[task], run_id="r", starting_commit="c",
        tasks_path=TASKS_PATH,
    )
    assert summary["mission_threshold"] == 4.7


def test_cli_threshold_still_wins_over_task_level_in_summary():
    task = {"id": "t", "category": "c", "prompt": "p", "validator": "v",
            "mission_threshold": 4.7}
    summary = MODULE.summarize(
        records=[], tasks=[task], run_id="r", starting_commit="c",
        tasks_path=TASKS_PATH, mission_threshold=4.2,
    )
    assert summary["mission_threshold"] == 4.2


def test_summary_threshold_is_none_when_neither_source_sets_it():
    task = {"id": "t", "category": "c", "prompt": "p", "validator": "v"}
    summary = MODULE.summarize(
        records=[], tasks=[task], run_id="r", starting_commit="c",
        tasks_path=TASKS_PATH,
    )
    assert summary["mission_threshold"] is None


# --- B: process_quality の握りつぶし ------------------------------------------

def _write_reviews(tmp_path, payload):
    archive = tmp_path / ".mission-state" / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "iter-1-abc-reviews-1.json").write_text(
        payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
    )


def test_non_list_inputs_is_reported_not_silently_zero(tmp_path):
    """inputs が list でないのは収集失敗。0 件の正常な run と区別できねばならない。"""
    _write_reviews(tmp_path, {"inputs": {"not": "a list"}})
    pq, err = MODULE.extract_process_quality(tmp_path)
    assert pq is None
    assert err is not None and "inputs" in err


def test_non_dict_reviews_payload_records_a_reason(tmp_path):
    _write_reviews(tmp_path, "42")
    pq, err = MODULE.extract_process_quality(tmp_path)
    assert pq is None
    assert err is not None and "not_object" in err


# --- C: arm 感応度 -------------------------------------------------------------

def test_arm_sensitivity_zero_when_arms_score_identically_per_task():
    """指標が arm の違いに一切感応していない run を申告する。

    measurement_valid は true のまま（「差なし」は正当な結論であり、
    測定失敗として握りつぶしてはならない）。
    """
    records = [
        _record(GOAL, marker=0.5, task_id="t0"), _record(GOAL, marker=1.0, task_id="t1"),
        _record(MISSION, marker=0.5, task_id="t0"), _record(MISSION, marker=1.0, task_id="t1"),
    ]
    summary = _summarize(records)
    assert summary["measurement_valid"] is True
    assert summary["marker_score_delta_vs_goal"] == 0.0
    assert summary["marker_score_arm_sensitivity"] == 0
    assert any("arm" in w and "sensitiv" in w.lower() for w in MODULE.summary_warnings(summary))


def test_arm_sensitivity_counts_cells_where_arms_differ():
    records = [
        _record(GOAL, marker=0.5, task_id="t0"), _record(GOAL, marker=1.0, task_id="t1"),
        _record(MISSION, marker=0.9, task_id="t0"), _record(MISSION, marker=1.0, task_id="t1"),
    ]
    summary = _summarize(records)
    assert summary["marker_score_arm_sensitivity"] == 1
    assert not any("sensitiv" in w.lower() for w in MODULE.summary_warnings(summary))


def test_arm_sensitivity_is_none_when_a_cell_pair_is_incomplete():
    """片 arm しか無いセルは比較できないので感応度を主張しない。"""
    records = [_record(GOAL, marker=0.5, task_id="t0")]
    summary = _summarize(records)
    assert summary["marker_score_arm_sensitivity"] is None
