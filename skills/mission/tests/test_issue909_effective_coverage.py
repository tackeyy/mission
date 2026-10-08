"""Latest-attempt rules, including criterion-level and multi-failure ordering."""
from dataclasses import replace

import pytest

from .test_issue895_fresh_review import _pure_projection, ADAPTER
from .test_issue909_fresh_review_receipts import terminal_document


def attempt(status='completed', *, criteria=('AC1', 'AC2'), number=1, independent=True,
            coverage='valid', stale=False):
    from mission_kernel.fresh_review import canonical_digest, request_document
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    from mission_kernel.fresh_review_coverage import FreshReviewAttempt
    request = replace(_pure_projection().requests[0].request, request_id='request-' + str(number),
                      nonce='nonce-' + str(number), criterion_ids=criteria,
                      candidate_bindings=tuple(replace(_pure_projection().requests[0].request.candidate_bindings[0],
                                                      criterion_id=key) for key in criteria))
    if stale:
        request = replace(request, contract_digest='sha256:' + 'b' * 64)
    receipt = None
    if status in ('completed', 'failed', 'blocked', 'abandoned-unknown'):
        raw = terminal_document(status)
        raw.update(request_id=request.request_id, nonce=request.nonce,
                   request_digest=canonical_digest(request_document(request)), candidate_digest=request.candidate_digest)
        if 'launch_receipt' in raw:
            raw['launch_receipt'].update(request_id=request.request_id, nonce=request.nonce,
                                         request_digest=raw['request_digest'])
            if not independent:
                raw['launch_receipt']['context_mode'] = 'inline'
                raw['launch_receipt']['child_identity'] = 'parent'
            raw['launch_digest'] = canonical_digest(raw['launch_receipt'])
        if status == 'completed':
            raw['independent'] = independent
            raw['coverage_receipt']['status'] = coverage
        receipt = decode_terminal_receipt(raw)
    return FreshReviewAttempt(request, status, receipt)


def bindings(attempts):
    from mission_kernel.fresh_review_coverage import FreshReviewBindings
    return FreshReviewBindings(ADAPTER, tuple((item.request.request_id, item.request.input_digest) for item in attempts),
                               (('command-1', ADAPTER),))


def test_latest_whole_attempt_includes_pending_and_never_uses_older_success():
    from mission_kernel.fresh_review_coverage import derive_effective_coverage
    old, latest = attempt(number=1), attempt('pending', number=2)
    partial = attempt(number=3, criteria=('AC1',))
    result = derive_effective_coverage((old, latest, partial), ('AC1', 'AC2'), bindings((old, latest, partial)))
    assert (result.effective_coverage, result.reason_code, result.request_id) == (
        'pending', 'acceptance-fresh-review-pending', 'request-2')


@pytest.mark.parametrize('state,stale,independent,coverage,expected,reason', [
    ('absent', False, True, 'valid', 'pending', 'missing'),
    ('pending', True, True, 'valid', 'open', 'stale'),
    ('dispatch-unknown', True, True, 'valid', 'open', 'stale'),
    ('running', True, True, 'valid', 'open', 'stale'),
    ('completed', True, False, 'open', 'open', 'stale'),
    ('failed', True, True, 'valid', 'open', 'stale'),
    ('blocked', True, True, 'valid', 'open', 'stale'),
    ('abandoned-unknown', True, True, 'valid', 'open', 'stale'),
    ('pending', False, True, 'valid', 'pending', 'pending'),
    ('dispatch-unknown', False, True, 'valid', 'pending', 'pending'),
    ('running', False, True, 'valid', 'pending', 'pending'),
    ('completed', False, False, 'open', 'open', 'non-independent'),
    ('completed', False, False, 'valid', 'open', 'non-independent'),
    ('failed', False, True, 'valid', 'open', 'coverage-open'),
    ('blocked', False, True, 'valid', 'open', 'coverage-open'),
    ('abandoned-unknown', False, True, 'valid', 'open', 'coverage-open'),
    ('completed', False, True, 'open', 'open', 'coverage-open'),
    ('completed', False, True, 'valid', 'valid', None),
])
@pytest.mark.parametrize('scope', ['whole', 'criterion'])
def test_same_ordered_table_for_whole_and_criterion(state, stale, independent, coverage, expected, reason, scope):
    from mission_kernel.fresh_review_coverage import derive_effective_coverage, judge_criterion
    # Even a failed latest attempt masks an older successful receipt.
    old = attempt(number=1)
    records = () if state == 'absent' else (old, attempt(state, number=2, stale=stale,
                                                        independent=independent, coverage=coverage))
    current = bindings(records)
    result = (derive_effective_coverage(records, ('AC1', 'AC2'), current) if scope == 'whole'
              else judge_criterion(records, 'AC1', current))
    code = None if reason is None else ('acceptance-coverage-open' if reason == 'coverage-open'
                                       else 'acceptance-fresh-review-' + reason)
    assert (result.effective_coverage, result.reason_code) == (expected, code)
    assert result.request_id == (None if state == 'absent' else 'request-2')


def test_prepare_order_not_timestamps_selects_the_attempt():
    from mission_kernel.fresh_review_coverage import derive_effective_coverage
    old = attempt()
    new = attempt('pending', number=2)
    new = replace(new, request=replace(new.request, created_at='2025-01-01T00:00:00+00:00'))
    result = derive_effective_coverage((old, new), ('AC1', 'AC2'), bindings((old, new)))
    assert result.request_id == 'request-2'
    assert result.reason_code == 'acceptance-fresh-review-pending'


@pytest.mark.parametrize('field', ['contract_digest', 'input_digests', 'candidate_snapshots'])
def test_current_binding_changes_make_even_running_attempt_stale(field):
    from mission_kernel.fresh_review_coverage import derive_effective_coverage
    row = attempt('running')
    current = bindings((row,))
    changed = 'sha256:' + 'b' * 64
    update = {'contract_digest': changed, 'input_digests': ((row.request.request_id, changed),),
              'candidate_snapshots': (('command-1', changed),)}[field]
    result = derive_effective_coverage((row,), ('AC1', 'AC2'), replace(current, **{field: update}))
    assert result.reason_code == 'acceptance-fresh-review-stale'


def test_partial_attempts_compose_criteria_but_cannot_replace_a_whole_receipt():
    from mission_kernel.fresh_review_coverage import derive_effective_coverage, judge_criterion
    first, second = attempt(criteria=('AC1',)), attempt(criteria=('AC2',), number=2)
    rows = (first, second)
    current = bindings(rows)
    assert derive_effective_coverage(rows, ('AC1', 'AC2'), current).reason_code == 'acceptance-fresh-review-missing'
    assert judge_criterion(rows, 'AC1', current).effective_coverage == 'valid'
    assert judge_criterion(rows, 'AC2', current).effective_coverage == 'valid'
    # A latest partial failure blocks its criterion, but whole coverage remains valid.
    whole, partial = attempt(), attempt('failed', criteria=('AC2',), number=2)
    rows = (whole, partial)
    current = bindings(rows)
    assert derive_effective_coverage(rows, ('AC1', 'AC2'), current).effective_coverage == 'valid'
    assert judge_criterion(rows, 'AC2', current).reason_code == 'acceptance-coverage-open'


@pytest.mark.parametrize('left,right,winner', [
    ('missing', 'stale', 'missing'), ('stale', 'pending', 'stale'),
    ('pending', 'non-independent', 'pending'), ('non-independent', 'coverage-open', 'non-independent'),
])
@pytest.mark.parametrize('reverse', [False, True])
def test_multi_failure_priority_is_table_order_not_criterion_order(left, right, winner, reverse):
    from mission_kernel.fresh_review_coverage import judge_fresh_review
    states = {'stale': ('running', True, True, 'valid'), 'pending': ('pending', False, True, 'valid'),
              'non-independent': ('completed', False, False, 'valid'),
              'coverage-open': ('completed', False, True, 'open')}
    # A valid whole attempt coexists with later partial failures. Missing uses AC3,
    # absent even from the whole request; whole and criterion failures compete.
    rows = [attempt()]
    for index, reason in enumerate((left, right), 1):
        if reason != 'missing':
            state, stale, independent, coverage = states[reason]
            rows.append(attempt(state, number=index + 1, criteria=('AC' + str(index),),
                                stale=stale, independent=independent, coverage=coverage))
    criteria = ('AC3', 'AC2') if left == 'missing' else ('AC1', 'AC2')
    if reverse:
        criteria = tuple(reversed(criteria))
    result = judge_fresh_review(tuple(rows), criteria, bindings(rows))
    code = 'acceptance-coverage-open' if winner == 'coverage-open' else 'acceptance-fresh-review-' + winner
    assert result.reason_code == code


def test_equal_priority_uses_whole_then_sorted_criteria():
    from mission_kernel.fresh_review_coverage import judge_fresh_review
    whole = attempt('pending')
    partial = attempt('pending', number=2, criteria=('AC2',))
    rows = (whole, partial)
    assert judge_fresh_review(rows, ('AC2', 'AC1'), bindings(rows)).request_id == 'request-1'


@pytest.mark.parametrize('field,value', [
    ('status', 'future'), ('status', None), ('status', []), ('terminal_receipt', None),
])
def test_malformed_attempts_are_not_implicitly_open_or_valid(field, value):
    from mission_kernel.fresh_review import FreshReviewError
    from mission_kernel.fresh_review_coverage import derive_effective_coverage
    row = attempt()
    with pytest.raises(FreshReviewError):
        derive_effective_coverage((replace(row, **{field: value}),), ('AC1', 'AC2'), bindings((row,)))


@pytest.mark.parametrize('field,value', [
    ('request_id', 'another'), ('nonce', 'another'), ('request_digest', ADAPTER),
    ('candidate_digest', ADAPTER), ('independent', False),
])
def test_typed_terminal_cannot_bypass_request_and_observation_bindings(field, value):
    from mission_kernel.fresh_review import FreshReviewError
    from mission_kernel.fresh_review_coverage import derive_effective_coverage
    row = attempt()
    with pytest.raises(FreshReviewError):
        derive_effective_coverage((replace(row, terminal_receipt=replace(row.terminal_receipt, **{field: value})),),
                                  ('AC1', 'AC2'), bindings((row,)))


@pytest.mark.parametrize('field', ['input_digests', 'candidate_snapshots'])
def test_duplicate_current_binding_entries_cannot_choose_an_arbitrary_digest(field):
    from mission_kernel.fresh_review import FreshReviewError
    from mission_kernel.fresh_review_coverage import derive_effective_coverage
    row = attempt('running')
    current = bindings((row,))
    duplicate = getattr(current, field) * 2
    with pytest.raises(FreshReviewError, match='fresh-review-current-bindings-invalid'):
        derive_effective_coverage((row,), ('AC1', 'AC2'), replace(current, **{field: duplicate}))


def test_current_candidate_map_checks_replay_and_partial_command_subset():
    from mission_kernel.fresh_review import candidate_identity
    from mission_kernel.fresh_review_coverage import derive_effective_coverage
    row = attempt('running', criteria=('AC1',))
    replay = replace(row.request.candidate_bindings[0], role='replay', command_id='replay-1')
    row = replace(row, request=replace(row.request, candidate_bindings=(*row.request.candidate_bindings, replay),
                                       candidate_digest=candidate_identity({'command-1': ADAPTER, 'replay-1': ADAPTER})))
    current = replace(bindings((row,)), candidate_snapshots=(('command-1', ADAPTER), ('replay-1', ADAPTER),
                                                            ('unselected-command', 'sha256:' + 'b' * 64)))
    assert derive_effective_coverage((row,), ('AC1',), current).reason_code == 'acceptance-fresh-review-pending'
    for snapshots in ((('command-1', ADAPTER),), (('command-1', ADAPTER), ('replay-1', 'sha256:' + 'b' * 64))):
        assert derive_effective_coverage((row,), ('AC1',), replace(current, candidate_snapshots=snapshots)).reason_code == 'acceptance-fresh-review-stale'


def test_empty_required_set_uses_latest_attempt_rather_than_inventing_a_required_criterion():
    from mission_kernel.fresh_review_coverage import derive_effective_coverage, judge_fresh_review
    rows = (attempt(criteria=('optional-1',)), attempt('pending', criteria=('optional-2',), number=2))
    current = bindings(rows)
    for judge in (derive_effective_coverage, judge_fresh_review):
        result = judge(rows, (), current)
        assert result.reason_code == 'acceptance-fresh-review-pending'
        assert result.request_id == 'request-2'
        assert judge((), (), bindings(())).reason_code == 'acceptance-fresh-review-missing'


def altered_launch(row, *, independent=None, **changes):
    from mission_kernel.fresh_review import canonical_digest
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt, receipt_document
    raw = receipt_document(row.terminal_receipt)
    raw['launch_receipt'].update(changes)
    raw['launch_digest'] = canonical_digest(raw['launch_receipt'])
    if independent is not None:
        raw['independent'] = independent
    return replace(row, terminal_receipt=decode_terminal_receipt(raw))


@pytest.mark.parametrize('excess', [0, 1])
def test_input_reference_obeys_launch_packet_limit(excess):
    from mission_kernel.fresh_review_coverage import derive_effective_coverage
    row = attempt()
    budget = dict(vars(row.terminal_receipt.launch_receipt.enforced_budget))
    budget['max_packet_bytes'] = row.request.input_ref.size - excess
    row = altered_launch(row, enforced_budget=budget)
    if excess:
        with pytest.raises(ValueError, match='^fresh-review-launch-binding-mismatch$'):
            derive_effective_coverage((row,), ('AC1', 'AC2'), bindings((row,)))
    else:
        assert derive_effective_coverage((row,), ('AC1', 'AC2'), bindings((row,))).effective_coverage == 'valid'


@pytest.mark.parametrize('field,value', [
    ('context_mode', 'inline'), ('child_identity', 'parent'), ('context_identity', 'parent'),
])
def test_each_independence_observation_alone_prevents_valid_coverage(field, value):
    from mission_kernel.fresh_review_coverage import derive_effective_coverage
    row = altered_launch(attempt(), independent=False, **{field: value})
    result = derive_effective_coverage((row,), ('AC1', 'AC2'), bindings((row,)))
    assert (result.effective_coverage, result.reason_code) == ('open', 'acceptance-fresh-review-non-independent')


@pytest.mark.parametrize('field', ['adapter_registration_digest', 'received_input_digest', 'enforced_tools',
                                  'wall_time_sec', 'max_tool_calls', 'max_replays', 'max_output_bytes', 'max_packet_bytes'])
def test_each_launch_request_binding_is_checked_individually(field):
    from mission_kernel.fresh_review import canonical_digest, request_document
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt, receipt_document
    from mission_kernel.fresh_review_coverage import derive_effective_coverage
    row = attempt()
    if field.startswith('max_') or field == 'wall_time_sec':
        row = replace(row, request=replace(row.request, **{field: getattr(row.request, field) - 1}))
        raw = receipt_document(row.terminal_receipt)
        raw['request_digest'] = raw['launch_receipt']['request_digest'] = canonical_digest(request_document(row.request))
        raw['launch_digest'] = canonical_digest(raw['launch_receipt'])
        row = replace(row, terminal_receipt=decode_terminal_receipt(raw))
    else:
        value = ['read-candidate'] if field == 'enforced_tools' else 'sha256:' + 'b' * 64
        row = altered_launch(row, **{field: value})
    with pytest.raises(ValueError, match='^fresh-review-launch-binding-mismatch$'):
        derive_effective_coverage((row,), ('AC1', 'AC2'), bindings((row,)))


@pytest.mark.parametrize('field', ['request_id', 'nonce'])
def test_request_identity_reuse_is_rejected(field):
    from mission_kernel.fresh_review_coverage import derive_effective_coverage
    first, second = attempt('pending'), attempt('pending', number=2)
    second = replace(second, request=replace(second.request, **{field: getattr(first.request, field)}))
    with pytest.raises(ValueError, match='^fresh-review-identity-reused$'):
        derive_effective_coverage((first, second), ('AC1', 'AC2'), bindings((first,)))


@pytest.mark.parametrize('status', ['pending', 'dispatch-unknown', 'running', 'failed', 'blocked', 'abandoned-unknown'])
def test_attempt_status_cannot_contradict_a_completed_terminal(status):
    from mission_kernel.fresh_review_coverage import derive_effective_coverage
    row = replace(attempt(), status=status)
    code = 'fresh-review-attempt-invalid' if status in ('pending', 'dispatch-unknown', 'running') else 'fresh-review-terminal-binding-mismatch'
    with pytest.raises(ValueError, match='^' + code + '$'):
        derive_effective_coverage((row,), ('AC1', 'AC2'), bindings((row,)))


def test_equal_priority_partial_failures_use_sorted_criterion_identifiers():
    from mission_kernel.fresh_review_coverage import judge_fresh_review
    rows = (attempt(), attempt('pending', number=2, criteria=('AC2',)),
            attempt('pending', number=3, criteria=('AC1',)))
    for criteria in (('AC1', 'AC2'), ('AC2', 'AC1')):
        result = judge_fresh_review(rows, criteria, bindings(rows))
        assert (result.reason_code, result.request_id) == ('acceptance-fresh-review-pending', 'request-3')


def test_criterion_only_stale_failure_precedes_whole_nonindependent_failure():
    from mission_kernel.fresh_review_coverage import judge_fresh_review
    whole = attempt(independent=False)
    partial = attempt('running', number=2, criteria=('AC2',), stale=True)
    rows = (whole, partial)
    decision = judge_fresh_review(rows, ('AC1', 'AC2'), bindings(rows))
    assert decision.reason_code == 'acceptance-fresh-review-stale'
    assert decision.request_id == 'request-2'
