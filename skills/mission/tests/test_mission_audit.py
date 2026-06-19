"""scripts/mission-audit.py regression tests."""

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
MISSION_AUDIT_PY = REPO_ROOT / "scripts" / "mission-audit.py"


def _write_state(path, **overrides):
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "mission": "audit test mission",
        "mission_id": "abc123456789",
        "complexity": "Standard",
        "iteration": 1,
        "threshold": 4.0,
        "score_history": [
            {"iteration": 1, "composite": 4.5, "min_item": 4.0, "items": {}, "timestamp": "2026-06-18T00:05:00Z"}
        ],
        "loop_active": False,
        "passes": True,
        "halt_reason": "",
        "started_at": "2026-06-18T00:00:00Z",
        "updated_at": "2026-06-18T00:10:00Z",
        "project_root": str(path.parents[2]),
        "session_id": "sess-a",
        "agent": "codex",
    }
    state.update(overrides)
    path.write_text(json.dumps(state), encoding="utf-8")


def test_audit_deduplicates_worktree_archive(tmp_path):
    sessions = tmp_path / ".mission-state" / "sessions"
    archive = tmp_path / ".mission-state" / "archive" / "worktree-feat"
    _write_state(sessions / "sess-a.json", project_root=str(tmp_path))
    _write_state(archive / "state.json", project_root=str(tmp_path))

    result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--since", "2026-06-18", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["total_sessions"] == 1
    assert data["duplicate_group_count"] == 0
    assert data["resolved_duplicate_group_count"] == 1
    assert data["pass_count"] == 1


def test_audit_does_not_dedupe_across_projects(tmp_path):
    _write_state(tmp_path / "p1" / ".mission-state" / "sessions" / "sess-a.json", project_root=str(tmp_path / "p1"))
    _write_state(tmp_path / "p2" / ".mission-state" / "sessions" / "sess-a.json", project_root=str(tmp_path / "p2"))

    result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--since", "2026-06-18", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["total_sessions"] == 2
    assert data["duplicate_group_count"] == 0
    assert data["resolved_duplicate_group_count"] == 0


def test_audit_keeps_unresolved_duplicate_groups(tmp_path):
    sessions = tmp_path / ".mission-state" / "sessions"
    _write_state(sessions / "sess-a.json", project_root=str(tmp_path))
    _write_state(
        sessions / "sess-a-copy.json",
        project_root=str(tmp_path),
        updated_at="2026-06-18T00:20:00Z",
    )

    result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--since", "2026-06-18", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["total_sessions"] == 1
    assert data["duplicate_group_count"] == 1
    assert data["resolved_duplicate_group_count"] == 0


def test_audit_reports_halt_incomplete_slow_and_low_score_buckets(tmp_path):
    _write_state(
        tmp_path / "halted" / ".mission-state" / "sessions" / "halted.json",
        project_root=str(tmp_path / "halted"),
        passes=False,
        halt_reason="orphan: pid 123 dead",
        score_history=[],
        session_id="halted",
    )
    _write_state(
        tmp_path / "incomplete" / ".mission-state" / "sessions" / "incomplete.json",
        project_root=str(tmp_path / "incomplete"),
        passes=False,
        loop_active=True,
        score_history=[],
        session_id="incomplete",
    )
    _write_state(
        tmp_path / "slow" / ".mission-state" / "sessions" / "slow.json",
        project_root=str(tmp_path / "slow"),
        started_at="2026-06-18T00:00:00Z",
        updated_at="2026-06-18T01:00:00Z",
        session_id="slow",
    )
    _write_state(
        tmp_path / "low" / ".mission-state" / "sessions" / "low.json",
        project_root=str(tmp_path / "low"),
        score_history=[
            {"iteration": 1, "composite": 4.1, "min_item": 3.8, "items": {}, "timestamp": "2026-06-18T00:05:00Z"}
        ],
        session_id="low",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(MISSION_AUDIT_PY),
            "--root",
            str(tmp_path),
            "--since",
            "2026-06-18",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["halt_incomplete_breakdown"]["stale-state-cleanup"] == 1
    assert data["halt_incomplete_breakdown"]["active-no-score-checkpoint"] == 1
    assert data["slow_session_breakdown"]["healthy-long-pass"] == 1
    assert data["low_score_pass_breakdown"]["valid-threshold-pass"] == 1


def test_audit_self_improvement_prompt_mentions_findings(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        passes=False,
        halt_reason="needs user confirmation",
        score_history=[],
    )
    result = subprocess.run(
        [
            sys.executable,
            str(MISSION_AUDIT_PY),
            "--root",
            str(tmp_path),
            "--since",
            "2026-06-18",
            "--self-improvement-prompt",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "/mission scripts/mission-audit.py" in result.stdout
    assert "`halted-runs`" in result.stdout


def test_audit_reports_selected_specialist_without_invocation(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        specialists_selected=[
            {"role": "doc-writer", "skill": "dev-doc-writer", "status": "selected"},
        ],
        specialist_invocations=[],
    )

    result = subprocess.run(
        [
            sys.executable,
            str(MISSION_AUDIT_PY),
            "--root",
            str(tmp_path),
            "--since",
            "2026-06-18",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)
    assert data["specialist_invocation_gap_count"] == 1
    assert data["specialist_invocation_gap_breakdown"]["dev-doc-writer"] == 1
    assert any(f["code"] == "specialist-invocation-gap" for f in data["findings"])


def test_audit_accepts_completed_specialist_invocation(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        specialists_selected=[
            {"role": "doc-writer", "skill": "dev-doc-writer", "status": "selected"},
        ],
        specialist_invocations=[
            {"skill": "dev-doc-writer", "status": "inline-applied", "mode": "codex-inline"},
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            str(MISSION_AUDIT_PY),
            "--root",
            str(tmp_path),
            "--since",
            "2026-06-18",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)
    assert data["specialist_invocation_gap_count"] == 0
    assert all(f["code"] != "specialist-invocation-gap" for f in data["findings"])
