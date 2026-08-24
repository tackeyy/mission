"""Pure A3 artifact, progress, and context evidence decisions.

This module accepts already captured values and returns inert, content-bound
effect requests.  It deliberately has no filesystem or repository dependency;
the selected persistence adapter owns lease validation and effect publication.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Callable, Mapping

from mission_kernel.artifact import (
    ArtifactEffectClaim,
    ArtifactRuleError,
    append_artifact_block_document,
    export_artifact_document,
    initialize_artifact_document,
    record_artifact_publication_document,
    render_artifact_document,
)
from mission_kernel.commands import (
    AppendArtifactBlock,
    Command,
    ExportArtifact,
    InitializeArtifact,
    RecordArtifactPublication,
    RenderArtifact,
)


MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
_EFFECT_KIND = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class EvidenceFailure(ValueError):
    """A stable fail-closed A3 validation error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class EvidenceEffect:
    """Immutable bytes bound to one adapter-owned publication target."""

    kind: str
    target: str
    content: bytes
    digest: str
    size: int


@dataclass(frozen=True)
class EvidenceDecision:
    """One proposed v4 state plus inert effects and public response fields."""

    state: dict
    effects: tuple[EvidenceEffect, ...]
    result: dict


@dataclass(frozen=True)
class PreparedArtifactOperation:
    """Typed artifact command plus inert bytes and CLI response payload."""

    command: Command
    effects: tuple[EvidenceEffect, ...]
    result: dict


@dataclass(frozen=True)
class ArtifactInitRequest:
    now: object
    artifact_path: object
    format: object
    title: object
    redaction_status: object
    required_for_pass: object


@dataclass(frozen=True)
class ArtifactAppendRequest:
    now: object
    section: object
    content: object
    source: object
    label: object


@dataclass(frozen=True)
class ArtifactRenderRequest:
    now: object
    redaction_status: object


@dataclass(frozen=True)
class ArtifactExportRequest:
    now: object
    destination: object
    redaction_status: object


@dataclass(frozen=True)
class ArtifactPublishRequest:
    now: object
    provider: object
    destination: object
    approval_text: object
    confirmed: object


def execute_artifact_operation(
    repository: object,
    prepare: Callable[[dict], PreparedArtifactOperation],
) -> dict:
    """Execute one prepared artifact operation through its repository port."""
    execute = getattr(repository, "execute_transition_effects", None)
    if not callable(execute):
        raise EvidenceFailure("artifact-repository-invalid")
    prepared, execution = execute(prepare)
    decision = getattr(execution, "decision", None)
    if decision is None or decision.accepted is not True:
        rejection = getattr(decision, "rejection", None)
        raise EvidenceFailure(
            getattr(rejection, "code", "artifact-transition-rejected")
        )
    projection = execution.projection
    payload = copy.deepcopy(prepared.result)
    if "artifact" in payload and payload["artifact"] != projection.get("artifact"):
        raise EvidenceFailure("artifact-projection-mismatch")
    return payload


def _artifact_render_bytes(
    render_text: Callable[[dict, dict], str]
) -> Callable[[dict, dict], bytes]:
    return lambda state, artifact: render_text(state, artifact).encode("utf-8")


def run_artifact_init(
    request: ArtifactInitRequest,
    repository: object,
    render_text: Callable[[dict, dict], str],
) -> dict:
    return execute_artifact_operation(
        repository,
        lambda state: prepare_artifact_init(
            state,
            now=request.now,
            artifact_path=request.artifact_path,
            format=request.format,
            title=request.title or state.get("mission") or "Mission Artifact",
            redaction_status=request.redaction_status,
            required_for_pass=request.required_for_pass,
            render=_artifact_render_bytes(render_text),
        ),
    )


def run_artifact_append(request: ArtifactAppendRequest, repository: object) -> dict:
    return execute_artifact_operation(
        repository,
        lambda state: prepare_artifact_append(
            state,
            now=request.now,
            section=request.section,
            content=request.content,
            source=request.source,
            label=request.label,
        ),
    )


def run_artifact_render(
    request: ArtifactRenderRequest,
    repository: object,
    render_text: Callable[[dict, dict], str],
) -> dict:
    return execute_artifact_operation(
        repository,
        lambda state: prepare_artifact_render(
            state,
            now=request.now,
            redaction_status=request.redaction_status,
            render=_artifact_render_bytes(render_text),
        ),
    )


def run_artifact_export(
    request: ArtifactExportRequest,
    repository: object,
    render_text: Callable[[dict, dict], str],
) -> dict:
    return execute_artifact_operation(
        repository,
        lambda state: prepare_artifact_export(
            state,
            now=request.now,
            destination=request.destination,
            redaction_status=request.redaction_status,
            render=_artifact_render_bytes(render_text),
        ),
    )


def run_artifact_publish(
    request: ArtifactPublishRequest,
    repository: object,
    render_text: Callable[[dict, dict], str],
) -> dict:
    return execute_artifact_operation(
        repository,
        lambda state: prepare_artifact_publish(
            state,
            now=request.now,
            provider=request.provider,
            destination=request.destination,
            approval_text=request.approval_text,
            confirmed=request.confirmed,
            render=_artifact_render_bytes(render_text),
        ),
    )


def verify_published_artifact_effects(
    cwd: object,
    effects: tuple[EvidenceEffect, ...],
    published: object,
    capture: Callable[[object, str, str], tuple[dict, bytes]],
    relative_path: Callable[[object, str], str],
    verify_object: Callable[[object], None],
    path_overrides: Mapping[str, object] | None = None,
) -> None:
    """Validate actual path/content identity without mutating decided state."""
    items = tuple(published) if isinstance(published, (tuple, list)) else ()
    if len(items) != len(effects):
        raise ValueError("published artifact effect count changed")
    for effect, item in zip(effects, items):
        verify_object(item)
        actual_target = relative_path(cwd, str(item.path))
        expected_target = effect.target
        override = (path_overrides or {}).get(effect.kind)
        if override is not None:
            expected_target = relative_path(cwd, str(override))
        identity, payload = capture(cwd, actual_target, "artifact-effect-verifier")
        if (
            actual_target != expected_target
            or identity.get("digest") != effect.digest.removeprefix("sha256:")
            or identity.get("size") != effect.size
            or payload != effect.content
        ):
            raise ValueError("published artifact effect identity changed")


def _text(value: object, code: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or "\x00" in value or (not allow_empty and not value):
        raise EvidenceFailure(code)
    return value


def _relative_target(value: object, code: str) -> str:
    text = _text(value, code)
    if text.startswith("/") or "\\" in text:
        raise EvidenceFailure(code)
    parts = text.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise EvidenceFailure(code)
    return text


def _timestamp(value: object) -> str:
    return _text(value, "timestamp-invalid")


def _state(value: object) -> dict:
    if not isinstance(value, Mapping):
        raise EvidenceFailure("state-invalid")
    return copy.deepcopy(dict(value))


def make_evidence_effect(kind: object, target: object, content: object) -> EvidenceEffect:
    if not isinstance(kind, str) or _EFFECT_KIND.fullmatch(kind) is None:
        raise EvidenceFailure("effect-kind-invalid")
    target_text = _relative_target(target, "effect-target-invalid")
    if type(content) is not bytes:
        raise EvidenceFailure("effect-content-invalid")
    if len(content) > MAX_EVIDENCE_BYTES:
        raise EvidenceFailure("effect-content-too-large")
    return EvidenceEffect(
        kind=kind,
        target=target_text,
        content=content,
        digest="sha256:" + hashlib.sha256(content).hexdigest(),
        size=len(content),
    )


def validate_evidence_effect(value: object) -> EvidenceEffect:
    if not isinstance(value, EvidenceEffect):
        raise ValueError("effect-binding-invalid")
    if (
        _EFFECT_KIND.fullmatch(value.kind) is None
        or _relative_target(value.target, "effect-target-invalid") != value.target
        or type(value.content) is not bytes
        or type(value.size) is not int
        or value.size < 0
        or value.size > MAX_EVIDENCE_BYTES
        or _DIGEST.fullmatch(value.digest) is None
        or value.size != len(value.content)
        or value.digest != "sha256:" + hashlib.sha256(value.content).hexdigest()
    ):
        raise ValueError("effect-binding-invalid")
    return value


def _artifact(value: dict) -> dict:
    artifact = value.get("artifact")
    if not isinstance(artifact, Mapping):
        raise EvidenceFailure("artifact-missing")
    return copy.deepcopy(dict(artifact))


def _render_effect(
    state: dict,
    artifact: dict,
    render: Callable[[dict, dict], bytes],
) -> EvidenceEffect:
    try:
        content = render(copy.deepcopy(state), copy.deepcopy(artifact))
    except EvidenceFailure:
        raise
    except Exception as exc:
        raise EvidenceFailure("artifact-render-failed") from exc
    return make_evidence_effect("artifact", artifact.get("path"), content)


def _effect_claim(effect: EvidenceEffect) -> ArtifactEffectClaim:
    return ArtifactEffectClaim(
        kind=effect.kind,
        target=effect.target,
        digest=effect.digest,
        size=effect.size,
    )


def _translate_artifact_rule(call: Callable[[], dict]) -> dict:
    try:
        return call()
    except ArtifactRuleError as exc:
        raise EvidenceFailure(exc.code) from exc


def prepare_artifact_init(
    state: object,
    *,
    now: object,
    artifact_path: object,
    format: object,
    title: object,
    redaction_status: object,
    required_for_pass: object,
    render: Callable[[dict, dict], bytes],
) -> PreparedArtifactOperation:
    original = _state(state)
    provisional = _translate_artifact_rule(
        lambda: initialize_artifact_document(
            original,
            at=now,
            path=artifact_path,
            format=format,
            title=title,
            redaction_status=redaction_status,
            required_for_pass=required_for_pass,
            effect=None,
        )
    )
    effect = _render_effect(provisional, _artifact(provisional), render)
    claim = _effect_claim(effect)
    command = InitializeArtifact(
        at=now,
        path=artifact_path,
        format=format,
        title=title,
        redaction_status=redaction_status,
        required_for_pass=required_for_pass,
        effect=claim,
    )
    final = _translate_artifact_rule(
        lambda: initialize_artifact_document(
            original,
            at=command.at,
            path=command.path,
            format=command.format,
            title=command.title,
            redaction_status=command.redaction_status,
            required_for_pass=command.required_for_pass,
            effect=command.effect,
        )
    )
    return PreparedArtifactOperation(
        command,
        (effect,),
        {"artifact": copy.deepcopy(final["artifact"])},
    )


def prepare_artifact_append(
    state: object,
    *,
    now: object,
    section: object,
    content: object,
    source: object,
    label: object,
) -> PreparedArtifactOperation:
    original = _state(state)
    command = AppendArtifactBlock(now, section, content, source, label)
    final = _translate_artifact_rule(
        lambda: append_artifact_block_document(
            original,
            at=command.at,
            section=command.section,
            content=command.content,
            source=command.source,
            label=command.label,
        )
    )
    block = final["artifact"]["blocks"][-1]
    return PreparedArtifactOperation(
        command,
        (),
        {"section": command.section, "block": copy.deepcopy(block)},
    )


def prepare_artifact_render(
    state: object,
    *,
    now: object,
    redaction_status: object,
    render: Callable[[dict, dict], bytes],
) -> PreparedArtifactOperation:
    original = _state(state)
    provisional = _translate_artifact_rule(
        lambda: render_artifact_document(
            original, at=now, redaction_status=redaction_status, effect=None
        )
    )
    effect = _render_effect(provisional, _artifact(provisional), render)
    command = RenderArtifact(now, redaction_status, _effect_claim(effect))
    final = _translate_artifact_rule(
        lambda: render_artifact_document(
            original,
            at=command.at,
            redaction_status=command.redaction_status,
            effect=command.effect,
        )
    )
    return PreparedArtifactOperation(
        command,
        (effect,),
        {"path": effect.target, "artifact": copy.deepcopy(final["artifact"])},
    )


def prepare_artifact_export(
    state: object,
    *,
    now: object,
    destination: object,
    redaction_status: object,
    render: Callable[[dict, dict], bytes],
) -> PreparedArtifactOperation:
    original = _state(state)
    provisional = _translate_artifact_rule(
        lambda: export_artifact_document(
            original,
            at=now,
            destination=destination,
            redaction_status=redaction_status,
            artifact_effect=None,
            export_effect=None,
        )
    )
    source_effect = _render_effect(provisional, _artifact(provisional), render)
    export_effect = make_evidence_effect(
        "artifact-export", destination, source_effect.content
    )
    command = ExportArtifact(
        now,
        destination,
        redaction_status,
        _effect_claim(source_effect),
        _effect_claim(export_effect),
    )
    final = _translate_artifact_rule(
        lambda: export_artifact_document(
            original,
            at=command.at,
            destination=command.destination,
            redaction_status=command.redaction_status,
            artifact_effect=command.artifact_effect,
            export_effect=command.export_effect,
        )
    )
    return PreparedArtifactOperation(
        command,
        (source_effect, export_effect),
        {
            "export": copy.deepcopy(final["artifact"]["exports"][-1]),
            "artifact": copy.deepcopy(final["artifact"]),
        },
    )


def prepare_artifact_publish(
    state: object,
    *,
    now: object,
    provider: object,
    destination: object,
    approval_text: object,
    confirmed: object,
    render: Callable[[dict, dict], bytes],
) -> PreparedArtifactOperation:
    original = _state(state)
    provisional = _translate_artifact_rule(
        lambda: record_artifact_publication_document(
            original,
            at=now,
            provider=provider,
            destination=destination,
            approval_text=approval_text,
            confirmed=confirmed,
            effect=None,
        )
    )
    effect = _render_effect(provisional, _artifact(provisional), render)
    command = RecordArtifactPublication(
        now,
        provider,
        destination,
        approval_text,
        confirmed,
        _effect_claim(effect),
    )
    final = _translate_artifact_rule(
        lambda: record_artifact_publication_document(
            original,
            at=command.at,
            provider=command.provider,
            destination=command.destination,
            approval_text=command.approval_text,
            confirmed=command.confirmed,
            effect=command.effect,
        )
    )
    return PreparedArtifactOperation(
        command,
        (effect,),
        {
            "publish_event": copy.deepcopy(final["artifact"]["publish_events"][-1]),
            "artifact": copy.deepcopy(final["artifact"]),
        },
    )


def artifact_init(
    state: object,
    *,
    now: object,
    artifact_path: object,
    format: object,
    title: object,
    redaction_status: object,
    required_for_pass: object,
    render: Callable[[dict, dict], bytes],
) -> EvidenceDecision:
    prepared = prepare_artifact_init(
        state,
        now=now,
        artifact_path=artifact_path,
        format=format,
        title=title,
        redaction_status=redaction_status,
        required_for_pass=required_for_pass,
        render=render,
    )
    command = prepared.command
    assert isinstance(command, InitializeArtifact)
    proposed = _translate_artifact_rule(
        lambda: initialize_artifact_document(
            state,
            at=command.at,
            path=command.path,
            format=command.format,
            title=command.title,
            redaction_status=command.redaction_status,
            required_for_pass=command.required_for_pass,
            effect=command.effect,
        )
    )
    return EvidenceDecision(proposed, prepared.effects, prepared.result)


def artifact_append(
    state: object,
    *,
    now: object,
    section: object,
    content: object,
    source: object,
    label: object,
) -> EvidenceDecision:
    prepared = prepare_artifact_append(
        state,
        now=now,
        section=section,
        content=content,
        source=source,
        label=label,
    )
    command = prepared.command
    assert isinstance(command, AppendArtifactBlock)
    proposed = _translate_artifact_rule(
        lambda: append_artifact_block_document(
            state,
            at=command.at,
            section=command.section,
            content=command.content,
            source=command.source,
            label=command.label,
        )
    )
    return EvidenceDecision(proposed, prepared.effects, prepared.result)


def artifact_render(
    state: object,
    *,
    now: object,
    redaction_status: object,
    render: Callable[[dict, dict], bytes],
) -> EvidenceDecision:
    prepared = prepare_artifact_render(
        state, now=now, redaction_status=redaction_status, render=render
    )
    command = prepared.command
    assert isinstance(command, RenderArtifact)
    proposed = _translate_artifact_rule(
        lambda: render_artifact_document(
            state,
            at=command.at,
            redaction_status=command.redaction_status,
            effect=command.effect,
        )
    )
    return EvidenceDecision(proposed, prepared.effects, prepared.result)


def artifact_export(
    state: object,
    *,
    now: object,
    destination: object,
    redaction_status: object,
    render: Callable[[dict, dict], bytes],
) -> EvidenceDecision:
    prepared = prepare_artifact_export(
        state,
        now=now,
        destination=destination,
        redaction_status=redaction_status,
        render=render,
    )
    command = prepared.command
    assert isinstance(command, ExportArtifact)
    proposed = _translate_artifact_rule(
        lambda: export_artifact_document(
            state,
            at=command.at,
            destination=command.destination,
            redaction_status=command.redaction_status,
            artifact_effect=command.artifact_effect,
            export_effect=command.export_effect,
        )
    )
    return EvidenceDecision(proposed, prepared.effects, prepared.result)


def artifact_publish(
    state: object,
    *,
    now: object,
    provider: object,
    destination: object,
    approval_text: object,
    render: Callable[[dict, dict], bytes],
) -> EvidenceDecision:
    prepared = prepare_artifact_publish(
        state,
        now=now,
        provider=provider,
        destination=destination,
        approval_text=approval_text,
        confirmed=True,
        render=render,
    )
    command = prepared.command
    assert isinstance(command, RecordArtifactPublication)
    proposed = _translate_artifact_rule(
        lambda: record_artifact_publication_document(
            state,
            at=command.at,
            provider=command.provider,
            destination=command.destination,
            approval_text=command.approval_text,
            confirmed=command.confirmed,
            effect=command.effect,
        )
    )
    return EvidenceDecision(proposed, prepared.effects, prepared.result)


def _progress_bytes(state: dict, progress: dict, iteration: int) -> bytes:
    lines = [
        f"<!-- mission-progress-meta: session_id={state.get('session_id')} mission_id={state.get('mission_id')} iteration={iteration} updated_at={progress.get('updated_at')} -->",
        "",
        "# Mission Progress Checkpoint",
        "",
        f"- kind: {progress.get('kind')}",
        f"- total: {progress.get('total')}",
        f"- completed: {progress.get('completed')}",
        f"- remaining: {progress.get('remaining')}",
        f"- batch_size: {progress.get('batch_size')}",
        f"- last_unit: {progress.get('last_unit') or ''}",
        f"- artifact_path: {progress.get('artifact_path') or ''}",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def progress_update(
    state: object,
    *,
    now: object,
    total: object,
    completed: object,
    batch_size: object,
    last_unit: object,
    artifact_path: object,
    iteration: object,
    evidence_path: object,
) -> EvidenceDecision:
    proposed = _state(state)
    at = _timestamp(now)
    if type(total) is not int or type(completed) is not int or total < 0 or not 0 <= completed <= total:
        raise EvidenceFailure("progress-bounds-invalid")
    if type(iteration) is not int or iteration < 0:
        raise EvidenceFailure("progress-iteration-invalid")
    if batch_size is not None and (type(batch_size) is not int or batch_size < 0):
        raise EvidenceFailure("progress-batch-size-invalid")
    for optional, code in (
        (last_unit, "progress-last-unit-invalid"),
        (artifact_path, "progress-artifact-path-invalid"),
    ):
        if optional is not None:
            _text(optional, code)
    target = _relative_target(evidence_path, "progress-evidence-path-invalid")
    progress = {
        "kind": "batch",
        "total": total,
        "completed": completed,
        "remaining": total - completed,
        "batch_size": batch_size,
        "last_unit": last_unit,
        "artifact_path": artifact_path,
        "updated_at": at,
        "evidence_path": target,
    }
    effect = make_evidence_effect("progress", target, _progress_bytes(proposed, progress, iteration))
    proposed["progress"] = progress
    proposed["updated_at"] = at
    return EvidenceDecision(proposed, (effect,), {"progress": copy.deepcopy(progress)})


def progress_clear(state: object, *, now: object) -> EvidenceDecision:
    proposed = _state(state)
    proposed.pop("progress", None)
    proposed["updated_at"] = _timestamp(now)
    return EvidenceDecision(proposed, (), {})


def context_manifest(
    state: object,
    *,
    now: object,
    iteration: object,
    output_path: object,
    effect_target: object | None = None,
) -> EvidenceDecision:
    proposed = _state(state)
    at = _timestamp(now)
    if type(iteration) is not int or iteration < 1:
        raise EvidenceFailure("context-iteration-invalid")
    target = _text(output_path, "context-output-path-invalid")
    publication_target = _relative_target(
        effect_target if effect_target is not None else target,
        "context-effect-target-invalid",
    )
    prior_findings: list[dict] = []
    history = proposed.get("score_history")
    if history is not None and not isinstance(history, list):
        raise EvidenceFailure("context-score-history-invalid")
    for entry in history or []:
        if not isinstance(entry, Mapping):
            continue
        findings = entry.get("findings_summary", [])
        if not isinstance(findings, list):
            raise EvidenceFailure("context-findings-invalid")
        prior_findings.extend(copy.deepcopy(item) for item in findings if isinstance(item, Mapping))
    manifest = {
        "schema": "mission-context-manifest/1",
        "iteration": iteration,
        "mission_goal": proposed.get("mission", ""),
        "mission_id": proposed.get("mission_id", ""),
        "assumptions_path": proposed.get("assumptions_path", ""),
        "prior_findings": prior_findings,
    }
    content = json.dumps(manifest, indent=2, ensure_ascii=False).encode("utf-8")
    effect = make_evidence_effect("context-manifest", publication_target, content)
    records = proposed.get("context_manifests")
    records = copy.deepcopy(records) if isinstance(records, Mapping) else {}
    records[str(iteration)] = {"path": target, "digest": effect.digest, "generated_at": at}
    proposed["context_manifests"] = records
    return EvidenceDecision(
        proposed,
        (effect,),
        {"path": target, "digest": effect.digest, "findings_count": len(prior_findings)},
    )
