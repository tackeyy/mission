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


Command = Union[AdvancePhase, MarkHalt, Reactivate, ResumeStale]
