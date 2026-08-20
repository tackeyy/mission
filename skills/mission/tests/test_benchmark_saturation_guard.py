"""#563: saturation guard — marker_saturated / measurement_valid / relative-score fields.

Contract under test:
- summarize() adds top-level marker_saturated / marker_saturation_detail /
  measurement_valid / marker_score_delta_vs_goal / marker_score_ratio_vs_goal.
- Per-arm: marker_score_min / marker_score_max / marker_score_distinct_values.
- summary_warnings() returns a non-empty list when measurement_valid is false.
- Real-data smoke: results/2026-08-19-tail-v280-r2.jsonl (all 1.0) yields
  measurement_valid=False.
"""

from __future__ import annotations

import importlib.util
import json
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

# Tasks path that exists in the repo so tasks_path.relative_to(REPO_ROOT) succeeds.
TASKS_PATH = BENCH / "tasks.tail.json"


def _record(arm: str, *, marker: float | None = 1.0, task_id: str = "t1") -> dict:
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
        "elapsed_minutes": 10.0,
        "total_cost_usd": 1.0,
        "task_id": task_id,
        # fields accessed by summarize for diff_review_measurement_gate
        "permission_mode_degraded": False,
        "mission_evidence_only": False,
        "failure_kind": None,
        "mission_routed": False,
        "diff_review_observations": None,
        "mission_iterations": None,
        "mission_passes": None,
    }


def _summarize(records: list[dict]) -> dict:
    tasks = [{"id": "t1"}]
    return MODULE.summarize(records, tasks, "run-x", "abc1234", TASKS_PATH)


# ===== marker_saturated / measurement_valid =====


def test_all_records_1_0_saturated_and_invalid():
    """All records at 1.0 -> saturated, measurement_valid false."""
    records = [
        _record("claude_code_goal_command", marker=1.0),
        _record("mission", marker=1.0),
    ]
    summary = _summarize(records)
    assert summary["marker_saturated"] is True
    assert summary["measurement_valid"] is False


def test_varied_scores_not_saturated():
    """Varied scores -> saturated false, measurement_valid true."""
    records = [
        _record("claude_code_goal_command", marker=0.5),
        _record("mission", marker=1.0),
    ]
    summary = _summarize(records)
    assert summary["marker_saturated"] is False
    assert summary["measurement_valid"] is True


def test_no_scored_records_invalid_not_saturated():
    """All-null markers: measurement_valid false, marker_saturated false."""
    records = [
        _record("claude_code_goal_command", marker=None),
        _record("mission", marker=None),
    ]
    summary = _summarize(records)
    assert summary["marker_saturated"] is False
    assert summary["measurement_valid"] is False


def test_empty_records_no_exception():
    """Zero records: no exception, measurement_valid false, marker_saturated false."""
    summary = _summarize([])
    assert summary["marker_saturated"] is False
    assert summary["measurement_valid"] is False


# ===== summary_warnings =====


def test_warnings_present_when_saturated():
    """summary_warnings returns non-empty list for saturated summary."""
    records = [
        _record("claude_code_goal_command", marker=1.0),
        _record("mission", marker=1.0),
    ]
    summary = _summarize(records)
    warnings = MODULE.summary_warnings(summary)
    assert len(warnings) > 0
    warning_text = "\n".join(warnings)
    assert "saturated" in warning_text.lower()


def test_warnings_empty_when_valid():
    """summary_warnings returns no saturation warning when measurement_valid is true."""
    records = [
        _record("claude_code_goal_command", marker=0.7),
        _record("mission", marker=0.9),
    ]
    summary = _summarize(records)
    warnings = MODULE.summary_warnings(summary)
    # No saturation-specific warning; a single-sample warning (#565) may appear
    # because these records have repeats_observed=1.
    text = "\n".join(warnings)
    assert "saturated" not in text.lower()
    assert "NOT valid" not in text


def test_warning_text_matches_issue_wording():
    """Warning text contains the key phrases from issue #557."""
    records = [
        _record("claude_code_goal_command", marker=1.0),
        _record("mission", marker=1.0),
    ]
    summary = _summarize(records)
    text = "\n".join(MODULE.summary_warnings(summary))
    assert "1.0" in text
    assert "Cost/time" in text or "cost" in text.lower()
    assert "NOT valid" in text or "not valid" in text.lower()


# ===== marker_score_delta_vs_goal / marker_score_ratio_vs_goal =====


def test_delta_and_ratio_goal_06_mission_08():
    """goal mean 0.6, mission mean 0.8 -> delta 0.2, ratio 1.3333."""
    records = [
        _record("claude_code_goal_command", marker=0.6, task_id="t1"),
        _record("mission", marker=0.8, task_id="t1"),
    ]
    summary = _summarize(records)
    assert summary["marker_score_delta_vs_goal"] == pytest.approx(0.2, abs=1e-4)


def test_ratio_null_when_goal_mean_zero():
    """goal mean 0 -> ratio is null, no ZeroDivisionError."""
    records = [
        _record("claude_code_goal_command", marker=0.0, task_id="t1"),
        _record("mission", marker=0.8, task_id="t1"),
    ]
    summary = _summarize(records)
    assert summary["marker_score_ratio_vs_goal"] is None


def test_delta_null_when_no_goal_records():
    """No goal arm records -> delta and ratio are null."""
    records = [_record("mission", marker=0.8, task_id="t1")]
    summary = _summarize(records)
    assert summary["marker_score_delta_vs_goal"] is None
    assert summary["marker_score_ratio_vs_goal"] is None


# ===== per-arm min / max / distinct_values =====


def test_per_arm_min_max_distinct_values_uniform():
    """Single score in arm -> min==max, distinct_values==1."""
    records = [
        _record("claude_code_goal_command", marker=0.75),
        _record("mission", marker=0.75),
    ]
    summary = _summarize(records)
    goal_arm = summary["arms"]["claude_code_goal_command"]
    assert goal_arm["marker_score_min"] == 0.75
    assert goal_arm["marker_score_max"] == 0.75
    assert goal_arm["marker_score_distinct_values"] == 1


def test_per_arm_distinct_values_multiple():
    """Multiple distinct scores in one arm -> distinct_values > 1."""
    records = [
        _record("claude_code_goal_command", marker=0.5, task_id="t1"),
        _record("claude_code_goal_command", marker=1.0, task_id="t2"),
        _record("mission", marker=0.8, task_id="t1"),
        _record("mission", marker=0.8, task_id="t2"),
    ]
    summary = _summarize(records)
    goal_arm = summary["arms"]["claude_code_goal_command"]
    assert goal_arm["marker_score_min"] == 0.5
    assert goal_arm["marker_score_max"] == 1.0
    assert goal_arm["marker_score_distinct_values"] == 2

    mission_arm = summary["arms"]["mission"]
    assert mission_arm["marker_score_distinct_values"] == 1


def test_per_arm_no_scored_records_distinct_zero():
    """No scored records in arm -> distinct_values == 0, min/max None."""
    records = [
        _record("claude_code_goal_command", marker=None),
        _record("mission", marker=0.8),
    ]
    summary = _summarize(records)
    goal_arm = summary["arms"]["claude_code_goal_command"]
    assert goal_arm["marker_score_min"] is None
    assert goal_arm["marker_score_max"] is None
    assert goal_arm["marker_score_distinct_values"] == 0


# ===== Real-data smoke test =====


REAL_DATA_PATH = RESULTS_DIR / "2026-08-19-tail-v280-r2.jsonl"


@pytest.mark.skipif(
    not REAL_DATA_PATH.exists(),
    reason="real-data file not present in this checkout",
)
def test_real_data_tail_v280_r2_measurement_invalid():
    """2026-08-19-tail-v280-r2 (all scores 1.0) -> measurement_valid is False."""
    records = [
        json.loads(line)
        for line in REAL_DATA_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert records, "JSONL file is empty"

    # Verify the real-data assumption: all scored records are 1.0
    scored = [r for r in records if r.get("quality_marker_score") is not None]
    assert scored, "No scored records found"
    assert all(r["quality_marker_score"] == 1.0 for r in scored), (
        "Expected all scores to be 1.0 in this file"
    )

    # Use the module's summarize with real tasks list derived from records
    task_ids_seen = list(dict.fromkeys(r["task_id"] for r in records if r.get("task_id")))
    tasks = [{"id": tid} for tid in task_ids_seen]
    summary = MODULE.summarize(
        records, tasks, "real-data-smoke", "abc1234", BENCH / "tasks.tail.json"
    )

    assert summary["measurement_valid"] is False, (
        f"Expected measurement_valid=False for all-1.0 records, got: {summary.get('measurement_valid')}"
    )
    assert summary["marker_saturated"] is True

    warnings = MODULE.summary_warnings(summary)
    assert len(warnings) > 0, "Expected non-empty warnings for saturated run"
    warning_text = "\n".join(warnings)
    assert "NOT valid" in warning_text or "not valid" in warning_text.lower()


# ---------------------------------------------------------------------------
# 弁別ゼロ: 満点でなくても全レコード同値なら測定は無効
# ---------------------------------------------------------------------------

def test_uniform_non_perfect_scores_are_reported_as_no_discrimination():
    """全レコードが同じ値なら、その値が 1.0 でなくても品質差は測れない。

    飽和 (全件 1.0) だけを検知すると、天井が 0.8 に移動した場合を
    見逃す。#557 が問題にしているのは「弁別できないこと」であり、
    「値が 1.0 であること」ではない。
    """
    records = [
        _record("claude_code_goal_command", marker=0.8, task_id="t1"),
        _record("claude_code_goal_command", marker=0.8, task_id="t2"),
        _record("mission", marker=0.8, task_id="t1"),
        _record("mission", marker=0.8, task_id="t2"),
    ]
    summary = _summarize(records)

    assert summary["marker_saturated"] is False
    assert summary["measurement_valid"] is False
    assert summary["measurement_valid_reason"] == "no_discrimination"
    assert summary["marker_score_delta_vs_goal"] == 0.0
    assert summary["arms"]["mission"]["marker_score_distinct_values"] == 1
    assert MODULE.summary_warnings(summary), "warning must be emitted"


def test_varied_scores_remain_valid_with_discrimination():
    """値がばらついていれば measurement_valid は true のまま。"""
    records = [
        _record("claude_code_goal_command", marker=0.6, task_id="t1"),
        _record("claude_code_goal_command", marker=0.8, task_id="t2"),
        _record("mission", marker=0.8, task_id="t1"),
        _record("mission", marker=1.0, task_id="t2"),
    ]
    summary = _summarize(records)

    assert summary["measurement_valid"] is True
    assert summary["measurement_valid_reason"] == "ok"
    # No saturation-specific warning; a single-sample warning (#565) may appear
    # when repeats_observed=1, which is expected for n=1 inputs.
    text = "\n".join(MODULE.summary_warnings(summary))
    assert "saturated" not in text.lower()
    assert "NOT valid" not in text
