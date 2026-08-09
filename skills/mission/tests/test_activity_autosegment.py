"""Issue #382: automatic activity measurement at lifecycle boundaries."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))


def _read(tmp_path):
    return json.loads((tmp_path / ".mission-state" / "sessions" / "test.json").read_text())


def test_init_opens_the_planning_activity_by_default(run_cli, tmp_path):
    result = run_cli("init", "automatic activity", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    current = _read(tmp_path)["activity_current"]
    assert current == {
        "kind": "active",
        "origin": "phase-default",
        "phase": "planning",
        "reason": "planning",
        "started_at": current["started_at"],
    }


def test_advance_uses_the_destination_phase_default_when_activity_is_omitted(run_cli, tmp_path):
    run_cli("init", "automatic activity", cwd=tmp_path, check=True)

    result = run_cli("advance", "--phase", "executing", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert _read(tmp_path)["activity_current"]["reason"] == "implementation"


def test_same_mission_reinit_opens_missing_default_once_without_zero_length_duplicate(
    run_cli, tmp_path
):
    """A resumed state without an activity measurement gets one safe default."""
    initial_at = "2027-08-10T00:00:00Z"
    resumed_at = "2027-08-10T00:01:00Z"
    run_cli(
        "init", "automatic activity", cwd=tmp_path, check=True,
        env_extra={"MISSION_STATE_NOW": initial_at},
    )
    run_cli(
        "activity", "end", "--at", resumed_at, cwd=tmp_path, check=True,
        env_extra={"MISSION_STATE_NOW": resumed_at},
    )

    run_cli(
        "init", "automatic activity", cwd=tmp_path, check=True,
        env_extra={"MISSION_STATE_NOW": resumed_at},
    )
    after_resume = _read(tmp_path)
    assert after_resume["activity_current"] == {
        "kind": "active",
        "origin": "phase-default",
        "phase": "planning",
        "reason": "planning",
        "started_at": resumed_at,
    }
    closed_count = len(after_resume["activity_segments"])

    run_cli(
        "init", "automatic activity", cwd=tmp_path, check=True,
        env_extra={"MISSION_STATE_NOW": resumed_at},
    )
    repeated = _read(tmp_path)
    assert repeated["activity_current"] == after_resume["activity_current"]
    assert len(repeated["activity_segments"]) == closed_count


def test_same_mission_reinit_drops_unobserved_open_boundary_without_zero_segment(
    run_cli, tmp_path
):
    """A resume gap beginning at the open boundary is unobserved, not a zero sample."""
    started_at = "2027-08-10T00:00:00Z"
    resumed_at = "2027-08-10T00:10:00Z"
    run_cli(
        "init", "automatic activity", cwd=tmp_path, check=True,
        env_extra={"MISSION_STATE_NOW": started_at},
    )

    run_cli(
        "init", "automatic activity", cwd=tmp_path, check=True,
        env_extra={"MISSION_STATE_NOW": resumed_at},
    )
    resumed = _read(tmp_path)
    assert all(segment["duration_sec"] > 0 for segment in resumed["activity_segments"])
    assert resumed["activity_unobserved_gap_sec"] == 600.0
    assert resumed["activity_current"] == {
        "kind": "active",
        "origin": "phase-default",
        "phase": "planning",
        "reason": "planning",
        "started_at": resumed_at,
    }
    history_count = len(resumed["activity_segments"])

    run_cli(
        "init", "automatic activity", cwd=tmp_path, check=True,
        env_extra={"MISSION_STATE_NOW": resumed_at},
    )
    repeated = _read(tmp_path)
    assert repeated["activity_current"] == resumed["activity_current"]
    assert len(repeated["activity_segments"]) == history_count


def test_aggregate_reviews_transitions_reviewing_to_scoring_activity(run_cli, tmp_path):
    run_cli("init", "automatic activity", cwd=tmp_path, check=True)
    run_cli(
        "advance", "--phase", "reviewing", "--artifact-applicability", "not-applicable",
        cwd=tmp_path, check=True,
    )
    review = tmp_path / "review.json"
    review.write_text(json.dumps({
        "schema": "mission-review/1",
        "perspective": "neutral",
        "iteration": 1,
        "scores": {
            "mission_achievement": 4.3,
            "accuracy": 4.2,
            "completeness": 4.1,
            "usability": 4.0,
        },
        "findings": [],
        "same_score_note": None,
        "notes": "review",
    }), encoding="utf-8")

    result = run_cli("aggregate-reviews", "--iteration", "1", "--input", str(review), cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    state = _read(tmp_path)
    assert state["phase"] == "scoring"
    assert state["activity_current"]["reason"] == "scoring"


def test_resume_gap_keeps_a_reason_and_rollup_conserves_elapsed(run_cli, tmp_path):
    run_cli("init", "automatic activity", cwd=tmp_path, check=True)
    run_cli("activity", "start", "--kind", "active", "--reason", "work", "--at", "2027-08-10T00:00:00Z", cwd=tmp_path, check=True)
    run_cli("activity", "start", "--kind", "active", "--reason", "resumed-implementation", "--resume", "--at", "2027-08-10T00:10:00Z", cwd=tmp_path, check=True)

    state = _read(tmp_path)
    assert state["activity_unobserved_gap_reasons_sec"]["clock-gap"] >= 0.0


def test_activity_event_api_maps_approval_and_specialist_boundaries():
    from activity_segments import record_activity_event

    state = {"phase": "executing", "loop_active": True, "updated_at": "2027-08-10T00:00:00Z"}
    record_activity_event(state, "awaiting-approval", "2027-08-10T00:00:00Z")
    assert state["activity_current"]["kind"] == "approval-wait"

    record_activity_event(state, "specialist", "2027-08-10T00:01:00Z")
    assert state["activity_current"]["kind"] == "external-wait"


def test_resume_gap_classifies_legacy_crash_provider_and_clock_boundaries():
    from activity_segments import close_activity_for_resume, start_activity_segment

    cases = {
        "legacy": {"phase": "executing", "loop_active": True},
        "crash": {
            "phase": "executing", "loop_active": True,
            "updated_at": "2027-08-10T00:00:00Z",
            "activity_last_event_at": "2027-08-10T00:00:30Z",
            "activity_last_event_phase": "executing",
        },
        "provider-no-events": {
            "phase": "executing", "loop_active": True,
            "updated_at": "2027-08-10T00:00:00Z",
        },
        "clock-gap": {
            "phase": "executing", "loop_active": True,
            "updated_at": "2027-08-10T00:00:00Z",
        },
    }
    for expected, state in cases.items():
        kind = "external-wait" if expected == "provider-no-events" else "active"
        reason = "external-command" if expected == "provider-no-events" else "work"
        start_activity_segment(state, kind, reason, "2027-08-10T00:00:00Z")
        close_activity_for_resume(state, "2027-08-10T00:01:00Z")
        assert state["activity_unobserved_gap_reasons_sec"][expected] >= 0.0


def test_parallel_implementer_sessions_have_no_zero_activity_cohort(run_cli, tmp_path):
    for index in range(7):
        env = {"MISSION_SESSION_ID": f"parallel-{index}", "MISSION_LEASE_ID": f"lease-{index}"}
        run_cli("init", f"parallel {index}", cwd=tmp_path, check=True, env_extra=env)
        run_cli(
            "activity", "start", "--kind", "active", "--reason", "work",
            "--at", f"2027-08-10T00:0{index}:00Z", cwd=tmp_path, check=True, env_extra=env,
        )

    result = run_cli("stats", "--root", str(tmp_path), "--json", cwd=tmp_path, check=True)

    activity = json.loads(result.stdout)["activity_timing"]
    assert activity["states_without_activity_count"] == 0
    assert activity["coverage_ratio"] >= 0.90


def test_specialist_invocation_evidence_does_not_change_activity(run_cli, tmp_path):
    run_cli("init", "specialist boundary", cwd=tmp_path, check=True)

    result = run_cli(
        "specialists", "log-invocation", "--iteration", "0", "--phase", "planning",
        "--role", "evidence", "--skill", "neutral-provider", "--mode", "codex-inline",
        "--status", "completed", cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert _read(tmp_path)["activity_current"]["reason"] == "planning"


def test_gap_reason_tampering_is_invalid_and_never_counted():
    from activity_segments import summarize_activity_states

    state = {
        "phase": "done",
        "phase_durations_sec": {"executing": 10.0},
        "activity_unobserved_gap_sec": 10.0,
        "activity_unobserved_gap_reasons_sec": {"bogus": 999.0},
    }
    timing = summarize_activity_states([state])
    assert timing["invalid_segment_count"] >= 1
    assert timing["totals_consistent"] is False
    assert timing["unobserved_gap_reasons_sec"] == {}
