"""Typed application preparations for progress, context, and verification evidence."""

from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
from pathlib import Path

from mission_kernel.commands import (
    ClearProgress,
    Command,
    ContextManifestEffectClaim,
    GenerateContextManifest,
    ProgressEffectClaim,
    RecordVerification,
    UpdateProgress,
    VerificationCheck,
)
from mission_kernel.evidence import (
    EvidenceRuleError,
    project_context_manifest,
    project_progress_update,
    project_verification_entry,
)
from .artifact import EvidenceEffect, EvidenceFailure, make_evidence_effect


@dataclass(frozen=True)
class PreparedEvidenceOperation:
    """Typed evidence command plus inert bytes and CLI response payload."""

    command: Command
    effects: tuple[EvidenceEffect, ...]
    result: dict


@dataclass(frozen=True)
class ProgressUpdateRequest:
    now: object
    total: object
    completed: object
    batch_size: object
    last_unit: object
    artifact_path: object
    iteration: object
    evidence_path: object


@dataclass(frozen=True)
class ProgressClearRequest:
    now: object


@dataclass(frozen=True)
class ContextManifestRequest:
    now: object
    iteration: object
    publication_path: object


@dataclass(frozen=True)
class VerificationRecordRequest:
    now: object
    iteration: object
    checks: object


@dataclass(frozen=True)
class PreparedVerificationRecord:
    """Validated checks plus the caller-stable retry identity for one record."""

    checks: object
    operation_id: str
    operation_command: object


def prepare_verification_record_operation(
    payload: object,
    *,
    iteration: object,
    state_path: object,
    compatibility_arguments,
    canonical_operation,
) -> PreparedVerificationRecord:
    """Bind one verification payload to a caller-stable operation identity.

    Without a stable operation id the repository mints a fresh one per
    invocation, so a crash retry appends a second record instead of replaying
    the first (#685).
    """
    checks = normalize_verification_checks(payload)
    path = Path(state_path)
    try:
        state_bytes = path.read_bytes()
    except OSError as error:
        raise EvidenceFailure("verification-state-read-failed") from error
    target_digest = "sha256:" + hashlib.sha256(state_bytes).hexdigest()
    caller_operation_id, arguments = compatibility_arguments(
        {"iteration": iteration},
        target_digest=target_digest,
        require_caller=False,
    )
    operation_id, operation_command = canonical_operation(
        path.stem,
        "verification-record",
        arguments,
        caller_operation_id=caller_operation_id,
    )
    return PreparedVerificationRecord(checks, operation_id, operation_command)


def execute_evidence_operation(repository: object, prepare) -> dict:
    execute = getattr(repository, "execute_evidence_transition_effects", None)
    if not callable(execute):
        raise EvidenceFailure("evidence-repository-invalid")
    prepared, execution = execute(prepare)
    decision = getattr(execution, "decision", None)
    if decision is None:
        if not getattr(execution, "replayed", False):
            raise EvidenceFailure("evidence-transition-rejected")
    elif decision.accepted is not True:
        rejection = getattr(decision, "rejection", None)
        raise EvidenceFailure(
            getattr(rejection, "code", "evidence-transition-rejected")
        )
    projection = execution.projection
    command = prepared.command
    payload = copy.deepcopy(prepared.result)
    if isinstance(command, UpdateProgress):
        if payload.get("progress") != projection.get("progress"):
            raise EvidenceFailure("progress-projection-mismatch")
    elif isinstance(command, ClearProgress):
        if "progress" in projection:
            raise EvidenceFailure("progress-projection-mismatch")
    elif isinstance(command, GenerateContextManifest):
        record = (projection.get("context_manifests") or {}).get(
            str(command.iteration)
        )
        if not isinstance(record, dict) or any(
            record.get(key) != payload.get(value)
            for key, value in (("path", "path"), ("digest", "digest"))
        ):
            raise EvidenceFailure("context-projection-mismatch")
    elif isinstance(command, RecordVerification):
        history = projection.get("verification_history")
        if not isinstance(history, list) or not history or history[-1] != payload.get(
            "verification"
        ):
            raise EvidenceFailure("verification-projection-mismatch")
    return payload


def evidence_publication_paths(
    prepared: object, effects: tuple[EvidenceEffect, ...]
) -> tuple[str, ...]:
    """Return publication destinations solely from immutable command claims."""
    command = getattr(prepared, "command", None)
    if isinstance(command, GenerateContextManifest):
        if len(effects) != 1 or command.effect.target != effects[0].target:
            raise EvidenceFailure("context-effect-claim-invalid")
        return (command.effect.publication_path,)
    return tuple(effect.target for effect in effects)


def verify_published_evidence_effects(
    cwd: object,
    effects: tuple[EvidenceEffect, ...],
    publication_paths: tuple[str, ...],
    published: object,
    capture,
    relative_path,
    verify_object,
) -> None:
    items = tuple(published) if isinstance(published, (tuple, list)) else ()
    if len(items) != len(effects) or len(publication_paths) != len(effects):
        raise ValueError("published evidence effect count changed")
    for effect, expected_path, item in zip(effects, publication_paths, items):
        verify_object(item)
        actual_target = relative_path(cwd, str(item.path))
        expected_target = relative_path(cwd, expected_path)
        identity, payload = capture(cwd, actual_target, "evidence-effect-verifier")
        if (
            actual_target != expected_target
            or identity.get("digest") != effect.digest.removeprefix("sha256:")
            or identity.get("size") != effect.size
            or payload != effect.content
        ):
            raise ValueError("published evidence effect identity changed")


def run_progress_update(
    request: ProgressUpdateRequest, repository: object
) -> dict:
    def prepare(state):
        iteration = (
            request.iteration
            if request.iteration is not None
            else state.get("iteration", 0)
        )
        evidence_path = request.evidence_path
        if callable(evidence_path):
            evidence_path = evidence_path(state, iteration)
        return prepare_progress_update(
            state,
            now=request.now,
            total=request.total,
            completed=request.completed,
            batch_size=request.batch_size,
            last_unit=request.last_unit,
            artifact_path=request.artifact_path,
            iteration=iteration,
            evidence_path=evidence_path,
        )

    return execute_evidence_operation(
        repository,
        prepare,
    )


def run_progress_clear(request: ProgressClearRequest, repository: object) -> dict:
    return execute_evidence_operation(
        repository,
        lambda state: prepare_progress_clear(state, now=request.now),
    )


def run_context_manifest(
    request: ContextManifestRequest, repository: object
) -> dict:
    return execute_evidence_operation(
        repository,
        lambda state: prepare_context_manifest(
            state,
            now=request.now,
            iteration=(
                request.iteration
                if request.iteration is not None
                else state.get("iteration", 1)
            ),
            publication_path=request.publication_path,
        ),
    )


def run_verification_record(
    request: VerificationRecordRequest, repository: object
) -> dict:
    return execute_evidence_operation(
        repository,
        lambda state: prepare_verification_record(
            state,
            now=request.now,
            iteration=request.iteration,
            checks=request.checks,
        ),
    )


def _translate(call):
    try:
        return call()
    except EvidenceRuleError as exc:
        raise EvidenceFailure(exc.code) from exc


def prepare_progress_update(
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
) -> PreparedEvidenceOperation:
    if not isinstance(state, dict):
        raise EvidenceFailure("state-invalid")
    provisional = UpdateProgress(
        now,
        total,
        completed,
        batch_size,
        last_unit,
        artifact_path,
        iteration,
        ProgressEffectClaim("progress", evidence_path, "sha256:" + "0" * 64, 0),
    )
    progress, content = _translate(
        lambda: project_progress_update(state, provisional)
    )
    effect = make_evidence_effect("progress", evidence_path, content)
    command = UpdateProgress(
        now,
        total,
        completed,
        batch_size,
        last_unit,
        artifact_path,
        iteration,
        ProgressEffectClaim(effect.kind, effect.target, effect.digest, effect.size),
    )
    verified, verified_content = _translate(
        lambda: project_progress_update(state, command)
    )
    if verified != progress or verified_content != content:
        raise EvidenceFailure("progress-projection-mismatch")
    return PreparedEvidenceOperation(
        command, (effect,), {"progress": copy.deepcopy(progress)}
    )


def prepare_progress_clear(
    state: object, *, now: object
) -> PreparedEvidenceOperation:
    if not isinstance(state, dict):
        raise EvidenceFailure("state-invalid")
    command = ClearProgress(now)
    return PreparedEvidenceOperation(command, (), {})


def prepare_context_manifest(
    state: object,
    *,
    now: object,
    iteration: object,
    publication_path: object,
) -> PreparedEvidenceOperation:
    if not isinstance(state, dict):
        raise EvidenceFailure("state-invalid")
    record, content, findings_count = _translate(
        lambda: project_context_manifest(
            state,
            iteration=iteration,
            publication_path=publication_path,
            at=now,
        )
    )
    target = Path(publication_path).name
    effect = make_evidence_effect("context-manifest", target, content)
    claim = ContextManifestEffectClaim(
        effect.kind,
        effect.target,
        publication_path,
        effect.digest,
        effect.size,
    )
    command = GenerateContextManifest(now, iteration, claim)
    if record["digest"] != claim.digest:
        raise EvidenceFailure("context-projection-mismatch")
    return PreparedEvidenceOperation(
        command,
        (effect,),
        {
            "path": publication_path,
            "digest": claim.digest,
            "findings_count": findings_count,
        },
    )


def normalize_verification_checks(payload: object) -> tuple[VerificationCheck, ...]:
    if not isinstance(payload, dict):
        raise EvidenceFailure("verification-payload-invalid")
    checks = payload.get("checks")
    if checks is None:
        checks = []
    if not isinstance(checks, list):
        raise EvidenceFailure("verification-checks-invalid")
    normalized = []
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise EvidenceFailure("verification-check-invalid")
        if not isinstance(check.get("ok"), bool):
            raise EvidenceFailure("verification-check-ok-invalid")
        name = check.get("name")
        detail = check.get("detail")
        normalized.append(
            VerificationCheck(
                name if isinstance(name, str) and name else f"check-{index}",
                check["ok"],
                detail if isinstance(detail, str) else None,
            )
        )
    return tuple(normalized)


def validate_context_iteration_override(value: object) -> None:
    """Validate an explicit CLI override while allowing state-derived defaults."""
    if value is not None and (type(value) is not int or value < 1):
        raise EvidenceFailure("context-iteration-invalid")


def prepare_verification_record(
    state: object,
    *,
    now: object,
    iteration: object,
    checks: object,
) -> PreparedEvidenceOperation:
    if not isinstance(state, dict):
        raise EvidenceFailure("state-invalid")
    command = RecordVerification(now, iteration, checks)
    entry = _translate(lambda: project_verification_entry(command))
    return PreparedEvidenceOperation(
        command, (), {"verification": copy.deepcopy(entry)}
    )
