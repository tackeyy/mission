"""Issue #275: diff-review iteration measurement stays additive and fail-open."""

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCHMARK_DIR = REPO_ROOT / "benchmarks" / "mission-vs-goal"


def _load_runner():
    path = BENCHMARK_DIR / "run_claude_goal_vs_mission.py"
    spec = importlib.util.spec_from_file_location("issue275_runner", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


RUNNER = _load_runner()


def _load_activity_segments():
    path = REPO_ROOT / "skills" / "mission" / "lib" / "activity_segments.py"
    spec = importlib.util.spec_from_file_location("issue275_activity_segments", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


ACTIVITY = _load_activity_segments()


def test_discriminating_tasks_force_a_bounded_unpassed_first_review_iteration():
    tasks = json.loads((BENCHMARK_DIR / "tasks.discriminating.json").read_text(encoding="utf-8"))["tasks"]

    assert len(tasks) == 5
    assert all(task.get("fail_first") is True for task in tasks)
    assert all(task.get("mission_max_iter", 0) >= 3 for task in tasks)


def test_fail_first_protocol_is_mission_only():
    task = {"id": "fixture", "category": "audit", "prompt": "inspect", "validator": "cover all", "fail_first": True}

    mission = RUNNER.build_prompt(task, "mission", "out.md", mission_max_iter=3)
    goal = RUNNER.build_prompt(task, "claude_code_goal_command", "out.md")

    assert "bounded first pass" in mission
    assert "at least one validator item" in mission
    assert "High" in mission
    assert "mark-passes" in mission
    assert "bounded first pass" not in goal


def test_activity_segments_are_owned_by_positive_state_iteration_and_split_on_change():
    # The state records the *last completed* score iteration. During iteration
    # 1 it is 0; after push-score it is 1 while iteration 2 is executing.
    state = {"phase": "executing", "loop_active": True, "iteration": 0}

    assert ACTIVITY.start_activity_segment(state, "active", "implementation", "2026-08-12T00:00:00Z") is True
    state["iteration"] = 1
    assert ACTIVITY.start_activity_segment(state, "active", "implementation", "2026-08-12T00:01:00Z") is True
    assert ACTIVITY.end_activity_segment(state, "2026-08-12T00:02:00Z") is True

    assert [segment["iteration"] for segment in state["activity_segments"]] == [1, 2]


def test_state_observations_measure_iteration_two_and_verified_context_manifest(tmp_path):
    archive = tmp_path / ".mission-state" / "archive"
    archive.mkdir(parents=True)
    aggregate = {
        "schema": "mission-review-aggregate/1", "iteration": 2,
        "context_mode_expected": "bounded", "context_manifest_generated": True,
    }
    content = json.dumps(aggregate).encode()
    evidence = archive / "iter-2.json"
    evidence.write_bytes(content)
    state = {
        "activity_segments": [{"iteration": 2, "kind": "active", "phase": "scoring", "duration_sec": 12}],
        "score_history": [
            {"iteration": 1, "timestamp": "2026-08-12T00:00:00Z"},
            {"iteration": 2, "timestamp": "2026-08-12T00:01:00Z", "review_evidence_ref": {
                "kind": "review-aggregate", "path": ".mission-state/archive/iter-2.json",
                "digest": "sha256:" + __import__("hashlib").sha256(content).hexdigest(),
            }},
        ],
    }

    observation = RUNNER.extract_diff_review_observations(tmp_path, state)
    second = observation["iterations"][1]
    assert second["activity_duration_sec"] == {"active": {"scoring": 12.0}}
    assert second["wall_clock_sec"] == 60.0
    assert second["context_manifest_generated"] is True
    assert second["context_manifest_fallback"] is False
    assert second["per_iteration_cost_status"] == "unavailable"

    RUNNER.attach_diff_review_record_cost(observation, 2.5)
    assert second["record_total_cost_usd"] == 2.5


def test_summary_exposes_diff_review_measurement_gate_counts():
    base = {
        "run_status": "completed", "comparable_attempt": True, "completion": True, "validator_pass": True,
        "human_quality_score": 5.0, "intervention_count": 0, "evidence_completeness": 5.0,
        "quality_marker_score": 1.0, "elapsed_minutes": 1.0, "total_cost_usd": 1.0,
        "permission_mode_degraded": False, "failure_kind": None,
    }
    records = [
        {**base, "arm": "mission", "mission_iterations": 2, "diff_review_observations": {"status": "observed", "iterations": [{"iteration": 2, "context_mode_expected": "bounded", "context_manifest_generated": True, "context_manifest_fallback": False}]}},
        {**base, "arm": "claude_code_goal_command", "mission_iterations": None},
    ]
    summary = RUNNER.summarize(records, [{"id": "fixture"}], "rid", "abc1234", BENCHMARK_DIR / "tasks.discriminating.json")

    assert summary["diff_review_measurement_gate"] == {
        "iter2_eligible_records": 1, "permission_degraded_records": 0,
        "iter2_record_cost_usd_total": 1.0, "iter2_record_cost_usd_mean": 1.0,
        "mission_loop_not_initialized_records": 0, "context_manifest_expected_iterations": 1,
        "context_manifest_generated_iterations": 1, "context_manifest_fallback_iterations": 0,
    }


@pytest.mark.parametrize("mutation", ["digest", "escape", "symlink", "hardlink"])
def test_untrusted_context_evidence_is_unavailable_without_breaking_measurement(tmp_path, mutation):
    archive = tmp_path / ".mission-state" / "archive"
    archive.mkdir(parents=True)
    evidence = archive / "iter-2.json"
    content = json.dumps({"schema": "mission-review-aggregate/1", "iteration": 2,
                          "context_mode_expected": "bounded", "context_manifest_generated": True}).encode()
    evidence.write_bytes(content)
    path = ".mission-state/archive/iter-2.json"
    digest = "sha256:" + __import__("hashlib").sha256(content).hexdigest()
    if mutation == "digest":
        digest = "sha256:" + "0" * 64
    elif mutation == "escape":
        path = "../outside.json"
    elif mutation == "symlink":
        evidence.unlink()
        evidence.symlink_to(tmp_path / "outside.json")
    elif mutation == "hardlink":
        (archive / "same-content.json").hardlink_to(evidence)
    state = {"score_history": [{"iteration": 2, "timestamp": "2026-08-12T00:01:00Z",
                                 "review_evidence_ref": {"kind": "review-aggregate", "path": path, "digest": digest}}]}

    row = RUNNER.extract_diff_review_observations(tmp_path, state)["iterations"][0]
    assert row["context_mode_expected"] is None
    assert row["context_manifest_generated"] is None
    assert row["context_manifest_fallback"] is None


def test_invalid_iteration_is_excluded_while_legacy_segment_remains_generically_accepted():
    state = {"activity_segments": [
        {"iteration": True, "kind": "active", "phase": "executing", "duration_sec": 1},
        {"iteration": -1, "kind": "active", "phase": "executing", "duration_sec": 1},
        {"iteration": "2", "kind": "active", "phase": "executing", "duration_sec": 1},
        {"kind": "active", "phase": "executing", "reason": "implementation",
         "started_at": "2026-08-12T00:00:00Z", "ended_at": "2026-08-12T00:00:02Z", "duration_sec": 2},
    ]}

    assert RUNNER.extract_diff_review_observations(Path.cwd(), state)["status"] == "unavailable"
    assert ACTIVITY.summarize_activity_states([state])["observed_total_sec"] == 2.0


def test_iter2_gate_excludes_blocked_degraded_and_noncomparable_records():
    base = {"arm": "mission", "run_status": "completed", "comparable_attempt": True, "failure_kind": None,
            "permission_mode_degraded": False, "mission_iterations": 2,
            "diff_review_observations": {"status": "observed", "iterations": []},
            "completion": True, "validator_pass": True, "human_quality_score": 5.0,
            "intervention_count": 0, "evidence_completeness": 5.0, "quality_marker_score": 1.0,
            "elapsed_minutes": 1.0, "total_cost_usd": 1.0}
    records = [
        {**base, "run_status": "blocked"},
        {**base, "permission_mode_degraded": True},
        {**base, "comparable_attempt": False},
    ]

    summary = RUNNER.summarize(records, [{"id": "fixture"}], "rid", "abc1234", BENCHMARK_DIR / "tasks.discriminating.json")
    assert summary["diff_review_measurement_gate"]["iter2_eligible_records"] == 0
