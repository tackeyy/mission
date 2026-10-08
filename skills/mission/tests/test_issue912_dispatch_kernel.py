"""Inert D2c transition and capacity contracts (split A)."""
import pytest

def test_shared_launch_boundary_rejects_changed_bindings_and_unenforced_capabilities():
    import copy
    from mission_kernel.fresh_review_dispatch import validate_launch
    from mission_kernel.fresh_review import FreshReviewError, canonical_digest, request_document
    from .test_issue895_fresh_review import _pure_projection, ADAPTER
    from .test_issue909_fresh_review_receipts import launch_document
    request = _pure_projection().requests[0].request
    raw = launch_document()
    raw['request_digest'] = canonical_digest(request_document(request))
    dispatch = dict(operation_id=raw['operation_id'], fencing_epoch=raw['fencing_epoch'], parent_identity='parent')
    assert validate_launch(request, dispatch, raw)[1] is True
    # 55 hostile inputs at the shared boundary, rather than CLI table copies.
    for field in ('request_id', 'request_digest', 'nonce', 'operation_id', 'fencing_epoch',
                  'adapter_registration_digest', 'parent_identity', 'child_identity', 'context_identity',
                  'received_input_digest', 'enforced_tools'):
        for bad in (None, {}, [], True, 'foreign'):
            value = copy.deepcopy(raw)
            value[field] = bad
            # Different observable child/context ids are legitimate host evidence.
            if bad == 'foreign' and field in ('child_identity', 'context_identity') or bad == [] and field == 'enforced_tools':
                assert validate_launch(request, dispatch, value)[1]
            else:
                with pytest.raises(FreshReviewError):
                    validate_launch(request, dispatch, value)
    value = copy.deepcopy(raw)
    value['enforced_tools'] = ['read-candidate']  # Request grants no tools.
    with pytest.raises(FreshReviewError, match='capability-unenforceable'):
        validate_launch(request, dispatch, value)
    value = copy.deepcopy(raw)
    value['received_input_digest'] = ADAPTER
    with pytest.raises(FreshReviewError, match='launch-binding-mismatch'):
        validate_launch(request, dispatch, value)



def test_dispatch_decoder_uses_the_e0_closed_shape():
    from dataclasses import replace
    from mission_kernel.fresh_review import FreshReviewError, projection_document, decode_projection, canonical_digest
    from mission_kernel.fresh_review_dispatch import reservation_id_for_operation
    from mission_kernel.json_codec import freeze_json_value
    from .test_issue895_fresh_review import _pure_projection
    from .test_issue917_fresh_review_bounds import maximum_intent
    projection = _pure_projection()
    record = projection.requests[0]
    dispatch = maximum_intent()
    dispatch.update(invocation_id='inv_' + canonical_digest(record.request.request_id)[7:39],
                    operation_id='dispatch', outbound_packet_digest=record.request.input_digest,
                    iteration=record.request.iteration,
                    reservation_id=reservation_id_for_operation('dispatch'), budget_class='verification')
    projection = replace(projection, requests=(replace(record, status='dispatch-unknown',
        operation_id='dispatch', intent_digest=record.prepare_intent_digest,
        payload_digest=record.prepare_payload_digest, dispatch=freeze_json_value(dispatch)),))
    raw = {'fresh_review': projection_document(projection)}
    assert decode_projection(raw) == projection
    for field, value in (
        ('reservation_id', 'dispatch'),
        ('reservation_id', reservation_id_for_operation('foreign-operation')),
        ('budget_class', 'arbitrary'), ('budget_class', 'repair'), ('budget_class', 'final'),
    ):
        invalid = dict(dispatch, **{field: value})
        invalid_projection = replace(projection, requests=(replace(projection.requests[0], dispatch=freeze_json_value(invalid)),))
        with pytest.raises(FreshReviewError, match='fresh-review-dispatch-invalid'):
            decode_projection({'fresh_review': projection_document(invalid_projection)})


def test_dispatch_statuses_release_only_the_completed_stage_reservation():
    from mission_kernel import state_capacity as capacity
    table = capacity._FRESH_REVIEW_FIXED_RESERVE_BY_STATUS
    assert table['dispatch-unknown'] == (table['reserved'] + 3455
                                         + capacity.FRESH_REVIEW_LAUNCH_OPERATION_STAGE_DELTA)
    assert capacity.FRESH_REVIEW_LAUNCH_OPERATION_STAGE_DELTA == 153
    assert table['running'] == table['reserved']
    assert table['blocked'] == table['abandoned-unknown'] == 0


def test_pending_projection_keeps_main_wire_bytes_and_omits_absent_dispatch_fields():
    import hashlib
    from mission_kernel.fresh_review import projection_document
    from mission_kernel.json_codec import encode_json_value, freeze_json_value
    from .test_issue895_fresh_review import _pure_projection

    document = projection_document(_pure_projection())
    record = document['requests'][0]
    assert not {'dispatch', 'launch', 'independent'} & record.keys()
    encoded = encode_json_value(freeze_json_value(document))
    assert len(encoded) == 1863
    assert hashlib.sha256(encoded).hexdigest() == '1cac8a137c0272e621ddc707e49c62f32a7dabdced1581172bbc6fb9b7cd54c3'


@pytest.mark.parametrize('status', ('blocked', 'abandoned-unknown'))
def test_terminal_without_lineage_reservation_has_zero_remaining_capacity_reservation(status):
    from mission_kernel import state_capacity as capacity
    from mission_kernel.fresh_review import projection_document, decode_projection, canonical_digest, request_document
    from mission_kernel.fresh_review_dispatch import reservation_id_for_operation
    from .test_issue895_fresh_review import _pure_projection
    from .test_issue909_fresh_review_receipts import terminal_document
    from .test_issue917_fresh_review_bounds import maximum_intent
    from .test_issue933_state_capacity_verdict import _v5_doc, canonical

    projection = _pure_projection()
    request = projection.requests[0].request
    dispatch = maximum_intent()
    dispatch.update(invocation_id='inv_' + canonical_digest(request.request_id)[7:39],
                    operation_id='dispatch', outbound_packet_digest=request.input_digest,
                    iteration=request.iteration, fencing_epoch=1, parent_identity='parent',
                    reservation_id=reservation_id_for_operation('dispatch'), budget_class='verification')
    result = terminal_document(status)
    result.update(request_id=request.request_id, request_digest=canonical_digest(request_document(request)),
                  nonce=request.nonce, dispatch_operation_id='dispatch', dispatch_fencing_epoch=1,
                  commit_operation_id='commit', commit_fencing_epoch=1,
                  candidate_digest=request.candidate_digest)
    launch = None
    if status == 'abandoned-unknown':
        from .test_issue909_fresh_review_receipts import launch_document
        launch = launch_document()
        launch.update(request_digest=canonical_digest(request_document(request)), operation_id='dispatch',
                      fencing_epoch=1, parent_identity='parent', received_input_digest=request.input_digest)
        result.update(launch_receipt=launch, launch_digest=canonical_digest(launch))
    raw = projection_document(projection)
    record = raw['requests'][0]
    record.update(status=status, operation_id='dispatch', intent_digest='sha256:' + 'a' * 64,
                  payload_digest='sha256:' + 'b' * 64, dispatch=dispatch, result=result)
    if launch is not None:
        record.update(launch=launch, independent=True)
    else:
        record.pop('launch', None)
        record.pop('independent', None)
    terminal = decode_projection({'fresh_review': raw}).requests[0]
    document = _v5_doc(requests=(terminal,))
    metrics = capacity._metrics(document, canonical(document))
    assert metrics.reserved == 0


def test_withdraw_command_obeys_fence_and_keeps_one_use_identity():
    from mission_kernel.commands import WithdrawFreshReviewRequest
    from mission_kernel.fresh_review_dispatch import dispatch_state
    from mission_kernel.fresh_review import FreshReviewError
    from mission_kernel import decode_mission_state
    from .test_issue895_fresh_review import _pure_projection
    import json
    from mission_kernel.fresh_review import projection_document
    from .mission_state_fixture_corpus import current_v5_open_state
    document = current_v5_open_state()
    document["lease"].update(owner_session_id="test", lease_id="lease", fencing_epoch=2,
                              lease_expires_at="9999-01-01T00:00:00Z")
    document["extensions"]["fresh_review"] = projection_document(_pure_projection())
    state = decode_mission_state(json.dumps(document).encode())
    request = state.fresh_review.requests[0].request
    with pytest.raises(FreshReviewError, match='stale-fence'):
        dispatch_state(state, WithdrawFreshReviewRequest(request.request_id, 'withdraw', 1))
    target = dispatch_state(state, WithdrawFreshReviewRequest(request.request_id, 'withdraw', 2))
    record = target.fresh_review.requests[0]
    assert record.status == 'withdrawn' and record.nonce == request.nonce


def test_kernel_binds_begin_launch_and_terminal_to_one_dispatch_operation():
    import copy
    import json
    from dataclasses import replace
    from acceptance_contract import canonical_contract_digest
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import BeginFreshReviewDispatch, CommitFreshReviewResult, RecordFreshReviewLaunch
    from mission_kernel.fresh_review import FreshReviewError, FreshReviewProjection, FreshReviewRecord, canonical_digest, decode_projection, projection_document, request_document
    from mission_kernel.fresh_review_dispatch import dispatch_state, reservation_id_for_operation
    from mission_kernel.json_codec import freeze_json_value
    from .test_issue895_fresh_review import _pure_projection
    from .test_issue909_fresh_review_receipts import launch_document, terminal_document
    from .test_issue917_fresh_review_bounds import maximum_intent
    from .test_issue936_state_capacity_reservation import _flat_doc, _minimal_contract

    document = _flat_doc(contract=_minimal_contract())
    record = _pure_projection().requests[0]
    request = replace(record.request,
                      contract_digest=canonical_contract_digest(document['acceptance_contract']), iteration=1)
    document['fresh_review'] = projection_document(FreshReviewProjection((replace(record, request=request),)))
    state = decode_mission_state(json.dumps(document).encode())
    request = state.fresh_review.requests[0].request
    dispatch = maximum_intent()
    dispatch.update(invocation_id='inv_' + canonical_digest(request.request_id)[7:39],
                    operation_id='dispatch', outbound_packet_digest=request.input_digest,
                    iteration=request.iteration, fencing_epoch=1, parent_identity='parent',
                    reservation_id=reservation_id_for_operation('dispatch'), budget_class='verification')
    launch = launch_document()
    launch.update(request_digest=canonical_digest(request_document(request)), operation_id='dispatch',
                  fencing_epoch=1, parent_identity='parent', received_input_digest=request.input_digest)
    begin = BeginFreshReviewDispatch(
        request.request_id, 'dispatch', 1, canonical_digest('intent'), canonical_digest('payload'),
        freeze_json_value(dispatch), request.candidate_digest)
    with pytest.raises(FreshReviewError, match='fresh-review-not-dispatch-unknown'):
        dispatch_state(state, RecordFreshReviewLaunch(
            request.request_id, 'dispatch', 1, freeze_json_value(launch), request.candidate_digest))
    state = dispatch_state(state, begin)
    assert state.fresh_review.requests[0].status == 'dispatch-unknown'
    assert state.fresh_review.requests[0].operation_id == 'dispatch'
    with pytest.raises(FreshReviewError, match='fresh-review-nonce-reused'):
        dispatch_state(state, begin)
    state = replace(state, lease=replace(state.lease, fencing_epoch=2))
    state = dispatch_state(state, RecordFreshReviewLaunch(
        request.request_id, 'reconcile', 2, freeze_json_value(launch), request.candidate_digest))
    assert state.fresh_review.requests[0].status == 'running'
    assert state.fresh_review.requests[0].launch_operation_id == 'reconcile'
    second_request = replace(request, request_id='request-2', nonce='nonce-2')
    second_record = FreshReviewRecord(second_request, 'prepare-two',
                                      canonical_digest('second-intent'), canonical_digest('second-payload'))
    with_second = FreshReviewProjection((*state.fresh_review.requests, second_record))
    second_document = state.legacy_passthrough.thaw()
    second_document['fresh_review'] = projection_document(with_second)
    second_document['fencing_epoch'] = 2
    state_with_second = decode_mission_state(json.dumps(second_document).encode())
    second_dispatch = dict(dispatch, operation_id='reconcile', fencing_epoch=2,
        invocation_id='inv_' + canonical_digest(second_request.request_id)[7:39],
        reservation_id=reservation_id_for_operation('reconcile'))
    with pytest.raises(FreshReviewError, match='fresh-review-operation-conflict'):
        dispatch_state(state_with_second, BeginFreshReviewDispatch(
            second_request.request_id, 'reconcile', 2, canonical_digest('second-dispatch-intent'),
            canonical_digest('second-dispatch-payload'), freeze_json_value(second_dispatch), second_request.candidate_digest))
    with pytest.raises(FreshReviewError, match='fresh-review-not-dispatch-unknown'):
        dispatch_state(state, RecordFreshReviewLaunch(
            request.request_id, 'reconcile', 2, freeze_json_value(launch), request.candidate_digest))
    receipt = terminal_document('abandoned-unknown')
    receipt.update(request_id=request.request_id, request_digest=canonical_digest(request_document(request)),
                   nonce=request.nonce, dispatch_operation_id='dispatch', dispatch_fencing_epoch=1,
                   commit_operation_id='reconcile', commit_fencing_epoch=2,
                   candidate_digest=request.candidate_digest, launch_receipt=launch,
                   launch_digest=canonical_digest(launch))
    state = dispatch_state(state, CommitFreshReviewResult(
        request.request_id, 'reconcile', 2, freeze_json_value(receipt)))
    assert state.fresh_review.requests[0].status == 'abandoned-unknown'
    wire = projection_document(state.fresh_review)
    assert decode_projection({'fresh_review': wire}) == state.fresh_review
    for field, value in (('launch_operation_id', 'x' * 129),
                         ('launch_operation_id', True),
                         ('launch_operation_id', 'prepare-one')):
        invalid = copy.deepcopy(wire)
        invalid['requests'][0][field] = value
        with pytest.raises(FreshReviewError):
            decode_projection({'fresh_review': invalid})
    invalid = copy.deepcopy(wire)
    invalid['requests'][0].pop('launch')
    with pytest.raises(FreshReviewError, match='fresh-review-record-invalid'):
        decode_projection({'fresh_review': invalid})
    second = copy.deepcopy(wire['requests'][0])
    second['request'].update(request_id='request-2', nonce='nonce-2')
    second.update(prepare_operation_id='reconcile', status='pending', operation_id=None,
                  intent_digest=None, payload_digest=None, result=None)
    for field in ('dispatch', 'launch', 'launch_operation_id', 'independent'):
        second.pop(field, None)
    for records in ((wire['requests'][0], second), (second, wire['requests'][0])):
        with pytest.raises(FreshReviewError):
            decode_projection({'fresh_review': {'schema': wire['schema'], 'requests': list(records)}})
    with pytest.raises(FreshReviewError, match='fresh-review-consumed'):
        dispatch_state(state, CommitFreshReviewResult(
            request.request_id, 'reconcile', 2, freeze_json_value(receipt)))


@pytest.mark.parametrize('status', ['dispatch-unknown', 'running', 'blocked', 'abandoned-unknown'])
def test_actual_maximum_dispatch_records_fit_e0_reservations(status):
    from dataclasses import replace
    from mission_kernel.fresh_review import (
        projection_document, decode_projection, canonical_digest, request_document,
        FreshReviewProjection, ToolCapability, FRESH_REVIEW_INT_MAX,
    )
    from mission_kernel.fresh_review_receipts import FRESH_REVIEW_MAX_ENCODED_BYTES
    from mission_kernel.fresh_review_dispatch import reservation_id_for_operation
    from mission_kernel.json_codec import encode_json_value, freeze_json_value
    from mission_kernel.state_capacity import FRESH_REVIEW_DISPATCH_STAGE_DELTA, FRESH_REVIEW_TERMINAL_STAGE_DELTA
    from .test_issue895_fresh_review import _pure_projection
    from .test_issue917_fresh_review_bounds import maximum_intent, maximum_launch, maximum_terminal
    record = _pure_projection().requests[0]
    request = replace(record.request, request_id='r'*128, nonce='n'*128, iteration=FRESH_REVIEW_INT_MAX,
                      allowed_tools=tuple(ToolCapability))
    record = replace(record, request=request, prepare_operation_id='p'*128)
    digest = canonical_digest(request_document(request))
    dispatch = maximum_intent()
    dispatch.update(invocation_id='inv_' + canonical_digest(request.request_id)[7:39],
                    operation_id='o'*128, outbound_packet_digest=request.input_digest,
                    reservation_id=reservation_id_for_operation('o' * 128), budget_class='verification')
    launch = maximum_launch()
    launch.update(request_id=request.request_id, nonce=request.nonce, request_digest=digest,
                  operation_id=dispatch['operation_id'], parent_identity=dispatch['parent_identity'],
                  adapter_registration_digest=request.adapter_registration_digest,
                  received_input_digest=request.input_digest)
    encode = lambda raw: encode_json_value(freeze_json_value(raw))
    assert len(encode(dispatch)) <= FRESH_REVIEW_MAX_ENCODED_BYTES['intent']
    independent = launch['context_mode'] == 'fresh' and launch['child_identity'] != launch['parent_identity']
    running = {'status': 'running', 'dispatch': dispatch, 'launch_receipt': launch,
               'launch_digest': canonical_digest(launch), 'independent': independent}
    assert len(encode(running)) <= FRESH_REVIEW_MAX_ENCODED_BYTES['running']
    result = None
    launched = status in ('running', 'abandoned-unknown')
    if status in ('blocked', 'abandoned-unknown'):
        result = maximum_terminal(status)
        result.update(request_id=request.request_id, nonce=request.nonce, request_digest=digest,
                      dispatch_operation_id=dispatch['operation_id'], candidate_digest=request.candidate_digest)
        if launched:
            result.update(launch_receipt=launch, launch_digest=canonical_digest(launch))
        assert len(encode(result)) <= FRESH_REVIEW_MAX_ENCODED_BYTES[status]
    updated = replace(record, status=status, operation_id=dispatch['operation_id'],
        intent_digest=record.prepare_intent_digest, payload_digest=record.prepare_payload_digest,
        dispatch=freeze_json_value(dispatch), launch=freeze_json_value(launch) if launched else None,
        launch_operation_id='l' * 128 if launched else None,
        independent=independent if launched else None, result=freeze_json_value(result) if result else None)
    raw = {'fresh_review': projection_document(FreshReviewProjection((updated,)))}
    assert decode_projection(raw).requests == (updated,)
    pending = {'fresh_review': projection_document(FreshReviewProjection((record,)))}
    delta = len(encode(raw)) - len(encode(pending))
    bound = FRESH_REVIEW_DISPATCH_STAGE_DELTA + (FRESH_REVIEW_TERMINAL_STAGE_DELTA if result else 0)
    assert delta <= bound


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'abandoned-unknown'])
@pytest.mark.parametrize('ended,accepted', [
    ('2026-01-01T00:00:00.000000Z', True),
    ('2026-01-01T00:00:01.000000Z', True),
    ('2025-12-31T23:59:59.000000Z', False),
])
def test_terminal_timestamps_compare_instants_and_allow_zero_duration(outcome, ended, accepted):
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    from .test_issue909_fresh_review_receipts import terminal_document
    raw = terminal_document(outcome)
    raw['ended_at'] = ended
    if accepted:
        assert decode_terminal_receipt(raw).ended_at == ended
    else:
        with pytest.raises(ValueError, match='^fresh-review-timestamp-order-invalid$'):
            decode_terminal_receipt(raw)


@pytest.mark.parametrize('status,launch_missing', [('blocked', False), ('abandoned-unknown', True)])
def test_terminal_variant_cannot_drop_a_persisted_launch(status, launch_missing):
    from dataclasses import replace
    from mission_kernel.fresh_review_dispatch import decode_dispatch_record
    from mission_kernel.fresh_review_dispatch import reservation_id_for_operation
    from mission_kernel.fresh_review import FreshReviewError, canonical_digest, request_document
    from .test_issue895_fresh_review import _pure_projection
    from .test_issue909_fresh_review_receipts import launch_document, terminal_document
    from .test_issue917_fresh_review_bounds import maximum_intent
    request = _pure_projection().requests[0].request
    dispatch = maximum_intent()
    dispatch.update(invocation_id='inv_' + canonical_digest(request.request_id)[7:39], operation_id='dispatch',
                    outbound_packet_digest=request.input_digest, iteration=request.iteration, fencing_epoch=2,
                    parent_identity='parent', reservation_id=reservation_id_for_operation('dispatch'),
                    budget_class='verification')
    launch = launch_document()
    launch['request_digest'] = canonical_digest(request_document(request))
    result = terminal_document(status, launched=not launch_missing)
    result['request_digest'] = launch['request_digest']
    fields = dict(request=request, operation_id='dispatch', status=status, dispatch=dispatch,
                  intent_digest='sha256:'+'a'*64, payload_digest='sha256:'+'a'*64,
                  launch=launch, independent=True, result=result)
    with pytest.raises(FreshReviewError, match='terminal-binding-mismatch'):
        decode_dispatch_record(fields)


def _launch_collision_fixture():
    from dataclasses import replace
    from acceptance_contract import canonical_contract_digest
    from mission_kernel.fresh_review import canonical_digest, request_document
    from mission_kernel.fresh_review_dispatch import reservation_id_for_operation
    from mission_kernel.json_codec import freeze_json_value
    from .test_issue895_fresh_review import _pure_projection
    from .test_issue909_fresh_review_receipts import launch_document
    from .test_issue917_fresh_review_bounds import maximum_intent
    from .test_issue936_state_capacity_reservation import _flat_doc, _minimal_contract

    document = _flat_doc(contract=_minimal_contract())
    pending = _pure_projection().requests[0]
    request = replace(pending.request,
        contract_digest=canonical_contract_digest(document['acceptance_contract']))
    dispatch = maximum_intent()
    dispatch.update(operation_id='dispatch', fencing_epoch=1, parent_identity='parent',
        invocation_id='inv_' + canonical_digest(request.request_id)[7:39],
        outbound_packet_digest=request.input_digest, iteration=request.iteration,
        reservation_id=reservation_id_for_operation('dispatch'), budget_class='verification')
    target = replace(pending, request=request, status='dispatch-unknown', operation_id='dispatch',
        intent_digest=pending.prepare_intent_digest, payload_digest=pending.prepare_payload_digest,
        dispatch=freeze_json_value(dispatch))
    other = replace(pending, prepare_operation_id='other-prepare',
        request=replace(request, request_id='request-other', nonce='nonce-other'))
    launch = launch_document()
    launch.update(request_digest=canonical_digest(request_document(request)), operation_id='dispatch',
        fencing_epoch=1, parent_identity='parent', received_input_digest=request.input_digest)
    document['fencing_epoch'] = 2
    return document, target, other, launch


@pytest.mark.parametrize('owner', ('prepare', 'dispatch', 'withdraw', 'commit', 'launch'))
def test_takeover_launch_rejects_every_other_record_operation(owner):
    import json
    from dataclasses import replace
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import RecordFreshReviewLaunch
    from mission_kernel.fresh_review import (FreshReviewError, FreshReviewProjection,
        canonical_digest, projection_document, request_document, withdraw_request)
    from mission_kernel.fresh_review_dispatch import dispatch_state, reservation_id_for_operation
    from mission_kernel.json_codec import freeze_json_value
    from .test_issue909_fresh_review_receipts import terminal_document

    document, target, other, launch = _launch_collision_fixture()
    if owner == 'withdraw':
        other = withdraw_request(FreshReviewProjection((other,)), request_id=other.request.request_id,
            operation_id='other-withdraw', fencing_epoch=2).requests[0]
    elif owner != 'prepare':
        dispatch = target.dispatch.thaw()
        dispatch.update(operation_id='other-dispatch',
            invocation_id='inv_' + canonical_digest(other.request.request_id)[7:39],
            reservation_id=reservation_id_for_operation('other-dispatch'))
        other = replace(other, status='dispatch-unknown', operation_id='other-dispatch',
            intent_digest=target.intent_digest, payload_digest=target.payload_digest,
            dispatch=freeze_json_value(dispatch))
        if owner == 'commit':
            receipt = terminal_document('blocked')
            receipt.update(request_id=other.request.request_id, nonce=other.request.nonce,
                request_digest=canonical_digest(request_document(other.request)),
                dispatch_operation_id='other-dispatch', dispatch_fencing_epoch=1,
                commit_operation_id='other-commit', commit_fencing_epoch=2,
                candidate_digest=other.request.candidate_digest)
            other = replace(other, status='blocked', result=freeze_json_value(receipt))
        elif owner == 'launch':
            other_launch = dict(launch, request_id=other.request.request_id, nonce=other.request.nonce,
                request_digest=canonical_digest(request_document(other.request)), operation_id='other-dispatch',
                child_identity='other-child', context_identity='other-context')
            other = replace(other, status='running', launch=freeze_json_value(other_launch),
                independent=True, launch_operation_id='other-launch')
    document['fresh_review'] = projection_document(FreshReviewProjection((target, other)))
    state = decode_mission_state(json.dumps(document).encode())
    with pytest.raises(FreshReviewError, match='^fresh-review-operation-conflict$'):
        dispatch_state(state, RecordFreshReviewLaunch(target.request.request_id, 'other-' + owner,
            2, freeze_json_value(launch), target.request.candidate_digest))
    assert state.fresh_review.requests[0].status == 'dispatch-unknown'


def test_capacity_withdrawal_skips_the_saved_launch_operation_identity():
    import json
    from mission_kernel import decode_mission_state, state_capacity as capacity
    from mission_kernel.commands import RecordFreshReviewLaunch
    from mission_kernel.fresh_review import FreshReviewProjection, projection_document
    from mission_kernel.fresh_review_dispatch import dispatch_state
    from mission_kernel.json_codec import encode_json_value, freeze_json_value

    document, target, other, launch = _launch_collision_fixture()
    document['fresh_review'] = projection_document(FreshReviewProjection((target, other)))
    state = decode_mission_state(json.dumps(document).encode())
    state = dispatch_state(state, RecordFreshReviewLaunch(target.request.request_id, '0' * 128,
        2, freeze_json_value(launch), target.request.candidate_digest))
    document = state.legacy_passthrough.thaw()
    encode = lambda value: len(encode_json_value(freeze_json_value(value)))
    before = encode(document)
    assert capacity._withdraw_all_pending_len(document, before, encode=encode) < before
