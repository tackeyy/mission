"""#565: variance statistics — p50/p90/stdev/repeats_observed/statistical_confidence.

Contract under test:
- Per-arm: elapsed_minutes_p50/p90, cost_usd_p50/p90, marker_score_p50,
  elapsed_minutes_stdev, cost_usd_stdev, marker_score_stdev.
- Top-level: repeats_observed (minimum cell count), statistical_confidence.
- summary_warnings() emits n=1 warning when repeats_observed <= 1.
- Both the saturation warning and the n=1 warning can appear simultaneously.
- Zero records, all-identical values, and single records do not raise.
"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH = REPO_ROOT / "benchmarks" / "mission-vs-goal"
RESULTS_DIR = BENCH / "results"


def _load(name: str):
    path = BENCH / name
    spec = importlib.util.spec_from_file_location(name.removesuffix(".py"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load("run_claude_goal_vs_mission.py")

TASKS_PATH = BENCH / "tasks.tail.json"


def _record(
    arm: str,
    *,
    marker: float | None = 0.8,
    task_id: str = "t1",
    elapsed: float = 10.0,
    cost: float = 1.0,
    run_index: int = 0,
) -> dict:
    """Minimal record accepted by summarize()."""
    return {
        "arm": arm,
        "run_status": "completed",
        "comparable_attempt": True,
        "completion": True,
        "validator_pass": True,
        "human_quality_score": 5.0,
        "intervention_count": 0,
        "evidence_completeness": 5.0,
        "quality_marker_score": marker,
        "elapsed_minutes": elapsed,
        "total_cost_usd": cost,
        "task_id": task_id,
        "run_index": run_index,
        "permission_mode_degraded": False,
        "mission_evidence_only": False,
        "failure_kind": None,
        "mission_routed": False,
        "diff_review_observations": None,
        "mission_iterations": None,
        "mission_passes": None,
    }


def _summarize(records: list[dict], tasks: list[dict] | None = None) -> dict:
    if tasks is None:
        task_ids = list(dict.fromkeys(r.get("task_id", "t1") for r in records)) or ["t1"]
        tasks = [{"id": tid} for tid in task_ids]
    return MODULE.summarize(records, tasks, "run-x", "abc1234", TASKS_PATH)


# ===== repeats_observed =====


def test_repeats_observed_single_record():
    """Single record per cell -> repeats_observed == 1."""
    records = [
        _record("claude_code_goal_command", task_id="t1"),
        _record("mission", task_id="t1"),
    ]
    summary = _summarize(records)
    assert summary["repeats_observed"] == 1


def test_repeats_observed_three_repeats():
    """3 records per cell -> repeats_observed == 3."""
    records = []
    for i in range(3):
        records.append(_record("claude_code_goal_command", task_id="t1", run_index=i))
        records.append(_record("mission", task_id="t1", run_index=i))
    summary = _summarize(records, tasks=[{"id": "t1"}])
    assert summary["repeats_observed"] == 3


def test_repeats_observed_zero_records():
    """No records -> repeats_observed == 0."""
    summary = _summarize([])
    assert summary["repeats_observed"] == 0


def test_repeats_observed_disagreeing_cells_reports_minimum():
    """When cells have different repeat counts, report the minimum.

    This is the conservative choice: statistical guarantees hold only for the
    least-covered cell. A run where task t1 ran 3 times but t2 ran only 1 time
    should be reported as repeats_observed=1, not 3.
    """
    records = [
        # t1: 3 repeats in mission arm
        _record("mission", task_id="t1", run_index=0, elapsed=10.0),
        _record("mission", task_id="t1", run_index=1, elapsed=12.0),
        _record("mission", task_id="t1", run_index=2, elapsed=14.0),
        # t2: 1 repeat in mission arm
        _record("mission", task_id="t2", run_index=0, elapsed=8.0),
        # goal arm: 1 repeat for each task
        _record("claude_code_goal_command", task_id="t1"),
        _record("claude_code_goal_command", task_id="t2"),
    ]
    summary = _summarize(records, tasks=[{"id": "t1"}, {"id": "t2"}])
    # Minimum cell count across all (arm, task_id) cells is 1 (t2 for mission,
    # and both tasks for goal arm each have 1).
    assert summary["repeats_observed"] == 1


# ===== statistical_confidence =====


def test_statistical_confidence_single_sample():
    """repeats_observed=1 -> statistical_confidence='single-sample'."""
    records = [
        _record("claude_code_goal_command", task_id="t1"),
        _record("mission", task_id="t1"),
    ]
    summary = _summarize(records)
    assert summary["statistical_confidence"] == "single-sample"


def test_statistical_confidence_low_two_repeats():
    """repeats_observed=2 -> statistical_confidence='low'."""
    records = []
    for i in range(2):
        records.append(_record("claude_code_goal_command", task_id="t1", run_index=i))
        records.append(_record("mission", task_id="t1", run_index=i))
    summary = _summarize(records, tasks=[{"id": "t1"}])
    assert summary["repeats_observed"] == 2
    assert summary["statistical_confidence"] == "low"


def test_statistical_confidence_adequate_three_repeats():
    """repeats_observed=3 -> statistical_confidence='adequate'."""
    records = []
    for i in range(3):
        records.append(_record("claude_code_goal_command", task_id="t1", run_index=i))
        records.append(_record("mission", task_id="t1", run_index=i))
    summary = _summarize(records, tasks=[{"id": "t1"}])
    assert summary["repeats_observed"] == 3
    assert summary["statistical_confidence"] == "adequate"


def test_statistical_confidence_zero_records():
    """Zero records -> repeats_observed=0, statistical_confidence='single-sample'."""
    summary = _summarize([])
    assert summary["repeats_observed"] == 0
    assert summary["statistical_confidence"] == "single-sample"


# ===== per-arm percentiles and stdev (3 repeats, varied values) =====


def _three_repeat_records() -> list[dict]:
    """3 repetitions per cell with varied elapsed/cost/marker values."""
    elapsed_vals = [10.0, 20.0, 30.0]
    cost_vals = [1.0, 2.0, 3.0]
    marker_vals = [0.5, 0.7, 0.9]
    records = []
    for i, (el, co, ma) in enumerate(zip(elapsed_vals, cost_vals, marker_vals)):
        records.append(
            _record("mission", task_id="t1", run_index=i, elapsed=el, cost=co, marker=ma)
        )
        # goal arm: uniform values to keep it simple
        records.append(
            _record("claude_code_goal_command", task_id="t1", run_index=i, elapsed=5.0, cost=0.5, marker=0.6)
        )
    return records


def test_three_repeats_elapsed_p50():
    """Sorted [10, 20, 30]: nearest-rank p50 = ceil(0.5*3)=2nd = 20."""
    records = _three_repeat_records()
    summary = _summarize(records, tasks=[{"id": "t1"}])
    mission = summary["arms"]["mission"]
    assert mission["elapsed_minutes_p50"] == pytest.approx(20.0, abs=1e-3)


def test_three_repeats_elapsed_p90():
    """Sorted [10, 20, 30]: nearest-rank p90 = ceil(0.9*3)=3rd = 30."""
    records = _three_repeat_records()
    summary = _summarize(records, tasks=[{"id": "t1"}])
    mission = summary["arms"]["mission"]
    assert mission["elapsed_minutes_p90"] == pytest.approx(30.0, abs=1e-3)


def test_three_repeats_cost_p50():
    """Sorted [1, 2, 3]: nearest-rank p50 = 2nd = 2."""
    records = _three_repeat_records()
    summary = _summarize(records, tasks=[{"id": "t1"}])
    mission = summary["arms"]["mission"]
    assert mission["cost_usd_p50"] == pytest.approx(2.0, abs=1e-3)


def test_three_repeats_cost_p90():
    """Sorted [1, 2, 3]: nearest-rank p90 = 3rd = 3."""
    records = _three_repeat_records()
    summary = _summarize(records, tasks=[{"id": "t1"}])
    mission = summary["arms"]["mission"]
    assert mission["cost_usd_p90"] == pytest.approx(3.0, abs=1e-3)


def test_three_repeats_marker_p50():
    """Sorted [0.5, 0.7, 0.9]: nearest-rank p50 = 2nd = 0.7."""
    records = _three_repeat_records()
    summary = _summarize(records, tasks=[{"id": "t1"}])
    mission = summary["arms"]["mission"]
    assert mission["marker_score_p50"] == pytest.approx(0.7, abs=1e-3)


def test_three_repeats_elapsed_stdev():
    """Sample stdev of [10, 20, 30] = 10.0."""
    records = _three_repeat_records()
    summary = _summarize(records, tasks=[{"id": "t1"}])
    mission = summary["arms"]["mission"]
    assert mission["elapsed_minutes_stdev"] == pytest.approx(10.0, abs=1e-3)


def test_three_repeats_cost_stdev():
    """Sample stdev of [1, 2, 3] = 1.0."""
    records = _three_repeat_records()
    summary = _summarize(records, tasks=[{"id": "t1"}])
    mission = summary["arms"]["mission"]
    assert mission["cost_usd_stdev"] == pytest.approx(1.0, abs=1e-3)


def test_three_repeats_marker_stdev():
    """Sample stdev of [0.5, 0.7, 0.9] ≈ 0.2."""
    records = _three_repeat_records()
    summary = _summarize(records, tasks=[{"id": "t1"}])
    mission = summary["arms"]["mission"]
    assert mission["marker_score_stdev"] == pytest.approx(0.2, abs=1e-3)


# ===== n=1: percentiles return the single value, stdev=null =====


def test_n1_percentiles_return_single_value():
    """n=1 record: all percentiles return that record's value."""
    records = [
        _record("mission", task_id="t1", elapsed=15.0, cost=1.5, marker=0.75),
        _record("claude_code_goal_command", task_id="t1"),
    ]
    summary = _summarize(records)
    mission = summary["arms"]["mission"]
    assert mission["elapsed_minutes_p50"] == pytest.approx(15.0, abs=1e-3)
    assert mission["elapsed_minutes_p90"] == pytest.approx(15.0, abs=1e-3)
    assert mission["cost_usd_p50"] == pytest.approx(1.5, abs=1e-3)
    assert mission["cost_usd_p90"] == pytest.approx(1.5, abs=1e-3)
    assert mission["marker_score_p50"] == pytest.approx(0.75, abs=1e-3)


def test_n1_stdev_is_null():
    """n=1 record: all stdev fields are None."""
    records = [
        _record("mission", task_id="t1"),
        _record("claude_code_goal_command", task_id="t1"),
    ]
    summary = _summarize(records)
    mission = summary["arms"]["mission"]
    assert mission["elapsed_minutes_stdev"] is None
    assert mission["cost_usd_stdev"] is None
    assert mission["marker_score_stdev"] is None


# ===== zero records does not raise =====


def test_zero_records_no_exception():
    """Zero records: no exception; per-arm percentiles and stdev are None."""
    summary = _summarize([])
    for arm_data in summary["arms"].values():
        assert arm_data["elapsed_minutes_p50"] is None
        assert arm_data["elapsed_minutes_stdev"] is None
        assert arm_data["cost_usd_p50"] is None
        assert arm_data["marker_score_p50"] is None


# ===== all-identical values: stdev=0, not None =====


def test_all_identical_values_stdev_zero():
    """n>=2 with identical values: stdev is 0.0, not None."""
    records = []
    for i in range(3):
        records.append(
            _record("mission", task_id="t1", run_index=i, elapsed=10.0, cost=1.0, marker=0.8)
        )
        records.append(
            _record("claude_code_goal_command", task_id="t1", run_index=i, elapsed=10.0, cost=1.0, marker=0.8)
        )
    summary = _summarize(records, tasks=[{"id": "t1"}])
    mission = summary["arms"]["mission"]
    assert mission["elapsed_minutes_stdev"] == pytest.approx(0.0, abs=1e-9)
    assert mission["cost_usd_stdev"] == pytest.approx(0.0, abs=1e-9)
    assert mission["marker_score_stdev"] == pytest.approx(0.0, abs=1e-9)


# ===== summary_warnings: n=1 warning =====


def test_n1_warning_present_when_single_sample():
    """repeats_observed=1 -> n=1 warning in summary_warnings."""
    records = [
        _record("claude_code_goal_command", task_id="t1", marker=0.6),
        _record("mission", task_id="t1", marker=0.8),
    ]
    summary = _summarize(records)
    assert summary["statistical_confidence"] == "single-sample"
    warnings = MODULE.summary_warnings(summary)
    text = "\n".join(warnings)
    assert "single-sample" in text
    assert "0.51x" in text or "0.51" in text
    assert "1.97x" in text or "1.97" in text
    assert "--repeats 3" in text


def test_n3_no_n1_warning():
    """repeats_observed=3 -> no single-sample warning."""
    records = []
    for i in range(3):
        records.append(_record("claude_code_goal_command", task_id="t1", run_index=i, marker=0.6))
        records.append(_record("mission", task_id="t1", run_index=i, marker=0.8))
    summary = _summarize(records, tasks=[{"id": "t1"}])
    assert summary["statistical_confidence"] == "adequate"
    warnings = MODULE.summary_warnings(summary)
    text = "\n".join(warnings)
    assert "single-sample" not in text
    assert "--repeats 3" not in text


# ===== both warnings coexist =====


def test_both_saturation_and_n1_warnings_coexist():
    """Saturated (all 1.0) AND repeats_observed=1 -> both warnings present."""
    records = [
        _record("claude_code_goal_command", task_id="t1", marker=1.0),
        _record("mission", task_id="t1", marker=1.0),
    ]
    summary = _summarize(records)
    assert summary["marker_saturated"] is True
    assert summary["repeats_observed"] == 1
    warnings = MODULE.summary_warnings(summary)
    assert len(warnings) >= 2, f"Expected 2+ warnings, got {len(warnings)}: {warnings}"
    text = "\n".join(warnings)
    assert "saturated" in text.lower() or "1.0" in text
    assert "single-sample" in text


# ===== real-data smoke test =====


REAL_DATA_PATH = RESULTS_DIR / "2026-08-19-tail-v280-r2.jsonl"


@pytest.mark.skipif(
    not REAL_DATA_PATH.exists(),
    reason="real-data file not present in this checkout",
)
def test_real_data_tail_v280_r2_single_sample():
    """2026-08-19-tail-v280-r2 (repeats=1) -> statistical_confidence='single-sample'
    and the n=1 warning is emitted."""
    records = [
        json.loads(line)
        for line in REAL_DATA_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert records, "JSONL file is empty"

    task_ids_seen = list(dict.fromkeys(r["task_id"] for r in records if r.get("task_id")))
    tasks = [{"id": tid} for tid in task_ids_seen]
    summary = MODULE.summarize(
        records, tasks, "real-data-smoke-565", "abc1234", BENCH / "tasks.tail.json"
    )

    assert summary["statistical_confidence"] == "single-sample", (
        f"Expected 'single-sample', got: {summary.get('statistical_confidence')}"
    )
    assert summary["repeats_observed"] <= 1

    warnings = MODULE.summary_warnings(summary)
    text = "\n".join(warnings)
    assert "single-sample" in text, f"Expected n=1 warning, got: {warnings}"


# ---------------------------------------------------------------------------
# レビュー指摘 (Fable 5): p90 の欠落と n=2 の nearest-rank 挙動
# ---------------------------------------------------------------------------

def test_marker_score_p90_is_reported():
    """品質の裾は p50 より p90 の方が診断的なので必ず出す。"""
    records = [
        _record("mission", marker=0.2, task_id="t1"),
        _record("mission", marker=0.9, task_id="t2"),
        _record("mission", marker=1.0, task_id="t3"),
        _record("claude_code_goal_command", marker=0.5, task_id="t1"),
    ]
    summary = _summarize(records)
    arm = summary["arms"]["mission"]
    assert arm["marker_score_p50"] == 0.9
    assert arm["marker_score_p90"] == 1.0


def test_nearest_rank_p50_with_two_samples_is_the_lower_observation():
    """n=2 の p50 は中点ではなく下側の実測値になる (nearest-rank の帰結)。

    補間しない方針なので、報告される percentile は必ず実測値に対応する。
    statistical_confidence="low" の run で p50 を中央値として読むと
    下方バイアスがかかるため、挙動をテストで固定して文書化と整合させる。
    """
    records = [
        _record("mission", marker=0.4, task_id="t1"),
        _record("mission", marker=0.8, task_id="t1"),
    ]
    summary = _summarize(records)
    assert summary["arms"]["mission"]["marker_score_p50"] == 0.4
    assert summary["statistical_confidence"] == "low"
