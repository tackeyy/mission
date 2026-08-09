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
        "phase": "planning",
        "reason": "planning",
        "started_at": current["started_at"],
    }


def test_advance_uses_the_destination_phase_default_when_activity_is_omitted(run_cli, tmp_path):
    run_cli("init", "automatic activity", cwd=tmp_path, check=True)

    result = run_cli("advance", "--phase", "executing", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert _read(tmp_path)["activity_current"]["reason"] == "implementation"


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


def test_specialist_invocation_records_the_common_activity_event(run_cli, tmp_path):
    run_cli("init", "specialist boundary", cwd=tmp_path, check=True)

    result = run_cli(
        "specialists", "log-invocation", "--iteration", "0", "--phase", "planning",
        "--role", "evidence", "--skill", "neutral-provider", "--mode", "codex-inline",
        "--status", "completed", cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert _read(tmp_path)["activity_current"]["reason"] == "external-command"
