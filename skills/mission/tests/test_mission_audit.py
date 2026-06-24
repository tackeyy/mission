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


def test_audit_deduplicates_nested_worktree_archive_sessions(tmp_path):
    sessions = tmp_path / ".mission-state" / "sessions"
    archive_sessions = tmp_path / ".mission-state" / "archive" / "worktree-feat" / "sessions"
    _write_state(sessions / "sess-a.json", project_root=str(tmp_path))
    _write_state(archive_sessions / "sess-a.json", project_root=str(tmp_path))

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


def test_audit_discovers_nested_worktree_archive_sessions(tmp_path):
    archive_sessions = tmp_path / ".mission-state" / "archive" / "worktree-feat" / "sessions"
    _write_state(
        archive_sessions / "sess-a.json",
        project_root=str(tmp_path),
        session_id="nested-archive",
    )

    result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--since", "2026-06-18", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["total_sessions"] == 1
    assert data["pass_count"] == 1


def test_audit_dedupe_prefers_pass_record_over_stale_halt(tmp_path):
    sessions = tmp_path / ".mission-state" / "sessions"
    archive = tmp_path / ".mission-state" / "archive" / "worktree-feat"
    _write_state(
        sessions / "sess-a.json",
        project_root=str(tmp_path),
        session_id="sess-a",
        passes=False,
        halt_reason="stale orphan pid 123 dead",
        score_history=[],
        updated_at="2026-06-18T00:05:00Z",
    )
    _write_state(
        archive / "state.json",
        project_root=str(tmp_path),
        session_id="sess-a",
        passes=True,
        halt_reason="",
        updated_at="2026-06-18T00:10:00Z",
    )

    result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--since", "2026-06-18", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["total_sessions"] == 1
    assert data["pass_count"] == 1
    assert data["halt_count"] == 0
    assert data["duplicate_group_count"] == 0


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


def test_audit_pass_rate_excludes_active_no_score_sessions(tmp_path):
    _write_state(
        tmp_path / "passed" / ".mission-state" / "sessions" / "passed.json",
        project_root=str(tmp_path / "passed"),
        session_id="passed",
    )
    _write_state(
        tmp_path / "active" / ".mission-state" / "sessions" / "active.json",
        project_root=str(tmp_path / "active"),
        passes=False,
        loop_active=True,
        score_history=[],
        halt_reason="",
        session_id="active-no-score",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(MISSION_AUDIT_PY),
            "--root",
            str(tmp_path),
            "--since",
            "2026-06-18",
            "--min-pass-rate",
            "0.9",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)
    assert data["total_sessions"] == 2
    assert data["active_no_score_checkpoint_count"] == 1
    assert data["pass_rate_denominator"] == 1
    assert data["pass_rate"] == 1.0
    assert all(f["code"] != "low-pass-rate" for f in data["findings"])


def test_audit_reports_missing_scoring_evidence_in_json(tmp_path):
    _write_state(
        tmp_path / "missing" / ".mission-state" / "sessions" / "missing.json",
        project_root=str(tmp_path / "missing"),
        mission_id="deadbeefcafebabe",
        session_id="missing-evidence",
    )
    _write_state(
        tmp_path / "present" / ".mission-state" / "sessions" / "present.json",
        project_root=str(tmp_path / "present"),
        mission_id="feedfacecafebabe",
        session_id="has-evidence",
    )
    evidence = tmp_path / "present" / ".mission-state" / "archive" / "iter-1-feedface-scoring.md"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("# scoring evidence\n", encoding="utf-8")

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
    assert data["missing_scoring_evidence_count"] == 1
    assert data["missing_scoring_evidence"][0]["session_id"] == "missing-evidence"
    assert data["missing_scoring_evidence"][0]["missing_iterations"] == [1]
    assert any(f["code"] == "missing-scoring-evidence" for f in data["findings"])


def test_audit_slow_session_buckets_track_phase_duration_observability(tmp_path):
    _write_state(
        tmp_path / "without-phases" / ".mission-state" / "sessions" / "slow.json",
        project_root=str(tmp_path / "without-phases"),
        started_at="2026-06-18T00:00:00Z",
        updated_at="2026-06-18T01:00:00Z",
        session_id="slow-without-phases",
    )
    _write_state(
        tmp_path / "with-phases" / ".mission-state" / "sessions" / "slow.json",
        project_root=str(tmp_path / "with-phases"),
        started_at="2026-06-18T00:00:00Z",
        updated_at="2026-06-18T01:00:00Z",
        session_id="slow-with-phases",
        phase_durations_sec={"planning": 900, "execution": 2700},
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
    assert data["slow_session_breakdown"]["healthy-long-pass"] == 2
    assert "slow-without-phase-durations" not in data["slow_session_breakdown"]
    assert "slow-with-phase-durations" not in data["slow_session_breakdown"]
    assert data["slow_phase_duration_breakdown"]["slow-without-phase-durations"] == 1
    assert data["slow_phase_duration_breakdown"]["slow-with-phase-durations"] == 1


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
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        task_profile={"primary": "documentation"},
        specialists_decision={"policy": "auto"},
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
    assert data["missing_specialist_selection_checkpoint_count"] == 0
    assert any(f["code"] == "specialist-invocation-gap" for f in data["findings"])


def test_audit_accepts_completed_specialist_invocation(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        task_profile={"primary": "documentation"},
        specialists_decision={"policy": "auto"},
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
    assert data["missing_specialist_selection_checkpoint_count"] == 0
    assert all(f["code"] != "specialist-invocation-gap" for f in data["findings"])


def test_audit_reports_unselected_specialist_invocation(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        task_profile={"primary": "documentation"},
        specialists_decision={"policy": "auto"},
        specialists_selected=[],
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
    assert data["unselected_specialist_invocation_count"] == 1
    assert data["unselected_specialist_invocations"][0]["skills"] == ["dev-doc-writer"]
    assert data["unselected_specialist_invocation_breakdown"]["dev-doc-writer"] == 1
    assert data["specialist_invocation_gap_count"] == 0
    assert any(f["code"] == "unselected-specialist-invocation" for f in data["findings"])


def test_audit_accepts_explicit_specialist_selection_metadata(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        task_profile={"primary": "documentation"},
        specialists_decision={"policy": "auto"},
        specialists_selected=[
            {
                "role": "doc-writer",
                "skill": "dev-doc-writer",
                "status": "selected",
                "selection_source": "user-instruction",
            },
        ],
        specialist_invocations=[
            {
                "skill": "dev-doc-writer",
                "status": "inline-applied",
                "mode": "codex-inline",
                "selection_source": "user-instruction",
            },
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
    assert data["unselected_specialist_invocation_count"] == 0
    assert data["specialist_invocation_gap_count"] == 0
    assert all(f["code"] != "unselected-specialist-invocation" for f in data["findings"])


def test_audit_reports_candidate_only_specialists(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        complexity="Critical",
        task_profile={"primary": "security", "risk": "high"},
        specialists_decision={"policy": "interactive"},
        specialists_candidates=[
            {"role": "security-reviewer", "skill": "dev-security-reviewer", "status": "available"},
            {"role": "unit-tester", "skill": "dev-unit-tester", "status": "available"},
        ],
        specialists_selected=[],
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
    assert data["candidate_only_specialist_count"] == 1
    assert data["candidate_only_specialists"][0]["session_id"] == "sess-a"
    assert data["candidate_only_specialists"][0]["priority"] == "P1"
    assert data["candidate_only_specialists"][0]["candidate_count"] == 2
    assert data["candidate_only_specialists"][0]["skills"] == ["dev-security-reviewer", "dev-unit-tester"]
    assert data["candidate_only_specialist_breakdown"][str(tmp_path.name)] == 1
    assert data["candidate_only_specialist_skill_breakdown"]["dev-security-reviewer"] == 1
    assert any(f["code"] == "candidate-only-specialists" and f["priority"] == "P1" for f in data["findings"])


def test_audit_does_not_report_candidate_only_when_specialist_selected(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        task_profile={"primary": "documentation"},
        specialists_decision={"policy": "auto"},
        specialists_candidates=[
            {"role": "doc-writer", "skill": "dev-doc-writer", "status": "available"},
        ],
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
    assert data["candidate_only_specialist_count"] == 0
    assert any(f["code"] == "specialist-invocation-gap" for f in data["findings"])
    assert all(f["code"] != "candidate-only-specialists" for f in data["findings"])


def test_audit_does_not_report_candidate_only_with_explicit_skip(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        task_profile={"primary": "documentation"},
        specialists_decision={"policy": "auto"},
        specialists_candidates=[
            {"role": "doc-writer", "skill": "dev-doc-writer", "status": "available"},
        ],
        specialists_selected=[],
        specialist_invocations=[
            {"skill": "dev-doc-writer", "status": "skipped", "mode": "codex-inline", "reason": "not needed"},
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
    assert data["candidate_only_specialist_count"] == 0
    assert data["unselected_specialist_invocation_count"] == 0
    assert all(f["code"] != "candidate-only-specialists" for f in data["findings"])


def test_audit_reports_unaccounted_high_risk_candidates_after_partial_skip(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        complexity="Critical",
        task_profile={"primary": "security", "secondary": ["testing"], "risk": "high"},
        specialists_decision={"policy": "interactive"},
        specialists_candidates=[
            {"role": "security-reviewer", "skill": "dev-security-reviewer", "status": "available"},
            {"role": "unit-tester", "skill": "dev-unit-tester", "status": "available"},
        ],
        specialists_selected=[],
        specialist_invocations=[
            {
                "skill": "dev-security-reviewer",
                "status": "skipped",
                "mode": "fallback-core",
                "reason": "Core review covered the security checklist",
            },
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
    assert data["candidate_only_specialist_count"] == 1
    assert data["candidate_only_specialists"][0]["candidate_count"] == 1
    assert data["candidate_only_specialists"][0]["skills"] == ["dev-unit-tester"]
    assert data["candidate_only_specialist_skill_breakdown"]["dev-unit-tester"] == 1
    assert "dev-security-reviewer" not in data["candidate_only_specialist_skill_breakdown"]


def test_audit_ignores_non_risk_candidate_when_complex_risk_candidates_are_accounted(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        complexity="Complex",
        task_profile={"primary": "documentation", "secondary": ["testing", "infra"], "risk": "medium"},
        specialists_decision={"policy": "auto"},
        specialists_candidates=[
            {"role": "doc-writer", "skill": "dev-doc-writer", "task_profiles": ["documentation"], "status": "available"},
            {"role": "backend", "skill": "dev-backend", "task_profiles": ["backend", "database"], "status": "available"},
            {"role": "unit-tester", "skill": "dev-unit-tester", "task_profiles": ["testing", "backend"], "status": "available"},
            {"role": "infra", "skill": "dev-infra", "task_profiles": ["infra"], "status": "available"},
        ],
        specialists_selected=[
            {"role": "doc-writer", "skill": "dev-doc-writer", "status": "selected"},
        ],
        specialist_invocations=[
            {"skill": "dev-doc-writer", "status": "inline-applied", "mode": "codex-inline"},
            {"skill": "dev-unit-tester", "status": "skipped", "mode": "fallback-core", "reason": "focused pytest covers the change"},
            {"skill": "dev-infra", "status": "skipped", "mode": "fallback-core", "reason": "no deployment or CI behavior changed"},
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
    assert data["candidate_only_specialist_count"] == 0
    assert "dev-backend" not in data["candidate_only_specialist_skill_breakdown"]
    assert all(f["code"] != "candidate-only-specialists" for f in data["findings"])


def test_audit_reports_database_candidate_only_when_database_signal_is_strong(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        mission="Design schema migration candidate accounting",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        complexity="Complex",
        task_profile={"primary": "documentation", "secondary": ["database"], "risk": "medium"},
        planned_files=["db/schema.sql", "skills/mission/bin/mission-state.py"],
        specialists_decision={"policy": "auto"},
        specialists_candidates=[
            {"role": "doc-writer", "skill": "dev-doc-writer", "task_profiles": ["documentation"], "status": "available"},
            {"role": "backend", "skill": "dev-backend", "task_profiles": ["backend", "database"], "status": "available"},
        ],
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
    assert data["candidate_only_specialist_count"] == 1
    assert data["candidate_only_specialists"][0]["priority"] == "P1"
    assert data["candidate_only_specialists"][0]["skills"] == ["dev-backend"]


def test_audit_does_not_report_candidate_only_without_candidates(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        task_profile={"primary": "documentation"},
        specialists_decision={"policy": "auto"},
        specialists_candidates=[],
        specialists_selected=[],
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
    assert data["candidate_only_specialist_count"] == 0
    assert all(f["code"] != "candidate-only-specialists" for f in data["findings"])


def test_audit_reports_missing_specialist_selection_checkpoint_after_rollout(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        updated_at="2026-06-20T10:15:00Z",
        task_profile={},
        specialists_decision={},
        specialists_selected=[],
        specialist_invocations=[],
    )

    result = subprocess.run(
        [
            sys.executable,
            str(MISSION_AUDIT_PY),
            "--root",
            str(tmp_path),
            "--since",
            "2026-06-20",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)
    assert data["missing_specialist_selection_checkpoint_count"] == 1
    assert data["missing_specialist_selection_checkpoints"][0]["session_id"] == "sess-a"
    assert data["missing_specialist_selection_checkpoint_breakdown"][str(tmp_path.name)] == 1
    assert any(f["code"] == "missing-specialist-selection-checkpoint" for f in data["findings"])


def test_audit_does_not_report_legacy_missing_specialist_selection_checkpoint(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T09:59:00Z",
        created_at_session="2026-06-20T09:59:00Z",
        updated_at="2026-06-20T10:30:00Z",
        task_profile={},
        specialists_decision={},
        specialists_selected=[],
        specialist_invocations=[],
    )

    result = subprocess.run(
        [
            sys.executable,
            str(MISSION_AUDIT_PY),
            "--root",
            str(tmp_path),
            "--since",
            "2026-06-20",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)
    assert data["missing_specialist_selection_checkpoint_count"] == 0
    assert all(f["code"] != "missing-specialist-selection-checkpoint" for f in data["findings"])
