"""#261: runner の mission-loop 遵守ガード.

openworld-v1 (2026-07-22) で mission arm が `.mission-state` 未初期化のまま素で
回答した無効 record が mission aggregate を希釈した実害への対策:

1. apply_mission_adherence_guard: mission arm で mission_state_note ==
   "mission_state_missing" のとき record 分類を run_status=failed /
   failure_kind=mission_loop_not_initialized / comparable_attempt=False に上書き
2. summarize: comparable 記録のみで計算する品質・速度・コストの
   comparable_* フィールドを追加 (既存フィールドは歴史的意味を維持)
"""
import importlib.util
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "mission-vs-goal"


def _load(name: str):
    path = BENCH / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load("run_claude_goal_vs_mission.py")

COMPLETED_STATUS = {
    "run_status": "completed",
    "blocked_reason": None,
    "failure_kind": None,
    "comparable_attempt": True,
}


# ===== 1. apply_mission_adherence_guard =====

def test_guard_invalidates_mission_record_without_state():
    """mission arm + state 不在 → failed / mission_loop_not_initialized / 非 comparable."""
    result = MODULE.apply_mission_adherence_guard(
        dict(COMPLETED_STATUS), arm="mission", mission_state_note="mission_state_missing")
    assert result["run_status"] == "failed"
    assert result["failure_kind"] == "mission_loop_not_initialized"
    assert result["comparable_attempt"] is False
    assert result["blocked_reason"] is None


def test_guard_keeps_goal_arm_untouched():
    """goal arm は state 検査の対象外."""
    result = MODULE.apply_mission_adherence_guard(
        dict(COMPLETED_STATUS), arm="claude_code_goal_command",
        mission_state_note="mission_state_missing")
    assert result == COMPLETED_STATUS


def test_guard_keeps_mission_with_state_untouched():
    """mission arm でも state があれば (note=None) 何もしない."""
    result = MODULE.apply_mission_adherence_guard(
        dict(COMPLETED_STATUS), arm="mission", mission_state_note=None)
    assert result == COMPLETED_STATUS


def test_guard_keeps_unreadable_state_untouched():
    """state 破損 (unreadable) はループ開始の証拠があるため無効化しない."""
    result = MODULE.apply_mission_adherence_guard(
        dict(COMPLETED_STATUS), arm="mission",
        mission_state_note="mission_state_unreadable:JSONDecodeError")
    assert result == COMPLETED_STATUS


def test_guard_does_not_downgrade_blocked():
    """既に blocked の record は blocked のまま (外的要因の分類を保持)."""
    blocked = {
        "run_status": "blocked",
        "blocked_reason": "api_usage_limit",
        "failure_kind": "api_usage_limit",
        "comparable_attempt": False,
    }
    result = MODULE.apply_mission_adherence_guard(
        dict(blocked), arm="mission", mission_state_note="mission_state_missing")
    assert result["run_status"] == "blocked"
    assert result["failure_kind"] == "api_usage_limit"


# ===== 2. summarize: comparable_* aggregate =====

def _record(arm, *, comparable=True, quality=5.0, elapsed=10.0, cost=2.0,
            marker=1.0, completion=True, validator=True):
    return {
        "arm": arm,
        "run_status": "completed" if comparable else "failed",
        "comparable_attempt": comparable,
        "completion": completion,
        "validator_pass": validator,
        "human_quality_score": quality,
        "intervention_count": 0,
        "evidence_completeness": quality,
        "quality_marker_score": marker,
        "elapsed_minutes": elapsed,
        "total_cost_usd": cost,
    }


def test_summarize_comparable_fields_exclude_invalid_records(tmp_path):
    """無効 record (comparable_attempt=False) は comparable_* 集計から除外される."""
    tasks_path = BENCH / "tasks.openworld.json"
    records = [
        _record("mission", comparable=True, quality=4.0, elapsed=15.0, cost=5.0),
        _record("mission", comparable=False, quality=5.0, elapsed=2.0, cost=0.5),
        _record("claude_code_goal_command", comparable=True, quality=4.0, elapsed=10.0, cost=2.0),
    ]
    tasks = [{"id": "t1"}]
    summary = MODULE.summarize(records, tasks, "rid", "abc1234", tasks_path)
    mission = summary["arms"]["mission"]
    # 無効 record の 2.0 分 / $0.5 が混ざらない
    assert mission["comparable_average_elapsed_minutes"] == 15.0
    assert mission["comparable_cost_usd_mean"] == 5.0
    assert mission["comparable_average_quality_score"] == 4.0
    # 既存フィールドは全 records の歴史的意味を維持 (希釈込み)
    assert mission["average_elapsed_minutes"] == 8.5


def test_summarize_comparable_fields_none_when_no_comparable(tmp_path):
    """comparable record ゼロなら comparable_* は None."""
    tasks_path = BENCH / "tasks.openworld.json"
    records = [_record("mission", comparable=False)]
    summary = MODULE.summarize(records, [{"id": "t1"}], "rid", "abc1234", tasks_path)
    mission = summary["arms"]["mission"]
    assert mission["comparable_average_elapsed_minutes"] is None
    assert mission["comparable_cost_usd_mean"] is None
    assert mission["comparable_average_quality_score"] is None
