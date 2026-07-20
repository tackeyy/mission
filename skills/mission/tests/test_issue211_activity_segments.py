"""Issue #211: phase activity segments make wait time observable."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
MISSION_AUDIT_PY = REPO_ROOT / "scripts" / "mission-audit.py"
TASK_TEXT = "activity timing task"
TASK_ID = hashlib.sha256(TASK_TEXT.encode("utf-8")).hexdigest()[:16]


def _state_path(project: Path) -> Path:
    path = project / ".mission-state" / "sessions" / "test.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _base_state(project: Path, **overrides) -> dict:
    state = {
        "mission": TASK_TEXT,
        "mission_id": TASK_ID,
        "complexity": "Standard",
        "reviewer_count": 2,
        "iteration": 1,
        "phase": "executing",
        "phase_started_at": "2026-07-21T00:00:00Z",
        "phase_durations_sec": {"planning": 30.0},
        "score_history": [],
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "started_at": "2026-07-21T00:00:00Z",
        "updated_at": "2026-07-21T00:00:00Z",
        "project_root": str(project),
        "session_id": "test",
        "agent": "codex",
    }
    state.update(overrides)
    return state


def _write_state(project: Path, **overrides) -> Path:
    path = _state_path(project)
    path.write_text(json.dumps(_base_state(project, **overrides)), encoding="utf-8")
    return path


def _run_activity(run_cli, project: Path, *args: str):
    return run_cli("activity", *args, cwd=project)


def test_activity_cli_records_all_kinds_and_preserves_legacy_phase_durations(
    tmp_path, run_cli
):
    path = _write_state(tmp_path)
    schedule = [
        ("active", "implementation", "2026-07-21T00:00:00Z"),
        ("external-wait", "external-response", "2026-07-21T00:10:00Z"),
        ("approval-wait", "user-approval", "2026-07-21T00:20:00Z"),
        ("reviewer-wait", "review-response", "2026-07-21T00:30:00Z"),
        ("idle", "no-runnable-work", "2026-07-21T00:40:00Z"),
    ]
    for kind, reason, at in schedule:
        result = _run_activity(
            run_cli,
            tmp_path,
            "start",
            "--kind",
            kind,
            "--reason",
            reason,
            "--at",
            at,
        )
        assert result.returncode == 0, result.stderr
    result = _run_activity(
        run_cli, tmp_path, "end", "--at", "2026-07-21T00:50:00Z"
    )
    assert result.returncode == 0, result.stderr

    state = json.loads(path.read_text())
    segments = state["activity_segments"]
    assert [segment["kind"] for segment in segments] == [row[0] for row in schedule]
    assert [segment["reason"] for segment in segments] == [row[1] for row in schedule]
    assert {segment["phase"] for segment in segments} == {"executing"}
    assert [segment["duration_sec"] for segment in segments] == [600.0] * 5
    assert state["activity_current"] is None
    assert state["phase_durations_sec"] == {"planning": 30.0}


def test_activity_start_and_end_are_idempotent(tmp_path, run_cli):
    path = _write_state(tmp_path)
    args = (
        "start",
        "--kind",
        "active",
        "--reason",
        "implementation",
        "--at",
        "2026-07-21T00:00:00Z",
    )
    assert _run_activity(run_cli, tmp_path, *args).returncode == 0
    assert _run_activity(run_cli, tmp_path, *args).returncode == 0
    end = ("end", "--at", "2026-07-21T00:10:00Z")
    assert _run_activity(run_cli, tmp_path, *end).returncode == 0
    assert _run_activity(run_cli, tmp_path, *end).returncode == 0

    state = json.loads(path.read_text())
    assert len(state["activity_segments"]) == 1
    assert state["activity_segments"][0]["duration_sec"] == 600.0


def test_activity_rejects_negative_segment_without_mutating_state(tmp_path, run_cli):
    path = _write_state(tmp_path)
    assert _run_activity(
        run_cli,
        tmp_path,
        "start",
        "--kind",
        "active",
        "--reason",
        "implementation",
        "--at",
        "2026-07-21T00:10:00Z",
    ).returncode == 0
    before = path.read_bytes()

    result = _run_activity(
        run_cli, tmp_path, "end", "--at", "2026-07-21T00:05:00Z"
    )

    assert result.returncode == 2
    assert "before" in result.stderr.lower()
    assert path.read_bytes() == before


def test_activity_resume_closes_only_observed_time_and_does_not_double_count(
    tmp_path, run_cli
):
    path = _write_state(
        tmp_path,
        updated_at="2026-07-21T00:05:00Z",
        activity_current={
            "kind": "active",
            "phase": "executing",
            "reason": "implementation",
            "started_at": "2026-07-21T00:00:00Z",
        },
        activity_segments=[],
    )
    start = (
        "start",
        "--kind",
        "active",
        "--reason",
        "resumed-implementation",
        "--at",
        "2026-07-21T01:00:00Z",
        "--resume",
    )
    assert _run_activity(run_cli, tmp_path, *start).returncode == 0
    assert _run_activity(run_cli, tmp_path, *start).returncode == 0
    assert _run_activity(
        run_cli, tmp_path, "end", "--at", "2026-07-21T01:10:00Z"
    ).returncode == 0

    state = json.loads(path.read_text())
    assert [segment["duration_sec"] for segment in state["activity_segments"]] == [
        300.0,
        600.0,
    ]
    assert state["activity_unobserved_gap_sec"] == 3300.0


def test_activity_resume_with_same_labels_still_closes_the_stale_segment_once(
    tmp_path, run_cli
):
    path = _write_state(
        tmp_path,
        updated_at="2026-07-21T00:05:00Z",
        activity_current={
            "kind": "active",
            "phase": "executing",
            "reason": "implementation",
            "started_at": "2026-07-21T00:00:00Z",
        },
        activity_segments=[],
    )
    start = (
        "start",
        "--kind",
        "active",
        "--reason",
        "implementation",
        "--at",
        "2026-07-21T01:00:00Z",
        "--resume",
    )

    assert _run_activity(run_cli, tmp_path, *start).returncode == 0
    assert _run_activity(run_cli, tmp_path, *start).returncode == 0

    state = json.loads(path.read_text())
    assert [segment["duration_sec"] for segment in state["activity_segments"]] == [
        300.0
    ]
    assert state["activity_current"]["started_at"] == "2026-07-21T01:00:00Z"
    assert state["activity_unobserved_gap_sec"] == 3300.0


@pytest.mark.parametrize("command", ["init", "refresh-pid"])
def test_reinit_and_refresh_close_open_segment_once_without_losing_history(
    tmp_path, run_cli, command
):
    path = _write_state(
        tmp_path,
        updated_at="2026-07-21T00:05:00Z",
        activity_current={
            "kind": "active",
            "phase": "executing",
            "reason": "work",
            "started_at": "2026-07-21T00:00:00Z",
        },
        activity_segments=[],
        activity_rollup={"observed_total_sec": 0.0},
    )
    args = ("init", TASK_TEXT) if command == "init" else ("refresh-pid",)
    env = {"MISSION_STATE_NOW": "2026-07-21T01:00:00Z"}

    first = run_cli(*args, cwd=tmp_path, env_extra=env)
    second = run_cli(*args, cwd=tmp_path, env_extra=env)

    assert first.returncode == second.returncode == 0
    state = json.loads(path.read_text())
    assert state["activity_current"] is None
    assert state["activity_rollup"]["observed_total_sec"] == 300.0
    assert state["activity_rollup"]["closed_segment_count"] == 1
    assert len(state["activity_segments"]) == 1


def test_phase_transition_splits_open_activity_atomically_and_terminal_closes_it(
    tmp_path, run_cli
):
    path = _write_state(tmp_path, phase_started_at="2026-07-21T00:00:00Z")
    assert _run_activity(
        run_cli,
        tmp_path,
        "start",
        "--kind",
        "active",
        "--reason",
        "work",
        "--at",
        "2026-07-21T00:00:00Z",
    ).returncode == 0

    transition = run_cli(
        "set",
        "phase=reviewing",
        cwd=tmp_path,
        env_extra={"MISSION_STATE_NOW": "2026-07-21T00:10:00Z"},
    )
    assert transition.returncode == 0, transition.stderr
    middle = json.loads(path.read_text())
    assert middle["activity_segments"][0]["duration_sec"] == 600.0
    assert middle["activity_current"] == {
        "kind": "active",
        "phase": "reviewing",
        "reason": "work",
        "started_at": "2026-07-21T00:10:00Z",
    }
    assert middle["phase_durations_sec"]["executing"] == 600.0

    halted = run_cli(
        "mark-halt",
        "--reason",
        "stop",
        "--category",
        "user-abort",
        cwd=tmp_path,
        env_extra={"MISSION_STATE_NOW": "2026-07-21T00:20:00Z"},
    )
    assert halted.returncode == 0, halted.stderr
    final = json.loads(path.read_text())
    assert final["activity_current"] is None
    assert [segment["duration_sec"] for segment in final["activity_segments"]] == [
        600.0,
        600.0,
    ]
    assert final["phase_durations_sec"]["reviewing"] == 600.0


def test_activity_history_is_bounded_while_rollup_keeps_all_duration(tmp_path, run_cli):
    path = _write_state(tmp_path)
    for index in range(40):
        start = f"2026-07-21T00:{index:02d}:00Z"
        end = f"2026-07-21T00:{index:02d}:30Z"
        assert _run_activity(
            run_cli,
            tmp_path,
            "start",
            "--kind",
            "active",
            "--reason",
            "work",
            "--at",
            start,
        ).returncode == 0
        assert _run_activity(run_cli, tmp_path, "end", "--at", end).returncode == 0
    state = json.loads(path.read_text())
    assert len(state["activity_segments"]) <= 32
    assert state["activity_rollup"]["closed_segment_count"] == 40
    assert state["activity_rollup"]["observed_total_sec"] == 1200.0
    stats = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    assert stats.returncode == 0, stats.stderr
    timing = json.loads(stats.stdout)["activity_timing"]
    assert timing["observed_total_sec"] == 1200.0
    assert timing["activity_duration_totals_sec"] == {"active": 1200.0}


def _closed(kind: str, phase: str, reason: str, start: str, end: str, duration: float):
    return {
        "kind": kind,
        "phase": phase,
        "reason": reason,
        "started_at": start,
        "ended_at": end,
        "duration_sec": duration,
    }


def test_stats_and_audit_share_activity_percentiles_and_reason_breakdown(
    tmp_path, run_cli
):
    state_a = [
        _closed("active", "planning", "work", "2026-07-21T00:00:00Z", "2026-07-21T00:00:30Z", 30),
        _closed("approval-wait", "planning", "user-approval", "2026-07-21T00:00:30Z", "2026-07-21T00:01:40Z", 70),
        _closed("external-wait", "executing", "external-response", "2026-07-21T00:01:40Z", "2026-07-21T00:03:20Z", 100),
    ]
    state_b = [
        _closed("active", "planning", "work", "2026-07-21T00:00:00Z", "2026-07-21T00:01:10Z", 70),
        _closed("approval-wait", "planning", "user-approval", "2026-07-21T00:01:10Z", "2026-07-21T00:01:40Z", 30),
        _closed("external-wait", "executing", "external-response", "2026-07-21T00:01:40Z", "2026-07-21T00:06:40Z", 300),
    ]
    _write_state(
        tmp_path / "a",
        session_id="a",
        activity_segments=state_a,
        activity_current=None,
        updated_at="2026-07-21T00:03:20Z",
    )
    _write_state(
        tmp_path / "b",
        session_id="b",
        activity_segments=state_b,
        activity_current=None,
        updated_at="2026-07-21T00:06:40Z",
    )

    stats_result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    assert stats_result.returncode == 0, stats_result.stderr
    stats = json.loads(stats_result.stdout)["activity_timing"]
    audit_result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--json"],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "MISSION_AUDIT_NOW": "2026-07-21T01:00:00Z"},
    )
    audit = json.loads(audit_result.stdout)["activity_timing"]

    assert audit == stats
    assert stats["percentile_method"] == "linear-interpolation-r7"
    assert stats["task_key"] == "mission_id-or-unknown"
    assert stats["task_duration_percentiles_sec"][TASK_ID] == {
        "count": 2,
        "p50": 300.0,
        "p90": 380.0,
    }
    assert stats["phase_duration_percentiles_sec"]["planning"] == {
        "count": 2,
        "p50": 100.0,
        "p90": 100.0,
    }
    assert stats["phase_duration_percentiles_sec"]["executing"] == {
        "count": 2,
        "p50": 200.0,
        "p90": 280.0,
    }
    assert stats["activity_duration_totals_sec"] == {
        "active": 100.0,
        "approval-wait": 100.0,
        "external-wait": 400.0,
    }
    assert stats["wait_reason_totals_sec"] == {
        "approval-wait": {"user-approval": 100.0},
        "external-wait": {"external-response": 400.0},
    }
    assert stats["observed_total_sec"] == 600.0
    assert sum(stats["activity_duration_totals_sec"].values()) == 600.0


def test_activity_summary_ignores_open_negative_and_unknown_segments(
    tmp_path, run_cli
):
    _write_state(
        tmp_path / "bad",
        activity_segments=[
            _closed("external-wait", "executing", "", "2026-07-21T00:00:00Z", "2026-07-21T00:00:10Z", 10),
            _closed("active", "executing", "bad", "2026-07-21T00:01:00Z", "2026-07-21T00:00:00Z", -60),
            _closed("mystery", "executing", "bad", "2026-07-21T00:00:00Z", "2026-07-21T00:00:10Z", 10),
            {"kind": "idle", "phase": "executing", "reason": "open", "started_at": "2026-07-21T00:00:00Z"},
        ],
        activity_current={
            "kind": "idle",
            "phase": "executing",
            "reason": "open",
            "started_at": "2026-07-21T00:00:00Z",
        },
    )
    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    timing = json.loads(result.stdout)["activity_timing"]

    assert timing["observed_total_sec"] == 10.0
    assert timing["invalid_segment_count"] == 2
    assert timing["open_segment_count"] == 1
    assert timing["wait_reason_totals_sec"] == {
        "external-wait": {"unknown": 10.0}
    }


def test_activity_summary_marks_legacy_time_unclassified_without_inventing_a_cause(
    tmp_path, run_cli
):
    _write_state(
        tmp_path,
        phase_durations_sec={"planning": 40.0, "executing": 60.0},
    )

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    timing = json.loads(result.stdout)["activity_timing"]
    assert timing["observed_total_sec"] == 0.0
    assert timing["unclassified_sec"] == 100.0
    assert timing["coverage_ratio"] == 0.0
    assert timing["wait_reason_totals_sec"] == {}
    assert timing["states_without_activity_count"] == 1


def test_activity_summary_rejects_malformed_rollup_values_and_reports_inconsistency(
    tmp_path, run_cli
):
    _write_state(
        tmp_path,
        phase_durations_sec={"executing": 30.0},
        activity_rollup={
            "observed_total_sec": 999.0,
            "closed_segment_count": 3,
            "activity_duration_totals_sec": {
                "active": 10.0,
                "future-kind": 20.0,
                "idle": float("nan"),
            },
            "phase_activity_duration_totals_sec": {
                "executing": {"active": 9.0},
            },
            "wait_reason_totals_sec": {
                "external-wait": {"external-response": -1.0},
            },
        },
    )

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    timing = json.loads(result.stdout)["activity_timing"]
    assert timing["observed_total_sec"] == 10.0
    assert timing["activity_duration_totals_sec"] == {"active": 10.0}
    assert timing["invalid_segment_count"] >= 4
    assert timing["totals_consistent"] is False


def test_activity_requires_explicit_reason_and_known_kind(tmp_path, run_cli):
    _write_state(tmp_path)
    missing = _run_activity(
        run_cli,
        tmp_path,
        "start",
        "--kind",
        "external-wait",
        "--at",
        "2026-07-21T00:00:00Z",
    )
    unknown = _run_activity(
        run_cli,
        tmp_path,
        "start",
        "--kind",
        "network-problem",
        "--reason",
        "unknown",
        "--at",
        "2026-07-21T00:00:00Z",
    )
    assert missing.returncode == 2
    assert unknown.returncode == 2


def test_wait_reason_is_enum_and_detail_is_sanitized(tmp_path, run_cli):
    path = _write_state(tmp_path)
    invalid = _run_activity(
        run_cli,
        tmp_path,
        "start",
        "--kind",
        "external-wait",
        "--reason",
        "probably-network",
        "--at",
        "2026-07-21T00:00:00Z",
    )
    assert invalid.returncode == 2

    valid = _run_activity(
        run_cli,
        tmp_path,
        "start",
        "--kind",
        "external-wait",
        "--reason",
        "external-response",
        "--detail",
        " provider\n\trequest ",
        "--at",
        "2026-07-21T00:00:00Z",
    )
    assert valid.returncode == 0, valid.stderr
    state = json.loads(path.read_text())
    assert state["activity_current"]["reason"] == "external-response"
    assert state["activity_current"]["detail"] == "provider request"


def test_activity_observability_does_not_change_review_quality_gate(tmp_path, run_cli):
    path = _write_state(
        tmp_path,
        threshold=4.0,
        min_item_threshold=3.5,
        reviewer_count=3,
        review_tier="full",
    )
    assert _run_activity(
        run_cli,
        tmp_path,
        "start",
        "--kind",
        "reviewer-wait",
        "--reason",
        "independent-review",
        "--at",
        "2026-07-21T00:00:00Z",
    ).returncode == 0
    state = json.loads(path.read_text())
    assert state["threshold"] == 4.0
    assert state["min_item_threshold"] == 3.5
    assert state["reviewer_count"] == 3
    assert state["review_tier"] == "full"
