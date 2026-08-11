"""Portable specialist selection and invocation lifecycle contract."""

from __future__ import annotations

import re
import secrets
from typing import Any, Mapping


SELECTION_DECISIONS = frozenset({"none", "selected", "declined", "unavailable"})
TERMINAL_INVOCATION_STATUSES = frozenset(
    {
        "completed",
        "prepared",
        "awaiting-input",
        "inline-applied",
        "skill-tool-applied",
        "skipped",
        "unavailable",
        "failed",
    }
)

_SELECTION_ID = re.compile(r"\Asel_[0-9a-f]{32}\Z")
_INVOCATION_ID = re.compile(r"\Ainv_[0-9a-f]{32}\Z")


class SpecialistLifecycleError(ValueError):
    """The lifecycle document is malformed or requests an invalid transition."""


def new_selection_id() -> str:
    """Return a new opaque selection identity."""
    return f"sel_{secrets.token_hex(16)}"


def new_invocation_id() -> str:
    """Return a new opaque invocation identity."""
    return f"inv_{secrets.token_hex(16)}"


def invocation_lifecycle_state(status: str) -> str:
    if status == "selected":
        return "selected"
    if status == "started":
        return "invoked"
    return "terminal"


def is_terminal_invocation(record: Mapping[str, Any]) -> bool:
    """Return whether an invocation is in a terminal status and state."""
    return (
        record.get("status") in TERMINAL_INVOCATION_STATUSES
        and record.get("lifecycle_state") == "terminal"
    )


def validate_selection_checkpoint(
    checkpoint: Mapping[str, Any], *, allow_pending: bool = False
) -> None:
    selection_id = checkpoint.get("selection_id")
    if not isinstance(selection_id, str) or not _SELECTION_ID.fullmatch(selection_id):
        raise SpecialistLifecycleError("selection_id must be an opaque sel_ identifier")
    decision = checkpoint.get("decision")
    if decision not in SELECTION_DECISIONS:
        raise SpecialistLifecycleError("selection decision is not recognized")
    reason_code = checkpoint.get("reason_code")
    if not isinstance(reason_code, str) or not reason_code.strip():
        raise SpecialistLifecycleError("selection reason_code is required")
    lifecycle_state = checkpoint.get("lifecycle_state")
    if allow_pending and decision == "none" and reason_code == "pending-evaluation":
        if lifecycle_state != "candidate":
            raise SpecialistLifecycleError("pending selection must remain in candidate state")
        return
    expected = "selected" if decision == "selected" else "terminal"
    if lifecycle_state != expected:
        raise SpecialistLifecycleError(
            f"selection lifecycle_state must be {expected} for decision={decision}"
        )


def selection_checkpoint(
    state: Mapping[str, Any], *, allow_pending: bool = False
) -> Mapping[str, Any]:
    """Look up and validate the current selection checkpoint."""
    checkpoint = state.get("specialists_decision")
    if not isinstance(checkpoint, Mapping):
        raise SpecialistLifecycleError("specialists_decision must be an object")
    validate_selection_checkpoint(checkpoint, allow_pending=allow_pending)
    return checkpoint


def validate_invocation_record(record: Mapping[str, Any]) -> None:
    invocation_id = record.get("invocation_id")
    if not isinstance(invocation_id, str) or not _INVOCATION_ID.fullmatch(invocation_id):
        raise SpecialistLifecycleError("invocation_id must be an opaque inv_ identifier")
    selection_id = record.get("selection_id")
    if selection_id is not None and (
        not isinstance(selection_id, str) or not _SELECTION_ID.fullmatch(selection_id)
    ):
        raise SpecialistLifecycleError("invocation selection_id is malformed")
    for field in ("phase", "role", "skill", "mode", "status"):
        value = record.get(field)
        if not isinstance(value, str) or not value.strip():
            raise SpecialistLifecycleError(f"invocation {field} is required")
    iteration = record.get("iteration")
    if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 0:
        raise SpecialistLifecycleError("invocation iteration must be a non-negative integer")
    expected = invocation_lifecycle_state(str(record["status"]))
    if record.get("lifecycle_state") != expected:
        raise SpecialistLifecycleError(
            f"invocation lifecycle_state must be {expected} for status={record['status']}"
        )


def invocation_by_id(state: Mapping[str, Any], invocation_id: str) -> Mapping[str, Any]:
    """Look up exactly one invocation by its opaque identity."""
    matches = [
        record
        for record in state.get("specialist_invocations") or []
        if isinstance(record, Mapping) and record.get("invocation_id") == invocation_id
    ]
    if len(matches) != 1:
        raise SpecialistLifecycleError("invocation_id must identify exactly one invocation")
    validate_invocation_record(matches[0])
    return matches[0]


def validate_invocation_transition(
    existing: Mapping[str, Any], requested: Mapping[str, Any]
) -> None:
    validate_invocation_record(existing)
    validate_invocation_record(requested)
    if existing.get("invocation_id") != requested.get("invocation_id"):
        raise SpecialistLifecycleError("invocation_id cannot change")
    for field in ("selection_id", "iteration", "phase", "role", "skill", "mode"):
        if existing.get(field) != requested.get(field):
            raise SpecialistLifecycleError(f"invocation identity mismatch for {field}")
    previous_status = str(existing.get("status"))
    next_status = str(requested.get("status"))
    if previous_status == "selected":
        allowed = next_status == "started" or next_status in TERMINAL_INVOCATION_STATUSES
    elif previous_status == "started":
        allowed = next_status in TERMINAL_INVOCATION_STATUSES
    else:
        allowed = False
    if not allowed:
        raise SpecialistLifecycleError(
            f"invalid specialist invocation transition: {previous_status} -> {next_status}"
        )


def validate_specialist_lifecycle(
    state: Mapping[str, Any], *, allow_pending: bool = False
) -> None:
    """Validate checkpoint bindings and unique invocation identities."""
    checkpoint = selection_checkpoint(state, allow_pending=allow_pending)
    selection_id = checkpoint["selection_id"]
    for collection_name in ("specialists_candidates", "specialists_selected"):
        collection = state.get(collection_name) or []
        if not isinstance(collection, list):
            raise SpecialistLifecycleError(f"{collection_name} must be a list")
        for item in collection:
            if not isinstance(item, Mapping):
                raise SpecialistLifecycleError(f"{collection_name} entries must be objects")
            if item.get("selection_id") != selection_id:
                raise SpecialistLifecycleError(
                    f"{collection_name} entry is not bound to the current selection_id"
                )
    invocations = state.get("specialist_invocations") or []
    if not isinstance(invocations, list):
        raise SpecialistLifecycleError("specialist_invocations must be a list")
    seen: set[str] = set()
    for invocation in invocations:
        if not isinstance(invocation, Mapping):
            raise SpecialistLifecycleError("specialist_invocations entries must be objects")
        if not invocation.get("invocation_id"):
            continue
        validate_invocation_record(invocation)
        invocation_id = str(invocation["invocation_id"])
        if invocation_id in seen:
            raise SpecialistLifecycleError("duplicate specialist invocation_id")
        seen.add(invocation_id)
