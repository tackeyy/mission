"""Issue #633: artifact commands are owned by the typed kernel."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from contextlib import contextmanager
import hashlib
import json

import pytest


def _claim():
    from mission_kernel.artifact import ArtifactEffectClaim

    return ArtifactEffectClaim(
        kind="artifact",
        target="reports/artifact.md",
        digest="sha256:" + "a" * 64,
        size=12,
    )


@pytest.mark.parametrize(
    ("command", "expected_type"),
    (
        (
            lambda: __import__(
                "mission_kernel.commands", fromlist=["InitializeArtifact"]
            ).InitializeArtifact(
                at="2030-01-02T03:04:05Z",
                path="reports/artifact.md",
                format="markdown",
                title="Portable artifact",
                redaction_status="reviewed",
                required_for_pass=True,
                effect=_claim(),
            ),
            "initialize-artifact",
        ),
        (
            lambda: __import__(
                "mission_kernel.commands", fromlist=["AppendArtifactBlock"]
            ).AppendArtifactBlock(
                at="2030-01-02T03:04:05Z",
                section="evidence",
                content="verified",
                source=None,
                label="check",
            ),
            "append-artifact-block",
        ),
        (
            lambda: __import__(
                "mission_kernel.commands", fromlist=["RenderArtifact"]
            ).RenderArtifact(
                at="2030-01-02T03:04:05Z",
                redaction_status=None,
                effect=_claim(),
            ),
            "render-artifact",
        ),
        (
            lambda: __import__(
                "mission_kernel.commands", fromlist=["ExportArtifact"]
            ).ExportArtifact(
                at="2030-01-02T03:04:05Z",
                destination="reports/export.md",
                redaction_status="reviewed",
                artifact_effect=_claim(),
                export_effect=__import__(
                    "mission_kernel.artifact", fromlist=["ArtifactEffectClaim"]
                ).ArtifactEffectClaim(
                    kind="artifact-export",
                    target="reports/export.md",
                    digest="sha256:" + "a" * 64,
                    size=12,
                ),
            ),
            "export-artifact",
        ),
        (
            lambda: __import__(
                "mission_kernel.commands", fromlist=["RecordArtifactPublication"]
            ).RecordArtifactPublication(
                at="2030-01-02T03:04:05Z",
                provider="local",
                destination=None,
                approval_text="approved",
                confirmed=True,
                effect=_claim(),
            ),
            "record-artifact-publication",
        ),
    ),
)
def test_artifact_commands_are_frozen_and_have_unique_canonical_types(
    command, expected_type
):
    from mission_kernel.commands import encode_kernel_command

    value = command()
    encoded = encode_kernel_command(value).thaw()

    assert encoded["schema"] == "mission-kernel-command/1"
    assert encoded["type"] == expected_type
    assert encoded["value"]["at"] == "2030-01-02T03:04:05Z"
    with pytest.raises(FrozenInstanceError):
        value.at = "changed"


def _document(state):
    from mission_kernel import project_legacy_document

    return json.loads(project_legacy_document(state))


def test_artifact_transition_sequence_owns_only_the_artifact_aggregate():
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import (
        AppendArtifactBlock,
        ExportArtifact,
        InitializeArtifact,
        RecordArtifactPublication,
        RenderArtifact,
    )
    from mission_kernel.transitions import decide

    original = {
        "phase": "executing",
        "passes": False,
        "loop_active": True,
        "halt_reason": "",
        "halt_category": None,
        "terminal_outcome": None,
        "score_history": [],
        "session_id": "portable-run",
        "artifact_lint": [],
        "artifact_lint_status": "clean",
        "artifact_lint_identity": {"path": "old.md"},
    }
    state = decode_mission_state(json.dumps(original).encode("utf-8"))
    projected_original = _document(state)
    completion_fields = (
        "phase",
        "passes",
        "loop_active",
        "halt_reason",
        "halt_category",
        "terminal_outcome",
        "score_history",
    )

    initialized = decide(
        state,
        InitializeArtifact(
            at="2030-01-02T03:04:05Z",
            path="reports/artifact.md",
            format="markdown",
            title="Portable artifact",
            redaction_status="reviewed",
            required_for_pass=True,
            effect=_claim(),
        ),
    )
    assert initialized.accepted is True
    assert initialized.rule_id == "artifact-initialize"
    assert initialized.transition is not None
    assert initialized.transition.effects == (_claim(),)
    assert _document(state) == projected_original
    current = initialized.transition.new_state
    artifact = _document(current)["artifact"]
    assert artifact == {
        "status": "draft",
        "format": "markdown",
        "title": "Portable artifact",
        "path": "reports/artifact.md",
        "exports": [],
        "publish_events": [],
        "redaction_status": "reviewed",
        "required_for_pass": True,
        "blocks": [],
        "created_at": "2030-01-02T03:04:05Z",
        "updated_at": "2030-01-02T03:04:05Z",
        "digest": "a" * 64,
        "size": 12,
        "producer_run_id": "portable-run",
    }

    appended = decide(
        current,
        AppendArtifactBlock(
            at="2030-01-02T03:05:05Z",
            section="evidence",
            content="verified\n",
            source=None,
            label="check",
        ),
    )
    assert appended.accepted is True
    assert appended.rule_id == "artifact-append-block"
    assert appended.transition is not None
    assert appended.transition.effects == ()
    current = appended.transition.new_state
    artifact = _document(current)["artifact"]
    assert artifact["blocks"] == [
        {
            "section": "evidence",
            "content": "verified",
            "timestamp": "2030-01-02T03:05:05Z",
            "label": "check",
        }
    ]
    assert "digest" not in artifact and "size" not in artifact

    render_claim = _claim().__class__(
        "artifact", "reports/artifact.md", "sha256:" + "b" * 64, 13
    )
    rendered = decide(
        current,
        RenderArtifact("2030-01-02T03:06:05Z", "not-needed", render_claim),
    )
    assert rendered.accepted is True
    assert rendered.rule_id == "artifact-render"
    assert rendered.transition is not None
    current = rendered.transition.new_state
    artifact = _document(current)["artifact"]
    assert artifact["status"] == "rendered"
    assert artifact["redaction_status"] == "not-needed"
    assert artifact["digest"] == "b" * 64

    artifact_claim = _claim().__class__(
        "artifact", "reports/artifact.md", "sha256:" + "c" * 64, 14
    )
    export_claim = _claim().__class__(
        "artifact-export", "reports/export.md", "sha256:" + "c" * 64, 14
    )
    exported = decide(
        current,
        ExportArtifact(
            "2030-01-02T03:07:05Z",
            "reports/export.md",
            "reviewed",
            artifact_claim,
            export_claim,
        ),
    )
    assert exported.accepted is True
    assert exported.rule_id == "artifact-export"
    assert exported.transition is not None
    current = exported.transition.new_state
    artifact = _document(current)["artifact"]
    assert artifact["exports"][-1] == {
        "path": "reports/export.md",
        "timestamp": "2030-01-02T03:07:05Z",
        "redaction_status": "reviewed",
    }

    publish_claim = _claim().__class__(
        "artifact", "reports/artifact.md", "sha256:" + "d" * 64, 15
    )
    published = decide(
        current,
        RecordArtifactPublication(
            "2030-01-02T03:08:05Z",
            "local",
            None,
            "approved",
            True,
            publish_claim,
        ),
    )
    assert published.accepted is True
    assert published.rule_id == "artifact-record-publication"
    assert published.transition is not None
    final = _document(published.transition.new_state)
    assert final["artifact"]["publish_events"][-1] == {
        "provider": "local",
        "timestamp": "2030-01-02T03:08:05Z",
        "approval_text": "approved",
        "status": "publish-prepared",
        "artifact_path": "reports/artifact.md",
    }
    assert final["artifact"]["status"] == "publish-prepared"
    assert not {
        "artifact_lint",
        "artifact_lint_status",
        "artifact_lint_identity",
    } & final.keys()
    assert {field: final.get(field) for field in completion_fields} == {
        field: projected_original.get(field) for field in completion_fields
    }


@pytest.mark.parametrize(
    "mutate",
    (
        lambda effect: (),
        lambda effect: (effect, effect),
        lambda effect: (replace(effect, kind="artifact-export"),),
        lambda effect: (replace(effect, target="reports/other.md"),),
        lambda effect: (replace(effect, digest="sha256:" + "0" * 64),),
        lambda effect: (replace(effect, size=effect.size + 1),),
    ),
)
def test_artifact_effect_binding_rejects_every_descriptor_mismatch(mutate):
    from mission_application.artifact import make_evidence_effect
    from mission_kernel import decode_mission_state
    from mission_kernel.artifact import ArtifactEffectClaim
    from mission_kernel.commands import InitializeArtifact
    from mission_kernel.transitions import (
        TransitionTableError,
        bind_transition_effects,
        decide,
    )

    effect = make_evidence_effect("artifact", "reports/artifact.md", b"content\n")
    claim = ArtifactEffectClaim(
        effect.kind, effect.target, effect.digest, effect.size
    )
    state = decode_mission_state(
        json.dumps(
            {
                "phase": "executing",
                "loop_active": True,
                "session_id": "portable-run",
            }
        ).encode("utf-8")
    )
    decision = decide(
        state,
        InitializeArtifact(
            "2030-01-02T03:04:05Z",
            effect.target,
            "markdown",
            "Portable artifact",
            "reviewed",
            False,
            claim,
        ),
    )
    assert decision.transition is not None

    with pytest.raises(
        TransitionTableError, match="invalid-transition-effect-binding"
    ):
        bind_transition_effects(decision.transition, mutate(effect))


def test_artifact_preparation_returns_typed_command_effects_and_payload_only():
    from mission_application.artifact import PreparedArtifactOperation, prepare_artifact_init
    from mission_kernel.commands import InitializeArtifact

    state = {
        "phase": "executing",
        "passes": False,
        "loop_active": True,
        "session_id": "portable-run",
        "mission": "Build a portable artifact",
    }

    prepared = prepare_artifact_init(
        state,
        now="2030-01-02T03:04:05Z",
        artifact_path="reports/artifact.md",
        format="markdown",
        title="Portable artifact",
        redaction_status="reviewed",
        required_for_pass=True,
        render=lambda document, artifact: (
            artifact["title"] + "\n" + document["mission"] + "\n"
        ).encode("utf-8"),
    )

    assert isinstance(prepared, PreparedArtifactOperation)
    assert isinstance(prepared.command, InitializeArtifact)
    assert [effect.target for effect in prepared.effects] == ["reports/artifact.md"]
    assert prepared.command.effect.digest == prepared.effects[0].digest
    assert prepared.result["artifact"]["producer_run_id"] == "portable-run"
    assert not hasattr(prepared, "state")


def test_repository_executes_artifact_transition_in_the_closed_order(monkeypatch):
    import mission_persistence.legacy_v4 as legacy
    from mission_application.artifact import prepare_artifact_init
    from mission_application.ports import LegacyCommandExecutionResult

    calls = []
    state = {
        "phase": "executing",
        "passes": False,
        "loop_active": True,
        "halt_reason": "",
        "session_id": "portable-run",
    }
    saved = []

    @contextmanager
    def lock():
        calls.append("lock-enter")
        try:
            yield
        finally:
            calls.append("lock-exit")

    @contextmanager
    def publish(effects):
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
        return prepare_artifact_init(
            document,
            now="2030-01-02T03:04:05Z",
            artifact_path="reports/artifact.md",
            format="markdown",
            title="Portable artifact",
            redaction_status="reviewed",
            required_for_pass=False,
            render=lambda _state, _artifact: b"content\n",
        )

    prepared, execution = repository.execute_transition_effects(
        prepare,
        effect_transaction=publish,
        verify_published=lambda effects, published: calls.append("verify"),
    )

    assert isinstance(execution, LegacyCommandExecutionResult)
    assert execution.projection == saved[0]
    assert prepared.result["artifact"] == saved[0]["artifact"]
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


@pytest.mark.parametrize(
    ("document", "command_factory", "expected_code"),
    (
        (
            {"session_id": "portable-run"},
            lambda claim: __import__(
                "mission_kernel.commands", fromlist=["InitializeArtifact"]
            ).InitializeArtifact(
                "2030-01-02T03:04:05Z",
                "../outside.md",
                "markdown",
                "Artifact",
                "reviewed",
                False,
                claim,
            ),
            "artifact-path-invalid",
        ),
        (
            {"session_id": "portable-run"},
            lambda claim: __import__(
                "mission_kernel.commands", fromlist=["InitializeArtifact"]
            ).InitializeArtifact(
                "2030-01-02T03:04:05Z",
                "reports/artifact.md",
                "markdown",
                "Artifact",
                "forged",
                False,
                claim,
            ),
            "artifact-redaction-invalid",
        ),
        (
            {
                "session_id": "portable-run",
                "artifact": {"path": "reports/artifact.md", "blocks": []},
            },
            lambda _claim: __import__(
                "mission_kernel.commands", fromlist=["AppendArtifactBlock"]
            ).AppendArtifactBlock(
                "2030-01-02T03:04:05Z", "unknown", "text", None, None
            ),
            "artifact-section-invalid",
        ),
        (
            {
                "session_id": "portable-run",
                "artifact": {"path": "reports/artifact.md", "blocks": {}},
            },
            lambda _claim: __import__(
                "mission_kernel.commands", fromlist=["AppendArtifactBlock"]
            ).AppendArtifactBlock(
                "2030-01-02T03:04:05Z", "evidence", "text", None, None
            ),
            "artifact-blocks-invalid",
        ),
        (
            {
                "session_id": "portable-run",
                "artifact": {
                    "path": "reports/artifact.md",
                    "blocks": [],
                    "exports": [],
                },
            },
            lambda claim: __import__(
                "mission_kernel.commands", fromlist=["ExportArtifact"]
            ).ExportArtifact(
                "2030-01-02T03:04:05Z",
                "../outside.md",
                "reviewed",
                claim,
                replace(claim, kind="artifact-export", target="../outside.md"),
            ),
            "artifact-export-path-invalid",
        ),
        (
            {
                "session_id": "portable-run",
                "artifact": {
                    "path": "reports/artifact.md",
                    "redaction_status": "reviewed",
                    "blocks": [],
                    "publish_events": [],
                },
            },
            lambda claim: __import__(
                "mission_kernel.commands", fromlist=["RecordArtifactPublication"]
            ).RecordArtifactPublication(
                "2030-01-02T03:04:05Z",
                "local",
                None,
                "approved",
                False,
                claim,
            ),
            "artifact-confirmation-required",
        ),
        (
            {
                "session_id": "portable-run",
                "artifact": {
                    "path": "reports/artifact.md",
                    "redaction_status": "reviewed",
                    "blocks": [],
                    "publish_events": [],
                },
            },
            lambda claim: __import__(
                "mission_kernel.commands", fromlist=["RecordArtifactPublication"]
            ).RecordArtifactPublication(
                "2030-01-02T03:04:05Z", "", None, "approved", True, claim
            ),
            "artifact-provider-invalid",
        ),
    ),
)
def test_artifact_reducers_reject_invalid_semantic_inputs(
    document, command_factory, expected_code
):
    from mission_kernel import decode_mission_state
    from mission_kernel.transitions import decide

    state = decode_mission_state(
        json.dumps(
            {
                "phase": "executing",
                "loop_active": True,
                **document,
            }
        ).encode("utf-8")
    )
    decision = decide(state, command_factory(_claim()))

    assert decision.accepted is False
    assert decision.transition is None
    assert decision.rejection is not None
    assert decision.rejection.code == expected_code


def test_export_effects_bind_in_order_and_duplicate_targets_close_before_publish():
    from mission_application.artifact import prepare_artifact_export
    from mission_kernel import decode_mission_state
    from mission_kernel.transitions import (
        TransitionTableError,
        bind_transition_effects,
        decide,
    )
    from mission_persistence.legacy_v4 import LegacyV4Repository

    document = {
        "phase": "executing",
        "loop_active": True,
        "session_id": "portable-run",
        "artifact": {
            "status": "rendered",
            "format": "markdown",
            "title": "Artifact",
            "path": "reports/artifact.md",
            "exports": [],
            "publish_events": [],
            "redaction_status": "reviewed",
            "required_for_pass": False,
            "blocks": [],
        },
    }
    prepared = prepare_artifact_export(
        document,
        now="2030-01-02T03:04:05Z",
        destination="reports/export.md",
        redaction_status="reviewed",
        render=lambda _state, _artifact: b"same content\n",
    )
    decision = decide(
        decode_mission_state(json.dumps(document).encode("utf-8")),
        prepared.command,
    )
    assert decision.transition is not None

    bound = bind_transition_effects(decision.transition, prepared.effects)
    assert bound.effects == prepared.effects
    with pytest.raises(
        TransitionTableError, match="invalid-transition-effect-binding"
    ):
        bind_transition_effects(
            decision.transition, tuple(reversed(prepared.effects))
        )
    duplicate = (
        prepared.effects[0],
        replace(prepared.effects[1], target=prepared.effects[0].target),
    )
    with pytest.raises(ValueError, match="effect-target-duplicated"):
        LegacyV4Repository.validate_effects(duplicate)


def test_artifact_init_updates_current_v5_state_and_preserves_head(tmp_path, run_cli):
    from mission_persistence.authoritative_reader import read_authoritative_snapshot

    initialized = run_cli(
        "init",
        "Initialize artifact on v5",
        "--complexity",
        "Simple",
        "--force-mission",
        "--artifact-applicability",
        "not-applicable",
        cwd=tmp_path,
    )
    assert initialized.returncode == 0, initialized.stderr
    session_path = tmp_path / ".mission-state" / "sessions" / "test.json"
    before_head = json.loads(session_path.read_text(encoding="utf-8"))

    result = run_cli(
        "artifact", "init", "--title", "V5 Artifact", "--json", cwd=tmp_path
    )

    assert result.returncode == 0, result.stderr
    state = read_authoritative_snapshot(
        session_path, expected_session_id="test"
    ).document_copy()
    artifact = state["artifact"]
    contract_fields = {
        key: artifact[key]
        for key in (
            "status",
            "format",
            "title",
            "path",
            "exports",
            "publish_events",
            "redaction_status",
            "required_for_pass",
            "blocks",
        )
    }
    assert contract_fields == {
        "status": "draft",
        "format": "markdown",
        "title": "V5 Artifact",
        "path": ".mission-state/artifacts/test/mission-artifact.md",
        "exports": [],
        "publish_events": [],
        "redaction_status": "unchecked",
        "required_for_pass": False,
        "blocks": [],
    }
    artifact_path = tmp_path / artifact["path"]
    assert artifact_path.is_file()
    artifact_bytes = artifact_path.read_bytes()
    assert "V5 Artifact" in artifact_bytes.decode("utf-8")
    assert artifact["digest"] == hashlib.sha256(artifact_bytes).hexdigest()
    assert artifact["size"] == len(artifact_bytes)
    assert artifact["producer_run_id"] == "test"
    assert artifact["created_at"] == artifact["updated_at"]
    after_head = json.loads(session_path.read_text(encoding="utf-8"))
    assert after_head["schema"] == "mission-head/1"
    assert after_head["generation"] == before_head["generation"] + 1
