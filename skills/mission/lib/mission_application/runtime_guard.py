"""Closed application use cases for runtime-guard observations."""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from typing import Callable, ContextManager, Protocol

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
from mission_common import is_supersede_marked
from mission_kernel.commands import MarkHalt
from mission_kernel.model import HaltCategory
from mission_kernel.transitions import decide, transition_control_claim_bounds
from .ports import LegacyMissionRepository


RUNTIME_GUARD_COMMAND_OWNERS = {
    "permission-preflight": "A5.runtime-guard",
    "stop-guard-observe": "A5.runtime-guard",
}

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
        decision = decide(
            real_state if real_state is not None else _mark_halt_decision_state(state),
            MarkHalt(
                HaltCategory.BLOCKED_EXTERNAL,
                reason,
                superseded=is_supersede_marked(state.get("resolution_status"), reason),
            ),
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
        transition = decision.transition if real_state is not None else None
        claimed = set(transition_control_claim_bounds(transition)) if transition is not None else set()

        def mutate(proposed: dict) -> None:
            proposed["halt_reason"] = reason
            if "halt_category" not in claimed:
                proposed["halt_category"] = "blocked-external"
            if "loop_active" not in claimed:
                proposed["loop_active"] = False
            if transition_phase is None:
                proposed["phase"] = "halted"
            else:
                _closed_permission_transition(
                    proposed, transition_phase, request.observed_at
                )
            if "terminal_outcome" not in claimed:
                proposed["terminal_outcome"] = "blocked_external"
            proposed["updated_at"] = request.observed_at

        proposed = repository.execute(state, mutate, transition)
        repository.save(proposed)
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
