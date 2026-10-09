"""Completion contracts exercised through the same preflight and transition guard."""
import copy
import json
from dataclasses import replace

import pytest

from acceptance_contract import canonical_contract_digest, verifier_definition_digest
from mission_kernel.commands import MarkPass
from mission_kernel.fresh_review import FreshReviewProjection, canonical_bytes, canonical_digest, request_document
from mission_kernel.fresh_review_completion import decode_completion_evidence
from mission_kernel.fresh_review_coverage import FreshReviewBindings
from mission_kernel.fresh_review_publish import evidence_claim, reference
from mission_kernel.fresh_review_receipts import decode_terminal_receipt
from mission_kernel.json_codec import freeze_json_value
from mission_kernel.transitions import acceptance_completion_rejection, decide
from .test_issue913_completion_inputs import published, gate_state
from .test_issue896_completed import completed_carrier


def bound_record(record, coverage, findings=(), **changes):
    """Bind real canonical bytes to refs; semantic faults are not digest faults."""
    request = replace(record.request, **changes)
    terminal = record.result.thaw()
    identity = dict(request_id=request.request_id, request_digest=canonical_digest(request_document(request)),
                    candidate_digest=request.candidate_digest)
    terminal.update(identity, nonce=request.nonce)
    terminal['launch_receipt'].update(request_id=request.request_id, nonce=request.nonce,
                                     request_digest=identity['request_digest'])
    terminal['launch_digest'] = canonical_digest(terminal['launch_receipt'])
    coverage = copy.deepcopy(coverage)
    coverage.update(identity)
    findings = tuple(dict(item, **identity) for item in findings)
    terminal['coverage_receipt'] = dict(status=coverage['status'],
        evidence_ref=reference(evidence_claim('fresh-review-coverage', canonical_bytes(coverage))))
    terminal['findings'] = [reference(evidence_claim('fresh-review-finding', canonical_bytes(item))) for item in findings]
    result = replace(record, request=request, result=freeze_json_value(terminal))
    evidence = decode_completion_evidence(request, decode_terminal_receipt(terminal), coverage, findings)
    return result, evidence


def command_for(state, evidence):
    records = state.fresh_review.requests
    contract = state.legacy_passthrough.thaw()['acceptance_contract']
    snapshots = dict((b.command_id, b.snapshot_digest) for r in records for b in r.request.candidate_bindings)
    return MarkPass(artifact_gate_satisfied=True, specialist_gate_satisfied=True,
        acceptance_candidate_digests=freeze_json_value({c['id']: snapshots[c['command_id']]
            for c in contract['criteria'] if c['required']}),
        fresh_review_evidence=tuple(evidence), fresh_review_bindings=FreshReviewBindings(
            canonical_contract_digest(contract), tuple((r.request.request_id, r.request.input_digest) for r in records),
            tuple(dict((b.command_id, b.snapshot_digest) for r in records for b in r.request.candidate_bindings).items())))


def receipts(document, candidate):
    contract = document['acceptance_contract']
    document['verification_receipts'] = [dict(criterion_id=c['id'], status='passed',
        contract_digest=canonical_contract_digest(contract), verifier_policy_digest=contract['verifier_policy']['digest'],
        verifier_definition_digest=verifier_definition_digest(contract['verifier_policy']['commands'][c['command_id']]),
        candidate_digest=candidate) for c in contract['criteria'] if c['required']]
    return document


@pytest.fixture
def clean(gate_state, published):
    coverage = json.loads(published[3])
    coverage['open_finding_ids'] = []
    record, evidence = bound_record(published[0], coverage)
    document = receipts(gate_state.legacy_passthrough.thaw(), record.request.candidate_bindings[0].snapshot_digest)
    state = replace(gate_state, fresh_review=FreshReviewProjection((record,)), legacy_passthrough=freeze_json_value(document))
    from mission_kernel.model import BoundScore, ScoreSource, ManualScoreRef, ContentAddressedRef, NotApplicableRevisionScope
    scope = NotApplicableRevisionScope('non-git')
    digest = 'sha256:' + 'a' * 64
    score = BoundScore(True, ScoreSource.MANUAL_IMPORT,
        freeze_json_value(dict(composite=4.5, min_item=4, open_high=0)),
        ManualScoreRef('evidence/manual.json', digest, 'fixture', scope),
        ContentAddressedRef('scoring-json', 'evidence/scoring.json', digest, 1), scope)
    state = replace(state, scores=(score,))
    return state, command_for(state, (evidence,))


def rejected(state, command, reason):
    before = copy.deepcopy(state)
    public_bytes = canonical_bytes([r.result.thaw() if r.result else None for r in state.fresh_review.requests])
    assert acceptance_completion_rejection(state, command) == reason
    decision = decide(state, command)
    assert decision.rejection.code == reason
    assert decision.transition is None and decision.events == decision.effects == ()
    assert state == before
    assert canonical_bytes([r.result.thaw() if r.result else None for r in state.fresh_review.requests]) == public_bytes


def test_current_clean_review_passes_both_guards_with_immutable_imported_coverage(clean):
    state, command = clean
    assert state.legacy_passthrough.thaw()['acceptance_contract']['coverage'] == {'status': 'pending'}
    assert acceptance_completion_rejection(state, command) is None
    decision = decide(state, command)
    assert decision.accepted
    assert decision.transition.new_state.control.passes is True


@pytest.mark.parametrize('case,reason', [
    ('no-carriers', 'fresh-review-completion-carrier-incomplete'),
    ('no-attempt', 'fresh-review-missing'), ('input', 'fresh-review-stale'),
    ('verification', 'fresh-review-stale'), ('replay', 'fresh-review-stale'),
    ('pending', 'fresh-review-pending'), ('dispatch-unknown', 'fresh-review-pending'),
    ('running', 'fresh-review-pending'), ('failed', 'coverage-open'),
    ('blocked', 'coverage-open'), ('abandoned-unknown', 'coverage-open'),
    ('non-independent', 'fresh-review-non-independent'), ('open', 'coverage-open'),
])
def test_latest_attempt_and_observed_bindings_control_completion(clean, case, reason):
    state, command = clean
    record = state.fresh_review.requests[0]
    if case == 'no-carriers':
        command = replace(command, fresh_review_evidence=(), fresh_review_bindings=None)
    elif case == 'no-attempt':
        state = replace(state, fresh_review=FreshReviewProjection())
        command = replace(command, fresh_review_evidence=(), fresh_review_bindings=replace(
            command.fresh_review_bindings, input_digests=(), candidate_snapshots=()))
    elif case in ('input', 'verification', 'replay'):
        bindings = command.fresh_review_bindings
        if case == 'input':
            bindings = replace(bindings, input_digests=((record.request.request_id, 'sha256:' + 'b' * 64),))
        else:
            snapshots = list(bindings.candidate_snapshots)
            i = 0 if case == 'verification' else 1
            snapshots[i] = (snapshots[i][0], 'sha256:' + 'b' * 64)
            bindings = replace(bindings, candidate_snapshots=tuple(snapshots))
        command = replace(command, fresh_review_bindings=bindings)
    else:
        # A failed or running second attempt must mask the earlier clean success.
        latest, evidence = bound_record(record, command.fresh_review_evidence[0].coverage.thaw(),
                                        request_id='latest', nonce='latest-nonce')
        raw = latest.result.thaw()
        if case in ('non-independent', 'open'):
            if case == 'non-independent':
                raw['independent'] = False
                raw['launch_receipt']['context_mode'] = 'inline'
                raw['launch_digest'] = canonical_digest(raw['launch_receipt'])
            else:
                cov = evidence.coverage.thaw()
                cov['status'] = 'open'
                cov['open_requirement_ids'] = ['R1']
                cov['requirements'][0].update(status='open', reason_code='unconfirmed')
                latest, evidence = bound_record(latest, cov)
                raw = latest.result.thaw()
            latest = replace(latest, result=freeze_json_value(raw))
            carried = (*command.fresh_review_evidence, evidence)
        else:
            if case in ('pending', 'dispatch-unknown', 'running'):
                latest = replace(latest, status=case, result=None)
            else:
                from .test_issue909_fresh_review_receipts import terminal_document
                terminal = terminal_document(case)
                for key in ('request_id', 'request_digest', 'candidate_digest', 'nonce'):
                    terminal[key] = raw[key]
                if 'launch_receipt' in terminal:
                    terminal['launch_receipt'] = raw['launch_receipt']
                    terminal['launch_digest'] = raw['launch_digest']
                latest = replace(latest, status=case, result=freeze_json_value(terminal))
            carried = command.fresh_review_evidence
        state = replace(state, fresh_review=FreshReviewProjection((record, latest)))
        command = command_for(state, carried)
    rejected(state, command, 'acceptance-' + reason)


@pytest.mark.parametrize('status', ['blocked', 'abandoned-unknown', 'pending', 'dispatch-unknown', 'running'])
@pytest.mark.parametrize('superseded', [True, False])
def test_only_latest_attempt_state_affects_completion(clean, status, superseded):
    state, command = clean
    record = state.fresh_review.requests[0]
    other, _ = bound_record(record, command.fresh_review_evidence[0].coverage.thaw(),
                           request_id='other', nonce='other-nonce')
    if status in ('pending', 'dispatch-unknown', 'running'):
        other = replace(other, status=status, result=None)
    else:
        from .test_issue909_fresh_review_receipts import terminal_document
        raw = other.result.thaw()
        terminal = terminal_document(status, launched=False, output=False)
        terminal.update({key: raw[key] for key in ('request_id', 'request_digest', 'candidate_digest', 'nonce')})
        other = replace(other, status=status, result=freeze_json_value(terminal))
    rows = (other, record) if superseded else (record, other)
    state = replace(state, fresh_review=FreshReviewProjection(rows))
    command = command_for(state, command.fresh_review_evidence)
    if superseded:
        assert acceptance_completion_rejection(state, command) is None
        assert decide(state, command).accepted
    else:
        reason = 'coverage-open' if status in ('blocked', 'abandoned-unknown') else 'fresh-review-pending'
        rejected(state, command, 'acceptance-' + reason)


@pytest.mark.parametrize('status', ['reserved', 'consumed'])
@pytest.mark.parametrize('scope', ['whole', 'criterion'])
@pytest.mark.parametrize('position', ['superseded', 'latest', 'stale'])
def test_legacy_nonce_checkpoints_follow_latest_attempt_rules(clean, status, scope, position):
    from mission_kernel.fresh_review import (FreshReviewRecord, reserve_request, consume_request,
                                            projection_document, decode_projection)
    state, command = clean
    if scope == 'criterion':
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
    request = replace(record.request, request_id='legacy', nonce='legacy-nonce')
    if scope == 'criterion':
        request = replace(request, criterion_ids=('AC2',),
            candidate_bindings=tuple(b for b in request.candidate_bindings if b.criterion_id == 'AC2'))
    pending = FreshReviewRecord(request, 'legacy-prepare', record.prepare_intent_digest, record.prepare_payload_digest)
    arguments = dict(operation_id='legacy-dispatch', intent_digest=canonical_digest('intent'),
                     payload_digest=canonical_digest('payload'))
    legacy = reserve_request(FreshReviewProjection((pending,)), request, **arguments)
    if status == 'consumed':
        # A legacy result can claim success without any authenticated receipt.
        legacy = consume_request(legacy, request, result={'status': 'passed'}, **arguments)
    other = legacy.requests[0]
    rows = (other, record) if position == 'superseded' else (record, other)
    projection = decode_projection({'fresh_review': projection_document(FreshReviewProjection(rows))})
    state = replace(state, fresh_review=projection)
    command = command_for(state, command.fresh_review_evidence)
    if position == 'stale':
        inputs = command.fresh_review_bindings.input_digests
        command = replace(command, fresh_review_bindings=replace(command.fresh_review_bindings,
            input_digests=(*inputs[:-1], (request.request_id, canonical_digest('changed-input')))))
    if position == 'superseded':
        assert acceptance_completion_rejection(state, command) is None
        assert decide(state, command).accepted
    else:
        reason = 'stale' if position == 'stale' else 'pending'
        rejected(state, command, 'acceptance-fresh-review-' + reason)


def with_contract(clean, change):
    state, command = clean
    document = state.legacy_passthrough.thaw()
    change(document['acceptance_contract'])
    record, evidence = bound_record(state.fresh_review.requests[0], command.fresh_review_evidence[0].coverage.thaw(),
        contract_digest=canonical_contract_digest(document['acceptance_contract']))
    state = replace(state, fresh_review=FreshReviewProjection((record,)),
        legacy_passthrough=freeze_json_value(receipts(document, record.request.candidate_bindings[0].snapshot_digest)))
    return state, command_for(state, (evidence,))


@pytest.mark.parametrize('case', ['historical-non-independent', 'historical-open', 'partial-open', 'blocked-search'])
@pytest.mark.parametrize('superseded', [True, False])
def test_superseded_whole_receipts_are_ignored_but_partial_obligations_remain(clean, case, superseded):
    state, command = clean
    if case in ('partial-open', 'blocked-search'):
        state, command = with_contract(clean, lambda c: c['criteria'].append(
            dict(c['criteria'][0], id='ACopt', required=False, requirement_ids=['R2'])))
    record = state.fresh_review.requests[0]
    coverage = command.fresh_review_evidence[0].coverage.thaw()
    older, evidence = bound_record(record, coverage, request_id='older', nonce='older-nonce')
    if case == 'historical-non-independent':
        raw = older.result.thaw()
        raw['independent'] = False
        raw['launch_receipt']['context_mode'] = 'inline'
        raw['launch_digest'] = canonical_digest(raw['launch_receipt'])
        older = replace(older, result=freeze_json_value(raw))
    else:
        if case == 'blocked-search':
            coverage['criterion_results'].append(dict(criterion_id='ACopt', status='blocked', reason_code='unavailable'))
            older, evidence = bound_record(older, coverage, criterion_ids=('AC1', 'ACopt'),
                candidate_bindings=(*older.request.candidate_bindings,
                    replace(older.request.candidate_bindings[0], criterion_id='ACopt')))
        else:
            coverage['status'] = 'open'
            coverage['open_requirement_ids'] = ['R1']
            coverage['requirements'][0].update(status='open', reason_code='unconfirmed')
            changes = {}
            if case == 'partial-open':
                coverage['criterion_results'] = [dict(criterion_id='ACopt', status='searched', reason_code='none')]
                changes = dict(criterion_ids=('ACopt',), candidate_bindings=tuple(
                    replace(b, criterion_id='ACopt') for b in older.request.candidate_bindings))
            older, evidence = bound_record(older, coverage, **changes)
    rows = (older, record) if superseded else (record, older)
    state = replace(state, fresh_review=FreshReviewProjection(rows))
    command = command_for(state, (evidence, *command.fresh_review_evidence))
    if case == 'partial-open' or not superseded:
        reason = 'fresh-review-non-independent' if case == 'historical-non-independent' else 'coverage-open'
        rejected(state, command, 'acceptance-' + reason)
    else:
        assert acceptance_completion_rejection(state, command) is None
        assert decide(state, command).accepted


@pytest.mark.parametrize('severity', ['High', 'Medium', 'Low'])
@pytest.mark.parametrize('binding', ['obligation', 'prohibited-effect'])
def test_later_clean_review_cannot_resolve_any_severity_bound_finding(clean, published, severity, binding):
    state, command = clean
    finding = json.loads(published[4][0])
    finding['severity'] = severity
    if binding == 'obligation':
        finding['prohibited_side_effect_ids'] = []
    else:
        finding['requirement_ids'] = []
    coverage = command.fresh_review_evidence[0].coverage.thaw()
    coverage['open_finding_ids'] = [finding['finding_id']]
    older, evidence = bound_record(state.fresh_review.requests[0], coverage, (finding,),
                                  request_id='older', nonce='older-nonce')
    state = replace(state, fresh_review=FreshReviewProjection((older, *state.fresh_review.requests)))
    rejected(state, command_for(state, (evidence, *command.fresh_review_evidence)), 'acceptance-unresolved-finding')


@pytest.mark.parametrize('case', ['unknown-open-finding', 'omitted-open-finding', 'unknown-open-requirement',
    'omitted-open-requirement', 'unknown-result', 'unrequested-result', 'unknown-requirement',
    'omitted-requirement', 'unknown-mapping', 'unknown-finding-requirement', 'unknown-side-effect', 'status-only',
    'verified-without-replay', 'verified-passed-replay', 'blocked-with-success-reason', 'replay-binding', 'blocked-actual-type'])
def test_authenticated_bytes_must_also_be_internally_consistent(clean, published, case):
    state, command = clean
    coverage = command.fresh_review_evidence[0].coverage.thaw()
    findings = []
    if case in ('omitted-open-finding', 'unknown-finding-requirement', 'unknown-side-effect',
                'verified-without-replay', 'verified-passed-replay', 'blocked-with-success-reason', 'replay-binding', 'blocked-actual-type'):
        findings = [json.loads(published[4][0])]
        coverage['open_finding_ids'] = ['finding-1']
    if case == 'unknown-open-finding':
        coverage['open_finding_ids'] = ['ghost']
    elif case == 'omitted-open-finding':
        coverage['open_finding_ids'] = []
    elif case == 'unknown-open-requirement':
        coverage.update(status='open', open_requirement_ids=['R1', 'ghost'])
        coverage['requirements'][0].update(status='open', reason_code='unconfirmed')
    elif case == 'omitted-open-requirement':
        coverage['requirements'][1].update(status='open', reason_code='unconfirmed')
    elif case == 'status-only':
        coverage['status'] = 'open'
    elif case == 'unknown-result':
        coverage['criterion_results'][0]['criterion_id'] = 'ghost'
    elif case == 'unrequested-result':
        state, command = with_contract(clean, lambda c: c['criteria'].append(dict(c['criteria'][0], id='ACopt', required=False)))
        coverage['criterion_results'].append(dict(criterion_id='ACopt', status='searched', reason_code='none'))
    elif case == 'unknown-requirement':
        coverage['requirements'][1]['requirement_id'] = 'ghost'
    elif case == 'omitted-requirement':
        coverage['requirements'].pop()
    elif case == 'unknown-mapping':
        coverage['requirements'][0]['criterion_ids'] = ['ghost']
    elif case == 'unknown-finding-requirement':
        findings[0]['requirement_ids'] = ['ghost']
    elif case == 'unknown-side-effect':
        findings[0]['prohibited_side_effect_ids'] = ['AC1:99']
    elif case == 'verified-without-replay':
        findings[0]['replay'] = None
    elif case == 'verified-passed-replay':
        findings[0]['replay'].update(status='passed', exit_code=0)
        findings[0]['actual'].update(status='passed', exit_code=0)
    elif case == 'blocked-with-success-reason':
        findings[0]['status'] = 'blocked'
    elif case == 'replay-binding':
        findings[0]['replay']['candidate_digest'] = 'sha256:' + 'b' * 64
    elif case == 'blocked-actual-type':
        findings[0].update(status='blocked', reason_code='replay-claim-unconfirmed')
        findings[0]['actual']['exit_code'] = True
    record, evidence = bound_record(state.fresh_review.requests[0], coverage, tuple(findings))
    state = replace(state, fresh_review=FreshReviewProjection((record,)))
    rejected(state, command_for(state, (evidence,)), 'acceptance-fresh-review-evidence-invalid')


@pytest.mark.parametrize('case,reason', [('null-contract', 'contract-invalid'),
    ('missing-receipt', 'receipt-missing'), ('not-passed', 'receipt-not-passed'),
    ('stale-receipt', 'receipt-stale'), ('missing-candidate', 'candidate-missing')])
def test_contract_and_normal_receipts_precede_carrier_and_review_failures(clean, case, reason):
    state, command = clean
    document = state.legacy_passthrough.thaw()
    if case == 'null-contract':
        document['acceptance_contract'] = None
    elif case == 'missing-receipt':
        document['verification_receipts'] = []
    elif case == 'not-passed':
        document['verification_receipts'][0]['status'] = 'blocked'
    elif case == 'stale-receipt':
        document['verification_receipts'][0]['contract_digest'] = 'sha256:' + 'b' * 64
    else:
        command = replace(command, acceptance_candidate_digests=freeze_json_value({}))
    state = replace(state, legacy_passthrough=freeze_json_value(document))
    command = replace(command, fresh_review_evidence=(), fresh_review_bindings=None)
    rejected(state, command, 'acceptance-' + reason)


@pytest.mark.parametrize('error', [ValueError, TypeError, RecursionError])
def test_contract_digest_failures_return_the_contract_reason(clean, monkeypatch, error):
    import acceptance_contract
    def invalid(_):
        raise error('invalid contract')
    monkeypatch.setattr(acceptance_contract, 'canonical_contract_digest', invalid)
    state, command = clean
    document = state.legacy_passthrough.thaw()
    document.pop('verification_receipts')
    rejected(replace(state, legacy_passthrough=freeze_json_value(document)), command, 'acceptance-contract-invalid')


def test_malformed_ledger_is_a_contract_failure_before_receipts(clean):
    state, command = clean
    document = state.legacy_passthrough.thaw()
    document['acceptance_contract']['requirements'] = None
    document['verification_receipts'] = []
    rejected(replace(state, legacy_passthrough=freeze_json_value(document)), command, 'acceptance-contract-invalid')


@pytest.mark.parametrize('case,reason', [('missing', 'fresh-review-missing'), ('stale', 'fresh-review-stale'),
    ('running', 'fresh-review-pending'), ('non-independent', 'fresh-review-non-independent'), ('open', 'coverage-open')])
def test_latest_partial_criterion_uses_the_same_table_as_whole_coverage(clean, case, reason):
    state, command = with_contract(clean, lambda c: c['criteria'].append(dict(c['criteria'][0], id='AC2')))
    whole = state.fresh_review.requests[0]
    coverage = command.fresh_review_evidence[0].coverage.thaw()
    coverage['criterion_results'].append(dict(criterion_id='AC2', status='searched', reason_code='none'))
    coverage['requirements'][0]['criterion_ids'].append('AC2')
    whole, full_evidence = bound_record(whole, coverage, criterion_ids=('AC1', 'AC2'),
        candidate_bindings=(*whole.request.candidate_bindings, *(replace(b, criterion_id='AC2') for b in whole.request.candidate_bindings)))
    if case == 'missing':
        # No whole attempt exists; even a stale partial result cannot outrank missing.
        partial_coverage = command.fresh_review_evidence[0].coverage.thaw()
        partial_coverage.update(status='open', open_requirement_ids=['R1'])
        partial, partial_evidence = bound_record(state.fresh_review.requests[0], partial_coverage)
        rows = (partial,)
        evidence = (partial_evidence,)
    else:
        coverage.update(status='open', open_requirement_ids=['R1'])
        coverage['criterion_results'] = [dict(criterion_id='AC2', status='searched', reason_code='none')]
        partial, partial_evidence = bound_record(whole, coverage, request_id='partial', nonce='partial-nonce',
            criterion_ids=('AC2',), candidate_bindings=tuple(b for b in whole.request.candidate_bindings if b.criterion_id == 'AC2'))
        if case in ('stale', 'running'):
            partial = replace(partial, status='running', result=None)
            evidence = (full_evidence,)
        else:
            if case == 'non-independent':
                terminal = partial.result.thaw()
                terminal['independent'] = False
                terminal['launch_receipt']['context_mode'] = 'inline'
                terminal['launch_digest'] = canonical_digest(terminal['launch_receipt'])
                partial = replace(partial, result=freeze_json_value(terminal))
            evidence = (full_evidence, partial_evidence)
        rows = (whole, partial)
    state = replace(state, fresh_review=FreshReviewProjection(rows))
    command = command_for(state, evidence)
    if case in ('missing', 'stale'):
        inputs = command.fresh_review_bindings.input_digests
        command = replace(command, fresh_review_bindings=replace(command.fresh_review_bindings,
            input_digests=(*inputs[:-1], (inputs[-1][0], 'sha256:' + 'b' * 64))))
    rejected(state, command, 'acceptance-' + reason)


@pytest.mark.parametrize('case,reason,superseded', [('running', 'fresh-review-pending', False),
    ('non-independent', 'fresh-review-non-independent', False), ('open', 'coverage-open', False),
    ('non-independent', 'unresolved-finding', True), ('open', 'unresolved-finding', True)])
def test_conditions_three_four_five_have_stable_priority(clean, published, case, reason, superseded):
    state, command = clean
    coverage = command.fresh_review_evidence[0].coverage.thaw()
    finding = json.loads(published[4][0])
    coverage['open_finding_ids'] = ['finding-1']
    if case == 'open':
        coverage.update(status='open', open_requirement_ids=['R1'])
        coverage['requirements'][0].update(status='open', reason_code='unconfirmed')
    older, evidence = bound_record(state.fresh_review.requests[0], coverage, (finding,), request_id='older', nonce='older-nonce')
    if case != 'open':
        terminal = older.result.thaw()
        terminal['independent'] = False
        terminal['launch_receipt']['context_mode'] = 'inline'
        terminal['launch_digest'] = canonical_digest(terminal['launch_receipt'])
        older = replace(older, result=freeze_json_value(terminal))
    latest = state.fresh_review.requests[0]
    carried = (evidence, *command.fresh_review_evidence)
    if case == 'running':
        latest = replace(latest, status='running', result=None)
        carried = (evidence,)
    rows = (older, latest) if case == 'running' or superseded else (latest, older)
    state = replace(state, fresh_review=FreshReviewProjection(rows))
    rejected(state, command_for(state, carried), 'acceptance-' + reason)


def test_old_contract_and_candidate_do_not_silently_dismiss_open_findings(clean, published):
    state, command = clean
    finding = json.loads(published[4][0])
    finding['replay']['contract_digest'] = 'sha256:' + 'b' * 64
    coverage = command.fresh_review_evidence[0].coverage.thaw()
    coverage['open_finding_ids'] = ['finding-1']
    older, evidence = bound_record(state.fresh_review.requests[0], coverage, (finding,),
        request_id='older', nonce='older-nonce', contract_digest='sha256:' + 'b' * 64)
    state = replace(state, fresh_review=FreshReviewProjection((older, *state.fresh_review.requests)))
    command = command_for(state, (evidence, *command.fresh_review_evidence))
    command = replace(command, fresh_review_bindings=replace(command.fresh_review_bindings,
        input_digests=((older.request.request_id, 'sha256:' + 'c' * 64), command.fresh_review_bindings.input_digests[1])))
    rejected(state, command, 'acceptance-unresolved-finding')


@pytest.mark.parametrize('case,reason', [('unexecuted', 'unresolved-finding'),
    ('passed', 'unresolved-finding'), ('unknown-reason', 'fresh-review-evidence-invalid')])
def test_blocked_replay_facts_remain_open_and_require_consistent_reasons(clean, published, case, reason):
    state, command = clean
    finding = json.loads(published[4][0])
    finding['status'] = 'blocked'
    finding['reason_code'] = 'replay-claim-unconfirmed'
    if case == 'passed':
        finding['replay'].update(status='passed', exit_code=0)
        finding['actual'].update(status='passed', exit_code=0)
    else:
        finding.update(replay=None, actual={}, reason_code='replay-budget-exceeded' if case == 'unexecuted' else 'invented')
    coverage = command.fresh_review_evidence[0].coverage.thaw()
    coverage['open_finding_ids'] = ['finding-1']
    record, evidence = bound_record(state.fresh_review.requests[0], coverage, (finding,))
    rejected(replace(state, fresh_review=FreshReviewProjection((record,))),
             command_for(state, (evidence,)), 'acceptance-' + reason)


@pytest.mark.parametrize('closed', [False, True])
def test_clean_and_legacy_sessions_use_the_same_guard_on_both_state_carriers(clean, closed):
    state, command = clean
    if closed:
        state = replace(state, legacy_passthrough=None, extensions=state.legacy_passthrough)
    assert acceptance_completion_rejection(state, command) is None
    assert decide(state, command).accepted
    document = state.extensions.thaw() if closed else state.legacy_passthrough.thaw()
    document.pop('acceptance_contract')
    key = 'extensions' if closed else 'legacy_passthrough'
    state = replace(state, **{key: freeze_json_value(document)})
    command = replace(command, fresh_review_evidence=None, fresh_review_bindings=True)
    assert acceptance_completion_rejection(state, command) is None
    assert decide(state, command).accepted


@pytest.mark.parametrize('gate,reason', [('artifact', 'artifact-gate-unsatisfied'),
    ('specialist', 'specialist-gate-unsatisfied'), ('score', 'authoritative-score-required'),
    ('force', 'force-approval-required')])
def test_clean_acceptance_keeps_existing_completion_gates(clean, gate, reason):
    state, command = clean
    if gate == 'score':
        state = replace(state, scores=(replace(state.scores[0], authoritative=False),))
    else:
        changes = {'artifact': dict(artifact_gate_satisfied=False), 'specialist': dict(specialist_gate_satisfied=False),
                   'force': dict(force=True)}
        command = replace(command, **changes[gate])
    assert acceptance_completion_rejection(state, command) is None
    decision = decide(state, command)
    assert decision.rejection.code == reason
    assert decision.transition is None and decision.effects == ()


def test_two_application_observations_must_describe_the_same_current_candidate(clean):
    state, command = clean
    candidate = 'sha256:' + 'b' * 64
    document = receipts(state.legacy_passthrough.thaw(), candidate)
    state = replace(state, legacy_passthrough=freeze_json_value(document))
    command = replace(command, acceptance_candidate_digests=freeze_json_value({'AC1': candidate}))
    rejected(state, command, 'acceptance-fresh-review-stale')


def test_context_only_finding_does_not_create_a_required_obligation(clean, published):
    state, command = with_contract(clean, lambda c: c['requirements'][0].update(classification='context'))
    finding = json.loads(published[4][0])
    finding['prohibited_side_effect_ids'] = []
    finding['replay']['contract_digest'] = command.fresh_review_bindings.contract_digest
    record, evidence = bound_record(state.fresh_review.requests[0], command.fresh_review_evidence[0].coverage.thaw(), (finding,))
    state = replace(state, fresh_review=FreshReviewProjection((record,)))
    command = command_for(state, (evidence,))
    assert acceptance_completion_rejection(state, command) is None
    assert decide(state, command).accepted
