"""Direct capacity-gate contracts, independent of writer/CLI wiring."""
import copy
import json

import pytest

from mission_kernel import state_capacity as sc
from mission_kernel.json_codec import encode_json_value, freeze_json_value


def _canonical(document):
    return encode_json_value(freeze_json_value(document))


@pytest.mark.parametrize('historical', [False, True])
@pytest.mark.parametrize('layout', ['v4', 'v5'])
@pytest.mark.parametrize('field,value,accepted', [
    ('halt_reason', '\x01' * 2048, True), ('halt_reason', '\x1c' * 2049, False),
    ('goal_dispatch_host', '\x01' * 128, True), ('goal_dispatch_host', 'x' * 129, False),
    ('goal_dispatch_resolution_fallback_reason', 'x' * 129, False),
    ('owner_session_id', 'a' * 128, True), ('lease_id', 'a' * 129, False),
    ('lease_id', 'bad token', False), ('fencing_epoch', 0, True),
    ('fencing_epoch', sc.LEASE_EPOCH_MAX, True), ('fencing_epoch', sc.LEASE_EPOCH_MAX + 1, False),
    ('fencing_epoch', True, False), ('fencing_epoch', 1.5, False),
])
def test_shared_writer_bounds_cover_both_layouts(layout, field, value, accepted, historical):
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    from .test_issue933_state_capacity_verdict import _base
    document, encoding = _base(layout)
    if layout == 'v4':
        target = document
    else:
        target = document['lease' if field in ('owner_session_id', 'lease_id', 'fencing_epoch') else 'extensions']
    target[field] = value
    if layout == 'v5' and field == 'halt_reason':
        document['control']['halt_reason'] = value
    raw = _canonical(document) if layout == 'v5' else json.dumps(document, indent=2, ensure_ascii=False).encode()
    if accepted or historical:
        assert check_state_capacity(raw if historical else None, raw, encoding=encoding).accepted
    else:
        with pytest.raises(CapacityWriteError, match='state-capacity-invariant-broken'):
            check_state_capacity(None, raw, encoding=encoding)


@pytest.mark.parametrize('layout', ['v4', 'v5'])
@pytest.mark.parametrize('epoch', [None, ''])
def test_new_partial_lease_requires_a_bounded_epoch(layout, epoch):
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    from .test_issue933_state_capacity_verdict import _base
    document, encoding = _base(layout)
    lease = document if layout == 'v4' else document['lease']
    lease.update(owner_session_id='owner', lease_id='token', fencing_epoch=epoch)
    raw = _canonical(document) if layout == 'v5' else json.dumps(document, indent=2).encode()
    with pytest.raises(CapacityWriteError, match='state-capacity-invariant-broken'):
        check_state_capacity(None, raw, encoding=encoding)


@pytest.mark.parametrize('layout', ['v4', 'v5'])
def test_stop_preserves_inherited_legacy_lease_without_bounding_new_tokens(layout):
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    from .test_issue933_state_capacity_verdict import _base
    base, encoding = _base(layout)
    lease = base if layout == 'v4' else base['lease']
    lease.update(owner_session_id='old owner', lease_id='old token', fencing_epoch=1,
                 lease_history=[{'owner_session_id': 'historical owner', 'reason': 'old reason'}])
    proposed = copy.deepcopy(base)
    control = proposed if layout == 'v4' else proposed['control']
    control.update(loop_active=False, halt_reason='stop')
    encode = _canonical if layout == 'v5' else lambda d: json.dumps(d, indent=2).encode()
    assert check_state_capacity(encode(base), encode(proposed), encoding=encoding).accepted
    target = proposed if layout == 'v4' else proposed['lease']
    target['lease_id'] = 'new invalid token'
    with pytest.raises(CapacityWriteError, match='state-capacity-invariant-broken'):
        check_state_capacity(encode(base), encode(proposed), encoding=encoding)


def test_changing_partial_historical_lease_does_not_inherit_missing_epoch():
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    base = {'schema_version': 4, 'mission_id': 'm', 'owner_session_id': 'old',
            'lease_id': 'old-token', 'fencing_epoch': None}
    proposed = dict(base, owner_session_id='new', lease_id='new-token')
    encode = lambda d: json.dumps(d, indent=2).encode()
    with pytest.raises(CapacityWriteError, match='state-capacity-invariant-broken'):
        check_state_capacity(encode(base), encode(proposed), encoding=sc.StateEncoding.LEGACY_PRETTY)


@pytest.mark.parametrize('layout', ['v4', 'v5'])
@pytest.mark.parametrize('mutation', ['append', 'delete', 'replace', 'wrong-retired', 'multiple', 'malformed-history', 'invalid-expiry', 'short-expiry'])
def test_lease_history_requires_append_of_exact_retired_lease(layout, mutation):
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    from .test_issue933_state_capacity_verdict import _base
    base, encoding = _base(layout)
    lease = base if layout == 'v4' else base['lease']
    row = dict(owner_session_id='other', lease_id='other-token', fencing_epoch=1, reason='takeover')
    lease['lease_history'] = 'corrupt' if mutation == 'malformed-history' else [dict(row)]
    proposed = copy.deepcopy(base)
    target = proposed if layout == 'v4' else proposed['lease']
    if mutation in ('malformed-history', 'invalid-expiry', 'short-expiry'):
        target['lease_history'] = ([] if mutation == 'malformed-history' else lease['lease_history']) + [
            dict(owner_session_id=lease['owner_session_id'], lease_id=lease['lease_id'],
                 fencing_epoch=lease['fencing_epoch'], reason='takeover')]
        target.update(lease_id='new-token', fencing_epoch=lease['fencing_epoch'] + 1)
        if mutation != 'malformed-history':
            target['lease_expires_at'] = 'invalid' if mutation == 'invalid-expiry' else '2000-01-01T00:00:00Z'
    elif mutation == 'delete':
        target['lease_history'] = []
    elif mutation == 'replace':
        target['lease_history'][0]['lease_id'] = 'replacement'
    else:
        target['lease_history'].append(dict(row))
        if mutation != 'append':
            target.update(lease_id='new-token', fencing_epoch=lease['fencing_epoch'] + 1)
        if mutation == 'multiple':
            target['lease_history'].append(dict(row))
    encode = _canonical if layout == 'v5' else lambda d: json.dumps(d, indent=2).encode()
    with pytest.raises(CapacityWriteError, match='state-capacity-invariant-broken'):
        check_state_capacity(encode(base), encode(proposed), encoding=encoding)


@pytest.mark.parametrize('layout', ['v4', 'v5'])
def test_over_capacity_halt_preserves_historical_goal_value(layout):
    from mission_persistence.capacity_gate import check_state_capacity
    from .test_issue933_state_capacity_verdict import _base
    base, encoding = _base(layout)
    fields = base if layout == 'v4' else base['extensions']
    fields['goal_dispatch_host'] = 'x' * 129
    encode = _canonical if layout == 'v5' else lambda d: json.dumps(d, indent=2, ensure_ascii=False).encode()
    fields['padding'] = 'p' * (sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA - 4096 - len(encode(base)) - 40)
    proposed = copy.deepcopy(base)
    target = proposed if layout == 'v4' else proposed['control']
    target.update(halt_reason='stop', loop_active=False, phase='halted')
    if layout == 'v5':
        proposed['extensions'].update(halt_reason='stop', loop_active=False, phase='halted')
    assert check_state_capacity(encode(base), encode(proposed), encoding=encoding).accepted


@pytest.mark.parametrize('layout', ['v4', 'v5'])
@pytest.mark.parametrize('old_token,epoch,extra_mutation,keep_owner', [('bad token', '5', False, False), ('x' * 129, True, False, False), ('bad token', 1, True, False), ('bad token', 1, False, True)])
def test_excess_takeover_halt_archives_only_the_exact_old_lease(layout, old_token, epoch, extra_mutation, keep_owner):
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    from .test_issue933_state_capacity_verdict import _base
    base, encoding = _base(layout)
    lease = base if layout == 'v4' else base['lease']
    lease.update(lease_id=old_token, fencing_epoch=epoch, lease_expires_at='2000-01-01T00:00:00Z')
    if keep_owner:
        lease['owner_session_id'] = 'old owner'
    fields = base if layout == 'v4' else base['extensions']
    encode = _canonical if layout == 'v5' else lambda d: json.dumps(d, indent=2, ensure_ascii=False).encode()
    fields['padding'] = 'p' * (sc.STATE_LIMIT - sc.STATE_CAPACITY_HALT_DELTA - 4096 - len(encode(base)) - 40)
    proposed = copy.deepcopy(base)
    target = proposed if layout == 'v4' else proposed['lease']
    target['lease_history'].append(dict(owner_session_id=lease['owner_session_id'], lease_id=old_token,
        fencing_epoch=int(epoch), reason='halt', at='2026-10-09T00:00:00Z'))
    target.update(owner_session_id=lease['owner_session_id'] if keep_owner else 'new-owner', lease_id='new-token', fencing_epoch=int(epoch) + 1,
        lease_expires_at='2099-01-01T00:00:00Z')
    (proposed if layout == 'v4' else proposed['control']).update(halt_reason='stop', loop_active=False, phase='halted')
    if extra_mutation:
        (proposed if layout == 'v4' else proposed['extensions'])['unrelated'] = True
        with pytest.raises(CapacityWriteError, match='state-capacity-exhausted'):
            check_state_capacity(encode(base), encode(proposed), encoding=encoding)
    else:
        assert check_state_capacity(encode(base), encode(proposed), encoding=encoding).accepted

