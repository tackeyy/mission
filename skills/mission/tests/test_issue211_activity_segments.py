"""Issue #211: phase activity segments make wait time observable."""

from __future__ import annotations

import json
import hashlib
import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
MISSION_AUDIT_PY = REPO_ROOT / "scripts" / "mission-audit.py"
TASK_TEXT = "activity timing task"
TASK_ID = hashlib.sha256(TASK_TEXT.encode("utf-8")).hexdigest()[:16]
MISSION_STATE_PY = REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py"


def _load_mission_state_module():
    spec = importlib.util.spec_from_file_location("mission_state_issue211", MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_activity_rejects_state_clock_rollback_without_mutating_state(
    tmp_path, run_cli
):
    path = _write_state(tmp_path, updated_at="2026-07-21T00:10:00Z")
    before = path.read_bytes()

    result = _run_activity(
        run_cli,
        tmp_path,
        "start",
        "--kind",
        "active",
        "--reason",
        "work",
        "--at",
        "2026-07-21T00:09:59Z",
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


def test_activity_resume_before_open_start_is_rejected_without_mutation(
    tmp_path, run_cli
):
    path = _write_state(
        tmp_path,
        updated_at="2026-07-21T00:00:00Z",
        activity_current={
            "kind": "active",
            "phase": "executing",
            "reason": "implementation",
            "started_at": "2026-07-21T00:10:00Z",
        },
        activity_segments=[],
    )
    before = path.read_bytes()

    result = _run_activity(
        run_cli,
        tmp_path,
        "start",
        "--kind",
        "active",
        "--reason",
        "resumed-implementation",
        "--at",
        "2026-07-21T00:05:00Z",
        "--resume",
    )

    assert result.returncode == 2
    assert "before" in result.stderr.lower()
    assert path.read_bytes() == before


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


def test_same_mission_reinit_accrues_and_preserves_the_open_phase(tmp_path, run_cli):
    path = _write_state(
        tmp_path,
        phase="executing",
        phase_started_at="2026-07-21T00:00:00Z",
        updated_at="2026-07-21T00:05:00Z",
        phase_durations_sec={"planning": 30.0},
    )

    result = run_cli(
        "init",
        TASK_TEXT,
        cwd=tmp_path,
        env_extra={"MISSION_STATE_NOW": "2026-07-21T01:00:00Z"},
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(path.read_text())
    assert state["phase"] == "executing"
    assert state["phase_started_at"] == "2026-07-21T01:00:00Z"
    assert state["phase_durations_sec"] == {
        "planning": 30.0,
        "executing": 300.0,
    }


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


def test_stats_and_audit_dedupe_same_session_with_shared_newest_precedence(
    tmp_path, run_cli
):
    project_root = str(tmp_path / "canonical-project")
    old = _base_state(
        tmp_path,
        project_root=project_root,
        session_id="duplicate",
        started_at="2026-07-20T00:00:00Z",
        updated_at="2026-07-21T00:01:00Z",
        activity_segments=[
            _closed("active", "executing", "work", "2026-07-21T00:00:00Z", "2026-07-21T00:01:00Z", 60)
        ],
        activity_current=None,
    )
    newest = _base_state(
        tmp_path,
        project_root=project_root,
        session_id="duplicate",
        started_at="2026-07-21T00:00:00Z",
        updated_at="2026-07-21T00:03:00Z",
        phase="done",
        loop_active=False,
        passes=True,
        activity_segments=[
            _closed("active", "executing", "work", "2026-07-21T00:00:00Z", "2026-07-21T00:03:00Z", 180)
        ],
        activity_current=None,
    )
    old_path = tmp_path / "a-old" / ".mission-state" / "sessions" / "duplicate.json"
    new_path = tmp_path / "z-new" / ".mission-state" / "sessions" / "duplicate.json"
    old_path.parent.mkdir(parents=True)
    new_path.parent.mkdir(parents=True)
    old_path.write_text(json.dumps(old))
    new_path.write_text(json.dumps(newest))

    stats = json.loads(
        run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path, check=True).stdout
    )["activity_timing"]
    audit = json.loads(
        subprocess.run(
            [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--json"],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "MISSION_AUDIT_NOW": "2026-07-21T01:00:00Z"},
        ).stdout
    )["activity_timing"]

    assert stats == audit
    assert stats["observed_total_sec"] == 180.0


def test_coverage_includes_the_current_phase_window_and_never_exceeds_one(
    tmp_path, run_cli
):
    _write_state(
        tmp_path,
        phase="executing",
        phase_started_at="2026-07-21T00:00:00Z",
        updated_at="2026-07-21T00:10:00Z",
        phase_durations_sec={"planning": 30.0},
        activity_segments=[
            _closed(
                "active",
                "executing",
                "work",
                "2026-07-21T00:00:00Z",
                "2026-07-21T00:10:00Z",
                600,
            )
        ],
        activity_current=None,
    )

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    timing = json.loads(result.stdout)["activity_timing"]
    assert timing["phase_duration_total_sec"] == 630.0
    assert timing["coverage_ratio"] == pytest.approx(600.0 / 630.0)
    assert timing["coverage_ratio"] <= 1.0
    assert timing["totals_consistent"] is True


def test_stats_text_and_audit_markdown_display_activity_timing(tmp_path, run_cli):
    _write_state(
        tmp_path,
        activity_segments=[
            _closed(
                "reviewer-wait",
                "reviewing",
                "review-response",
                "2026-07-21T00:00:00Z",
                "2026-07-21T00:01:00Z",
                60,
            )
        ],
        activity_current=None,
        activity_unobserved_gap_sec=12.0,
    )

    stats = run_cli("stats", "--root", str(tmp_path), cwd=tmp_path)
    audit = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "MISSION_AUDIT_NOW": "2026-07-21T01:00:00Z"},
    )

    assert "activity_timing:" in stats.stdout
    assert "reviewer-wait/review-response" in stats.stdout
    assert "phase reviewing" in stats.stdout
    assert f"task {TASK_ID}" in stats.stdout
    assert "unobserved gap:" in stats.stdout
    assert "totals consistent:" in stats.stdout
    assert "## Activity Timing" in audit.stdout
    assert "reviewer-wait/review-response" in audit.stdout
    assert "phase `reviewing`: p50" in audit.stdout
    assert f"task `{TASK_ID}`: p50" in audit.stdout
    assert "unobserved gap:" in audit.stdout
    assert "totals consistent:" in audit.stdout


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
    assert timing["invalid_segment_count"] == 3
    assert timing["open_segment_count"] == 0
    assert timing["wait_reason_totals_sec"] == {
        "external-wait": {"unknown": 10.0}
    }


@pytest.mark.parametrize(
    "activity_current",
    [
        {
            "kind": "future-kind",
            "phase": "executing",
            "reason": "work",
            "started_at": "2026-07-21T00:00:00Z",
        },
        {
            "kind": "active",
            "phase": "executing",
            "reason": "future-reason",
            "started_at": "2026-07-21T00:00:00Z",
        },
        {
            "kind": "active",
            "phase": "executing",
            "reason": "work",
            "started_at": "not-a-time",
        },
        {
            "kind": "active",
            "phase": "executing",
            "reason": "work",
            "started_at": "2026-07-21T02:00:00Z",
        },
    ],
)
def test_malformed_open_activity_is_an_invalid_anomaly_not_an_open_segment(
    tmp_path, run_cli, activity_current
):
    _write_state(
        tmp_path,
        updated_at="2026-07-21T01:00:00Z",
        activity_current=activity_current,
    )

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    timing = json.loads(result.stdout)["activity_timing"]
    assert timing["open_segment_count"] == 0
    assert timing["invalid_segment_count"] == 1
    assert timing["totals_consistent"] is False


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


def test_activity_start_rejects_terminal_state_without_mutation(tmp_path, run_cli):
    path = _write_state(tmp_path, phase="done", loop_active=False, passes=True)
    before = path.read_bytes()

    result = _run_activity(
        run_cli,
        tmp_path,
        "start",
        "--kind",
        "active",
        "--reason",
        "work",
        "--at",
        "2026-07-21T00:01:00Z",
    )

    assert result.returncode == 2
    assert path.read_bytes() == before


@pytest.mark.parametrize("terminal", ["mark-halt", "mark-passes"])
def test_terminal_control_succeeds_despite_malformed_activity_and_records_anomaly(
    tmp_path, run_cli, terminal
):
    path = _write_state(
        tmp_path,
        activity_current={
            "kind": "future-kind",
            "phase": "executing",
            "reason": "future-reason",
            "started_at": "not-a-time",
        },
    )
    args = (
        ("mark-halt", "--reason", "safe halt", "--category", "other")
        if terminal == "mark-halt"
        else (
            "mark-passes",
            "--force",
            "--reason",
            "test override",
            "--approved-by-user",
        )
    )

    result = run_cli(
        *args,
        cwd=tmp_path,
        env_extra={"MISSION_STATE_NOW": "2026-07-21T00:01:00Z"},
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(path.read_text())
    assert state["loop_active"] is False
    assert state["activity_current"] is None
    assert state["activity_anomaly_counts"]["invalid-current-terminal"] == 1


def test_repeated_terminal_command_closes_a_late_open_activity(tmp_path, run_cli):
    path = _write_state(
        tmp_path,
        phase="halted",
        loop_active=False,
        halt_reason="already halted",
        activity_current={
            "kind": "idle",
            "phase": "halted",
            "reason": "no-runnable-work",
            "started_at": "2026-07-21T00:00:00Z",
        },
    )

    result = run_cli(
        "mark-halt",
        "--reason",
        "still halted",
        "--category",
        "other",
        cwd=tmp_path,
        env_extra={"MISSION_STATE_NOW": "2026-07-21T00:01:00Z"},
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(path.read_text())
    assert state["activity_current"] is None
    assert state["activity_rollup"]["observed_total_sec"] == 60.0


def test_terminal_closes_only_to_last_trusted_update_and_marks_crash_gap_unobserved(
    tmp_path, run_cli
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
    )

    result = run_cli(
        "mark-halt",
        "--reason",
        "stale terminal",
        "--category",
        "stale",
        cwd=tmp_path,
        env_extra={"MISSION_STATE_NOW": "2026-07-21T01:00:00Z"},
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(path.read_text())
    assert state["activity_rollup"]["observed_total_sec"] == 300.0
    assert state["activity_unobserved_gap_sec"] == 3300.0


def test_stale_terminal_restores_pre_halt_phase_on_refresh_and_allows_work(
    tmp_path, run_cli
):
    path = _write_state(
        tmp_path,
        phase="executing",
        phase_started_at="2026-07-21T00:00:00Z",
        updated_at="2026-07-21T00:05:00Z",
        activity_current={
            "kind": "active",
            "phase": "executing",
            "reason": "work",
            "started_at": "2026-07-21T00:00:00Z",
        },
    )

    halted = run_cli(
        "mark-halt",
        "--reason",
        "stale: automatic stop",
        "--category",
        "stale",
        cwd=tmp_path,
        env_extra={"MISSION_STATE_NOW": "2026-07-21T01:00:00Z"},
    )
    assert halted.returncode == 0, halted.stderr
    stopped = json.loads(path.read_text())
    assert stopped["phase"] == "halted"
    assert stopped["resume_target_phase"] == "executing"
    assert stopped["phase_durations_sec"]["executing"] == 300.0

    refreshed = run_cli(
        "refresh-pid",
        cwd=tmp_path,
        env_extra={"MISSION_STATE_NOW": "2026-07-21T01:01:00Z"},
    )
    assert refreshed.returncode == 0, refreshed.stderr
    resumed = json.loads(path.read_text())
    assert resumed["loop_active"] is True
    assert resumed["halt_reason"] == ""
    assert resumed["phase"] == "executing"
    assert resumed["phase_started_at"] == "2026-07-21T01:01:00Z"
    assert "resume_target_phase" not in resumed

    activity = _run_activity(
        run_cli,
        tmp_path,
        "start",
        "--kind",
        "active",
        "--reason",
        "work",
        "--at",
        "2026-07-21T01:01:00Z",
    )
    assert activity.returncode == 0, activity.stderr
    next_result = run_cli("next", cwd=tmp_path)
    assert next_result.returncode == 0, next_result.stderr
    assert json.loads(next_result.stdout)["next_action"] == "run-executor"


def test_manual_halt_is_not_reactivated_by_orphan_like_reason(tmp_path, run_cli):
    path = _write_state(tmp_path, phase="executing")
    halted = run_cli(
        "mark-halt",
        "--reason",
        "orphan: manually acknowledged",
        "--category",
        "other",
        cwd=tmp_path,
        env_extra={"MISSION_STATE_NOW": "2026-07-21T00:01:00Z"},
    )
    assert halted.returncode == 0, halted.stderr

    refreshed = run_cli(
        "refresh-pid",
        cwd=tmp_path,
        env_extra={"MISSION_STATE_NOW": "2026-07-21T00:02:00Z"},
    )

    assert refreshed.returncode == 0, refreshed.stderr
    assert json.loads(refreshed.stdout)["reactivated"] is False
    state = json.loads(path.read_text())
    assert state["phase"] == "halted"
    assert state["loop_active"] is False
    assert state["halt_reason"] == "orphan: manually acknowledged"


def test_same_mission_reinit_with_invalid_open_activity_fails_without_overwrite(
    tmp_path, run_cli
):
    path = _write_state(
        tmp_path,
        activity_current={
            "kind": "future-kind",
            "phase": "executing",
            "reason": "future-reason",
            "started_at": "not-a-time",
        },
    )
    before = path.read_bytes()

    result = run_cli(
        "init",
        TASK_TEXT,
        cwd=tmp_path,
        env_extra={"MISSION_STATE_NOW": "2026-07-21T01:00:00Z"},
    )

    assert result.returncode == 2
    assert path.read_bytes() == before


def test_activity_writer_accepts_json_integer_rollup_values(tmp_path, run_cli):
    path = _write_state(
        tmp_path,
        updated_at="2026-07-21T00:00:05Z",
        activity_current={
            "kind": "active",
            "phase": "executing",
            "reason": "work",
            "started_at": "2026-07-21T00:00:05Z",
        },
        activity_rollup={
            "observed_total_sec": 5,
            "closed_segment_count": 1,
            "activity_duration_totals_sec": {"active": 5},
            "phase_activity_duration_totals_sec": {"executing": {"active": 5}},
            "wait_reason_totals_sec": {},
        },
    )

    result = _run_activity(
        run_cli, tmp_path, "end", "--at", "2026-07-21T00:00:15Z"
    )

    assert result.returncode == 0, result.stderr
    state = json.loads(path.read_text())
    assert state["activity_rollup"]["observed_total_sec"] == 15.0
    assert state["activity_rollup"]["closed_segment_count"] == 2


def test_zero_duration_closed_segments_remain_percentile_samples(tmp_path, run_cli):
    _write_state(
        tmp_path,
        activity_segments=[
            _closed(
                "active",
                "executing",
                "work",
                "2026-07-21T00:00:00Z",
                "2026-07-21T00:00:00Z",
                0,
            )
        ],
        activity_current=None,
    )

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)

    timing = json.loads(result.stdout)["activity_timing"]
    assert timing["task_duration_percentiles_sec"][TASK_ID] == {
        "count": 1,
        "p50": 0.0,
        "p90": 0.0,
    }
    assert timing["phase_duration_percentiles_sec"]["executing"] == {
        "count": 1,
        "p50": 0.0,
        "p90": 0.0,
    }


def test_rollup_reason_count_and_gap_anomalies_are_counted(tmp_path, run_cli):
    _write_state(
        tmp_path,
        activity_rollup={
            "observed_total_sec": 10.0,
            "closed_segment_count": True,
            "activity_duration_totals_sec": {"external-wait": 10.0},
            "phase_activity_duration_totals_sec": {
                "executing": {"external-wait": 10.0}
            },
            "wait_reason_totals_sec": {
                "external-wait": {"future-reason": 10.0}
            },
        },
        activity_unobserved_gap_sec=float("nan"),
    )

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)

    timing = json.loads(result.stdout)["activity_timing"]
    assert timing["observed_total_sec"] == 10.0
    assert timing["wait_reason_totals_sec"] == {}
    assert timing["closed_segment_count"] == 0
    assert timing["unobserved_gap_sec"] == 0.0
    assert timing["invalid_segment_count"] >= 3


def test_refresh_pid_closes_and_restarts_phase_at_the_observed_resume_boundary(
    tmp_path, run_cli
):
    path = _write_state(
        tmp_path,
        phase="executing",
        phase_started_at="2026-07-21T00:00:00Z",
        updated_at="2026-07-21T00:05:00Z",
        phase_durations_sec={"planning": 30.0},
        pid=0,
    )

    refreshed = run_cli(
        "refresh-pid",
        cwd=tmp_path,
        env_extra={"MISSION_STATE_NOW": "2026-07-21T01:00:00Z"},
    )
    transitioned = run_cli(
        "set",
        "phase=reviewing",
        cwd=tmp_path,
        env_extra={"MISSION_STATE_NOW": "2026-07-21T01:10:00Z"},
    )

    assert refreshed.returncode == transitioned.returncode == 0
    state = json.loads(path.read_text())
    assert state["phase_durations_sec"] == {
        "planning": 30.0,
        "executing": 900.0,
    }


def test_stats_and_audit_keep_missing_project_roots_separate_by_state_path(
    tmp_path, run_cli
):
    for name, duration in (("one", 60), ("two", 120)):
        state = _base_state(
            tmp_path,
            project_root=None,
            session_id="same-session",
            activity_segments=[
                _closed(
                    "active",
                    "executing",
                    "work",
                    "2026-07-21T00:00:00Z",
                    f"2026-07-21T00:0{duration // 60}:00Z",
                    duration,
                )
            ],
            activity_current=None,
        )
        path = tmp_path / name / ".mission-state" / "sessions" / "same-session.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(state))

    stats = json.loads(
        run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path, check=True).stdout
    )["activity_timing"]
    audit = json.loads(
        subprocess.run(
            [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--json"],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "MISSION_AUDIT_NOW": "2026-07-21T01:00:00Z"},
        ).stdout
    )["activity_timing"]

    assert stats == audit
    assert stats["observed_total_sec"] == 180.0


def test_stats_and_audit_normalize_trailing_slash_in_project_identity(
    tmp_path, run_cli
):
    canonical = str(tmp_path / "canonical")
    for name, project_root, duration, updated in (
        ("old", canonical + "/", 60, "2026-07-21T00:01:00Z"),
        ("new", canonical, 120, "2026-07-21T00:02:00Z"),
    ):
        state = _base_state(
            tmp_path,
            project_root=project_root,
            session_id="same-session",
            updated_at=updated,
            activity_segments=[
                _closed(
                    "active",
                    "executing",
                    "work",
                    "2026-07-21T00:00:00Z",
                    f"2026-07-21T00:0{duration // 60}:00Z",
                    duration,
                )
            ],
            activity_current=None,
        )
        path = tmp_path / name / ".mission-state" / "sessions" / "same-session.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(state))

    stats = json.loads(
        run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path, check=True).stdout
    )["activity_timing"]
    audit = json.loads(
        subprocess.run(
            [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--json"],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "MISSION_AUDIT_NOW": "2026-07-21T01:00:00Z"},
        ).stdout
    )["activity_timing"]

    assert stats == audit
    assert stats["observed_total_sec"] == 120.0


@pytest.mark.parametrize(
    "activity_current",
    [
        {
            "kind": ["active"],
            "phase": "executing",
            "reason": "work",
            "started_at": "2026-07-21T00:00:00Z",
        },
        {
            "kind": "active",
            "phase": "executing",
            "reason": ["work"],
            "started_at": "2026-07-21T00:00:00Z",
        },
        "malformed-scalar",
    ],
)
def test_unhashable_or_scalar_current_activity_is_counted_not_crashed(
    tmp_path, run_cli, activity_current
):
    _write_state(tmp_path, activity_current=activity_current)

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    timing = json.loads(result.stdout)["activity_timing"]
    assert timing["open_segment_count"] == 0
    assert timing["invalid_segment_count"] == 1
    assert timing["totals_consistent"] is False


def test_unhashable_raw_segment_is_invalid_not_a_stats_crash(tmp_path, run_cli):
    _write_state(
        tmp_path,
        activity_segments=[
            {
                "kind": ["active"],
                "phase": "executing",
                "reason": "work",
                "started_at": "2026-07-21T00:00:00Z",
                "ended_at": "2026-07-21T00:00:10Z",
                "duration_sec": 10.0,
            }
        ],
        activity_current=None,
    )

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    timing = json.loads(result.stdout)["activity_timing"]
    assert timing["observed_total_sec"] == 0.0
    assert timing["invalid_segment_count"] == 1
    assert timing["totals_consistent"] is False


def test_invalid_only_rollup_does_not_create_a_zero_percentile_sample(
    tmp_path, run_cli
):
    _write_state(
        tmp_path,
        activity_rollup={
            "observed_total_sec": 0.0,
            "closed_segment_count": 1,
            "activity_duration_totals_sec": {"future-kind": 0.0},
            "phase_activity_duration_totals_sec": {
                "executing": {"future-kind": 0.0}
            },
            "wait_reason_totals_sec": {},
        },
    )

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    timing = json.loads(result.stdout)["activity_timing"]
    assert timing["task_duration_percentiles_sec"] == {}
    assert timing["phase_duration_percentiles_sec"] == {}
    assert timing["invalid_segment_count"] >= 1
    assert timing["totals_consistent"] is False


@pytest.mark.parametrize(
    "rollup",
    [
        {"observed_total_sec": 0.0, "closed_segment_count": 1},
        {
            "observed_total_sec": 0.0,
            "closed_segment_count": 1,
            "activity_duration_totals_sec": [],
            "phase_activity_duration_totals_sec": [],
            "wait_reason_totals_sec": [],
        },
        {
            "observed_total_sec": 0.0,
            "closed_segment_count": 1,
            "activity_duration_totals_sec": {},
            "phase_activity_duration_totals_sec": {},
            "wait_reason_totals_sec": {},
        },
    ],
)
def test_rollup_requires_all_aggregate_maps_for_a_valid_sample(
    tmp_path, run_cli, rollup
):
    _write_state(tmp_path, activity_rollup=rollup)

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    timing = json.loads(result.stdout)["activity_timing"]
    assert timing["task_duration_percentiles_sec"] == {}
    assert timing["phase_duration_percentiles_sec"] == {}
    assert timing["invalid_segment_count"] >= 1
    assert timing["totals_consistent"] is False


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


@pytest.mark.parametrize("command", ["cleanup-stale", "halt-all"])
def test_bulk_terminal_writers_reread_under_lock_and_close_concurrent_activity(
    tmp_path, monkeypatch, capsys, command
):
    module = _load_mission_state_module()
    project = tmp_path / "project"
    state_path = _write_state(project, pid=99999999)
    original_lock = module.StateLock

    class InjectingLock:
        def __init__(self, path):
            self.delegate = original_lock(path)

        def __enter__(self):
            value = self.delegate.__enter__()
            latest = json.loads(state_path.read_text())
            latest["concurrent_sentinel"] = "preserve-me"
            latest["activity_current"] = {
                "kind": "active",
                "phase": "executing",
                "reason": "work",
                "started_at": "2026-07-21T00:00:00Z",
            }
            latest["updated_at"] = "2026-07-21T00:00:00Z"
            state_path.write_text(json.dumps(latest))
            return value

        def __exit__(self, exc_type, exc, tb):
            return self.delegate.__exit__(exc_type, exc, tb)

    monkeypatch.setattr(module, "StateLock", InjectingLock)
    monkeypatch.setattr(module, "iso_now", lambda: "2026-07-21T00:01:00Z")
    monkeypatch.setattr(module, "_iter_state_files", lambda root, **kwargs: [state_path])
    monkeypatch.setattr(module, "_default_search_roots", lambda: [project])
    monkeypatch.setattr(module, "_pid_is_agent", lambda pid: False)

    if command == "cleanup-stale":
        module.cmd_cleanup_stale(argparse.Namespace(root=str(project), execute=True))
    else:
        module.cmd_halt(
            argparse.Namespace(
                all=True,
                root=str(project),
                reason="bulk halt",
                category="other",
            )
        )
    capsys.readouterr()

    state = json.loads(state_path.read_text())
    assert state["concurrent_sentinel"] == "preserve-me"
    assert state["activity_current"] is None
    if command == "cleanup-stale":
        assert state["activity_rollup"]["observed_total_sec"] == 0.0
        assert state["activity_unobserved_gap_sec"] == 60.0
    else:
        assert state["activity_rollup"]["observed_total_sec"] == 60.0
        assert "activity_unobserved_gap_sec" not in state


def test_bulk_terminal_control_time_is_sampled_after_lock_and_never_precedes_update(
    tmp_path, monkeypatch, capsys
):
    module = _load_mission_state_module()
    project = tmp_path / "project"
    state_path = _write_state(project, pid=99999999)
    original_lock = module.StateLock

    class InjectingLock:
        def __init__(self, path):
            self.delegate = original_lock(path)

        def __enter__(self):
            value = self.delegate.__enter__()
            latest = json.loads(state_path.read_text())
            latest["activity_current"] = {
                "kind": "active",
                "phase": "executing",
                "reason": "work",
                "started_at": "2026-07-21T00:00:00Z",
            }
            latest["updated_at"] = "2026-07-21T00:02:00Z"
            state_path.write_text(json.dumps(latest))
            return value

        def __exit__(self, exc_type, exc, tb):
            return self.delegate.__exit__(exc_type, exc, tb)

    monkeypatch.setattr(module, "StateLock", InjectingLock)
    monkeypatch.setattr(module, "iso_now", lambda: "2026-07-21T00:01:00Z")
    monkeypatch.setattr(module, "_iter_state_files", lambda root, **kwargs: [state_path])
    monkeypatch.setattr(module, "_pid_is_agent", lambda pid: False)

    module.cmd_cleanup_stale(argparse.Namespace(root=str(project), execute=True))
    capsys.readouterr()

    state = json.loads(state_path.read_text())
    assert state["updated_at"] == "2026-07-21T00:02:00Z"
    assert state["activity_current"] is None
    assert state["activity_rollup"]["observed_total_sec"] == 120.0
    assert state.get("activity_anomaly_counts", {}) == {}
