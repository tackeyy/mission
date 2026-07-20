"""Shared mission state helpers used by state and audit tools."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
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

# #190: halt_reason のカテゴリ enum。state 側 (mark-halt --category) と audit 側
# (halt_or_incomplete_bucket の構造化優先ロジック) で同一定義を共有する。
HALT_CATEGORIES = {
    "blocked-external",
    "awaiting-approval",
    "partial-done",
    "stagnation",
    "user-abort",
    "stale",
    "other",
}


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


def _project_identity(project_root: Any, source_path: str) -> str:
    if isinstance(project_root, str) and project_root.strip():
        try:
            return str(Path(project_root).expanduser().resolve(strict=False))
        except (OSError, RuntimeError):
            return str(Path(project_root))
    if source_path:
        source = Path(source_path)
        for parent in source.parents:
            if parent.name == ".mission-state":
                try:
                    return str(parent.parent.resolve(strict=False))
                except (OSError, RuntimeError):
                    return str(parent.parent)
    return ""


def state_identity(
    state: dict[str, Any], fallback_session: str = "", source_path: str = ""
) -> tuple[str, str, str]:
    """Identity shared by live/archive audit and stats deduplication."""
    return (
        _project_identity(state.get("project_root"), source_path),
        str(state.get("session_id") or fallback_session),
        str(state.get("mission_id") or ""),
    )


def state_dedupe_rank(state: dict[str, Any], source_path: str = "") -> tuple[int, float, int, str]:
    """Prefer terminal success, then newest update, then live/path determinism."""
    classification = classify_state(state)
    status_rank = {"pass": 0, "halt": 1, "incomplete": 2}.get(classification, 3)
    updated = parse_iso_datetime(state.get("updated_at"))
    if updated and updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    updated_rank = updated.timestamp() if updated else 0.0
    if "/archive/worktree-" in source_path:
        path_rank = 1
    elif "/sessions/" in source_path:
        path_rank = 0
    else:
        path_rank = 2
    return (status_rank, -updated_rank, path_rank, source_path)
