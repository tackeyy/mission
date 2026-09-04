"""Issue #634: remaining evidence commands are owned by the typed kernel."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from contextlib import contextmanager
import ast
import hashlib
import json

import pytest


def test_evidence_commands_are_frozen_and_have_unique_canonical_types():
    from mission_kernel.commands import (
        ClearProgress,
        ContextManifestEffectClaim,
        GenerateContextManifest,
        ProgressEffectClaim,
        RecordVerification,
        UpdateProgress,
        VerificationCheck,
        encode_kernel_command,
    )

    progress_claim = ProgressEffectClaim(
        "progress",
        ".mission-state/archive/progress.md",
        "sha256:" + "a" * 64,
        12,
    )
    context_claim = ContextManifestEffectClaim(
        "context-manifest",
        "manifest.json",
        "evidence/context/manifest.json",
        "sha256:" + "b" * 64,
        13,
    )
    commands = (
        (
            UpdateProgress(
                "2030-01-02T03:04:05Z",
                7,
                3,
                2,
                "unit-3",
                "reports/progress.md",
                1,
                progress_claim,
            ),
            "update-progress",
        ),
        (ClearProgress("2030-01-02T03:04:05Z"), "clear-progress"),
        (
            GenerateContextManifest(
                "2030-01-02T03:04:05Z", 1, context_claim
            ),
            "generate-context-manifest",
        ),
        (
            RecordVerification(
                "2030-01-02T03:04:05Z",
                -1,
                (VerificationCheck("tests", True, "12 passed"),),
            ),
            "record-verification",
        ),
    )

    assert [encode_kernel_command(command).thaw()["type"] for command, _ in commands] == [
        expected for _, expected in commands
    ]
    encoded_verification = encode_kernel_command(commands[-1][0]).thaw()
    assert encoded_verification["value"]["checks"] == [
        {"name": "tests", "ok": True, "detail": "12 passed"}
    ]
    with pytest.raises(FrozenInstanceError):
        commands[0][0].at = "changed"


def _legacy_document(state):
    from mission_kernel import project_legacy_document

    return json.loads(project_legacy_document(state))


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def test_four_evidence_reducers_own_only_their_observation_fields():
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import (
        ClearProgress,
        ContextManifestEffectClaim,
        GenerateContextManifest,
        ProgressEffectClaim,
        RecordVerification,
        UpdateProgress,
        VerificationCheck,
    )
    from mission_kernel.transitions import decide

    original = {
        "phase": "executing",
        "passes": False,
        "loop_active": True,
        "halt_reason": "",
        "halt_category": None,
        "terminal_outcome": None,
        "score_history": [
            {
                "iteration": 1,
                "findings_summary": [{"id": "F-1", "severity": "Medium"}],
            }
        ],
        "session_id": "portable-run",
        "mission_id": "abcdef0123456789",
        "mission": "Preserve evidence semantics",
        "assumptions_path": ".mission-state/assumptions.md",
        "context_manifests": {"0": {"path": "old.json"}},
        "verification_history": {"legacy": "invalid"},
    }
    state = decode_mission_state(json.dumps(original).encode("utf-8"))
    before = _legacy_document(state)
    completion_fields = (
        "phase",
        "passes",
        "loop_active",
        "halt_reason",
        "halt_category",
        "terminal_outcome",
        "score_history",
    )
    at = "2030-01-02T03:04:05Z"
    target = ".mission-state/archive/progress.md"
    progress_bytes = (
        "<!-- mission-progress-meta: session_id=portable-run "
        "mission_id=abcdef0123456789 iteration=1 "
        "updated_at=2030-01-02T03:04:05Z -->\n\n"
        "# Mission Progress Checkpoint\n\n"
        "- kind: batch\n- total: 7\n- completed: 3\n- remaining: 4\n"
        "- batch_size: 2\n- last_unit: unit-3\n"
        "- artifact_path: reports/progress.md\n"
    ).encode("utf-8")
    progress_claim = ProgressEffectClaim(
        "progress", target, _digest(progress_bytes), len(progress_bytes)
    )

    updated = decide(
        state,
        UpdateProgress(
            at, 7, 3, 2, "unit-3", "reports/progress.md", 1, progress_claim
        ),
    )
    assert updated.accepted is True
    assert updated.rule_id == "progress-update"
    assert updated.transition is not None
    assert updated.transition.effects == (progress_claim,)
    assert _legacy_document(state) == before
    current = updated.transition.new_state
    updated_document = _legacy_document(current)
    assert updated_document["progress"]["remaining"] == 4
    assert updated_document["progress"]["evidence_path"] == target
    assert updated_document["updated_at"] == at

    cleared = decide(current, ClearProgress("2030-01-02T03:05:05Z"))
    assert cleared.accepted is True
    assert cleared.rule_id == "progress-clear"
    assert cleared.transition is not None
    assert cleared.transition.effects == ()
    current = cleared.transition.new_state
    cleared_document = _legacy_document(current)
    assert "progress" not in cleared_document
    assert cleared_document["updated_at"] == "2030-01-02T03:05:05Z"

    publication_path = "evidence/context/manifest.json"
    manifest = {
        "schema": "mission-context-manifest/1",
        "iteration": 2,
        "mission_goal": "Preserve evidence semantics",
        "mission_id": "abcdef0123456789",
        "assumptions_path": ".mission-state/assumptions.md",
        "prior_findings": [{"id": "F-1", "severity": "Medium"}],
        # #690: the entry carries no producer marker, so the manifest reports
        # "partial" rather than letting the projection read as "nothing found".
        "prior_findings_status": "partial",
    }
    manifest_bytes = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    context_claim = ContextManifestEffectClaim(
        "context-manifest",
        "manifest.json",
        publication_path,
        _digest(manifest_bytes),
        len(manifest_bytes),
    )
    generated = decide(
        current,
        GenerateContextManifest("2030-01-02T03:06:05Z", 2, context_claim),
    )
    assert generated.accepted is True
    assert generated.rule_id == "context-manifest-generate"
    assert generated.transition is not None
    assert generated.transition.effects == (context_claim,)
    current = generated.transition.new_state
    generated_document = _legacy_document(current)
    assert generated_document["context_manifests"]["0"] == {"path": "old.json"}
    assert generated_document["context_manifests"]["2"] == {
        "path": publication_path,
        "digest": context_claim.digest,
        "generated_at": "2030-01-02T03:06:05Z",
    }
    assert generated_document["updated_at"] == "2030-01-02T03:05:05Z"

    recorded = decide(
        current,
        RecordVerification(
            "2030-01-02T03:07:05Z",
            -1,
            (
                VerificationCheck("tests", True, "12 passed"),
                VerificationCheck("lint", False, None),
            ),
        ),
    )
    assert recorded.accepted is True
    assert recorded.rule_id == "verification-record"
    assert recorded.transition is not None
    assert recorded.transition.effects == ()
    final = _legacy_document(recorded.transition.new_state)
    assert final["verification_history"] == [
        {
            "iteration": -1,
            "kind": "execution",
            "status": "failed",
            "checks": [
                {"name": "tests", "ok": True, "detail": "12 passed"},
                {"name": "lint", "ok": False, "detail": None},
            ],
            "failed_count": 1,
            "recorded_at": "2030-01-02T03:07:05Z",
        }
    ]
    assert final["updated_at"] == "2030-01-02T03:07:05Z"
    assert {field: final.get(field) for field in completion_fields} == {
        field: before.get(field) for field in completion_fields
    }


def test_application_prepares_four_typed_evidence_operations_without_state_output():
    from mission_application.evidence import (
        PreparedEvidenceOperation,
        normalize_verification_checks,
        prepare_context_manifest,
        prepare_progress_clear,
        prepare_progress_update,
        prepare_verification_record,
    )
    from mission_kernel.commands import (
        ClearProgress,
        GenerateContextManifest,
        RecordVerification,
        UpdateProgress,
    )

    state = {
        "session_id": "portable-run",
        "mission_id": "abcdef0123456789",
        "mission": "Preserve evidence semantics",
        "iteration": 1,
        "score_history": [],
    }
    at = "2030-01-02T03:04:05Z"
    prepared = (
        prepare_progress_update(
            state,
            now=at,
            total=7,
            completed=3,
            batch_size=2,
            last_unit="unit-3",
            artifact_path="reports/progress.md",
            iteration=1,
            evidence_path=".mission-state/archive/progress.md",
        ),
        prepare_progress_clear(state, now=at),
        prepare_context_manifest(
            state,
            now=at,
            iteration=1,
            publication_path="evidence/context/manifest.json",
        ),
        prepare_verification_record(
            state,
            now=at,
            iteration=0,
            checks=normalize_verification_checks(
                {"schema": "unknown", "checks": [{"name": "tests", "ok": True}]}
            ),
        ),
    )

    assert all(isinstance(item, PreparedEvidenceOperation) for item in prepared)
    assert [type(item.command) for item in prepared] == [
        UpdateProgress,
        ClearProgress,
        GenerateContextManifest,
        RecordVerification,
    ]
    assert [len(item.effects) for item in prepared] == [1, 0, 1, 0]
    assert prepared[0].command.effect.digest == prepared[0].effects[0].digest
    assert prepared[2].command.effect.publication_path == (
        "evidence/context/manifest.json"
    )
    assert prepared[2].command.effect.target == "manifest.json"
    assert prepared[3].result["verification"]["status"] == "passed"
    assert all(not hasattr(item, "state") for item in prepared)


def test_repository_executes_evidence_transition_through_the_shared_transaction_core(
    monkeypatch,
):
    import mission_persistence.legacy_v4 as legacy
    from mission_application.evidence import (
        PreparedEvidenceOperation,
        prepare_progress_update,
    )

    calls = []
    saved = []
    state = {
        "phase": "executing",
        "passes": False,
        "loop_active": True,
        "halt_reason": "",
        "session_id": "portable-run",
        "mission_id": "abcdef0123456789",
    }

    @contextmanager
    def lock():
        calls.append("lock-enter")
        try:
            yield
        finally:
            calls.append("lock-exit")

    @contextmanager
    def publish(prepared, effects):
        assert isinstance(prepared, PreparedEvidenceOperation)
        calls.append("publish")
        try:
            yield tuple("published:" + effect.target for effect in effects)
        finally:
            calls.append("publish-exit")

    original_decide = legacy.decide
    original_bind = legacy.bind_transition_effects
    original_validate = legacy.LegacyV4Repository.validate_effects

    def observed_decide(current, command):
        calls.append("decide")
        return original_decide(current, command)

    def observed_bind(transition, effects):
        calls.append("bind")
        return original_bind(transition, effects)

    def observed_validate(effects):
        calls.append("validate")
        return original_validate(effects)

    monkeypatch.setattr(legacy, "decide", observed_decide)
    monkeypatch.setattr(legacy, "bind_transition_effects", observed_bind)
    monkeypatch.setattr(
        legacy.LegacyV4Repository,
        "validate_effects",
        staticmethod(observed_validate),
    )
    repository = legacy.LegacyV4Repository(
        lock=lock,
        read_state=lambda: calls.append("load") or state,
        write_state=lambda document: (calls.append("save"), saved.append(document)),
        backup_state=lambda: None,
    )

    def prepare(document):
        calls.append("prepare")
        return prepare_progress_update(
            document,
            now="2030-01-02T03:04:05Z",
            total=7,
            completed=3,
            batch_size=2,
            last_unit="unit-3",
            artifact_path="reports/progress.md",
            iteration=1,
            evidence_path=".mission-state/archive/progress.md",
        )

    prepared, execution = repository.execute_evidence_transition_effects(
        prepare,
        effect_transaction=publish,
        verify_published=lambda operation, effects, published: calls.append("verify"),
    )

    assert prepared.result["progress"] == saved[0]["progress"]
    assert execution.projection == saved[0]
    assert calls == [
        "lock-enter",
        "load",
        "prepare",
        "decide",
        "validate",
        "bind",
        "publish",
        "verify",
        "save",
        "publish-exit",
        "lock-exit",
    ]


@pytest.mark.parametrize("operation", ["progress", "context"])
@pytest.mark.parametrize(
    "mutate",
    (
        lambda effect: (),
        lambda effect: (effect, effect),
        lambda effect: (replace(effect, kind="other"),),
        lambda effect: (replace(effect, target="other.json"),),
        lambda effect: (replace(effect, digest="sha256:" + "0" * 64),),
        lambda effect: (replace(effect, size=effect.size + 1),),
    ),
)
def test_evidence_effect_binding_rejects_every_descriptor_mismatch(
    operation, mutate
):
    from mission_application.evidence import (
        prepare_context_manifest,
        prepare_progress_update,
    )
    from mission_kernel import decode_mission_state
    from mission_kernel.transitions import (
        TransitionTableError,
        bind_transition_effects,
        decide,
    )

    document = {
        "phase": "executing",
        "loop_active": True,
        "session_id": "portable-run",
        "mission_id": "abcdef0123456789",
        "score_history": [],
    }
    prepared = (
        prepare_progress_update(
            document,
            now="2030-01-02T03:04:05Z",
            total=1,
            completed=1,
            batch_size=None,
            last_unit=None,
            artifact_path=None,
            iteration=1,
            evidence_path=".mission-state/archive/progress.md",
        )
        if operation == "progress"
        else prepare_context_manifest(
            document,
            now="2030-01-02T03:04:05Z",
            iteration=1,
            publication_path="evidence/context/manifest.json",
        )
    )
    state = decode_mission_state(json.dumps(document).encode("utf-8"))
    decision = decide(state, prepared.command)
    assert decision.transition is not None

    with pytest.raises(
        TransitionTableError, match="invalid-transition-effect-binding"
    ):
        bind_transition_effects(
            decision.transition, mutate(prepared.effects[0])
        )


def test_context_claim_binds_state_path_and_publication_basename_before_effects():
    from mission_application.evidence import prepare_context_manifest
    from mission_kernel import decode_mission_state
    from mission_kernel.transitions import decide

    document = {
        "phase": "executing",
        "loop_active": True,
        "session_id": "portable-run",
        "score_history": [],
    }
    prepared = prepare_context_manifest(
        document,
        now="2030-01-02T03:04:05Z",
        iteration=1,
        publication_path="evidence/context/manifest.json",
    )
    command = replace(
        prepared.command,
        effect=replace(prepared.command.effect, target="different.json"),
    )
    state = decode_mission_state(json.dumps(document).encode("utf-8"))

    decision = decide(state, command)

    assert decision.accepted is False
    assert decision.transition is None
    assert decision.effects == ()
    assert decision.rejection is not None
    assert decision.rejection.code == "context-effect-claim-invalid"


@pytest.mark.parametrize(
    ("command_factory", "expected_code"),
    (
        (
            lambda command, check: replace(command, at=""),
            "verification-timestamp-invalid",
        ),
        (
            lambda command, check: replace(command, iteration=True),
            "verification-iteration-invalid",
        ),
        (
            lambda command, check: replace(command, checks=[check]),
            "verification-checks-invalid",
        ),
        (
            lambda command, check: replace(command, checks=({"ok": True},)),
            "verification-check-invalid",
        ),
        (
            lambda command, check: replace(
                command, checks=(replace(check, name=""),)
            ),
            "verification-check-name-invalid",
        ),
        (
            lambda command, check: replace(
                command, checks=(replace(check, ok=1),)
            ),
            "verification-check-ok-invalid",
        ),
        (
            lambda command, check: replace(
                command, checks=(replace(check, detail=1),)
            ),
            "verification-check-detail-invalid",
        ),
    ),
)
def test_typed_verification_rejects_invalid_inputs_without_transition(
    command_factory, expected_code
):
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import RecordVerification, VerificationCheck
    from mission_kernel.transitions import decide

    check = VerificationCheck("tests", True, None)
    command = command_factory(
        RecordVerification("2030-01-02T03:04:05Z", 0, (check,)), check
    )
    state = decode_mission_state(
        json.dumps(
            {"phase": "executing", "loop_active": True, "session_id": "run"}
        ).encode("utf-8")
    )

    decision = decide(state, command)

    assert decision.accepted is False
    assert decision.transition is None
    assert decision.effects == ()
    assert decision.rejection is not None
    assert decision.rejection.code == expected_code


def test_malformed_legacy_score_history_is_rejected_before_context_publication_or_save():
    from mission_application.evidence import prepare_context_manifest
    from mission_persistence.fenced_commit import FencedCommitError
    from mission_persistence.legacy_v4 import LegacyV4Repository

    calls = []
    malformed = {
        "phase": "executing",
        "loop_active": True,
        "session_id": "portable-run",
        "score_history": ["malformed-entry"],
    }

    @contextmanager
    def publish(_prepared, _effects):
        calls.append("publish")
        yield ()

    repository = LegacyV4Repository(
        lock=lambda: _recording_context(calls, "lock"),
        read_state=lambda: calls.append("load") or malformed,
        write_state=lambda _document: calls.append("save"),
        backup_state=lambda: calls.append("backup"),
    )

    with pytest.raises(FencedCommitError, match="legacy state cannot be decoded"):
        repository.execute_evidence_transition_effects(
            lambda state: prepare_context_manifest(
                state,
                now="2030-01-02T03:04:05Z",
                iteration=1,
                publication_path="evidence/context/manifest.json",
            ),
            effect_transaction=publish,
        )

    assert calls == ["lock-enter", "load", "lock-exit"]
    assert malformed["score_history"] == ["malformed-entry"]


@pytest.mark.parametrize("operation", ["progress", "context"])
def test_foreign_lease_rejects_before_typed_evidence_publication(operation):
    from mission_application.evidence import (
        prepare_context_manifest,
        prepare_progress_update,
    )
    from mission_persistence.legacy_v4 import LegacyV4Repository

    calls = []

    def rejected_load():
        calls.append("load")
        raise ValueError("foreign-lease")

    @contextmanager
    def publish(_prepared, _effects):
        calls.append("publish")
        yield ()

    repository = LegacyV4Repository(
        lock=lambda: _recording_context(calls, "lock"),
        read_state=rejected_load,
        write_state=lambda _document: calls.append("save"),
        backup_state=lambda: calls.append("backup"),
    )

    def prepare(state):
        if operation == "progress":
            return prepare_progress_update(
                state,
                now="2030-01-02T03:04:05Z",
                total=1,
                completed=1,
                batch_size=None,
                last_unit=None,
                artifact_path=None,
                iteration=1,
                evidence_path=".mission-state/archive/progress.md",
            )
        return prepare_context_manifest(
            state,
            now="2030-01-02T03:04:05Z",
            iteration=1,
            publication_path="evidence/context/manifest.json",
        )

    with pytest.raises(ValueError, match="foreign-lease"):
        repository.execute_evidence_transition_effects(
            prepare, effect_transaction=publish
        )

    assert calls == ["lock-enter", "load", "lock-exit"]


@pytest.mark.parametrize("operation", ["progress", "context"])
def test_typed_evidence_publication_rolls_back_when_state_save_fails(operation):
    from mission_application.evidence import (
        prepare_context_manifest,
        prepare_progress_update,
    )
    from mission_persistence.legacy_v4 import LegacyV4Repository

    published = {"existing": b"old"}
    calls = []
    state = {
        "phase": "executing",
        "loop_active": True,
        "session_id": "portable-run",
        "mission_id": "abcdef0123456789",
        "score_history": [],
    }

    @contextmanager
    def publish(_prepared, effects):
        before = dict(published)
        try:
            for effect in effects:
                published[effect.target] = effect.content
            calls.append("published")
            yield effects
        except BaseException:
            published.clear()
            published.update(before)
            calls.append("rolled-back")
            raise

    repository = LegacyV4Repository(
        lock=lambda: _recording_context(calls, "lock"),
        read_state=lambda: state,
        write_state=lambda _document: (_ for _ in ()).throw(
            RuntimeError("save-failed")
        ),
        backup_state=lambda: calls.append("backup"),
    )

    def prepare(document):
        if operation == "progress":
            return prepare_progress_update(
                document,
                now="2030-01-02T03:04:05Z",
                total=1,
                completed=1,
                batch_size=None,
                last_unit=None,
                artifact_path=None,
                iteration=1,
                evidence_path=".mission-state/archive/progress.md",
            )
        return prepare_context_manifest(
            document,
            now="2030-01-02T03:04:05Z",
            iteration=1,
            publication_path="evidence/context/manifest.json",
        )

    with pytest.raises(RuntimeError, match="save-failed"):
        repository.execute_evidence_transition_effects(
            prepare, effect_transaction=publish
        )

    assert published == {"existing": b"old"}
    assert "published" in calls and "rolled-back" in calls
    assert "progress" not in state and "context_manifests" not in state


@pytest.mark.parametrize("failure", ["publish", "identity"])
def test_context_publication_failure_never_commits_state_or_output(failure):
    from mission_application.evidence import prepare_context_manifest
    from mission_persistence.legacy_v4 import LegacyV4Repository

    state = {
        "phase": "executing",
        "loop_active": True,
        "session_id": "portable-run",
        "score_history": [],
    }
    published = {}
    saved = []

    @contextmanager
    def publish(_prepared, effects):
        before = dict(published)
        try:
            if failure == "publish":
                raise RuntimeError("publish-failed")
            published[effects[0].target] = effects[0].content
            yield effects
        except BaseException:
            published.clear()
            published.update(before)
            raise

    repository = LegacyV4Repository(
        lock=lambda: _recording_context([], "lock"),
        read_state=lambda: state,
        write_state=lambda document: saved.append(document),
        backup_state=lambda: None,
    )
    verify = (
        (lambda _prepared, _effects, _published: (_ for _ in ()).throw(
            ValueError("identity-changed")
        ))
        if failure == "identity"
        else None
    )

    with pytest.raises(
        (RuntimeError if failure == "publish" else ValueError),
        match=("publish-failed" if failure == "publish" else "identity-changed"),
    ):
        repository.execute_evidence_transition_effects(
            lambda document: prepare_context_manifest(
                document,
                now="2030-01-02T03:04:05Z",
                iteration=1,
                publication_path="evidence/context/manifest.json",
            ),
            effect_transaction=publish,
            verify_published=verify,
        )

    assert published == {}
    assert saved == []
    assert "context_manifests" not in state


@contextmanager
def _recording_context(calls, name):
    calls.append(f"{name}-enter")
    try:
        yield None
    finally:
        calls.append(f"{name}-exit")


@pytest.mark.parametrize(
    "field", ["progress", "context_manifests", "verification_history"]
)
def test_generic_set_cannot_bypass_dedicated_evidence_authority(field):
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import SetExtensionFields
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import decide

    state = decode_mission_state(
        json.dumps(
            {"phase": "executing", "loop_active": True, "session_id": "run"}
        ).encode("utf-8")
    )
    command = SetExtensionFields(freeze_json_value({field: {}}))

    decision = decide(state, command)

    assert decision.accepted is False
    assert decision.transition is None
    assert decision.rejection is not None
    assert decision.rejection.code == "dedicated-field"


def test_four_cli_adapters_have_no_direct_state_mutation_or_legacy_decision_save():
    from pathlib import Path

    source_path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    names = (
        "cmd_progress_update",
        "cmd_progress_clear",
        "cmd_context_manifest",
        "cmd_verification_record",
    )

    for name in names:
        function = functions[name]
        assert not any(
            isinstance(node, ast.Subscript)
            and isinstance(node.ctx, (ast.Store, ast.Del))
            for node in ast.walk(function)
        ), name
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"update", "setdefault", "pop", "save"}
            for node in ast.walk(function)
        ), name
        called_names = {
            node.func.id
            for node in ast.walk(function)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "_run_evidence_decision" not in called_names


@pytest.mark.parametrize("iteration", [0, -1])
@pytest.mark.parametrize("schema", [None, "unknown-schema"])
def test_verification_cli_preserves_schema_and_iteration_acceptance(
    state_dir, run_cli, read_state, iteration, schema
):
    payload = {"checks": [{"name": "tests", "ok": True}]}
    if schema is not None:
        payload["schema"] = schema

    result = run_cli(
        "verification",
        "record",
        "--iteration",
        str(iteration),
        "--stdin",
        cwd=state_dir.parent,
        input_text=json.dumps(payload),
    )

    assert result.returncode == 0, result.stderr
    entry = read_state(state_dir)["verification_history"][-1]
    assert entry["iteration"] == iteration
    assert entry["status"] == "passed"


def test_false_negative_latest_remains_global_append_order_and_skips_not_run():
    from mission_gate_outcome import false_negative_summary

    failed_then_not_run = {
        "mission": "failed-remains-latest-classifiable",
        "passes": True,
        "score_history": [{"iteration": 1, "composite": 4.5}],
        "verification_history": [
            {"iteration": 1, "status": "failed", "failed_count": 1},
            {"iteration": 2, "status": "not-run", "failed_count": 0},
        ],
    }
    failed_then_passed = {
        "mission": "later-pass-wins-globally",
        "passes": True,
        "score_history": [
            {"iteration": 1, "composite": 4.5},
            {"iteration": 2, "composite": 4.5},
        ],
        "verification_history": [
            {"iteration": 1, "status": "failed", "failed_count": 1},
            {"iteration": 2, "status": "passed", "failed_count": 0},
        ],
    }

    summary = false_negative_summary([failed_then_not_run, failed_then_passed])

    assert summary["missions_with_verification"] == 2
    assert summary["count"] == 1
    assert summary["missions"] == ["failed-remains-latest-classifiable"]
