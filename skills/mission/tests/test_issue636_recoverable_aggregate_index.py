"""Issue #636: recoverable aggregate index publication for legacy saves."""

from __future__ import annotations

import contextlib
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


_LIB_ROOT = Path(__file__).resolve().parents[1] / "lib"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_v4_save_orders_durable_intent_around_authority_write():
    from mission_persistence.legacy_v4 import LegacyV4Repository

    calls = []
    intent = object()
    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: {},
        backup_state=lambda: calls.append("backup"),
        write_state=lambda _state: calls.append("state"),
        aggregate_recover=lambda: calls.append("recover"),
        aggregate_prepare=lambda action: calls.append("intent:" + action) or intent,
        aggregate_finalize=lambda prepared: calls.append(
            "index" if prepared is intent else "wrong-intent"
        ),
    )

    repository.save({"loop_active": False}, aggregate_action="remove")

    assert calls == ["recover", "intent:remove", "backup", "state", "index"]


def test_partial_coordinator_injection_is_rejected():
    from mission_persistence.legacy_v4 import LegacyV4Repository

    with pytest.raises(ValueError, match="aggregate coordinator"):
        LegacyV4Repository(
            lock=contextlib.nullcontext,
            read_state=lambda: {},
            write_state=lambda _state: None,
            backup_state=lambda: None,
            aggregate_recover=lambda: None,
        )


def test_v5_save_orders_durable_intent_around_authority_commit():
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    calls = []
    intent = object()

    class Backend:
        def _stage_persistence(self, _admitted, *, state_bytes, effects):
            assert state_bytes
            assert effects == ()
            calls.append("stage")
            return SimpleNamespace(precondition=object())

        def commit(self, _prepared, _precondition):
            calls.append("commit")

    repository = V5CompatibilityRepository(
        repository=Backend(),
        session_id="s1",
        lease_owner_session_id="owner",
        presented_lease_id="lease",
        aggregate_recover=lambda: calls.append("recover"),
        aggregate_prepare=lambda action: calls.append("intent:" + action) or intent,
        aggregate_finalize=lambda prepared: calls.append(
            "index" if prepared is intent else "wrong-intent"
        ),
    )
    repository._admitted = object()

    repository.save({"loop_active": True}, aggregate_action="add")

    assert calls == ["recover", "intent:add", "stage", "commit", "index"]


def test_v5_rejects_unloaded_or_unserializable_state_before_intent():
    from mission_persistence.fenced_commit import FencedCommitError
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    calls = []
    callbacks = {
        "aggregate_recover": lambda: calls.append("recover"),
        "aggregate_prepare": lambda action: calls.append("intent:" + action),
        "aggregate_finalize": lambda _intent: calls.append("index"),
    }
    unloaded = V5CompatibilityRepository(
        repository=object(),
        session_id="s1",
        lease_owner_session_id="owner",
        presented_lease_id="lease",
        **callbacks,
    )
    with pytest.raises(FencedCommitError, match="was not loaded"):
        unloaded.save({"loop_active": True}, aggregate_action="add")

    unserializable = V5CompatibilityRepository(
        repository=object(),
        session_id="s1",
        lease_owner_session_id="owner",
        presented_lease_id="lease",
        **callbacks,
    )
    unserializable._admitted = object()
    with pytest.raises(TypeError):
        unserializable.save({"extension": {1}}, aggregate_action="add")

    assert calls == []


def test_v5_lease_callback_failure_reconciles_and_releases_intent(tmp_path):
    from mission_persistence.aggregate_index import RecoverableAggregateIndex
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    root = tmp_path / ".mission-state"
    authority = _write_legacy_authority(root, "s1", active=False)
    (root / "aggregate.json").write_text(
        json.dumps({"active_sessions": []}), encoding="utf-8"
    )
    coordinator = RecoverableAggregateIndex(
        root, session_id="s1", authority_format="legacy-v4"
    )

    class Backend:
        def _stage_persistence(self, _admitted, *, state_bytes, effects):
            assert state_bytes and effects == ()
            return SimpleNamespace(precondition=object())

        def commit(self, _prepared, _precondition):
            authority.unlink()
            _write_legacy_authority(root, "s1", active=True)

    def fail_lease(_pending_lease, _state):
        raise RuntimeError("lease callback failed")

    repository = V5CompatibilityRepository(
        repository=Backend(),
        session_id="s1",
        lease_owner_session_id="owner",
        presented_lease_id="lease",
        aggregate_recover=coordinator.recover,
        aggregate_prepare=coordinator.prepare,
        aggregate_finalize=coordinator.finalize,
        lease_committed=fail_lease,
    )
    repository._admitted = SimpleNamespace(pending_lease=object())

    with pytest.raises(RuntimeError, match="lease callback failed"):
        repository.save({"loop_active": True}, aggregate_action="add")

    assert json.loads((root / "aggregate.json").read_text(encoding="utf-8"))[
        "active_sessions"
    ] == ["s1"]
    assert list(coordinator.intent_directory.glob("*.json")) == []
    assert RecoverableAggregateIndex(root).recover() == 0


def _write_legacy_authority(root, session_id, *, active):
    path = root / "sessions" / (session_id + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "mission": "fixture",
                "mission_id": "fixture-id",
                "session_id": session_id,
                "phase": "planning" if active else "halted",
                "loop_active": active,
                "passes": False,
                "halt_reason": "" if active else "stopped",
            }
        ),
        encoding="utf-8",
    )
    return path


def _crash_after_intent_publish(root, session_id, authority_format, action):
    script = r'''
import os
import sys
from pathlib import Path
from mission_persistence.aggregate_index import RecoverableAggregateIndex

def kill(point):
    if point == "after-intent-publish":
        os._exit(91)

RecoverableAggregateIndex(
    Path(sys.argv[1]),
    session_id=sys.argv[2],
    authority_format=sys.argv[3],
    fault_injector=kill,
).prepare(sys.argv[4])
'''
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.fspath(_LIB_ROOT)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            os.fspath(root),
            session_id,
            authority_format,
            action,
        ],
        cwd=os.fspath(root.parent),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 91, result.stderr


def test_finalize_reconciles_membership_and_consumes_the_intent(tmp_path):
    from mission_persistence.aggregate_index import RecoverableAggregateIndex

    root = tmp_path / ".mission-state"
    authority = _write_legacy_authority(root, "s1", active=False)
    (root / "aggregate.json").write_text(
        json.dumps({"active_sessions": []}), encoding="utf-8"
    )
    coordinator = RecoverableAggregateIndex(
        root,
        session_id="s1",
        authority_format="legacy-v4",
        clock=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )

    intent = coordinator.prepare("add")
    authority.unlink()
    _write_legacy_authority(root, "s1", active=True)
    coordinator.finalize(intent)

    aggregate = json.loads((root / "aggregate.json").read_text(encoding="utf-8"))
    assert aggregate == {
        "active_sessions": ["s1"],
        "updated_at": "2026-08-24T00:00:00Z",
    }
    assert list((root / "aggregate-index-intents").glob("*.json")) == []


def test_v4_state_failure_attempts_immediate_intent_recovery_without_masking_error():
    from mission_persistence.legacy_v4 import LegacyV4Repository

    calls = []

    def fail_write(_state):
        calls.append("state")
        raise OSError("authority unavailable")

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: {},
        backup_state=lambda: calls.append("backup"),
        write_state=fail_write,
        aggregate_recover=lambda: calls.append("recover"),
        aggregate_prepare=lambda action: calls.append("intent:" + action) or object(),
        aggregate_finalize=lambda _intent: calls.append("index"),
    )

    with pytest.raises(OSError, match="authority unavailable"):
        repository.save({}, aggregate_action="remove")

    assert calls == ["recover", "intent:remove", "backup", "state", "recover"]


def test_permission_halt_preserves_success_after_post_commit_index_failure():
    from mission_application.ports import AggregateIndexError
    from mission_application.runtime_guard import record_permission_observation
    from .test_issue620_kernel_a5_c1 import (
        _RecordingRepository,
        _active_state,
        _denied_request,
    )

    class FailingIndexRepository(_RecordingRepository):
        def save(
            self,
            state,
            *,
            backup=True,
            administrative=False,
            aggregate_action=None,
        ):
            super().save(
                state,
                backup=backup,
                administrative=administrative,
                aggregate_action=aggregate_action,
            )
            assert aggregate_action == "remove"
            raise AggregateIndexError("index remains recoverable")

    repository = FailingIndexRepository(_active_state())
    result = record_permission_observation(repository, _denied_request())

    assert result.halt_recorded is True
    assert repository.saved["loop_active"] is False


def test_v4_coordinator_preserves_every_saved_key_and_value(tmp_path):
    from mission_persistence.aggregate_index import RecoverableAggregateIndex
    from mission_persistence.legacy_v4 import LegacyV4Repository

    root = tmp_path / ".mission-state"
    authority = _write_legacy_authority(root, "s1", active=False)
    (root / "aggregate.json").write_text(
        json.dumps({"active_sessions": []}), encoding="utf-8"
    )
    target = json.loads(authority.read_text(encoding="utf-8"))
    target.update(
        {
            "loop_active": True,
            "phase": "planning",
            "halt_reason": "",
            "extension": {"nested": [1, "two", False, None]},
        }
    )
    baseline = {}
    LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: {},
        write_state=lambda state: baseline.update(copy.deepcopy(state)),
        backup_state=lambda: None,
        add_to_aggregate=lambda: None,
    ).save(copy.deepcopy(target), aggregate_action="add")
    coordinator = RecoverableAggregateIndex(
        root, session_id="s1", authority_format="legacy-v4"
    )

    def write_candidate(state):
        authority.write_text(json.dumps(state), encoding="utf-8")

    LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: {},
        write_state=write_candidate,
        backup_state=lambda: None,
        aggregate_recover=coordinator.recover,
        aggregate_prepare=coordinator.prepare,
        aggregate_finalize=coordinator.finalize,
    ).save(copy.deepcopy(target), aggregate_action="add")

    assert json.loads(authority.read_text(encoding="utf-8")) == baseline == target


def test_v5_coordinator_preserves_every_saved_key_and_value(tmp_path):
    from mission_kernel import project_legacy_document
    from mission_persistence.aggregate_index import RecoverableAggregateIndex
    from mission_persistence.fenced_commit import LocalFencedRepository
    from mission_persistence.legacy_v4 import V5CompatibilityRepository
    from .test_issue503_fenced_commit import _commit_cli_init

    baseline_local, baseline_root, baseline_clock, *_ = _commit_cli_init(
        tmp_path / "baseline"
    )
    candidate_root = tmp_path / "candidate" / ".mission-state"
    candidate_root.parent.mkdir()
    shutil.copytree(baseline_root, candidate_root)
    candidate_local = LocalFencedRepository(candidate_root, clock=baseline_clock)

    def save(repository, root, *, recover=None, prepare=None, finalize=None):
        snapshot = repository.read("test")
        compatibility = V5CompatibilityRepository(
            repository=repository,
            session_id="test",
            lease_owner_session_id="test",
            presented_lease_id=snapshot.state.lease.lease_id,
            add_to_aggregate=(lambda: None) if recover is None else None,
            aggregate_recover=recover,
            aggregate_prepare=prepare,
            aggregate_finalize=finalize,
        )
        with compatibility.transaction():
            document = compatibility.load()
            document["extension"] = {"nested": [1, "two", False, None]}
            compatibility.save(document, aggregate_action="add")
        return json.loads(project_legacy_document(repository.read("test").state))

    baseline = save(baseline_local, baseline_root)
    (candidate_root / "aggregate.json").write_text(
        json.dumps({"active_sessions": []}), encoding="utf-8"
    )
    coordinator = RecoverableAggregateIndex(
        candidate_root, session_id="test", authority_format="v5"
    )
    candidate = save(
        candidate_local,
        candidate_root,
        recover=coordinator.recover,
        prepare=coordinator.prepare,
        finalize=coordinator.finalize,
    )

    assert candidate == baseline


@pytest.mark.parametrize(
    ("action", "former_active", "target_active", "former_membership", "target_membership"),
    (
        ("add", False, True, [], ["s1"]),
        ("remove", True, False, ["s1"], []),
    ),
)
def test_recovery_uses_authority_not_recorded_action_and_is_idempotent(
    tmp_path,
    action,
    former_active,
    target_active,
    former_membership,
    target_membership,
):
    from mission_persistence.aggregate_index import RecoverableAggregateIndex

    root = tmp_path / ".mission-state"
    authority = _write_legacy_authority(root, "s1", active=former_active)
    (root / "aggregate.json").write_text(
        json.dumps({"active_sessions": former_membership}), encoding="utf-8"
    )
    _crash_after_intent_publish(root, "s1", "legacy-v4", action)
    authority.unlink()
    _write_legacy_authority(root, "s1", active=target_active)

    restarted = RecoverableAggregateIndex(
        root,
        clock=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    assert restarted.recover() == 1
    recovered_bytes = (root / "aggregate.json").read_bytes()
    assert json.loads(recovered_bytes)["active_sessions"] == target_membership
    assert restarted.recover() == 0
    assert (root / "aggregate.json").read_bytes() == recovered_bytes


def test_missing_index_recovery_rebuilds_all_authoritative_memberships(tmp_path):
    from mission_persistence.aggregate_index import RecoverableAggregateIndex

    root = tmp_path / ".mission-state"
    first = _write_legacy_authority(root, "s1", active=False)
    _write_legacy_authority(root, "s2", active=True)
    coordinator = RecoverableAggregateIndex(
        root,
        session_id="s1",
        authority_format="legacy-v4",
        clock=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    _crash_after_intent_publish(root, "s1", "legacy-v4", "add")
    first.unlink()
    _write_legacy_authority(root, "s1", active=True)

    coordinator.recover()

    aggregate = json.loads((root / "aggregate.json").read_text(encoding="utf-8"))
    assert aggregate["active_sessions"] == ["s1", "s2"]


def test_rebuild_uses_embedded_session_id_for_flat_legacy_authority(tmp_path):
    from mission_persistence.aggregate_index import RecoverableAggregateIndex

    root = tmp_path / ".mission-state"
    session = _write_legacy_authority(root, "embedded", active=True)
    flat = root / "state.json"
    session.replace(flat)

    result = RecoverableAggregateIndex(root).repair(execute=True)

    assert result["active_sessions"] == ["embedded"]


def test_corrupt_index_keeps_intent_until_explicit_repair(tmp_path):
    from mission_persistence.aggregate_index import (
        AggregateIndexProtocolError,
        RecoverableAggregateIndex,
    )

    root = tmp_path / ".mission-state"
    authority = _write_legacy_authority(root, "s1", active=False)
    (root / "aggregate.json").write_text("{corrupt", encoding="utf-8")
    coordinator = RecoverableAggregateIndex(
        root,
        session_id="s1",
        authority_format="legacy-v4",
        clock=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
    )
    coordinator.prepare("add")
    authority.unlink()
    _write_legacy_authority(root, "s1", active=True)

    with pytest.raises(AggregateIndexProtocolError) as error:
        coordinator.recover()
    assert error.value.code == "aggregate-invalid"
    assert len(list(coordinator.intent_directory.glob("*.json"))) == 1

    result = coordinator.repair(execute=True)
    assert result["valid"] is True
    assert result["matches_authority"] is True
    assert result["pending_intents"] == 0
    assert json.loads(coordinator.aggregate_path.read_text())["active_sessions"] == ["s1"]


def test_duplicate_json_keys_in_index_fail_closed_and_keep_intent(tmp_path):
    from mission_persistence.aggregate_index import (
        AggregateIndexProtocolError,
        RecoverableAggregateIndex,
    )

    root = tmp_path / ".mission-state"
    _write_legacy_authority(root, "s1", active=True)
    (root / "aggregate.json").write_text(
        '{"active_sessions":[],"active_sessions":["s1"]}', encoding="utf-8"
    )
    coordinator = RecoverableAggregateIndex(
        root, session_id="s1", authority_format="legacy-v4"
    )
    coordinator.prepare("add")

    with pytest.raises(AggregateIndexProtocolError) as error:
        coordinator.recover()
    assert error.value.code == "aggregate-invalid"
    assert len(list(coordinator.intent_directory.glob("*.json"))) == 1


@pytest.mark.parametrize(
    "invalid_kind",
    ("array", "wrong-element", "symlink", "hardlink", "oversized"),
)
def test_invalid_index_identity_shape_or_size_keeps_intent(tmp_path, invalid_kind):
    from mission_persistence.aggregate_index import (
        AGGREGATE_LIMIT,
        AggregateIndexProtocolError,
        RecoverableAggregateIndex,
    )

    root = tmp_path / ".mission-state"
    _write_legacy_authority(root, "s1", active=True)
    aggregate = root / "aggregate.json"
    if invalid_kind == "array":
        aggregate.write_text("[]", encoding="utf-8")
    elif invalid_kind == "wrong-element":
        aggregate.write_text('{"active_sessions":[1]}', encoding="utf-8")
    elif invalid_kind == "symlink":
        target = tmp_path / "outside-index.json"
        target.write_text('{"active_sessions":[]}', encoding="utf-8")
        aggregate.symlink_to(target)
    elif invalid_kind == "hardlink":
        aggregate.write_text('{"active_sessions":[]}', encoding="utf-8")
        os.link(aggregate, tmp_path / "aggregate-alias.json")
    else:
        aggregate.write_bytes(b" " * (AGGREGATE_LIMIT + 1))
    coordinator = RecoverableAggregateIndex(
        root, session_id="s1", authority_format="legacy-v4"
    )
    intent = coordinator.prepare("add")

    with pytest.raises(AggregateIndexProtocolError) as error:
        coordinator.finalize(intent)

    expected = (
        "aggregate-identity-invalid"
        if invalid_kind in {"symlink", "hardlink"}
        else "aggregate-invalid"
    )
    assert error.value.code == expected
    assert intent.path.exists()


def test_index_identity_change_after_publish_keeps_intent(tmp_path):
    from mission_persistence.aggregate_index import (
        AggregateIndexProtocolError,
        RecoverableAggregateIndex,
    )

    root = tmp_path / ".mission-state"
    authority = _write_legacy_authority(root, "s1", active=False)
    aggregate = root / "aggregate.json"
    aggregate.write_text(json.dumps({"active_sessions": []}), encoding="utf-8")

    def replace_published_index(point):
        if point == "after-index-publish":
            replacement = aggregate.with_suffix(".replacement")
            replacement.write_text("{corrupt", encoding="utf-8")
            os.replace(replacement, aggregate)

    coordinator = RecoverableAggregateIndex(
        root,
        session_id="s1",
        authority_format="legacy-v4",
        fault_injector=replace_published_index,
    )
    intent = coordinator.prepare("add")
    authority.unlink()
    _write_legacy_authority(root, "s1", active=True)

    with pytest.raises(AggregateIndexProtocolError) as error:
        coordinator.finalize(intent)
    assert error.value.code == "aggregate-changed"
    assert intent.path.exists()


def test_intent_identity_change_before_removal_keeps_replacement(tmp_path):
    from mission_persistence.aggregate_index import (
        AggregateIndexProtocolError,
        RecoverableAggregateIndex,
    )

    root = tmp_path / ".mission-state"
    authority = _write_legacy_authority(root, "s1", active=False)
    (root / "aggregate.json").write_text(
        json.dumps({"active_sessions": []}), encoding="utf-8"
    )
    holder = {}

    def replace_intent(point):
        if point == "before-intent-remove":
            intent = holder["intent"]
            replacement = intent.path.with_suffix(".replacement")
            replacement.write_bytes(intent.payload)
            os.replace(replacement, intent.path)

    coordinator = RecoverableAggregateIndex(
        root,
        session_id="s1",
        authority_format="legacy-v4",
        fault_injector=replace_intent,
    )
    intent = coordinator.prepare("add")
    holder["intent"] = intent
    authority.unlink()
    _write_legacy_authority(root, "s1", active=True)

    with pytest.raises(AggregateIndexProtocolError) as error:
        coordinator.finalize(intent)

    assert error.value.code == "intent-changed"
    assert intent.path.exists()


def test_rebuild_rejects_duplicate_authority_for_one_session(tmp_path):
    from mission_persistence.aggregate_index import (
        AggregateIndexProtocolError,
        RecoverableAggregateIndex,
    )

    root = tmp_path / ".mission-state"
    session = _write_legacy_authority(root, "same", active=True)
    (root / "state.json").write_bytes(session.read_bytes())

    with pytest.raises(AggregateIndexProtocolError) as error:
        RecoverableAggregateIndex(root).repair(execute=True)
    assert error.value.code == "authority-duplicated"


def test_recovery_rejects_path_traversal_session_id_in_intent(tmp_path):
    from mission_persistence.aggregate_index import (
        AggregateIndexProtocolError,
        RecoverableAggregateIndex,
    )

    root = tmp_path / ".mission-state"
    directory = root / "aggregate-index-intents"
    directory.mkdir(parents=True)
    session_id = "../escape"
    name = hashlib.sha256(session_id.encode("utf-8")).hexdigest() + ".json"
    (directory / name).write_text(
        json.dumps(
            {
                "schema": "mission-aggregate-index-intent/1",
                "session_id": session_id,
                "action": "remove",
                "authority_format": "legacy-v4",
                "base_authority_digest": "sha256:" + "0" * 64,
                "created_at": "2026-08-24T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AggregateIndexProtocolError) as error:
        RecoverableAggregateIndex(root).recover()
    assert error.value.code == "intent-invalid"


def test_recovery_rejects_symlinked_intent_directory(tmp_path):
    from mission_persistence.aggregate_index import (
        AggregateIndexProtocolError,
        RecoverableAggregateIndex,
    )

    root = tmp_path / ".mission-state"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "aggregate-index-intents").symlink_to(outside, target_is_directory=True)

    with pytest.raises(AggregateIndexProtocolError) as error:
        RecoverableAggregateIndex(root).recover()
    assert error.value.code == "intent-directory-invalid"


def test_concurrent_finalizers_do_not_lose_membership_updates(tmp_path):
    from mission_persistence.aggregate_index import RecoverableAggregateIndex

    root = tmp_path / ".mission-state"
    (root / "aggregate.json").parent.mkdir(parents=True)
    (root / "aggregate.json").write_text(
        json.dumps({"active_sessions": []}), encoding="utf-8"
    )
    coordinators = []
    intents = []
    for session_id in ("s1", "s2"):
        authority = _write_legacy_authority(root, session_id, active=False)
        coordinator = RecoverableAggregateIndex(
            root,
            session_id=session_id,
            authority_format="legacy-v4",
            clock=lambda: datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
        intent = coordinator.prepare("add")
        authority.unlink()
        _write_legacy_authority(root, session_id, active=True)
        coordinators.append(coordinator)
        intents.append(intent)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda pair: pair[0].finalize(pair[1]), zip(coordinators, intents)))

    aggregate = json.loads((root / "aggregate.json").read_text(encoding="utf-8"))
    assert set(aggregate["active_sessions"]) == {"s1", "s2"}


def test_recovery_does_not_consume_a_live_save_intent(tmp_path):
    from mission_persistence.aggregate_index import RecoverableAggregateIndex

    root = tmp_path / ".mission-state"
    authority = _write_legacy_authority(root, "s1", active=False)
    (root / "aggregate.json").write_text(
        json.dumps({"active_sessions": []}), encoding="utf-8"
    )
    saving = RecoverableAggregateIndex(
        root, session_id="s1", authority_format="legacy-v4"
    )
    intent = saving.prepare("add")

    competing_recovery = RecoverableAggregateIndex(root)
    assert competing_recovery.recover() == 0
    assert intent.path.exists()

    authority.unlink()
    _write_legacy_authority(root, "s1", active=True)
    saving.finalize(intent)
    assert json.loads(saving.aggregate_path.read_text())["active_sessions"] == ["s1"]
    assert competing_recovery.recover() == 0


def test_reconcile_rechecks_raw_authority_without_repository_read_under_index_lock(
    tmp_path,
):
    from mission_persistence.aggregate_index import RecoverableAggregateIndex

    coordinator = RecoverableAggregateIndex(tmp_path / ".mission-state")
    index_locked = {"value": False}
    capture = SimpleNamespace(session_id="s1")
    intent = SimpleNamespace(session_id="s1", authority_format="v5")

    def capture_authority(_session_id, _authority_format):
        assert index_locked["value"] is False
        return capture

    @contextlib.contextmanager
    def index_lock():
        index_locked["value"] = True
        try:
            yield
        finally:
            index_locked["value"] = False

    def authority_unchanged(candidate):
        assert index_locked["value"] is True
        assert candidate is capture
        return True

    coordinator._capture_authority = capture_authority
    coordinator._locked = index_lock
    coordinator._authority_unchanged = authority_unchanged
    coordinator._publish_membership = (
        lambda _capture, **_kwargs: ((), "digest")
    )
    coordinator._consume = lambda _intent, _fingerprint: None

    coordinator._reconcile(intent)


def test_repair_reads_authorities_outside_the_index_lock(tmp_path):
    from mission_persistence.aggregate_index import RecoverableAggregateIndex

    coordinator = RecoverableAggregateIndex(tmp_path / ".mission-state")
    index_locked = {"value": False}
    capture = SimpleNamespace(session_id="s1", active=True)

    def desired_captures():
        assert index_locked["value"] is False
        return [capture]

    @contextlib.contextmanager
    def index_lock():
        index_locked["value"] = True
        try:
            yield
        finally:
            index_locked["value"] = False

    def authority_unchanged(candidate):
        assert index_locked["value"] is True
        assert candidate is capture
        return True

    coordinator._desired_captures = desired_captures
    coordinator._locked = index_lock
    coordinator._authority_unchanged = authority_unchanged
    coordinator._load_aggregate = lambda: ({"active_sessions": ["s1"]}, ())
    coordinator.recover = lambda: 0

    result = coordinator.repair(execute=True)

    assert result["valid"] is True
    assert result["matches_authority"] is True


def _kill_during_aggregate_save(
    root: Path, authority_format: str, action: str, kill_point: str
) -> subprocess.CompletedProcess:
    script = r'''
import json
import os
import sys
from dataclasses import replace
from pathlib import Path

from mission_kernel import project_legacy_document
from mission_kernel.json_codec import decode_json_object
from mission_persistence.aggregate_index import RecoverableAggregateIndex
from mission_persistence.fenced_commit import (
    AuditMetadata,
    ExecutionRequest,
    LocalFencedRepository,
    compute_intent_digest,
)
from mission_persistence.local_uow import VerifiedBlobSet


root = Path(sys.argv[1])
authority_format = sys.argv[2]
action = sys.argv[3]
kill_point = sys.argv[4]
target_active = action == "add"


def kill(point):
    if point == kill_point:
        os._exit(91)


coordinator = RecoverableAggregateIndex(
    root,
    session_id="test",
    authority_format=authority_format,
    fault_injector=kill,
)
intent = coordinator.prepare(action)

if authority_format == "legacy-v4":
    path = root / "sessions" / "test.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["loop_active"] = target_active
    document["passes"] = False
    document["halt_reason"] = "" if target_active else "stopped"
    document["phase"] = "planning" if target_active else "halted"
    temporary = path.with_suffix(".next")
    temporary.write_text(json.dumps(document), encoding="utf-8")
    os.replace(temporary, path)
else:
    repository = LocalFencedRepository(root, fault_injector=kill)
    snapshot = repository.read("test")
    command = decode_json_object(
        b'{"schema":"mission-command-intent/1","type":"compatibility-mutation"}'
    )
    blobs = VerifiedBlobSet(())
    operation_id = "issue-636-kill-" + action
    request = ExecutionRequest(
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
        presented_lease_id=snapshot.state.lease.lease_id,
        audit=AuditMetadata("compatibility-mutation", ()),
    )
    admitted = repository.begin(request)
    target = replace(
        admitted.base.state,
        lease=admitted.pending_lease.target,
        snapshot_provenance=None,
    )
    document = json.loads(project_legacy_document(target))
    document["loop_active"] = target_active
    document["passes"] = False
    document["halt_reason"] = "" if target_active else "stopped"
    document["phase"] = "planning" if target_active else "halted"
    state_bytes = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    prepared = repository._stage_persistence(admitted, state_bytes=state_bytes, effects=())
    repository.commit(prepared, prepared.precondition)

if kill_point == "after-authority-publish":
    os._exit(91)
coordinator.finalize(intent)
raise AssertionError("fault point was not reached")
'''
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.fspath(_LIB_ROOT)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            os.fspath(root),
            authority_format,
            action,
            kill_point,
        ],
        cwd=os.fspath(root.parent),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("authority_format", ("legacy-v4", "v5"))
@pytest.mark.parametrize("action", ("add", "remove"))
@pytest.mark.parametrize(
    "kill_point",
    ("after-intent-publish", "after-authority-publish", "after-index-publish"),
)
def test_kill_points_are_recovered_twice_without_drift(
    tmp_path, authority_format, action, kill_point
):
    from mission_persistence.aggregate_index import RecoverableAggregateIndex

    root = tmp_path / ".mission-state"
    if authority_format == "legacy-v4":
        _write_legacy_authority(root, "test", active=action == "remove")
    else:
        from .test_issue503_fenced_commit import _commit_cli_init

        _local, root, _clock, _state_path, _state_bytes, _result = _commit_cli_init(
            tmp_path
        )
    initial_membership = ["test"] if action == "remove" else []
    (root / "aggregate.json").write_text(
        json.dumps({"active_sessions": initial_membership}), encoding="utf-8"
    )

    killed = _kill_during_aggregate_save(root, authority_format, action, kill_point)
    assert killed.returncode == 91, killed.stderr

    restarted = RecoverableAggregateIndex(root)
    assert restarted.recover() == 1
    first = (root / "aggregate.json").read_bytes()
    if kill_point == "after-intent-publish":
        former_active = authority_format == "v5" or action == "remove"
        expected = ["test"] if former_active else []
    else:
        expected = ["test"] if action == "add" else []
    assert json.loads(first)["active_sessions"] == expected
    assert restarted.recover() == 0
    assert (root / "aggregate.json").read_bytes() == first


@pytest.mark.parametrize(
    "fenced_fault_point",
    (
        "after-prepare",
        "after-generation-publish",
        "after-commit-publish",
        "before-head-replace",
        "after-head-replace",
    ),
)
def test_v5_fenced_commit_kills_reconcile_the_published_head(
    tmp_path, fenced_fault_point
):
    from mission_persistence.aggregate_index import RecoverableAggregateIndex
    from mission_persistence.authoritative_reader import read_authoritative_snapshot
    from mission_persistence.fenced_commit import LocalFencedRepository
    from .test_issue503_fenced_commit import _commit_cli_init

    _local, root, *_ = _commit_cli_init(tmp_path)
    (root / "aggregate.json").write_text(
        json.dumps({"active_sessions": ["test"]}), encoding="utf-8"
    )

    killed = _kill_during_aggregate_save(
        root, "v5", "remove", fenced_fault_point
    )
    assert killed.returncode == 91, killed.stderr

    coordinator = RecoverableAggregateIndex(root)
    assert coordinator.recover() == 1
    before_fenced_recovery = json.loads(coordinator.aggregate_path.read_text())[
        "active_sessions"
    ]
    expected = [] if fenced_fault_point == "after-head-replace" else ["test"]
    assert before_fenced_recovery == expected

    LocalFencedRepository(root).recover("test")
    authority = read_authoritative_snapshot(
        root / "sessions" / "test.json", expected_session_id="test"
    )
    assert authority.loop_active is (fenced_fault_point != "after-head-replace")
    assert json.loads(coordinator.aggregate_path.read_text())["active_sessions"] == expected
    assert coordinator.recover() == 0


def test_kill_during_recovery_after_index_publish_is_idempotent(tmp_path):
    from mission_persistence.aggregate_index import RecoverableAggregateIndex

    root = tmp_path / ".mission-state"
    _write_legacy_authority(root, "test", active=False)
    (root / "aggregate.json").write_text(
        json.dumps({"active_sessions": []}), encoding="utf-8"
    )
    killed_save = _kill_during_aggregate_save(
        root, "legacy-v4", "add", "after-authority-publish"
    )
    assert killed_save.returncode == 91, killed_save.stderr
    script = r'''
import os
import sys
from pathlib import Path
from mission_persistence.aggregate_index import RecoverableAggregateIndex

def kill(point):
    if point == "after-index-publish":
        os._exit(91)

RecoverableAggregateIndex(Path(sys.argv[1]), fault_injector=kill).recover()
'''
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.fspath(_LIB_ROOT)
    killed = subprocess.run(
        [sys.executable, "-c", script, os.fspath(root)],
        cwd=os.fspath(tmp_path),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert killed.returncode == 91, killed.stderr
    published = (root / "aggregate.json").read_bytes()
    assert json.loads(published)["active_sessions"] == ["test"]
    coordinator = RecoverableAggregateIndex(root)
    assert len(list(coordinator.intent_directory.glob("*.json"))) == 1

    assert coordinator.recover() == 1
    assert (root / "aggregate.json").read_bytes() == published
    assert coordinator.recover() == 0


def test_repair_command_is_check_only_unless_execute_is_explicit(tmp_path, run_cli):
    root = tmp_path / ".mission-state"
    _write_legacy_authority(root, "s1", active=True)
    aggregate = root / "aggregate.json"
    aggregate.write_text("{corrupt", encoding="utf-8")

    checked = run_cli("repair-aggregate-index", cwd=tmp_path)
    assert checked.returncode == 0, checked.stderr
    checked_result = json.loads(checked.stdout)
    assert checked_result["executed"] is False
    assert checked_result["valid"] is False
    assert aggregate.read_text(encoding="utf-8") == "{corrupt"

    executed = run_cli("repair-aggregate-index", "--execute", cwd=tmp_path)
    assert executed.returncode == 0, executed.stderr
    executed_result = json.loads(executed.stdout)
    assert executed_result["executed"] is True
    assert executed_result["matches_authority"] is True
    assert json.loads(aggregate.read_text())["active_sessions"] == ["s1"]


@pytest.mark.parametrize(
    "relative_path",
    (
        "bin/mission-state.py",
        "lib/mission_application/runtime_guard.py",
        "lib/mission_persistence/administrative.py",
        "lib/mission_persistence/aggregate_index.py",
        "lib/mission_persistence/legacy_v4.py",
        "refs/state-management.md",
    ),
)
def test_changed_source_and_plugin_mirrors_are_byte_identical(relative_path):
    source = _REPOSITORY_ROOT / "skills" / "mission" / relative_path
    plugin = _REPOSITORY_ROOT / "plugins" / "mission" / "skills" / "mission" / relative_path
    assert source.read_bytes() == plugin.read_bytes()
