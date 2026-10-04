"""D2b: receipt schemas stay closed before any publication path exists."""
import pytest

from .test_issue895_fresh_review import ADAPTER, _pure_projection


def launch_document():
    request = _pure_projection().requests[0].request
    return dict(schema='mission-fresh-review-launch/1', request_id=request.request_id,
                request_digest=ADAPTER, nonce=request.nonce, operation_id='dispatch',
                fencing_epoch=2, adapter_registration_digest=ADAPTER,
                parent_identity='parent', child_identity='child', context_identity='context',
                context_mode='fresh', received_input_digest=request.input_digest,
                started_at='2026-01-01T00:00:00+00:00', enforced_tools=[],
                enforced_budget={key: getattr(request, key) for key in (
                    'wall_time_sec', 'max_tool_calls', 'max_replays', 'max_output_bytes', 'max_packet_bytes')})


def test_launch_decodes_observation_without_caller_independence_flag():
    from mission_kernel.fresh_review_receipts import decode_launch_receipt
    launch = decode_launch_receipt(launch_document())
    assert launch.operation_id == 'dispatch'
    assert launch.fencing_epoch == 2
    assert launch.enforced_tools == ()
    with pytest.raises(ValueError, match='fresh-review-launch-shape-invalid'):
        decode_launch_receipt({**launch_document(), 'independent': True})


def evidence(kind):
    return dict(kind=kind, digest=ADAPTER, size=19,
                relative_path='evidence/fresh-review/' + ADAPTER[7:] + '.json')


def terminal_document(outcome='completed', *, launched=True, output=True):
    base = dict(schema='mission-fresh-review-terminal/1', request_id='request-1',
                request_digest=ADAPTER, nonce='nonce-1', dispatch_operation_id='dispatch',
                dispatch_fencing_epoch=2, commit_operation_id='reconcile', commit_fencing_epoch=3,
                outcome=outcome, reason={'completed': 'none', 'failed': 'output-invalid',
                                        'blocked': 'launch-unavailable',
                                        'abandoned-unknown': 'child-unobservable'}[outcome],
                candidate_digest=_pure_projection().requests[0].request.candidate_digest,
                budget_used=dict(wall_time_sec=1, tool_calls=0, replays=0, output_bytes=19),
                ended_at='2026-01-01T00:00:01+00:00')
    if outcome in ('completed', 'failed') or outcome == 'abandoned-unknown' and launched:
        from mission_kernel.fresh_review import canonical_digest
        base.update(launch_receipt=launch_document(), launch_digest=canonical_digest(launch_document()))
    if outcome == 'completed' or outcome == 'failed' and output:
        base.update(output_ref=evidence('fresh-review-output'), output_digest=ADAPTER)
    if outcome == 'completed':
        base.update(independent=True, coverage_receipt=dict(status='valid',
                    evidence_ref=evidence('fresh-review-coverage')), findings=[])
    if outcome == 'blocked':
        base.update(launch_attempted=False, cancel_result='not-requested')
    return base


@pytest.mark.parametrize('outcome,launched,output', [
    ('completed', True, True), ('failed', True, True), ('failed', True, False),
    ('blocked', False, False), ('abandoned-unknown', False, False),
    ('abandoned-unknown', True, False),
])
def test_terminal_variants_preserve_dispatch_and_commit_separately(outcome, launched, output):
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    receipt = decode_terminal_receipt(terminal_document(outcome, launched=launched, output=output))
    assert receipt.outcome == outcome
    assert (receipt.dispatch_operation_id, receipt.dispatch_fencing_epoch) == ('dispatch', 2)
    assert (receipt.commit_operation_id, receipt.commit_fencing_epoch) == ('reconcile', 3)
    assert type(receipt).__name__ == {
        'completed': 'CompletedFreshReview', 'failed': 'FailedFreshReview',
        'blocked': 'BlockedFreshReview', 'abandoned-unknown': 'AbandonedFreshReview',
    }[outcome]


LAUNCH_FIELDS = tuple(launch_document())
COMMON_FIELDS = ('schema', 'request_id', 'request_digest', 'nonce', 'dispatch_operation_id',
                 'dispatch_fencing_epoch', 'commit_operation_id', 'commit_fencing_epoch',
                 'outcome', 'reason', 'candidate_digest', 'budget_used', 'ended_at')
VARIANT_FIELDS = {
    'completed': ('launch_receipt', 'launch_digest', 'output_ref', 'output_digest',
                  'coverage_receipt', 'findings', 'independent'),
    'failed': ('launch_receipt', 'launch_digest'),
    'blocked': ('launch_attempted', 'cancel_result'),
    'abandoned-unknown': (),
}


def assert_coded_rejection(decoder, value):
    from mission_kernel.fresh_review import FreshReviewError
    with pytest.raises(FreshReviewError) as error:
        decoder(value)
    assert error.value.code.startswith('fresh-review-')


@pytest.mark.parametrize('field', LAUNCH_FIELDS)
@pytest.mark.parametrize('null', [False, True])
def test_every_launch_field_is_required_and_not_null(field, null):
    from mission_kernel.fresh_review_receipts import decode_launch_receipt
    raw = launch_document()
    if null:
        raw[field] = None
    else:
        del raw[field]
    assert_coded_rejection(decode_launch_receipt, raw)


@pytest.mark.parametrize('outcome,field', [
    (outcome, field) for outcome, extra in VARIANT_FIELDS.items()
    for field in COMMON_FIELDS + extra
])
@pytest.mark.parametrize('null', [False, True])
def test_every_terminal_required_field_is_required_and_not_null(outcome, field, null):
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    raw = terminal_document(outcome, launched=False, output=False)
    if null:
        raw[field] = None
    else:
        del raw[field]
    code = 'outcome-invalid' if field == 'outcome' else ('terminal-null-invalid' if null else 'terminal-shape-invalid')
    with pytest.raises(ValueError, match='^fresh-review-' + code + '$'):
        decode_terminal_receipt(raw)


@pytest.mark.parametrize('outcome,field', [
    (outcome, field) for outcome, extra in VARIANT_FIELDS.items()
    for field in ('launch_receipt', 'launch_digest', 'output_ref', 'output_digest',
                  'coverage_receipt', 'findings', 'independent', 'launch_attempted', 'cancel_result')
    if field not in extra and not (outcome == 'failed' and field in ('output_ref', 'output_digest'))
    and not (outcome == 'abandoned-unknown' and field in ('launch_receipt', 'launch_digest'))
])
def test_fields_from_other_variants_are_forbidden_even_when_null(outcome, field):
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    raw = terminal_document(outcome, launched=False, output=False)
    assert_coded_rejection(decode_terminal_receipt, {**raw, field: None})
    assert_coded_rejection(decode_terminal_receipt, {**raw, field: 'forged'})


@pytest.mark.parametrize('outcome,pair', [
    ('failed', ('output_ref', 'output_digest')),
    ('abandoned-unknown', ('launch_receipt', 'launch_digest')),
])
def test_optional_pairs_are_both_present_or_both_absent_never_null(outcome, pair):
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    full = terminal_document(outcome)
    for field in pair:
        raw = dict(full)
        del raw[field]
        assert_coded_rejection(decode_terminal_receipt, raw)
        with pytest.raises(ValueError, match='^fresh-review-terminal-null-invalid$'):
            decode_terminal_receipt({**full, field: None})


def test_unknown_keys_at_each_nested_boundary_cannot_smuggle_authority():
    import copy
    from mission_kernel.fresh_review_receipts import decode_launch_receipt, decode_terminal_receipt
    for decoder, raw, paths in (
        (decode_launch_receipt, launch_document(), [(), ('enforced_budget',)]),
        (decode_terminal_receipt, terminal_document(), [(), ('budget_used',), ('launch_receipt',),
            ('launch_receipt', 'enforced_budget'), ('output_ref',), ('coverage_receipt',),
            ('coverage_receipt', 'evidence_ref')]),
    ):
        for path in paths:
            value = copy.deepcopy(raw)
            target = value
            for key in path:
                target = target[key]
            target['extra'] = True
            assert_coded_rejection(decoder, value)
    raw = terminal_document()
    raw['findings'] = [{**evidence('fresh-review-finding'), 'extra': True}]
    assert_coded_rejection(decode_terminal_receipt, raw)


def test_wrong_types_and_nested_nulls_never_escape_as_internal_errors():
    import copy
    from mission_kernel.fresh_review import canonical_digest
    from mission_kernel.fresh_review_receipts import decode_launch_receipt, decode_terminal_receipt

    def leaves(value, path=()):
        if isinstance(value, dict):
            for key, child in value.items():
                yield from leaves(child, (*path, key))
        else:
            yield path

    for decoder, raw in [(decode_launch_receipt, launch_document()),
                         (decode_terminal_receipt, terminal_document()),
                         (decode_terminal_receipt, terminal_document('blocked'))]:
        for path in leaves(raw):
            original = raw
            for key in path:
                original = original[key]
            for bad in (None, {}, [], 1.5, True, '', -1):
                # Empty tools/findings and actual bool fields have valid exceptions.
                if bad == [] and isinstance(original, list) or type(original) is bool and type(bad) is bool:
                    continue
                value = copy.deepcopy(raw)
                target = value
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = bad
                if decoder == decode_terminal_receipt and path[0] == 'launch_receipt':
                    value['launch_digest'] = canonical_digest(value['launch_receipt'])
                assert_coded_rejection(decoder, value)


def test_outcome_reasons_are_closed_and_scoped_to_the_variant():
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    # The terminal set is independently specified rather than copied from production.
    allowed = {
        'completed': ['none'],
        'failed': ['child-failed', 'output-invalid', 'budget-exceeded', 'binding-mismatch', 'timeout', 'interrupted'],
        'blocked': ['launch-unavailable', 'input-too-large', 'registration-mismatch', 'identity-unobservable',
                    'input-unobservable', 'capability-unenforceable', 'launch-invalid', 'binding-mismatch'],
        'abandoned-unknown': ['child-unobservable', 'output-unobservable', 'interrupted'],
    }
    for outcome, reasons in allowed.items():
        for reason in set(sum(allowed.values(), [])) | {'future'}:
            raw = {**terminal_document(outcome), 'reason': reason}
            if reason in reasons:
                assert decode_terminal_receipt(raw).reason == reason
            else:
                assert_coded_rejection(decode_terminal_receipt, raw)


def test_observed_digests_and_dispatch_bindings_are_not_commit_bindings():
    import copy
    from mission_kernel.fresh_review import canonical_digest
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    raw = terminal_document()
    for key, value in [('request_id', 'another'), ('request_digest', 'sha256:' + 'b' * 64),
                       ('nonce', 'another'), ('operation_id', 'reconcile'), ('fencing_epoch', 3)]:
        changed = copy.deepcopy(raw)
        changed['launch_receipt'][key] = value
        changed['launch_digest'] = canonical_digest(changed['launch_receipt'])
        assert_coded_rejection(decode_terminal_receipt, changed)
    for key in ('launch_digest', 'output_digest'):
        assert_coded_rejection(decode_terminal_receipt, {**raw, key: 'sha256:' + 'b' * 64})


def test_diagnostic_overbudget_usage_and_launch_attempted_blocked_remain_recordable():
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    raw = terminal_document('failed')
    raw['budget_used']['output_bytes'] = 300000
    raw['reason'] = 'budget-exceeded'
    assert decode_terminal_receipt(raw).budget_used.output_bytes == 300000
    raw = terminal_document('blocked')
    assert decode_terminal_receipt({**raw, 'launch_attempted': True, 'cancel_result': 'unknown',
                                    'reason': 'identity-unobservable'}).launch_attempted


def test_typed_receipts_roundtrip_without_optional_nulls():
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt, decode_launch_receipt, receipt_document
    launch = decode_launch_receipt(launch_document())
    assert decode_launch_receipt(receipt_document(launch)) == launch
    for outcome, launched, output in [('completed', True, True), ('failed', True, False),
                                       ('blocked', False, False), ('abandoned-unknown', False, False)]:
        receipt = decode_terminal_receipt(terminal_document(outcome, launched=launched, output=output))
        assert decode_terminal_receipt(receipt_document(receipt)) == receipt


@pytest.mark.parametrize('used,maximum', [
    ('wall_time_sec', 'wall_time_sec'), ('tool_calls', 'max_tool_calls'),
    ('replays', 'max_replays'), ('output_bytes', 'max_output_bytes'),
])
def test_completed_cannot_claim_success_after_enforced_budget_exhaustion(used, maximum):
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    raw = terminal_document()
    raw['budget_used'][used] = raw['launch_receipt']['enforced_budget'][maximum] + 1
    assert_coded_rejection(decode_terminal_receipt, raw)


def test_registration_pin_mismatch_can_be_observed_after_launch():
    # Design 689 section 3 explicitly includes post-launch registration pin failures.
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    raw = terminal_document('blocked')
    raw.update(launch_attempted=True, reason='registration-mismatch', cancel_result='cancelled')
    assert decode_terminal_receipt(raw).reason == 'registration-mismatch'


def test_every_nested_required_field_is_rejected_when_missing_or_null():
    import copy
    from mission_kernel.fresh_review_receipts import decode_launch_receipt, decode_terminal_receipt
    for decoder, raw, paths in (
        (decode_launch_receipt, launch_document(), [('enforced_budget',)]),
        (decode_terminal_receipt, terminal_document(), [('budget_used',), ('coverage_receipt',),
                                                       ('output_ref',), ('coverage_receipt', 'evidence_ref')]),
    ):
        for path in paths:
            mapping = raw
            for key in path:
                mapping = mapping[key]
            for field in mapping:
                for null in (False, True):
                    value = copy.deepcopy(raw)
                    target = value
                    for key in path:
                        target = target[key]
                    if null:
                        target[field] = None
                    else:
                        del target[field]
                    assert_coded_rejection(decoder, value)
    raw = terminal_document()
    raw['findings'] = [evidence('fresh-review-finding')]
    assert decode_terminal_receipt(raw).findings[0].kind == 'fresh-review-finding'
    for field in raw['findings'][0]:
        value = copy.deepcopy(raw)
        del value['findings'][0][field]
        assert_coded_rejection(decode_terminal_receipt, value)
        value = copy.deepcopy(raw)
        value['findings'][0][field] = None
        assert_coded_rejection(decode_terminal_receipt, value)


def test_failed_receipt_keeps_empty_output_bytes_as_diagnostic_evidence():
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    raw = terminal_document('failed')
    raw['output_ref']['size'] = 0
    raw['budget_used']['output_bytes'] = 0
    assert decode_terminal_receipt(raw).output_ref.size == 0


@pytest.mark.parametrize('excess', [0, 1])
def test_completed_output_reference_obeys_enforced_byte_limit(excess):
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    raw = terminal_document()
    raw['output_ref']['size'] = raw['launch_receipt']['enforced_budget']['max_output_bytes'] + excess
    if excess:
        with pytest.raises(ValueError, match='^fresh-review-budget-exceeded$'):
            decode_terminal_receipt(raw)
    else:
        assert decode_terminal_receipt(raw).output_ref.size == 262144


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'abandoned-unknown'])
def test_terminal_cannot_end_before_its_launch(outcome):
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    raw = terminal_document(outcome)
    raw['ended_at'] = '2025-12-31T23:59:59+00:00'
    with pytest.raises(ValueError, match='^fresh-review-timestamp-order-invalid$'):
        decode_terminal_receipt(raw)


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'blocked', 'abandoned-unknown'])
def test_commit_epoch_cannot_precede_dispatch(outcome):
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    raw = terminal_document(outcome)
    raw['commit_fencing_epoch'] = 1
    with pytest.raises(ValueError, match='^fresh-review-fence-order-invalid$'):
        decode_terminal_receipt(raw)
    raw['commit_fencing_epoch'] = 2
    assert decode_terminal_receipt(raw).commit_fencing_epoch == 2


@pytest.mark.parametrize('field,value', [
    ('context_mode', 'inline'), ('context_mode', 'shared'),
    ('child_identity', 'parent'), ('context_identity', 'parent'),
])
def test_decoder_cannot_assert_independence_against_launch_observations(field, value):
    from mission_kernel.fresh_review import canonical_digest
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    raw = terminal_document()
    raw['launch_receipt'][field] = value
    raw['launch_digest'] = canonical_digest(raw['launch_receipt'])
    with pytest.raises(ValueError, match='^fresh-review-independent-invalid$'):
        decode_terminal_receipt(raw)
    raw['independent'] = False
    assert not decode_terminal_receipt(raw).independent


@pytest.mark.parametrize('launched', [False, True])
@pytest.mark.parametrize('cancel', ['not-requested', 'cancelled', 'failed', 'unknown'])
def test_blocked_cancel_result_requires_a_launch_attempt(launched, cancel):
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    raw = terminal_document('blocked')
    raw.update(launch_attempted=launched, cancel_result=cancel, reason='identity-unobservable')
    if launched != (cancel != 'not-requested'):
        with pytest.raises(ValueError, match='^fresh-review-cancel-invalid$'):
            decode_terminal_receipt(raw)
    else:
        assert decode_terminal_receipt(raw).cancel_result == cancel


@pytest.mark.parametrize('reason', ['launch-unavailable', 'input-too-large'])
def test_prelaunch_reason_cannot_claim_a_launch_attempt(reason):
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    raw = terminal_document('blocked')
    raw.update(reason=reason, launch_attempted=True, cancel_result='cancelled')
    with pytest.raises(ValueError, match='^fresh-review-launch-attempted-invalid$'):
        decode_terminal_receipt(raw)


@pytest.mark.parametrize('field,value,code', [
    ('coverage_receipt', {'status': 'pending', 'evidence_ref': evidence('fresh-review-coverage')}, 'coverage-invalid'),
    ('findings', [evidence('fresh-review-finding')] * 2, 'findings-invalid'),
    ('output_ref', {**evidence('fresh-review-output'), 'size': 0}, 'evidence-ref-invalid'),
])
def test_completed_cannot_publish_pending_duplicate_or_empty_evidence(field, value, code):
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    with pytest.raises(ValueError, match='^fresh-review-' + code + '$'):
        decode_terminal_receipt({**terminal_document(), field: value})


@pytest.mark.parametrize('path,code', [
    (('budget_used', 'tool_calls'), 'budget-used-invalid'),
    (('coverage_receipt', 'status'), 'coverage-invalid'),
    (('output_ref', 'size'), 'evidence-ref-invalid'),
    (('launch_receipt', 'context_mode'), 'context-invalid'),
    (('launch_receipt', 'started_at'), 'timestamp-invalid'),
    (('launch_receipt', 'enforced_budget', 'max_packet_bytes'), 'budget-invalid'),
])
def test_nested_null_reasons_are_exact(path, code):
    from mission_kernel.fresh_review import canonical_digest
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    raw = terminal_document()
    target = raw
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = None
    raw['launch_digest'] = canonical_digest(raw['launch_receipt'])
    with pytest.raises(ValueError, match='^fresh-review-' + code + '$'):
        decode_terminal_receipt(raw)


def test_delayed_terminal_publication_does_not_extend_measured_child_wall_time():
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    raw = terminal_document()
    raw['ended_at'] = '2026-01-01T00:10:00+00:00'
    raw['budget_used']['wall_time_sec'] = 300
    assert decode_terminal_receipt(raw).outcome == 'completed'
