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
    ('lease_id', 'bad token', False), ('fencing_epoch', 0, False),
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



def _lease_trial(layout):
    from .test_issue933_state_capacity_verdict import _base
    base, encoding = _base(layout)
    lease = base if layout == 'v4' else base['lease']
    lease.update(fencing_epoch=2, lease_history=[dict(owner_session_id='retired',
        lease_id='retired-token', fencing_epoch=1, reason='takeover', at='2026-10-08T00:00:00Z')])
    proposed = copy.deepcopy(base)
    target = proposed if layout == 'v4' else proposed['lease']
    target['lease_history'].append(dict(owner_session_id=lease['owner_session_id'],
        lease_id=lease['lease_id'], fencing_epoch=2, reason='takeover', at='2026-10-09T00:00:00Z'))
    target.update(owner_session_id='new-owner', lease_id='new-token', fencing_epoch=3)
    encode = _canonical if layout == 'v5' else lambda d: json.dumps(d, indent=2).encode()
    return base, proposed, target, encoding, encode


@pytest.mark.parametrize('layout', ['v4', 'v5'])
@pytest.mark.parametrize('defect', ['valid', 'missing-at', 'invalid-at', 'naive-at',
                                  'reused-id', 'no-append-reused-id', 'offset-at', 'offset-expiry',
                                  'untrimmed-owner', 'zero-epoch', 'extra-key'])
def test_appended_lease_and_current_identity_obey_decoder(layout, defect):
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    from mission_kernel.codec_v4 import _decode_legacy_lease
    from mission_kernel.codec_v5 import _decode_lease
    from mission_kernel.errors import MissionStateDecodeError
    base, proposed, lease, encoding, encode = _lease_trial(layout)
    entry = lease['lease_history'][-1]
    read_lease = _decode_legacy_lease if layout == 'v4' else lambda fields: _decode_lease({'lease': {'kind': 'fenced', **fields}})
    if defect == 'missing-at':
        del entry['at']
    elif defect == 'invalid-at':
        entry['at'] = 'invalid'
    elif defect == 'naive-at':
        entry['at'] = '2026-10-09T00:00:00'
    elif defect == 'reused-id':
        lease['lease_id'] = 'retired-token'
    elif defect == 'no-append-reused-id':
        lease['lease_history'].pop()
        lease['lease_id'] = 'retired-token'
    elif defect == 'offset-at':
        entry['at'] = '2026-10-09T09:00:00+09:00'
    elif defect == 'offset-expiry':
        lease['lease_expires_at'] = '9999-12-31T23:59:59+00:00'
    elif defect == 'untrimmed-owner':
        (base if layout == 'v4' else base['lease'])['owner_session_id'] = entry['owner_session_id'] = ' old '
    elif defect == 'zero-epoch':
        (base if layout == 'v4' else base['lease'])['fencing_epoch'] = entry['fencing_epoch'] = 0
        lease.update(fencing_epoch=1, lease_history=[entry])
        (base if layout == 'v4' else base['lease'])['lease_history'] = []
    elif defect == 'extra-key':
        entry['extra'] = True
    valid = defect == 'valid' or (layout == 'v4' and defect in ('offset-at', 'offset-expiry'))
    if valid:
        read_lease(lease)
        assert sc.classify_write_kind(base, proposed, encoding=encoding) is sc.WriteKind.STOP_TAKEOVER
        assert check_state_capacity(encode(base), encode(proposed), encoding=encoding).accepted
    else:
        if defect != 'extra-key' or layout == 'v5':
            with pytest.raises(MissionStateDecodeError):
                read_lease(lease)
        assert sc.classify_write_kind(base, proposed, encoding=encoding) is sc.WriteKind.NORMAL
        with pytest.raises(CapacityWriteError, match='state-capacity-invariant-broken'):
            check_state_capacity(encode(base), encode(proposed), encoding=encoding)


@pytest.mark.parametrize('layout', ['v4', 'v5'])
@pytest.mark.parametrize('historical', ['reason', 'owner', 'lease-id', 'epoch', 'extra-key'])
def test_over_capacity_renewal_preserves_unchanged_history(layout, historical):
    from mission_persistence.capacity_gate import check_state_capacity
    from .test_issue933_state_capacity_verdict import _push_over_capacity
    base, _, _, encoding, encode = _lease_trial(layout)
    lease = base if layout == 'v4' else base['lease']
    row = lease['lease_history'][0]
    if historical == 'reason': row['reason'] = 'old reason'
    elif historical == 'owner': row['owner_session_id'] = 'old owner'
    elif historical == 'lease-id': row['lease_id'] = 'old token'
    elif historical == 'epoch':
        row['fencing_epoch'] = sc.LEASE_EPOCH_MAX + 1
        lease['fencing_epoch'] = sc.LEASE_EPOCH_MAX + 2
    else: row['extra'] = True
    lease['lease_expires_at'] = '2026-10-09T00:00:00Z'
    base = _push_over_capacity(base, encoding)
    proposed = copy.deepcopy(base)
    target = proposed if layout == 'v4' else proposed['lease']
    target['lease_expires_at'] = '2099-01-01T00:00:00Z'
    assert sc.classify_write_kind(base, proposed, encoding=encoding) is sc.WriteKind.STOP_TAKEOVER
    assert check_state_capacity(encode(base), encode(proposed), encoding=encoding).accepted
    assert target['lease_history'] == (base if layout == 'v4' else base['lease'])['lease_history']


@pytest.mark.parametrize('container', ['extensions', 'lease'])
@pytest.mark.parametrize('value', [None, []])
@pytest.mark.parametrize('side', ['base', 'proposed'])
def test_malformed_v5_containers_return_capacity_error(container, value, side):
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    base, proposed, _, encoding, encode = _lease_trial('v5')
    (base if side == 'base' else proposed)[container] = value
    with pytest.raises(CapacityWriteError, match='state-capacity-invariant-broken'):
        check_state_capacity(encode(base), encode(proposed), encoding=encoding)


@pytest.mark.parametrize('history', ['corrupt', None, [None]])
def test_malformed_expired_legacy_history_is_a_lease_refusal(history):
    from .test_issue936_state_capacity_reservation import _acquire_capacity_lease
    state = dict(owner_session_id='old', lease_id='old-token', fencing_epoch=1,
        lease_expires_at='2000-01-01T00:00:00Z', lease_history=history)
    before = copy.deepcopy(state)
    with pytest.raises(ValueError, match='lease held.*invalid lease history'):
        _acquire_capacity_lease(state, 'new', lease_id='new-token', reason='takeover')
    assert state == before
