"""Issue #354: fenced session lease ownership and stale cleanup."""

import importlib.util
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"
SPEC = importlib.util.spec_from_file_location("mission_state_issue354", MISSION_STATE_PY)
MISSION_STATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MISSION_STATE)
LEASE_CARRIER_PREFIX = "MISSION_LEASE_CARRIER="


def _lease_state(**overrides):
    state = {
        "mission_id": "abc",
        "loop_active": True,
        "session_id": "owner-a",
        "owner_session_id": "owner-a",
        "lease_id": "lease-a",
        "fencing_epoch": 3,
        "lease_expires_at": "2026-08-07T12:15:00Z",
        "updated_at": "2026-08-07T12:00:00Z",
        "last_activity_at": "2026-08-07T12:00:00Z",
    }
    state.update(overrides)
    return state


def _lease_carrier(stderr: str) -> dict:
    records = [
        json.loads(line.removeprefix(LEASE_CARRIER_PREFIX))
        for line in stderr.splitlines()
        if line.startswith(LEASE_CARRIER_PREFIX)
    ]
    assert len(records) == 1, stderr
    return records[0]


def _raw_cli(cwd: Path, *args: str, lease_id: str | None = None):
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("MISSION_")
        and key not in {"CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"}
    }
    env["MISSION_SESSION_ID"] = "test"
    if lease_id is not None:
        env["MISSION_LEASE_ID"] = lease_id
    return subprocess.run(
        [sys.executable, str(MISSION_STATE_PY), *args],
        cwd=str(cwd), capture_output=True, text=True, env=env,
    )


@pytest.mark.parametrize("relative_state_path", [
    Path(".mission-state/sessions/session-a.json"),
    Path(".mission-state/state.json"),
])
def test_project_root_uses_nearest_state_structure_when_ancestor_has_same_name(
    tmp_path, relative_state_path,
):
    project = tmp_path / ".mission-state" / "outer" / "project"
    state_path = project / relative_state_path

    derived = MISSION_STATE._project_root_of(state_path)

    assert derived == project
    assert MISSION_STATE.lock_file(derived) == project / ".mission-state" / ".state.lock"


def test_legacy_state_acquires_epoch_one_lease(monkeypatch):
    monkeypatch.setenv("MISSION_STATE_NOW", "2026-08-07T12:00:00Z")
    monkeypatch.setenv("MISSION_LEASE_ID", "new-lease")
    state = {"mission_id": "abc", "loop_active": True}

    decision = MISSION_STATE.acquire_or_verify_lease(state, "owner-a")

    assert decision.action == "acquired"
    assert state["fencing_epoch"] == 1
    assert state["owner_session_id"] == "owner-a"
    assert state["lease_id"] == "new-lease"
    assert state["lease_expires_at"] == "2026-08-07T12:15:00Z"


def test_legacy_mutation_emits_carrier_for_next_independent_process(state_dir):
    first = _raw_cli(state_dir.parent, "set", "iteration=2")

    assert first.returncode == 0, first.stderr
    carrier = _lease_carrier(first.stderr)
    assert carrier["schema"] == "mission-lease-carrier/1"
    assert carrier["action"] == "acquired"
    assert carrier["session_id"] == "test"
    assert carrier["fencing_epoch"] == 1

    second = _raw_cli(
        state_dir.parent, "set", "iteration=3",
        lease_id=carrier["lease_id"],
    )
    assert second.returncode == 0, second.stderr


def test_partial_lease_is_rejected_instead_of_downgraded_to_legacy(monkeypatch):
    monkeypatch.setenv("MISSION_STATE_NOW", "2026-08-07T12:00:00Z")
    state = {"mission_id": "abc", "loop_active": True, "fencing_epoch": 9}

    with pytest.raises(MISSION_STATE.LeaseRejectedError, match="malformed partial"):
        MISSION_STATE.acquire_or_verify_lease(state, "owner-a")

    assert state["fencing_epoch"] == 9


def test_self_lease_renews_without_changing_epoch(monkeypatch):
    monkeypatch.setenv("MISSION_STATE_NOW", "2026-08-07T12:10:00Z")
    monkeypatch.setenv("MISSION_LEASE_ID", "lease-a")
    state = _lease_state()

    decision = MISSION_STATE.acquire_or_verify_lease(state, "owner-a")

    assert decision.action == "renewed"
    assert state["fencing_epoch"] == 3
    assert state["lease_expires_at"] == "2026-08-07T12:25:00Z"


def test_same_owner_without_fencing_token_cannot_renew(monkeypatch):
    monkeypatch.setenv("MISSION_STATE_NOW", "2026-08-07T12:10:00Z")
    monkeypatch.delenv("MISSION_LEASE_ID", raising=False)
    state = _lease_state()

    with pytest.raises(MISSION_STATE.LeaseRejectedError, match="lease held by owner-a until"):
        MISSION_STATE.acquire_or_verify_lease(state, "owner-a")


def test_clock_rollback_renew_does_not_shorten_expiry(monkeypatch):
    monkeypatch.setenv("MISSION_STATE_NOW", "2026-08-07T11:00:00Z")
    monkeypatch.setenv("MISSION_LEASE_ID", "lease-a")
    state = _lease_state(lease_expires_at="2026-08-07T12:15:00Z")

    MISSION_STATE.acquire_or_verify_lease(state, "owner-a")

    assert state["lease_expires_at"] == "2026-08-07T12:15:00Z"


def test_pid_reuse_cannot_write_foreign_unexpired_lease(monkeypatch):
    monkeypatch.setenv("MISSION_STATE_NOW", "2026-08-07T12:05:00Z")
    monkeypatch.setenv("MISSION_LEASE_ID", "lease-b")
    state = _lease_state(pid=4242)

    with pytest.raises(MISSION_STATE.LeaseRejectedError, match="lease held by owner-a until"):
        MISSION_STATE.acquire_or_verify_lease(state, "owner-b")


def test_expired_foreign_takeover_increments_epoch_and_records_history(monkeypatch):
    monkeypatch.setenv("MISSION_STATE_NOW", "2026-08-07T12:20:00Z")
    monkeypatch.setenv("MISSION_LEASE_ID", "lease-b")
    state = _lease_state()

    decision = MISSION_STATE.acquire_or_verify_lease(state, "owner-b", reason="resume")

    assert decision.action == "taken-over"
    assert state["owner_session_id"] == "owner-b"
    assert state["lease_id"] == "lease-b"
    assert state["fencing_epoch"] == 4
    assert state["lease_history"][-1] == {
        "owner_session_id": "owner-a",
        "lease_id": "lease-a",
        "fencing_epoch": 3,
        "reason": "resume",
        "at": "2026-08-07T12:20:00Z",
    }


def test_clock_rollback_old_owner_is_rejected_after_takeover(monkeypatch):
    state = _lease_state(lease_expires_at="2026-08-07T11:59:00Z")
    monkeypatch.setenv("MISSION_STATE_NOW", "2026-08-07T12:00:00Z")
    monkeypatch.setenv("MISSION_LEASE_ID", "lease-b")
    MISSION_STATE.acquire_or_verify_lease(state, "owner-b", reason="expired")

    monkeypatch.setenv("MISSION_STATE_NOW", "2026-08-07T11:55:00Z")
    monkeypatch.setenv("MISSION_LEASE_ID", "lease-a")
    with pytest.raises(MISSION_STATE.LeaseRejectedError):
        MISSION_STATE.acquire_or_verify_lease(state, "owner-a")


def test_old_owner_cannot_return_after_takeover(monkeypatch):
    state = _lease_state(lease_expires_at="2026-08-07T11:59:00Z")
    monkeypatch.setenv("MISSION_STATE_NOW", "2026-08-07T12:00:00Z")
    monkeypatch.setenv("MISSION_LEASE_ID", "lease-b")
    MISSION_STATE.acquire_or_verify_lease(state, "owner-b")

    monkeypatch.setenv("MISSION_STATE_NOW", "2026-08-07T12:16:00Z")
    monkeypatch.setenv("MISSION_LEASE_ID", "lease-a")
    with pytest.raises(MISSION_STATE.LeaseRejectedError):
        MISSION_STATE.acquire_or_verify_lease(state, "owner-a")


def test_init_returns_lease_contract_and_mutation_accepts_token(tmp_path, run_cli):
    env = {"MISSION_SESSION_ID": "session-a", "MISSION_LEASE_ID": "lease-a"}
    initialized = run_cli(
        "init", "lease test", "--complexity", "Standard", cwd=tmp_path,
        env_extra=env, check=True,
    )
    contract = json.loads(initialized.stdout)

    assert contract["lease_id"] == "lease-a"
    assert contract["fencing_epoch"] == 1
    changed = run_cli("set", "iteration=1", cwd=tmp_path, env_extra=env)
    assert changed.returncode == 0, changed.stderr


def test_explicit_stale_token_is_rejected_by_cli(tmp_path, run_cli):
    owner = {"MISSION_SESSION_ID": "session-a", "MISSION_LEASE_ID": "lease-a"}
    run_cli("init", "lease test", "--complexity", "Standard", cwd=tmp_path,
            env_extra=owner, check=True)

    stale = run_cli(
        "set", "iteration=1", cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "session-a", "MISSION_LEASE_ID": "stale-lease"},
    )

    assert stale.returncode == 2
    assert "lease held by session-a until" in stale.stderr


def test_same_session_cli_without_token_is_rejected(tmp_path, run_cli):
    owner = {"MISSION_SESSION_ID": "session-a", "MISSION_LEASE_ID": "lease-a"}
    run_cli("init", "lease test", "--complexity", "Standard", cwd=tmp_path,
            env_extra=owner, check=True)

    missing = run_cli(
        "set", "iteration=1", cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "session-a", "MISSION_LEASE_ID": None},
    )

    assert missing.returncode == 2
    assert "lease held by session-a until" in missing.stderr


def test_same_pid_fallback_session_without_token_is_rejected(tmp_path, run_cli):
    sessionless = {
        "MISSION_SESSION_ID": None,
        "CLAUDE_CODE_SESSION_ID": None,
        "CODEX_THREAD_ID": None,
    }
    initialized = run_cli(
        "init", "fallback lease", "--complexity", "Standard", cwd=tmp_path,
        env_extra={**sessionless, "MISSION_LEASE_ID": "fallback-lease"}, check=True,
    )
    assert json.loads(initialized.stdout)["session_id"].startswith("pid-")

    missing = run_cli(
        "set", "iteration=1", cwd=tmp_path,
        env_extra={**sessionless, "MISSION_LEASE_ID": None},
    )

    assert missing.returncode == 2
    assert "lease held by pid-" in missing.stderr


def test_cleanup_stale_uses_expired_lease_not_dead_pid(tmp_path, run_cli):
    project = tmp_path / "project"
    sessions = project / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    state = _lease_state(
        session_id="session-a",
        owner_session_id="session-a",
        pid=99999999,
        project_root=str(project),
        lease_expires_at="2026-08-07T11:59:00Z",
        last_activity_at="2026-08-07T11:58:00Z",
    )
    (sessions / "session-a.json").write_text(json.dumps(state))

    result = run_cli(
        "cleanup-stale", "--root", str(tmp_path), cwd=tmp_path,
        env_extra={"MISSION_STATE_NOW": "2026-08-07T12:00:00Z"},
    )

    output = json.loads(result.stdout)
    assert output["would_halt"][0]["reason"] == "expired-session-lease"


def test_cleanup_stale_skips_expired_lease_with_newer_activity_heartbeat(tmp_path, run_cli):
    project = tmp_path / "project"
    sessions = project / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    state = _lease_state(
        session_id="session-a",
        owner_session_id="session-a",
        pid=99999999,
        project_root=str(project),
        lease_expires_at="2026-08-07T11:59:00Z",
        last_activity_at="2026-08-07T11:59:30Z",
    )
    (sessions / "session-a.json").write_text(json.dumps(state))

    result = run_cli(
        "cleanup-stale", "--root", str(tmp_path), cwd=tmp_path,
        env_extra={"MISSION_STATE_NOW": "2026-08-07T12:00:00Z"},
    )

    output = json.loads(result.stdout)
    assert output["would_halt"] == []
    assert output["skipped"][0]["reason"] == "lease-expired-activity-heartbeat-present"


@pytest.mark.parametrize(
    ("replacement", "expected_owner", "expected_epoch"),
    [
        ({"lease_expires_at": "2026-08-07T12:15:00Z"}, "owner-a", 3),
        ({
            "owner_session_id": "owner-b",
            "lease_id": "lease-b",
            "fencing_epoch": 4,
            "lease_expires_at": "2026-08-07T12:15:00Z",
        }, "owner-b", 4),
    ],
)
def test_cleanup_janitor_rejects_renew_or_takeover_race(
    tmp_path, monkeypatch, capsys, replacement, expected_owner, expected_epoch,
):
    """Observation-to-lock race cannot halt a lease renewed/taken over before CAS."""
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    path = sessions / "owner-a.json"
    state = _lease_state(
        project_root=str(tmp_path),
        lease_expires_at="2026-08-07T11:59:00Z",
        last_activity_at="2026-08-07T11:58:00Z",
        phase="executing",
        passes=False,
        halt_reason="",
        score_history=[],
    )
    path.write_text(json.dumps(state))
    monkeypatch.setenv("MISSION_STATE_NOW", "2026-08-07T12:00:00Z")
    original_terminalize = MISSION_STATE._terminalize_state_file

    def race_before_lock(*args, **kwargs):
        current = json.loads(path.read_text())
        current.update(replacement)
        path.write_text(json.dumps(current))
        return original_terminalize(*args, **kwargs)

    monkeypatch.setattr(MISSION_STATE, "_terminalize_state_file", race_before_lock)

    MISSION_STATE.cmd_cleanup_stale(
        type("Args", (), {"root": str(tmp_path), "execute": True})()
    )

    output = json.loads(capsys.readouterr().out)
    current = json.loads(path.read_text())
    assert output["halted"] == []
    assert current["loop_active"] is True
    assert current["owner_session_id"] == expected_owner
    assert current["fencing_epoch"] == expected_epoch


def test_cleanup_janitor_revalidation_holds_writer_lock_with_same_named_ancestor(
    tmp_path, monkeypatch, capsys,
):
    project = tmp_path / ".mission-state" / "outer" / "project"
    sessions = project / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    path = sessions / "owner-a.json"
    path.write_text(json.dumps(_lease_state(
        project_root=str(project),
        lease_expires_at="2026-08-07T11:59:00Z",
        last_activity_at="2026-08-07T11:58:00Z",
        phase="executing",
        passes=False,
        halt_reason="",
        score_history=[],
    )))
    monkeypatch.setenv("MISSION_STATE_NOW", "2026-08-07T12:00:00Z")
    original_check = MISSION_STATE._expired_lease_without_heartbeat
    checks = 0
    writer_blocked_during_cas = False

    def probe_writer_lock(data):
        nonlocal checks, writer_blocked_during_cas
        checks += 1
        if checks == 2:
            with pytest.raises(TimeoutError):
                with MISSION_STATE.StateLock(
                    MISSION_STATE.lock_file(project), timeout=0.05,
                ):
                    pass
            writer_blocked_during_cas = True
        return original_check(data)

    monkeypatch.setattr(
        MISSION_STATE, "_expired_lease_without_heartbeat", probe_writer_lock,
    )

    MISSION_STATE.cmd_cleanup_stale(
        type("Args", (), {"root": str(project), "execute": True})()
    )

    output = json.loads(capsys.readouterr().out)
    assert writer_blocked_during_cas is True
    assert output["halted"][0]["path"] == str(path)
    assert json.loads(path.read_text())["loop_active"] is False


def test_refresh_pid_then_resume_does_not_false_stale(tmp_path, run_cli):
    env = {
        "MISSION_SESSION_ID": "session-a",
        "MISSION_LEASE_ID": "lease-a",
        "MISSION_STATE_NOW": "2026-08-07T12:00:00Z",
    }
    run_cli("init", "lease test", "--complexity", "Standard", cwd=tmp_path,
            env_extra=env, check=True)
    run_cli("refresh-pid", "--force", cwd=tmp_path, env_extra=env, check=True)

    resumed = run_cli("resume", "--force", cwd=tmp_path, env_extra=env)

    assert resumed.returncode == 0, resumed.stderr
    assert json.loads(resumed.stdout)["resume"]["halted_stale"] == 0
    state = json.loads(
        (tmp_path / ".mission-state" / "sessions" / "session-a.json").read_text()
    )
    assert state["loop_active"] is True


def test_resume_takes_over_expired_foreign_lease_and_records_reason(tmp_path, run_cli):
    project = tmp_path
    sessions = project / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    state = _lease_state(
        session_id="session-a",
        owner_session_id="old-runner",
        lease_id="old-lease",
        fencing_epoch=8,
        lease_expires_at="2026-08-07T11:59:00Z",
        pid=99999999,
        project_root=str(project),
        phase="executing",
        passes=False,
        halt_reason="",
        score_history=[],
    )
    (sessions / "session-a.json").write_text(json.dumps(state))

    resumed = run_cli(
        "resume", "--force", cwd=project,
        env_extra={
            "MISSION_SESSION_ID": "session-a",
            "MISSION_LEASE_ID": "new-lease",
            "MISSION_STATE_NOW": "2026-08-07T12:00:00Z",
        },
    )

    assert resumed.returncode == 0, resumed.stderr
    updated = json.loads((sessions / "session-a.json").read_text())
    assert updated["fencing_epoch"] == 9
    assert updated["owner_session_id"] == "session-a"
    assert updated["lease_history"][-1]["reason"] == "resume"


def test_resume_expired_foreign_lease_ignores_live_legacy_pid_without_force(
    tmp_path, run_cli,
):
    sessions = tmp_path / ".mission-state" / "sessions"
    sessions.mkdir(parents=True)
    state = _lease_state(
        session_id="session-a",
        owner_session_id="old-runner",
        lease_id="old-lease",
        lease_expires_at="2026-08-07T11:59:00Z",
        pid=4242,
        project_root=str(tmp_path),
        phase="executing",
        passes=False,
        halt_reason="",
        score_history=[],
    )
    (sessions / "session-a.json").write_text(json.dumps(state))

    resumed = run_cli(
        "resume", cwd=tmp_path,
        env_extra={
            "MISSION_SESSION_ID": "session-a",
            "MISSION_LEASE_ID": "new-lease",
            "MISSION_STATE_NOW": "2026-08-07T12:00:00Z",
            "MISSION_FORCE_PID_IS_AGENT": "1",
        },
    )

    assert resumed.returncode == 0, resumed.stderr
    updated = json.loads((sessions / "session-a.json").read_text())
    assert updated["owner_session_id"] == "session-a"
    assert updated["fencing_epoch"] == 4


def test_concurrent_renew_is_serialized_and_keeps_single_owner(tmp_path, run_cli):
    env = {"MISSION_SESSION_ID": "session-a", "MISSION_LEASE_ID": "lease-a"}
    run_cli("init", "lease test", "--complexity", "Standard", cwd=tmp_path,
            env_extra=env, check=True)

    def renew(iteration):
        return run_cli("set", f"iteration={iteration}", cwd=tmp_path, env_extra=env)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(renew, (1, 2)))

    assert all(result.returncode == 0 for result in results)
    state = json.loads(
        (tmp_path / ".mission-state" / "sessions" / "session-a.json").read_text()
    )
    assert state["owner_session_id"] == "session-a"
    assert state["lease_id"] == "lease-a"
    assert state["fencing_epoch"] == 1
