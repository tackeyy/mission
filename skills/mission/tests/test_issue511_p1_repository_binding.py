from __future__ import annotations

from dataclasses import replace
import contextlib
import hashlib
import ast
import json
from pathlib import Path
import sys
from datetime import datetime, timedelta

import pytest


LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(TEST_DIR))

from mission_kernel import decode_mission_state  # noqa: E402
from mission_kernel.commands import MarkHalt  # noqa: E402
from mission_kernel.json_codec import decode_json_object  # noqa: E402
from mission_kernel.model import HaltCategory  # noqa: E402
from mission_kernel.transitions import bind_transition_effects, decide  # noqa: E402
from mission_kernel.transitions import Transition  # noqa: E402
from mission_persistence.fenced_commit import (  # noqa: E402
    AdmittedSnapshot,
    AuditMetadata,
    CommitResult,
    ExecutionRequest,
    LocalFencedRepository,
    compute_intent_digest,
)
from mission_persistence.local_uow import (  # noqa: E402
    BlobBinding,
    VerifiedBlob,
    VerifiedBlobSet,
)
from mission_persistence.legacy_v4 import LegacyV4Repository  # noqa: E402
from mission_state_fixture_corpus import generate_cli_state_bytes  # noqa: E402


def _request(
    operation_id: str,
    lease_id: str | None,
    blobs: VerifiedBlobSet | None = None,
) -> ExecutionRequest:
    command = decode_json_object(
        json.dumps(
            {"schema": "mission-command-intent/1", "type": "mark-halt"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    blobs = VerifiedBlobSet(()) if blobs is None else blobs
    return ExecutionRequest(
        session_id="test",
        lease_owner_session_id="test",
        command=command,
        blobs=blobs,
        operation_id=operation_id,
        intent_digest=compute_intent_digest(
            session_id="test",
            lease_owner_session_id="test",
            operation_id=operation_id,
            command=command,
            blobs=blobs,
        ),
        presented_lease_id=lease_id,
        audit=AuditMetadata("mark-halt", ("mission-halted",)),
    )


def _seed_repository(tmp_path: Path) -> tuple[LocalFencedRepository, Path, str]:
    source_path, state_bytes = generate_cli_state_bytes(tmp_path / "source")
    state = json.loads(state_bytes)
    lease_id = state["lease_id"]
    admitted_at = datetime.fromisoformat(
        state["lease_expires_at"].replace("Z", "+00:00")
    ) - timedelta(seconds=900)
    repository_root = tmp_path / "repository" / ".mission-state"
    repository = LocalFencedRepository(repository_root, clock=lambda: admitted_at)
    request = _request("seed-operation", lease_id)
    admitted = repository.begin(request)
    assert isinstance(admitted, AdmittedSnapshot)
    prepared = repository._stage_persistence(admitted, state_bytes=state_bytes, effects=())
    repository.commit(prepared, prepared.precondition)
    assert source_path.read_bytes() == state_bytes
    return repository, repository_root, lease_id


def test_public_stage_accepts_only_the_sealed_transition_and_request_blobs(tmp_path):
    repository, _root, lease_id = _seed_repository(tmp_path)
    request = _request("halt-operation", lease_id)
    admitted = repository.begin(request)
    assert isinstance(admitted, AdmittedSnapshot)
    assert admitted.base is not None
    admitted_state = replace(
        admitted.base.state,
        lease=admitted.pending_lease.target,
        snapshot_provenance=None,
    )
    decision = decide(admitted_state, MarkHalt(HaltCategory.OTHER, "bounded stop"))
    assert decision.accepted and decision.transition is not None

    prepared = repository.stage(admitted, decision.transition, request.blobs)
    result = repository.commit(prepared, prepared.precondition)

    assert isinstance(result, CommitResult)
    assert repository.read("test").state.control.halt_reason == "bounded stop"
    forged = Transition(
        decision.transition.new_state,
        decision.transition.events,
        decision.transition.effects,
    )
    with pytest.raises(Exception, match="transition is not kernel-issued"):
        repository.stage(admitted, forged, request.blobs)


def test_execute_decides_after_pending_lease_and_rejection_publishes_nothing(tmp_path):
    repository, root, lease_id = _seed_repository(tmp_path)
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    observed = []

    def reject(state):
        observed.append(state.lease)
        return decide(state, object())

    result = repository.execute(_request("rejected-operation", lease_id), reject)
    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    assert result.accepted is False
    assert result.rejection_code == "unknown-command"
    assert observed and observed[0].lease_id == lease_id
    assert after == before


def test_public_stage_binds_verified_effects_to_the_sealed_state_decision(tmp_path):
    repository, root, lease_id = _seed_repository(tmp_path)
    content = b"closed evidence\n"
    binding = BlobBinding(
        blob_id="artifact-output",
        kind="artifact",
        relative_path="evidence/output.txt",
        digest="sha256:" + hashlib.sha256(content).hexdigest(),
        size=len(content),
    )
    blobs = VerifiedBlobSet((VerifiedBlob(binding, content),))
    request = _request("effect-operation", lease_id, blobs)
    admitted = repository.begin(request)
    assert isinstance(admitted, AdmittedSnapshot) and admitted.base is not None
    state = replace(
        admitted.base.state,
        lease=admitted.pending_lease.target,
        snapshot_provenance=None,
    )
    decision = decide(state, MarkHalt(HaltCategory.OTHER, "effect stop"))
    transition = bind_transition_effects(decision.transition, (binding,))

    prepared = repository.stage(admitted, transition, blobs)
    repository.commit(prepared, prepared.precondition)

    assert (root.parent / "evidence" / "output.txt").read_bytes() == content


def test_legacy_typed_request_publishes_effect_and_state_in_one_transaction(tmp_path):
    state_path, state_bytes = generate_cli_state_bytes(tmp_path / "legacy-effect")
    document = json.loads(state_bytes)
    lease_id = document["lease_id"]
    admitted_at = datetime.fromisoformat(
        document["lease_expires_at"].replace("Z", "+00:00")
    ) - timedelta(seconds=900)
    content = b"legacy closed evidence\n"
    binding = BlobBinding(
        blob_id="legacy-output",
        kind="artifact",
        relative_path="evidence/legacy.txt",
        digest="sha256:" + hashlib.sha256(content).hexdigest(),
        size=len(content),
    )
    blobs = VerifiedBlobSet((VerifiedBlob(binding, content),))
    saved = []
    published = []

    @contextlib.contextmanager
    def publish(effects):
        published.extend(effects)
        yield tuple(effect.target for effect in effects)

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: json.loads(state_path.read_bytes()),
        write_state=lambda value: saved.append(value),
        backup_state=lambda: None,
        clock=lambda: admitted_at,
        effect_transaction=publish,
    )

    def decide_effect(state):
        decision = decide(state, MarkHalt(HaltCategory.OTHER, "legacy effect stop"))
        return replace(
            decision,
            transition=bind_transition_effects(decision.transition, (binding,)),
        )

    result = repository.execute(
        _request("legacy-effect-operation", lease_id, blobs),
        decide_effect,
    )

    assert result.accepted is True
    assert saved[-1]["halt_reason"] == "legacy effect stop"
    assert published[0].content == content


def test_a1_a5_application_modules_have_no_persistence_or_direct_writer_dependency():
    application_root = LIB_DIR / "mission_application"
    for name in ("lifecycle.py", "review.py", "artifact.py", "planning.py", "runtime_guard.py"):
        tree = ast.parse((application_root / name).read_text(encoding="utf-8"))
        imported = {
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        }
        assert not any(module.startswith("mission_persistence") for module in imported)
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert calls.isdisjoint({"write_bytes", "write_text", "replace", "unlink"})


@pytest.mark.parametrize("repository_kind", ["legacy-v4", "v5"])
def test_common_typed_request_decision_result_contract(repository_kind, tmp_path):
    if repository_kind == "v5":
        repository, _root, lease_id = _seed_repository(tmp_path)
    else:
        state_path, state_bytes = generate_cli_state_bytes(tmp_path / "legacy-common")
        document = json.loads(state_bytes)
        lease_id = document["lease_id"]
        admitted_at = datetime.fromisoformat(
            document["lease_expires_at"].replace("Z", "+00:00")
        ) - timedelta(seconds=900)
        saved = []
        repository = LegacyV4Repository(
            lock=contextlib.nullcontext,
            read_state=lambda: json.loads(state_path.read_bytes()),
            write_state=lambda value: saved.append(value),
            backup_state=lambda: None,
            clock=lambda: admitted_at,
        )

    request = _request("common-operation", lease_id)
    result = repository.execute(
        request,
        lambda state: decide(
            state,
            MarkHalt(HaltCategory.OTHER, "common bounded stop"),
        ),
    )

    assert result.accepted is True
    assert result.rejection_code is None
    if repository_kind == "v5":
        assert result.commit is not None
        assert repository.read("test").state.control.halt_reason == "common bounded stop"
    else:
        assert result.commit is None
        assert saved and saved[-1]["halt_reason"] == "common bounded stop"


def test_selector_pins_one_loaded_format_and_rejects_drift(tmp_path):
    from mission_persistence.repository_binding import (
        FormatPinnedRepositorySelector,
        RepositoryFormat,
        RepositorySelectionError,
    )

    legacy_path, _ = generate_cli_state_bytes(tmp_path / "legacy")
    legacy_repository = object()
    v5_repository = object()
    calls = []
    selector = FormatPinnedRepositorySelector(
        session_id="test",
        session_path=legacy_path,
        legacy_factory=lambda: calls.append("v4") or legacy_repository,
        v5_factory=lambda: calls.append("v5") or v5_repository,
    )

    first = selector.select()
    second = selector.select()
    assert first is second
    assert first.format is RepositoryFormat.LEGACY_V4
    assert first.repository is legacy_repository
    assert calls == ["v4"]

    _v5_repository, v5_root, _v5_lease = _seed_repository(tmp_path / "drift")
    legacy_path.write_bytes((v5_root / "sessions" / "test.json").read_bytes())
    with pytest.raises(RepositorySelectionError, match="repository-format-drift"):
        selector.select()


def test_selector_recognizes_an_existing_v5_head_without_an_environment_flag(tmp_path):
    _repository, root, _lease_id = _seed_repository(tmp_path)
    from mission_persistence.repository_binding import (
        FormatPinnedRepositorySelector,
        RepositoryFormat,
    )

    selected = FormatPinnedRepositorySelector(
        session_id="test",
        session_path=root / "sessions" / "test.json",
        legacy_factory=lambda: pytest.fail("legacy writer selected for a v5 head"),
        v5_factory=lambda: "v5-repository",
    ).select()

    assert selected.format is RepositoryFormat.V5
    assert selected.repository == "v5-repository"
