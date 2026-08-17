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


MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
ARTIFACT_REDACTION_STATUSES = frozenset(
    {"unchecked", "checked", "reviewed", "not-needed"}
)
ARTIFACT_SECTIONS = frozenset(
    {"mission", "plan", "execution", "evidence", "review", "score_gate", "assumptions", "follow_ups"}
)
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


def _bind_artifact_identity(state: dict, artifact: dict, effect: EvidenceEffect) -> None:
    artifact.update(
        {
            "path": effect.target,
            "digest": effect.digest.removeprefix("sha256:"),
            "size": effect.size,
            "producer_run_id": str(state.get("session_id") or "").strip(),
        }
    )
    if not artifact["producer_run_id"]:
        raise EvidenceFailure("artifact-producer-invalid")
    for key in ("artifact_lint", "artifact_lint_status", "artifact_lint_identity"):
        state.pop(key, None)
    state["artifact_applicability"] = "producing"
    state["artifact"] = artifact


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
    proposed = _state(state)
    at = _timestamp(now)
    path = _relative_target(artifact_path, "artifact-path-invalid")
    if not isinstance(format, str) or not format:
        raise EvidenceFailure("artifact-format-invalid")
    if not isinstance(title, str) or not title:
        raise EvidenceFailure("artifact-title-invalid")
    if redaction_status not in ARTIFACT_REDACTION_STATUSES:
        raise EvidenceFailure("artifact-redaction-invalid")
    if type(required_for_pass) is not bool:
        raise EvidenceFailure("artifact-required-invalid")
    artifact = {
        "status": "draft",
        "format": format,
        "title": title,
        "path": path,
        "exports": [],
        "publish_events": [],
        "redaction_status": redaction_status,
        "required_for_pass": required_for_pass,
        "blocks": [],
        "created_at": at,
        "updated_at": at,
    }
    proposed["artifact"] = artifact
    proposed["artifact_applicability"] = "producing"
    proposed["updated_at"] = at
    effect = _render_effect(proposed, artifact, render)
    _bind_artifact_identity(proposed, artifact, effect)
    return EvidenceDecision(proposed, (effect,), {"artifact": copy.deepcopy(artifact)})


def artifact_append(
    state: object,
    *,
    now: object,
    section: object,
    content: object,
    source: object,
    label: object,
) -> EvidenceDecision:
    proposed = _state(state)
    artifact = _artifact(proposed)
    at = _timestamp(now)
    if section not in ARTIFACT_SECTIONS:
        raise EvidenceFailure("artifact-section-invalid")
    if not isinstance(content, str):
        raise EvidenceFailure("artifact-content-invalid")
    block = {"section": section, "content": content.rstrip(), "timestamp": at}
    if source:
        block["source"] = _text(source, "artifact-source-invalid")
    elif source not in (None, ""):
        raise EvidenceFailure("artifact-source-invalid")
    if label:
        block["label"] = _text(label, "artifact-label-invalid")
    elif label not in (None, ""):
        raise EvidenceFailure("artifact-label-invalid")
    blocks = artifact.get("blocks")
    if not isinstance(blocks, list):
        raise EvidenceFailure("artifact-blocks-invalid")
    blocks.append(block)
    artifact["status"] = "draft"
    artifact.pop("digest", None)
    artifact.pop("size", None)
    artifact["updated_at"] = at
    for key in ("artifact_lint", "artifact_lint_status", "artifact_lint_identity"):
        proposed.pop(key, None)
    proposed["artifact"] = artifact
    proposed["updated_at"] = at
    return EvidenceDecision(
        proposed, (), {"section": section, "block": copy.deepcopy(block)}
    )


def artifact_render(
    state: object,
    *,
    now: object,
    redaction_status: object,
    render: Callable[[dict, dict], bytes],
) -> EvidenceDecision:
    proposed = _state(state)
    artifact = _artifact(proposed)
    at = _timestamp(now)
    if redaction_status is not None:
        if redaction_status not in ARTIFACT_REDACTION_STATUSES:
            raise EvidenceFailure("artifact-redaction-invalid")
        artifact["redaction_status"] = redaction_status
    artifact["status"] = "rendered"
    artifact["last_rendered_at"] = at
    artifact["updated_at"] = at
    proposed["artifact"] = artifact
    proposed["updated_at"] = at
    effect = _render_effect(proposed, artifact, render)
    _bind_artifact_identity(proposed, artifact, effect)
    return EvidenceDecision(
        proposed,
        (effect,),
        {"path": effect.target, "artifact": copy.deepcopy(artifact)},
    )


def artifact_export(
    state: object,
    *,
    now: object,
    destination: object,
    redaction_status: object,
    render: Callable[[dict, dict], bytes],
) -> EvidenceDecision:
    proposed = _state(state)
    artifact = _artifact(proposed)
    at = _timestamp(now)
    dst = _relative_target(destination, "artifact-export-path-invalid")
    if redaction_status not in ARTIFACT_REDACTION_STATUSES - {"unchecked"}:
        raise EvidenceFailure("artifact-export-redaction-invalid")
    artifact["redaction_status"] = redaction_status
    artifact["status"] = "exported"
    artifact["last_rendered_at"] = at
    artifact["updated_at"] = at
    proposed["artifact"] = artifact
    proposed["updated_at"] = at
    source_effect = _render_effect(proposed, artifact, render)
    _bind_artifact_identity(proposed, artifact, source_effect)
    export_effect = make_evidence_effect("artifact-export", dst, source_effect.content)
    export_entry = {"path": dst, "timestamp": at, "redaction_status": redaction_status}
    exports = artifact.get("exports")
    if not isinstance(exports, list):
        raise EvidenceFailure("artifact-exports-invalid")
    exports.append(export_entry)
    proposed["artifact"] = artifact
    return EvidenceDecision(
        proposed,
        (source_effect, export_effect),
        {"export": copy.deepcopy(export_entry), "artifact": copy.deepcopy(artifact)},
    )


def artifact_publish(
    state: object,
    *,
    now: object,
    provider: object,
    destination: object,
    approval_text: object,
    render: Callable[[dict, dict], bytes],
) -> EvidenceDecision:
    proposed = _state(state)
    artifact = _artifact(proposed)
    at = _timestamp(now)
    provider_text = _text(provider, "artifact-provider-invalid")
    approval = _text(approval_text, "artifact-approval-invalid")
    if artifact.get("redaction_status") == "unchecked":
        raise EvidenceFailure("artifact-publish-redaction-invalid")
    has_destination = isinstance(destination, str) and bool(destination)
    if destination not in (None, "") and not has_destination:
        raise EvidenceFailure("artifact-destination-invalid")
    event = {
        "provider": provider_text,
        "timestamp": at,
        "approval_text": approval,
        "status": "published" if has_destination else "publish-prepared",
    }
    if has_destination:
        event["destination"] = _text(destination, "artifact-destination-invalid")
    events = artifact.get("publish_events")
    if not isinstance(events, list):
        raise EvidenceFailure("artifact-publish-events-invalid")
    events.append(event)
    artifact["status"] = event["status"]
    artifact["updated_at"] = at
    proposed["artifact"] = artifact
    proposed["updated_at"] = at
    effect = _render_effect(proposed, artifact, render)
    _bind_artifact_identity(proposed, artifact, effect)
    event["artifact_path"] = effect.target
    proposed["artifact"] = artifact
    return EvidenceDecision(
        proposed,
        (effect,),
        {"publish_event": copy.deepcopy(event), "artifact": copy.deepcopy(artifact)},
    )


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
