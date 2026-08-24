"""Application use case for specialist selection checkpoint decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Optional, Set

from mission_kernel.commands import DeclineSpecialistSelection


class SpecialistSelectionFailure(ValueError):
    """A specialist selection transition was rejected before publication."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProviderConfirmationResolution:
    first_use: bool
    decision: Optional[dict]


def resolve_provider_confirmation(
    candidate: Mapping[str, object],
    provider_id: str,
    consented: Set[str],
    first_use: Set[str],
) -> ProviderConfirmationResolution:
    """Resolve legacy and risk first-use flags against explicit provider consent."""
    risk = candidate.get("risk")
    risk_confirmation = (
        bool(risk.get("first_use_confirmation"))
        if isinstance(risk, Mapping)
        else False
    )
    explicit_confirmation = bool(candidate.get("confirm"))
    confirmation_required = risk_confirmation or explicit_confirmation
    caller_first_use = candidate.get("skill") in first_use or provider_id in first_use
    if caller_first_use or (confirmation_required and provider_id not in consented):
        return ProviderConfirmationResolution(
            first_use=True,
            decision={
                "policy": "first-use",
                "action": "ask-user",
                "reason": (
                    "specialist requires first-use confirmation: "
                    + str(candidate.get("skill"))
                ),
                "prompted_user": True,
            },
        )
    if explicit_confirmation and provider_id in consented:
        return ProviderConfirmationResolution(
            first_use=False,
            decision={
                "policy": "consented-confirmation",
                "action": "continue-core",
                "reason": (
                    "specialist confirmation was previously consented: "
                    + str(candidate.get("skill"))
                ),
                "prompted_user": False,
                "confirmation_resolved": True,
            },
        )
    return ProviderConfirmationResolution(first_use=False, decision=None)


@dataclass(frozen=True)
class SpecialistDeclineRequest:
    selection_id: str
    reason: str


@dataclass(frozen=True)
class SpecialistDeclineServices:
    cwd: Callable[[], object]
    resolve_state_file: Callable[[object], object]
    read_bytes: Callable[[object], bytes]
    inspect_repository_bytes: Callable[..., object]
    sha256: Callable[[bytes], object]
    compatibility_operation_arguments: Callable[..., tuple]
    canonical_compatibility_operation: Callable[..., tuple]
    repository_factory: Callable[..., object]
    repository_format_v5: object
    clock: Callable[[], str]


def decline_specialist_selection(
    request: SpecialistDeclineRequest,
    services: SpecialistDeclineServices,
) -> dict:
    """Execute one identity-bound typed decline through the repository port."""
    try:
        cwd = services.cwd()
        state_file = services.resolve_state_file(cwd)
        session_id = state_file.stem
        target_bytes = services.read_bytes(state_file)
        inspected = services.inspect_repository_bytes(
            target_bytes, expected_session_id=session_id
        )
        target_digest = "sha256:" + services.sha256(target_bytes).hexdigest()
        caller_operation_id, operation_arguments = (
            services.compatibility_operation_arguments(
                {
                    "selection_id": request.selection_id,
                    "reason": request.reason,
                },
                target_digest=target_digest,
                require_caller=inspected.format is services.repository_format_v5,
            )
        )
        operation_id, operation_command = services.canonical_compatibility_operation(
            session_id,
            "specialists-decline",
            operation_arguments,
            caller_operation_id=caller_operation_id,
        )
        repository = services.repository_factory(
            cwd,
            state_file,
            stamp=True,
            strict_read=True,
            session_id=session_id,
            operation_id=operation_id,
            operation_command=operation_command,
            operation_command_type="specialists-decline",
        )
        command = DeclineSpecialistSelection(
            selection_id=request.selection_id,
            reason=request.reason,
            at=services.clock(),
        )
        with repository.transaction():
            repository.load()
            execution = repository.execute(command)
    except (OSError, TypeError, ValueError) as error:
        raise SpecialistSelectionFailure(str(error)) from error

    decision = execution.decision
    if not decision.accepted:
        rejection = decision.rejection
        raise SpecialistSelectionFailure(
            rejection.code if rejection is not None else "specialist-decline-rejected"
        )
    checkpoint = execution.projection.get("specialists_decision")
    if not isinstance(checkpoint, dict):
        raise SpecialistSelectionFailure("specialist-selection-checkpoint-invalid")
    return {"ok": True, "specialists_decision": checkpoint}
