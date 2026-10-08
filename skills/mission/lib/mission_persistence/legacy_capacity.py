"""Legacy lease admission and exact-byte state publication."""
from __future__ import annotations

import json
from datetime import timezone

from mission_common import parse_iso_datetime
from provider_public_contract import validate_specialist_public_state
from .legacy_v4 import LegacyV4Repository
from .capacity_gate import (StateEncoding, check_state_capacity, validate_lease_token, validate_lease_epoch)


def acquire_legacy_lease(state, session_id, *, reason, presented_lease_id, now,
                         new_token, fields_present, lease_fields, expiry, renewed_expiry,
                         rejected_error, decision):
    lease_field_count = sum(state.get(key) not in (None, "") for key in lease_fields)
    if 0 < lease_field_count < len(lease_fields):
        raise rejected_error("malformed partial session lease")
    if not fields_present(state):
        validate_lease_token(session_id)
        lease_id = presented_lease_id or new_token()
        validate_lease_token(lease_id)
        state["owner_session_id"] = session_id
        state["lease_id"] = lease_id
        state["fencing_epoch"] = 1
        state["lease_expires_at"] = expiry(now)
        return decision("acquired", lease_id, 1)

    owner = str(state["owner_session_id"])
    current_lease_id = str(state["lease_id"])

    def parsed_epoch():
        try:
            return int(state["fencing_epoch"])
        except (ValueError, TypeError, OverflowError) as error:
            raise rejected_error(f"lease held by {owner} until {state.get('lease_expires_at')} (invalid fencing epoch)") from error

    same_owner = owner == session_id
    token_matches = presented_lease_id == current_lease_id if same_owner else False
    if same_owner and token_matches:
        epoch = parsed_epoch()
        state["lease_expires_at"] = renewed_expiry(
            str(state["lease_expires_at"]), now
        )
        return decision("renewed", current_lease_id, epoch)

    expires = parse_iso_datetime(str(state.get("lease_expires_at") or ""))
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    expired = expires is not None and now >= expires.astimezone(timezone.utc)
    if not expired:
        # Same-owner writers without the matching token wait like any foreign
        # writer: after expiry they recover through the fenced takeover below.
        raise rejected_error(
            f"lease held by {owner} until {state.get('lease_expires_at')}"
        )

    retired_lease_ids = {
        str(item.get("lease_id"))
        for item in state.get("lease_history", [])
        if isinstance(item, dict) and item.get("lease_id")
    }
    if presented_lease_id and (
        presented_lease_id == current_lease_id
        or presented_lease_id in retired_lease_ids
    ):
        raise rejected_error(
            f"lease held by {owner} until {state.get('lease_expires_at')} (stale fencing token)"
        )
    epoch = parsed_epoch()
    if session_id != state["owner_session_id"]:
        validate_lease_token(session_id)
    validate_lease_token(reason)
    validate_lease_epoch(epoch)
    new_lease_id = presented_lease_id or new_token()
    validate_lease_token(new_lease_id)
    validate_lease_epoch(epoch + 1)
    state.setdefault("lease_history", []).append({
        "owner_session_id": owner,
        "lease_id": current_lease_id,
        "fencing_epoch": epoch,
        "reason": reason,
        "at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    state["owner_session_id"] = session_id
    state["lease_id"] = new_lease_id
    state["fencing_epoch"] = epoch + 1
    state["lease_expires_at"] = expiry(now)
    return decision("taken-over", new_lease_id, epoch + 1)


def checked_legacy_state_content(path, data, *, replacement=False, base_bytes=None):
    validate_specialist_public_state(data)
    content = json.dumps(data, indent=2, ensure_ascii=False)
    if base_bytes is None:
        base_bytes = path.read_bytes() if path.exists() else None
    check_state_capacity(base_bytes, content.encode('utf-8'), encoding=StateEncoding.LEGACY_PRETTY, replacement=replacement)
    return content


def prepare_legacy_json(path, data, *, administrative, lease_decision, expected_identity, services,
                        before_publish=None, replacement=False, base_bytes=None):
    is_state = services.is_state_shape(data) or services.is_state_path(path)
    if services.is_state_shape(data):
        validate_specialist_public_state(data)
    if lease_decision is services.unset:
        lease_decision = services.enforce_lease(path, data)
    if not administrative and services.is_state_shape(data):
        data['last_activity_at'] = services.now()
    if is_state:
        content = checked_legacy_state_content(path, data, replacement=replacement, base_bytes=base_bytes)
    else:
        content = json.dumps(data, indent=2, ensure_ascii=False)
    def publish():
        if before_publish is not None:
            before_publish(path)
        services.atomic_write(path, lambda f: f.write(content), expected_identity=expected_identity)
        if isinstance(lease_decision, services.decision_type):
            services.process_leases[str(path.resolve())] = lease_decision.lease_id
        services.emit_lease(data, lease_decision)
    return publish


def write_legacy_json(path, data, *, prepare_only=False, **options):
    publish = prepare_legacy_json(path, data, **options)
    return publish if prepare_only else publish()


def write_legacy_terminal(path, data, *, atomic_write, backup_state):
    content = checked_legacy_state_content(path, data)
    backup_state(path)
    atomic_write(path, lambda f: f.write(content))


class DeferredBackupRepository(LegacyV4Repository):
    """Forward save's backup intent to the checked writer's publish callback."""
    def __init__(self, *, write_state, backup_state, **bindings):
        pending = [False]
        def request():
            pending[0] = True
        def write(data, **options):
            callback = backup_state if pending[0] else None
            pending[0] = False
            write_state(data, before_publish=callback, **options)
        super().__init__(write_state=write, backup_state=request, **bindings)
