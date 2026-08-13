"""#386: command outcome telemetry is bounded, safe, and observable."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path
import pytest
import command_outcomes
from command_outcomes import LIMIT, OutcomeStoreError, append_sidecar


AUDIT_PY = Path(__file__).resolve().parents[3] / "scripts" / "mission-audit.py"


def _audit_counts(root: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(AUDIT_PY), "--root", str(root), "--json"],
        cwd=root, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["command_outcome_counts"]


def _review_bytes():
    return (json.dumps({
        "schema": "mission-review/1", "iteration": 1, "perspective": "quality",
        "scores": {"mission_achievement": 4.5, "accuracy": 4.5, "completeness": 4.5, "usability": 4.5},
        "findings": [],
    }) + "\n").encode("utf-8")


def _prepare_current_command_provider(state_dir, run_cli, command: str):
    root = state_dir.parent
    state_file = state_dir / "sessions" / "test.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["complexity"] = "Complex"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    registry = root / "provider-registry.json"
    registry.write_text(json.dumps({
        "schema": "mission-specialist-registry/2",
        "specialists_v2": [{
            "provider_id": "fixture-provider",
            "role": "evidence",
            "skill": "fixture-provider",
            "kind": "command",
            "command": command,
            "args": [],
            "env": {},
            "task_profiles": ["architecture"],
            "phases": ["planning", "review"],
            "activation": {
                "min_complexity": "Complex",
                "auto_select_if": ["complexity"],
            },
        }],
    }), encoding="utf-8")
    env = {"PATH": f"{root / 'commands'}{os.pathsep}{os.environ.get('PATH', '')}"}
    recommendation = run_cli(
        "specialists", "recommend", "--no-default-skill-roots", "--task",
        "Review the architecture", "--registry", str(registry),
        "--complexity", "Complex", "--record-state", cwd=root,
        env_extra=env,
    )
    assert recommendation.returncode == 0, recommendation.stderr
    run_cli(
        "advance", "--phase", "reviewing", "--artifact-applicability", "not-applicable",
        cwd=root, check=True, env_extra=env,
    )
    return registry, env


def test_stats_and_audit_count_state_and_sidecar_command_outcomes(state_dir, run_cli, tmp_path):
    review = tmp_path / "bad.json"
    review.write_text('{"schema":"wrong"}', encoding="utf-8")

    result = run_cli(
        "review-import", "--iteration", "1", "--input", str(review),
        "--event-id", "attempt-2", "--root-event-id", "root-1", "--attempt", "2",
        "--retry-of", "attempt-1", cwd=state_dir.parent,
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["outcome"] == {
        "event_id": "attempt-2", "root_event_id": "root-1", "attempt": 2,
        "retry_of": "attempt-1", "command": "review-import", "outcome_kind": "invalid-input",
        "guidance": True,
    }
    stats = json.loads(run_cli("stats", "--root", str(state_dir.parent), "--json", cwd=state_dir.parent).stdout)
    assert stats["command_outcome_counts"] == {
        "ok": 0, "expected-gate": 0, "invalid-input": 1, "external": 0,
        "internal-error": 0, "unique_root_events": 1, "retry_count": 1,
        "invalid_records": 0, "corrupt_sidecars": 0,
    }
    assert _audit_counts(state_dir.parent) == stats["command_outcome_counts"]


def test_corrupt_sidecar_is_never_silently_accepted_and_is_visible_in_stats(state_dir, run_cli):
    telemetry = state_dir / "telemetry" / "command-outcomes"
    telemetry.mkdir(parents=True)
    token = hashlib.sha256(b"test").hexdigest()[:16]
    (telemetry / f"{token}.json").write_text("not-json", encoding="utf-8")

    result = run_cli("stats", "--root", str(state_dir.parent), "--json", cwd=state_dir.parent)

    assert result.returncode == 0
    assert json.loads(result.stdout)["command_outcome_counts"]["corrupt_sidecars"] == 1


def test_command_provider_unavailable_is_an_external_outcome_producer(
    state_dir, run_cli, prepare_approved_invocation
):
    command_dir = state_dir.parent / "commands"
    command_dir.mkdir()
    command = command_dir / "fixture-provider-command"
    command.write_text("#!/bin/sh\ncat\n", encoding="utf-8")
    command.chmod(0o700)
    registry, env = _prepare_current_command_provider(
        state_dir, run_cli, "fixture-provider-command"
    )
    invoke_args, env, _ = prepare_approved_invocation(
        cwd=state_dir.parent, provider="fixture-provider", iteration=1,
        phase="review", registry=registry, env_extra=env,
    )
    command.unlink()

    result = run_cli(
        *invoke_args, "--event-id", "provider-attempt",
        cwd=state_dir.parent, env_extra=env,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["outcome_kind"] == "external"


def test_unexpected_provider_packet_error_is_internal_without_leaking_path(
    state_dir, run_cli, tmp_path, prepare_approved_invocation
):
    command_dir = state_dir.parent / "commands"
    command_dir.mkdir()
    command = command_dir / "fixture-provider-command"
    command.write_text("#!/bin/sh\ncat\n", encoding="utf-8")
    command.chmod(0o700)
    registry, env = _prepare_current_command_provider(
        state_dir, run_cli, "fixture-provider-command"
    )
    invoke_args, env, prepared = prepare_approved_invocation(
        cwd=state_dir.parent, provider="fixture-provider", iteration=1,
        phase="review", registry=registry, env_extra=env,
    )
    state = json.loads((state_dir / "sessions" / "test.json").read_text(encoding="utf-8"))
    missing = state_dir / state["provider_preflights"][prepared["preflight_id"]]["artifact_path"]
    missing.unlink()

    result = run_cli(
        *invoke_args, "--event-id", "internal-attempt",
        cwd=state_dir.parent, env_extra=env,
    )

    assert result.returncode == 1
    assert json.loads(result.stdout)["outcome_kind"] == "internal-error"
    assert str(missing) not in result.stdout


def test_phase_and_lease_gates_keep_state_bytes_and_emit_expected_gate_sidecars(state_dir, run_cli):
    state_file = state_dir / "sessions" / "test.json"
    before_phase = state_file.read_bytes()
    phase = run_cli(
        "advance", "--phase", "halted", "--json", "--event-id", "phase-gate", cwd=state_dir.parent,
    )
    assert phase.returncode == 2
    assert json.loads(phase.stdout)["outcome_kind"] == "expected-gate"
    assert state_file.read_bytes() == before_phase

    state = json.loads(before_phase)
    state.update({
        "owner_session_id": "another-session", "lease_id": "another-lease", "fencing_epoch": 1,
        "lease_expires_at": "2099-01-01T00:00:00Z",
    })
    state_file.write_text(json.dumps(state), encoding="utf-8")
    before_lease = state_file.read_bytes()
    lease = run_cli("set", "phase=executing", "--json", "--event-id", "lease-gate", cwd=state_dir.parent)
    assert lease.returncode == 2
    assert json.loads(lease.stdout)["outcome_kind"] == "expected-gate"
    assert state_file.read_bytes() == before_lease


@pytest.mark.parametrize(("option", "value"), [
    ("--event-id", "../escape"), ("--root-event-id", "/absolute"),
    ("--retry-of", "contains space"), ("--attempt", "0"),
])
def test_lineage_inputs_are_validated_before_review_state_write(state_dir, run_cli, tmp_path, option, value):
    source = tmp_path / "review.json"
    source.write_bytes(_review_bytes())
    state_file = state_dir / "sessions" / "test.json"
    before = state_file.read_bytes()

    result = run_cli("review-import", "--iteration", "1", "--input", str(source), option, value, cwd=state_dir.parent)

    assert result.returncode == 2
    assert json.loads(result.stdout)["outcome_kind"] == "invalid-input"
    assert state_file.read_bytes() == before


def _outcome(index):
    return {"event_id": f"event-{index}", "root_event_id": "root", "attempt": index + 1,
            "retry_of": "prior" if index else None, "command": "fixture", "outcome_kind": "expected-gate"}


def test_sidecar_cap_and_atomic_publish_leave_no_temp_files(state_dir):
    token = hashlib.sha256(b"test").hexdigest()[:16]
    for index in range(LIMIT + 2):
        record = _outcome(index)
        if index == 0:
            record.pop("retry_of")
        append_sidecar(state_dir, token, record)
    directory = state_dir / "telemetry" / "command-outcomes"
    sidecar = json.loads((directory / f"{token}.json").read_text(encoding="utf-8"))
    assert len(sidecar["records"]) == LIMIT
    assert not list(directory.glob(".*.tmp"))


@pytest.mark.parametrize("mode", ["symlink", "hardlink", "fifo", "oversize", "corrupt"])
def test_sidecar_hostile_files_fail_closed(mode, state_dir, tmp_path):
    token = hashlib.sha256(b"test").hexdigest()[:16]
    directory = state_dir / "telemetry" / "command-outcomes"
    directory.mkdir(parents=True)
    sidecar = directory / f"{token}.json"
    if mode == "symlink":
        target = tmp_path / "target.json"
        target.write_text("{}", encoding="utf-8")
        sidecar.symlink_to(target)
    elif mode == "hardlink":
        target = tmp_path / "target.json"
        target.write_text("{}", encoding="utf-8")
        sidecar.hardlink_to(target)
    elif mode == "fifo":
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.unlink(missing_ok=True)
        command_outcomes.os.mkfifo(sidecar)
    elif mode == "corrupt":
        sidecar.write_text("not-json", encoding="utf-8")
    else:
        sidecar.write_bytes(b"x" * (256 * 1024 + 1))
    with pytest.raises(OutcomeStoreError):
        append_sidecar(state_dir, token, _outcome(1))


def test_sidecar_lock_hardlink_is_rejected_without_touching_external_bytes(state_dir, tmp_path):
    token = hashlib.sha256(b"test").hexdigest()[:16]
    directory = state_dir / "telemetry" / "command-outcomes"
    directory.mkdir(parents=True)
    external = tmp_path / "external.lock"
    original = b"must-not-be-truncated"
    external.write_bytes(original)
    lock = directory / f"{token}.lock"
    lock.hardlink_to(external)

    with pytest.raises(OutcomeStoreError):
        append_sidecar(state_dir, token, _outcome(1))

    assert external.read_bytes() == original
    assert not (directory / f"{token}.json").exists()


def test_sidecar_unsafe_symlink_ancestor_is_rejected_without_external_writes(state_dir, tmp_path):
    external = tmp_path / "outside"
    external.mkdir()
    telemetry = state_dir / "telemetry"
    telemetry.symlink_to(external, target_is_directory=True)
    token = hashlib.sha256(b"test").hexdigest()[:16]

    with pytest.raises(OutcomeStoreError):
        append_sidecar(state_dir, token, _outcome(1))

    assert not list(external.rglob("*"))
    records, invalid, corrupt = command_outcomes.iter_records({}, state_dir, token)
    assert (records, invalid, corrupt) == ([], 0, 1)
    assert not list(external.rglob("*"))


def test_lock_same_name_replacement_after_open_is_rejected(monkeypatch, state_dir):
    directory = state_dir / "telemetry" / "command-outcomes"
    directory.mkdir(parents=True)
    lock = directory / "race.lock"
    original_flock = command_outcomes.fcntl.flock
    replaced = False

    def replace_before_flock(fd, operation):
        nonlocal replaced
        if operation & command_outcomes.fcntl.LOCK_EX and not replaced:
            replaced = True
            lock.unlink()
            lock.write_bytes(b"replacement")
        return original_flock(fd, operation)

    monkeypatch.setattr(command_outcomes.fcntl, "flock", replace_before_flock)
    with pytest.raises(OutcomeStoreError, match="changed"):
        with command_outcomes._Lock(lock):
            pass
    assert lock.read_bytes() == b"replacement"


def test_sidecar_parent_replacement_after_lock_fails_before_publish(
    monkeypatch, state_dir,
):
    token = hashlib.sha256(b"test").hexdigest()[:16]
    directory = state_dir / "telemetry" / "command-outcomes"
    directory.mkdir(parents=True)
    detached = state_dir / "telemetry" / "detached-command-outcomes"
    original_verify = getattr(command_outcomes, "_verify_directory_identity", None)
    verification_count = 0

    def replace_parent_after_lock(directory_fd, named_parent):
        nonlocal verification_count
        verification_count += 1
        if verification_count == 2:
            directory.rename(detached)
            directory.mkdir()
            (directory / "sentinel").write_bytes(b"replacement-directory")
        if original_verify is not None:
            return original_verify(directory_fd, named_parent)
        return None

    monkeypatch.setattr(
        command_outcomes, "_verify_directory_identity", replace_parent_after_lock,
        raising=False,
    )

    with pytest.raises(OutcomeStoreError, match="directory changed"):
        append_sidecar(state_dir, token, _outcome(1))

    assert (directory / "sentinel").read_bytes() == b"replacement-directory"
    assert not (directory / f"{token}.json").exists()
    assert not (detached / f"{token}.json").exists()


@pytest.mark.parametrize("mode", ["symlink", "hardlink", "fifo", "oversize", "corrupt"])
def test_iter_records_rejects_hostile_sidecars_without_touching_targets(
    mode, state_dir, tmp_path,
):
    token = hashlib.sha256(b"test").hexdigest()[:16]
    directory = state_dir / "telemetry" / "command-outcomes"
    directory.mkdir(parents=True)
    sidecar = directory / f"{token}.json"
    target = tmp_path / "external.json"
    original = b"external-must-not-change"
    target.write_bytes(original)
    if mode == "symlink":
        sidecar.symlink_to(target)
    elif mode == "hardlink":
        sidecar.hardlink_to(target)
    elif mode == "fifo":
        command_outcomes.os.mkfifo(sidecar)
    elif mode == "oversize":
        sidecar.write_bytes(b"x" * (256 * 1024 + 1))
    else:
        sidecar.write_text("not-json", encoding="utf-8")

    records, invalid, corrupt = command_outcomes.iter_records({}, state_dir, token)

    assert records == []
    assert invalid == 0
    assert corrupt == 1
    assert target.read_bytes() == original


def test_state_and_sidecar_identical_event_is_counted_once(state_dir):
    token = hashlib.sha256(b"test").hexdigest()[:16]
    record = _outcome(1)
    append_sidecar(state_dir, token, record)

    records, invalid, corrupt = command_outcomes.iter_records(
        {"command_outcomes": [record]}, state_dir, token,
    )

    assert records == [record]
    assert (invalid, corrupt) == (0, 0)
    summary = command_outcomes.summarize(records)
    assert summary["expected-gate"] == 1
    assert summary["unique_root_events"] == 1
    assert summary["retry_count"] == 1


def test_conflicting_duplicate_event_is_excluded_and_marked_invalid(state_dir):
    token = hashlib.sha256(b"test").hexdigest()[:16]
    state_record = _outcome(1)
    sidecar_record = {**state_record, "outcome_kind": "invalid-input"}
    append_sidecar(state_dir, token, sidecar_record)

    records, invalid, corrupt = command_outcomes.iter_records(
        {"command_outcomes": [state_record]}, state_dir, token,
    )

    assert records == []
    assert (invalid, corrupt) == (1, 0)


@pytest.mark.parametrize("conflicting", [False, True])
def test_stats_and_audit_share_cross_source_event_dedupe(
    conflicting, state_dir, run_cli,
):
    token = hashlib.sha256(b"test").hexdigest()[:16]
    state_record = _outcome(1)
    state_file = state_dir / "sessions" / "test.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["command_outcomes"] = [state_record]
    state_file.write_text(json.dumps(state), encoding="utf-8")
    sidecar_record = (
        {**state_record, "outcome_kind": "invalid-input"}
        if conflicting else state_record
    )
    append_sidecar(state_dir, token, sidecar_record)

    stats_result = run_cli(
        "stats", "--root", str(state_dir.parent), "--json", cwd=state_dir.parent,
    )
    assert stats_result.returncode == 0, stats_result.stderr
    stats = json.loads(stats_result.stdout)["command_outcome_counts"]
    assert _audit_counts(state_dir.parent) == stats
    assert stats["expected-gate"] == (0 if conflicting else 1)
    assert stats["invalid-input"] == 0
    assert stats["invalid_records"] == (1 if conflicting else 0)


def test_stats_and_audit_namespace_event_and_root_ids_per_session(
    state_dir, run_cli,
):
    sessions = state_dir / "sessions"
    first_path = sessions / "test.json"
    first = json.loads(first_path.read_text(encoding="utf-8"))
    shared = {
        "event_id": "shared-event", "root_event_id": "shared-root", "attempt": 1,
        "command": "fixture", "outcome_kind": "expected-gate",
    }
    first["command_outcomes"] = [shared]
    first_path.write_text(json.dumps(first), encoding="utf-8")
    second = {
        **first, "session_id": "other", "mission_id": "other-mission",
        "command_outcomes": [{**shared, "outcome_kind": "invalid-input"}],
    }
    (sessions / "other.json").write_text(json.dumps(second), encoding="utf-8")

    result = run_cli(
        "stats", "--root", str(state_dir.parent), "--json", cwd=state_dir.parent,
    )

    assert result.returncode == 0, result.stderr
    counts = json.loads(result.stdout)["command_outcome_counts"]
    assert _audit_counts(state_dir.parent) == counts
    assert counts["expected-gate"] == 1
    assert counts["invalid-input"] == 1
    assert counts["unique_root_events"] == 2
    assert counts["invalid_records"] == 0
