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


Command = Union[AdvancePhase, MarkHalt, MarkPass, Reactivate, ResumeStale]


_COMMAND_TYPES = {
    AdvancePhase: "advance-phase",
    MarkHalt: "mark-halt",
    MarkPass: "mark-pass",
    Reactivate: "reactivate",
    ResumeStale: "resume-stale",
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
