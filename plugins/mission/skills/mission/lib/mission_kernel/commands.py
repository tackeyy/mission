"""Closed K2 command subset whose authority is present in MissionState."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union

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
