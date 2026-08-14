"""Shared mission state helpers used by state and audit tools."""

from __future__ import annotations

import math
import re
import secrets
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
    "evidence-submitted",  # #311: Checker 系 role の正規出口 (証拠提出完了)
    "routed-goal",  # #325: adaptive routing による goal 契約直行 (pass-rate 対象外)
    "stagnation",
    "user-abort",
    "stale",
    "other",
}

# #311: session の役割。checker 系は iter=0 で証拠提出して終わるのが設計どおりのため、
# pass-rate は implementer 限定の指標を別途出す (既存指標は全 role 対象のまま不変)
SESSION_ROLES = ("implementer", "checker", "planning", "analyze", "release")
TERMINAL_OUTCOMES = (
    "completed_pass",
    "completed_evidence",
    "blocked_external",
    "awaiting_approval",
    "stale_superseded",
    "failed",
    "incomplete",
    "user_aborted",
    "routed_elsewhere",
)
EVIDENCE_COMPLETION_ROLES = {"checker", "planning", "analyze"}
_CORRELATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def opaque_token(value: Any) -> str:
    """Validate one portable, locator-free token used by correlation and groups."""
    if not isinstance(value, str) or not _CORRELATION_ID.fullmatch(value):
        raise ValueError("opaque token must be a non-empty portable token")
    return value


def correlation_id(value: Any | None = None) -> str:
    """Return one portable opaque correlation ID, locally issued when absent."""
    if value is None:
        return "mission-local-" + secrets.token_hex(16)
    try:
        return opaque_token(value)
    except ValueError:
        raise ValueError("correlation ID must be a non-empty opaque token")


def _derive_control_terminal_outcome(state: dict[str, Any]) -> str | None:
    if state.get("passes") is True:
        if state.get("loop_active") is False and not state.get("halt_reason"):
            return "completed_pass"
        return "failed"
    if state.get("loop_active") is True:
        return "failed" if state.get("halt_reason") else None
    if not state.get("halt_reason"):
        return "incomplete"
    category = state.get("halt_category")
    reason = str(state.get("halt_reason") or "").strip().lower()
    resolution_status = str(state.get("resolution_status") or "").strip().lower()
    if "halt_category" in state and not isinstance(category, str):
        return "failed"
    if (
        resolution_status == "superseded"
        or reason in {
            "superseded by a replacement run",
            "superseded by replacement run",
        }
    ):
        return "stale_superseded"
    if category not in HALT_CATEGORIES and reason.startswith(("orphan:", "stale:")):
        return "stale_superseded"
    if category == "evidence-submitted":
        return (
            "completed_evidence"
            if session_role(state) in EVIDENCE_COMPLETION_ROLES
            else "incomplete"
        )
    return {
        "partial-done": "incomplete",
        "blocked-external": "blocked_external",
        "awaiting-approval": "awaiting_approval",
        "stale": "stale_superseded",
        "stagnation": "failed",
        "other": "failed",
        "user-abort": "user_aborted",
        "routed-goal": "routed_elsewhere",
    }.get(category, "failed")


def derive_terminal_outcome(state: dict[str, Any]) -> str | None:
    """Return a terminal business outcome without rewriting legacy state.

    Active states return ``None``. Persisted explicit outcomes are accepted only
    when they match the control fields; malformed or contradictory input fails
    closed as ``failed``.
    """
    derived = _derive_control_terminal_outcome(state)
    if "terminal_outcome" not in state:
        return derived
    explicit = state.get("terminal_outcome")
    if explicit not in TERMINAL_OUTCOMES or explicit != derived:
        return "failed"
    return explicit


def session_role(state: dict) -> str:
    """#311: state の役割。旧 state (フィールドなし) は implementer 扱い (後方互換)."""
    role = state.get("session_role")
    return role if role in SESSION_ROLES else "implementer"


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None


def classify_state(state: dict[str, Any]) -> str:
    if "terminal_outcome" in state:
        return "pass" if derive_terminal_outcome(state) == "completed_pass" else "halt"
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


def state_age_details(
    state: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """Return the selected progress timestamp source and the derived age."""
    source_field = None
    for field in ("heartbeat_at", "last_progress_at", "last_activity_at", "updated_at"):
        value = state.get(field)
        if not value:
            continue
        if not isinstance(value, str):
            # Preserve the legacy `or` chain: once the first present timestamp is malformed,
            # do not fall through to older fields and accidentally turn a broken heartbeat into
            # a stale auto-halt.
            return {"timestamp_field": None, "age_sec": None}
        updated = parse_iso_datetime(value)
        if updated is None:
            # Same legacy short-circuit for unparseable strings.
            return {"timestamp_field": None, "age_sec": None}
        source_field = field
        break
    if source_field is None:
        return {"timestamp_field": None, "age_sec": None}
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    seconds = (
        base.astimezone(timezone.utc) - updated.astimezone(timezone.utc)
    ).total_seconds()
    if not math.isfinite(seconds) or seconds < 0:
        return {"timestamp_field": source_field, "age_sec": None}
    return {"timestamp_field": source_field, "age_sec": seconds}


def state_age_since_update_sec(
    state: dict[str, Any], *, now: datetime | None = None
) -> float | None:
    """Return non-negative age from the best progress timestamp, normalized to UTC."""
    return state_age_details(state, now=now)["age_sec"]


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
    observation_now = now or datetime.now(timezone.utc)
    health_classes = [
        classify_pass_rate_health(
            state,
            now=observation_now,
            stale_after_sec=stale_after_sec,
        )
        for state in states
    ]
    counts = {name: 0 for name in PASS_RATE_HEALTH_CLASSES}
    for classification in health_classes:
        counts[classification] += 1
    terminal_outcomes = [derive_terminal_outcome(state) for state in states]
    terminal_outcome_counts = {name: 0 for name in TERMINAL_OUTCOMES}
    for outcome in terminal_outcomes:
        if outcome is not None:
            terminal_outcome_counts[outcome] += 1
    terminal_count = sum(terminal_outcome_counts.values())
    raw_denominator = len(states)
    # #325: routed-goal halt は「mission が仕事を辞退した」記録であり品質債務ではない。
    # completed 分母から除外し routed_count として別計上する。
    routed = terminal_outcome_counts["routed_elsewhere"]
    completed_denominator = sum(
        counts[name] for name in ("pass", "halt", "abandoned", "stale")
    ) - routed
    pass_count = counts["pass"]
    # #311: role 別 additive 集計。既存フィールドは全 role 対象の歴史的意味を維持する
    role_counts: dict[str, int] = {name: 0 for name in SESSION_ROLES}
    impl_pass = 0
    impl_completed = 0
    evidence_completed = 0
    evidence_comparable = 0
    for state, outcome in zip(states, terminal_outcomes):
        role = session_role(state)
        role_counts[role] += 1
        if role == "implementer" and outcome in {"completed_pass", "failed", "incomplete"}:
            impl_completed += 1
            if outcome == "completed_pass":
                impl_pass += 1
        if role in EVIDENCE_COMPLETION_ROLES and outcome in {
            "completed_evidence", "failed", "incomplete"
        }:
            evidence_comparable += 1
            if outcome == "completed_evidence":
                evidence_completed += 1
    return {
        "health_classes": health_classes,
        "terminal_outcomes": terminal_outcomes,
        "terminal_outcome_counts": terminal_outcome_counts,
        "terminal_count": terminal_count,
        "non_terminal_count": len(states) - terminal_count,
        "routed_count": routed,
        "role_counts": role_counts,
        "implementer_pass_rate_numerator": impl_pass,
        "implementer_pass_rate_denominator": impl_completed,
        "implementer_pass_rate": impl_pass / impl_completed if impl_completed else None,
        "evidence_completion_rate_numerator": evidence_completed,
        "evidence_completion_rate_denominator": evidence_comparable,
        "evidence_completion_rate": (
            evidence_completed / evidence_comparable if evidence_comparable else None
        ),
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
    # #310: 管理系書き込み (resolution batch 等) が updated_at を汚染するため、
    # エージェント活動の実時刻 last_activity_at を優先する (壁時計 500x 膨張の実害対策)
    updated = parse_iso_datetime(state.get("last_activity_at") or state.get("updated_at"))
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
