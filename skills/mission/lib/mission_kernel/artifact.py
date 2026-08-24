"""Pure artifact aggregate rules and immutable publication claims."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import re
from typing import Mapping, Optional


ARTIFACT_REDACTION_STATUSES = frozenset(
    {"unchecked", "checked", "reviewed", "not-needed"}
)
ARTIFACT_SECTIONS = frozenset(
    {
        "mission",
        "plan",
        "execution",
        "evidence",
        "review",
        "score_gate",
        "assumptions",
        "follow_ups",
    }
)
_EFFECT_KIND = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class ArtifactRuleError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ArtifactEffectClaim:
    """One content identity the adapter must bind before publication."""

    kind: str
    target: str
    digest: str
    size: int


def _text(value: object, code: str, *, allow_empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or "\x00" in value
        or (not allow_empty and not value)
    ):
        raise ArtifactRuleError(code)
    return value


def relative_artifact_target(value: object, code: str) -> str:
    text = _text(value, code)
    if text.startswith("/") or "\\" in text:
        raise ArtifactRuleError(code)
    if any(part in {"", ".", ".."} for part in text.split("/")):
        raise ArtifactRuleError(code)
    return text


def validate_artifact_effect_claim(
    value: object,
    *,
    kind: Optional[str] = None,
    target: Optional[str] = None,
) -> ArtifactEffectClaim:
    if not isinstance(value, ArtifactEffectClaim):
        raise ArtifactRuleError("artifact-effect-claim-invalid")
    if (
        _EFFECT_KIND.fullmatch(value.kind) is None
        or relative_artifact_target(value.target, "artifact-effect-claim-invalid")
        != value.target
        or _DIGEST.fullmatch(value.digest) is None
        or type(value.size) is not int
        or value.size < 0
        or (kind is not None and value.kind != kind)
        or (target is not None and value.target != target)
    ):
        raise ArtifactRuleError("artifact-effect-claim-invalid")
    return value


def _document(value: object) -> dict:
    if not isinstance(value, Mapping):
        raise ArtifactRuleError("artifact-state-invalid")
    return copy.deepcopy(dict(value))


def _artifact(document: dict) -> dict:
    artifact = document.get("artifact")
    if not isinstance(artifact, Mapping):
        raise ArtifactRuleError("artifact-missing")
    return copy.deepcopy(dict(artifact))


def _timestamp(value: object) -> str:
    return _text(value, "timestamp-invalid")


def _invalidate_lint(document: dict) -> None:
    for key in ("artifact_lint", "artifact_lint_status", "artifact_lint_identity"):
        document.pop(key, None)


def _producer_run_id(document: dict) -> str:
    value = str(document.get("session_id") or "").strip()
    if not value:
        raise ArtifactRuleError("artifact-producer-invalid")
    return value


def _bind_identity(
    document: dict,
    artifact: dict,
    effect: ArtifactEffectClaim,
) -> None:
    claim = validate_artifact_effect_claim(
        effect,
        kind="artifact",
        target=relative_artifact_target(
            artifact.get("path"), "artifact-path-invalid"
        ),
    )
    artifact.update(
        {
            "path": claim.target,
            "digest": claim.digest.removeprefix("sha256:"),
            "size": claim.size,
            "producer_run_id": _producer_run_id(document),
        }
    )
    _invalidate_lint(document)
    document["artifact_applicability"] = "producing"
    document["artifact"] = artifact


def initialize_artifact_document(
    value: object,
    *,
    at: object,
    path: object,
    format: object,
    title: object,
    redaction_status: object,
    required_for_pass: object,
    effect: Optional[ArtifactEffectClaim],
) -> dict:
    document = _document(value)
    timestamp = _timestamp(at)
    target = relative_artifact_target(path, "artifact-path-invalid")
    format_text = _text(format, "artifact-format-invalid")
    title_text = _text(title, "artifact-title-invalid")
    if redaction_status not in ARTIFACT_REDACTION_STATUSES:
        raise ArtifactRuleError("artifact-redaction-invalid")
    if type(required_for_pass) is not bool:
        raise ArtifactRuleError("artifact-required-invalid")
    artifact = {
        "status": "draft",
        "format": format_text,
        "title": title_text,
        "path": target,
        "exports": [],
        "publish_events": [],
        "redaction_status": redaction_status,
        "required_for_pass": required_for_pass,
        "blocks": [],
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    document["artifact"] = artifact
    document["artifact_applicability"] = "producing"
    document["updated_at"] = timestamp
    _invalidate_lint(document)
    if effect is not None:
        _bind_identity(document, artifact, effect)
    return document


def append_artifact_block_document(
    value: object,
    *,
    at: object,
    section: object,
    content: object,
    source: object,
    label: object,
) -> dict:
    document = _document(value)
    artifact = _artifact(document)
    timestamp = _timestamp(at)
    if section not in ARTIFACT_SECTIONS:
        raise ArtifactRuleError("artifact-section-invalid")
    if not isinstance(content, str):
        raise ArtifactRuleError("artifact-content-invalid")
    block = {"section": section, "content": content.rstrip(), "timestamp": timestamp}
    if source:
        block["source"] = _text(source, "artifact-source-invalid")
    elif source not in (None, ""):
        raise ArtifactRuleError("artifact-source-invalid")
    if label:
        block["label"] = _text(label, "artifact-label-invalid")
    elif label not in (None, ""):
        raise ArtifactRuleError("artifact-label-invalid")
    blocks = artifact.get("blocks")
    if not isinstance(blocks, list):
        raise ArtifactRuleError("artifact-blocks-invalid")
    blocks.append(block)
    artifact["status"] = "draft"
    artifact.pop("digest", None)
    artifact.pop("size", None)
    artifact["updated_at"] = timestamp
    document["artifact"] = artifact
    document["updated_at"] = timestamp
    _invalidate_lint(document)
    return document


def render_artifact_document(
    value: object,
    *,
    at: object,
    redaction_status: object,
    effect: Optional[ArtifactEffectClaim],
) -> dict:
    document = _document(value)
    artifact = _artifact(document)
    timestamp = _timestamp(at)
    if redaction_status is not None:
        if redaction_status not in ARTIFACT_REDACTION_STATUSES:
            raise ArtifactRuleError("artifact-redaction-invalid")
        artifact["redaction_status"] = redaction_status
    artifact["status"] = "rendered"
    artifact["last_rendered_at"] = timestamp
    artifact["updated_at"] = timestamp
    document["artifact"] = artifact
    document["updated_at"] = timestamp
    if effect is not None:
        _bind_identity(document, artifact, effect)
    return document


def export_artifact_document(
    value: object,
    *,
    at: object,
    destination: object,
    redaction_status: object,
    artifact_effect: Optional[ArtifactEffectClaim],
    export_effect: Optional[ArtifactEffectClaim],
) -> dict:
    document = _document(value)
    artifact = _artifact(document)
    timestamp = _timestamp(at)
    target = relative_artifact_target(destination, "artifact-export-path-invalid")
    if redaction_status not in ARTIFACT_REDACTION_STATUSES - {"unchecked"}:
        raise ArtifactRuleError("artifact-export-redaction-invalid")
    artifact["redaction_status"] = redaction_status
    artifact["status"] = "exported"
    artifact["last_rendered_at"] = timestamp
    artifact["updated_at"] = timestamp
    document["artifact"] = artifact
    document["updated_at"] = timestamp
    if (artifact_effect is None) != (export_effect is None):
        raise ArtifactRuleError("artifact-effect-claim-invalid")
    if artifact_effect is not None and export_effect is not None:
        _bind_identity(document, artifact, artifact_effect)
        export_claim = validate_artifact_effect_claim(
            export_effect, kind="artifact-export", target=target
        )
        if (
            export_claim.digest != artifact_effect.digest
            or export_claim.size != artifact_effect.size
        ):
            raise ArtifactRuleError("artifact-export-content-mismatch")
    exports = artifact.get("exports")
    if not isinstance(exports, list):
        raise ArtifactRuleError("artifact-exports-invalid")
    exports.append(
        {
            "path": target,
            "timestamp": timestamp,
            "redaction_status": redaction_status,
        }
    )
    document["artifact"] = artifact
    return document


def record_artifact_publication_document(
    value: object,
    *,
    at: object,
    provider: object,
    destination: object,
    approval_text: object,
    confirmed: object,
    effect: Optional[ArtifactEffectClaim],
) -> dict:
    document = _document(value)
    artifact = _artifact(document)
    timestamp = _timestamp(at)
    provider_text = _text(provider, "artifact-provider-invalid")
    approval = _text(approval_text, "artifact-approval-invalid")
    if confirmed is not True:
        raise ArtifactRuleError("artifact-confirmation-required")
    if artifact.get("redaction_status") == "unchecked":
        raise ArtifactRuleError("artifact-publish-redaction-invalid")
    has_destination = isinstance(destination, str) and bool(destination)
    if destination not in (None, "") and not has_destination:
        raise ArtifactRuleError("artifact-destination-invalid")
    event = {
        "provider": provider_text,
        "timestamp": timestamp,
        "approval_text": approval,
        "status": "published" if has_destination else "publish-prepared",
    }
    if has_destination:
        event["destination"] = _text(destination, "artifact-destination-invalid")
    events = artifact.get("publish_events")
    if not isinstance(events, list):
        raise ArtifactRuleError("artifact-publish-events-invalid")
    events.append(event)
    artifact["status"] = event["status"]
    artifact["updated_at"] = timestamp
    document["artifact"] = artifact
    document["updated_at"] = timestamp
    if effect is not None:
        _bind_identity(document, artifact, effect)
        event["artifact_path"] = effect.target
    document["artifact"] = artifact
    return document
