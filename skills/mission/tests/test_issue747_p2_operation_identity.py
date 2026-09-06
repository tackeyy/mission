"""#747 P2: stable operation identity.

Contracts 1-24 of the P2 design (Issue #747, design v1-v16).  Version-1
records come from genuine repositories written by the pre-P2 library
(``fixtures/issue747_p2_v1_records``, generated with ``2aaa2f2``), not from
hand-built documents, so the dual-read is exercised against what production
actually wrote.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mission_kernel.json_codec import decode_json_object

from .test_issue503_fenced_commit import _Clock, _commit_cli_init, _parse_time, _request

FIXTURES = Path(__file__).parent / "fixtures" / "issue747_p2_v1_records"
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class _Stop(Exception):
    pass


def _stop_at(point_name):
    def injector(point):
        if point == point_name:
            raise _Stop(point)

    return injector


def _v1_repository(tmp_path, kind):
    from mission_persistence.fenced_commit import LocalFencedRepository

    source = FIXTURES / kind
    assert (source / "mission-state" / "operations").is_dir(), (
        "the v1 fixture repository is missing; the dual-read contracts would pass vacuously"
    )
    dest = tmp_path / kind
    shutil.copytree(source, dest, symlinks=False)
    # The fixture keeps the repository as ``mission-state``: the real name is
    # gitignored (``.mission-state/``), so a dotted copy would never reach CI.
    (dest / "mission-state").rename(dest / ".mission-state")
    for path in dest.rglob("*"):
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    meta = json.loads((dest / "fixture.json").read_text(encoding="utf-8"))
    repository = dest / ".mission-state"
    clock = _Clock(_parse_time(meta["clock"]))
    local = LocalFencedRepository(repository, clock=clock, fault_injector=None)
    return local, repository, meta, clock


def _all_files(root: Path) -> dict:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def _all_dirs(root: Path) -> set:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()}


def _all_metadata(root: Path) -> dict:
    """Mode, inode and change time of everything under one root."""
    entries = {}
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        entries[path.relative_to(root).as_posix()] = (
            stat.S_IMODE(metadata.st_mode),
            metadata.st_ino,
            metadata.st_ctime_ns,
        )
    return entries


def _init_request(meta):
    return _request(
        operation_id=meta["init"]["operation_id"],
        lease_id=meta["lease_id"],
        argv=tuple(meta["init"]["argv"]),
        command_type=meta["init"]["command_type"],
        event_types=tuple(meta["init"]["event_types"]),
    )


def _v1_blob_set(dest: Path, meta):
    from mission_persistence.local_uow import BlobBinding, VerifiedBlob, VerifiedBlobSet

    blob = meta["blob_operation"]["blob"]
    content = (dest / blob["content_file"]).read_bytes()
    binding = BlobBinding(blob["blob_id"], blob["kind"], blob["relative_path"], blob["digest"], blob["size"])
    return VerifiedBlobSet((VerifiedBlob(binding, content),))


def _generated_blob_set(content: bytes, path: str = "build/manifest.json"):
    from mission_application.evidence_publication import derive_blob_id
    from mission_persistence.evidence_order import published_binding_type
    from mission_persistence.local_uow import VerifiedBlob, VerifiedBlobSet

    binding = published_binding_type()(
        blob_id=derive_blob_id(path),
        kind="context-manifest",
        relative_path=path,
        digest="sha256:" + hashlib.sha256(content).hexdigest(),
        size=len(content),
        origin="generated",
        target=Path(path).name,
    )
    return VerifiedBlobSet((VerifiedBlob(binding, content),))


def _commit_operation(local, request, state_bytes):
    from mission_persistence.fenced_commit import OperationReplay

    admitted = local.begin(request)
    assert not isinstance(admitted, OperationReplay)
    prepared = local._stage_persistence(
        admitted,
        state_bytes=state_bytes,
        effects=tuple(blob.binding for blob in request.blobs.blobs),
    )
    return local.commit(prepared, prepared.precondition)


def _mutated_state(tmp_path, base_bytes, clock, *, phase, lease_id="fixture-lease", name="target"):
    from .test_issue503_fenced_commit import _cli_mutation_from_bytes

    return _cli_mutation_from_bytes(
        tmp_path / name,
        base_bytes,
        lease_id=lease_id,
        now=clock.current.strftime("%Y-%m-%dT%H:%M:%SZ"),
        phase=phase,
    )


def _operation_record(repository: Path, session_id: str, operation_id: str) -> tuple[Path, dict]:
    from mission_persistence.fenced_commit import LocalFencedRepository

    local = LocalFencedRepository(repository, clock=lambda: datetime.now(timezone.utc))
    path = local._operation_path(session_id, operation_id)
    return path, json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Contract 1: the identity is the same before and after prepare
# --------------------------------------------------------------------------


class TestSemanticIdentity:
    def test_the_digest_does_not_change_when_generated_blobs_appear(self):
        from mission_persistence.fenced_commit import compute_intent_digest

        before = _request(operation_id="op-identity", lease_id="fixture-lease", argv=("set", "phase=executing"))
        after = _request(
            operation_id="op-identity",
            lease_id="fixture-lease",
            argv=("set", "phase=executing"),
            blobs=_generated_blob_set(b"first attempt\n"),
        )
        again = _request(
            operation_id="op-identity",
            lease_id="fixture-lease",
            argv=("set", "phase=executing"),
            blobs=_generated_blob_set(b"second attempt, other bytes\n"),
        )
        assert before.intent_digest == after.intent_digest == again.intent_digest
        assert before.intent_digest.startswith("sha256:")
        # The scheme is the second generation, not the first.
        from mission_persistence.fenced_commit import compute_legacy_intent_digest

        legacy = compute_legacy_intent_digest(
            session_id=before.session_id,
            lease_owner_session_id=before.lease_owner_session_id,
            operation_id=before.operation_id,
            command=before.command,
            blobs=before.blobs,
        )
        assert legacy != before.intent_digest
        assert compute_intent_digest(
            session_id=before.session_id,
            lease_owner_session_id=before.lease_owner_session_id,
            operation_id=before.operation_id,
            command=before.command,
            blobs=before.blobs,
        ) == before.intent_digest

    def test_the_moment_the_caller_asked_is_not_part_of_the_identity(self):
        """D3: the timestamp is store-authoritative, so a retry with a new clock is one operation."""
        from mission_application.evidence_publication import project_semantic_command, semantic_intent_digest

        def typed(at):
            return {
                "schema": "mission-kernel-command/1",
                "type": "append-artifact-block",
                "value": {"at": at, "section": "plan", "content": "first", "source": None, "label": None},
            }

        assert "at" not in project_semantic_command(typed("2030-01-01T00:00:00Z"))["value"]
        inputs = {
            "session_id": "s", "lease_owner_session_id": "s", "operation_id": "op", "bindings": (),
        }
        first = semantic_intent_digest(dict(inputs, command=typed("2030-01-01T00:00:00Z")))
        second = semantic_intent_digest(dict(inputs, command=typed("2030-01-01T00:00:09Z")))
        assert first == second
        # Everything else about the command still decides the identity.
        other = typed("2030-01-01T00:00:00Z")
        other["value"]["section"] = "risks"
        assert semantic_intent_digest(dict(inputs, command=other)) != first
        # A command carrying an effect claim keeps the claim projection.
        exported = {
            "schema": "mission-kernel-command/1",
            "type": "export-artifact",
            "value": {
                "at": "2030-01-01T00:00:00Z",
                "destination": "out/report.md",
                "redaction_status": "checked",
                "artifact_effect": {"kind": "artifact", "target": "artifact.md",
                                    "digest": "sha256:" + "0" * 64, "size": 3},
                "export_effect": {"kind": "artifact", "target": "report.md",
                                  "digest": "sha256:" + "1" * 64, "size": 3},
            },
        }
        projected = project_semantic_command(exported)
        assert "at" not in projected["value"]
        assert "digest" not in projected["value"]["artifact_effect"]

    def test_a_captured_blob_is_part_of_the_identity(self):
        from mission_persistence.local_uow import BlobBinding, VerifiedBlob, VerifiedBlobSet

        def captured(content: bytes):
            binding = BlobBinding(
                "review-evidence", "review-input", "archive/review.json",
                "sha256:" + hashlib.sha256(content).hexdigest(), len(content),
            )
            return VerifiedBlobSet((VerifiedBlob(binding, content),))

        one = _request(operation_id="op-captured", lease_id="fixture-lease", argv=("set", "phase=executing"), blobs=captured(b"a"))
        two = _request(operation_id="op-captured", lease_id="fixture-lease", argv=("set", "phase=executing"), blobs=captured(b"b"))
        assert one.intent_digest != two.intent_digest

    def test_an_unknown_origin_is_refused_as_an_invalid_request(self):
        import types

        from mission_persistence.fenced_commit import FencedCommitError
        from mission_persistence.local_uow import VerifiedBlob, VerifiedBlobSet

        binding = types.SimpleNamespace(
            blob_id="b", kind="k", relative_path="p/q", digest="sha256:" + "0" * 64, size=1, origin="derived"
        )
        with pytest.raises(FencedCommitError) as excinfo:
            _request(
                operation_id="op-origin", lease_id="fixture-lease", argv=("set", "phase=executing"),
                blobs=VerifiedBlobSet((VerifiedBlob(binding, b"x"),)),
            )
        assert excinfo.value.code == "request-invalid"

    def test_every_digest_reads_bindings_through_one_projection(self):
        """Contract 3 (v11): semantic digest, materialization and both D4 sites share ``binding_records``."""
        import inspect

        import mission_persistence.fenced_commit as persistence
        import mission_persistence.legacy_v4 as executor

        assert "binding_records(blobs)" in inspect.getsource(persistence.compute_intent_digest)
        assert "binding_records(" in inspect.getsource(persistence.LocalFencedRepository._materialization)
        assert "binding_records(" in inspect.getsource(persistence.LocalFencedRepository._generated_digest)
        assert "binding_records(" in inspect.getsource(
            executor.V5CompatibilityRepository._assert_replay_materializes
        )


# --------------------------------------------------------------------------
# Contracts 2, 11, 13, 14: version-1 records are read, never rewritten
# --------------------------------------------------------------------------


class TestVersionOneDualRead:
    def test_a_retry_of_a_version_one_operation_replays_with_the_records_digest(self, tmp_path):
        from mission_persistence.fenced_commit import CommitResult, OperationReplay

        local, repository, meta, _clock = _v1_repository(tmp_path, "committed")
        _path, record = _operation_record(repository, "test", "operation-init")
        assert record["schema"] == "mission-operation/1"

        replay = local.begin(_init_request(meta))

        assert isinstance(replay, OperationReplay)
        assert replay.record_version == 1
        assert replay.materialization is None
        assert replay.intent_digest == record["intent_digest"]
        assert replay.result == CommitResult(**meta["init"]["result"])
        # Nothing was rewritten to the new generation.
        assert json.loads(_path.read_text(encoding="utf-8")) == record

    def test_a_version_one_operation_with_blobs_replays_once_the_blobs_are_final(self, tmp_path):
        from mission_persistence.fenced_commit import OperationReplay

        local, repository, meta, _clock = _v1_repository(tmp_path, "committed")
        request = _request(
            operation_id=meta["blob_operation"]["operation_id"],
            lease_id=meta["lease_id"],
            argv=tuple(meta["blob_operation"]["argv"]),
            blobs=_v1_blob_set(repository.parent, meta),
        )
        replay = local.begin(request)
        assert isinstance(replay, OperationReplay) and replay.record_version == 1

    def test_a_different_intent_still_collides_with_a_version_one_record(self, tmp_path):
        from mission_persistence.fenced_commit import FencedCommitError

        local, _repository, meta, _clock = _v1_repository(tmp_path, "committed")
        other = _request(
            operation_id=meta["init"]["operation_id"], lease_id=meta["lease_id"], argv=("set", "phase=done")
        )
        with pytest.raises(FencedCommitError) as excinfo:
            local.begin(other)
        assert excinfo.value.code == "operation-intent-collision"

    def test_the_historical_read_compares_the_commit_in_the_records_own_generation(self, tmp_path):
        from mission_persistence.fenced_commit import FencedCommitError

        local, _repository, meta, _clock = _v1_repository(tmp_path, "committed")
        replay = local.begin(_init_request(meta))

        state = local.read_operation_state(
            replay.result, session_id="test", operation_id="operation-init",
            intent_digest=replay.intent_digest, record_version=replay.record_version,
            materialization=replay.materialization,
        )
        assert replay.materialization is None
        assert state.identity.session_id in (None, "test")
        # Contract 17: the generation is compared before the digest.
        with pytest.raises(FencedCommitError) as excinfo:
            local.read_operation_state(
                replay.result, session_id="test", operation_id="operation-init",
                intent_digest=replay.intent_digest, record_version=2,
                materialization={
                    "base_head_digest": None,
                    "blobs_digest": None,
                    "state_digest": "sha256:" + "0" * 64,
                },
            )
        assert excinfo.value.code == "lineage-mismatch"
        assert "generation" in str(excinfo.value)
        # The generation also decides whether a materialization may be given
        # at all: omitting one for a version-2 record would skip the lineage
        # comparison, and supplying one for a version-1 record is not its shape.
        for version, value in ((2, None), (1, {"base_head_digest": None, "blobs_digest": None, "state_digest": "sha256:" + "0" * 64})):
            with pytest.raises(FencedCommitError) as excinfo:
                local.read_operation_state(
                    replay.result, session_id="test", operation_id="operation-init",
                    intent_digest=replay.intent_digest, record_version=version, materialization=value,
                )
            assert excinfo.value.code == "record-invalid"

    def test_the_executor_replays_a_version_one_operation_end_to_end(self, tmp_path):
        from mission_persistence.legacy_v4 import V5CompatibilityRepository

        local, _repository, meta, _clock = _v1_repository(tmp_path, "committed")
        request = _init_request(meta)
        executor = V5CompatibilityRepository(
            repository=local, session_id="test", lease_owner_session_id="test",
            presented_lease_id=meta["lease_id"],
            operation_id=request.operation_id, operation_command=request.command,
            operation_command_type=meta["init"]["command_type"],
        )
        with executor.transaction():
            current = executor.read_snapshot()
            executor.load()
            assert executor.operation_replayed
            historical = executor._replayed_state_document().thaw()
        # The head moved on (the blob operation set phase=reviewing); the
        # replay answers with the state the init committed.
        assert current["phase"] == "reviewing"
        assert historical["phase"] != "reviewing"
        assert historical["lease_id"] == meta["lease_id"]

    def test_a_pending_version_one_prepare_recovers_into_version_one_records(self, tmp_path):
        """Contract 13: derived records inherit the prepare's generation."""
        from mission_persistence.fenced_commit import OperationReplay

        local, repository, meta, _clock = _v1_repository(tmp_path, "rollforward_pending")
        prepared = list((repository / "transactions" / "prepared").glob("*.json"))
        assert len(prepared) == 1
        assert json.loads(prepared[0].read_text())["schema"] == "mission-prepare/1"

        local.recover("test")

        _path, record = _operation_record(repository, "test", "operation-v1-pending")
        assert record["schema"] == "mission-operation/1" and "materialization" not in record
        markers = [json.loads(p.read_text()) for p in (repository / "transactions" / "resolved").glob("*.json")]
        assert {m["schema"] for m in markers} == {"mission-recovery/1"}
        index = repository / "transactions" / "resolved-operations" / "finalized" / _path.name
        assert json.loads(index.read_text())["schema"] == "mission-recovery-operation/1"
        assert not list((repository / "transactions" / "prepared").glob("*.json"))
        request = _request(operation_id="operation-v1-pending", lease_id=meta["lease_id"], argv=("set", "phase=reviewing"))
        replay = local.begin(request)
        assert isinstance(replay, OperationReplay) and replay.record_version == 1

    def test_generations_coexist_in_one_repository(self, tmp_path):
        """Contract 14: a v1 retry, a v2 retry and a collision are each judged correctly."""
        from mission_persistence.fenced_commit import FencedCommitError, OperationReplay

        local, repository, meta, clock = _v1_repository(tmp_path, "committed")
        base_bytes = local.read("test").state_bytes
        target = _mutated_state(tmp_path, base_bytes, clock, phase="done", lease_id=meta["lease_id"])
        new_request = _request(operation_id="operation-v2-new", lease_id=meta["lease_id"], argv=("set", "phase=done"))
        result = _commit_operation(local, new_request, target)
        _path, record = _operation_record(repository, "test", "operation-v2-new")
        assert record["schema"] == "mission-operation/2"
        assert record["materialization"]["blobs_digest"] is None

        v2 = local.begin(new_request)
        v1 = local.begin(_init_request(meta))
        assert isinstance(v2, OperationReplay) and v2.record_version == 2 and v2.result == result
        assert isinstance(v1, OperationReplay) and v1.record_version == 1
        with pytest.raises(FencedCommitError) as excinfo:
            local.begin(_request(operation_id="operation-v2-new", lease_id=meta["lease_id"], argv=("set", "phase=executing")))
        assert excinfo.value.code == "operation-intent-collision"


# --------------------------------------------------------------------------
# Contract 7 (D7): a read-only lookup before prepare
# --------------------------------------------------------------------------


class TestLookupBeforePrepare:
    def test_a_version_two_operation_is_found_before_any_blob_exists(self, tmp_path):
        from mission_persistence.fenced_commit import OperationReplay

        local, _repository, clock, _state_path, base_bytes, _result = _commit_cli_init(tmp_path)
        target = _mutated_state(tmp_path, base_bytes, clock, phase="executing")
        request = _request(
            operation_id="operation-generated", lease_id="fixture-lease", argv=("set", "phase=executing"),
            blobs=_generated_blob_set(b"manifest bytes\n"),
        )
        (tmp_path / "repository" / "build").mkdir()
        result = _commit_operation(local, request, target)

        before_prepare = _request(operation_id="operation-generated", lease_id="fixture-lease", argv=("set", "phase=executing"))
        found = local.lookup_operation(before_prepare)
        assert isinstance(found, OperationReplay) and found.result == result
        assert found.materialization["blobs_digest"] is not None

    def test_a_version_one_record_is_undetermined_until_the_blobs_are_final(self, tmp_path):
        from mission_persistence.fenced_commit import LEGACY_UNDETERMINED, OperationReplay

        local, repository, meta, _clock = _v1_repository(tmp_path, "committed")
        blob_operation = meta["blob_operation"]
        without_blobs = _request(
            operation_id=blob_operation["operation_id"], lease_id=meta["lease_id"], argv=tuple(blob_operation["argv"])
        )
        assert local.lookup_operation(without_blobs) is LEGACY_UNDETERMINED
        with_blobs = _request(
            operation_id=blob_operation["operation_id"], lease_id=meta["lease_id"], argv=tuple(blob_operation["argv"]),
            blobs=_v1_blob_set(repository.parent, meta),
        )
        assert isinstance(local.lookup_operation(with_blobs), OperationReplay)
        # A blob-less v1 operation is decided immediately.
        assert isinstance(local.lookup_operation(_init_request(meta)), OperationReplay)
        assert local.lookup_operation(
            _request(operation_id="never-seen", lease_id=meta["lease_id"], argv=("set", "phase=done"))
        ) is None

    def test_the_lookup_does_not_lay_out_a_repository_that_does_not_exist(self, tmp_path):
        from mission_persistence.fenced_commit import LocalFencedRepository

        root = tmp_path / "never-created" / ".mission-state"
        local = LocalFencedRepository(root, clock=_Clock(datetime.now(timezone.utc)), fault_injector=None)
        request = _request(operation_id="operation-none", lease_id="fixture-lease", argv=("set", "phase=done"))
        assert local.lookup_operation(request) is None
        assert not root.exists() and not root.parent.exists()

        # A partially laid out root is not completed either: the lock file is
        # what taking the lock would create.
        partial = tmp_path / "partial" / ".mission-state"
        (partial / "operations").mkdir(parents=True)
        partial_local = LocalFencedRepository(partial, clock=_Clock(datetime.now(timezone.utc)), fault_injector=None)
        assert partial_local.lookup_operation(request) is None
        assert sorted(p.name for p in partial.iterdir()) == ["operations"]

    def test_a_root_that_cannot_be_inspected_is_not_reported_as_empty(self, tmp_path):
        """Answering "no operation" on an I/O failure would hide that it ran."""
        from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

        parent = tmp_path / "sealed"
        root = parent / ".mission-state"
        root.mkdir(parents=True)
        (root / "operations").mkdir()
        (root / ".state.lock").touch()
        local = LocalFencedRepository(root, clock=_Clock(datetime.now(timezone.utc)), fault_injector=None)
        request = _request(operation_id="operation-none", lease_id="fixture-lease", argv=("set", "phase=done"))
        os.chmod(parent, 0o000)
        try:
            if os.geteuid() == 0:
                pytest.skip("root bypasses directory permissions")
            with pytest.raises(FencedCommitError) as excinfo:
                local.lookup_operation(request)
        finally:
            os.chmod(parent, 0o700)
        assert excinfo.value.code == "repository-invalid"

    def test_the_lookup_writes_nothing(self, tmp_path):
        local, repository, meta, _clock = _v1_repository(tmp_path, "committed")
        local.begin(_init_request(meta))  # a normal begin has already laid the directories out
        files_before, dirs_before = _all_files(repository), _all_dirs(repository)
        metadata_before = _all_metadata(repository)
        local.lookup_operation(_init_request(meta))
        local.lookup_operation(_request(operation_id="never-seen", lease_id=meta["lease_id"], argv=("set", "phase=done")))
        assert _all_files(repository) == files_before
        assert _all_dirs(repository) == dirs_before
        # Content is not the whole of writing nothing: repairing a mode
        # or touching an inode is a write the byte comparison cannot see.
        assert _all_metadata(repository) == metadata_before

    def test_the_lookup_refuses_a_lock_it_would_have_to_repair(self, tmp_path):
        from mission_persistence.fenced_commit import FencedCommitError

        local, repository, meta, _clock = _v1_repository(tmp_path, "committed")
        local.begin(_init_request(meta))
        lock = repository / ".state.lock"
        os.chmod(lock, 0o644)
        with pytest.raises(FencedCommitError) as excinfo:
            local.lookup_operation(_init_request(meta))
        assert excinfo.value.code == "repository-invalid"
        assert stat.S_IMODE(lock.stat().st_mode) == 0o644


# --------------------------------------------------------------------------
# Contract 3 / 10: version-two records and their recovery
# --------------------------------------------------------------------------


class TestVersionTwoRecords:
    def test_genesis_and_blob_less_operations_record_null_where_nothing_exists(self, tmp_path):
        local, repository, _clock, _state_path, _base_bytes, result = _commit_cli_init(tmp_path)
        _path, record = _operation_record(repository, "test", "operation-init")
        assert record["schema"] == "mission-operation/2"
        assert record["materialization"] == {
            "base_head_digest": None,
            "blobs_digest": None,
            "state_digest": json.loads(
                (repository / "commits" / (result.commit_digest.removeprefix("sha256:") + ".json")).read_text()
            )["state"]["digest"],
        }
        head = json.loads((repository / "sessions" / "test.json").read_text())
        assert head["schema"] == "mission-head/1"

    def test_roll_forward_recovery_rebuilds_a_version_two_record_from_the_prepare(self, tmp_path):
        from mission_persistence.fenced_commit import OperationReplay

        local, repository, clock, _state_path, base_bytes, init = _commit_cli_init(tmp_path)
        target = _mutated_state(tmp_path, base_bytes, clock, phase="executing")
        (tmp_path / "repository" / "build").mkdir()
        request = _request(
            operation_id="operation-crash", lease_id="fixture-lease", argv=("set", "phase=executing"),
            blobs=_generated_blob_set(b"generated at first run\n"),
        )
        local.fault_injector = _stop_at("after-head-replace")
        admitted = local.begin(request)
        prepared = local._stage_persistence(
            admitted, state_bytes=target, effects=tuple(b.binding for b in request.blobs.blobs)
        )
        with pytest.raises(_Stop):
            local.commit(prepared, prepared.precondition)
        local.fault_injector = None
        prepare_document = json.loads(next((repository / "transactions" / "prepared").glob("*.json")).read_text())
        assert prepare_document["schema"] == "mission-prepare/2"

        local.recover("test")

        _path, record = _operation_record(repository, "test", "operation-crash")
        assert record["schema"] == "mission-operation/2"
        assert record["materialization"] == prepare_document["materialization"]
        assert record["materialization"]["base_head_digest"] == init.head_digest
        assert record["materialization"]["blobs_digest"] is not None
        replay = local.begin(request)
        assert isinstance(replay, OperationReplay) and replay.record_version == 2

    def test_a_prepare_and_commit_of_different_generations_block_recovery(self, tmp_path):
        """Contract 16: a mixed lineage is refused rather than read by halves."""
        from mission_persistence.fenced_commit import FencedCommitError

        local, repository, clock, _state_path, base_bytes, _init = _commit_cli_init(tmp_path)
        target = _mutated_state(tmp_path, base_bytes, clock, phase="executing")
        request = _request(operation_id="operation-mixed", lease_id="fixture-lease", argv=("set", "phase=executing"))
        local.fault_injector = _stop_at("after-head-replace")
        admitted = local.begin(request)
        prepared = local._stage_persistence(admitted, state_bytes=target, effects=())
        with pytest.raises(_Stop):
            local.commit(prepared, prepared.precondition)
        local.fault_injector = None
        prepare_path = next((repository / "transactions" / "prepared").glob("*.json"))
        document = json.loads(prepare_path.read_text())
        document["schema"] = "mission-prepare/1"
        del document["materialization"]
        prepare_path.write_bytes(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())

        with pytest.raises(FencedCommitError) as excinfo:
            local.recover("test")
        assert excinfo.value.code == "recovery-ambiguous"

    def test_a_damaged_rolled_back_index_does_not_stand_between_a_record_and_its_replay(self, tmp_path):
        """The committed record is authoritative; the replay branch does not open the rolled-back index."""
        from mission_persistence.fenced_commit import OperationReplay

        local, repository, meta, _clock = _v1_repository(tmp_path, "committed")
        rolled_dir = repository / "transactions" / "resolved-operations" / "rolled-back"
        path, _record = _operation_record(repository, "test", "operation-init")
        (rolled_dir / path.name).write_bytes(b"{not json")
        replay = local.begin(_init_request(meta))
        assert isinstance(replay, OperationReplay) and replay.record_version == 1

    def test_a_finalized_index_of_another_generation_than_the_record_is_a_lineage_mismatch(self, tmp_path):
        """Contract 18: v1 finalized index + v2 operation record."""
        from mission_persistence.fenced_commit import FencedCommitError

        local, repository, meta, _clock = _v1_repository(tmp_path, "committed")
        path, record = _operation_record(repository, "test", "operation-init")
        request = _init_request(meta)
        record["schema"] = "mission-operation/2"
        record["intent_digest"] = request.intent_digest
        record["materialization"] = {"base_head_digest": None, "blobs_digest": None, "state_digest": "sha256:" + "0" * 64}
        path.write_bytes(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())

        with pytest.raises(FencedCommitError) as excinfo:
            local.begin(request)
        assert excinfo.value.code == "lineage-mismatch"


# --------------------------------------------------------------------------
# Contract 18 / 20: rolled-back indexes across generations
# --------------------------------------------------------------------------


class TestRolledBackIndex:
    def test_the_same_intent_is_admitted_again_over_a_version_one_rolled_back_index(self, tmp_path):
        from mission_persistence.fenced_commit import OperationReplay

        local, repository, meta, clock = _v1_repository(tmp_path, "committed")
        rolled = meta["rolled_back_operation"]
        base_bytes = local.read("test").state_bytes
        target = _mutated_state(tmp_path, base_bytes, clock, phase="done", lease_id=meta["lease_id"])
        same_intent = _request(operation_id=rolled["operation_id"], lease_id=meta["lease_id"], argv=tuple(rolled["argv"]))

        result = _commit_operation(local, same_intent, target)  # no collision: same intent, v1 rule

        _path, record = _operation_record(repository, "test", rolled["operation_id"])
        assert record["schema"] == "mission-operation/2"
        # An intervening operation, then a replay: the v1 rolled-back index is
        # not consulted once a record exists.
        later = _mutated_state(tmp_path, local.read("test").state_bytes, clock, phase="executing", lease_id=meta["lease_id"], name="later")
        _commit_operation(local, _request(operation_id="operation-after", lease_id=meta["lease_id"], argv=("set", "phase=executing")), later)
        replay = local.begin(same_intent)
        assert isinstance(replay, OperationReplay) and replay.result == result

    def test_another_intent_over_a_version_one_rolled_back_index_collides(self, tmp_path):
        from mission_persistence.fenced_commit import FencedCommitError

        local, _repository, meta, _clock = _v1_repository(tmp_path, "committed")
        rolled = meta["rolled_back_operation"]
        with pytest.raises(FencedCommitError) as excinfo:
            local.begin(_request(operation_id=rolled["operation_id"], lease_id=meta["lease_id"], argv=("set", "phase=executing")))
        assert excinfo.value.code == "operation-intent-collision"

    def test_a_second_rollback_inherits_the_version_one_index(self, tmp_path):
        """Contract 20: the index stays as first written; recovery completes."""
        local, repository, meta, clock = _v1_repository(tmp_path, "committed")
        rolled = meta["rolled_back_operation"]
        index_dir = repository / "transactions" / "resolved-operations" / "rolled-back"
        (index_path,) = list(index_dir.glob("*.json"))
        before = index_path.read_bytes()
        base_bytes = local.read("test").state_bytes
        target = _mutated_state(tmp_path, base_bytes, clock, phase="done", lease_id=meta["lease_id"])
        same_intent = _request(operation_id=rolled["operation_id"], lease_id=meta["lease_id"], argv=tuple(rolled["argv"]))
        local.fault_injector = _stop_at("after-prepare")
        admitted = local.begin(same_intent)
        prepared = local._stage_persistence(admitted, state_bytes=target, effects=())
        with pytest.raises(_Stop):
            local.commit(prepared, prepared.precondition)
        local.fault_injector = None

        local.recover("test")

        assert index_path.read_bytes() == before
        assert json.loads(before)["schema"] == "mission-recovery-operation/1"
        assert not list((repository / "transactions" / "prepared").glob("*.json"))
        markers = [json.loads(p.read_text()) for p in (repository / "transactions" / "resolved").glob("*.json")]
        assert "mission-recovery/2" in {m["schema"] for m in markers}
        # And the same intent is admitted once more afterwards.
        from mission_persistence.fenced_commit import OperationReplay

        assert not isinstance(local.begin(same_intent), OperationReplay)


# --------------------------------------------------------------------------
# Contracts 4, 12, 21, 22, 24: D4 and the commit lock
# --------------------------------------------------------------------------


def _executor_for(local, request, lease_id="fixture-lease"):
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    return V5CompatibilityRepository(
        repository=local, session_id=request.session_id, lease_owner_session_id=request.lease_owner_session_id,
        presented_lease_id=lease_id, operation_id=request.operation_id, operation_command=request.command,
        operation_command_type="mutating-command",
    )


class TestReplayMaterialization:
    def _committed_generated(self, tmp_path):
        local, repository, clock, _state_path, base_bytes, _init = _commit_cli_init(tmp_path)
        (tmp_path / "repository" / "build").mkdir()
        target = _mutated_state(tmp_path, base_bytes, clock, phase="executing")
        request = _request(
            operation_id="operation-generated", lease_id="fixture-lease", argv=("set", "phase=executing"),
            blobs=_generated_blob_set(b"first run\n"),
        )
        result = _commit_operation(local, request, target)
        return local, repository, clock, request, result

    def test_an_immediate_retry_with_the_same_bytes_replays(self, tmp_path):
        local, _repository, _clock, request, _result = self._committed_generated(tmp_path)
        executor = _executor_for(local, request)
        with executor.transaction():
            executor.read_snapshot()
            executor.load(blobs=_generated_blob_set(b"first run\n"))
            assert executor.operation_replayed

    def test_an_immediate_retry_with_other_bytes_is_a_materialization_mismatch(self, tmp_path):
        from mission_persistence.fenced_commit import FencedCommitError

        local, repository, _clock, request, _result = self._committed_generated(tmp_path)
        files_before = _all_files(repository)
        executor = _executor_for(local, request)
        with executor.transaction():
            executor.read_snapshot()
            with pytest.raises(FencedCommitError) as excinfo:
                executor.load(blobs=_generated_blob_set(b"second run, other bytes\n"))
        assert excinfo.value.code == "replay-materialization-mismatch"
        assert executor._replayed is None
        assert _all_files(repository) == files_before

    def test_a_retry_after_an_intervening_operation_replays_without_comparing(self, tmp_path, monkeypatch):
        import mission_persistence.legacy_v4 as module

        local, _repository, clock, request, _result = self._committed_generated(tmp_path)
        later = _mutated_state(tmp_path, local.read("test").state_bytes, clock, phase="reviewing", name="later")
        _commit_operation(local, _request(operation_id="operation-between", lease_id="fixture-lease", argv=("set", "phase=reviewing")), later)
        calls = []
        monkeypatch.setattr(module, "assert_replay_materializes", lambda **kwargs: calls.append(kwargs))
        executor = _executor_for(local, request)
        with executor.transaction():
            executor.read_snapshot()
            executor.load(blobs=_generated_blob_set(b"second run, other bytes\n"))
            assert executor.operation_replayed
        assert calls == []

    def test_both_identifiers_decide_whether_anything_intervened(self):
        """Contract 12: a head digest match alone does not make the retry immediate."""
        import types

        from mission_persistence.legacy_v4 import V5CompatibilityRepository
        from mission_persistence.local_uow import VerifiedBlobSet

        executor = object.__new__(V5CompatibilityRepository)
        executor._repository = types.SimpleNamespace(root=None)
        replay = types.SimpleNamespace(
            result=types.SimpleNamespace(head_digest="sha256:" + "a" * 64, generation=3),
            record_version=2,
            materialization={"base_head_digest": None, "blobs_digest": "sha256:" + "b" * 64, "state_digest": "sha256:" + "c" * 64},
        )
        request = types.SimpleNamespace(blobs=VerifiedBlobSet(()))
        executor._observed_base = {"base_head_digest": "sha256:" + "a" * 64, "base_generation": 2}
        executor._assert_replay_materializes(request, replay)  # generation differs: no comparison
        executor._observed_base = {"base_head_digest": "sha256:" + "0" * 64, "base_generation": 3}
        executor._assert_replay_materializes(request, replay)  # head differs: no comparison
        executor._observed_base = None
        executor._assert_replay_materializes(request, replay)  # a bare load(): no comparison
        executor._observed_base = {"base_head_digest": "sha256:" + "a" * 64, "base_generation": 3}
        from mission_persistence.fenced_commit import FencedCommitError

        with pytest.raises(FencedCommitError) as excinfo:
            executor._assert_replay_materializes(request, replay)  # both match, bytes differ (nothing vs digest)
        assert excinfo.value.code == "replay-materialization-mismatch"
        replay.record_version = 1
        executor._assert_replay_materializes(request, replay)  # a version-1 record carries nothing

    def test_the_observed_base_does_not_survive_the_transaction(self, tmp_path):
        local, _repository, _clock, request, _result = self._committed_generated(tmp_path)
        executor = _executor_for(local, request)
        with executor.transaction():
            executor.read_snapshot()
            assert executor._observed_base is not None
        assert executor._observed_base is None
        with executor.transaction():
            assert executor._observed_base is None

    def test_a_commit_time_replay_with_the_same_bytes_returns_the_recorded_result(self, tmp_path):
        """Contract 21 / 24: another execution committed the operation after this one was admitted."""
        from mission_persistence.fenced_commit import CommitResult, LocalFencedRepository

        local, repository, clock, _state_path, base_bytes, _init = _commit_cli_init(tmp_path)
        (tmp_path / "repository" / "build").mkdir()
        target = _mutated_state(tmp_path, base_bytes, clock, phase="executing")
        request = _request(
            operation_id="operation-raced", lease_id="fixture-lease", argv=("set", "phase=executing"),
            blobs=_generated_blob_set(b"same bytes\n"),
        )
        first = local.begin(request)
        other = LocalFencedRepository(repository, clock=clock, fault_injector=None)
        recorded = _commit_operation(other, request, target)
        prepared = local._stage_persistence(first, state_bytes=target, effects=tuple(b.binding for b in request.blobs.blobs))

        committed = local.commit(prepared, prepared.precondition)

        assert isinstance(committed, CommitResult) and committed == recorded
        assert len(list((repository / "generations").glob("*.json"))) == 2

    def test_a_commit_time_replay_with_other_bytes_from_the_same_base_is_a_mismatch(self, tmp_path):
        from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

        local, repository, clock, _state_path, base_bytes, _init = _commit_cli_init(tmp_path)
        (tmp_path / "repository" / "build").mkdir()
        target = _mutated_state(tmp_path, base_bytes, clock, phase="executing")
        mine = _request(
            operation_id="operation-raced", lease_id="fixture-lease", argv=("set", "phase=executing"),
            blobs=_generated_blob_set(b"my bytes\n"),
        )
        theirs = _request(
            operation_id="operation-raced", lease_id="fixture-lease", argv=("set", "phase=executing"),
            blobs=_generated_blob_set(b"their bytes\n"),
        )
        assert mine.intent_digest == theirs.intent_digest
        first = local.begin(mine)
        other = LocalFencedRepository(repository, clock=clock, fault_injector=None)
        _commit_operation(other, theirs, target)
        prepared = local._stage_persistence(first, state_bytes=target, effects=tuple(b.binding for b in mine.blobs.blobs))

        with pytest.raises(FencedCommitError) as excinfo:
            local.commit(prepared, prepared.precondition)
        assert excinfo.value.code == "replay-materialization-mismatch"

    def test_a_commit_time_replay_from_another_base_is_reported_by_the_cas(self, tmp_path):
        """The recorded result does not stand in for a run admitted on an older base."""
        from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

        local, repository, clock, _state_path, base_bytes, _init = _commit_cli_init(tmp_path)
        target = _mutated_state(tmp_path, base_bytes, clock, phase="executing")
        request = _request(operation_id="operation-raced", lease_id="fixture-lease", argv=("set", "phase=executing"))
        first = local.begin(request)  # admitted on the genesis base
        other = LocalFencedRepository(repository, clock=clock, fault_injector=None)
        between = _mutated_state(tmp_path, base_bytes, clock, phase="reviewing", name="between")
        _commit_operation(other, _request(operation_id="operation-between", lease_id="fixture-lease", argv=("set", "phase=reviewing")), between)
        later_target = _mutated_state(tmp_path, other.read("test").state_bytes, clock, phase="executing", name="later")
        _commit_operation(other, request, later_target)  # the same operation, from the moved base
        prepared = local._stage_persistence(first, state_bytes=target, effects=())

        with pytest.raises(FencedCommitError) as excinfo:
            local.commit(prepared, prepared.precondition)
        assert excinfo.value.code == "head-cas-mismatch"

    def test_a_materialization_that_disagrees_with_its_own_lineage_is_refused(self, tmp_path):
        """Well-formed is not enough: the digests must be the prepare's / commit's own."""
        from mission_persistence.fenced_commit import FencedCommitError

        local, repository, clock, _state_path, base_bytes, _init = _commit_cli_init(tmp_path)
        target = _mutated_state(tmp_path, base_bytes, clock, phase="executing")
        request = _request(operation_id="operation-tampered", lease_id="fixture-lease", argv=("set", "phase=executing"))
        local.fault_injector = _stop_at("after-head-replace")
        admitted = local.begin(request)
        prepared = local._stage_persistence(admitted, state_bytes=target, effects=())
        with pytest.raises(_Stop):
            local.commit(prepared, prepared.precondition)
        local.fault_injector = None
        prepare_path = next((repository / "transactions" / "prepared").glob("*.json"))
        document = json.loads(prepare_path.read_text())
        document["materialization"]["state_digest"] = "sha256:" + "e" * 64
        prepare_path.write_bytes(json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        with pytest.raises(FencedCommitError) as excinfo:
            local.recover("test")
        assert excinfo.value.code == "recovery-ambiguous"

        # And on the operation record side, when the historical state is read.
        # The replay itself does not dereference the commit (U2 section 6.6),
        # so the binding is made where the commit is already in hand.
        local2, repository2, _clock2, _sp, _bb, _init2 = _commit_cli_init(tmp_path / "second")
        init_request = _request(
            operation_id="operation-init", lease_id="fixture-lease", argv=("init", "Issue 500 CLI corpus"),
            command_type="init", event_types=("mission-initialized",),
        )
        replay = local2.begin(init_request)
        tampered = dict(replay.materialization, base_head_digest="sha256:" + "d" * 64)
        with pytest.raises(FencedCommitError) as excinfo:
            local2.read_operation_state(
                replay.result, session_id="test", operation_id="operation-init",
                intent_digest=replay.intent_digest, record_version=replay.record_version,
                materialization=tampered,
            )
        assert excinfo.value.code == "lineage-mismatch"
        # The honest materialization passes.
        local2.read_operation_state(
            replay.result, session_id="test", operation_id="operation-init",
            intent_digest=replay.intent_digest, record_version=replay.record_version,
            materialization=replay.materialization,
        )

    def test_the_commit_lock_rechecks_the_resolved_index(self, tmp_path):
        """Contract 22: a rolled-back other intent that appeared after admission collides at commit."""
        from mission_persistence.fenced_commit import FencedCommitError, LocalFencedRepository

        local, repository, clock, _state_path, base_bytes, _init = _commit_cli_init(tmp_path)
        target = _mutated_state(tmp_path, base_bytes, clock, phase="executing")
        other_target = _mutated_state(tmp_path, base_bytes, clock, phase="reviewing", name="other")
        mine = _request(operation_id="operation-contested", lease_id="fixture-lease", argv=("set", "phase=executing"))
        theirs = _request(operation_id="operation-contested", lease_id="fixture-lease", argv=("set", "phase=reviewing"))
        admitted = local.begin(mine)

        other = LocalFencedRepository(repository, clock=clock, fault_injector=_stop_at("after-prepare"))
        their_admission = other.begin(theirs)
        their_prepared = other._stage_persistence(their_admission, state_bytes=other_target, effects=())
        with pytest.raises(_Stop):
            other.commit(their_prepared, their_prepared.precondition)
        other.fault_injector = None
        other.recover("test")
        assert list((repository / "transactions" / "resolved-operations" / "rolled-back").glob("*.json"))

        prepared = local._stage_persistence(admitted, state_bytes=target, effects=())
        with pytest.raises(FencedCommitError) as excinfo:
            local.commit(prepared, prepared.precondition)
        assert excinfo.value.code == "operation-intent-collision"
        assert not list((repository / "transactions" / "prepared").glob("*.json"))


# --------------------------------------------------------------------------
# Contract 5 / 6 (D6): one identity per transaction
# --------------------------------------------------------------------------


class TestTransactionIdentity:
    def test_the_minted_identity_holds_for_the_transaction_and_no_longer(self, tmp_path):
        local, _repository, _clock, _state_path, _base_bytes, _init = _commit_cli_init(tmp_path)
        from mission_persistence.legacy_v4 import V5CompatibilityRepository

        executor = V5CompatibilityRepository(
            repository=local, session_id="test", lease_owner_session_id="test", presented_lease_id="fixture-lease"
        )
        with executor.transaction():
            first = executor._request().operation_id
            second = executor._request().operation_id
        with executor.transaction():
            third = executor._request().operation_id
        assert first == second != third
        assert first.startswith("compat:")

    def test_a_configured_identity_wins(self, tmp_path):
        local, _repository, _clock, _state_path, _base_bytes, _init = _commit_cli_init(tmp_path)
        request = _request(operation_id="operation-configured", lease_id="fixture-lease", argv=("set", "phase=executing"))
        executor = _executor_for(local, request)
        with executor.transaction():
            assert executor._request().operation_id == "operation-configured"


# --------------------------------------------------------------------------
# Contract 8 / 23: CLI classification and the record byte limit
# --------------------------------------------------------------------------


def _cli_module(name):
    import importlib.util

    path = PROJECT_ROOT / "skills" / "mission" / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_new_code_is_an_internal_error_and_the_old_codes_keep_their_class():
    module = _cli_module("issue747_p2_outcomes")
    assert module._fenced_cli_outcome_kind("replay-materialization-mismatch") == "internal-error"
    assert module._fenced_cli_outcome_kind("lineage-mismatch") == "internal-error"
    assert module._fenced_cli_outcome_kind("operation-intent-collision") == "invalid-input"


class TestContractDocuments:
    """Contract 9: the U2 / U3 contracts describe generation 2 and call generation 1 read-only."""

    U2 = PROJECT_ROOT / "docs" / "design" / "503-u2-contract.md"
    U3 = PROJECT_ROOT / "docs" / "design" / "504-u3-contract.md"

    def test_the_u2_contract_names_the_second_generation(self):
        text = self.U2.read_text(encoding="utf-8")
        for needle in (
            "`mission-intent/2`", "### 6.3 `mission-commit/2`", "### 6.5 `mission-prepare/2`",
            "### 6.6 `mission-operation/2`", "`OperationReplay`", "`materialization`",
            "| `MAX_OPERATION_BYTES` | 4,194,304 B |",
            "### 7.0 Read-only operation lookup", "`lookup_operation(request)`",
            "`LEGACY_UNDETERMINED`",
        ):
            assert needle in text, needle
        for stale in ("815 B maximum-shaped candidate", "4 KiB operation-record limit",
                      "return its exact `CommitResult`", "immutable captured bytes |"):
            assert stale not in text, stale

    def test_every_generation_one_mention_is_marked_read_only(self):
        import re

        for path in (self.U2, self.U3):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"mission-(intent|prepare|commit|operation|recovery|recovery-operation)/1\b", line):
                    lowered = line.lower()
                    assert any(marker in lowered for marker in ("read", "generation 1", "generation-1", "/{1,2}", "v1", "legacy")), (
                        path.name, number, line
                    )


class TestRecordByteLimit:
    def _sizes(self, generation: int, *, null_digests: bool):
        from mission_persistence.fenced_commit import (
            MAX_HEAD_BYTES,
            CommitResult,
            HeadRecord,
            RecordRef,
            _canonical_bytes,
            _head_document,
            _operation_document,
        )

        token = "t" * 128
        session = "s" * 128
        digest = "sha256:" + "f" * 64
        ref = RecordRef(digest, "commits/" + "f" * 64 + ".json", 4096)
        head = HeadRecord(commit=ref, generation=generation, session_id=session, state_generation=ref)
        head_bytes = _canonical_bytes(_head_document(head), limit=10**9)
        document = _operation_document(
            session_id=session,
            operation_id=token,
            intent_digest=digest,
            result=CommitResult(digest, generation, digest, digest),
            version=2,
            materialization={
                "base_head_digest": None if null_digests else digest,
                "blobs_digest": None if null_digests else digest,
                "state_digest": digest,
            },
        )
        operation_bytes = _canonical_bytes(document, limit=10**9)
        return len(head_bytes), len(operation_bytes), MAX_HEAD_BYTES

    def test_the_operation_record_fits_whenever_the_head_fits(self):
        from mission_persistence.fenced_commit import MAX_OPERATION_BYTES

        from mission_kernel.json_codec import STATE_LIMIT

        assert MAX_OPERATION_BYTES == STATE_LIMIT
        overhead = 2 * 128 + 4 * 72 + 512  # two tokens, four digest slots, keys and punctuation
        for digits in (1, 64, 2000):
            for null_digests in (False, True):
                head, operation, limit = self._sizes(int("9" * digits), null_digests=null_digests)
                assert operation <= head + overhead
                assert head + overhead < MAX_OPERATION_BYTES
        # The bound is the head: at the largest generation whose head still fits ...
        largest = int("9" * 1)
        for digits in range(1, 4200):
            head, _operation, limit = self._sizes(int("9" * digits), null_digests=False)
            if head > limit:
                break
            largest = int("9" * digits)
        head, operation, limit = self._sizes(largest, null_digests=False)
        assert head <= limit and operation < MAX_OPERATION_BYTES

    def test_a_head_exactly_at_max_head_bytes_is_accepted(self):
        """`len == MAX_HEAD_BYTES` is accepted (U2 section 3.3); one digit more is not."""
        from mission_persistence.fenced_commit import (
            MAX_HEAD_BYTES,
            FencedCommitError,
            HeadRecord,
            RecordRef,
            _canonical_bytes,
            _head_document,
        )

        digest = "sha256:" + "f" * 64
        ref = RecordRef(digest, "commits/" + "f" * 64 + ".json", 4096)

        def encoded(generation):
            head = HeadRecord(commit=ref, generation=generation, session_id="test", state_generation=ref)
            return _head_document(head)

        # One digit of the generation is one byte; pad the generation until the
        # head is exactly at the limit.
        short = len(_canonical_bytes(encoded(1), limit=10**6))
        digits = MAX_HEAD_BYTES - short + 1
        at_limit = _canonical_bytes(encoded(int("9" * digits)), limit=MAX_HEAD_BYTES)
        assert len(at_limit) == MAX_HEAD_BYTES
        with pytest.raises(FencedCommitError) as excinfo:
            _canonical_bytes(encoded(int("9" * (digits + 1))), limit=MAX_HEAD_BYTES)
        assert excinfo.value.code == "record-too-large"

    def test_a_head_beyond_its_limit_stops_before_the_operation_record(self):
        from mission_persistence.fenced_commit import (
            MAX_HEAD_BYTES,
            FencedCommitError,
            HeadRecord,
            RecordRef,
            _canonical_bytes,
            _head_document,
        )

        digest = "sha256:" + "f" * 64
        ref = RecordRef(digest, "commits/" + "f" * 64 + ".json", 4096)
        head = HeadRecord(commit=ref, generation=int("9" * 4200), session_id="test", state_generation=ref)
        with pytest.raises(FencedCommitError) as excinfo:
            _canonical_bytes(_head_document(head), limit=MAX_HEAD_BYTES)
        assert excinfo.value.code == "record-too-large"
