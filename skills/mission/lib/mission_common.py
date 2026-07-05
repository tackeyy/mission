"""Shared mission state helpers used by state and audit tools."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

PREPARATION_ONLY_MARKERS = (
    "Oracle Browser Review Prepared",
    "Browser Review Prepared",
    "Paste the browser oracle review here",
    "To capture the oracle review as command-provider output",
    "Prompt file:",
    "Result file:",
    "Packet file:",
    "Review URL:",
)

SPECIALIST_SELECTION_CHECKPOINT_REQUIRED_AT = datetime(2026, 6, 20, 10, 6, 47, tzinfo=timezone.utc)


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def classify_state(state: dict[str, Any]) -> str:
    if state.get("passes") is True:
        return "pass"
    if state.get("halt_reason"):
        return "halt"
    if not state.get("loop_active"):
        return "abandoned"
    return "incomplete"


def duration_sec(state: dict[str, Any]) -> float | None:
    started = parse_iso_datetime(state.get("started_at"))
    updated = parse_iso_datetime(state.get("updated_at"))
    if not started or not updated:
        return None
    try:
        seconds = (updated - started).total_seconds()
    except TypeError:
        return None
    return seconds if seconds >= 0 else None
