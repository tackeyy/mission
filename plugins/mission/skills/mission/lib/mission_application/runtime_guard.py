"""Closed application use cases for runtime-guard observations."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Callable, ContextManager, Protocol

from .ports import MissionRepository


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


def record_permission_observation(
    repository: MissionRepository,
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

        def mutate(proposed: dict) -> None:
            proposed["halt_reason"] = reason
            proposed["halt_category"] = "blocked-external"
            proposed["loop_active"] = False
            if transition_phase is None:
                proposed["phase"] = "halted"
            else:
                transition_phase(proposed, "halted", request.observed_at)
            proposed["terminal_outcome"] = "blocked_external"
            proposed["updated_at"] = request.observed_at

        proposed = repository.execute(state, mutate)
        repository.save(proposed)
    return PermissionObservationResult(
        False,
        True,
        probes,
        halt_reason=reason,
        halt_category="blocked-external",
        terminal_outcome="blocked_external",
    )
