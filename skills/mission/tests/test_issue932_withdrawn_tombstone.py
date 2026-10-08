"""E0b-1: pending request withdrawal as a withdrawn tombstone (#932).

Design: docs/design/880-repair-lineage.md Sec.2 "決定（pending request の取下げ:
withdrawn tombstone）". The tombstone replaces a pending record in place; it is
never a terminal and never introduces lineage. The command that triggers the
withdrawal is D2c's concern (#912); this module exercises only the pure
projection state, the reducer, and the readers that must not crash on it.
"""
import json

import pytest

from mission_kernel.fresh_review import (
    FRESH_REVIEW_CAPACITY_WITHDRAWN_REASON, FRESH_REVIEW_INT_MAX, PROJECTION_SCHEMA,
    FreshReviewError, FreshReviewProjection, FreshReviewRecord, WithdrawnFreshReviewRecord,
    candidate_identity, canonical_bytes, canonical_digest, consume_request, decode_projection,
    decode_request, projection_document, reserve_request, withdraw_request,
)

from .test_issue879_completion_cli import completion_session
from .test_issue895_fresh_review import ADAPTER, ARGS, _prepare, _pure_projection

_POOL = '0123456789abcdefghijklmnopqrstuvwxyz'


def _ids(n, length):
    if length == 1:
        return list(_POOL[:n])
    return [('a' + str(i)).ljust(length, 'x') for i in range(n)]


def _pending_record(n_criteria, id_len, *, prefix=''):
    """A pending record with the shared ids and criterion ids at id_len chars."""
    ids = _ids(n_criteria + 3, id_len)
    request_id, nonce, prepare_operation_id = (prefix + value for value in ids[:3])
    criterion_ids = [prefix + value for value in ids[3:]]
    input_digest = canonical_digest({'input': 'fixture-' + request_id})
    bindings = [{'criterion_id': cid, 'role': 'verification', 'command_id': 'command-1',
                 'definition_digest': ADAPTER, 'snapshot_digest': ADAPTER} for cid in criterion_ids]
    request = decode_request({
        'schema': 'mission-fresh-review-request/1', 'request_id': request_id, 'nonce': nonce,
        'mission_id': 'mission-1', 'session_id': 'session-1', 'requirement_digest': ADAPTER,
        'contract_digest': ADAPTER, 'verifier_policy_digest': ADAPTER,
        'candidate_digest': candidate_identity({'command-1': ADAPTER}), 'input_digest': input_digest,
        'adapter_registration_digest': ADAPTER, 'candidate_bindings': bindings,
        'criterion_ids': criterion_ids, 'iteration': 1, 'perspective': 'counterexamples',
        'allowed_tools': [], 'wall_time_sec': 300, 'max_tool_calls': 64, 'max_replays': 16,
        'max_output_bytes': 262144, 'max_packet_bytes': 1048576, 'created_at': '2026-01-01T00:00:00+00:00',
        'input_ref': {'kind': 'fresh-review-input',
                      'relative_path': 'evidence/fresh-review/' + input_digest[7:] + '.json',
                      'digest': input_digest, 'size': 19},
    })
    return FreshReviewRecord(request, prepare_operation_id, ADAPTER, ADAPTER)


def _withdraw(record, *, operation_id='withdraw-1', fencing_epoch=1):
    projection = withdraw_request(FreshReviewProjection((record,)), request_id=record.request.request_id,
                                  operation_id=operation_id, fencing_epoch=fencing_epoch)
    return projection.requests[0]


def _v5_canonical(projection):
    return canonical_bytes({'fresh_review': projection_document(projection)})


def _v4_flat(projection):
    return json.dumps({'fresh_review': projection_document(projection)}, indent=2, ensure_ascii=False).encode('utf-8')


@pytest.mark.parametrize('n_criteria', [1, 2, 10])
@pytest.mark.parametrize('id_len', [1, 128])
def test_tombstone_strictly_smaller(n_criteria, id_len):
    """The max-shape tombstone is strictly smaller than the min-shape pending record."""
    pending = _pending_record(n_criteria, id_len)
    withdrawn = _withdraw(pending, operation_id='w' * 128, fencing_epoch=FRESH_REVIEW_INT_MAX)
    for encode in (_v5_canonical, _v4_flat):
        pending_len = len(encode(FreshReviewProjection((pending,))))
        withdrawn_len = len(encode(FreshReviewProjection((withdrawn,))))
        assert withdrawn_len < pending_len, (encode, n_criteria, id_len, pending_len, withdrawn_len)


def test_withdraw_request_is_pure_and_only_touches_the_target_pending_record():
    one = _pending_record(1, 4, prefix='one-')
    two = _pending_record(1, 4, prefix='two-')
    projection = FreshReviewProjection((one, two))
    result = withdraw_request(projection, request_id=one.request.request_id,
                              operation_id='withdraw-one', fencing_epoch=5)
    assert result.requests[1] == two  # Untouched sibling record, same position.
    tombstone = result.requests[0]
    assert isinstance(tombstone, WithdrawnFreshReviewRecord)
    assert tombstone.status == 'withdrawn'
    assert tombstone.reason == FRESH_REVIEW_CAPACITY_WITHDRAWN_REASON
    assert tombstone.request_id == one.request.request_id
    assert tombstone.nonce == one.request.nonce
    assert tombstone.prepare_operation_id == one.prepare_operation_id
    from mission_kernel.fresh_review import request_document
    assert tombstone.request_digest == canonical_digest(request_document(one.request))
    assert tombstone.criterion_ids == one.request.criterion_ids
    assert tombstone.withdraw_operation_id == 'withdraw-one'
    assert tombstone.withdraw_fencing_epoch == 5
    # The original projection is untouched; the reducer is pure.
    assert projection.requests == (one, two)


def test_withdraw_request_unavailable_and_not_pending():
    pending = _pending_record(1, 4)
    projection = FreshReviewProjection((pending,))
    with pytest.raises(FreshReviewError, match='^fresh-review-request-unavailable$'):
        withdraw_request(projection, request_id='missing', operation_id='op', fencing_epoch=1)
    request = pending.request
    intent, payload = canonical_digest('dispatch'), canonical_digest({'input': request.input_digest})
    reserved = reserve_request(projection, request, operation_id='dispatch-1', intent_digest=intent, payload_digest=payload)
    with pytest.raises(FreshReviewError, match='^fresh-review-request-not-pending$'):
        withdraw_request(reserved, request_id=request.request_id, operation_id='withdraw-1', fencing_epoch=1)
    consumed = consume_request(reserved, request, operation_id='dispatch-1', intent_digest=intent,
                               payload_digest=payload, result={'status': 'blocked'})
    with pytest.raises(FreshReviewError, match='^fresh-review-request-not-pending$'):
        withdraw_request(consumed, request_id=request.request_id, operation_id='withdraw-2', fencing_epoch=1)


def test_withdraw_request_is_idempotent_and_rejects_a_different_operation():
    pending = _pending_record(1, 4)
    withdrawn_projection = withdraw_request(FreshReviewProjection((pending,)), request_id=pending.request.request_id,
                                            operation_id='withdraw-1', fencing_epoch=9)
    same = withdraw_request(withdrawn_projection, request_id=pending.request.request_id,
                            operation_id='withdraw-1', fencing_epoch=9)
    assert same == withdrawn_projection
    with pytest.raises(FreshReviewError, match='^fresh-review-request-withdrawn$'):
        withdraw_request(withdrawn_projection, request_id=pending.request.request_id,
                         operation_id='withdraw-2', fencing_epoch=9)


def test_withdraw_request_rejects_operation_id_collisions_with_other_records():
    pending = _pending_record(1, 4, prefix='one-')
    other = _pending_record(1, 4, prefix='two-')
    projection = FreshReviewProjection((pending, other))
    with pytest.raises(FreshReviewError, match='^fresh-review-operation-conflict$'):
        withdraw_request(projection, request_id=pending.request.request_id,
                         operation_id=other.prepare_operation_id, fencing_epoch=1)
    withdrawn_projection = withdraw_request(projection, request_id=pending.request.request_id,
                                            operation_id='withdraw-shared', fencing_epoch=1)
    with pytest.raises(FreshReviewError, match='^fresh-review-operation-conflict$'):
        withdraw_request(withdrawn_projection, request_id=other.request.request_id,
                         operation_id='withdraw-shared', fencing_epoch=1)


def test_withdrawn_record_blocks_reserve_and_consume():
    pending = _pending_record(1, 4)
    withdrawn_projection = withdraw_request(FreshReviewProjection((pending,)), request_id=pending.request.request_id,
                                            operation_id='withdraw-1', fencing_epoch=1)
    request = pending.request
    intent, payload = canonical_digest('dispatch'), canonical_digest({'input': request.input_digest})
    with pytest.raises(FreshReviewError, match='^fresh-review-request-withdrawn$'):
        reserve_request(withdrawn_projection, request, operation_id='dispatch-1', intent_digest=intent, payload_digest=payload)
    with pytest.raises(FreshReviewError, match='^fresh-review-request-withdrawn$'):
        consume_request(withdrawn_projection, request, operation_id='dispatch-1', intent_digest=intent,
                        payload_digest=payload, result={'status': 'blocked'})


def test_prepare_replay_on_withdrawn_request_is_rejected(completion_session, run_cli):
    from .test_issue879_completion_cli import _rewrite_fixture_document
    root, request = _prepare(completion_session, run_cli)

    def mutate(document):
        container = document.get('extensions', document)
        projection = decode_projection(container)
        withdrawn = withdraw_request(projection, request_id=request['request_id'],
                                     operation_id='withdraw-1', fencing_epoch=1)
        container['fresh_review'] = projection_document(withdrawn)
    _rewrite_fixture_document(root, mutate)

    replay = run_cli(*ARGS, cwd=root, env_extra={'MISSION_OPERATION_ID': 'prepare-one'})
    assert replay.returncode == 2, replay.stdout + replay.stderr
    assert 'fresh-review-request-withdrawn' in replay.stdout + replay.stderr

    status = run_cli('fresh-review', 'status', cwd=root)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)['requests'] == [{
        'request_id': request['request_id'], 'status': 'withdrawn',
        'reason': 'capacity-withdrawn', 'criterion_ids': request['criterion_ids'],
    }]


def test_kernel_prepare_rejects_withdrawn_replay_and_nonce_reuse():
    """prepare_request_state must fail closed even when _historical never ran.

    _historical (application layer) only guards same-operation retries; a
    brand-new operation that happens to reuse a withdrawn nonce/request_id
    reaches the kernel transition directly, so the kernel loop itself must
    reject both cases.
    """
    from dataclasses import replace as dc_replace
    from mission_kernel import decode_mission_state
    from mission_kernel.transitions import decide
    from mission_kernel.commands import PrepareFreshReview, FreshReviewInputEffectClaim
    from mission_kernel.json_codec import freeze_json_value
    from .mission_state_fixture_corpus import current_v5_open_state

    state = decode_mission_state(json.dumps(current_v5_open_state()).encode())
    record = _pending_record(1, 8, prefix='kernel-')
    withdrawn_projection = withdraw_request(FreshReviewProjection((record,)), request_id=record.request.request_id,
                                            operation_id='withdraw-1', fencing_epoch=1)
    # validate_projection_backing requires the raw document to agree with the
    # typed projection, so the extensions bag must carry the same tombstone.
    extensions = dict(state.extensions.thaw())
    extensions['fresh_review'] = projection_document(withdrawn_projection)
    withdrawn_state = dc_replace(state, fresh_review=withdrawn_projection,
                                 extensions=freeze_json_value(extensions))

    request = record.request
    ref = request.input_ref
    claim = FreshReviewInputEffectClaim(ref.kind, ref.relative_path, ref.digest, ref.size)
    # The withdrawn guard raises before the packet/contract body is ever read.
    replay_command = PrepareFreshReview(request, record.prepare_operation_id, record.prepare_intent_digest,
                                        record.prepare_payload_digest, freeze_json_value({'dummy': True}), claim)
    decision = decide(withdrawn_state, replay_command)
    assert not decision.accepted and decision.rejection.code == 'fresh-review-request-withdrawn'

    reuse_command = dc_replace(replay_command, operation_id='prepare-new')
    decision = decide(withdrawn_state, reuse_command)
    assert not decision.accepted and decision.rejection.code == 'fresh-review-nonce-reused'

    # Each identity is one-use on its own: a nonce or a request_id alone collides.
    for changed in ({'request_id': 'fresh-request-id'}, {'nonce': 'fresh-nonce'}):
        alone = dc_replace(reuse_command, request=dc_replace(request, **changed))
        decision = decide(withdrawn_state, alone)
        assert not decision.accepted and decision.rejection.code == 'fresh-review-nonce-reused', changed

    # The withdraw operation is consumed too: it cannot name a new prepare.
    fresh = dc_replace(request, request_id='fresh-request-id', nonce='fresh-nonce')
    taken = dc_replace(reuse_command, operation_id='withdraw-1', request=fresh)
    decision = decide(withdrawn_state, taken)
    assert not decision.accepted and decision.rejection.code == 'fresh-review-operation-conflict'


def test_historical_lookup_treats_the_withdraw_operation_as_consumed():
    from mission_application.fresh_review import _historical
    record = _pending_record(1, 8)
    projection = withdraw_request(FreshReviewProjection((record,)), request_id=record.request.request_id,
                                  operation_id='withdraw-1', fencing_epoch=1)
    state = {'fresh_review': projection_document(projection)}
    with pytest.raises(FreshReviewError, match='^fresh-review-operation-conflict$'):
        _historical(state, 'withdraw-1', record.prepare_intent_digest, record.prepare_payload_digest)
    with pytest.raises(FreshReviewError, match='^fresh-review-request-withdrawn$'):
        _historical(state, record.prepare_operation_id, record.prepare_intent_digest, record.prepare_payload_digest)
    assert _historical(state, 'unrelated-op', record.prepare_intent_digest, record.prepare_payload_digest) is None


def test_decoder_uniqueness_spans_withdrawn_and_pending():
    pending = _pending_record(1, 4, prefix='one-')
    other = _pending_record(1, 4, prefix='two-')
    withdrawn_projection = withdraw_request(FreshReviewProjection((pending, other)), request_id=pending.request.request_id,
                                            operation_id='withdraw-1', fencing_epoch=1)
    document = projection_document(withdrawn_projection)
    assert decode_projection({'fresh_review': document}) == withdrawn_projection

    collisions = [
        ('request_id', other.request.request_id),
        ('nonce', other.request.nonce),
        ('prepare_operation_id', other.prepare_operation_id),
    ]
    for field, value in collisions:
        tampered = json.loads(json.dumps(document))
        tampered['requests'][0][field] = value
        with pytest.raises(FreshReviewError, match='^fresh-review-identity-reused$'):
            decode_projection({'fresh_review': tampered})

    tampered = json.loads(json.dumps(document))
    tampered['requests'][0]['withdraw_operation_id'] = other.prepare_operation_id
    with pytest.raises(FreshReviewError, match='^fresh-review-identity-reused$'):
        decode_projection({'fresh_review': tampered})


def test_d1_saved_state_without_withdrawn_records_still_decodes():
    """E0b must not narrow the D1 shape that is already persisted (pending/reserved/consumed)."""
    saved = projection_document(_pure_projection())
    assert decode_projection({'fresh_review': saved}) == _pure_projection()


def test_criterion_order_and_record_position_are_preserved():
    first = _pending_record(1, 4, prefix='first-')
    middle = _pending_record(1, 4, prefix='mid-')
    middle = FreshReviewRecord(
        decode_request({**_as_document(middle.request), 'criterion_ids': ['zz-crit', 'aa-crit'],
                        'candidate_bindings': [
                            {'criterion_id': cid, 'role': 'verification', 'command_id': 'command-1',
                             'definition_digest': ADAPTER, 'snapshot_digest': ADAPTER}
                            for cid in ('zz-crit', 'aa-crit')]}),
        middle.prepare_operation_id, middle.prepare_intent_digest, middle.prepare_payload_digest)
    last = _pending_record(1, 4, prefix='last-')
    projection = FreshReviewProjection((first, middle, last))
    result = withdraw_request(projection, request_id=middle.request.request_id,
                              operation_id='withdraw-middle', fencing_epoch=1)
    assert len(result.requests) == 3
    assert result.requests[0] == first
    assert result.requests[2] == last
    tombstone = result.requests[1]
    assert isinstance(tombstone, WithdrawnFreshReviewRecord)
    assert tombstone.criterion_ids == ('zz-crit', 'aa-crit')  # Not re-sorted.


def _as_document(request):
    from mission_kernel.fresh_review import request_document
    return request_document(request)


def _withdrawn_document():
    pending = _pending_record(1, 4)
    withdrawn = _withdraw(pending, operation_id='w' * 128, fencing_epoch=FRESH_REVIEW_INT_MAX)
    return dict(projection_document(FreshReviewProjection((withdrawn,)))['requests'][0])


@pytest.mark.parametrize('field,bad,code', [
    ('status', 'bogus', 'record-invalid'),
    ('request_id', 'x' * 129, 'identity-invalid'),
    ('request_id', 123, 'identity-invalid'),
    ('nonce', '../escape', 'identity-invalid'),
    ('prepare_operation_id', '', 'identity-invalid'),
    ('request_digest', 'sha256:' + 'a' * 63, 'digest-invalid'),
    ('request_digest', 'not-a-digest', 'digest-invalid'),
    ('criterion_ids', [], 'list-invalid'),
    ('criterion_ids', ['dup', 'dup'], 'list-invalid'),
    ('criterion_ids', 'not-a-list', 'list-invalid'),
    ('reason', 'other-reason', 'reason-invalid'),
    ('withdraw_operation_id', 'x' * 129, 'identity-invalid'),
    ('withdraw_fencing_epoch', -1, 'fence-invalid'),
    ('withdraw_fencing_epoch', FRESH_REVIEW_INT_MAX + 1, 'fence-invalid'),
    ('withdraw_fencing_epoch', True, 'fence-invalid'),
    ('withdraw_fencing_epoch', '1', 'fence-invalid'),
])
def test_withdrawn_record_fields_are_closed_and_reason_coded(field, bad, code):
    raw = _withdrawn_document()
    raw[field] = bad
    with pytest.raises(FreshReviewError, match='^fresh-review-' + code + '$'):
        decode_projection({'fresh_review': {'schema': PROJECTION_SCHEMA, 'requests': [raw]}})


@pytest.mark.parametrize('missing_field', list(WithdrawnFreshReviewRecord.__dataclass_fields__))
def test_withdrawn_record_rejects_missing_fields(missing_field):
    raw = _withdrawn_document()
    del raw[missing_field]
    with pytest.raises(FreshReviewError, match='^fresh-review-record-invalid$'):
        decode_projection({'fresh_review': {'schema': PROJECTION_SCHEMA, 'requests': [raw]}})


def test_withdrawn_record_rejects_extra_fields():
    raw = _withdrawn_document()
    raw['extra'] = True
    with pytest.raises(FreshReviewError, match='^fresh-review-record-invalid$'):
        decode_projection({'fresh_review': {'schema': PROJECTION_SCHEMA, 'requests': [raw]}})
    raw = _withdrawn_document()
    raw['result'] = None  # a pending/reserved/consumed-only field
    with pytest.raises(FreshReviewError, match='^fresh-review-record-invalid$'):
        decode_projection({'fresh_review': {'schema': PROJECTION_SCHEMA, 'requests': [raw]}})


@pytest.mark.parametrize('epoch', [0, FRESH_REVIEW_INT_MAX])
def test_withdraw_fencing_epoch_accepts_its_closed_range(epoch):
    pending = _pending_record(1, 4)
    withdrawn = _withdraw(pending, fencing_epoch=epoch)
    assert withdrawn.withdraw_fencing_epoch == epoch
    document = projection_document(FreshReviewProjection((withdrawn,)))
    assert decode_projection({'fresh_review': document}).requests[0].withdraw_fencing_epoch == epoch


@pytest.mark.parametrize('epoch', [-1, FRESH_REVIEW_INT_MAX + 1, True, False])
def test_withdraw_request_rejects_fencing_epoch_outside_its_range(epoch):
    # True/False are valid ints in Python (1/0) but must still be rejected as bool.
    pending = _pending_record(1, 4)
    projection = FreshReviewProjection((pending,))
    with pytest.raises(FreshReviewError, match='^fresh-review-fence-invalid$'):
        withdraw_request(projection, request_id=pending.request.request_id, operation_id='op', fencing_epoch=epoch)


def test_withdrawn_projection_round_trips_through_encode_and_decode():
    pending = _pending_record(2, 16, prefix='rt-')
    withdrawn_projection = withdraw_request(FreshReviewProjection((pending,)), request_id=pending.request.request_id,
                                            operation_id='withdraw-rt', fencing_epoch=42)
    document = projection_document(withdrawn_projection)
    persisted = json.loads(json.dumps({'fresh_review': document}))
    assert decode_projection(persisted) == withdrawn_projection
    # Direct dataclass construction is not decoding; validate_projection_backing exercises both.
    from mission_kernel.fresh_review import validate_projection_backing
    validate_projection_backing(persisted, withdrawn_projection)


def test_decoder_rejects_withdraw_operation_reusing_its_own_or_an_earlier_prepare_id():
    # The reducer never produces either shape, so the decoder must not admit it.
    document = _withdrawn_document()
    document['withdraw_operation_id'] = document['prepare_operation_id']
    with pytest.raises(FreshReviewError, match='^fresh-review-operation-conflict$'):
        decode_projection({'fresh_review': {'schema': 'mission-fresh-review/1', 'requests': [document]}})

    earlier = _pending_record(1, 4, prefix='one-')
    later = _pending_record(1, 4, prefix='two-')
    projection = withdraw_request(FreshReviewProjection((earlier, later)), request_id=later.request.request_id,
                                  operation_id='withdraw-1', fencing_epoch=1)
    tampered = json.loads(json.dumps(projection_document(projection)))
    tampered['requests'][1]['withdraw_operation_id'] = earlier.prepare_operation_id
    with pytest.raises(FreshReviewError, match='^fresh-review-operation-conflict$'):
        decode_projection({'fresh_review': tampered})


def test_withdraw_resend_with_a_different_fence_is_a_conflict_not_a_replay():
    pending = _pending_record(1, 4)
    projection = withdraw_request(FreshReviewProjection((pending,)), request_id=pending.request.request_id,
                                  operation_id='withdraw-1', fencing_epoch=7)
    with pytest.raises(FreshReviewError, match='^fresh-review-operation-conflict$'):
        withdraw_request(projection, request_id=pending.request.request_id,
                         operation_id='withdraw-1', fencing_epoch=8)
