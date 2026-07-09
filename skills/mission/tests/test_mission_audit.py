"""scripts/mission-audit.py regression tests."""

import json
import os
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


def test_audit_deduplicates_archive_to_archive_copies(tmp_path):
    project_root = tmp_path / "worktree"
    repo_archive_sessions = tmp_path / "repo" / ".mission-state" / "archive" / "worktree-feat" / "sessions"
    worktree_archive = project_root / ".mission-state" / "archive"
    _write_state(
        repo_archive_sessions / "sess-a.json",
        project_root=str(project_root),
    )
    _write_state(
        worktree_archive / "state-sess-a-abc12345.json",
        project_root=str(project_root),
    )

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


def test_audit_reports_invalid_score_iteration(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "bad-iter.json",
        project_root=str(tmp_path),
        session_id="bad-iter",
        score_history=[
            {"iteration": 0, "composite": 4.5, "min_item": 4.0, "items": {}, "timestamp": "2026-06-18T00:05:00Z"}
        ],
    )

    result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--since", "2026-06-18", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["invalid_score_iteration_count"] == 1
    assert data["invalid_score_iterations"][0]["invalid_iterations"] == [0]
    assert data["missing_scoring_evidence_count"] == 0
    assert any(finding["code"] == "invalid-score-iteration" for finding in data["findings"])


def test_audit_reports_blank_specialist_invocation(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "blank-specialist.json",
        project_root=str(tmp_path),
        session_id="blank-specialist",
        specialist_invocations=[
            {
                "iteration": 1,
                "phase": "review",
                "role": "code-reviewer",
                "skill": " ",
                "mode": "fallback-core",
                "status": "skipped",
            }
        ],
    )

    result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--since", "2026-06-18", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["blank_specialist_invocation_count"] == 1
    assert data["blank_specialist_invocations"][0]["blank_count"] == 1
    assert any(finding["code"] == "blank-specialist-invocation" for finding in data["findings"])


def test_audit_reports_completed_command_provider_with_preparation_only_evidence(tmp_path):
    evidence = tmp_path / ".mission-state" / "archive" / "iter-1-abc12345-specialist-oracle-reviewer.md"
    evidence.parent.mkdir(parents=True)
    evidence.write_text(
        "<!-- mission-specialist-meta: status=completed -->\n"
        "# Oracle Browser Review Prepared\n\n"
        "To capture the oracle review as command-provider output, rerun with ORACLE_MISSION_WAIT_SECONDS=900.\n",
        encoding="utf-8",
    )
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "prepared-completed.json",
        project_root=str(tmp_path),
        session_id="prepared-completed",
        specialist_invocations=[
            {
                "iteration": 1,
                "phase": "review",
                "role": "oracle-reviewer",
                "skill": "oracle-reviewer",
                "mode": "command-provider",
                "status": "completed",
                "evidence_path": ".mission-state/archive/iter-1-abc12345-specialist-oracle-reviewer.md",
            }
        ],
    )

    result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--since", "2026-06-18", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["preparation_only_completed_provider_count"] == 1
    item = data["preparation_only_completed_providers"][0]
    assert item["session_id"] == "prepared-completed"
    assert item["bad_entries"][0]["skill"] == "oracle-reviewer"
    assert "Oracle Browser Review Prepared" in item["bad_entries"][0]["evidence"][0]["markers"]
    assert any(finding["code"] == "preparation-only-completed-provider" for finding in data["findings"])


def test_audit_reports_standard_audit_testing_candidate_only(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "standard-audit.json",
        mission="execution-log audit and self-improvement",
        project_root=str(tmp_path),
        session_id="standard-audit",
        complexity="Standard",
        task_profile={"primary": "maintenance", "signals": ["execution-log", "改善"]},
        specialists_candidates=[
            {
                "role": "testing reviewer",
                "skill": "dev-test-strategist",
                "kind": "skill",
                "task_profiles": ["testing"],
                "status": "available",
            }
        ],
        specialists_selected=[],
        specialist_invocations=[],
    )

    result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--since", "2026-06-18", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["candidate_only_specialist_count"] == 1
    assert data["candidate_only_specialists"][0]["priority"] == "P1"
    assert data["candidate_only_specialists"][0]["skills"] == ["dev-test-strategist"]
    assert any(finding["code"] == "candidate-only-specialists" and finding["priority"] == "P1" for finding in data["findings"])


def test_audit_current_since_splits_historical_debt(tmp_path):
    _write_state(
        tmp_path / "old" / ".mission-state" / "sessions" / "old.json",
        mission="execution-log audit and self-improvement",
        project_root=str(tmp_path / "old"),
        session_id="old-candidate-only",
        updated_at="2026-06-25T23:59:00Z",
        complexity="Standard",
        task_profile={"primary": "maintenance", "signals": ["execution-log"]},
        specialists_candidates=[
            {"role": "unit tester", "skill": "unit-test-provider", "kind": "skill", "task_profiles": ["testing"], "status": "available"}
        ],
        specialists_selected=[],
        specialist_invocations=[],
    )
    _write_state(
        tmp_path / "new" / ".mission-state" / "sessions" / "new.json",
        mission="execution-log audit and self-improvement",
        project_root=str(tmp_path / "new"),
        session_id="new-candidate-only",
        updated_at="2026-06-26T00:01:00Z",
        complexity="Standard",
        task_profile={"primary": "maintenance", "signals": ["execution-log"]},
        specialists_candidates=[
            {"role": "unit tester", "skill": "unit-test-provider", "kind": "skill", "task_profiles": ["testing"], "status": "available"}
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
            "2026-06-25",
            "--current-since",
            "2026-06-26",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["candidate_only_specialist_count"] == 2
    assert data["current_candidate_only_specialist_count"] == 1
    assert data["historical_candidate_only_specialist_count"] == 1
    assert data["current_candidate_only_specialists"][0]["session_id"] == "new-candidate-only"
    assert data["historical_candidate_only_specialists"][0]["session_id"] == "old-candidate-only"
    assert any(
        finding["code"] == "candidate-only-specialists"
        and "1 current sessions" in finding["summary"]
        for finding in data["findings"]
    )
    assert any(finding["code"] == "historical-fixed-debt" for finding in data["findings"])


def test_audit_since_accepts_iso_timestamp(tmp_path):
    _write_state(
        tmp_path / "before" / ".mission-state" / "sessions" / "before.json",
        project_root=str(tmp_path / "before"),
        session_id="before-cutoff",
        updated_at="2026-07-06T03:00:52Z",
    )
    _write_state(
        tmp_path / "after" / ".mission-state" / "sessions" / "after.json",
        project_root=str(tmp_path / "after"),
        session_id="after-cutoff",
        updated_at="2026-07-06T19:58:45Z",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(MISSION_AUDIT_PY),
            "--root",
            str(tmp_path),
            "--since",
            "2026-07-06T03:00:53.439Z",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["total_sessions"] == 1
    assert data["by_project"]["after"]["total"] == 1
    assert "before" not in data["by_project"]


def test_audit_current_since_keeps_historical_debt_out_of_blocking_findings(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "old.json",
        project_root=str(tmp_path),
        session_id="old-bad-iter",
        updated_at="2026-06-25T23:59:00Z",
        score_history=[
            {"iteration": 0, "composite": 4.5, "min_item": 4.0, "items": {}, "timestamp": "2026-06-25T23:58:00Z"}
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            str(MISSION_AUDIT_PY),
            "--root",
            str(tmp_path),
            "--since",
            "2026-06-25",
            "--current-since",
            "2026-06-26",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["invalid_score_iteration_count"] == 1
    assert data["current_invalid_score_iteration_count"] == 0
    assert data["historical_invalid_score_iteration_count"] == 1
    assert all(finding["code"] != "invalid-score-iteration" for finding in data["findings"])
    assert any(finding["code"] == "historical-fixed-debt" for finding in data["findings"])


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
        updated_at="2026-06-18T00:10:00Z",
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
        env={**os.environ, "MISSION_AUDIT_NOW": "2026-06-18T00:15:00Z"},
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


def test_audit_ignores_worktree_archive_aggregate_json(tmp_path):
    _write_state(
        tmp_path / "passed" / ".mission-state" / "sessions" / "passed.json",
        project_root=str(tmp_path / "passed"),
        session_id="passed",
    )
    aggregate = (
        tmp_path
        / "repo"
        / ".mission-state"
        / "archive"
        / "worktree-feature"
        / "aggregate.json"
    )
    aggregate.parent.mkdir(parents=True, exist_ok=True)
    aggregate.write_text(
        json.dumps({"active_sessions": [], "updated_at": "2026-06-18T00:10:00Z"}),
        encoding="utf-8",
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
    assert data["total_sessions"] == 1
    assert data["pass_count"] == 1
    assert data["abandoned_count"] == 0
    assert data["pass_rate"] == 1.0
    assert "unknown" not in data["by_project"]
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


def test_audit_accepts_worktree_iteration_archive_scoring_evidence(tmp_path):
    project_root = tmp_path / "worktree"
    archive_root = tmp_path / "repo" / ".mission-state" / "archive" / "worktree-neutral"
    _write_state(
        archive_root / "state.json",
        project_root=str(project_root),
        mission_id="feedfacecafebabe",
        session_id="has-iteration-archive-evidence",
    )
    evidence = archive_root / "iteration-archive" / "iter-1-feedface-scoring.md"
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
    assert data["missing_scoring_evidence_count"] == 0
    assert all(f["code"] != "missing-scoring-evidence" for f in data["findings"])


def test_audit_accepts_worktree_mission_archive_scoring_evidence(tmp_path):
    project_root = tmp_path / "removed-worktree"
    archive_root = tmp_path / "repo" / ".mission-state" / "archive" / "worktree-neutral"
    _write_state(
        archive_root / "state.json",
        project_root=str(project_root),
        mission_id="feedfacecafebabe",
        session_id="has-mission-archive-evidence",
        score_history=[
            {
                "iteration": 1,
                "composite": 4.6,
                "min_item": 4.4,
                "items": {},
                "timestamp": "2026-07-04T00:00:00Z",
                "score_source": "scoring-json",
                "scoring_evidence_path": str(
                    project_root / ".mission-state" / "archive" / "iter-1-feedface-scoring.json"
                ),
            }
        ],
    )
    evidence = archive_root / "mission-archive" / "iter-1-feedface-scoring.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text('{"composite": 4.6}\n', encoding="utf-8")

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
    assert data["missing_scoring_evidence_count"] == 0
    assert all(f["code"] != "missing-scoring-evidence" for f in data["findings"])


def test_audit_accepts_explicit_scoring_evidence_json_path(tmp_path):
    evidence = tmp_path / "evidence" / "score.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"composite": 4.6}\n', encoding="utf-8")
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "json-evidence.json",
        project_root=str(tmp_path),
        mission_id="feedfacecafebabe",
        session_id="json-evidence",
        score_history=[
            {
                "iteration": 1,
                "composite": 4.6,
                "min_item": 4.0,
                "items": {},
                "timestamp": "2026-06-18T00:05:00Z",
                "scoring_evidence_path": str(evidence),
            }
        ],
    )

    result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--since", "2026-06-18", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["missing_scoring_evidence_count"] == 0


def test_audit_accepts_worktree_archive_json_scoring_evidence(tmp_path):
    project_root = tmp_path / "worktree"
    archive_root = tmp_path / "repo" / ".mission-state" / "archive" / "worktree-neutral"
    _write_state(
        archive_root / "sessions" / "archived.json",
        project_root=str(project_root),
        mission_id="feedfacecafebabe",
        session_id="archived-json-evidence",
    )
    evidence = archive_root / "archive" / "iter-1-feedface-scoring.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text('{"composite": 4.5}\n', encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--since", "2026-06-18", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["missing_scoring_evidence_count"] == 0


def test_audit_treats_fresh_active_no_score_as_pending_not_debt(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "fresh.json",
        project_root=str(tmp_path),
        session_id="fresh",
        passes=False,
        loop_active=True,
        score_history=[],
        halt_reason="",
        started_at="2026-07-03T00:00:00Z",
        created_at_session="2026-07-03T00:00:00Z",
        updated_at="2026-07-03T00:10:00Z",
        task_profile={},
        specialists_decision={},
        specialists_candidates=[
            {"role": "doc-writer", "skill": "documentation-provider", "status": "available", "task_profiles": ["documentation"]},
        ],
        specialists_selected=[
            {"role": "doc-writer", "skill": "documentation-provider", "status": "selected"},
        ],
        specialist_invocations=[],
    )
    env = {**os.environ, "MISSION_AUDIT_NOW": "2026-07-03T00:15:00Z", "MISSION_AUDIT_ACTIVE_PENDING_SECONDS": "1800"}

    result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--since", "2026-07-03", "--json"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    data = json.loads(result.stdout)
    assert data["active_no_score_pending_count"] == 1
    assert data["stale_active_no_score_count"] == 0
    assert data["missing_specialist_selection_checkpoint_count"] == 0
    assert data["specialist_invocation_gap_count"] == 0
    assert data["candidate_only_specialist_count"] == 0


def test_audit_reports_stale_active_no_score_as_actionable(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "stale.json",
        project_root=str(tmp_path),
        session_id="stale",
        passes=False,
        loop_active=True,
        score_history=[],
        halt_reason="",
        started_at="2026-07-03T00:00:00Z",
        created_at_session="2026-07-03T00:00:00Z",
        updated_at="2026-07-03T00:00:00Z",
        task_profile={},
        specialists_decision={},
        specialists_candidates=[
            {"role": "doc-writer", "skill": "documentation-provider", "status": "available", "task_profiles": ["documentation"]},
        ],
        specialists_selected=[
            {"role": "doc-writer", "skill": "documentation-provider", "status": "selected"},
        ],
        specialist_invocations=[],
    )
    env = {**os.environ, "MISSION_AUDIT_NOW": "2026-07-03T05:00:00Z", "MISSION_STALE_ACTIVE_SECONDS": "3600"}

    result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(tmp_path), "--since", "2026-07-03", "--json"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    data = json.loads(result.stdout)
    assert data["stale_active_no_score_count"] == 1
    assert data["active_no_score_pending_count"] == 0
    assert data["missing_specialist_selection_checkpoint_count"] == 1
    assert data["specialist_invocation_gap_count"] == 1
    assert data["halt_incomplete_breakdown"]["stale-active-live-pid"] == 1
    assert any(f["code"] == "stale-active-no-score" for f in data["findings"])


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
    assert data["coarse_phase_attribution_count"] == 0


def test_audit_reports_coarse_phase_attribution(tmp_path):
    _write_state(
        tmp_path / "coarse" / ".mission-state" / "sessions" / "slow.json",
        project_root=str(tmp_path / "coarse"),
        started_at="2026-06-18T00:00:00Z",
        updated_at="2026-06-18T01:00:00Z",
        session_id="coarse-planning",
        phase_durations_sec={"planning": 3500, "scoring": 5},
    )
    _write_state(
        tmp_path / "granular" / ".mission-state" / "sessions" / "slow.json",
        project_root=str(tmp_path / "granular"),
        started_at="2026-06-18T00:00:00Z",
        updated_at="2026-06-18T01:00:00Z",
        session_id="granular",
        phase_durations_sec={"planning": 1200, "executing": 1500, "review": 800, "scoring": 100},
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
    assert data["coarse_phase_attribution_count"] == 1
    assert data["coarse_phase_attributions"][0]["session_id"] == "coarse-planning"
    assert data["slow_phase_duration_breakdown"]["slow-with-coarse-phase-attribution"] == 1
    assert data["slow_phase_duration_breakdown"]["slow-with-phase-durations"] == 1
    assert any(f["code"] == "coarse-phase-attribution" for f in data["findings"])


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
    assert "open/closed issue" in result.stdout
    assert "重複確認" in result.stdout
    assert "development/tech-lead review" in result.stdout
    assert "OSS portability" in result.stdout


def test_audit_reports_selected_specialist_without_invocation(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        task_profile={"primary": "documentation"},
        specialists_decision={"policy": "auto"},
        specialists_selected=[
            {"role": "doc-writer", "skill": "documentation-provider", "status": "selected"},
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
    assert data["specialist_invocation_gap_breakdown"]["documentation-provider"] == 1
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
            {"role": "doc-writer", "skill": "documentation-provider", "status": "selected"},
        ],
        specialist_invocations=[
            {"skill": "documentation-provider", "status": "inline-applied", "mode": "codex-inline"},
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


def test_audit_accepts_task_required_source_retrieval_provider(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "source-retrieval.json",
        started_at="2026-07-03T00:00:00Z",
        created_at_session="2026-07-03T00:00:00Z",
        task_profile={"primary": "research"},
        specialists_decision={"policy": "task-required", "reason": "source retrieval required by task"},
        specialists_selected=[
            {
                "role": "source-retrieval",
                "skill": "source-retrieval-provider",
                "status": "selected",
                "selection_source": "task-required",
            },
        ],
        specialist_invocations=[
            {
                "skill": "source-retrieval-provider",
                "role": "source-retrieval",
                "status": "inline-applied",
                "mode": "codex-inline",
                "selection_source": "task-required",
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
            "2026-07-03",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)
    assert data["specialist_invocation_gap_count"] == 0
    assert data["unselected_specialist_invocation_count"] == 0
    assert data["missing_specialist_selection_checkpoint_count"] == 0


def test_audit_reports_unselected_specialist_invocation(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        task_profile={"primary": "documentation"},
        specialists_decision={"policy": "auto"},
        specialists_selected=[],
        specialist_invocations=[
            {"skill": "documentation-provider", "status": "inline-applied", "mode": "codex-inline"},
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
    assert data["unselected_specialist_invocations"][0]["skills"] == ["documentation-provider"]
    assert data["unselected_specialist_invocation_breakdown"]["documentation-provider"] == 1
    assert data["specialist_invocation_gap_count"] == 0
    assert any(f["code"] == "unselected-specialist-invocation" for f in data["findings"])


def test_audit_treats_phase_plan_providers_as_selected(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        task_profile={"primary": "backend"},
        specialists_decision={"policy": "auto", "action": "select"},
        specialists_selected=[
            {"skill": "documentation-provider", "selection_source": "auto"},
        ],
        specialists_phase_plan=[
            {
                "phase": "execution",
                "providers": ["integration-test-provider"],
                "roles": ["integration-test"],
                "max_providers": 1,
            },
            {
                "phase": "review",
                "providers": ["code-review-provider"],
                "roles": ["code-review"],
                "max_providers": 1,
            },
        ],
        specialist_invocations=[
            {"skill": "documentation-provider", "status": "completed", "mode": "codex-inline"},
            {"skill": "integration-test-provider", "status": "completed", "mode": "codex-inline"},
            {"skill": "code-review-provider", "status": "completed", "mode": "codex-inline"},
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


def test_audit_excludes_core_mission_invocations_from_unselected_specialists(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        task_profile={"primary": "documentation"},
        specialists_decision={"policy": "auto"},
        specialists_selected=[],
        specialist_invocations=[
            {"skill": "mission-planner", "status": "completed", "mode": "core-loop"},
            {"skill": "mission-executor", "status": "completed", "mode": "core-loop"},
            {"skill": "mission-reviewer", "status": "completed", "mode": "core-loop"},
            {"skill": "mission-core", "status": "completed", "mode": "core-loop"},
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
    assert all(f["code"] != "unselected-specialist-invocation" for f in data["findings"])


def test_audit_reports_only_external_invocation_when_core_and_external_are_mixed(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        task_profile={"primary": "documentation"},
        specialists_decision={"policy": "auto"},
        specialists_selected=[],
        specialist_invocations=[
            {"skill": "mission-reviewer", "status": "completed", "mode": "core-loop"},
            {"skill": "documentation-provider", "status": "inline-applied", "mode": "codex-inline"},
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
    assert data["unselected_specialist_invocations"][0]["skills"] == ["documentation-provider"]


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
                "skill": "documentation-provider",
                "status": "selected",
                "selection_source": "user-instruction",
            },
        ],
        specialist_invocations=[
            {
                "skill": "documentation-provider",
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


def test_audit_reports_unresolved_ask_user_specialist_confirmation(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        complexity="Complex",
        task_profile={"primary": "documentation", "risk": "high"},
        specialists_decision={"policy": "confirm", "action": "ask-user", "prompted_user": True},
        specialists_selected=[],
        specialist_invocations=[
            {"skill": "example-strategy-orchestrator", "status": "inline-applied", "mode": "codex-inline"},
            {"skill": "example-strategy-reviewer", "status": "completed", "mode": "natural-language"},
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
    assert data["unresolved_confirm_specialist_selection_count"] == 1
    assert data["unresolved_confirm_specialist_selections"][0]["skills"] == [
        "example-strategy-orchestrator",
        "example-strategy-reviewer",
    ]
    assert data["unselected_specialist_invocation_count"] == 0
    assert any(f["code"] == "unresolved-confirm-specialist-selection" for f in data["findings"])


def test_audit_accepts_confirmed_ask_user_specialist_selection(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        complexity="Complex",
        task_profile={"primary": "documentation", "risk": "high"},
        specialists_decision={"policy": "confirm", "action": "ask-user", "prompted_user": True},
        specialists_selected=[
            {
                "role": "strategy-partner",
                "skill": "example-strategy-orchestrator",
                "status": "selected",
                "selection_source": "confirmed-user",
            },
        ],
        specialist_invocations=[
            {
                "role": "strategy-partner",
                "skill": "example-strategy-orchestrator",
                "status": "inline-applied",
                "mode": "codex-inline",
                "selection_source": "confirmed-user",
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
    assert data["unresolved_confirm_specialist_selection_count"] == 0
    assert data["unselected_specialist_invocation_count"] == 0


def test_audit_reports_candidate_only_specialists(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        complexity="Critical",
        task_profile={"primary": "security", "risk": "high"},
        specialists_decision={"policy": "interactive"},
        specialists_candidates=[
            {"role": "security-reviewer", "skill": "security-review-provider", "status": "available"},
            {"role": "unit-tester", "skill": "unit-test-provider", "status": "available"},
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
    assert data["candidate_only_specialists"][0]["skills"] == ["security-review-provider", "unit-test-provider"]
    assert data["candidate_only_specialist_breakdown"][str(tmp_path.name)] == 1
    assert data["candidate_only_specialist_skill_breakdown"]["security-review-provider"] == 1
    assert any(f["code"] == "candidate-only-specialists" and f["priority"] == "P1" for f in data["findings"])


def test_audit_does_not_report_candidate_only_for_active_ask_user_wait(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        updated_at="2026-06-20T10:12:00Z",
        loop_active=True,
        passes=False,
        score_history=[],
        iteration=0,
        task_profile={"primary": "documentation"},
        specialists_decision={"policy": "interactive", "action": "ask-user", "prompted_user": True},
        specialists_candidates=[
            {"role": "doc-writer", "skill": "documentation-provider", "status": "available"},
            {"role": "document-review", "skill": "sc-document-reviewer", "status": "available"},
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
    assert data["candidate_only_specialist_count"] == 0
    assert all(f["code"] != "candidate-only-specialists" for f in data["findings"])


def test_audit_does_not_report_candidate_only_when_specialist_selected(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        task_profile={"primary": "documentation"},
        specialists_decision={"policy": "auto"},
        specialists_candidates=[
            {"role": "doc-writer", "skill": "documentation-provider", "status": "available"},
        ],
        specialists_selected=[
            {"role": "doc-writer", "skill": "documentation-provider", "status": "selected"},
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
            {"role": "doc-writer", "skill": "documentation-provider", "status": "available"},
        ],
        specialists_selected=[],
        specialist_invocations=[
            {"skill": "documentation-provider", "status": "skipped", "mode": "codex-inline", "reason": "not needed"},
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
            {"role": "security-reviewer", "skill": "security-review-provider", "status": "available"},
            {"role": "unit-tester", "skill": "unit-test-provider", "status": "available"},
        ],
        specialists_selected=[],
        specialist_invocations=[
            {
                "skill": "security-review-provider",
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
    assert data["candidate_only_specialists"][0]["skills"] == ["unit-test-provider"]
    assert data["candidate_only_specialist_skill_breakdown"]["unit-test-provider"] == 1
    assert "security-review-provider" not in data["candidate_only_specialist_skill_breakdown"]


def test_audit_ignores_non_risk_candidate_when_complex_risk_candidates_are_accounted(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "sess-a.json",
        started_at="2026-06-20T10:10:00Z",
        created_at_session="2026-06-20T10:10:00Z",
        complexity="Complex",
        task_profile={"primary": "documentation", "secondary": ["testing", "infra"], "risk": "medium"},
        specialists_decision={"policy": "auto"},
        specialists_candidates=[
            {"role": "doc-writer", "skill": "documentation-provider", "task_profiles": ["documentation"], "status": "available"},
            {"role": "backend", "skill": "backend-provider", "task_profiles": ["backend", "database"], "status": "available"},
            {"role": "unit-tester", "skill": "unit-test-provider", "task_profiles": ["testing", "backend"], "status": "available"},
            {"role": "infra", "skill": "infra-provider", "task_profiles": ["infra"], "status": "available"},
        ],
        specialists_selected=[
            {"role": "doc-writer", "skill": "documentation-provider", "status": "selected"},
        ],
        specialist_invocations=[
            {"skill": "documentation-provider", "status": "inline-applied", "mode": "codex-inline"},
            {"skill": "unit-test-provider", "status": "skipped", "mode": "fallback-core", "reason": "focused pytest covers the change"},
            {"skill": "infra-provider", "status": "skipped", "mode": "fallback-core", "reason": "no deployment or CI behavior changed"},
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
    assert "backend-provider" not in data["candidate_only_specialist_skill_breakdown"]
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
            {"role": "doc-writer", "skill": "documentation-provider", "task_profiles": ["documentation"], "status": "available"},
            {"role": "backend", "skill": "backend-provider", "task_profiles": ["backend", "database"], "status": "available"},
        ],
        specialists_selected=[
            {"role": "doc-writer", "skill": "documentation-provider", "status": "selected"},
        ],
        specialist_invocations=[
            {"skill": "documentation-provider", "status": "inline-applied", "mode": "codex-inline"},
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
    assert data["candidate_only_specialists"][0]["skills"] == ["backend-provider"]


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


def test_audit_does_not_require_specialist_selection_checkpoint_for_simple(tmp_path):
    _write_state(
        tmp_path / ".mission-state" / "sessions" / "simple.json",
        started_at="2026-07-03T00:00:00Z",
        created_at_session="2026-07-03T00:00:00Z",
        complexity="Simple",
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
            "2026-07-03",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)
    assert data["missing_specialist_selection_checkpoint_count"] == 0
    assert all(f["code"] != "missing-specialist-selection-checkpoint" for f in data["findings"])
