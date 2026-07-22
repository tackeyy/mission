"""Explicit, audited reactivation for manually halted missions."""

import json


def _halt(run_cli, state_dir, *, category="awaiting-approval"):
    return run_cli(
        "mark-halt",
        "--reason",
        "external recovery requires approval",
        "--category",
        category,
        cwd=state_dir.parent,
        check=True,
    )


def test_reactivate_requires_explicit_user_approval(state_dir, run_cli, read_state):
    _halt(run_cli, state_dir)
    before = read_state(state_dir)

    result = run_cli(
        "reactivate",
        "--reason",
        "user approved the scoped recovery",
        "--expected-category",
        "awaiting-approval",
        cwd=state_dir.parent,
    )

    assert result.returncode == 2
    assert "--approved-by-user" in result.stderr
    assert read_state(state_dir) == before


def test_reactivate_atomically_restores_state_and_records_audit(
    state_dir, run_cli, read_state
):
    _halt(run_cli, state_dir, category="blocked-external")

    result = run_cli(
        "reactivate",
        "--approved-by-user",
        "--reason",
        "user confirmed the scoped external recovery",
        "--expected-category",
        "blocked-external",
        "--phase",
        "executing",
        cwd=state_dir.parent,
    )

    assert result.returncode == 0, result.stderr
    state = read_state(state_dir)
    assert state["loop_active"] is True
    assert state["passes"] is False
    assert state["phase"] == "executing"
    assert state["halt_reason"] == ""
    assert "halt_category" not in state
    assert state["activity_current"]["kind"] == "active"
    assert state["activity_current"]["reason"] == "resumed-implementation"
    assert state["activity_current"]["phase"] == "executing"
    audit = state["reactivation_history"][-1]
    assert audit["previous_halt_reason"] == "external recovery requires approval"
    assert audit["previous_halt_category"] == "blocked-external"
    assert audit["approved_reason"] == "user confirmed the scoped external recovery"
    assert audit["approved_by_user"] is True
    assert audit["target_phase"] == "executing"
    aggregate = json.loads((state_dir / "aggregate.json").read_text())
    assert "test" in aggregate["active_sessions"]


def test_reactivate_rejects_category_mismatch_without_mutation(
    state_dir, run_cli, read_state
):
    _halt(run_cli, state_dir, category="awaiting-approval")
    before = read_state(state_dir)

    result = run_cli(
        "reactivate",
        "--approved-by-user",
        "--reason",
        "user approved a different blocker",
        "--expected-category",
        "blocked-external",
        cwd=state_dir.parent,
    )

    assert result.returncode == 2
    assert "一致しません" in result.stderr
    assert read_state(state_dir) == before


def test_reactivate_rejects_stale_halt_and_routes_to_resume(
    state_dir, run_cli, read_state
):
    _halt(run_cli, state_dir, category="stale")
    before = read_state(state_dir)

    result = run_cli(
        "reactivate",
        "--approved-by-user",
        "--reason",
        "manual approval is unnecessary for stale ownership",
        "--expected-category",
        "stale",
        cwd=state_dir.parent,
    )

    assert result.returncode == 2
    assert "resume" in result.stderr
    assert read_state(state_dir) == before


def test_explicit_manual_category_wins_over_orphan_like_reason(
    state_dir, run_cli, read_state
):
    run_cli(
        "mark-halt",
        "--reason",
        "orphan: manually acknowledged",
        "--category",
        "other",
        cwd=state_dir.parent,
        check=True,
    )

    result = run_cli(
        "reactivate",
        "--approved-by-user",
        "--reason",
        "user approved this manual halt",
        "--expected-category",
        "other",
        cwd=state_dir.parent,
    )

    assert result.returncode == 0, result.stderr
    assert read_state(state_dir)["loop_active"] is True


def test_malformed_category_uses_unknown_confirmation_but_preserves_raw_audit(
    state_dir, run_cli, read_state
):
    _halt(run_cli, state_dir)
    path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["halt_category"] = "legacy-bogus"
    path.write_text(json.dumps(state))

    next_result = run_cli("next", cwd=state_dir.parent, check=True)
    assert "--expected-category unknown" in json.loads(next_result.stdout)["command_hint"]

    result = run_cli(
        "reactivate",
        "--approved-by-user",
        "--reason",
        "user approved legacy state recovery",
        "--expected-category",
        "unknown",
        cwd=state_dir.parent,
    )

    assert result.returncode == 0, result.stderr
    audit = read_state(state_dir)["reactivation_history"][-1]
    assert audit["previous_halt_category"] == "legacy-bogus"


def test_structured_malformed_category_does_not_crash_next_and_preserves_raw_audit(
    state_dir, run_cli, read_state
):
    _halt(run_cli, state_dir)
    path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["halt_category"] = {"legacy": "bogus"}
    path.write_text(json.dumps(state))

    next_result = run_cli("next", cwd=state_dir.parent)
    assert next_result.returncode == 0, next_result.stderr
    assert "--expected-category unknown" in json.loads(next_result.stdout)["command_hint"]

    result = run_cli(
        "reactivate",
        "--approved-by-user",
        "--reason",
        "user approved structured legacy state recovery",
        "--expected-category",
        "unknown",
        cwd=state_dir.parent,
    )

    assert result.returncode == 0, result.stderr
    audit = read_state(state_dir)["reactivation_history"][-1]
    assert audit["previous_halt_category"] == {"legacy": "bogus"}


def test_reactivate_closes_legacy_current_activity_before_starting_new_segment(
    state_dir, run_cli, read_state
):
    _halt(run_cli, state_dir)
    path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["updated_at"] = "2026-07-22T00:01:00Z"
    state["activity_current"] = {
        "kind": "active",
        "phase": "executing",
        "reason": "work",
        "started_at": "2026-07-22T00:00:00Z",
    }
    path.write_text(json.dumps(state))

    result = run_cli(
        "reactivate",
        "--approved-by-user",
        "--reason",
        "user approved recovery",
        "--expected-category",
        "awaiting-approval",
        cwd=state_dir.parent,
        env_extra={"MISSION_STATE_NOW": "2026-07-22T00:02:00Z"},
    )

    assert result.returncode == 0, result.stderr
    state = read_state(state_dir)
    assert state["activity_segments"][-1]["reason"] == "work"
    assert state["activity_segments"][-1]["ended_at"] == "2026-07-22T00:01:00Z"
    assert state["activity_current"]["reason"] == "resumed-implementation"


def test_reactivation_history_cannot_be_overwritten_with_generic_set(
    state_dir, run_cli, read_state
):
    _halt(run_cli, state_dir)
    run_cli(
        "reactivate",
        "--approved-by-user",
        "--reason",
        "user approved recovery",
        "--expected-category",
        "awaiting-approval",
        cwd=state_dir.parent,
        check=True,
    )
    before = read_state(state_dir)

    result = run_cli("set", "reactivation_history=[]", cwd=state_dir.parent)

    assert result.returncode == 2
    assert "変更不可" in result.stderr
    assert read_state(state_dir) == before
