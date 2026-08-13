"""#425: lane duration SLO and rendezvous loss observability."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import activity_segments as activity


BASELINE_JSON = (
    Path(__file__).resolve().parents[3]
    / "benchmarks"
    / "mission-vs-goal"
    / "results"
    / "2026-08-13-lane-slo-baseline.json"
)


def _state_root(tmp_path: Path) -> Path:
    root = tmp_path / ".mission-state"
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    return root


def _write_session(
    tmp_path: Path,
    session_id: str,
    *,
    session_role: str,
    started_at: str,
    updated_at: str,
    phase: str = "executing",
    passes: bool = False,
    loop_active: bool = True,
    halt_reason: str = "",
    activity_segments: list[dict] | None = None,
    activity_rollup: dict | None = None,
) -> Path:
    state = {
        "mission": "lane duration test",
        "mission_id": "lane-slo",
        "session_id": session_id,
        "session_role": session_role,
        "phase": phase,
        "passes": passes,
        "loop_active": loop_active,
        "halt_reason": halt_reason,
        "started_at": started_at,
        "updated_at": updated_at,
        "project_root": str(tmp_path),
    }
    if activity_segments is not None:
        state["activity_segments"] = activity_segments
    if activity_rollup is not None:
        state["activity_rollup"] = activity_rollup
    path = _state_root(tmp_path) / "sessions" / f"{session_id}.json"
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return path


def _write_archive_session(tmp_path: Path, session_id: str) -> Path:
    archive = tmp_path / ".mission-state" / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    path = archive / f"state-{session_id}.json"
    path.write_text(
        json.dumps(
            {
                "mission": "lane duration test",
                "mission_id": "lane-slo",
                "session_id": session_id,
                "session_role": "implementer",
                "phase": "done",
                "passes": True,
                "loop_active": False,
                "halt_reason": "",
                "started_at": "2026-08-13T00:00:00Z",
                "updated_at": "2026-08-13T00:10:00Z",
                "project_root": str(tmp_path),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def test_subagent_wait_kind_accepted_and_rolled_up():
    activity.validate_activity("subagent-wait", "checker-evidence")
    state = {
        "activity_segments": [
            {
                "kind": "subagent-wait",
                "reason": "checker-evidence",
                "phase": "reviewing",
                "started_at": "2026-08-13T00:00:00Z",
                "ended_at": "2026-08-13T00:10:00Z",
                "duration_sec": 600.0,
            }
        ]
    }
    summary = activity.summarize_activity_states([state])

    assert summary["activity_duration_totals_sec"]["subagent-wait"] == 600.0
    assert summary["wait_reason_totals_sec"]["subagent-wait"]["checker-evidence"] == 600.0


def test_subagent_wait_unknown_reason_rejected():
    with pytest.raises(activity.ActivityTimingError):
        activity.validate_activity("subagent-wait", "bogus")


def test_lane_report_groups_by_session_role(tmp_path, run_cli):
    _write_session(
        tmp_path,
        "impl",
        session_role="implementer",
        started_at="2026-08-13T00:00:00Z",
        updated_at="2026-08-13T00:10:00Z",
        activity_segments=[
            {
                "kind": "active",
                "reason": "implementation",
                "phase": "executing",
                "started_at": "2026-08-13T00:00:00Z",
                "ended_at": "2026-08-13T00:05:00Z",
                "duration_sec": 300.0,
            }
        ],
    )
    _write_session(
        tmp_path,
        "checker",
        session_role="checker",
        started_at="2026-08-13T00:00:00Z",
        updated_at="2026-08-13T00:05:00Z",
        activity_segments=[
            {
                "kind": "active",
                "reason": "work",
                "phase": "reviewing",
                "started_at": "2026-08-13T00:00:00Z",
                "ended_at": "2026-08-13T00:02:32Z",
                "duration_sec": 152.0,
            }
        ],
    )
    _write_archive_session(tmp_path, "archived")

    result = run_cli("lane-report", "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [session["session_id"] for session in payload["sessions"]] == ["checker", "impl"]
    assert payload["role_summaries"]["implementer"]["observed_active_sec"] == 300.0
    assert payload["role_summaries"]["checker"]["observed_active_sec"] == 152.0
    assert "archived" not in {session["session_id"] for session in payload["sessions"]}


def test_lane_report_rendezvous_loss_computed(tmp_path, run_cli):
    _write_session(
        tmp_path,
        "impl",
        session_role="implementer",
        phase="done",
        passes=True,
        loop_active=False,
        halt_reason="",
        started_at="2026-08-13T00:00:00Z",
        updated_at="2026-08-13T00:20:00Z",
        activity_segments=[
            {
                "kind": "active",
                "reason": "implementation",
                "phase": "executing",
                "started_at": "2026-08-13T00:00:00Z",
                "ended_at": "2026-08-13T00:05:00Z",
                "duration_sec": 300.0,
            },
            {
                "kind": "subagent-wait",
                "reason": "implementation-provider",
                "phase": "executing",
                "started_at": "2026-08-13T00:05:00Z",
                "ended_at": "2026-08-13T00:10:00Z",
                "duration_sec": 300.0,
            },
        ],
    )
    _write_session(
        tmp_path,
        "checker",
        session_role="checker",
        phase="done",
        passes=False,
        loop_active=False,
        halt_reason="done",
        started_at="2026-08-13T00:00:00Z",
        updated_at="2026-08-13T00:10:00Z",
        activity_segments=[
            {
                "kind": "active",
                "reason": "work",
                "phase": "reviewing",
                "started_at": "2026-08-13T00:00:00Z",
                "ended_at": "2026-08-13T00:02:32Z",
                "duration_sec": 152.0,
            }
        ],
    )

    result = run_cli("lane-report", "--json", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    implementer = next(session for session in payload["sessions"] if session["session_id"] == "impl")
    assert implementer["rendezvous_loss_sec"] == 148.0
    assert payload["rendezvous_loss_sec"] == 148.0


def test_lane_report_slo_breach_detection(tmp_path, run_cli):
    _write_session(
        tmp_path,
        "impl",
        session_role="implementer",
        phase="done",
        passes=True,
        loop_active=False,
        halt_reason="",
        started_at="2026-08-13T00:00:00Z",
        updated_at="2026-08-13T00:16:01Z",
        activity_segments=[
            {
                "kind": "active",
                "reason": "implementation",
                "phase": "executing",
                "started_at": "2026-08-13T00:00:00Z",
                "ended_at": "2026-08-13T00:10:00Z",
                "duration_sec": 600.0,
            }
        ],
    )
    result = run_cli("lane-report", "--json", "--slo-minutes", "15", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["sessions"][0]["slo_breached"] is True


def test_lane_report_slo_within_budget(tmp_path, run_cli):
    _write_session(
        tmp_path,
        "impl",
        session_role="implementer",
        phase="done",
        passes=True,
        loop_active=False,
        halt_reason="",
        started_at="2026-08-13T00:00:00Z",
        updated_at="2026-08-13T00:14:59Z",
        activity_segments=[
            {
                "kind": "active",
                "reason": "implementation",
                "phase": "executing",
                "started_at": "2026-08-13T00:00:00Z",
                "ended_at": "2026-08-13T00:10:00Z",
                "duration_sec": 600.0,
            }
        ],
    )
    result = run_cli("lane-report", "--json", "--slo-minutes", "15", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["sessions"][0]["slo_breached"] is False


def test_lane_report_without_state_dir_exits_1(tmp_path, run_cli):
    result = run_cli("lane-report", "--json", cwd=tmp_path)

    assert result.returncode == 1
    assert "lane-report requires at least one mission state" in result.stderr


def test_baseline_json_schema_valid():
    payload = json.loads(BASELINE_JSON.read_text(encoding="utf-8"))
    assert payload["schema"] == "mission-lane-slo-baseline/1"
    assert payload["source"] == "実運用ログ観測 (2026-08-12/13)"
    assert payload["cc_full_tier_wall_sec"] == 2220
    assert payload["codex_planning_sec"] == 1021
    assert payload["checker_wall_sec"] == 678
    assert payload["checker_active_sec"] == 152
    assert payload["rendezvous_loss_sec"] == 526
