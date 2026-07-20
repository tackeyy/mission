"""Shared mission state helpers used by state and audit tools."""

from __future__ import annotations

import math
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


PASS_RATE_HEALTH_CLASSES = (
    "pass",
    "halt",
    "abandoned",
    "active",
    "active-no-score",
    "stale",
)


def state_age_since_update_sec(
    state: dict[str, Any], *, now: datetime | None = None
) -> float | None:
    """Return non-negative age from the best progress timestamp, normalized to UTC."""
    updated = parse_iso_datetime(
        state.get("heartbeat_at") or state.get("last_progress_at") or state.get("updated_at")
    )
    if updated is None:
        return None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    seconds = (base.astimezone(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds()
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def has_scoring_checkpoint(state: dict[str, Any]) -> bool:
    """Return true only when score history contains a finite numeric composite."""
    history = state.get("score_history")
    if not isinstance(history, list):
        return False
    for entry in history:
        if not isinstance(entry, dict):
            continue
        composite = entry.get("composite")
        if (
            isinstance(composite, (int, float))
            and not isinstance(composite, bool)
            and math.isfinite(float(composite))
        ):
            return True
    return False


def classify_pass_rate_health(
    state: dict[str, Any],
    *,
    now: datetime | None = None,
    stale_after_sec: int,
) -> str:
    """Classify one session into an exclusive pass-rate/health population.

    Fresh active sessions are not completed. A stale active session is actionable
    completed health debt so it cannot disappear from the quality denominator.
    Missing, malformed, or future progress timestamps fail closed as stale.
    """
    terminal = classify_state(state)
    if terminal != "incomplete":
        return terminal
    age = state_age_since_update_sec(state, now=now)
    if age is None or age >= max(0, stale_after_sec):
        return "stale"
    return "active" if has_scoring_checkpoint(state) else "active-no-score"


def summarize_pass_rate_population(
    states: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    stale_after_sec: int,
) -> dict[str, Any]:
    """Return shared raw/completed rates and exclusive session health counts."""
    health_classes = [
        classify_pass_rate_health(state, now=now, stale_after_sec=stale_after_sec)
        for state in states
    ]
    counts = {name: 0 for name in PASS_RATE_HEALTH_CLASSES}
    for classification in health_classes:
        counts[classification] += 1
    raw_denominator = len(states)
    completed_denominator = sum(
        counts[name] for name in ("pass", "halt", "abandoned", "stale")
    )
    pass_count = counts["pass"]
    return {
        "health_classes": health_classes,
        "raw_pass_rate_numerator": pass_count,
        "raw_pass_rate_denominator": raw_denominator,
        "raw_pass_rate": pass_count / raw_denominator if raw_denominator else None,
        "completed_pass_rate_numerator": pass_count,
        "completed_pass_rate_denominator": completed_denominator,
        "completed_pass_rate": pass_count / completed_denominator if completed_denominator else None,
        "active_count": counts["active"],
        "active_no_score_count": counts["active-no-score"],
        "stale_count": counts["stale"],
        "halt_count": counts["halt"],
        "abandoned_count": counts["abandoned"],
        "incomplete_count": counts["active"] + counts["active-no-score"] + counts["stale"],
    }


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
