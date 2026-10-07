"""E0a: bounded receipt bytes are inputs to the later capacity reservation."""
import pytest

from mission_kernel.fresh_review_receipts import decode_terminal_receipt
from .test_issue909_fresh_review_receipts import terminal_document, evidence


def test_findings_capacity_accepts_61_and_rejects_62():
    raw = terminal_document()
    raw['findings'] = [dict(evidence('fresh-review-finding'),
        digest='sha256:' + format(n, '064x'),
        relative_path='evidence/fresh-review/' + format(n, '064x') + '.json')
        for n in range(61)]
    assert len(decode_terminal_receipt(raw).findings) == 61
    raw['findings'].append(dict(evidence('fresh-review-finding'),
        digest='sha256:' + 'f' * 64, relative_path='evidence/fresh-review/' + 'f' * 64 + '.json'))
    with pytest.raises(ValueError, match='^fresh-review-findings-over-limit$'):
        decode_terminal_receipt(raw)


@pytest.mark.parametrize('outcome,path,maximum,code', [
    ('completed', ('output_ref', 'size'), 262144, 'evidence-ref-invalid'),
    ('completed', ('coverage_receipt', 'evidence_ref', 'size'), 262144, 'evidence-ref-invalid'),
    ('completed', ('findings', 0, 'size'), 262144, 'evidence-ref-invalid'),
    ('failed', ('output_ref', 'size'), 262144, 'evidence-ref-invalid'),
    ('failed', ('dispatch_fencing_epoch',), 2**63-1, 'fence-invalid'),
    ('failed', ('commit_fencing_epoch',), 2**63-1, 'fence-invalid'),
    *[('failed', ('budget_used', key), 2**63-1, 'budget-used-invalid')
      for key in ('wall_time_sec', 'tool_calls', 'replays', 'output_bytes')],
    ('failed', ('launch_receipt', 'fencing_epoch'), 2**63-1, 'fence-invalid'),
])
def test_numeric_fields_accept_the_limit_and_reject_one_more(outcome, path, maximum, code):
    from mission_kernel.fresh_review import canonical_digest
    raw = terminal_document(outcome)
    raw['findings'] = [evidence('fresh-review-finding')] if outcome == 'completed' else None
    if outcome != 'completed':
        del raw['findings']
    if path[-1] == 'fencing_epoch' or path[-1] == 'dispatch_fencing_epoch':
        raw['dispatch_fencing_epoch'] = raw['commit_fencing_epoch'] = maximum
        raw['launch_receipt']['fencing_epoch'] = maximum
    target = raw
    for key in path[:-1]:
        target = target[key]
    for excess in (0, 1):
        target[path[-1]] = maximum + excess
        raw['launch_digest'] = canonical_digest(raw['launch_receipt'])
        if excess:
            with pytest.raises(ValueError, match='^fresh-review-' + code + '$'):
                decode_terminal_receipt(raw)
        else:
            decode_terminal_receipt(raw)


@pytest.mark.parametrize('field', ['started_at', 'ended_at'])
@pytest.mark.parametrize('bad', ['2026-01-01T00:00:00+00:00',
    '2026-01-01T00:00:00.0000000Z', '2026-01-01T00:00:00.00000Z',
    '2026-01-01T00:00:00.000000+00:00', '2026-01-01 00:00:00.000000Z',
    '2026-02-30T00:00:00.000000Z', '2026-01-01T00:00:00.000000Z\n'])
def test_timestamps_require_exact_canonical_shape_and_valid_calendar(field, bad):
    from mission_kernel.fresh_review_receipts import decode_launch_receipt
    from .test_issue909_fresh_review_receipts import launch_document
    raw = launch_document() if field == 'started_at' else terminal_document('blocked')
    decoder = decode_launch_receipt if field == 'started_at' else decode_terminal_receipt
    raw[field] = '2026-01-01T00:00:00.000000Z'
    decoder(raw)
    raw[field] = bad
    with pytest.raises(ValueError, match='^fresh-review-timestamp-invalid$'):
        decoder(raw)


def test_import_over_limit_failure_requires_durable_output_pair():
    raw = terminal_document('failed')
    raw['reason'] = 'output-over-import-limit'
    assert decode_terminal_receipt(raw).reason == 'output-over-import-limit'
    for field in ('output_ref', 'output_digest'):
        missing = {key: value for key, value in raw.items() if key != field}
        with pytest.raises(ValueError, match='^fresh-review-terminal-shape-invalid$'):
            decode_terminal_receipt(missing)
    missing = {key: value for key, value in raw.items() if key not in ('output_ref', 'output_digest')}
    with pytest.raises(ValueError, match='^fresh-review-terminal-shape-invalid$'):
        decode_terminal_receipt(missing)


def maximum_intent():
    return dict(invocation_id='i'*128, operation_id='o'*128,
        outbound_packet_digest='sha256:'+'f'*64, iteration=2**63-1,
        fencing_epoch=2**63-1, status='dispatch-unknown', lifecycle_state='dispatch-unknown',
        parent_identity='p'*128, adapter_id='a'*128,
        deadline_at='9999-12-31T23:59:59.999999Z', reservation_id='r'*128,
        budget_class='\x00'*128)


@pytest.mark.parametrize('field,bad,code', [
    *[(key, 'x'*129, 'identity-invalid') for key in
      ('invocation_id', 'operation_id', 'parent_identity', 'adapter_id', 'reservation_id')],
    ('outbound_packet_digest', 'sha256:'+'f'*65, 'digest-invalid'),
    ('iteration', 2**63, 'dispatch-invalid'),
    ('fencing_epoch', 2**63, 'fence-invalid'),
    ('deadline_at', '9999-12-31T23:59:59.9999990Z', 'timestamp-invalid'),
    ('budget_class', 'x'*129, 'budget-class-invalid'),
    ('budget_class', 'é', 'budget-class-invalid'),
    ('status', 'future', 'dispatch-invalid'),
    ('lifecycle_state', 'future', 'dispatch-invalid'),
])
def test_dispatch_shape_caps_each_field_without_fixing_budget_classes(field, bad, code):
    from mission_kernel.fresh_review_receipts import validate_dispatch_intent
    raw = maximum_intent()
    validate_dispatch_intent(raw)
    with pytest.raises(ValueError, match='^fresh-review-' + code + '$'):
        validate_dispatch_intent({**raw, field: bad})
    if field == 'budget_class':
        validate_dispatch_intent({**raw, field: 'future/class'})


def maximum_launch():
    from .test_issue909_fresh_review_receipts import launch_document
    raw = launch_document()
    for key in ('request_id', 'nonce', 'operation_id', 'parent_identity', 'child_identity', 'context_identity'):
        raw[key] = key[0]*128
    raw.update(fencing_epoch=2**63-1, context_mode='shared',
        started_at='9999-12-31T23:59:59.999999Z',
        enforced_tools=['read-candidate', 'replay-verifier'],
        enforced_budget=dict(wall_time_sec=300, max_tool_calls=64, max_replays=16,
                             max_output_bytes=262144, max_packet_bytes=1048576))
    return raw


def maximum_running():
    from mission_kernel.fresh_review import canonical_digest
    launch = maximum_launch()
    intent = maximum_intent()
    intent.update(operation_id=launch['operation_id'], parent_identity=launch['parent_identity'],
                  outbound_packet_digest=launch['received_input_digest'])
    return dict(status='running', dispatch=intent, launch_receipt=launch,
                launch_digest=canonical_digest(launch), independent=False)


def test_running_shape_is_closed_and_bounds_its_nested_records():
    from mission_kernel.fresh_review_receipts import validate_running_record
    raw = maximum_running()
    validate_running_record(raw)
    for field in raw:
        with pytest.raises(ValueError, match='^fresh-review-running-invalid$'):
            validate_running_record({key: value for key, value in raw.items() if key != field})
    for changed in ({**raw, 'extra': True}, {**raw, 'independent': 1}, {**raw, 'status': 'future'}):
        with pytest.raises(ValueError, match='^fresh-review-running-invalid$'):
            validate_running_record(changed)
    with pytest.raises(ValueError, match='^fresh-review-digest-invalid$'):
        validate_running_record({**raw, 'launch_digest': 'sha256:'+'f'*65})
    with pytest.raises(ValueError, match='^fresh-review-budget-class-invalid$'):
        validate_running_record({**raw, 'dispatch': {**raw['dispatch'], 'budget_class': 'x'*129}})
    with pytest.raises(ValueError, match='^fresh-review-fence-invalid$'):
        validate_running_record({**raw, 'launch_receipt': {**raw['launch_receipt'], 'fencing_epoch': 2**63}})


def maximum_terminal(outcome):
    from mission_kernel.fresh_review import canonical_digest
    raw = terminal_document(outcome)
    launch = maximum_launch()
    raw.update(request_id=launch['request_id'], nonce=launch['nonce'],
        dispatch_operation_id=launch['operation_id'], commit_operation_id='c'*128,
        dispatch_fencing_epoch=2**63-1, commit_fencing_epoch=2**63-1,
        ended_at='9999-12-31T23:59:59.999999Z',
        budget_used=dict(wall_time_sec=2**63-1, tool_calls=2**63-1,
                         replays=2**63-1, output_bytes=2**63-1))
    if 'launch_receipt' in raw:
        raw.update(launch_receipt=launch, launch_digest=canonical_digest(launch))
    if 'output_ref' in raw:
        raw['output_ref']['size'] = 262144
    if outcome == 'completed':
        raw.update(independent=False, budget_used=dict(wall_time_sec=300,
                   tool_calls=64, replays=16, output_bytes=262144))
        raw['coverage_receipt']['evidence_ref']['size'] = 262144
        raw['findings'] = [dict(kind='fresh-review-finding', size=262144,
            digest='sha256:'+format(n, '064x'),
            relative_path='evidence/fresh-review/'+format(n, '064x')+'.json') for n in range(61)]
    elif outcome == 'failed':
        raw['reason'] = 'output-over-import-limit'
    elif outcome == 'blocked':
        raw.update(reason='capability-unenforceable', launch_attempted=False,
                   cancel_result='not-requested')
    else:
        raw['reason'] = 'output-unobservable'
    return raw


@pytest.mark.parametrize('shape', ['launch', 'completed', 'failed', 'blocked',
                                  'abandoned-unknown', 'intent', 'running'])
def test_maximum_encoded_shapes_pin_the_future_capacity_inputs(shape):
    from mission_kernel import fresh_review_receipts as receipts
    from mission_kernel.json_codec import encode_json_value, freeze_json_value
    raw = (maximum_launch() if shape == 'launch' else maximum_intent() if shape == 'intent'
           else maximum_running() if shape == 'running' else maximum_terminal(shape))
    decoder = (receipts.decode_launch_receipt if shape == 'launch'
               else receipts.validate_dispatch_intent if shape == 'intent'
               else receipts.validate_running_record if shape == 'running'
               else receipts.decode_terminal_receipt)
    decoder(raw)
    expected = {'launch': 1498, 'completed': 17925, 'failed': 3100, 'blocked': 1211,
                'abandoned-unknown': 2765, 'intent': 1797, 'running': 3455}
    encoded = encode_json_value(freeze_json_value(raw))
    assert len(encoded) == expected[shape]
    assert receipts.FRESH_REVIEW_MAX_ENCODED_BYTES[shape] == expected[shape]


@pytest.mark.parametrize('key', ['request_id', 'nonce', 'operation_id',
                                'parent_identity', 'child_identity', 'context_identity'])
def test_launch_ids_are_ascii_and_at_most_128_characters(key):
    from mission_kernel.fresh_review_receipts import decode_launch_receipt
    raw = maximum_launch()
    for bad in ('x'*129, 'é'*128):
        with pytest.raises(ValueError, match='^fresh-review-identity-invalid$'):
            decode_launch_receipt({**raw, key: bad})
    decode_launch_receipt(raw)


@pytest.mark.parametrize('key', ['request_id', 'nonce', 'dispatch_operation_id', 'commit_operation_id'])
def test_terminal_ids_are_ascii_and_at_most_128_characters(key):
    raw = maximum_terminal('blocked')
    decode_terminal_receipt(raw)
    with pytest.raises(ValueError, match='^fresh-review-identity-invalid$'):
        decode_terminal_receipt({**raw, key: 'x'*129})


@pytest.mark.parametrize('key', ['request_digest', 'adapter_registration_digest', 'received_input_digest'])
def test_launch_digests_are_exactly_71_characters(key):
    from mission_kernel.fresh_review_receipts import decode_launch_receipt
    raw = maximum_launch()
    decode_launch_receipt(raw)
    for bad in ('sha256:'+'a'*65, 'sha256:'+'a'*63, 'sha256:'+'A'*64):
        with pytest.raises(ValueError, match='^fresh-review-digest-invalid$'):
            decode_launch_receipt({**raw, key: bad})


@pytest.mark.parametrize('key', ['request_digest', 'candidate_digest'])
def test_terminal_digests_are_exactly_71_characters(key):
    raw = maximum_terminal('blocked')
    decode_terminal_receipt(raw)
    with pytest.raises(ValueError, match='^fresh-review-digest-invalid$'):
        decode_terminal_receipt({**raw, key: 'sha256:'+'a'*65})


@pytest.mark.parametrize('key,limit', [('wall_time_sec', 300), ('max_tool_calls', 64),
    ('max_replays', 16), ('max_output_bytes', 262144), ('max_packet_bytes', 1048576)])
def test_each_enforced_budget_has_a_positive_bounded_range(key, limit):
    from mission_kernel.fresh_review_receipts import decode_launch_receipt
    raw = maximum_launch()
    decode_launch_receipt(raw)
    for bad in (0, limit+1, True):
        raw['enforced_budget'][key] = bad
        with pytest.raises(ValueError, match='^fresh-review-budget-invalid$'):
            decode_launch_receipt(raw)
    raw['enforced_budget'][key] = 1
    decode_launch_receipt(raw)


@pytest.mark.parametrize('outcome,minimum', [('completed', 1), ('failed', 0)])
def test_output_size_lower_bounds_preserve_failed_empty_diagnostics(outcome, minimum):
    raw = terminal_document(outcome)
    raw['output_ref']['size'] = minimum
    decode_terminal_receipt(raw)
    for bad in (minimum-1, True):
        raw['output_ref']['size'] = bad
        with pytest.raises(ValueError, match='^fresh-review-evidence-ref-invalid$'):
            decode_terminal_receipt(raw)


def test_bounded_constants_match_the_contract_without_narrowing_saved_d1_requests():
    from mission_kernel import fresh_review as fresh
    from .test_issue895_fresh_review import _pure_projection
    assert (fresh.FRESH_REVIEW_FINDINGS_LIMIT, fresh.FRESH_REVIEW_EVIDENCE_MAX_BYTES,
            fresh.FRESH_REVIEW_INT_MAX, fresh.FRESH_REVIEW_ID_MAX_CHARS,
            fresh.FRESH_REVIEW_DIGEST_CHARS, fresh.FRESH_REVIEW_TIMESTAMP_CHARS) == (
            61, 262144, 2**63-1, 128, 71, 27)
    saved = fresh.projection_document(_pure_projection())
    assert fresh.decode_projection({'fresh_review': saved}) == _pure_projection()
    # D1's already persisted variable fields are deliberately outside these bounds.
    saved['requests'][0]['request'].update(iteration=2**80,
        created_at='2026-01-01T00:00:00.'+'0'*100+'Z')
    assert fresh.decode_projection({'fresh_review': saved}).requests[0].request.iteration == 2**80


def test_dispatch_requires_every_field_and_rejects_extra_fields_and_wrong_scalars():
    from mission_kernel.fresh_review_receipts import validate_dispatch_intent
    raw = maximum_intent()
    for field in raw:
        with pytest.raises(ValueError, match='^fresh-review-dispatch-invalid$'):
            validate_dispatch_intent({key: value for key, value in raw.items() if key != field})
    with pytest.raises(ValueError, match='^fresh-review-dispatch-invalid$'):
        validate_dispatch_intent({**raw, 'extra': True})
    for field, code in [('iteration', 'dispatch-invalid'), ('fencing_epoch', 'fence-invalid')]:
        for bad in (-1, True):
            with pytest.raises(ValueError, match='^fresh-review-'+code+'$'):
                validate_dispatch_intent({**raw, field: bad})
        validate_dispatch_intent({**raw, field: 0})
    for bad in ('', None, 1):
        with pytest.raises(ValueError, match='^fresh-review-budget-class-invalid$'):
            validate_dispatch_intent({**raw, 'budget_class': bad})
    validate_dispatch_intent({**raw, 'budget_class': 'x'})


def test_tools_remain_a_two_value_duplicate_free_list():
    from mission_kernel.fresh_review_receipts import decode_launch_receipt
    raw = maximum_launch()
    decode_launch_receipt(raw)
    for bad in (raw['enforced_tools']+['read-candidate'], ['future']):
        with pytest.raises(ValueError, match='^fresh-review-tools-invalid$'):
            decode_launch_receipt({**raw, 'enforced_tools': bad})


@pytest.mark.parametrize('path', [('launch_digest',), ('output_digest',),
    ('output_ref', 'digest'), ('coverage_receipt', 'evidence_ref', 'digest'), ('findings', 0, 'digest')])
def test_terminal_evidence_digests_cannot_grow_beyond_the_fixed_shape(path):
    raw = maximum_terminal('completed')
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = 'sha256:'+'a'*65
    with pytest.raises(ValueError, match='^fresh-review-digest-invalid$'):
        decode_terminal_receipt(raw)


@pytest.mark.parametrize('path', [('output_ref',), ('coverage_receipt', 'evidence_ref'), ('findings', 0)])
@pytest.mark.parametrize('field', ['relative_path', 'kind'])
def test_reference_strings_have_no_variable_length_extension(path, field):
    raw = maximum_terminal('completed')
    target = raw
    for key in path:
        target = target[key]
    target[field] += 'x'
    with pytest.raises(ValueError, match='^fresh-review-evidence-ref-invalid$'):
        decode_terminal_receipt(raw)


@pytest.mark.parametrize('outcome', ['failed', 'blocked', 'abandoned-unknown'])
@pytest.mark.parametrize('key', ['dispatch_fencing_epoch', 'commit_fencing_epoch',
                               'wall_time_sec', 'tool_calls', 'replays', 'output_bytes'])
def test_noncompleted_integer_lower_bounds_reject_negative_and_boolean(outcome, key):
    raw = terminal_document(outcome, launched=False, output=False)
    target = raw['budget_used'] if key in raw['budget_used'] else raw
    # A failed launch remains mandatory even when no diagnostic output exists.
    for bad in (-1, True):
        target[key] = bad
        code = 'budget-used-invalid' if target is raw['budget_used'] else 'fence-invalid'
        with pytest.raises(ValueError, match='^fresh-review-'+code+'$'):
            decode_terminal_receipt(raw)


@pytest.mark.parametrize('integer,cancel', [(0, 'not-requested'), (1, 'unknown')])
def test_launch_attempted_rejects_integers_even_when_cancel_and_reason_agree(integer, cancel):
    raw = terminal_document('blocked')
    raw.update(launch_attempted=bool(integer), cancel_result=cancel, reason='identity-unobservable')
    decode_terminal_receipt(raw)
    raw['launch_attempted'] = integer
    with pytest.raises(ValueError, match='^fresh-review-launch-attempted-invalid$'):
        decode_terminal_receipt(raw)
