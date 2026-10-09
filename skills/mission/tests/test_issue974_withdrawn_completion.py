"""Withdrawal must mask earlier success without inventing evidence or bindings."""
import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from mission_kernel.fresh_review import (
    FreshReviewProjection, FreshReviewRecord, FreshReviewError, canonical_bytes,
    decode_projection, projection_document, withdraw_request,
)
from mission_kernel.fresh_review_coverage import (
    FreshReviewAttempt, derive_effective_coverage, judge_criterion, judge_fresh_review,
)
from mission_kernel.transitions import acceptance_completion_rejection, decide
from .test_issue909_effective_coverage import attempt, bindings
from .test_issue913_completion_judgement import clean, with_contract, bound_record, command_for
from .test_issue913_completion_inputs import gate_state, published
from .test_issue896_completed import completed_carrier


def tombstone(request, criteria):
    pending = FreshReviewRecord(replace(request, request_id='withdrawn-request', nonce='withdrawn-nonce',
        criterion_ids=criteria, candidate_bindings=tuple(b for b in request.candidate_bindings
                                                       if b.criterion_id in criteria)),
        'withdrawn-prepare', 'sha256:' + 'a' * 64, 'sha256:' + 'a' * 64)
    return withdraw_request(FreshReviewProjection((pending,)), request_id=pending.request.request_id,
                            operation_id='withdraw-operation', fencing_epoch=1).requests[0]


@pytest.mark.parametrize('criteria', [('AC1', 'AC2'), ('AC2',)])
@pytest.mark.parametrize('superseded', [False, True])
@pytest.mark.parametrize('stale', [False, True])
def test_withdrawn_selection_and_missing_priority_in_all_coverage_apis(criteria, superseded, stale):
    completed = attempt(stale=stale)
    withdrawn = FreshReviewAttempt(tombstone(completed.request, criteria), 'withdrawn')
    attempts = (withdrawn, completed) if superseded else (completed, withdrawn)
    current = bindings((completed,))  # Tombstones have no current input/candidate bindings.
    expected = ('acceptance-fresh-review-stale' if stale else None) if superseded else 'acceptance-fresh-review-missing'
    for result in (judge_criterion(attempts, 'AC2', current), judge_fresh_review(attempts, ('AC1', 'AC2'), current)):
        assert result.reason_code == expected
        assert result.request_id == (completed.request.request_id if superseded else 'withdrawn-request')
    whole = derive_effective_coverage(attempts, ('AC1', 'AC2'), current)
    assert whole.reason_code == (expected if len(criteria) == 2 or superseded else
                                 ('acceptance-fresh-review-stale' if stale else None))
    if not superseded:
        assert judge_criterion(attempts, 'AC2', current).effective_coverage == 'pending'


@pytest.mark.parametrize('scope', ['whole', 'criterion'])
@pytest.mark.parametrize('superseded', [False, True])
@pytest.mark.parametrize('stale', [None, 'input', 'candidate'])
def test_withdrawal_preflight_and_mark_pass_share_the_fail_closed_selection(clean, scope, superseded, stale):
    state, command = clean
    if scope == 'criterion':
        from mission_kernel.json_codec import freeze_json_value
        state, command = with_contract(clean, lambda c: c['criteria'].append(dict(c['criteria'][0], id='AC2')))
        whole = state.fresh_review.requests[0]
        coverage = command.fresh_review_evidence[0].coverage.thaw()
        coverage['criterion_results'].append(dict(criterion_id='AC2', status='searched', reason_code='none'))
        coverage['requirements'][0]['criterion_ids'].append('AC2')
        whole, evidence = bound_record(whole, coverage, criterion_ids=('AC1', 'AC2'),
            candidate_bindings=(*whole.request.candidate_bindings,
                *(replace(b, criterion_id='AC2') for b in whole.request.candidate_bindings)))
        whole = replace(whole, launch=freeze_json_value(whole.result.thaw()['launch_receipt']))
        state = replace(state, fresh_review=FreshReviewProjection((whole,)))
        command = command_for(state, (evidence,))
    record = state.fresh_review.requests[0]
    withdrawn = tombstone(record.request, ('AC2',) if scope == 'criterion' else record.request.criterion_ids)
    projection = FreshReviewProjection((withdrawn, record) if superseded else (record, withdrawn))
    state = replace(state, fresh_review=decode_projection({'fresh_review': projection_document(projection)}))
    if stale == 'input':
        command = replace(command, fresh_review_bindings=replace(command.fresh_review_bindings,
            input_digests=((record.request.request_id, 'sha256:' + 'b' * 64),)))
    elif stale == 'candidate':
        snapshots = command.fresh_review_bindings.candidate_snapshots
        command = replace(command, fresh_review_bindings=replace(command.fresh_review_bindings,
            candidate_snapshots=((snapshots[0][0], 'sha256:' + 'b' * 64), *snapshots[1:])))
    expected = ('acceptance-fresh-review-missing' if not superseded else
                'acceptance-fresh-review-stale' if stale else None)
    before = copy.deepcopy(state)
    public_bytes = canonical_bytes(projection_document(state.fresh_review))
    assert acceptance_completion_rejection(state, command) == expected
    result = decide(state, command)
    if expected:
        assert result.rejection.code == expected
        assert result.transition is None and result.events == result.effects == ()
    else:
        assert result.accepted and result.transition.new_state.control.passes is True
    assert state == before
    assert canonical_bytes(projection_document(state.fresh_review)) == public_bytes


def test_only_withdrawn_needs_no_evidence_or_bindings_and_is_missing(clean):
    state, command = clean
    record = state.fresh_review.requests[0]
    state = replace(state, fresh_review=FreshReviewProjection((tombstone(record.request, ('AC1',)),)))
    command = replace(command, fresh_review_evidence=(), fresh_review_bindings=replace(
        command.fresh_review_bindings, input_digests=(), candidate_snapshots=()))
    assert acceptance_completion_rejection(state, command) == 'acceptance-fresh-review-missing'
    result = decide(state, command)
    assert result.rejection.code == 'acceptance-fresh-review-missing'
    assert result.transition is None and result.effects == result.events == ()


@pytest.mark.parametrize('fault', ['status', 'terminal', 'criteria', 'digest', 'id', 'nonce', 'normal-withdrawn'])
def test_withdrawn_cannot_bypass_attempt_shape_or_one_use_identity(fault):
    completed = attempt()
    withdrawn = FreshReviewAttempt(tombstone(completed.request, ('AC1',)), 'withdrawn')
    if fault == 'status':
        withdrawn = replace(withdrawn, status='pending')
    elif fault == 'terminal':
        withdrawn = replace(withdrawn, terminal_receipt=completed.terminal_receipt)
    elif fault == 'criteria':
        withdrawn = replace(withdrawn, request=replace(withdrawn.request, criterion_ids=('AC1', 'AC1')))
    elif fault == 'digest':
        withdrawn = replace(withdrawn, request=replace(withdrawn.request, request_digest='invalid'))
    elif fault in ('id', 'nonce'):
        field = 'request_id' if fault == 'id' else 'nonce'
        withdrawn = replace(withdrawn, request=replace(withdrawn.request, **{field: getattr(completed.request, field)}))
    else:
        withdrawn = replace(withdrawn, request=completed.request)
    with pytest.raises(FreshReviewError):
        judge_fresh_review((completed, withdrawn), ('AC1', 'AC2'), bindings((completed,)))


def test_observe_omits_withdrawn_bindings_and_evidence(clean, tmp_path, monkeypatch):
    from mission_application import fresh_review_completion as app
    from mission_kernel.fresh_review_completion import validate_completion_carriers
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    state, command = clean
    record = state.fresh_review.requests[0]
    withdrawn = tombstone(record.request, ('AC1',))
    state = replace(state, fresh_review=FreshReviewProjection((record, withdrawn)))
    data = dict(state.legacy_passthrough.thaw(), fresh_review=projection_document(state.fresh_review))
    coverage_ref = decode_terminal_receipt(record.result.thaw()).coverage_receipt.evidence_ref
    reads, captures = [], []
    def read(root, path, limit):
        reads.append(path)
        assert path == coverage_ref.relative_path
        return canonical_bytes(command.fresh_review_evidence[0].coverage.thaw())
    def capture(root, commands):
        captures.append(set(commands))
        return {key: SimpleNamespace(digest=digest, files=())
                for key, digest in command.fresh_review_bindings.candidate_snapshots}
    monkeypatch.setattr(app.prepare, '_capture', capture)
    observed = app.observe_completion_inputs(data, root=tmp_path,
        load_policy=lambda _: data['acceptance_contract']['verifier_policy'], read_evidence=read)
    assert reads == [coverage_ref.relative_path]
    assert captures == [set(dict(command.fresh_review_bindings.candidate_snapshots))] * 2
    assert [item.request_id for item in observed.evidence] == [record.request.request_id]
    assert [key for key, _ in observed.bindings.input_digests] == [record.request.request_id]
    validate_completion_carriers(state.fresh_review, observed.evidence, observed.bindings,
                                 observed.bindings.contract_digest)
