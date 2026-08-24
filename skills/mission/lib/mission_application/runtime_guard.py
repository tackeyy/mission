"""Closed application use cases for runtime-guard observations."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, ContextManager, Optional, Protocol, Tuple, Union

from activity_segments import (
    ACTIVITY_KINDS,
    ACTIVITY_REASONS_BY_KIND,
    RECENT_SEGMENT_LIMIT,
    WAIT_KINDS,
)

from .lifecycle import (
    TERMINALIZABLE_ACTIVE,
    _mark_halt_decision_state,
    diagnose_terminalizable_state,
    real_terminalizable_state,
)
from mission_common import parse_iso_datetime, is_supersede_marked
from mission_kernel.commands import MarkHalt
from mission_kernel.model import HaltCategory
from mission_kernel.transitions import decide
from .ports import (
    AggregateIndexError,
    LegacyCommandExecutionResult,
    LegacyMissionRepository,
)
from .compatibility import compatibility_delta


RUNTIME_GUARD_COMMAND_OWNERS = {
    "permission-preflight": "A5.runtime-guard",
    "stop-guard-observe": "A5.runtime-guard",
}


class RuntimeGuardFailure(ValueError):
    """A fail-closed runtime-guard request rejection."""


@dataclass(frozen=True)
class ResolvedProviderConsentPathObservation:
    """The one adapter-resolved representation used for policy and I/O."""

    parts: tuple[str, ...]


@dataclass(frozen=True)
class ProviderConsentRequest:
    """Pure consent policy input with adapter-resolved path facts."""

    provider: object
    resolved_path: ResolvedProviderConsentPathObservation


def validate_provider_consent_path_parts(parts: tuple[str, ...]) -> None:
    """Apply the consent-path policy to adapter-resolved path facts."""
    if type(parts) is not tuple or any(type(part) is not str for part in parts):
        raise ValueError("provider-consent-session-path-forbidden")
    if any(part.casefold() == ".mission-state" for part in parts):
        raise ValueError("provider-consent-session-path-forbidden")


def resolve_provider_consent_path(
    resolved: ResolvedProviderConsentPathObservation,
) -> tuple[str, ...]:
    """Validate and return the exact path representation used by the adapter."""
    try:
        if type(resolved) is not ResolvedProviderConsentPathObservation:
            raise ValueError("provider-consent-path-resolution-invalid")
        validate_provider_consent_path_parts(resolved.parts)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeGuardFailure(str(exc)) from exc
    return resolved.parts


def validate_provider_consent_request(
    request: ProviderConsentRequest,
) -> tuple[str, tuple[str, ...]]:
    """Normalize the provider and validate the exact path used for consent I/O."""
    if type(request) is not ProviderConsentRequest or type(request.provider) is not str:
        raise RuntimeGuardFailure("--provider is required")
    provider = request.provider.strip()
    if not provider:
        raise RuntimeGuardFailure("--provider is required")
    return provider, resolve_provider_consent_path(request.resolved_path)


@dataclass(frozen=True)
class RegisteredEntryPointDistributionObservation:
    """Adapter-observed entry point and distribution facts."""

    entry_point_name: str
    entry_point_value: str
    has_attached_distribution: bool
    distribution_name: str
    distribution_version: str
    owned_entry_points: tuple[tuple[str, str, str], ...]


def validate_registered_entry_point_distribution(
    *,
    entry_point_name: str,
    entry_point_value: str,
    has_attached_distribution: bool,
    distribution_name: str,
    distribution_version: str,
    owned_entry_points: tuple[tuple[str, str, str], ...],
    configured_distribution: str,
    configured_version: str,
    group: str,
) -> None:
    """Validate adapter-observed entry point and distribution facts only."""
    invalid = "approval verifier distribution identity mismatch"
    if (
        type(entry_point_name) is not str
        or not entry_point_name
        or type(entry_point_value) is not str
        or not entry_point_value
        or type(has_attached_distribution) is not bool
        or type(distribution_name) is not str
        or not distribution_name
        or type(distribution_version) is not str
        or not distribution_version
        or type(configured_distribution) is not str
        or not configured_distribution
        or type(configured_version) is not str
        or not configured_version
        or type(group) is not str
        or not group
        or type(owned_entry_points) is not tuple
        or any(
            type(item) is not tuple
            or len(item) != 3
            or any(type(value) is not str for value in item)
            for item in owned_entry_points
        )
    ):
        raise ValueError(invalid)
    if not has_attached_distribution and sum(
        item == (group, entry_point_name, entry_point_value)
        for item in owned_entry_points
    ) != 1:
        raise ValueError(invalid)
    if (
        distribution_name.lower() != configured_distribution.lower()
        or distribution_version != configured_version
    ):
        raise ValueError("approval verifier distribution identity mismatch")


def validate_registered_approval_entry_point_distribution(
    observed: RegisteredEntryPointDistributionObservation,
    configured_item: object,
    *,
    group: str,
) -> None:
    """Apply closed policy to adapter-observed distribution facts."""
    invalid = "approval verifier distribution identity mismatch"
    try:
        if (
            type(observed) is not RegisteredEntryPointDistributionObservation
            or type(configured_item) is not dict
        ):
            raise ValueError(invalid)
        validate_registered_entry_point_distribution(
            entry_point_name=observed.entry_point_name,
            entry_point_value=observed.entry_point_value,
            has_attached_distribution=observed.has_attached_distribution,
            distribution_name=observed.distribution_name,
            distribution_version=observed.distribution_version,
            owned_entry_points=observed.owned_entry_points,
            configured_distribution=configured_item["distribution"],
            configured_version=configured_item["version"],
            group=group,
        )
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise ValueError(invalid) from exc

STOP_GUARD_SCHEMA = "mission-stop-guard/1"
_STOP_KEYS = frozenset(
    {
        "schema",
        "session_id",
        "last_digest",
        "last_detail_epoch",
        "block_count",
        "reinjection_count",
        "detail_count",
        "heartbeat_count",
    }
)
_COUNTER_KEYS = (
    "last_detail_epoch",
    "block_count",
    "reinjection_count",
    "detail_count",
    "heartbeat_count",
)
_PROBE_TARGETS = ("state", "assumptions")
_PROBE_OUTCOMES = frozenset({"allowed", "denied", "unknown"})
_PROBE_ERRORS = frozenset(
    {None, "write-unavailable", "invalid-evidence-path", "assumptions-path-missing"}
)
_PERMISSION_TRANSITION_FIELDS = frozenset(
    {
        "activity_anomaly_counts",
        "activity_current",
        "activity_rollup",
        "activity_segments",
        "phase",
        "phase_durations_sec",
        "phase_started_at",
        "resume_target_phase",
    }
)
_MISSING = object()
_PHASES = frozenset({"planning", "executing", "reviewing", "scoring"})
_ACTIVITY_PHASES = _PHASES | {"unknown"}
_SEGMENT_KEYS = frozenset(
    {
        "kind",
        "phase",
        "reason",
        "started_at",
        "ended_at",
        "duration_sec",
        "detail",
        "iteration",
    }
)
_ROLLUP_KEYS = frozenset(
    {
        "observed_total_sec",
        "closed_segment_count",
        "activity_duration_totals_sec",
        "phase_activity_duration_totals_sec",
        "wait_reason_totals_sec",
    }
)
_ANOMALY_KEYS = frozenset({"invalid-current-terminal", "invalid-phase-terminal"})


class GuardFindingKind(str, Enum):
    NONE = "none"
    STALE = "stale"
    AWAITING_USER = "awaiting-user"
    LEASE_EXPIRED = "lease-expired"
    ORPHAN = "orphan"
    INDETERMINATE = "indeterminate"


class SessionSelectionReason(str, Enum):
    NONE = "none"
    EXACT_SESSION_ID = "exact-session-id"
    EXACT_PID_FENCED = "exact-pid-fenced"
    LEGACY_PID_FALLBACK = "legacy-pid-fallback"
    ENVLESS_FIRST_ELIGIBLE = "envless-first-eligible"
    NO_ELIGIBLE_SESSION = "no-eligible-session"
    AUTHORITATIVE_STATE_UNREADABLE = "authoritative-state-unreadable"


class LeaseStatus(str, Enum):
    ABSENT = "absent"
    UNEXPIRED = "unexpired"
    EXPIRED = "expired"
    INVALID = "invalid"


class GuardCommandKind(str, Enum):
    NONE = "none"
    MARK_HALT = "mark-halt"
    CLEANUP_STALE = "cleanup-stale"
    STOP_GUARD_OBSERVE = "stop-guard-observe"


class GuardHaltCategory(str, Enum):
    STALE = "stale"


@dataclass(frozen=True)
class SessionSelection:
    state_file: Optional[str]
    session_id: Optional[str]
    reason: SessionSelectionReason
    considered_state_files: Tuple[str, ...]


@dataclass(frozen=True)
class FreshnessEvidence:
    timestamp_field: Optional[str]
    timestamp_value: Optional[str]
    observed_at: str
    age_sec: Optional[int]
    warn_after_sec: int
    halt_after_sec: int


@dataclass(frozen=True)
class LeaseEvidence:
    status: LeaseStatus
    expires_at: Optional[str]
    observed_at: str


@dataclass(frozen=True)
class OrphanEvidence:
    pid: Optional[int]
    pid_alive: Optional[bool]
    check_applicable: bool


@dataclass(frozen=True)
class GuardEvidence:
    freshness: Optional[FreshnessEvidence]
    awaiting_user: bool
    lease: LeaseEvidence
    orphan: OrphanEvidence
    planning_warn_iterations: int
    pending_digest: str


@dataclass(frozen=True)
class NoCommand:
    kind: GuardCommandKind = GuardCommandKind.NONE


@dataclass(frozen=True)
class MarkHaltCommand:
    cwd: str
    session_id: str
    reason: str
    category: GuardHaltCategory
    origin: GuardFindingKind
    kind: GuardCommandKind = GuardCommandKind.MARK_HALT


@dataclass(frozen=True)
class CleanupStaleExecuteCommand:
    root: str
    expected_state_file: str
    execute: bool
    kind: GuardCommandKind = GuardCommandKind.CLEANUP_STALE

    def __post_init__(self) -> None:
        if self.execute is not True:
            raise ValueError("cleanup-stale-execute-required")


@dataclass(frozen=True)
class StopGuardObserveCommand:
    session_id: str
    digest: str
    now_epoch: int
    ttl_seconds: int
    attempt: int
    max_attempts: int
    kind: GuardCommandKind = GuardCommandKind.STOP_GUARD_OBSERVE


GuardCommand = Union[
    NoCommand,
    MarkHaltCommand,
    CleanupStaleExecuteCommand,
    StopGuardObserveCommand,
]


@dataclass(frozen=True)
class GuardContinuation:
    project_root: str
    hook_session_id: Optional[str]
    hook_session_id_source: str
    hook_pid: Optional[int]
    processed_orphan_state_files: Tuple[str, ...]


@dataclass(frozen=True)
class HookReply:
    emit: bool
    decision: str
    reason: str
    outcome_kind: str


@dataclass(frozen=True)
class GuardDecision:
    decision_id: str
    host_decision: str
    reason_code: str
    outcome_kind: str
    selection: SessionSelection
    finding: GuardFindingKind
    evidence: GuardEvidence
    command: GuardCommand
    continuation: GuardContinuation
    reply: HookReply
    display_reason: str
    planning_warning: str
    session_id: str
    pending_digest: str


@dataclass(frozen=True)
class GuardCommandReceipt:
    decision_id: str
    kind: GuardCommandKind
    exit_code: int
    stdout: str


@dataclass(frozen=True)
class GuardSessionFact:
    state_file: str
    session_id: str
    project_root: Optional[str]
    loop_active: bool
    passes: bool
    halt_reason: str
    halt_category: str
    lease_owner_session_id: Optional[str]
    lease_expires_at: Optional[str]
    pid: Optional[int]
    pid_alive: Optional[bool]
    heartbeat_at: Optional[str]
    last_progress_at: Optional[str]
    last_activity_at: Optional[str]
    updated_at: Optional[str]
    awaiting_user: bool
    iteration: int
    phase: str
    score_history_count: int
    last_score: Optional[float]
    threshold: float
    mission: str
    issue_ref: Optional[str]
    read_error: Optional[str]


@dataclass(frozen=True)
class GuardRequest:
    project_root: str
    stop_hook_active: bool
    mission_session_id: Optional[str]
    claude_session_id: Optional[str]
    codex_thread_id: Optional[str]
    hook_pid: Optional[int]
    candidates: Tuple[GuardSessionFact, ...]
    observed_at: str
    observed_epoch: int
    stale_halt_seconds_raw: Optional[str]
    planning_warn_iterations_raw: Optional[str]
    observe_ttl_seconds_raw: Optional[str]
    pending_breakdown: str
    pending_digest: str
    processed_orphan_state_files: Tuple[str, ...]


@dataclass(frozen=True)
class GuardPolicy:
    warn_after_sec: int
    halt_after_sec: int
    planning_warn_iterations: int
    observe_ttl_seconds: int
    observe_max_attempts: int


def _positive_int(raw: object, default: int, minimum: int) -> int:
    try:
        value = int(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


def normalize_guard_policy(
    *,
    stale_halt_seconds_raw: object = None,
    planning_warn_iterations_raw: object = None,
    observe_ttl_seconds_raw: object = None,
) -> GuardPolicy:
    """Normalize the existing shell/Python policy without changing boundaries."""
    return GuardPolicy(
        warn_after_sec=3600,
        halt_after_sec=_positive_int(stale_halt_seconds_raw, 10800, 300),
        planning_warn_iterations=_positive_int(
            planning_warn_iterations_raw, 3, 1
        ),
        observe_ttl_seconds=_positive_int(observe_ttl_seconds_raw, 600, 1),
        observe_max_attempts=3,
    )


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    # #511 の境界検査は application 層の属性呼び出し ``.replace(`` を filesystem
    # publication と見なすため、ISO parsing は mission_common の共通 helper へ委譲する。
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return datetime.combine(parsed.date(), parsed.time(), tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sanitize_session_id(value: str) -> str:
    safe = re.sub(r"[/\\]", "_", value).strip().lstrip(".")
    return safe or "default"


def _hook_session(request: GuardRequest) -> Tuple[Optional[str], str]:
    if request.mission_session_id:
        return _sanitize_session_id(request.mission_session_id), "mission-session-id"
    if request.claude_session_id:
        return "cc-" + _sanitize_session_id(request.claude_session_id), "claude-session-id"
    if request.codex_thread_id:
        return "cx-" + _sanitize_session_id(request.codex_thread_id), "codex-thread-id"
    if request.hook_pid is not None and request.hook_pid > 0:
        return "pid-" + _sanitize_session_id(str(request.hook_pid)), "pid-fallback"
    return None, "none"


def _state_file_session_id(state_file: str) -> str:
    name = re.split(r"[/\\\\]", state_file)[-1]
    return name[:-5] if name.endswith(".json") else name


def _ordered_candidates(
    request: GuardRequest, hook_session_id: Optional[str], source: str
) -> Tuple[GuardSessionFact, ...]:
    candidates = tuple(sorted(request.candidates, key=lambda item: item.state_file))
    if hook_session_id is None:
        return candidates
    exact = tuple(
        item for item in candidates
        if _state_file_session_id(item.state_file) == hook_session_id
    )
    if not exact:
        return candidates
    if source != "pid-fallback":
        return exact[:1]
    first = exact[0]
    return (first,) + tuple(item for item in candidates if item is not first)


def _terminal_reason(candidate: GuardSessionFact, project_root: str) -> Optional[str]:
    if candidate.project_root and candidate.project_root != project_root:
        return "project-root-mismatch"
    if candidate.passes:
        return "passes-true"
    if candidate.halt_category == "evidence-submitted":
        return "evidence-submitted"
    if candidate.halt_reason:
        return "halt-reason"
    if not candidate.loop_active:
        return "inactive"
    return None


def _outcome_kind(reason: str) -> str:
    return {
        "passes-true": "completed-pass",
        "halt-reason": "halted",
        "evidence-submitted": "completed-evidence",
    }.get(reason, "expected-gate")


def _freshness(
    candidate: GuardSessionFact, observed_at: str, policy: GuardPolicy
) -> FreshnessEvidence:
    observed = _parse_iso(observed_at)
    if observed is None:
        raise ValueError("guard-observed-at-invalid")
    timestamp_field = None
    timestamp_value = None
    age_sec = None
    for field in (
        "heartbeat_at", "last_progress_at", "last_activity_at", "updated_at"
    ):
        value = getattr(candidate, field)
        if not value:
            continue
        timestamp_field = field
        timestamp_value = value
        parsed = _parse_iso(value)
        if parsed is not None:
            seconds = (observed - parsed).total_seconds()
            if math.isfinite(seconds) and seconds >= 0:
                age_sec = int(seconds)
        break
    return FreshnessEvidence(
        timestamp_field=timestamp_field,
        timestamp_value=timestamp_value,
        observed_at=observed_at,
        age_sec=age_sec,
        warn_after_sec=policy.warn_after_sec,
        halt_after_sec=policy.halt_after_sec,
    )


def _lease_evidence(candidate: GuardSessionFact, observed_at: str) -> LeaseEvidence:
    if candidate.lease_owner_session_id is None and candidate.lease_expires_at is None:
        return LeaseEvidence(LeaseStatus.ABSENT, None, observed_at)
    observed = _parse_iso(observed_at)
    expiry = _parse_iso(candidate.lease_expires_at)
    if observed is None:
        raise ValueError("guard-observed-at-invalid")
    if expiry is None:
        status = LeaseStatus.INVALID
    elif expiry > observed:
        status = LeaseStatus.UNEXPIRED
    else:
        status = LeaseStatus.EXPIRED
    return LeaseEvidence(status, candidate.lease_expires_at, observed_at)


def _display(candidate: GuardSessionFact, request: GuardRequest, policy: GuardPolicy) -> Tuple[str, str]:
    last_score = candidate.last_score if candidate.last_score is not None else "n/a"
    session_label = candidate.session_id + (
        "(#%s)" % candidate.issue_ref if candidate.issue_ref else ""
    )
    planning_warning = ""
    if (
        candidate.score_history_count == 0
        and candidate.iteration >= policy.planning_warn_iterations
    ):
        planning_warning = (
            "[WARN: push-score 未実行の疑い (iter=%s, score_history 空, phase=%s)。"
            "mission-state.py get で state を確認し、push-score 未実行なら push-score を実行してください] "
        ) % (candidate.iteration, candidate.phase)
    display_reason = (
        "/mission skill アクティブ・未達 (session=%s, 未達一覧=[%s], iter=%s, "
        "last_score=%s, threshold=%s)。 state.json の passes=true か halt_reason を立てるまで"
        "ループを継続。 ミッション: %s"
    ) % (
        session_label,
        request.pending_breakdown,
        candidate.iteration,
        last_score,
        candidate.threshold,
        candidate.mission[:200],
    )
    return display_reason, planning_warning


def _decision_id(
    request: GuardRequest, selection: SessionSelection, reason: str, finding: GuardFindingKind
) -> str:
    material = "\n".join(
        (
            request.project_root,
            selection.state_file or "",
            selection.session_id or "",
            selection.reason.value,
            reason,
            finding.value,
            request.observed_at,
            ",".join(request.processed_orphan_state_files),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _empty_evidence(request: GuardRequest, policy: GuardPolicy) -> GuardEvidence:
    return GuardEvidence(
        freshness=None,
        awaiting_user=False,
        lease=LeaseEvidence(LeaseStatus.ABSENT, None, request.observed_at),
        orphan=OrphanEvidence(None, None, False),
        planning_warn_iterations=policy.planning_warn_iterations,
        pending_digest=request.pending_digest,
    )


def _terminal_decision(
    request: GuardRequest,
    policy: GuardPolicy,
    selection: SessionSelection,
    continuation: GuardContinuation,
    reason: str,
    *,
    finding: GuardFindingKind = GuardFindingKind.NONE,
    emit: bool = False,
    reply_reason: str = "",
) -> GuardDecision:
    outcome = _outcome_kind(reason)
    return GuardDecision(
        decision_id=_decision_id(request, selection, reason, finding),
        host_decision="block" if emit else "skip",
        reason_code=reason,
        outcome_kind=outcome,
        selection=selection,
        finding=finding,
        evidence=_empty_evidence(request, policy),
        command=NoCommand(),
        continuation=continuation,
        reply=HookReply(emit, "block" if emit else "skip", reply_reason, outcome),
        display_reason="",
        planning_warning="",
        session_id=selection.session_id or "",
        pending_digest=request.pending_digest,
    )


def decide_stop_guard(request: GuardRequest) -> GuardDecision:
    """Return the sole typed policy decision for one Stop-hook observation."""
    if not isinstance(request, GuardRequest):
        raise ValueError("guard-request-invalid")
    policy = normalize_guard_policy(
        stale_halt_seconds_raw=request.stale_halt_seconds_raw,
        planning_warn_iterations_raw=request.planning_warn_iterations_raw,
        observe_ttl_seconds_raw=request.observe_ttl_seconds_raw,
    )
    hook_session_id, hook_source = _hook_session(request)
    continuation = GuardContinuation(
        project_root=request.project_root,
        hook_session_id=hook_session_id,
        hook_session_id_source=hook_source,
        hook_pid=request.hook_pid,
        processed_orphan_state_files=request.processed_orphan_state_files,
    )
    if request.stop_hook_active:
        return _terminal_decision(
            request,
            policy,
            SessionSelection(None, None, SessionSelectionReason.NONE, ()),
            continuation,
            "stop-hook-reentry",
        )

    considered = []
    selected = None
    selected_reason = SessionSelectionReason.NO_ELIGIBLE_SESSION
    last_terminal_reason = "no-eligible-session"
    last_terminal_candidate = None
    for candidate in _ordered_candidates(request, hook_session_id, hook_source):
        if candidate.state_file in request.processed_orphan_state_files:
            continue
        considered.append(candidate.state_file)
        if candidate.read_error is not None:
            selection = SessionSelection(
                candidate.state_file,
                candidate.session_id,
                SessionSelectionReason.AUTHORITATIVE_STATE_UNREADABLE,
                tuple(considered),
            )
            return _terminal_decision(
                request,
                policy,
                selection,
                continuation,
                "authoritative-state-unreadable",
                finding=GuardFindingKind.INDETERMINATE,
                emit=True,
                reply_reason=(
                    "authoritative session state を検証できないため安全側で停止: "
                    + candidate.state_file
                ),
            )
        terminal_reason = _terminal_reason(candidate, request.project_root)
        if terminal_reason is not None:
            last_terminal_reason = terminal_reason
            last_terminal_candidate = candidate
            continue
        file_session_id = _state_file_session_id(candidate.state_file)
        if hook_session_id:
            if file_session_id == hook_session_id:
                if (
                    candidate.lease_owner_session_id is not None
                    and candidate.lease_owner_session_id != hook_session_id
                ):
                    last_terminal_reason = "lease-owner-mismatch"
                    continue
                selected_reason = (
                    SessionSelectionReason.EXACT_PID_FENCED
                    if hook_source == "pid-fallback"
                    else SessionSelectionReason.EXACT_SESSION_ID
                )
            elif (
                hook_source == "pid-fallback"
                and candidate.lease_owner_session_id is None
                and candidate.pid == request.hook_pid
            ):
                selected_reason = SessionSelectionReason.LEGACY_PID_FALLBACK
            else:
                last_terminal_reason = (
                    "pid-owner-mismatch"
                    if hook_source == "pid-fallback" and candidate.lease_owner_session_id is None
                    else "session-owner-mismatch"
                )
                continue
        else:
            selected_reason = SessionSelectionReason.ENVLESS_FIRST_ELIGIBLE

        orphan_applicable = (
            hook_session_id is None
            and candidate.lease_owner_session_id is None
            and candidate.pid is not None
            and candidate.pid > 0
        )
        if orphan_applicable and candidate.pid_alive is False:
            selection = SessionSelection(
                candidate.state_file,
                candidate.session_id,
                selected_reason,
                tuple(considered),
            )
            evidence = GuardEvidence(
                freshness=None,
                awaiting_user=candidate.awaiting_user,
                lease=_lease_evidence(candidate, request.observed_at),
                orphan=OrphanEvidence(candidate.pid, False, True),
                planning_warn_iterations=policy.planning_warn_iterations,
                pending_digest=request.pending_digest,
            )
            reason = "orphan-pid"
            finding = GuardFindingKind.ORPHAN
            return GuardDecision(
                decision_id=_decision_id(request, selection, reason, finding),
                host_decision="skip",
                reason_code=reason,
                outcome_kind="expected-gate",
                selection=selection,
                finding=finding,
                evidence=evidence,
                command=MarkHaltCommand(
                    cwd=request.project_root,
                    session_id=candidate.session_id,
                    reason="orphan: pid %s dead" % candidate.pid,
                    category=GuardHaltCategory.STALE,
                    origin=GuardFindingKind.ORPHAN,
                ),
                continuation=continuation,
                reply=HookReply(False, "skip", "", "expected-gate"),
                display_reason="",
                planning_warning="",
                session_id=candidate.session_id,
                pending_digest=request.pending_digest,
            )
        selected = candidate
        break

    if selected is None:
        retain_terminal = (
            last_terminal_candidate is not None
            and last_terminal_reason in {
                "project-root-mismatch",
                "passes-true",
                "evidence-submitted",
                "halt-reason",
                "inactive",
            }
        )
        selection = SessionSelection(
            last_terminal_candidate.state_file if retain_terminal else None,
            last_terminal_candidate.session_id if retain_terminal else None,
            SessionSelectionReason.NO_ELIGIBLE_SESSION,
            tuple(considered),
        )
        decision = _terminal_decision(
            request, policy, selection, continuation, last_terminal_reason
        )
        if not retain_terminal:
            return decision
        lease = _lease_evidence(last_terminal_candidate, request.observed_at)
        display_reason, planning_warning = _display(
            last_terminal_candidate, request, policy
        )
        evidence = replace(
            decision.evidence,
            awaiting_user=last_terminal_candidate.awaiting_user,
            lease=lease,
            orphan=OrphanEvidence(
                last_terminal_candidate.pid,
                None,
                False,
            ),
        )
        return replace(
            decision,
            evidence=evidence,
            display_reason=display_reason,
            planning_warning=planning_warning,
            session_id=last_terminal_candidate.session_id,
        )

    selection = SessionSelection(
        selected.state_file,
        selected.session_id,
        selected_reason,
        tuple(considered),
    )
    freshness = _freshness(selected, request.observed_at, policy)
    lease = _lease_evidence(selected, request.observed_at)
    orphan_applicable = (
        hook_session_id is None
        and lease.status is LeaseStatus.ABSENT
        and selected.pid is not None
        and selected.pid > 0
    )
    evidence = GuardEvidence(
        freshness=freshness,
        awaiting_user=selected.awaiting_user,
        lease=lease,
        orphan=OrphanEvidence(
            selected.pid,
            selected.pid_alive if orphan_applicable else None,
            orphan_applicable,
        ),
        planning_warn_iterations=policy.planning_warn_iterations,
        pending_digest=request.pending_digest,
    )
    display_reason, planning_warning = _display(selected, request, policy)
    reason = "active-unfinished"
    finding = GuardFindingKind.NONE
    reply_prefix = ""
    command = None

    if freshness.age_sec is None:
        finding = GuardFindingKind.INDETERMINATE
    elif freshness.age_sec > policy.halt_after_sec:
        minutes = freshness.age_sec // 60
        if lease.status is LeaseStatus.UNEXPIRED:
            finding = GuardFindingKind.STALE
            reply_prefix = (
                "[WARN: state が %s分 未更新だが session lease は有効なため "
                "stale auto-halt を保留] "
            ) % minutes
        elif selected.awaiting_user:
            finding = GuardFindingKind.AWAITING_USER
            reply_prefix = (
                "[WARN: state が %s分 未更新だが awaiting_user=true のため "
                "stale auto-halt を保留] "
            ) % minutes
        elif lease.status in {LeaseStatus.EXPIRED, LeaseStatus.INVALID}:
            finding = (
                GuardFindingKind.LEASE_EXPIRED
                if lease.status is LeaseStatus.EXPIRED
                else GuardFindingKind.INDETERMINATE
            )
            command = CleanupStaleExecuteCommand(
                root=request.project_root,
                expected_state_file=selected.state_file,
                execute=True,
            )
        else:
            finding = GuardFindingKind.STALE
            command = MarkHaltCommand(
                cwd=request.project_root,
                session_id=selected.session_id,
                reason="stale: auto-halted after %sm idle" % minutes,
                category=GuardHaltCategory.STALE,
                origin=GuardFindingKind.STALE,
            )
    elif freshness.age_sec > policy.warn_after_sec:
        reply_prefix = (
            "[WARN: state が %s分 未更新。stuck/放置の可能性 — cleanup-stale を検討] "
            % (freshness.age_sec // 60)
        )

    if command is None:
        if request.pending_digest:
            command = StopGuardObserveCommand(
                session_id=selected.session_id,
                digest=request.pending_digest,
                now_epoch=request.observed_epoch,
                ttl_seconds=policy.observe_ttl_seconds,
                attempt=0,
                max_attempts=policy.observe_max_attempts,
            )
        else:
            command = NoCommand()
    auto_action = command.kind in {
        GuardCommandKind.MARK_HALT, GuardCommandKind.CLEANUP_STALE
    }
    reply = HookReply(
        emit=not auto_action,
        decision="block",
        reason=reply_prefix + planning_warning + display_reason,
        outcome_kind="expected-gate",
    )
    return GuardDecision(
        decision_id=_decision_id(request, selection, reason, finding),
        host_decision="block",
        reason_code=reason,
        outcome_kind="expected-gate",
        selection=selection,
        finding=finding,
        evidence=evidence,
        command=command,
        continuation=continuation,
        reply=reply,
        display_reason=display_reason,
        planning_warning=planning_warning,
        session_id=selected.session_id,
        pending_digest=request.pending_digest,
    )


def _command_failure_reply(prior: GuardDecision) -> GuardDecision:
    return replace(
        prior,
        finding=GuardFindingKind.INDETERMINATE,
        command=NoCommand(),
        reply=HookReply(
            True,
            "block",
            "stale auto-halt の書き込みに失敗。手動で cleanup-stale を実行してください",
            "expected-gate",
        ),
    )


def _reply_prefix(prior: GuardDecision) -> str:
    if prior.display_reason and prior.reply.reason.endswith(prior.display_reason):
        return prior.reply.reason[: -len(prior.display_reason)]
    return ""


def resolve_guard_command_receipt(
    prior: GuardDecision, receipt: GuardCommandReceipt
) -> GuardDecision:
    """Resolve one closed command receipt without delegating policy to shell."""
    if receipt.decision_id != prior.decision_id:
        raise ValueError("guard-receipt-decision-mismatch")
    if receipt.kind is not prior.command.kind:
        raise ValueError("guard-receipt-command-mismatch")
    if isinstance(prior.command, MarkHaltCommand):
        if prior.command.origin is GuardFindingKind.ORPHAN:
            processed = prior.continuation.processed_orphan_state_files + (
                prior.selection.state_file,
            )
            return replace(
                prior,
                reason_code="orphan-processed",
                command=NoCommand(),
                continuation=replace(
                    prior.continuation,
                    processed_orphan_state_files=tuple(
                        item for item in processed if item is not None
                    ),
                ),
                reply=HookReply(False, "skip", "", "expected-gate"),
            )
        if receipt.exit_code == 0:
            return replace(
                prior,
                reason_code="stale-auto-halt-complete",
                command=NoCommand(),
                reply=HookReply(False, "skip", "", "expected-gate"),
            )
        return _command_failure_reply(prior)
    if isinstance(prior.command, CleanupStaleExecuteCommand):
        target_halted = False
        if receipt.exit_code == 0:
            try:
                payload = json.loads(receipt.stdout)
                halted = payload.get("halted", []) if isinstance(payload, dict) else []
                target_halted = any(
                    isinstance(item, dict)
                    and item.get("path") == prior.command.expected_state_file
                    for item in halted
                )
            except (TypeError, ValueError):
                target_halted = False
        if target_halted:
            return replace(
                prior,
                reason_code="stale-auto-halt-complete",
                command=NoCommand(),
                reply=HookReply(False, "skip", "", "expected-gate"),
            )
        return _command_failure_reply(prior)
    if isinstance(prior.command, StopGuardObserveCommand):
        mode = None
        if receipt.exit_code == 0:
            try:
                payload = json.loads(receipt.stdout)
                candidate_mode = payload.get("mode") if isinstance(payload, dict) else None
                if candidate_mode in {"detail", "heartbeat"}:
                    mode = candidate_mode
            except (TypeError, ValueError):
                mode = None
        if mode is None:
            next_attempt = prior.command.attempt + 1
            if next_attempt < prior.command.max_attempts:
                return replace(
                    prior,
                    command=replace(prior.command, attempt=next_attempt),
                )
            mode = "detail"
        if mode == "heartbeat":
            reason = (
                _reply_prefix(prior)
                + "/mission heartbeat (blocker=unfinished-mission, "
                "next=python3 scripts/mission-state.py next)"
            )
        else:
            reason = prior.reply.reason
        return replace(
            prior,
            command=NoCommand(),
            reply=HookReply(True, "block", reason, "expected-gate"),
        )
    raise ValueError("guard-receipt-command-not-dispatchable")


class StopObservationRepository(Protocol):
    """CAS persistence boundary for one Stop-hook sidecar."""

    def transaction(self) -> ContextManager[object]:
        ...

    def load(self, session_id: str) -> tuple[dict | None, object | None]:
        ...

    def save(
        self, session_id: str, document: dict, expected_identity: object | None
    ) -> None:
        ...


@dataclass(frozen=True)
class StopObservationRequest:
    session_id: str
    digest: str
    now_epoch: int
    ttl_seconds: int


@dataclass(frozen=True)
class StopObservationResult:
    mode: str
    document: dict


@dataclass(frozen=True)
class PermissionProbe:
    target: str
    outcome: str
    error: str | None


@dataclass(frozen=True)
class PermissionObservationRequest:
    probes: tuple[PermissionProbe, ...]
    observed_at: str


@dataclass(frozen=True)
class PermissionObservationResult:
    ok: bool
    halt_recorded: bool
    probes: tuple[PermissionProbe, ...]
    halt_reason: str | None = None
    halt_category: str | None = None
    terminal_outcome: str | None = None
    decision: object | None = None
    # 批2-a-3 (#632): claims の根拠に実 state を使えたか。"undecodable" は
    # 想定外の劣化であり、呼び出し元が観測できるようにする（state には書かない）。
    claim_source: str | None = None


class PermissionHaltRejected(RuntimeError):
    def __init__(self, code: str):
        super().__init__("permission-halt-rejected: " + code)
        self.code = code


def _bounded_token(value: object, *, maximum: int = 256) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and value.strip() == value
        and all(0x20 <= ord(char) <= 0x7E for char in value)
    )


def _validate_stop_request(request: StopObservationRequest) -> None:
    if not isinstance(request, StopObservationRequest):
        raise ValueError("stop-observation-invalid")
    if not isinstance(request.session_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", request.session_id
    ):
        raise ValueError("stop-session-invalid")
    if not isinstance(request.digest, str) or not re.fullmatch(
        r"[0-9a-f]{64}", request.digest
    ):
        raise ValueError("stop-digest-invalid")
    for value, minimum in ((request.now_epoch, 0), (request.ttl_seconds, 1)):
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise ValueError("stop-time-invalid")


def _validated_previous(value: object, session_id: str) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != _STOP_KEYS:
        raise ValueError("stop-state-invalid")
    if value.get("schema") != STOP_GUARD_SCHEMA or value.get("session_id") != session_id:
        raise ValueError("stop-state-identity-invalid")
    digest = value.get("last_digest")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("stop-state-digest-invalid")
    for key in _COUNTER_KEYS:
        item = value.get(key)
        if not isinstance(item, int) or isinstance(item, bool) or item < 0:
            raise ValueError("stop-state-counter-invalid")
    if value["detail_count"] + value["heartbeat_count"] != value["block_count"]:
        raise ValueError("stop-state-counter-inconsistent")
    if value["reinjection_count"] != value["block_count"]:
        raise ValueError("stop-state-counter-inconsistent")
    return copy.deepcopy(value)


def observe_stop_guard(
    repository: StopObservationRepository, request: StopObservationRequest
) -> StopObservationResult:
    """Append one bounded Stop observation and publish it through CAS."""
    _validate_stop_request(request)
    with repository.transaction():
        raw_previous, identity = repository.load(request.session_id)
        previous = _validated_previous(raw_previous, request.session_id)
        changed = previous is None or previous["last_digest"] != request.digest
        ttl_elapsed = (
            previous is not None
            and request.now_epoch - previous["last_detail_epoch"] >= request.ttl_seconds
        )
        mode = "detail" if changed or ttl_elapsed else "heartbeat"
        document = {
            "schema": STOP_GUARD_SCHEMA,
            "session_id": request.session_id,
            "last_digest": request.digest,
            "last_detail_epoch": (
                request.now_epoch if mode == "detail" else previous["last_detail_epoch"]
            ),
            "block_count": (previous["block_count"] if previous else 0) + 1,
            "reinjection_count": (previous["reinjection_count"] if previous else 0) + 1,
            "detail_count": (previous["detail_count"] if previous else 0)
            + int(mode == "detail"),
            "heartbeat_count": (previous["heartbeat_count"] if previous else 0)
            + int(mode == "heartbeat"),
        }
        closed = _validated_previous(document, request.session_id)
        repository.save(request.session_id, closed, identity)
    return StopObservationResult(mode=mode, document=copy.deepcopy(closed))


def _validate_permission_request(
    request: PermissionObservationRequest,
) -> tuple[PermissionProbe, ...]:
    if not isinstance(request, PermissionObservationRequest):
        raise ValueError("permission-observation-invalid")
    if not _bounded_token(request.observed_at, maximum=64) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z",
        request.observed_at,
    ):
        raise ValueError("permission-observed-at-invalid")
    if not isinstance(request.probes, tuple) or not request.probes:
        raise ValueError("permission-probes-invalid")
    probes = []
    seen = set()
    expected_index = 0
    for probe in request.probes:
        if not isinstance(probe, PermissionProbe):
            raise ValueError("permission-probe-invalid")
        if probe.target not in _PROBE_TARGETS or probe.target in seen:
            raise ValueError("permission-probe-target-invalid")
        if expected_index >= len(_PROBE_TARGETS) or probe.target != _PROBE_TARGETS[expected_index]:
            raise ValueError("permission-probe-order-invalid")
        if probe.outcome not in _PROBE_OUTCOMES or probe.error not in _PROBE_ERRORS:
            raise ValueError("permission-probe-result-invalid")
        if (probe.outcome == "allowed") != (probe.error is None):
            raise ValueError("permission-probe-result-inconsistent")
        seen.add(probe.target)
        expected_index += 1
        probes.append(probe)
        if probe.outcome != "allowed":
            break
    if probes[-1].outcome == "allowed" and len(probes) != len(_PROBE_TARGETS):
        raise ValueError("permission-probes-incomplete")
    return tuple(probes)


def _permission_reason(probe: PermissionProbe) -> str:
    if probe.error == "assumptions-path-missing":
        detail = "assumptions path missing"
    elif probe.error == "invalid-evidence-path":
        detail = "assumptions evidence path is invalid"
    else:
        detail = f"{probe.target} write unavailable"
    return f"Phase 0 permission preflight failed before task execution: {detail}"


def _finite_nonnegative(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
        and value >= 0
    )


def _timestamp(value: object) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value)
    )


def _numeric_map(value: object, allowed_keys: frozenset[str]) -> bool:
    return isinstance(value, dict) and all(
        key in allowed_keys and _finite_nonnegative(item)
        for key, item in value.items()
    )


def _valid_activity_segment(segment: object) -> bool:
    if not isinstance(segment, dict) or not set(segment).issubset(_SEGMENT_KEYS):
        return False
    required = {"kind", "phase", "reason", "started_at", "ended_at", "duration_sec"}
    if not required.issubset(segment):
        return False
    kind = segment.get("kind")
    reason = segment.get("reason")
    if (
        kind not in ACTIVITY_KINDS
        or reason not in ACTIVITY_REASONS_BY_KIND[kind]
        or segment.get("phase") not in _ACTIVITY_PHASES
        or not _timestamp(segment.get("started_at"))
        or not _timestamp(segment.get("ended_at"))
        or not _finite_nonnegative(segment.get("duration_sec"))
    ):
        return False
    detail = segment.get("detail")
    if detail is not None and (
        not isinstance(detail, str)
        or not detail
        or len(detail) > 160
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in detail)
    ):
        return False
    iteration = segment.get("iteration")
    return iteration is None or (
        isinstance(iteration, int) and not isinstance(iteration, bool) and iteration > 0
    )


def _valid_activity_rollup(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != _ROLLUP_KEYS:
        return False
    count = value.get("closed_segment_count")
    if (
        not _finite_nonnegative(value.get("observed_total_sec"))
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or not _numeric_map(value.get("activity_duration_totals_sec"), frozenset(ACTIVITY_KINDS))
    ):
        return False
    phase_totals = value.get("phase_activity_duration_totals_sec")
    if not isinstance(phase_totals, dict) or any(
        phase not in _ACTIVITY_PHASES
        or not _numeric_map(totals, frozenset(ACTIVITY_KINDS))
        for phase, totals in phase_totals.items()
    ):
        return False
    wait_totals = value.get("wait_reason_totals_sec")
    return isinstance(wait_totals, dict) and all(
        kind in WAIT_KINDS
        and _numeric_map(totals, frozenset(ACTIVITY_REASONS_BY_KIND[kind]))
        for kind, totals in wait_totals.items()
    )


def _closed_permission_transition(
    state: dict,
    transition_phase: Callable[[dict, str, str], None],
    observed_at: str,
) -> None:
    """Project only the closed timing delta from a compatibility reducer."""
    candidate = copy.deepcopy(state)
    transition_phase(candidate, "halted", observed_at)
    changed = {
        key
        for key in set(state) | set(candidate)
        if state.get(key, _MISSING) != candidate.get(key, _MISSING)
    }
    if not changed.issubset(_PERMISSION_TRANSITION_FIELDS):
        raise ValueError("permission-transition-invalid")
    if (
        candidate.get("phase") != "halted"
        or candidate.get("phase_started_at") != observed_at
        or "resume_target_phase" in candidate
        or candidate.get("activity_current") is not None
    ):
        raise ValueError("permission-transition-invalid")
    durations = candidate.get("phase_durations_sec")
    if durations is not None and not _numeric_map(durations, _PHASES):
        raise ValueError("permission-transition-invalid")
    anomalies = candidate.get("activity_anomaly_counts")
    if anomalies is not None and (
        not isinstance(anomalies, dict)
        or any(
            key not in _ANOMALY_KEYS
            or not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            for key, value in anomalies.items()
        )
    ):
        raise ValueError("permission-transition-invalid")
    segments = candidate.get("activity_segments", [])
    if (
        not isinstance(segments, list)
        or len(segments) > RECENT_SEGMENT_LIMIT
        or not all(_valid_activity_segment(segment) for segment in segments)
    ):
        raise ValueError("permission-transition-invalid")
    rollup = candidate.get("activity_rollup")
    if rollup is not None and not _valid_activity_rollup(rollup):
        raise ValueError("permission-transition-invalid")
    for key in _PERMISSION_TRANSITION_FIELDS:
        if key in candidate:
            state[key] = copy.deepcopy(candidate[key])
        else:
            state.pop(key, None)


def record_permission_observation(
    repository: LegacyMissionRepository,
    request: PermissionObservationRequest,
    *,
    transition_phase: Callable[[dict, str, str], None] | None = None,
) -> PermissionObservationResult:
    """Persist the fixed fail-closed consequence of a closed probe sequence."""
    probes = _validate_permission_request(request)
    failed = next((probe for probe in probes if probe.outcome != "allowed"), None)
    if failed is None:
        return PermissionObservationResult(True, False, probes)
    reason = _permission_reason(failed)
    with repository.transaction():
        state = repository.load()
        # 批1-d (#620): 固定の fail-closed 帰結 (blocked-external halt) も kernel
        # decision を gate とし、#630 の claims 検証つき execute を通す。他 A5
        # observation writer と同じく synthetic monotonic view で decidable を
        # 維持する（劣化 doc でも preflight halt を書けなくしない）。
        diagnosis = diagnose_terminalizable_state(state)
        real_state = real_terminalizable_state(state) if diagnosis == TERMINALIZABLE_ACTIVE else None
        use_transition = real_state is not None

        def mutate(proposed: dict) -> None:
            proposed["halt_reason"] = reason
            proposed["halt_category"] = "blocked-external"
            proposed["loop_active"] = False
            if transition_phase is None:
                proposed["phase"] = "halted"
            else:
                _closed_permission_transition(
                    proposed, transition_phase, request.observed_at
                )
            proposed["terminal_outcome"] = "blocked_external"
            proposed["updated_at"] = request.observed_at

        proposed = copy.deepcopy(state)
        mutate(proposed)
        if use_transition:
            command = MarkHalt(
                HaltCategory.BLOCKED_EXTERNAL,
                reason,
                superseded=is_supersede_marked(
                    state.get("resolution_status"), reason
                ),
                at=request.observed_at,
                legacy_reason=reason,
                compatibility=compatibility_delta(
                    state,
                    proposed,
                    exclude={
                        "phase",
                        "loop_active",
                        "halt_reason",
                        "halt_category",
                        "terminal_outcome",
                        "updated_at",
                    },
                ),
                permission_observation=True,
            )
        else:
            command = MarkHalt(
                HaltCategory.BLOCKED_EXTERNAL,
                reason,
                superseded=is_supersede_marked(
                    state.get("resolution_status"), reason
                ),
            )
        decision = decide(
            real_state if real_state is not None else _mark_halt_decision_state(state),
            command,
        )
        if not decision.accepted:
            # kernel invariant 違反は呼び出し元の ValueError 吸収（運用系の
            # 縮退経路）に混ぜず、fail-open を防ぐ（批2-a-2 #631）
            code = (
                decision.rejection.code
                if decision.rejection is not None
                else "rejection-unclosed"
            )
            raise PermissionHaltRejected(code)

        try:
            if use_transition:
                execution = repository.execute(command, aggregate_action="remove")
                decision = execution.decision
                if not decision.accepted:
                    code = (
                        decision.rejection.code
                        if decision.rejection is not None
                        else "rejection-unclosed"
                    )
                    raise PermissionHaltRejected(code)
                proposed = execution.projection
            else:
                repository.save(proposed, aggregate_action="remove")
        except AggregateIndexError as error:
            # The authority is already halted.  Preserve permission-preflight's
            # historical success result while the durable intent records the
            # derived-index repair that remains pending.
            if isinstance(error.execution, LegacyCommandExecutionResult):
                decision = error.execution.decision
                proposed = error.execution.projection
    return PermissionObservationResult(
        False,
        True,
        probes,
        halt_reason=reason,
        halt_category=proposed.get("halt_category"),
        terminal_outcome=proposed.get("terminal_outcome"),
        decision=decision,
        claim_source=diagnosis,
    )
