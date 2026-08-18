from __future__ import annotations

from dataclasses import dataclass, replace
import contextlib
import hashlib
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timedelta
from typing import get_type_hints

import pytest


LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
TEST_DIR = Path(__file__).resolve().parent
MISSION_STATE_PY = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
sys.path.insert(0, str(LIB_DIR))
sys.path.insert(0, str(TEST_DIR))

from mission_kernel import decode_mission_state  # noqa: E402
from mission_application.ports import (  # noqa: E402
    LegacyMissionRepository,
    MissionRepository,
    RecoverableUnitOfWork,
    VerifiedBlobView,
    VerifiedBlobSetView,
)
from mission_kernel.commands import (  # noqa: E402
    AdvancePhase,
    MarkHalt,
    encode_kernel_command,
    kernel_command_type,
)
from mission_kernel.json_codec import decode_json_object  # noqa: E402
from mission_kernel.model import HaltCategory, Phase  # noqa: E402
from mission_kernel.transitions import bind_transition_effects, decide  # noqa: E402
from mission_kernel.transitions import Transition  # noqa: E402
from mission_persistence.fenced_commit import (  # noqa: E402
    AdmittedSnapshot,
    AuditMetadata,
    CommitResult,
    FencedCommitError,
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


def _load_mission_state_module(name: str):
    spec = importlib.util.spec_from_file_location(name, MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _request(
    operation_id: str,
    lease_id: str | None,
    blobs: VerifiedBlobSet | None = None,
    typed_command=None,
    event_types: tuple[str, ...] = (),
) -> ExecutionRequest:
    command = (
        encode_kernel_command(typed_command)
        if typed_command is not None
        else decode_json_object(
            json.dumps(
                {"schema": "mission-command-intent/1", "type": "bootstrap"},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        )
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
        audit=AuditMetadata(
            kernel_command_type(typed_command) if typed_command is not None else "bootstrap",
            event_types,
        ),
        typed_command=typed_command,
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


def _kill_after_public_stage(repository_root: Path) -> subprocess.CompletedProcess:
    """Crash a real interpreter after public stage and before commit."""
    script = r'''
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mission_application.ports import AuditMetadata, ExecutionRequest
from mission_kernel.commands import MarkHalt, encode_kernel_command, kernel_command_type
from mission_kernel.model import HaltCategory
from mission_kernel.transitions import decide
from mission_persistence.fenced_commit import LocalFencedRepository, compute_intent_digest
from mission_persistence.local_uow import VerifiedBlobSet


repository_root = Path(sys.argv[1])
reader = LocalFencedRepository(repository_root)
snapshot = reader.read("test")
lease = snapshot.state.lease
clock = datetime.strptime(lease.lease_expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(
    tzinfo=timezone.utc
) - timedelta(seconds=900)
command = MarkHalt(HaltCategory.OTHER, "crash boundary stop")
encoded = encode_kernel_command(command)
blobs = VerifiedBlobSet(())
operation_id = "public-stage-process-crash"
request = ExecutionRequest(
    session_id="test",
    lease_owner_session_id="test",
    command=encoded,
    blobs=blobs,
    operation_id=operation_id,
    intent_digest=compute_intent_digest(
        session_id="test",
        lease_owner_session_id="test",
        operation_id=operation_id,
        command=encoded,
        blobs=blobs,
    ),
    presented_lease_id=lease.lease_id,
    audit=AuditMetadata(kernel_command_type(command), ("mission-halted",)),
    typed_command=command,
)


def kill(point):
    if point == "after-stage":
        os._exit(91)


repository = LocalFencedRepository(
    repository_root,
    clock=lambda: clock,
    fault_injector=kill,
)
admitted = repository.begin(request)
state = replace(
    admitted.base.state,
    lease=admitted.pending_lease.target,
    snapshot_provenance=None,
)
transition = decide(state, command).transition
repository.stage(admitted, transition, blobs)
raise AssertionError("public stage returned instead of crossing the crash boundary")
'''
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.fspath(LIB_DIR)
    return subprocess.run(
        [sys.executable, "-c", script, os.fspath(repository_root)],
        cwd=repository_root.parent,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_public_stage_accepts_only_the_sealed_transition_and_request_blobs(tmp_path):
    repository, _root, lease_id = _seed_repository(tmp_path)
    command = MarkHalt(HaltCategory.OTHER, "bounded stop")
    request = _request(
        "halt-operation", lease_id, typed_command=command, event_types=("mission-halted",)
    )
    admitted = repository.begin(request)
    assert isinstance(admitted, AdmittedSnapshot)
    assert admitted.base is not None
    admitted_state = replace(
        admitted.base.state,
        lease=admitted.pending_lease.target,
        snapshot_provenance=None,
    )
    decision = decide(admitted_state, command)
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
    object.__setattr__(forged, "_seal", decision.transition._seal)
    object.__setattr__(forged, "_input_state", decision.transition._input_state)
    object.__setattr__(forged, "_command", decision.transition._command)
    with pytest.raises(Exception, match="transition is not kernel-issued"):
        repository.stage(admitted, forged, request.blobs)


def test_process_crash_after_public_stage_preserves_head_and_recovers_pending_stage(
    tmp_path,
):
    repository, root, _lease_id = _seed_repository(tmp_path)
    before = repository.read("test")

    killed = _kill_after_public_stage(root)

    assert killed.returncode == 91, killed.stderr
    reconstructed = LocalFencedRepository(root)
    after_crash = reconstructed.read("test")
    pending = list((root / "transactions").glob(".stage-*"))
    assert after_crash.state == before.state
    assert after_crash.head_bytes == before.head_bytes
    assert after_crash.state.control.halt_reason != "crash boundary stop"
    assert len(pending) == 1

    recovered = reconstructed.recover("test")

    assert recovered.generation == before.result.generation
    assert reconstructed.read("test").state == before.state
    assert not list((root / "transactions").glob(".stage-*"))


def test_public_stage_rejects_sealed_transition_from_modified_admitted_state(tmp_path):
    repository, _root, lease_id = _seed_repository(tmp_path)
    command = MarkHalt(HaltCategory.OTHER, "bound stop")
    request = _request(
        "bound-operation", lease_id, typed_command=command, event_types=("mission-halted",)
    )
    admitted = repository.begin(request)
    assert isinstance(admitted, AdmittedSnapshot) and admitted.base is not None
    admitted_state = replace(
        admitted.base.state,
        lease=admitted.pending_lease.target,
        snapshot_provenance=None,
    )
    modified = replace(
        admitted_state,
        control=replace(admitted_state.control, threshold=admitted_state.control.threshold + 0.1),
    )
    transition = decide(modified, command).transition

    with pytest.raises(Exception, match="transition differs from its admitted state or command"):
        repository.stage(admitted, transition, request.blobs)


def test_public_stage_rejects_kernel_transition_modified_after_issue(tmp_path):
    repository, _root, lease_id = _seed_repository(tmp_path)
    command = MarkHalt(HaltCategory.OTHER, "authorized stop")
    request = _request(
        "modified-output-operation",
        lease_id,
        typed_command=command,
        event_types=("mission-halted",),
    )
    admitted = repository.begin(request)
    assert isinstance(admitted, AdmittedSnapshot) and admitted.base is not None
    admitted_state = replace(
        admitted.base.state,
        lease=admitted.pending_lease.target,
        snapshot_provenance=None,
    )
    transition = decide(admitted_state, command).transition
    assert transition is not None
    forged_state = replace(
        transition.new_state,
        control=replace(transition.new_state.control, halt_reason="forged after issue"),
    )
    object.__setattr__(transition, "new_state", forged_state)

    with pytest.raises(Exception, match="transition differs from canonical decision output"):
        repository.stage(admitted, transition, request.blobs)


def test_public_stage_rejects_audit_events_that_differ_from_transition(tmp_path):
    repository, _root, lease_id = _seed_repository(tmp_path)
    command = MarkHalt(HaltCategory.OTHER, "audit stop")
    request = _request("audit-operation", lease_id, typed_command=command)
    admitted = repository.begin(request)
    assert isinstance(admitted, AdmittedSnapshot) and admitted.base is not None
    state = replace(
        admitted.base.state,
        lease=admitted.pending_lease.target,
        snapshot_provenance=None,
    )
    transition = decide(state, command).transition

    with pytest.raises(Exception, match="audit event categories differ"):
        repository.stage(admitted, transition, request.blobs)


def test_execute_decides_after_pending_lease_and_rejection_publishes_nothing(tmp_path):
    repository, root, lease_id = _seed_repository(tmp_path)
    before = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }
    observed = []

    command = AdvancePhase(Phase.DONE)
    request = _request("rejected-operation", lease_id, typed_command=command)
    admitted = repository.begin(request)
    observed.append(admitted.pending_lease.target)
    result = repository.execute(request)
    after = {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }

    assert result.accepted is False
    assert result.rejection_code == "terminal-target-forbidden"
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
    command = MarkHalt(HaltCategory.OTHER, "effect stop")
    request = _request(
        "effect-operation",
        lease_id,
        blobs,
        typed_command=command,
        event_types=("mission-halted",),
    )
    admitted = repository.begin(request)
    assert isinstance(admitted, AdmittedSnapshot) and admitted.base is not None
    state = replace(
        admitted.base.state,
        lease=admitted.pending_lease.target,
        snapshot_provenance=None,
    )
    decision = decide(state, command)
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

    command = MarkHalt(HaltCategory.OTHER, "legacy effect stop")
    request = _request(
        "legacy-effect-operation",
        lease_id,
        blobs,
        typed_command=command,
        event_types=("mission-halted",),
    )
    result = repository.execute(request)

    assert result.accepted is True
    assert saved[-1]["halt_reason"] == "legacy effect stop"
    assert published[0].content == content


def test_common_repository_protocol_separates_legacy_transaction_capability(tmp_path):
    local, _root, _lease_id = _seed_repository(tmp_path / "local")
    state_path, _state_bytes = generate_cli_state_bytes(tmp_path / "legacy")
    legacy = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: json.loads(state_path.read_bytes()),
        write_state=lambda _value: None,
        backup_state=lambda: None,
    )

    assert isinstance(local, MissionRepository)
    assert isinstance(local, RecoverableUnitOfWork)
    assert not isinstance(local, LegacyMissionRepository)
    assert isinstance(legacy, MissionRepository)
    assert isinstance(legacy, LegacyMissionRepository)
    assert not isinstance(legacy, RecoverableUnitOfWork)
    assert legacy.read("test").state.identity.session_id == "test"


def test_execution_request_blob_type_is_a_persistence_independent_protocol():
    annotations = get_type_hints(ExecutionRequest)
    digest_annotations = get_type_hints(compute_intent_digest)
    blob_annotations = get_type_hints(VerifiedBlobSetView)

    assert annotations["blobs"] is VerifiedBlobSetView
    assert digest_annotations["blobs"] is VerifiedBlobSetView
    assert set(blob_annotations) == {"blobs"}
    assert getattr(blob_annotations["blobs"], "__origin__", None) is tuple
    assert getattr(blob_annotations["blobs"], "__args__", ()) == (VerifiedBlobView, Ellipsis)


@dataclass(frozen=True)
class _ProtocolOnlyBlobSetView:
    blobs: tuple[VerifiedBlob, ...]


def test_compute_intent_digest_rejects_protocol_only_blob_view():
    content = b'{"schema":"mission-state/1","session_id":"test"}'
    binding = BlobBinding(
        blob_id="protocol-only-blob",
        kind="cli-output",
        relative_path="evidence/mission-state.json",
        digest="sha256:" + hashlib.sha256(content).hexdigest(),
        size=len(content),
    )
    structural_view = _ProtocolOnlyBlobSetView((VerifiedBlob(binding, content),))

    with pytest.raises(FencedCommitError, match="blobs are not immutable and verified"):
        compute_intent_digest(
            session_id="test",
            lease_owner_session_id="test",
            operation_id="protocol-only-operation",
            command=decode_json_object(
                b'{"schema":"mission-command-intent/1","type":"bootstrap"}'
            ),
            blobs=structural_view,  # type: ignore[arg-type]
        )


def test_legacy_v4_read_propagates_structural_bugs(monkeypatch, tmp_path):
    from mission_persistence import legacy_v4 as legacy_v4_module

    state_path, state_bytes = generate_cli_state_bytes(tmp_path / "legacy-structural-bug")
    document = json.loads(state_bytes)
    admitted_at = datetime.fromisoformat(
        document["lease_expires_at"].replace("Z", "+00:00")
    ) - timedelta(seconds=900)
    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: json.loads(state_path.read_bytes()),
        write_state=lambda _value: None,
        backup_state=lambda: None,
        clock=lambda: admitted_at,
    )

    monkeypatch.setattr(
        legacy_v4_module,
        "decode_mission_state",
        lambda _source: (_ for _ in ()).throw(RecursionError("structural bug")),
    )

    with pytest.raises(RecursionError, match="structural bug"):
        repository.read("test")

    command = MarkHalt(HaltCategory.OTHER, "structural bug stop")
    request = _request(
        "structural-bug-operation",
        document["lease_id"],
        typed_command=command,
        event_types=("mission-halted",),
    )
    with pytest.raises(RecursionError, match="structural bug"):
        repository.execute(request)


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
        # Attribute ``replace`` here means filesystem publication (for example
        # ``Path.replace``).  A direct ``dataclasses.replace(...)`` Name call is
        # intentionally outside this inventory because it does not write bytes.
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert calls.isdisjoint({"write_bytes", "write_text", "replace", "unlink"})


def test_persistence_modules_do_not_import_private_symbols_from_peers():
    persistence_root = LIB_DIR / "mission_persistence"
    violations = []
    for path in sorted(persistence_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            imports_persistence_peer = node.level > 0 or module.startswith(
                "mission_persistence"
            )
            if not imports_persistence_peer:
                continue
            for alias in node.names:
                if alias.name.startswith("_"):
                    violations.append(
                        f"{path.name}:{node.lineno} imports {alias.name} from {module}"
                    )

    assert violations == []


@pytest.mark.parametrize("repository_kind", ["legacy-v4", "v5"])
def test_common_typed_request_decision_result_contract(repository_kind, tmp_path):
    """Both formats share acceptance, while only v5 can return a commit record."""
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

    command = MarkHalt(HaltCategory.OTHER, "common bounded stop")
    request = _request(
        "common-operation",
        lease_id,
        typed_command=command,
        event_types=("mission-halted",),
    )
    result = repository.execute(request)

    assert result.accepted is True
    assert result.rejection_code is None
    if repository_kind == "v5":
        assert result.commit is not None
        assert repository.read("test").state.control.halt_reason == "common bounded stop"
    else:
        assert result.commit is None
        assert saved and saved[-1]["halt_reason"] == "common bounded stop"


def test_typed_execution_rejects_command_document_and_audit_drift(tmp_path):
    repository, _root, lease_id = _seed_repository(tmp_path)
    command = MarkHalt(HaltCategory.OTHER, "bound stop")
    request = _request(
        "drift-operation", lease_id, typed_command=command, event_types=("mission-halted",)
    )
    forged_document = replace(
        request,
        command=decode_json_object(b'{"schema":"mission-kernel-command/1","type":"mark-halt","value":{}}'),
    )
    with pytest.raises(Exception, match="typed and immutable commands differ"):
        repository.execute(forged_document)
    wrong_audit = replace(request, audit=AuditMetadata("advance-phase", ("mission-halted",)))
    with pytest.raises(Exception, match="audit command category differs"):
        repository.execute(wrong_audit)


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


def test_legacy_set_rejects_v5_head_with_fail_closed_reason(run_cli, tmp_path):
    _repository, repository_root, _lease_id = _seed_repository(tmp_path)
    state_path = repository_root / "sessions" / "test.json"
    before = state_path.read_bytes()

    result = run_cli(
        "set",
        "compatibility_probe=1",
        cwd=repository_root.parent,
    )

    assert result.returncode != 0
    assert "repository-format-v5-requires-uow" in result.stderr
    assert "internal-error" not in result.stdout
    assert state_path.read_bytes() == before


def test_cli_repository_rejection_cannot_return_when_translator_is_injected(
    monkeypatch,
    tmp_path,
):
    module = _load_mission_state_module("issue511_rejection_divergence")
    rejection = module.RepositorySelectionError("repository-format-invalid")
    monkeypatch.setattr(
        module,
        "select_legacy_repository",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(rejection),
    )
    monkeypatch.setattr(
        module,
        "_reject_legacy_repository_selection",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(AssertionError, match="rejection translator returned"):
        module._select_legacy_repository_for_cli(
            "test", tmp_path / "state.json", lambda _guard: object()
        )


def test_cli_repository_rejection_reports_expected_read_failure(
    monkeypatch,
    capsys,
    tmp_path,
):
    module = _load_mission_state_module("issue511_expected_read_failure")
    rejection = module.RepositorySelectionError("repository-session-invalid")
    monkeypatch.setattr(
        module,
        "_read_stable_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read failed")),
    )

    with pytest.raises(SystemExit) as exited:
        module._reject_legacy_repository_selection(tmp_path / "state.json", rejection)

    assert exited.value.code == 2
    assert "repository-session-invalid" in capsys.readouterr().err


def test_cli_repository_rejection_does_not_swallow_unexpected_failure(
    monkeypatch,
    tmp_path,
):
    module = _load_mission_state_module("issue511_unexpected_read_failure")
    rejection = module.RepositorySelectionError("repository-session-invalid")
    monkeypatch.setattr(
        module,
        "_read_stable_bytes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("unexpected reader defect")
        ),
    )

    with pytest.raises(RuntimeError, match="unexpected reader defect"):
        module._reject_legacy_repository_selection(tmp_path / "state.json", rejection)


def test_cli_repository_rejection_preserves_schema_version_diagnostic(
    monkeypatch,
    tmp_path,
):
    module = _load_mission_state_module("issue511_schema_version_diagnostic")
    rejection = module.RepositorySelectionError("repository-format-invalid")
    monkeypatch.setattr(
        module,
        "_read_stable_bytes",
        lambda *_args, **_kwargs: b'{"schema_version":5}',
    )

    with pytest.raises(module.UnsupportedSchemaVersionError, match="schema_version 5"):
        module._reject_legacy_repository_selection(tmp_path / "state.json", rejection)


def test_retained_cli_format_guard_cannot_return_when_translator_is_injected(
    monkeypatch,
    tmp_path,
):
    module = _load_mission_state_module("issue511_guard_divergence")
    rejection = module.RepositorySelectionError("repository-format-drift")

    def select_repository(_session_id, _state_file, legacy_factory):
        return legacy_factory(lambda: (_ for _ in ()).throw(rejection))

    def construct_repository(format_guard):
        format_guard()
        return object()

    monkeypatch.setattr(module, "select_legacy_repository", select_repository)
    monkeypatch.setattr(
        module,
        "_reject_legacy_repository_selection",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(AssertionError, match="rejection translator returned"):
        module._select_legacy_repository_for_cli(
            "test", tmp_path / "state.json", construct_repository
        )


def test_retained_legacy_selector_rechecks_format_at_repository_load(tmp_path):
    from mission_persistence.repository_binding import require_legacy_session

    legacy_path, _ = generate_cli_state_bytes(tmp_path / "retained-legacy")
    selector = require_legacy_session("test", legacy_path)
    _repository, v5_root, _lease = _seed_repository(tmp_path / "retained-v5")
    legacy_path.write_bytes((v5_root / "sessions" / "test.json").read_bytes())
    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: pytest.fail("v5 head reached legacy reader"),
        write_state=lambda _value: None,
        backup_state=lambda: None,
        format_guard=lambda: selector.select(),
    )

    with pytest.raises(Exception, match="repository-format-drift"):
        repository.load()


def test_retained_legacy_selector_rechecks_format_at_repository_save(tmp_path):
    from mission_persistence.repository_binding import (
        RepositorySelectionError,
        require_legacy_session,
    )

    legacy_path, _ = generate_cli_state_bytes(tmp_path / "retained-save-legacy")
    original = json.loads(legacy_path.read_bytes())
    selector = require_legacy_session("test", legacy_path)
    _repository, v5_root, _lease = _seed_repository(tmp_path / "retained-save-v5")
    v5_head = (v5_root / "sessions" / "test.json").read_bytes()
    legacy_path.write_bytes(v5_head)
    writes = []
    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: pytest.fail("save drift unexpectedly read legacy state"),
        write_state=lambda value: writes.append(value),
        backup_state=lambda: pytest.fail("save drift reached backup"),
        format_guard=lambda: selector.select(),
    )

    with pytest.raises(RepositorySelectionError, match="repository-format-drift"):
        repository.save(original)

    assert writes == []
    assert legacy_path.read_bytes() == v5_head


def test_legacy_selector_rejects_cross_session_identity_before_factory(tmp_path):
    from mission_persistence.repository_binding import (
        FormatPinnedRepositorySelector,
        RepositorySelectionError,
    )

    session = tmp_path / "foreign.json"
    session.write_text(
        json.dumps(
            {
                "mission": "foreign",
                "session_id": "foreign-session",
                "phase": "planning",
                "loop_active": True,
            }
        ),
        encoding="utf-8",
    )
    calls = []
    with pytest.raises(RepositorySelectionError, match="repository-session-mismatch"):
        FormatPinnedRepositorySelector(
            session_id="expected-session",
            session_path=session,
            legacy_factory=lambda: calls.append("legacy"),
            v5_factory=lambda: calls.append("v5"),
        ).select()
    assert calls == []


def test_retained_selector_rejects_same_format_cross_session_replacement(tmp_path):
    from mission_persistence.repository_binding import (
        FormatPinnedRepositorySelector,
        RepositorySelectionError,
    )

    session, _ = generate_cli_state_bytes(tmp_path / "same-format")
    calls = []
    selector = FormatPinnedRepositorySelector(
        session_id="test",
        session_path=session,
        legacy_factory=lambda: calls.append("legacy") or "legacy",
        v5_factory=lambda: calls.append("v5") or "v5",
    )
    selector.select()
    foreign = json.loads(session.read_bytes())
    foreign["session_id"] = "foreign-session"
    session.write_text(json.dumps(foreign), encoding="utf-8")

    with pytest.raises(RepositorySelectionError, match="repository-session-mismatch"):
        selector.select()
    assert calls == ["legacy"]


def test_retained_selector_rejects_removal_of_known_session_identity(tmp_path):
    from mission_persistence.repository_binding import (
        FormatPinnedRepositorySelector,
        RepositorySelectionError,
    )

    session, _ = generate_cli_state_bytes(tmp_path / "identity-downgrade")
    calls = []
    selector = FormatPinnedRepositorySelector(
        session_id="test",
        session_path=session,
        legacy_factory=lambda: calls.append("legacy") or "legacy",
        v5_factory=lambda: calls.append("v5") or "v5",
    )
    selector.select()
    downgraded = json.loads(session.read_bytes())
    downgraded.pop("session_id")
    session.write_text(json.dumps(downgraded), encoding="utf-8")

    with pytest.raises(RepositorySelectionError, match="repository-session-drift"):
        selector.select()
    assert calls == ["legacy"]


def test_missing_version_and_session_identity_remains_bounded_legacy_compatibility(tmp_path):
    from mission_persistence.repository_binding import (
        FormatPinnedRepositorySelector,
        RepositoryFormat,
    )

    session = tmp_path / "legacy.json"
    session.write_text(
        json.dumps({"mission": "old", "phase": "planning", "loop_active": True}),
        encoding="utf-8",
    )
    selection = FormatPinnedRepositorySelector(
        session_id="expected-session",
        session_path=session,
        legacy_factory=lambda: "legacy",
        v5_factory=lambda: pytest.fail("legacy document selected v5"),
    ).select()
    assert selection.format is RepositoryFormat.LEGACY_V4


@pytest.mark.parametrize(
    "document",
    [
        {"schema": "mission-head/2", "session_id": "test"},
        {"schema": "mission-state/5", "session_id": "test"},
        {"schema": "future-format/1", "session_id": "test"},
        {},
        {"schema_version": 5, "session_id": "test"},
        {"schema_version": 4, "session_id": "test"},
    ],
)
def test_selector_rejects_every_unknown_or_future_format(tmp_path, document):
    from mission_persistence.repository_binding import (
        FormatPinnedRepositorySelector,
        RepositorySelectionError,
    )

    session = tmp_path / "state.json"
    session.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(RepositorySelectionError, match="repository-format-invalid"):
        FormatPinnedRepositorySelector(
            session_id="test",
            session_path=session,
            legacy_factory=lambda: pytest.fail("unknown format reached legacy"),
            v5_factory=lambda: pytest.fail("unknown format reached v5"),
        ).select()
