"""Typed application preparations for progress, context, and verification evidence."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
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
from mission_kernel.json_codec import encode_json_value, freeze_json_value
from .artifact import EvidenceEffect, EvidenceFailure, make_evidence_effect
from .ports import LegacyCommandExecutionResult


@dataclass(frozen=True)
class PreparedEvidenceOperation:
    """Typed evidence command plus inert bytes and CLI response payload."""

    command: Command
    effects: tuple[EvidenceEffect, ...]
    result: dict
    volatile_fields: tuple[str, ...] = ()


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
    kind: object = "execution"


@dataclass(frozen=True)
class PreparedVerificationRecord:
    """Validated checks plus the caller-stable retry identity for one record."""

    checks: object
    operation_id: str | None
    operation_command: object | None
    kind: str = "execution"


def _verification_checks_digest(checks: tuple[VerificationCheck, ...]) -> str:
    normalized = [
        {"detail": check.detail, "name": check.name, "ok": check.ok}
        for check in checks
    ]
    content = encode_json_value(freeze_json_value(normalized))
    return "sha256:" + hashlib.sha256(content).hexdigest()


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
    kind, checks = normalize_verification_payload(payload)
    path = Path(state_path)
    caller_operation_id, arguments = compatibility_arguments(
        {
            "checks_digest": _verification_checks_digest(checks),
            "iteration": iteration,
            "kind": kind,
        },
        target_digest="",
        require_caller=False,
    )
    if caller_operation_id is None:
        # Without a caller-supplied id the repository keeps minting a fresh
        # identity per invocation.  Deriving one from the pre-state digest
        # would make two concurrent runs share an identity and replay each
        # other, so replay stays opt-in through MISSION_OPERATION_ID.
        return PreparedVerificationRecord(checks, None, None, kind)
    operation_id, operation_command = canonical_operation(
        path.stem,
        "verification-record",
        arguments,
        caller_operation_id=caller_operation_id,
    )
    return PreparedVerificationRecord(checks, operation_id, operation_command, kind)


def execute_evidence_operation(repository: object, prepare) -> dict:
    execute = getattr(repository, "execute_evidence_transition_effects", None)
    if not callable(execute):
        raise EvidenceFailure("evidence-repository-invalid")
    prepared, execution = execute(prepare)
    if not isinstance(execution, LegacyCommandExecutionResult):
        raise EvidenceFailure("evidence-execution-result-invalid")
    decision = execution.decision
    if decision is None:
        if not execution.replayed:
            raise EvidenceFailure("evidence-transition-rejected")
    elif decision.accepted is not True:
        rejection = getattr(decision, "rejection", None)
        raise EvidenceFailure(
            getattr(rejection, "code", "evidence-transition-rejected")
        )
    projection = execution.projection
    command = prepared.command
    payload = copy.deepcopy(prepared.result)
    replayed = execution.replayed
    if isinstance(command, UpdateProgress):
        if not _record_matches(
            projection.get("progress"), payload.get("progress"), replayed, prepared,
            projection,
        ):
            raise EvidenceFailure("progress-projection-mismatch")
        if replayed:
            payload["progress"] = copy.deepcopy(projection.get("progress"))
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
        # path and digest are content-addressed and this record has no
        # clock-derived field, so the projected values need no store authority.
    elif isinstance(command, RecordVerification):
        history = projection.get("verification_history")
        if not isinstance(history, list) or not history:
            raise EvidenceFailure("verification-projection-mismatch")
        expected = payload.get("verification")
        if replayed:
            matches = [
                record
                for record in history
                if _record_matches(record, expected, True, prepared, projection)
            ]
            if len(matches) != 1:
                raise EvidenceFailure("verification-projection-mismatch")
            record = matches[0]
        else:
            record = history[-1]
        if not _record_matches(record, expected, replayed, prepared, projection):
            raise EvidenceFailure("verification-projection-mismatch")
        if replayed:
            payload["verification"] = copy.deepcopy(record)
    return payload


def _record_matches(
    stored: object,
    expected: object,
    replayed: bool,
    prepared: PreparedEvidenceOperation,
    state: object,
) -> bool:
    """Compare a stored record with the one this invocation would have written.

    A replay re-derives clock-sourced fields from the current time, so those
    fields are compared only on the first execution; the stored record stays
    authoritative for them.  Every other field must match either way, so a
    replay that would have produced different content is still rejected.
    """
    if not replayed:
        return stored == expected
    if not isinstance(stored, dict) or not isinstance(expected, dict):
        return False
    volatile = prepared.volatile_fields
    try:
        validated = _project_stored_replay_record(prepared, state, stored)
    except EvidenceRuleError:
        return False
    if validated is not None and validated != stored:
        return False
    return {key: value for key, value in stored.items() if key not in volatile} == {
        key: value for key, value in expected.items() if key not in volatile
    }


def _project_stored_replay_record(
    prepared: PreparedEvidenceOperation, state: object, stored: dict
) -> dict | None:
    """Revalidate store-authoritative fields through their kernel projector."""
    volatile = prepared.volatile_fields
    if not volatile:
        return None
    if len(volatile) != 1 or volatile[0] not in stored:
        raise EvidenceRuleError("evidence-volatile-field-invalid")
    command = replace(prepared.command, at=stored[volatile[0]])
    if isinstance(command, UpdateProgress):
        projected, _content = project_progress_update(state, command)
        return projected
    if isinstance(command, RecordVerification):
        return project_verification_entry(command)
    raise EvidenceRuleError("evidence-volatile-command-invalid")


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
            kind=request.kind,
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
        command,
        (effect,),
        {"progress": copy.deepcopy(progress)},
        volatile_fields=("updated_at",),
    )


def prepare_progress_clear(
    state: object, *, now: object
) -> PreparedEvidenceOperation:
    if not isinstance(state, dict):
        raise EvidenceFailure("state-invalid")
    command = ClearProgress(now)
    return PreparedEvidenceOperation(command, (), {}, volatile_fields=())


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
        volatile_fields=(),
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


def normalize_verification_payload(
    payload: object,
) -> tuple[str, tuple[VerificationCheck, ...]]:
    if not isinstance(payload, dict):
        raise EvidenceFailure("verification-payload-invalid")
    kind = payload.get("kind", "execution")
    if kind not in ("execution", "implementation-read"):
        raise EvidenceFailure("verification-kind-invalid")
    return kind, normalize_verification_checks(payload)


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
    kind: object = "execution",
) -> PreparedEvidenceOperation:
    if not isinstance(state, dict):
        raise EvidenceFailure("state-invalid")
    command = RecordVerification(now, iteration, checks, kind)
    entry = _translate(lambda: project_verification_entry(command))
    return PreparedEvidenceOperation(
        command,
        (),
        {"verification": copy.deepcopy(entry)},
        volatile_fields=("recorded_at",),
    )
