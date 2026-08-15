"""Issue #504: deterministic crash recovery for the isolated v5 repository."""

from __future__ import annotations

import os
import json
import ast
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from .mission_state_fixture_corpus import generate_cli_state_bytes
from .test_issue503_fenced_commit import (
    _cli_mutation_from_bytes,
    _commit_cli_init,
    _request,
    _verified_blob_set,
)


_MISSION_ROOT = Path(__file__).resolve().parents[1]
_LIB_ROOT = _MISSION_ROOT / "lib"


def _kill_after_private_stage(repository: Path, state_path: Path) -> subprocess.CompletedProcess:
    repository.parent.mkdir(parents=True, exist_ok=True)
    script = r'''
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mission_kernel.json_codec import decode_json_object
from mission_persistence.fenced_commit import (
    AuditMetadata,
    ExecutionRequest,
    LocalFencedRepository,
    compute_intent_digest,
)
from mission_persistence.local_uow import VerifiedBlobSet


repository = Path(sys.argv[1])
state_path = Path(sys.argv[2])
state_bytes = state_path.read_bytes()
state = json.loads(state_bytes.decode("utf-8"))
command = decode_json_object(
    b'{"argv":["init","actual-cli-state"],"schema":"mission-command-intent/1"}'
)
blobs = VerifiedBlobSet(())
operation_id = "operation-kill-after-stage"
intent_digest = compute_intent_digest(
    session_id="test",
    lease_owner_session_id="test",
    operation_id=operation_id,
    command=command,
    blobs=blobs,
)
request = ExecutionRequest(
    session_id="test",
    lease_owner_session_id="test",
    command=command,
    blobs=blobs,
    operation_id=operation_id,
    intent_digest=intent_digest,
    presented_lease_id=state["lease_id"],
    audit=AuditMetadata("init", ("mission-initialized",)),
)
expiry = datetime.strptime(state["lease_expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
    tzinfo=timezone.utc
)


def kill(point):
    if point == "after-stage":
        os._exit(91)


local = LocalFencedRepository(
    repository,
    clock=lambda: expiry - timedelta(seconds=900),
    fault_injector=kill,
)
admitted = local.begin(request)
local._stage_persistence(admitted, state_bytes=state_bytes, effects=())
'''
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.fspath(_LIB_ROOT)
    return subprocess.run(
        [sys.executable, "-c", script, os.fspath(repository), os.fspath(state_path)],
        cwd=os.fspath(repository.parent),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _authoritative_files(repository: Path) -> dict[str, bytes]:
    files = {}
    for directory_name in ("sessions", "objects", "generations", "commits", "operations"):
        directory = repository / directory_name
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir()):
            if path.is_file() and not path.is_symlink():
                files[path.relative_to(repository).as_posix()] = path.read_bytes()
    return files


def _all_regular_files(repository: Path) -> dict[str, bytes]:
    return {
        path.relative_to(repository).as_posix(): path.read_bytes()
        for path in sorted(repository.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _kill_during_commit(
    repository: Path,
    target_state_path: Path,
    *,
    clock_text: str,
    fault_point: str,
    projection_source: Path | None = None,
    projection_relative_path: str = "-",
    projection_source_2: Path | None = None,
    projection_relative_path_2: str = "-",
) -> subprocess.CompletedProcess:
    script = r'''
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from mission_kernel.json_codec import decode_json_object
from mission_persistence.fenced_commit import (
    AuditMetadata,
    ExecutionRequest,
    LocalFencedRepository,
    compute_intent_digest,
)
from mission_persistence.local_uow import BlobBinding, VerifiedBlob, VerifiedBlobSet


repository = Path(sys.argv[1])
target_state_path = Path(sys.argv[2])
clock = datetime.strptime(sys.argv[3], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
fault_point = sys.argv[4]
state_bytes = target_state_path.read_bytes()
command = decode_json_object(
    b'{"argv":["set","phase=reviewing"],"schema":"mission-command-intent/1"}'
)
projection_inputs = [
    (sys.argv[5], sys.argv[6]),
    (sys.argv[7], sys.argv[8]),
]
projection_inputs = [item for item in projection_inputs if item[0] != "-"]
captured = []
for index, (projection_source, projection_relative_path) in enumerate(projection_inputs):
    projection_bytes = Path(projection_source).read_bytes()
    digest = "sha256:" + __import__("hashlib").sha256(projection_bytes).hexdigest()
    blob_id = "compatibility-state" if len(projection_inputs) == 1 else "compatibility-state-%d" % index
    binding = BlobBinding(
        blob_id,
        "cli-output",
        projection_relative_path,
        digest,
        len(projection_bytes),
    )
    captured.append(VerifiedBlob(binding, projection_bytes))
blobs = VerifiedBlobSet(tuple(captured))
operation_id = "operation-crash-recovery"
intent_digest = compute_intent_digest(
    session_id="test",
    lease_owner_session_id="test",
    operation_id=operation_id,
    command=command,
    blobs=blobs,
)
request = ExecutionRequest(
    session_id="test",
    lease_owner_session_id="test",
    command=command,
    blobs=blobs,
    operation_id=operation_id,
    intent_digest=intent_digest,
    presented_lease_id="fixture-lease",
    audit=AuditMetadata("set", ("mission-state-changed",)),
)


def kill(point):
    if point == fault_point:
        os._exit(91)


local = LocalFencedRepository(repository, clock=lambda: clock, fault_injector=kill)
admitted = local.begin(request)
effects = tuple(blob.binding for blob in blobs.blobs)
prepared = local._stage_persistence(admitted, state_bytes=state_bytes, effects=effects)
local.commit(prepared, prepared.precondition)
'''
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.fspath(_LIB_ROOT)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            os.fspath(repository),
            os.fspath(target_state_path),
            clock_text,
            fault_point,
            os.fspath(projection_source) if projection_source is not None else "-",
            projection_relative_path,
            os.fspath(projection_source_2) if projection_source_2 is not None else "-",
            projection_relative_path_2,
        ],
        cwd=os.fspath(repository.parent),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _kill_during_recovery(
    repository: Path,
    *,
    fault_point: str,
) -> subprocess.CompletedProcess:
    script = r'''
import os
import sys
from pathlib import Path

from mission_persistence.fenced_commit import LocalFencedRepository


def kill(point):
    if point == sys.argv[2]:
        os._exit(91)


local = LocalFencedRepository(Path(sys.argv[1]), fault_injector=kill)
local.recover("test")
'''
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.fspath(_LIB_ROOT)
    return subprocess.run(
        [sys.executable, "-c", script, os.fspath(repository), fault_point],
        cwd=os.fspath(repository.parent),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_process_kill_after_stage_recovers_verified_residue_idempotently(tmp_path):
    from mission_persistence.fenced_commit import LocalFencedRepository

    state_path, state_bytes = generate_cli_state_bytes(tmp_path / "actual-cli")
    assert state_path.read_bytes() == state_bytes
    repository = tmp_path / "repository" / ".mission-state"

    killed = _kill_after_private_stage(repository, state_path)

    assert killed.returncode == 91, killed.stderr
    staged = list((repository / "transactions").glob(".stage-*"))
    assert len(staged) == 1
    before = _authoritative_files(repository)

    local = LocalFencedRepository(repository)
    first = local.recover("test")
    after_first = _authoritative_files(repository)
    all_after_first = _all_regular_files(repository)
    second = local.recover("test")

    assert first == second
    assert before == after_first == _authoritative_files(repository)
    assert all_after_first == _all_regular_files(repository)
    assert not list((repository / "transactions").glob(".stage-*"))


def test_process_kill_after_effect_stage_removes_bound_projection_bundle(tmp_path):
    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)
    projection = repository.parent / "compatibility" / "state.json"
    projection.parent.mkdir(parents=True)
    projection.write_bytes(base_bytes)

    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point="after-stage",
        projection_source=target_path,
        projection_relative_path="compatibility/state.json",
    )

    assert killed.returncode == 91, killed.stderr
    assert list((repository / "transactions").glob(".stage-*"))
    assert list((repository / "transactions" / "projections").iterdir())

    first = local.recover("test")
    all_after_first = _all_regular_files(repository)
    second = local.recover("test")

    assert first == second
    assert projection.read_bytes() == base_bytes
    assert all_after_first == _all_regular_files(repository)
    assert not list((repository / "transactions").glob(".stage-*"))
    assert not list((repository / "transactions" / "projections").iterdir())


@pytest.mark.parametrize(
    "fault_point",
    (
        "after-prepare",
        "after-generation-publish",
        "after-commit-publish",
        "before-head-replace",
    ),
)
def test_process_kill_before_head_rolls_back_to_exact_base_idempotently(
    tmp_path,
    fault_point,
):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)
    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point=fault_point,
    )

    assert killed.returncode == 91, killed.stderr
    assert list((repository / "transactions" / "prepared").glob("*.json"))
    assert local.read("test").state_bytes == base_bytes
    crashed_files = _authoritative_files(repository)

    first = local.recover("test")
    after_first = _authoritative_files(repository)
    all_after_first = _all_regular_files(repository)
    second = local.recover("test")

    assert first == second
    assert local.read("test").state_bytes == base_bytes
    assert crashed_files == after_first == _authoritative_files(repository)
    assert all_after_first == _all_regular_files(repository)
    assert not list((repository / "transactions" / "prepared").glob("*.json"))
    assert not list((repository / "transactions").glob(".stage-*"))
    with pytest.raises(FencedCommitError) as collision:
        local.begin(
            _request(
                operation_id="operation-crash-recovery",
                lease_id="fixture-lease",
                argv=("set", "phase=scoring"),
            )
        )
    assert collision.value.code == "operation-intent-collision"


def _projection_identity(path: Path) -> tuple[int, int, int, int, int]:
    metadata = path.lstat()
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def test_kill_after_projection_restores_exact_base_inode_and_cli_bytes(tmp_path):
    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)
    projection = repository.parent / "compatibility" / "state.json"
    projection.parent.mkdir(parents=True)
    projection.write_bytes(base_bytes)
    base_identity = _projection_identity(projection)

    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point="after-projection:0",
        projection_source=target_path,
        projection_relative_path="compatibility/state.json",
    )

    assert killed.returncode == 91, killed.stderr
    assert local.read("test").state_bytes == base_bytes
    assert projection.read_bytes() == target_bytes

    first = local.recover("test")
    second = local.recover("test")

    assert first == second
    assert projection.read_bytes() == base_bytes
    assert _projection_identity(projection) == base_identity
    assert local.read("test").state_bytes == base_bytes
    assert not list((repository / "transactions" / "prepared").glob("*.json"))
    assert not list((repository / "transactions").glob(".stage-*"))


def test_kill_after_first_projection_leaves_second_projection_at_base(tmp_path):
    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)
    first = repository.parent / "compatibility" / "first.json"
    second = repository.parent / "compatibility" / "second.json"
    first.parent.mkdir(parents=True)
    first.write_bytes(base_bytes)
    second.write_bytes(target_bytes)
    first_identity = _projection_identity(first)
    second_identity = _projection_identity(second)

    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point="after-projection:0",
        projection_source=target_path,
        projection_relative_path="compatibility/first.json",
        projection_source_2=Path(_state_path),
        projection_relative_path_2="compatibility/second.json",
    )

    assert killed.returncode == 91, killed.stderr
    assert first.read_bytes() == target_bytes
    assert second.read_bytes() == target_bytes

    first_recovery = local.recover("test")
    second_recovery = local.recover("test")

    assert first_recovery == second_recovery
    assert first.read_bytes() == base_bytes
    assert second.read_bytes() == target_bytes
    assert _projection_identity(first) == first_identity
    assert _projection_identity(second) == second_identity


@pytest.mark.parametrize(
    "fault_point",
    (
        "after-head-replace",
        "after-operation-publish",
        "after-lineage-verify",
        "after-resolution-marker",
        "after-finalize",
    ),
)
def test_kill_after_head_then_same_operation_retry_returns_original_result(
    tmp_path,
    fault_point,
):
    from mission_persistence.fenced_commit import CommitResult, FencedCommitError

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)

    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point=fault_point,
    )

    assert killed.returncode == 91, killed.stderr
    assert local.read("test").state_bytes == target_bytes
    assert bool(list((repository / "transactions" / "prepared").glob("*.json"))) == (
        fault_point != "after-finalize"
    )
    operation_path = local._operation_path("test", "operation-crash-recovery")
    assert operation_path.exists() == (fault_point != "after-head-replace")
    request = _request(
        operation_id="operation-crash-recovery",
        lease_id="fixture-lease",
        argv=("set", "phase=reviewing"),
    )

    replay = local.begin(request)
    files_after_recovery = _authoritative_files(repository)
    repeated = local.begin(request)

    assert isinstance(replay, CommitResult)
    assert repeated == replay
    assert local.read("test").state_bytes == target_bytes
    assert operation_path.exists()
    assert files_after_recovery == _authoritative_files(repository)
    assert len(list((repository / "generations").glob("*.json"))) == 2
    assert not list((repository / "transactions" / "prepared").glob("*.json"))
    with pytest.raises(FencedCommitError) as collision:
        local.begin(
            _request(
                operation_id="operation-crash-recovery",
                lease_id="fixture-lease",
                argv=("set", "phase=scoring"),
            )
        )
    assert getattr(collision.value, "code", None) == "operation-intent-collision"


def test_target_recovery_recreates_missing_projection_from_exact_private_inode(tmp_path):
    from mission_persistence.fenced_commit import CommitResult

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)
    projection = repository.parent / "compatibility" / "state.json"
    projection.parent.mkdir(parents=True)
    projection.write_bytes(base_bytes)

    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point="after-head-replace",
        projection_source=target_path,
        projection_relative_path="compatibility/state.json",
    )

    assert killed.returncode == 91, killed.stderr
    published_identity = _projection_identity(projection)
    assert projection.read_bytes() == target_bytes
    projection.unlink()
    # Match the child request's actual CLI projection binding.
    from mission_persistence.local_uow import BlobBinding, VerifiedBlob, VerifiedBlobSet
    import hashlib

    binding = BlobBinding(
        "compatibility-state",
        "cli-output",
        "compatibility/state.json",
        "sha256:" + hashlib.sha256(target_bytes).hexdigest(),
        len(target_bytes),
    )
    request = _request(
        operation_id="operation-crash-recovery",
        lease_id="fixture-lease",
        argv=("set", "phase=reviewing"),
        blobs=VerifiedBlobSet((VerifiedBlob(binding, target_bytes),)),
    )

    replay = local.begin(request)

    assert isinstance(replay, CommitResult)
    assert projection.read_bytes() == target_bytes
    assert _projection_identity(projection) == published_identity
    assert not list((repository / "transactions" / "prepared").glob("*.json"))
    assert not list((repository / "transactions" / "projections").iterdir())


def test_malformed_prepare_blocks_without_repair_or_evidence_deletion(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)
    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point="after-prepare",
    )
    assert killed.returncode == 91, killed.stderr
    prepare = next((repository / "transactions" / "prepared").glob("*.json"))
    prepare.write_bytes(b"{")

    with pytest.raises(FencedCommitError) as blocked:
        local.recover("test")

    assert blocked.value.code == "recovery-ambiguous"
    assert prepare.read_bytes() == b"{"
    assert local.read("test").state_bytes == base_bytes
    with pytest.raises(FencedCommitError) as repeated_blocked:
        local.recover("test")

    assert repeated_blocked.value.code == "recovery-ambiguous"
    with pytest.raises(FencedCommitError) as write_blocked:
        local.begin(
            _request(
                operation_id="operation-after-malformed-prepare",
                lease_id="fixture-lease",
                argv=("set", "phase=scoring"),
            )
        )
    assert write_blocked.value.code == "recovery-ambiguous"


def test_foreign_newer_head_blocks_without_guessed_rollback(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)
    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point="after-commit-publish",
    )
    assert killed.returncode == 91, killed.stderr
    prepare = next((repository / "transactions" / "prepared").glob("*.json"))
    head_path = repository / "sessions" / "test.json"
    foreign = json.loads(head_path.read_bytes())
    foreign["generation"] += 2
    foreign_bytes = json.dumps(
        foreign,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    head_path.write_bytes(foreign_bytes)

    with pytest.raises(FencedCommitError) as blocked:
        local.recover("test")

    assert blocked.value.code == "recovery-ambiguous"
    assert head_path.read_bytes() == foreign_bytes
    assert prepare.exists()


def test_operation_record_disagreement_blocks_target_recovery(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)
    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point="after-operation-publish",
    )
    assert killed.returncode == 91, killed.stderr
    operation = local._operation_path("test", "operation-crash-recovery")
    disagreed = json.loads(operation.read_bytes())
    disagreed["result"]["generation"] += 1
    disagreed_bytes = json.dumps(
        disagreed,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    operation.write_bytes(disagreed_bytes)
    prepare = next((repository / "transactions" / "prepared").glob("*.json"))

    with pytest.raises(FencedCommitError) as blocked:
        local.recover("test")

    assert blocked.value.code == "recovery-ambiguous"
    assert operation.read_bytes() == disagreed_bytes
    assert prepare.exists()


def test_base_recovery_rejects_same_operation_tombstone_without_deleting_evidence(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)
    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point="after-prepare",
    )
    assert killed.returncode == 91, killed.stderr
    prepare = next((repository / "transactions" / "prepared").glob("*.json"))
    operation = local._operation_path("test", "operation-crash-recovery")
    fake_digest = "sha256:" + "9" * 64
    fake = {
        "commit_digest": fake_digest,
        "intent_digest": _request(
            operation_id="operation-crash-recovery",
            lease_id="fixture-lease",
            argv=("set", "phase=reviewing"),
        ).intent_digest,
        "operation_id": "operation-crash-recovery",
        "result": {
            "commit_digest": fake_digest,
            "generation": 999,
            "head_digest": "sha256:" + "8" * 64,
            "state_generation_digest": "sha256:" + "7" * 64,
        },
        "schema": "mission-operation/1",
        "session_id": "test",
    }
    fake_bytes = json.dumps(
        fake,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    operation.write_bytes(fake_bytes)

    with pytest.raises(FencedCommitError) as blocked:
        local.recover("test")

    assert blocked.value.code == "recovery-ambiguous"
    assert prepare.exists()
    assert operation.read_bytes() == fake_bytes
    assert local.read("test").state_bytes == base_bytes


def test_kill_during_post_resolution_projection_cleanup_is_resumable(tmp_path):
    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)
    projection = repository.parent / "compatibility" / "state.json"
    projection.parent.mkdir(parents=True)
    projection.write_bytes(base_bytes)

    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point="during-projection-cleanup-base:0",
        projection_source=target_path,
        projection_relative_path="compatibility/state.json",
    )

    assert killed.returncode == 91, killed.stderr
    assert next((repository / "transactions" / "resolved").glob("*.json")).exists()
    assert next((repository / "transactions" / "prepared").glob("*.json")).exists()
    report = local.recover("test")
    repeated = local.recover("test")

    assert report == repeated
    assert projection.read_bytes() == target_bytes
    assert not list((repository / "transactions" / "prepared").glob("*.json"))
    assert not list((repository / "transactions" / "projections").iterdir())


def test_target_cleanup_resumes_after_private_after_link_is_removed(tmp_path):
    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)
    projection = repository.parent / "compatibility" / "state.json"
    projection.parent.mkdir(parents=True)
    projection.write_bytes(base_bytes)

    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point="during-projection-cleanup-after:0",
        projection_source=target_path,
        projection_relative_path="compatibility/state.json",
    )

    assert killed.returncode == 91, killed.stderr
    report = local.recover("test")
    repeated = local.recover("test")

    assert report == repeated
    assert projection.read_bytes() == target_bytes
    assert not list((repository / "transactions" / "prepared").glob("*.json"))
    assert not list((repository / "transactions" / "projections").iterdir())


def test_base_cleanup_resumes_after_private_after_file_is_removed(tmp_path):
    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)
    projection = repository.parent / "compatibility" / "state.json"
    projection.parent.mkdir(parents=True)
    projection.write_bytes(base_bytes)
    prepared = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point="after-prepare",
        projection_source=target_path,
        projection_relative_path="compatibility/state.json",
    )
    assert prepared.returncode == 91, prepared.stderr

    killed = _kill_during_recovery(
        repository,
        fault_point="during-projection-cleanup-after:0",
    )

    assert killed.returncode == 91, killed.stderr
    report = local.recover("test")
    repeated = local.recover("test")

    assert report == repeated
    assert projection.read_bytes() == base_bytes
    assert not list((repository / "transactions" / "prepared").glob("*.json"))
    assert not list((repository / "transactions" / "projections").iterdir())


def test_commit_rejects_projection_parent_replaced_after_stage(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase="reviewing",
    )
    blobs = _verified_blob_set(target_bytes)
    projection = repository.parent / "evidence" / "mission-state.json"
    projection.parent.mkdir(parents=True)
    projection.write_bytes(base_bytes)
    admitted = local.begin(
        _request(
            operation_id="operation-parent-replaced",
            lease_id="fixture-lease",
            argv=("set", "phase=reviewing"),
            blobs=blobs,
        )
    )
    prepared = local._stage_persistence(
        admitted,
        state_bytes=target_bytes,
        effects=(blobs.blobs[0].binding,),
    )
    detached_parent = repository.parent / "evidence-detached"
    projection.parent.rename(detached_parent)
    projection.parent.mkdir()
    os.rename(
        detached_parent / projection.name,
        projection,
    )

    with pytest.raises(FencedCommitError) as blocked:
        local.commit(prepared, prepared.precondition)

    assert blocked.value.code in {
        "recovery-ambiguous",
        "repository-changed",
    }
    assert projection.read_bytes() == base_bytes


def test_projection_parent_swap_during_recovery_blocks_without_writing_replacement(
    tmp_path,
    monkeypatch,
):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)
    projection = repository.parent / "compatibility" / "state.json"
    projection.parent.mkdir(parents=True)
    projection.write_bytes(base_bytes)
    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point="after-head-replace",
        projection_source=target_path,
        projection_relative_path="compatibility/state.json",
    )
    assert killed.returncode == 91, killed.stderr
    projection.unlink()
    original_parent = projection.parent
    detached_parent = repository.parent / "compatibility-detached"
    prepare = next((repository / "transactions" / "prepared").glob("*.json"))
    real_link = os.link
    swapped = False

    def swap_then_link(src, dst, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            swapped = True
            original_parent.rename(detached_parent)
            original_parent.mkdir()
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", swap_then_link)

    with pytest.raises(FencedCommitError) as blocked:
        local.recover("test")

    assert blocked.value.code in {"recovery-ambiguous", "repository-changed"}
    assert prepare.exists()
    assert not projection.exists()
    assert not (detached_parent / "state.json").exists()


def test_rollback_failure_preserves_verifiable_bundle_and_blocks_writes(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    local, repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
    clock_text = clock.current.strftime("%Y-%m-%dT%H:%M:%SZ")
    target_bytes = _cli_mutation_from_bytes(
        tmp_path / "actual-cli-target",
        base_bytes,
        lease_id="fixture-lease",
        now=clock_text,
        phase="reviewing",
    )
    target_path = tmp_path / "actual-cli-target-state.json"
    target_path.write_bytes(target_bytes)
    projection = repository.parent / "compatibility" / "state.json"
    projection.parent.mkdir(parents=True)
    projection.write_bytes(base_bytes)
    killed = _kill_during_commit(
        repository,
        target_path,
        clock_text=clock_text,
        fault_point="after-projection:0",
        projection_source=target_path,
        projection_relative_path="compatibility/state.json",
    )
    assert killed.returncode == 91, killed.stderr

    def fail_rollback(point: str) -> None:
        if point == "during-projection-rollback:0":
            raise RuntimeError("injected rollback stop")

    local.fault_injector = fail_rollback
    with pytest.raises(FencedCommitError) as interrupted:
        local.recover("test")

    assert interrupted.value.code == "recovery-ambiguous"
    prepare = next((repository / "transactions" / "prepared").glob("*.json"))
    bundle = next((repository / "transactions" / "projections").iterdir())
    base_backup = bundle / "base-000.blob"
    assert prepare.exists()
    assert base_backup.read_bytes() == base_bytes
    assert not projection.exists()
    with pytest.raises(FencedCommitError):
        local.begin(
            _request(
                operation_id="operation-blocked-by-rollback",
                lease_id="fixture-lease",
                argv=("set", "phase=scoring"),
            )
        )

    local.fault_injector = None
    report = local.recover("test")

    assert report.ready is True
    assert projection.read_bytes() == base_bytes
    assert not prepare.exists()


def test_recovery_has_no_domain_transition_or_production_route():
    source_paths = [
        _LIB_ROOT / "mission_persistence" / "fenced_commit.py",
        _LIB_ROOT / "mission_persistence" / "local_uow.py",
    ]
    imported = set()
    for source_path in source_paths:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)

    assert "mission_kernel.transitions" not in imported
    assert "mission_kernel.commands" not in imported
    production_sources = [
        *(_MISSION_ROOT / "bin").glob("*.py"),
        *(Path(__file__).resolve().parents[3] / "scripts").glob("*.py"),
    ]
    assert all(
        "mission_persistence.fenced_commit" not in path.read_text(encoding="utf-8")
        for path in production_sources
    )
