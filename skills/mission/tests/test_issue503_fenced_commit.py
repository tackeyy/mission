"""Issue #503: fenced generation CAS and immutable commit/head records."""

from __future__ import annotations

import ast
import fcntl
import hashlib
import json
import os
import stat
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest

from mission_kernel.json_codec import decode_json_object, encode_json_object

from .mission_state_fixture_corpus import (
    _run_cli_with_clock,
    generate_cli_state_bytes,
)


def _parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class _Clock:
    def __init__(self, current: datetime):
        self.current = current

    def __call__(self) -> datetime:
        return self.current


class _SequenceClock:
    def __init__(self, values):
        self.values = iter(values)

    def __call__(self) -> datetime:
        return next(self.values)


def _verified_blob_set(content: bytes, *, blob_id: str = "cli-evidence"):
    from mission_persistence.local_uow import BlobBinding, VerifiedBlob, VerifiedBlobSet

    binding = BlobBinding(
        blob_id=blob_id,
        kind="cli-output",
        relative_path="evidence/mission-state.json",
        digest="sha256:" + hashlib.sha256(content).hexdigest(),
        size=len(content),
    )
    return VerifiedBlobSet((VerifiedBlob(binding, content),))


def _request(
    *,
    operation_id: str,
    lease_id: Optional[str],
    argv: tuple[str, ...],
    session_id: str = "test",
    lease_owner_session_id: str = "test",
    command_type: str = "mutating-command",
    event_types: tuple[str, ...] = ("mission-state-changed",),
    extra_command: Optional[dict] = None,
    blobs=None,
):
    from mission_persistence.fenced_commit import (
        AuditMetadata,
        ExecutionRequest,
        compute_intent_digest,
    )
    from mission_persistence.local_uow import VerifiedBlobSet

    command_document = {
        "argv": list(argv),
        "schema": "mission-command-intent/1",
    }
    if extra_command:
        command_document.update(extra_command)
    command = decode_json_object(
        json.dumps(
            command_document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    request_blobs = VerifiedBlobSet(()) if blobs is None else blobs
    return ExecutionRequest(
        session_id=session_id,
        lease_owner_session_id=lease_owner_session_id,
        command=command,
        blobs=request_blobs,
        operation_id=operation_id,
        intent_digest=compute_intent_digest(
            session_id=session_id,
            lease_owner_session_id=lease_owner_session_id,
            operation_id=operation_id,
            command=command,
            blobs=request_blobs,
        ),
        presented_lease_id=lease_id,
        audit=AuditMetadata(command_type=command_type, event_types=event_types),
    )


def _public_bytes(repository: Path) -> dict[str, bytes]:
    result = {}
    for directory_name in ("sessions", "objects", "generations", "commits", "operations"):
        directory = repository / directory_name
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir()):
            if path.is_file() and not path.is_symlink():
                result[path.relative_to(repository).as_posix()] = path.read_bytes()
    return result


def _commit_cli_init(tmp_path: Path, *, fault_injector=None):
    from mission_persistence.fenced_commit import CommitResult, LocalFencedRepository

    cli_root = tmp_path / "cli-init"
    state_path, state_bytes = generate_cli_state_bytes(cli_root)
    state_document = json.loads(state_bytes.decode("utf-8"))
    admitted_at = _parse_time(state_document["lease_expires_at"]) - timedelta(seconds=900)
    clock = _Clock(admitted_at)
    repository = tmp_path / "repository" / ".mission-state"
    request = _request(
        operation_id="operation-init",
        lease_id=state_document["lease_id"],
        argv=("init", "Issue 500 CLI corpus"),
        command_type="init",
        event_types=("mission-initialized",),
    )
    local = LocalFencedRepository(
        repository,
        clock=clock,
        fault_injector=None,
    )
    admitted = local.begin(request)
    assert not isinstance(admitted, CommitResult)
    prepared = local._stage_persistence(admitted, state_bytes=state_bytes, effects=())
    result = local.commit(prepared, prepared.precondition)
    local.fault_injector = fault_injector
    return local, repository, clock, state_path, state_bytes, result


def _cli_mutation_from_bytes(
    root: Path,
    base_bytes: bytes,
    *,
    lease_id: str,
    now: str,
    phase: str,
) -> bytes:
    session = root / ".mission-state" / "sessions" / "test.json"
    session.parent.mkdir(parents=True)
    session.write_bytes(base_bytes)
    completed = _run_cli_with_clock(
        root,
        "set",
        "fixture_transition_probe=true",
        lease_id=lease_id,
        now=now,
    )
    assert completed.returncode == 0, completed.stderr
    # These repository tests need a deterministic next-generation byte image,
    # not a lifecycle transition.  Acquire/renew the lease through the CLI,
    # then arrange the requested historical phase directly as test fixture data.
    state = json.loads(session.read_text(encoding="utf-8"))
    state.pop("fixture_transition_probe", None)
    state["phase"] = phase
    session.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return session.read_bytes()


def _with_admitted_takeover_reason(state_bytes: bytes, admitted) -> bytes:
    document = json.loads(state_bytes)
    target_history = admitted.pending_lease.target.lease_history
    if target_history:
        document["lease_history"][-1]["reason"] = target_history[-1].reason
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_v5_repository_commits_and_reads_exact_cli_state_generation(tmp_path):
    local, repository, _clock, state_path, state_bytes, result = _commit_cli_init(tmp_path)

    snapshot = local.read("test")

    assert state_path.read_bytes() == state_bytes
    assert snapshot.state_bytes == state_bytes
    assert snapshot.head.generation == 1
    assert snapshot.commit.target_generation == 1
    assert snapshot.commit.base.generation == 0
    assert snapshot.commit.base.head_digest is None
    assert snapshot.commit.generation.digest == snapshot.head.state_generation.digest
    assert snapshot.result == result
    assert json.loads(snapshot.head_bytes)["schema"] == "mission-head/1"
    assert json.loads(snapshot.commit_bytes)["schema"] == "mission-commit/1"
    assert len(list((repository / "generations").glob("*.json"))) == 1


def test_two_prepared_commits_from_generation_n_have_one_winner(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock.current = _parse_time("2099-01-01T00:00:00Z")
    winner_bytes = _cli_mutation_from_bytes(
        tmp_path / "winner-cli",
        base_bytes,
        lease_id="winner-lease",
        now="2099-01-01T00:00:00Z",
        phase="reviewing",
    )
    loser_bytes = _cli_mutation_from_bytes(
        tmp_path / "loser-cli",
        base_bytes,
        lease_id="loser-lease",
        now="2099-01-01T00:00:00Z",
        phase="scoring",
    )
    winner = local.begin(
        _request(
            operation_id="operation-winner",
            lease_id="winner-lease",
            argv=("set", "phase=reviewing"),
        )
    )
    loser = local.begin(
        _request(
            operation_id="operation-loser",
            lease_id="loser-lease",
            argv=("set", "phase=scoring"),
        )
    )
    winner_bytes = _with_admitted_takeover_reason(winner_bytes, winner)
    loser_bytes = _with_admitted_takeover_reason(loser_bytes, loser)
    winner_prepared = local._stage_persistence(winner, state_bytes=winner_bytes, effects=())
    loser_prepared = local._stage_persistence(loser, state_bytes=loser_bytes, effects=())

    winner_result = local.commit(winner_prepared, winner_prepared.precondition)
    after_winner = _public_bytes(repository)
    with pytest.raises(FencedCommitError) as rejected:
        local.commit(loser_prepared, loser_prepared.precondition)

    assert rejected.value.code == "head-cas-mismatch"
    assert _public_bytes(repository) == after_winner
    assert local.read("test").result == winner_result
    assert len(list((repository / "generations").glob("*.json"))) == 2
    assert not loser_prepared.staged.root.exists()


def test_same_operation_and_intent_prepared_twice_returns_winner_result(tmp_path):
    local, repository, clock, _state_path, state_bytes, _result = _commit_cli_init(tmp_path)
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "duplicate-operation-cli",
        state_bytes,
        lease_id="fixture-lease",
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="executing",
    )
    request = _request(
        operation_id="operation-duplicate-prepared",
        lease_id="fixture-lease",
        argv=("set", "phase=executing"),
    )
    first = local.begin(request)
    second = local.begin(request)
    first_prepared = local._stage_persistence(first, state_bytes=target_bytes, effects=())
    second_prepared = local._stage_persistence(second, state_bytes=target_bytes, effects=())

    winner = local.commit(first_prepared, first_prepared.precondition)
    after_winner = _public_bytes(repository)
    replay = local.commit(second_prepared, second_prepared.precondition)

    assert replay == winner
    assert _public_bytes(repository) == after_winner
    assert not second_prepared.staged.root.exists()


def test_matching_session_without_matching_token_rejects_before_staging(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, clock, _state_path, _state_bytes, _result = _commit_cli_init(tmp_path)
    before = _public_bytes(repository)

    with pytest.raises(FencedCommitError) as missing:
        local.begin(
            _request(
                operation_id="operation-missing-token",
                lease_id=None,
                argv=("set", "phase=reviewing"),
            )
        )
    with pytest.raises(FencedCommitError) as wrong:
        local.begin(
            _request(
                operation_id="operation-wrong-token",
                lease_id="wrong-lease",
                argv=("set", "phase=reviewing"),
            )
        )

    assert missing.value.code == "lease-token-required"
    assert wrong.value.code == "lease-rejected"
    assert _public_bytes(repository) == before
    assert clock.current < _parse_time(local.read("test").state.lease.lease_expires_at)


def test_live_foreign_session_without_token_is_lease_rejected(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, _repository, _clock, _state_path, _state_bytes, _result = _commit_cli_init(tmp_path)
    request = _request(
        operation_id="operation-live-foreign-no-token",
        lease_id=None,
        argv=("set", "phase=reviewing"),
        lease_owner_session_id="other-session",
    )

    with pytest.raises(FencedCommitError) as rejected:
        local.begin(request)

    assert rejected.value.code == "lease-rejected"


def test_expired_takeover_increments_fence_once_and_retires_old_token(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, _repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock.current = _parse_time("2099-01-01T00:00:00Z")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "takeover-cli",
        base_bytes,
        lease_id="replacement-lease",
        now="2099-01-01T00:00:00Z",
        phase="reviewing",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-takeover",
            lease_id="replacement-lease",
            argv=("set", "phase=reviewing"),
        )
    )
    target_bytes = _with_admitted_takeover_reason(target_bytes, admitted)
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    local.commit(prepared, prepared.precondition)

    lease = local.read("test").state.lease
    assert lease.fencing_epoch == 2
    assert lease.lease_id == "replacement-lease"
    assert len(lease.lease_history) == 1
    assert lease.lease_history[0].lease_id == "fixture-lease"
    clock.current = _parse_time("2099-01-01T00:15:00Z")
    with pytest.raises(FencedCommitError) as stale:
        local.begin(
            _request(
                operation_id="operation-stale-token",
                lease_id="fixture-lease",
                argv=("set", "phase=scoring"),
            )
        )
    assert stale.value.code == "stale-fencing-token"


def test_expired_lease_without_presented_token_generates_fenced_takeover(
    monkeypatch,
    tmp_path,
):
    import mission_persistence.fenced_commit as fenced_commit

    local, _repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock.current = _parse_time("2099-01-01T00:00:00Z")
    cli_root = tmp_path / "no-token-cli"
    session = cli_root / ".mission-state" / "sessions" / "test.json"
    session.parent.mkdir(parents=True)
    session.write_bytes(base_bytes)
    completed = _run_cli_with_clock(
        cli_root,
        "set",
        "fixture_takeover_probe=true",
        lease_id=None,
        now="2099-01-01T00:00:00Z",
    )
    assert completed.returncode == 0, completed.stderr
    target_bytes = session.read_bytes()
    generated_token = json.loads(target_bytes)["lease_id"]
    token_generation_calls = []

    def generate_token(size):
        token_generation_calls.append(size)
        if len(token_generation_calls) == 1:
            return generated_token
        return ("%032x" % len(token_generation_calls))[-32:]

    monkeypatch.setattr(fenced_commit.secrets, "token_hex", generate_token)
    admitted = local.begin(
        _request(
            operation_id="operation-no-token-takeover",
            lease_id=None,
            argv=("set", "phase=reviewing"),
        )
    )
    target_bytes = _with_admitted_takeover_reason(target_bytes, admitted)
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    local.commit(prepared, prepared.precondition)

    lease = local.read("test").state.lease
    assert lease.lease_id == generated_token
    assert lease.fencing_epoch == 2
    assert lease.lease_history[0].lease_id == "fixture-lease"
    assert token_generation_calls[0] == 16


def test_preliminary_domain_rejection_leaves_public_bytes_identical(tmp_path):
    local, repository, _clock, _state_path, _state_bytes, _result = _commit_cli_init(tmp_path)
    before = _public_bytes(repository)

    admitted = local.begin(
        _request(
            operation_id="operation-domain-reject",
            lease_id="fixture-lease",
            argv=("mark-passes",),
        )
    )
    assert admitted.pending_lease.target.fencing_epoch == 1

    assert _public_bytes(repository) == before


def test_lease_expiry_after_stage_rejects_before_publication(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    base_expiry = _parse_time(json.loads(base_bytes)["lease_expires_at"])
    admitted_time = base_expiry - timedelta(seconds=1)
    clock.current = admitted_time
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "renew-cli",
        base_bytes,
        lease_id="fixture-lease",
        now=admitted_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="reviewing",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-expired-stage",
            lease_id="fixture-lease",
            argv=("set", "phase=reviewing"),
        )
    )
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    before = _public_bytes(repository)
    clock.current = base_expiry

    with pytest.raises(FencedCommitError) as rejected:
        local.commit(prepared, prepared.precondition)

    assert rejected.value.code == "lease-precondition-changed"
    assert _public_bytes(repository) == before
    assert not prepared.staged.root.exists()


def test_expired_same_owner_exact_token_renews_without_fence_increment(tmp_path):
    local, _repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock.current = _parse_time("2099-01-01T00:00:00Z")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "expired-owner-cli",
        base_bytes,
        lease_id="fixture-lease",
        now="2099-01-01T00:00:00Z",
        phase="reviewing",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-expired-owner-renew",
            lease_id="fixture-lease",
            argv=("set", "phase=reviewing"),
        )
    )
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    local.commit(prepared, prepared.precondition)

    lease = local.read("test").state.lease
    assert lease.fencing_epoch == 1
    assert lease.lease_id == "fixture-lease"
    assert lease.lease_history == ()


def test_expired_base_renewal_target_expiry_after_stage_rejects(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock.current = _parse_time("2099-01-01T00:00:00Z")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "expired-target-cli",
        base_bytes,
        lease_id="fixture-lease",
        now="2099-01-01T00:00:00Z",
        phase="reviewing",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-expired-renew-target",
            lease_id="fixture-lease",
            argv=("set", "phase=reviewing"),
        )
    )
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    before = _public_bytes(repository)
    clock.current = _parse_time(admitted.pending_lease.target.lease_expires_at)

    with pytest.raises(FencedCommitError) as rejected:
        local.commit(prepared, prepared.precondition)

    assert rejected.value.code == "lease-precondition-changed"
    assert _public_bytes(repository) == before
    assert not prepared.staged.root.exists()


def test_reload_redecide_restage_commits_only_one_combined_next_generation(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock.current = _parse_time("2099-01-01T00:00:00Z")
    first_bytes = _cli_mutation_from_bytes(
        tmp_path / "first-cli",
        base_bytes,
        lease_id="first-lease",
        now="2099-01-01T00:00:00Z",
        phase="reviewing",
    )
    stale_bytes = _cli_mutation_from_bytes(
        tmp_path / "stale-cli",
        base_bytes,
        lease_id="stale-lease",
        now="2099-01-01T00:00:00Z",
        phase="scoring",
    )
    first = local.begin(
        _request(operation_id="operation-first", lease_id="first-lease", argv=("set", "phase=reviewing"))
    )
    stale = local.begin(
        _request(operation_id="operation-stale", lease_id="stale-lease", argv=("set", "phase=scoring"))
    )
    first_bytes = _with_admitted_takeover_reason(first_bytes, first)
    stale_bytes = _with_admitted_takeover_reason(stale_bytes, stale)
    first_prepared = local._stage_persistence(first, state_bytes=first_bytes, effects=())
    stale_prepared = local._stage_persistence(stale, state_bytes=stale_bytes, effects=())
    local.commit(first_prepared, first_prepared.precondition)
    with pytest.raises(FencedCommitError):
        local.commit(stale_prepared, stale_prepared.precondition)

    clock.current = _parse_time("2099-01-01T00:00:01Z")
    fresh_bytes = _cli_mutation_from_bytes(
        tmp_path / "fresh-cli",
        first_bytes,
        lease_id="first-lease",
        now="2099-01-01T00:00:01Z",
        phase="scoring",
    )
    fresh = local.begin(
        _request(operation_id="operation-fresh", lease_id="first-lease", argv=("set", "phase=scoring"))
    )
    fresh_prepared = local._stage_persistence(fresh, state_bytes=fresh_bytes, effects=())
    local.commit(fresh_prepared, fresh_prepared.precondition)

    snapshot = local.read("test")
    assert snapshot.head.generation == 3
    assert snapshot.state.lease.fencing_epoch == 2
    assert len(list((repository / "generations").glob("*.json"))) == 3


@pytest.mark.parametrize(
    "attack",
    ["duplicate", "invalid-utf8", "non-finite", "oversize", "noncanonical", "symlink", "hardlink", "digest"],
)
def test_head_attacks_fail_closed(tmp_path, attack):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, _clock, _state_path, _state_bytes, _result = _commit_cli_init(tmp_path)
    head = repository / "sessions" / "test.json"
    original = head.read_bytes()
    if attack == "duplicate":
        head.write_bytes(b'{"schema":"mission-head/1","schema":"mission-head/1"}')
    elif attack == "invalid-utf8":
        head.write_bytes(b"{\xff}")
    elif attack == "non-finite":
        head.write_bytes(b'{"generation":NaN}')
    elif attack == "oversize":
        head.write_bytes(b" " * 4097)
    elif attack == "noncanonical":
        head.write_text(json.dumps(json.loads(original), indent=2), encoding="utf-8")
    elif attack == "symlink":
        backup = head.with_name("head-backup")
        os.replace(head, backup)
        head.symlink_to(backup.name)
    elif attack == "hardlink":
        backup = head.with_name("head-backup")
        os.replace(head, backup)
        os.link(backup, head)
    else:
        document = json.loads(original)
        document["commit"]["digest"] = "sha256:" + "0" * 64
        head.write_bytes(
            json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )

    with pytest.raises(FencedCommitError):
        local.read("test")


@pytest.mark.parametrize(
    "attack",
    ["duplicate", "invalid-utf8", "non-finite", "oversize", "symlink", "hardlink", "digest", "size"],
)
def test_commit_record_attacks_fail_closed(tmp_path, attack):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, _clock, _state_path, _state_bytes, _result = _commit_cli_init(tmp_path)
    head_document = json.loads((repository / "sessions" / "test.json").read_bytes())
    commit = repository / head_document["commit"]["path"]
    if attack == "duplicate":
        commit.write_bytes(b'{"schema":"mission-commit/1","schema":"mission-commit/1"}')
    elif attack == "invalid-utf8":
        commit.write_bytes(b"{\xff}")
    elif attack == "non-finite":
        commit.write_bytes(b'{"fencing_epoch":NaN}')
    elif attack == "oversize":
        commit.write_bytes(b" " * (4 * 1024 * 1024 + 1))
    elif attack == "symlink":
        backup = commit.with_name("commit-backup")
        os.replace(commit, backup)
        commit.symlink_to(backup.name)
    elif attack == "hardlink":
        backup = commit.with_name("commit-backup")
        os.replace(commit, backup)
        os.link(backup, commit)
    elif attack == "digest":
        content = bytearray(commit.read_bytes())
        content[-1] = ord(" ")
        commit.write_bytes(bytes(content))
    else:
        head_document["commit"]["size"] += 1
        (repository / "sessions" / "test.json").write_bytes(
            json.dumps(head_document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )

    with pytest.raises(FencedCommitError):
        local.read("test")


def test_state_object_hardlink_fails_closed_at_authoritative_read(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, _clock, _state_path, _state_bytes, _result = _commit_cli_init(tmp_path)
    snapshot = local.read("test")
    state_object = repository / snapshot.commit.state.path
    backup = state_object.with_name("state-backup")
    os.replace(state_object, backup)
    os.link(backup, state_object)

    with pytest.raises(FencedCommitError):
        local.read("test")


@pytest.mark.parametrize("attack", ["duplicate", "invalid-utf8", "non-finite", "oversize"])
def test_stage_rejects_strict_state_attacks_derived_from_cli_bytes(tmp_path, attack):
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    _path, actual = generate_cli_state_bytes(tmp_path / "cli")
    document = json.loads(actual)
    clock = _Clock(_parse_time(document["lease_expires_at"]) - timedelta(seconds=900))
    local = LocalFencedRepository(tmp_path / "repository" / ".mission-state", clock=clock)
    admitted = local.begin(
        _request(operation_id="operation-attack", lease_id="fixture-lease", argv=("init",))
    )
    if attack == "duplicate":
        attacked = b'{"schema_version":4,' + actual.lstrip()[1:]
    elif attack == "invalid-utf8":
        attacked = actual[:-1] + b"\xff}"
    elif attack == "non-finite":
        attacked = actual.replace(b'"threshold": 4.0', b'"threshold": NaN', 1)
    else:
        attacked = actual + b" " * (4 * 1024 * 1024 + 1 - len(actual))

    with pytest.raises((FencedCommitError, ValueError)):
        local._stage_persistence(admitted, state_bytes=attacked, effects=())


def test_same_operation_and_intent_returns_one_result_and_different_intent_rejects(tmp_path):
    from mission_persistence.fenced_commit import CommitResult, FencedCommitError

    local, _repository, _clock, _state_path, _state_bytes, result = _commit_cli_init(tmp_path)
    same = _request(
        operation_id="operation-init",
        lease_id="fixture-lease",
        argv=("init", "Issue 500 CLI corpus"),
        command_type="init",
        event_types=("mission-initialized",),
    )
    replay = local.begin(same)

    assert isinstance(replay, CommitResult)
    assert replay == result
    with pytest.raises(FencedCommitError) as collision:
        local.begin(
            _request(
                operation_id="operation-init",
                lease_id="fixture-lease",
                argv=("init", "different normalized intent"),
                command_type="init",
                event_types=("mission-initialized",),
            )
        )
    assert collision.value.code == "operation-intent-collision"


def test_operation_tombstone_replay_does_not_require_or_root_commit_record(tmp_path):
    from mission_persistence.fenced_commit import CommitResult

    local, repository, _clock, _state_path, _state_bytes, result = _commit_cli_init(tmp_path)
    snapshot = local.read("test")
    (repository / snapshot.head.commit.path).unlink()
    same = _request(
        operation_id="operation-init",
        lease_id="fixture-lease",
        argv=("init", "Issue 500 CLI corpus"),
        command_type="init",
        event_types=("mission-initialized",),
    )

    replay = local.begin(same)

    assert isinstance(replay, CommitResult)
    assert replay == result


def test_commit_audit_contains_no_lease_token_or_raw_provider_secret(tmp_path):
    from mission_persistence.fenced_commit import CommitResult, LocalFencedRepository

    cli_root = tmp_path / "cli"
    _path, state_bytes = generate_cli_state_bytes(cli_root)
    state = json.loads(state_bytes)
    clock = _Clock(_parse_time(state["lease_expires_at"]) - timedelta(seconds=900))
    local = LocalFencedRepository(tmp_path / "repository" / ".mission-state", clock=clock)
    request = _request(
        operation_id="operation-secret-boundary",
        lease_id="fixture-lease",
        argv=("specialists", "invoke-prepared"),
        command_type="fixture-lease",
        event_types=("raw-provider-secret-value",),
        extra_command={"provider_secret": "raw-provider-secret-value"},
    )
    admitted = local.begin(request)
    assert not isinstance(admitted, CommitResult)
    prepared = local._stage_persistence(admitted, state_bytes=state_bytes, effects=())
    local.commit(prepared, prepared.precondition)
    commit_bytes = local.read("test").commit_bytes

    assert b"fixture-lease" not in commit_bytes
    assert b"raw-provider-secret-value" not in commit_bytes
    assert b"provider_secret" not in commit_bytes
    assert json.loads(commit_bytes)["audit"] == {
        "command_type_digest": "sha256:"
        + hashlib.sha256(b"fixture-lease").hexdigest(),
        "event_type_digests": [
            "sha256:" + hashlib.sha256(b"raw-provider-secret-value").hexdigest()
        ],
    }


@pytest.mark.parametrize("fault_point", ["before-head-replace", "after-head-replace"])
def test_head_replacement_is_the_crash_authority_boundary(tmp_path, fault_point):
    from mission_persistence.fenced_commit import AdmittedSnapshot

    fired = False

    def fault_injector(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise RuntimeError("injected crash")

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(
        tmp_path,
        fault_injector=fault_injector,
    )
    clock.current = _parse_time("2099-01-01T00:00:00Z")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "target-cli",
        base_bytes,
        lease_id="replacement-lease",
        now="2099-01-01T00:00:00Z",
        phase="reviewing",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-crash",
            lease_id="replacement-lease",
            argv=("set", "phase=reviewing"),
        )
    )
    target_bytes = _with_admitted_takeover_reason(target_bytes, admitted)
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())

    with pytest.raises(RuntimeError, match="injected crash"):
        local.commit(prepared, prepared.precondition)

    snapshot = local.read("test")
    expected = base_bytes if fault_point == "before-head-replace" else target_bytes
    assert snapshot.state_bytes == expected
    assert list((repository / "transactions" / "prepared").glob("*.json"))
    admitted_after_recovery = local.begin(
        _request(
            operation_id="operation-after-crash",
            lease_id="replacement-lease",
            argv=("set", "phase=scoring"),
        )
    )
    assert isinstance(admitted_after_recovery, AdmittedSnapshot)
    assert not list((repository / "transactions" / "prepared").glob("*.json"))


def test_production_init_remains_v4_and_does_not_import_u2_repository(tmp_path):
    state_path, state_bytes = generate_cli_state_bytes(tmp_path / "cli")
    entrypoint = state_path.parents[2]
    mission_state_py = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"

    assert json.loads(state_bytes)["schema_version"] == 4
    assert "fenced_commit" not in mission_state_py.read_text(encoding="utf-8")
    assert not (entrypoint / ".mission-state" / "commits").exists()


def test_lock_symlink_is_rejected_without_chmod_or_follow(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    _path, state_bytes = generate_cli_state_bytes(tmp_path / "cli")
    state = json.loads(state_bytes)
    clock = _Clock(_parse_time(state["lease_expires_at"]) - timedelta(seconds=900))
    repository = tmp_path / "repository" / ".mission-state"
    repository.mkdir(parents=True)
    victim = repository.parent / "victim"
    victim.write_bytes(b"must-not-be-opened-as-lock")
    os.chmod(victim, 0o644)
    (repository / ".state.lock").symlink_to(victim)
    local = LocalFencedRepository(repository, clock=clock)

    with pytest.raises(FencedCommitError):
        local.begin(
            _request(operation_id="operation-lock-link", lease_id="fixture-lease", argv=("init",))
        )

    assert stat.S_IMODE(victim.stat().st_mode) == 0o644
    assert victim.read_bytes() == b"must-not-be-opened-as-lock"


def test_lock_hardlink_is_rejected_without_chmod(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    repository.mkdir(parents=True)
    victim = repository.parent / "victim"
    victim.write_bytes(b"must-not-be-used-as-lock")
    os.chmod(victim, 0o644)
    os.link(victim, repository / ".state.lock")
    local = LocalFencedRepository(repository)

    with pytest.raises(FencedCommitError):
        local.begin(
            _request(operation_id="operation-lock-hardlink", lease_id="fixture-lease", argv=("init",))
        )

    assert stat.S_IMODE(victim.stat().st_mode) == 0o644
    assert victim.read_bytes() == b"must-not-be-used-as-lock"


def test_lock_name_swap_cannot_create_a_second_cas_authority(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)

    with pytest.raises(FencedCommitError) as changed:
        with local._lock():
            (repository / ".state.lock").rename(repository / ".detached-state.lock")
            (repository / ".state.lock").write_bytes(b"")
            competing_root = os.open(
                repository,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(competing_root, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(competing_root)

    assert changed.value.code == "repository-changed"


def test_commit_directory_swap_cannot_redirect_immutable_publication(monkeypatch, tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    original_link = os.link
    swapped = False

    def swapping_link(source, destination, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            (repository / "commits").rename(repository / "detached-commits")
            (repository / "commits").mkdir(mode=0o700)
        return original_link(source, destination, **kwargs)

    with local._lock():
        monkeypatch.setattr("mission_persistence.fenced_commit.os.link", swapping_link)
        with pytest.raises(FencedCommitError) as rejected:
            local._publish_named_immutable(
                repository / "commits" / "candidate.json",
                b"candidate",
                limit=1024,
                collision_code="immutable-commit-collision",
            )

    assert rejected.value.code == "repository-changed"
    assert not (repository / "commits" / "candidate.json").exists()
    assert (repository / "detached-commits" / "candidate.json").read_bytes() == b"candidate"


def test_sessions_directory_swap_cannot_redirect_head_replace(monkeypatch, tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    repository = tmp_path / "repository" / ".mission-state"
    local = LocalFencedRepository(repository)
    original_replace = os.replace
    swapped = False

    def swapping_replace(source, destination, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            (repository / "sessions").rename(repository / "detached-sessions")
            (repository / "sessions").mkdir(mode=0o700)
        return original_replace(source, destination, **kwargs)

    with local._lock():
        monkeypatch.setattr("mission_persistence.fenced_commit.os.replace", swapping_replace)
        with pytest.raises(FencedCommitError) as rejected:
            local._replace_head("test", b"candidate-head")

    assert rejected.value.code == "repository-changed"
    assert not (repository / "sessions" / "test.json").exists()
    assert (repository / "detached-sessions" / "test.json").read_bytes() == b"candidate-head"


def test_begin_reads_one_exact_head_snapshot(monkeypatch, tmp_path):
    local, _repository, _clock, _state_path, _state_bytes, _result = _commit_cli_init(tmp_path)
    original = local._read_head_unlocked
    calls = 0

    def counted(session_id):
        nonlocal calls
        calls += 1
        return original(session_id)

    monkeypatch.setattr(local, "_read_head_unlocked", counted)
    local.begin(
        _request(
            operation_id="operation-single-head-read",
            lease_id="fixture-lease",
            argv=("set", "phase=reviewing"),
        )
    )

    assert calls == 1


def test_malformed_other_session_prepare_blocks_globally(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, _clock, _state_path, _state_bytes, _result = _commit_cli_init(tmp_path)
    malformed = {
        "audit": None,
        "base": None,
        "effects": None,
        "fencing_epoch": None,
        "generation": None,
        "intent_digest": None,
        "operation_id": None,
        "prepared_at": None,
        "projections": None,
        "schema": "mission-prepare/1",
        "session_id": "other-session",
        "state": None,
        "target_generation": None,
        "transaction_id": None,
    }
    path = repository / "transactions" / "prepared" / "wrong-name.json"
    path.write_bytes(json.dumps(malformed, sort_keys=True, separators=(",", ":")).encode())

    with pytest.raises(FencedCommitError) as rejected:
        local.begin(
            _request(
                operation_id="operation-malformed-prepare",
                lease_id="fixture-lease",
                argv=("set", "phase=reviewing"),
            )
        )

    assert rejected.value.code == "recovery-ambiguous"


@pytest.mark.parametrize("attack", ["mutable-nested", "duplicate-key"])
def test_runtime_unchecked_frozen_command_is_rejected(tmp_path, attack):
    from mission_kernel.model import FrozenJsonObject
    from mission_persistence.fenced_commit import AuditMetadata, ExecutionRequest, FencedCommitError, LocalFencedRepository
    from mission_persistence.local_uow import VerifiedBlobSet

    _path, state_bytes = generate_cli_state_bytes(tmp_path / "cli")
    state = json.loads(state_bytes)
    clock = _Clock(_parse_time(state["lease_expires_at"]) - timedelta(seconds=900))
    local = LocalFencedRepository(tmp_path / "repository" / ".mission-state", clock=clock)
    if attack == "mutable-nested":
        command = FrozenJsonObject((("argv", ["init"]), ("schema", "mission-command-intent/1")))
    else:
        command = FrozenJsonObject((("argv", ("init",)), ("argv", ("set",))))
    command_bytes = encode_json_object(command)
    request = ExecutionRequest(
        session_id="test",
        lease_owner_session_id="test",
        command=command,
        blobs=VerifiedBlobSet(()),
        operation_id="operation-forged-frozen",
        intent_digest="sha256:" + hashlib.sha256(command_bytes).hexdigest(),
        presented_lease_id="fixture-lease",
        audit=AuditMetadata("init", ()),
    )

    with pytest.raises(FencedCommitError) as rejected:
        local.begin(request)

    assert rejected.value.code == "request-invalid"


def test_runtime_unchecked_mutable_verified_blob_set_is_rejected_at_begin(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository
    from mission_persistence.local_uow import VerifiedBlobSet

    _path, state_bytes = generate_cli_state_bytes(tmp_path / "cli")
    state = json.loads(state_bytes)
    clock = _Clock(_parse_time(state["lease_expires_at"]) - timedelta(seconds=900))
    local = LocalFencedRepository(tmp_path / "repository" / ".mission-state", clock=clock)
    request = replace(
        _request(operation_id="operation-mutable-blobs", lease_id="fixture-lease", argv=("init",)),
        blobs=VerifiedBlobSet([]),
    )

    with pytest.raises(FencedCommitError) as rejected:
        local.begin(request)

    assert rejected.value.code == "request-invalid"


def test_clock_failure_before_prepare_discards_private_stage(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, clock, _state_path, state_bytes, _result = _commit_cli_init(tmp_path)
    admitted = local.begin(
        _request(
            operation_id="operation-clock-invalid-before-prepare",
            lease_id="fixture-lease",
            argv=("set", "phase=executing"),
        )
    )
    prepared = local._stage_persistence(admitted, state_bytes=state_bytes, effects=())
    public_before = _public_bytes(repository)

    def invalid_first_clock():
        return clock.current.replace(tzinfo=None)

    local.clock = invalid_first_clock

    with pytest.raises(FencedCommitError) as rejected:
        local.commit(prepared, prepared.precondition)

    assert rejected.value.code == "request-invalid"
    assert _public_bytes(repository) == public_before
    assert not list((repository / "transactions" / "prepared").glob("*.json"))
    assert not prepared.staged.root.exists()


def test_p1_exposes_typed_stage_and_keeps_raw_bytes_private(tmp_path):
    """P1 replaces the U2 seam with the ADR typed transition boundary."""
    from mission_persistence.fenced_commit import LocalFencedRepository

    local = LocalFencedRepository(tmp_path / "repository" / ".mission-state")

    assert callable(local.stage)
    assert callable(local._stage_persistence)


def test_u2_private_persistence_seam_is_not_exported_from_package_root():
    """H1: package consumers cannot reach the isolated U2 staging seam."""
    import mission_persistence

    forbidden_api = {
        "LocalFencedRepository",
        "PreparedCommit",
        "_stage_persistence",
        "stage",
    }

    assert forbidden_api.isdisjoint(getattr(mission_persistence, "__all__", ()))
    for name in forbidden_api:
        assert not hasattr(mission_persistence, name)


def test_production_entrypoints_do_not_import_u2_private_persistence_seam():
    """H1: U2 remains unreachable until P1 installs the typed boundary."""
    repository = Path(__file__).resolve().parents[3]
    forbidden_imports = {
        "mission_persistence.fenced_commit",
        "fenced_commit",
        "LocalFencedRepository",
        "PreparedCommit",
        "_stage_persistence",
    }

    for base in (repository / "skills" / "mission" / "bin", repository / "scripts"):
        for path in base.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.add(node.module or "")
                    imported.update(alias.name for alias in node.names)
            assert forbidden_imports.isdisjoint(imported), path


@pytest.mark.parametrize(
    "attack",
    [
        "request-command",
        "request-session-id",
        "request-owner-session-id",
        "request-operation-id",
        "request-intent-digest",
        "request-audit",
        "request-blobs",
        "request-lease-token",
        "admitted-base",
        "admitted-precondition",
        "admitted-pending-lease",
        "admitted-target-generation",
        "transaction-id",
        "prepared-precondition",
        "prepared-target-state",
        "prepared-state-effects",
        "staged-generation-digest",
        "different-valid-stage",
    ],
)
def test_commit_rejects_every_post_stage_binding_substitution(tmp_path, attack):
    """H2: PreparedCommit is a sealed binding of request, stage, and transaction."""
    from mission_persistence.fenced_commit import AuditMetadata, FencedCommitError
    from mission_persistence.local_uow import BlobBinding

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "binding-target-cli",
        base_bytes,
        lease_id="fixture-lease",
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="executing",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-stage-binding",
            lease_id="fixture-lease",
            argv=("set", "phase=executing"),
        )
    )
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    tampered = prepared
    if attack == "request-command":
        alternate_request = _request(
            operation_id="operation-stage-binding",
            lease_id="fixture-lease",
            argv=("set", "phase=scoring"),
        )
        tampered = replace(
            prepared,
            admitted=replace(prepared.admitted, request=alternate_request),
        )
    elif attack == "request-session-id":
        changed_request = _request(
            operation_id="operation-stage-binding",
            lease_id="fixture-lease",
            argv=("set", "phase=executing"),
            session_id="substituted-session",
            lease_owner_session_id="test",
        )
        tampered = replace(
            prepared,
            admitted=replace(prepared.admitted, request=changed_request),
        )
    elif attack == "request-owner-session-id":
        changed_request = _request(
            operation_id="operation-stage-binding",
            lease_id="fixture-lease",
            argv=("set", "phase=executing"),
            session_id="test",
            lease_owner_session_id="substituted-owner",
        )
        tampered = replace(
            prepared,
            admitted=replace(prepared.admitted, request=changed_request),
        )
    elif attack == "request-operation-id":
        changed_request = replace(
            prepared.admitted.request,
            operation_id="operation-substituted-after-stage",
        )
        tampered = replace(
            prepared,
            admitted=replace(prepared.admitted, request=changed_request),
        )
    elif attack == "request-intent-digest":
        changed_request = replace(
            prepared.admitted.request,
            intent_digest="sha256:" + "0" * 64,
        )
        tampered = replace(
            prepared,
            admitted=replace(prepared.admitted, request=changed_request),
        )
    elif attack == "request-audit":
        changed_request = replace(
            prepared.admitted.request,
            audit=AuditMetadata("substituted-audit", ("substituted-event",)),
        )
        tampered = replace(
            prepared,
            admitted=replace(prepared.admitted, request=changed_request),
        )
    elif attack == "request-blobs":
        changed_request = _request(
            operation_id="operation-stage-binding",
            lease_id="fixture-lease",
            argv=("set", "phase=executing"),
            blobs=_verified_blob_set(base_bytes, blob_id="substituted-blob"),
        )
        tampered = replace(
            prepared,
            admitted=replace(prepared.admitted, request=changed_request),
        )
    elif attack == "request-lease-token":
        changed_request = replace(
            prepared.admitted.request,
            presented_lease_id="substituted-token",
        )
        tampered = replace(
            prepared,
            admitted=replace(prepared.admitted, request=changed_request),
        )
    elif attack == "admitted-base":
        tampered = replace(
            prepared,
            admitted=replace(prepared.admitted, base=None),
        )
    elif attack == "admitted-precondition":
        changed_precondition = replace(
            prepared.admitted.precondition,
            base_generation=prepared.admitted.precondition.base_generation + 7,
        )
        tampered = replace(
            prepared,
            admitted=replace(prepared.admitted, precondition=changed_precondition),
        )
    elif attack == "admitted-pending-lease":
        changed_pending = replace(
            prepared.admitted.pending_lease,
            action="substituted-action",
        )
        tampered = replace(
            prepared,
            admitted=replace(prepared.admitted, pending_lease=changed_pending),
        )
    elif attack == "admitted-target-generation":
        tampered = replace(
            prepared,
            admitted=replace(
                prepared.admitted,
                target_generation=prepared.admitted.target_generation + 1,
            ),
        )
    elif attack == "transaction-id":
        tampered = replace(prepared, transaction_id="f" * 32)
    elif attack == "prepared-precondition":
        tampered = replace(
            prepared,
            precondition=replace(
                prepared.precondition,
                base_generation=prepared.precondition.base_generation + 7,
            ),
        )
    elif attack == "prepared-target-state":
        tampered = replace(prepared, target_state=prepared.admitted.base.state)
    elif attack == "prepared-state-effects":
        fake_effect = BlobBinding(
            blob_id="substituted-effect",
            kind="cli-output",
            relative_path="evidence/substituted.json",
            digest="sha256:" + hashlib.sha256(base_bytes).hexdigest(),
            size=len(base_bytes),
        )
        tampered = replace(prepared, state_bytes=base_bytes, effects=(fake_effect,))
    elif attack == "staged-generation-digest":
        tampered = replace(
            prepared,
            staged=replace(
                prepared.staged,
                generation_digest="sha256:" + "0" * 64,
            ),
        )
    else:
        alternate_bytes = _cli_mutation_from_bytes(
            tmp_path / "alternate-valid-stage-cli",
            base_bytes,
            lease_id="fixture-lease",
            now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
            phase="scoring",
        )
        alternate = local._stage_persistence(admitted, state_bytes=alternate_bytes, effects=())
        tampered = replace(prepared, staged=alternate.staged)
    public_before = _public_bytes(repository)

    with pytest.raises(FencedCommitError):
        local.commit(tampered, tampered.precondition)

    assert _public_bytes(repository) == public_before


def test_recomputed_prepared_binding_digest_cannot_authorize_substituted_request(tmp_path):
    """A caller cannot mint a new stage authorization by recomputing a public digest."""
    import mission_persistence.fenced_commit as fenced_commit

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "recomputed-binding-cli",
        base_bytes,
        lease_id="fixture-lease",
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="executing",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-recomputed-binding",
            lease_id="fixture-lease",
            argv=("set", "phase=executing"),
        )
    )
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    substituted_request = _request(
        operation_id="operation-recomputed-binding",
        lease_id="fixture-lease",
        argv=("set", "phase=scoring"),
    )
    forged = replace(
        prepared,
        admitted=replace(prepared.admitted, request=substituted_request),
    )
    forged = replace(
        forged,
        binding_digest=fenced_commit._prepared_binding_digest(forged),
    )
    public_before = _public_bytes(repository)

    with pytest.raises(fenced_commit.FencedCommitError) as rejected:
        local.commit(forged, forged.precondition)

    assert rejected.value.code == "precondition-mismatch"
    assert _public_bytes(repository) == public_before


def test_prepared_stage_is_authorized_only_by_its_repository_instance(tmp_path):
    """A prepared value is not a transferable authority between repository instances."""
    from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "instance-authority-cli",
        base_bytes,
        lease_id="fixture-lease",
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="executing",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-instance-authority",
            lease_id="fixture-lease",
            argv=("set", "phase=executing"),
        )
    )
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    other = LocalFencedRepository(repository, clock=clock)
    public_before = _public_bytes(repository)

    with pytest.raises(FencedCommitError) as rejected:
        other.commit(prepared, prepared.precondition)

    assert rejected.value.code == "precondition-mismatch"
    assert _public_bytes(repository) == public_before
    assert prepared.staged.root.exists()
    local.commit(prepared, prepared.precondition)


def test_stage_registry_tamper_rejects_and_invalidates_private_stage(tmp_path):
    """A corrupt repository-owned binding cannot authorize or remain reusable."""
    from mission_persistence.fenced_commit import FencedCommitError

    local, _repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "registry-tamper-cli",
        base_bytes,
        lease_id="fixture-lease",
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="executing",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-registry-tamper",
            lease_id="fixture-lease",
            argv=("set", "phase=executing"),
        )
    )
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    local._stage_binding_registry[prepared.transaction_id] = "sha256:" + "0" * 64

    with pytest.raises(FencedCommitError) as rejected:
        local.commit(prepared, prepared.precondition)

    assert rejected.value.code == "precondition-mismatch"
    assert prepared.transaction_id not in local._stage_binding_registry
    assert not prepared.staged.root.exists()


def test_versioned_intent_binds_operation_id():
    """An operation id is part of the canonical versioned intent envelope."""
    from mission_persistence.fenced_commit import compute_intent_digest

    request = _request(
        operation_id="operation-intent-a",
        lease_id="fixture-lease",
        argv=("set", "phase=executing"),
    )
    first = compute_intent_digest(
        session_id=request.session_id,
        lease_owner_session_id=request.lease_owner_session_id,
        operation_id="operation-intent-a",
        command=request.command,
        blobs=request.blobs,
    )
    second = compute_intent_digest(
        session_id=request.session_id,
        lease_owner_session_id=request.lease_owner_session_id,
        operation_id="operation-intent-b",
        command=request.command,
        blobs=request.blobs,
    )

    assert first != second


def test_same_operation_and_command_with_different_cli_blob_is_an_intent_collision(tmp_path):
    """H3: the versioned intent includes blob identity, bytes, and bindings."""
    from mission_persistence.fenced_commit import FencedCommitError

    local, _repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    first_blob = _verified_blob_set(base_bytes)
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "blob-intent-target-cli",
        base_bytes,
        lease_id="fixture-lease",
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="executing",
    )
    command = ("set", "phase=executing")
    first_request = _request(
        operation_id="operation-blob-intent",
        lease_id="fixture-lease",
        argv=command,
        blobs=first_blob,
    )
    admitted = local.begin(first_request)
    prepared = local._stage_persistence(
        admitted,
        state_bytes=target_bytes,
        effects=(first_blob.blobs[0].binding,),
    )
    local.commit(prepared, prepared.precondition)
    second_cli_bytes = _cli_mutation_from_bytes(
        tmp_path / "different-blob-cli",
        base_bytes,
        lease_id="fixture-lease",
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="reviewing",
    )
    second_request = _request(
        operation_id="operation-blob-intent",
        lease_id="fixture-lease",
        argv=command,
        blobs=_verified_blob_set(second_cli_bytes),
    )

    with pytest.raises(FencedCommitError) as collision:
        local.begin(second_request)

    assert collision.value.code == "operation-intent-collision"


def test_operation_replay_intentionally_excludes_audit_and_lease_token(tmp_path):
    """H3: transport audit and presented fencing token do not alter intent meaning."""
    local, _repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "intent-exclusion-cli",
        base_bytes,
        lease_id="fixture-lease",
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="executing",
    )
    first_request = _request(
        operation_id="operation-intent-exclusions",
        lease_id="fixture-lease",
        argv=("set", "phase=executing"),
    )
    admitted = local.begin(first_request)
    prepared = local._stage_persistence(
        admitted,
        state_bytes=target_bytes,
        effects=(),
    )
    winner = local.commit(prepared, prepared.precondition)
    replay_request = _request(
        operation_id="operation-intent-exclusions",
        lease_id="different-valid-token",
        argv=("set", "phase=executing"),
        command_type="different-audit-category",
        event_types=("different-audit-event",),
    )

    assert replay_request.intent_digest == first_request.intent_digest
    assert local.begin(replay_request) == winner


def test_expired_takeover_history_never_persists_raw_audit_token(tmp_path):
    """H4: takeover retirement reason is a closed non-secret code."""
    local, _repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock.current = _parse_time("2099-01-01T00:00:00Z")
    cli_target_bytes = _cli_mutation_from_bytes(
        tmp_path / "takeover-secret-cli",
        base_bytes,
        lease_id="replacement-lease",
        now="2099-01-01T00:00:00Z",
        phase="reviewing",
    )
    raw_audit_token = "raw-audit-token-must-not-persist"
    admitted = local.begin(
        _request(
            operation_id="operation-takeover-secret",
            lease_id="replacement-lease",
            argv=("set", "phase=reviewing"),
            command_type=raw_audit_token,
        )
    )
    target_document = json.loads(cli_target_bytes)
    target_document["lease_history"][-1]["reason"] = (
        admitted.pending_lease.target.lease_history[-1].reason
    )
    target_bytes = json.dumps(
        target_document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    local.commit(prepared, prepared.precondition)
    snapshot = local.read("test")

    assert raw_audit_token.encode("utf-8") not in snapshot.state_bytes
    assert snapshot.state.lease.lease_history[0].reason == "lease-expired-takeover"


def test_commit_rechecks_expiry_at_head_authority_boundary(tmp_path):
    """H5: expiry crossed after precheck cannot reach head replacement."""
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, _clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    expiry = _parse_time(json.loads(base_bytes)["lease_expires_at"])
    admitted_at = expiry - timedelta(seconds=2)
    local.clock = _Clock(admitted_at)
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "expiry-race-cli",
        base_bytes,
        lease_id="fixture-lease",
        now=admitted_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="reviewing",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-expiry-race",
            lease_id="fixture-lease",
            argv=("set", "phase=reviewing"),
        )
    )
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    base_snapshot = local.read("test")
    local.clock = _SequenceClock((expiry - timedelta(seconds=1), expiry))

    with pytest.raises(FencedCommitError) as rejected:
        local.commit(prepared, prepared.precondition)

    assert rejected.value.code == "lease-precondition-changed"
    assert local.read("test").state_bytes == base_bytes
    assert local.read("test").head_bytes == base_snapshot.head_bytes
    assert not local._operation_path("test", "operation-expiry-race").exists()
    assert list((repository / "transactions" / "prepared").glob("*.json"))
    report = local.recover("test")
    assert report.ready is True
    assert not list((repository / "transactions" / "prepared").glob("*.json"))


def test_fault_hook_expiry_crossing_is_rechecked_at_os_replace_boundary(tmp_path):
    """The final lease sample must occur after the last pre-replace fault hook."""
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, _clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    base_snapshot = local.read("test")
    expiry = _parse_time(json.loads(base_bytes)["lease_expires_at"])
    admitted_at = expiry - timedelta(seconds=2)
    mutable_clock = _Clock(admitted_at)
    local.clock = mutable_clock
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "replace-boundary-expiry-cli",
        base_bytes,
        lease_id="fixture-lease",
        now=admitted_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="reviewing",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-replace-boundary-expiry",
            lease_id="fixture-lease",
            argv=("set", "phase=reviewing"),
        )
    )
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    mutable_clock.current = expiry - timedelta(seconds=1)

    def cross_expiry(point: str) -> None:
        if point == "before-head-replace":
            mutable_clock.current = expiry

    local.fault_injector = cross_expiry

    with pytest.raises(FencedCommitError) as rejected:
        local.commit(prepared, prepared.precondition)

    assert rejected.value.code == "lease-precondition-changed"
    assert prepared.transaction_id not in local._stage_binding_registry
    snapshot = local.read("test")
    assert snapshot.head_bytes == base_snapshot.head_bytes
    assert snapshot.state_bytes == base_bytes
    assert not local._operation_path(
        "test", "operation-replace-boundary-expiry"
    ).exists()
    assert list((repository / "transactions" / "prepared").glob("*.json"))
    report = local.recover("test")
    assert report.ready is True
    assert not list((repository / "transactions" / "prepared").glob("*.json"))


def test_valid_retained_prepare_for_other_session_blocks_repository_globally(tmp_path):
    """U3 still refuses to recover a transaction for a different session."""
    from mission_persistence.fenced_commit import FencedCommitError

    def crash_before_head(point: str) -> None:
        if point == "before-head-replace":
            raise RuntimeError("retain valid prepare")

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(
        tmp_path,
        fault_injector=crash_before_head,
    )
    clock.current = _parse_time("2099-01-01T00:00:00Z")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "retained-prepare-cli",
        base_bytes,
        lease_id="replacement-lease",
        now="2099-01-01T00:00:00Z",
        phase="reviewing",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-retained-prepare",
            lease_id="replacement-lease",
            argv=("set", "phase=reviewing"),
        )
    )
    target_bytes = _with_admitted_takeover_reason(target_bytes, admitted)
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    with pytest.raises(RuntimeError, match="retain valid prepare"):
        local.commit(prepared, prepared.precondition)
    assert list((repository / "transactions" / "prepared").glob("*.json"))
    local.fault_injector = None
    other_request = _request(
        operation_id="operation-other-session",
        lease_id="other-lease",
        argv=("init", "other session"),
        session_id="other-session",
        lease_owner_session_id="other-session",
    )

    with pytest.raises(FencedCommitError) as blocked:
        local.begin(other_request)

    assert blocked.value.code == "recovery-ambiguous"


def _oversize_effect_documents():
    effects = []
    for index in range(5):
        digest = "sha256:" + hashlib.sha256(("effect-%d" % index).encode("ascii")).hexdigest()
        effects.append(
            {
                "blob_id": "effect-%d" % index,
                "digest": digest,
                "kind": "cli-output",
                "object": "objects/" + digest.removeprefix("sha256:") + ".blob",
                "relative_path": "evidence/effect-%d.json" % index,
                "size": 4 * 1024 * 1024,
            }
        )
    return effects


@pytest.mark.parametrize("record_kind", ["commit", "prepare", "manifest"])
def test_authoritative_records_reject_effect_aggregate_over_16_mib(tmp_path, record_kind):
    """M1: every authoritative reader re-enforces U1's aggregate limit."""
    import mission_persistence.fenced_commit as fenced_commit

    local, _repository, _clock, _state_path, _state_bytes, _result = _commit_cli_init(tmp_path)
    snapshot = local.read("test")
    effects = _oversize_effect_documents()
    if record_kind == "commit":
        document = json.loads(snapshot.commit_bytes)
        document["effects"] = effects
        content = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")

        def read_record():
            return fenced_commit._parse_commit(content)
    elif record_kind == "prepare":
        document = json.loads(snapshot.commit_bytes)
        document.pop("committed_at")
        document["prepared_at"] = "2099-01-01T00:00:00Z"
        document["projections"] = []
        document["schema"] = "mission-prepare/1"
        document["effects"] = effects
        content = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")

        def read_record():
            return fenced_commit._parse_prepare(
                content,
                document["transaction_id"] + ".json",
            )
    else:
        manifest = json.loads((local.root / snapshot.commit.generation.path).read_bytes())
        manifest["blobs"] = effects
        content = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")

        def read_record():
            return local._manifest_records(content)

    with pytest.raises(fenced_commit.FencedCommitError) as rejected:
        read_record()

    assert rejected.value.code == "blob-set-too-large"


def test_rejection_surfaces_private_stage_cleanup_failure(monkeypatch, tmp_path):
    """M2: cleanup failure cannot be hidden behind the original rejection."""
    import mission_persistence.fenced_commit as fenced_commit
    from mission_persistence.local_uow import LocalUnitOfWorkError

    local, _repository, _clock, _state_path, state_bytes, _result = _commit_cli_init(tmp_path)
    admitted = local.begin(
        _request(
            operation_id="operation-cleanup-failure",
            lease_id="fixture-lease",
            argv=("set", "phase=executing"),
        )
    )
    prepared = local._stage_persistence(admitted, state_bytes=state_bytes, effects=())

    def fail_cleanup(_repository, _staged):
        raise LocalUnitOfWorkError("stage-cleanup-failed", "injected cleanup failure")

    monkeypatch.setattr(fenced_commit, "discard_staged_generation", fail_cleanup)
    wrong_precondition = replace(
        prepared.precondition,
        pending_lease_digest="sha256:" + "0" * 64,
    )

    with pytest.raises(fenced_commit.FencedCommitError) as rejected:
        local.commit(prepared, wrong_precondition)

    assert rejected.value.code == "stage-cleanup-failed"
    assert isinstance(rejected.value.__cause__, fenced_commit.FencedCommitError)
    assert rejected.value.__cause__.code == "precondition-mismatch"
    assert prepared.transaction_id not in local._stage_binding_registry


def test_replay_cleanup_failure_is_not_reported_as_success(monkeypatch, tmp_path):
    """M2: an idempotent winner is returned only after replay stage cleanup succeeds."""
    import mission_persistence.fenced_commit as fenced_commit
    from mission_persistence.local_uow import LocalUnitOfWorkError

    local, _repository, clock, _state_path, state_bytes, _result = _commit_cli_init(tmp_path)
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "replay-cleanup-cli",
        state_bytes,
        lease_id="fixture-lease",
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="executing",
    )
    request = _request(
        operation_id="operation-replay-cleanup-failure",
        lease_id="fixture-lease",
        argv=("set", "phase=executing"),
    )
    first = local.begin(request)
    replay = local.begin(request)
    first_prepared = local._stage_persistence(first, state_bytes=target_bytes, effects=())
    replay_prepared = local._stage_persistence(replay, state_bytes=target_bytes, effects=())
    local.commit(first_prepared, first_prepared.precondition)

    def fail_cleanup(_repository, _staged):
        raise LocalUnitOfWorkError("stage-cleanup-failed", "injected replay cleanup failure")

    monkeypatch.setattr(fenced_commit, "discard_staged_generation", fail_cleanup)

    with pytest.raises(fenced_commit.FencedCommitError) as rejected:
        local.commit(replay_prepared, replay_prepared.precondition)

    assert rejected.value.code == "stage-cleanup-failed"
    assert replay_prepared.transaction_id not in local._stage_binding_registry


def test_same_operation_id_is_independent_between_sessions(tmp_path):
    """M3: operation idempotency is namespaced by session."""
    from mission_persistence.fenced_commit import AdmittedSnapshot

    local, _repository, _clock, _state_path, _state_bytes, _result = _commit_cli_init(tmp_path)
    other_request = _request(
        operation_id="operation-init",
        lease_id="other-lease",
        argv=("init", "independent other session"),
        command_type="init",
        event_types=("mission-initialized",),
        session_id="other-session",
        lease_owner_session_id="other-session",
    )

    admitted = local.begin(other_request)

    assert isinstance(admitted, AdmittedSnapshot)


def test_cli_blob_effect_is_published_and_read_end_to_end(tmp_path):
    """M4: a non-empty effect survives stage, commit, object publication, and read."""
    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    blobs = _verified_blob_set(base_bytes)
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "effect-target-cli",
        base_bytes,
        lease_id="fixture-lease",
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="executing",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-effect-end-to-end",
            lease_id="fixture-lease",
            argv=("set", "phase=executing"),
            blobs=blobs,
        )
    )
    binding = blobs.blobs[0].binding
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=(binding,))

    local.commit(prepared, prepared.precondition)
    snapshot = local.read("test")

    assert snapshot.commit.effects[0].digest == binding.digest
    assert (repository / snapshot.commit.effects[0].object).read_bytes() == base_bytes


@pytest.mark.parametrize(
    "fault",
    ["short-write", "file-fsync", "link", "unlink", "replace", "post-replace-dir-fsync"],
)
def test_commit_io_faults_preserve_one_authoritative_head(monkeypatch, tmp_path, fault):
    """M4: low-level record I/O faults are surfaced without an ambiguous head."""
    import mission_persistence.fenced_commit as fenced_commit

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / (fault + "-cli"),
        base_bytes,
        lease_id="fixture-lease",
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="executing",
    )
    admitted = local.begin(
        _request(
            operation_id="operation-io-" + fault,
            lease_id="fixture-lease",
            argv=("set", "phase=executing"),
        )
    )
    prepared = local._stage_persistence(admitted, state_bytes=target_bytes, effects=())
    original_write = os.write
    original_fsync = os.fsync
    original_link = os.link
    original_unlink = os.unlink
    original_replace = os.replace
    fired = False
    head_replaced = False

    if fault == "short-write":
        def short_write(descriptor, content):
            nonlocal fired
            if not fired and len(content) > 1:
                fired = True
                return original_write(descriptor, content[: max(1, len(content) // 2)])
            return original_write(descriptor, content)

        monkeypatch.setattr(fenced_commit.os, "write", short_write)
        local.commit(prepared, prepared.precondition)
        assert fired
        assert local.read("test").state_bytes == target_bytes
        return

    if fault == "file-fsync":
        def failing_fsync(descriptor):
            nonlocal fired
            if not fired:
                fired = True
                raise OSError("injected file fsync failure")
            return original_fsync(descriptor)

        monkeypatch.setattr(fenced_commit.os, "fsync", failing_fsync)
    elif fault == "link":
        def failing_link(source, destination, **kwargs):
            nonlocal fired
            if not fired:
                fired = True
                raise OSError("injected link failure")
            return original_link(source, destination, **kwargs)

        monkeypatch.setattr(fenced_commit.os, "link", failing_link)
    elif fault == "unlink":
        def failing_unlink(path, **kwargs):
            nonlocal fired
            if not fired:
                fired = True
                raise OSError("injected unlink failure")
            return original_unlink(path, **kwargs)

        monkeypatch.setattr(fenced_commit.os, "unlink", failing_unlink)
    elif fault == "replace":
        def failing_replace(source, destination, **kwargs):
            nonlocal fired
            if not fired:
                fired = True
                raise OSError("injected replace failure")
            return original_replace(source, destination, **kwargs)

        monkeypatch.setattr(fenced_commit.os, "replace", failing_replace)
    else:
        def tracking_replace(source, destination, **kwargs):
            nonlocal head_replaced
            result = original_replace(source, destination, **kwargs)
            head_replaced = True
            return result

        def failing_post_replace_fsync(descriptor):
            nonlocal fired
            if head_replaced and not fired:
                fired = True
                raise OSError("injected post-replace directory fsync failure")
            return original_fsync(descriptor)

        monkeypatch.setattr(fenced_commit.os, "replace", tracking_replace)
        monkeypatch.setattr(fenced_commit.os, "fsync", failing_post_replace_fsync)

    with pytest.raises(fenced_commit.FencedCommitError):
        local.commit(prepared, prepared.precondition)

    assert fired
    expected = target_bytes if fault == "post-replace-dir-fsync" else base_bytes
    assert local.read("test").state_bytes == expected
    retained = list((repository / "transactions" / "prepared").glob("*.json"))
    assert bool(retained) == (fault in {"unlink", "replace", "post-replace-dir-fsync"})
