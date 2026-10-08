"""Shared pre-publication capacity gate; byte images are the writer's inputs."""
from __future__ import annotations

import json
from dataclasses import asdict

from mission_kernel.errors import StateBoundaryError
from mission_kernel.json_codec import STATE_LIMIT, decode_json_object
from mission_kernel.state_capacity import (
    CapacityBase, StateEncoding, state_capacity_verdict,
    HALT_REASON_MAX_CHARS, GOAL_DISPATCH_REASON_MAX_CHARS,
    LEASE_TOKEN_PATTERN, LEASE_EPOCH_MAX,
    remaining_takeovers,
)


class CapacityWriteError(StateBoundaryError):
    def __init__(self, code):
        super().__init__(code, code)


def validate_capacity_fields(document, base=None):
    """Bound stored values, including raw compatibility copies of halt reasons."""
    v5 = document.get('schema_version') == 5 and isinstance(document.get('control'), dict)
    containers = (document.get('control', {}), document.get('extensions', {})) if v5 else (document,)
    for fields in containers:
        reason = fields.get('halt_reason')
        if reason is not None and (not isinstance(reason, str) or len(reason) > HALT_REASON_MAX_CHARS):
            raise CapacityWriteError('state-capacity-invariant-broken')
        for key, value in fields.items():
            if key.startswith('goal_dispatch_') and (
                not isinstance(value, str) or len(value) > GOAL_DISPATCH_REASON_MAX_CHARS
            ):
                raise CapacityWriteError('state-capacity-invariant-broken')
    lease = document.get('lease', {}) if v5 else document
    previous = (base.get('lease', {}) if v5 else base) if base is not None else {}
    # Historical values are not new allocations. Preserve them verbatim;
    # next_takeover_cost reserves the measured cost of archiving old tokens.
    def unchanged(value, old):
        return json.dumps(value, sort_keys=True) == json.dumps(old, sort_keys=True)
    identities = ('owner_session_id', 'lease_id', 'fencing_epoch')
    inherited = base is not None and all(
        key in previous and key in lease and unchanged(lease[key], previous[key]) for key in identities)
    active = any(lease.get(key) not in (None, '') for key in ('owner_session_id', 'lease_id'))
    for key in ('owner_session_id', 'lease_id', 'fencing_epoch'):
        value = lease.get(key)
        if inherited:
            continue
        if active or value not in (None, ''):
            (validate_lease_epoch if key == 'fencing_epoch' else validate_lease_token)(value)
    history = lease.get('lease_history', [])
    old_history = previous.get('lease_history', [])
    if not isinstance(history, list):
        raise CapacityWriteError('state-capacity-invariant-broken')
    if not isinstance(old_history, list):
        old_history = []
    migrating = (not inherited and type(previous.get('fencing_epoch')) is int
                 and lease.get('fencing_epoch') == previous['fencing_epoch'] + 1
                 and lease.get('lease_id') != previous.get('lease_id')
                 and len(history) == len(old_history) + 1
                 and unchanged(history[:-1], old_history))
    for index, entry in enumerate(history):
        if index < len(old_history) and unchanged(entry, old_history[index]):
            continue
        if not isinstance(entry, dict):
            raise CapacityWriteError('state-capacity-invariant-broken')
        for key in ('owner_session_id', 'lease_id', 'reason'):
            if (migrating and index == len(old_history) and key != 'reason'
                    and entry.get(key) == str(previous.get(key))
                    and entry.get('fencing_epoch') == previous.get('fencing_epoch')):
                continue
            validate_lease_token(entry.get(key))
        validate_lease_epoch(entry.get('fencing_epoch'))


def validate_lease_token(value):
    if not isinstance(value, str) or LEASE_TOKEN_PATTERN.fullmatch(value) is None:
        raise CapacityWriteError('state-capacity-invariant-broken')


def validate_lease_epoch(value):
    if type(value) is not int or not 0 <= value <= LEASE_EPOCH_MAX:
        raise CapacityWriteError('state-capacity-invariant-broken')


def check_state_capacity(base_bytes, proposed_bytes, *, encoding):
    """Judge the exact encoded images before the first persistence side effect."""
    proposed = (decode_json_object(proposed_bytes, limit=max(STATE_LIMIT, len(proposed_bytes))).thaw()
                if encoding is StateEncoding.CANONICAL else json.loads(proposed_bytes))
    if not isinstance(proposed, dict):
        raise StateBoundaryError('root-not-object', 'root-not-object')
    base = None if base_bytes is None else CapacityBase(json.loads(base_bytes), len(base_bytes))
    validate_capacity_fields(proposed, None if base is None else base.document)
    verdict = state_capacity_verdict(base, proposed, len(proposed_bytes), encoding=encoding)
    if not verdict.accepted:
        raise CapacityWriteError(verdict.code)
    return verdict


def state_capacity_status(state_path, *, load_snapshot):
    snapshot, _state = load_snapshot(state_path, legacy_compatibility=True)
    document = snapshot.raw_document_copy()
    encoding = StateEncoding.CANONICAL if snapshot.generation is not None else StateEncoding.LEGACY_PRETTY
    size = len(snapshot.state_bytes)
    verdict = state_capacity_verdict(CapacityBase(document, size), document, size, encoding=encoding)
    return {**asdict(verdict.metrics), 'mode': verdict.mode, 'code': verdict.code,
            'remaining_takeovers': remaining_takeovers(document)}
