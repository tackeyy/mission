"""Closed K2 command subset whose authority is present in MissionState."""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Optional, Union

from .json_codec import decode_json_object, encode_json_object, freeze_json_value
from .model import FrozenJsonObject
from .model import HaltCategory, Phase, PreparedHandoff


@dataclass(frozen=True)
class AdvancePhase:
    target: Phase
    prepared_handoff: Optional[PreparedHandoff] = None


@dataclass(frozen=True)
class MarkHalt:
    category: HaltCategory
    reason: str
    superseded: bool = False


@dataclass(frozen=True)
class Reactivate:
    expected_category: HaltCategory
    reason: str
    approved_by_user: bool
    target: Phase


@dataclass(frozen=True)
class ResumeStale:
    target: Phase


@dataclass(frozen=True)
class MarkPass:
    """Request the kernel's sole completion transition.

    Evidence adapters and application use cases validate external bytes before
    constructing this command.  The kernel still owns the final conjunction of
    score, findings, artifact, specialist, and force-approval facts.
    """

    force: bool = False
    force_approval_verified: bool = False
    artifact_gate_satisfied: bool = False
    specialist_gate_satisfied: bool = False


@dataclass(frozen=True)
class SetExtensionFields:
    """Request generic extension-property writes outside dedicated authority.

    The closed field classification below is the kernel's authority: keys owned
    by a dedicated lifecycle, lease, progress, scoring, or evidence command are
    rejected by the reducer, so the generic command can never bypass the
    command that owns a state transition or its audit trail (#617 批1-a).
    """

    fields: FrozenJsonObject


# Fields whose value is fixed at genesis or owned by the pass gate; a generic
# write would forge identity or completion evidence.  ``init`` recomputes
# mission identity, and pass-gate facts only move through their own commands.
GENERIC_SET_FROZEN_FIELDS = frozenset(
    {
        "mission",
        "mission_id",
        "passes",
        "passes_forced",
        "force_reason",
        "score_history",
        "failure_ledger",
        "threshold",
        "schema_version",
        "session_role",
        "terminal_outcome",
        "artifact_applicability",
        "artifact",
        "artifact_path",
        "artifact_lint",
        "artifact_lint_identity",
        "artifact_lint_status",
        "project_root",
        "started_at",
        "created_at_session",
        "reactivation_history",
    }
)


# Fields whose authority belongs to a dedicated lifecycle, lease, progress, or
# scoring command.  Generic ``set`` remains available for extension properties
# such as complexity and bounded orchestration observations, but cannot bypass
# the command that owns a state transition or its audit trail.
GENERIC_SET_DEDICATED_FIELDS = frozenset(
    {
        "phase",
        "phase_started_at",
        "phase_durations_sec",
        "activity_current",
        "activity_segments",
        "activity_rollup",
        "activity_last_event_at",
        "activity_last_event_phase",
        "activity_anomaly_counts",
        "activity_unobserved_gap_sec",
        "activity_unobserved_gap_reasons_sec",
        "pid",
        "pid_source",
        "loop_active",
        "halt_reason",
        "halt_category",
        "resume_target_phase",
        "owner_session_id",
        "lease_id",
        "fencing_epoch",
        "lease_expires_at",
        "lease_history",
        "last_activity_at",
        "updated_at",
    }
)


Command = Union[
    AdvancePhase, MarkHalt, MarkPass, Reactivate, ResumeStale, SetExtensionFields
]


_COMMAND_TYPES = {
    AdvancePhase: "advance-phase",
    MarkHalt: "mark-halt",
    MarkPass: "mark-pass",
    Reactivate: "reactivate",
    ResumeStale: "resume-stale",
    SetExtensionFields: "set-extension-fields",
}


def _command_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, FrozenJsonObject):
        return value.thaw()
    if isinstance(value, tuple):
        return [_command_value(item) for item in value]
    if is_dataclass(value):
        return {
            field.name: _command_value(getattr(value, field.name))
            for field in fields(value)
            if not field.name.startswith("_")
        }
    raise TypeError("kernel-command-value-invalid")


def kernel_command_type(command: object) -> str:
    for command_class, name in _COMMAND_TYPES.items():
        if type(command) is command_class:
            return name
    raise TypeError("kernel-command-type-invalid")


def encode_kernel_command(command: object) -> FrozenJsonObject:
    """Return the one canonical immutable document for a typed command."""
    payload = _command_value(command)
    if not isinstance(payload, dict):
        raise TypeError("kernel-command-value-invalid")
    encoded = freeze_json_value(
        {
            "schema": "mission-kernel-command/1",
            "type": kernel_command_type(command),
            "value": payload,
        }
    )
    if not isinstance(encoded, FrozenJsonObject):
        raise TypeError("kernel-command-value-invalid")
    return decode_json_object(encode_json_object(encoded))
