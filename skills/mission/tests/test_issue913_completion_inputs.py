"""Inert completion observations: bind public bytes without enabling pass."""
import base64
import json
from dataclasses import replace

import pytest

from mission_kernel.fresh_review import canonical_bytes, FreshReviewError
from mission_kernel.json_codec import freeze_json_value
from .test_issue896_completed import completed_carrier
from .test_issue879_completion_cli import completion_session


@pytest.fixture
def evidence_carrier(published):
    from mission_kernel.fresh_review_completion import decode_completion_evidence
    record, terminal, _, coverage, findings = published
    return decode_completion_evidence(record.request, terminal, json.loads(coverage),
                                      tuple(json.loads(item) for item in findings))


@pytest.fixture
def published(completed_carrier):
    from mission_kernel.fresh_review_output import decode_output
    from mission_kernel.fresh_review_publish import completed_evidence
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    record, command, contract = completed_carrier
    coverage, findings = completed_evidence(
        decode_output(json.loads(base64.b64decode(command.output_base64))),
        record.request, contract, command.replay_results)
    from .test_issue917_fresh_review_bounds import maximum_intent
    from mission_kernel.fresh_review import canonical_digest
    from mission_kernel.fresh_review_dispatch import reservation_id_for_operation, budget_class_for_fresh_review_dispatch
    dispatch = maximum_intent()
    dispatch.update(invocation_id='inv_' + canonical_digest(record.request.request_id)[7:39],
        operation_id='dispatch', fencing_epoch=2, parent_identity='parent',
        outbound_packet_digest=record.request.input_digest, iteration=record.request.iteration,
        reservation_id=reservation_id_for_operation('dispatch'), budget_class=budget_class_for_fresh_review_dispatch())
    record = replace(record, status='completed', result=command.receipt,
        dispatch=freeze_json_value(dispatch), intent_digest=record.request.contract_digest,
        payload_digest=record.request.contract_digest, launch_operation_id='dispatch')
    return record, decode_terminal_receipt(command.receipt.thaw()), contract, coverage, findings


def test_emitted_evidence_retains_all_completion_facts_without_reclassifying_findings(published):
    from mission_kernel.fresh_review_completion import decode_completion_evidence
    record, terminal, _, coverage, findings = published
    carrier = decode_completion_evidence(record.request, terminal, json.loads(coverage),
                                         tuple(json.loads(item) for item in findings))
    assert carrier.request_id == record.request.request_id
    assert carrier.coverage.thaw()['criterion_results'] == [
        dict(criterion_id='AC1', status='searched', reason_code='none')]
    assert carrier.coverage.thaw()['open_requirement_ids'] == []
    assert carrier.coverage.thaw()['open_finding_ids'] == ['finding-1']
    finding = carrier.findings[0].thaw()
    assert (finding['requirement_ids'], finding['prohibited_side_effect_ids']) == (['R1'], ['AC1:0'])
    assert (finding['status'], finding['resolution'], finding['severity']) == ('verified', 'open', 'Low')
    assert canonical_bytes(carrier.coverage.thaw()) == coverage
    assert canonical_bytes(finding) == findings[0]


def test_unexecuted_replay_preserves_empty_observation_and_blocked_finding(completed_carrier):
    from mission_kernel.fresh_review_output import decode_output
    from mission_kernel.fresh_review_publish import completed_evidence, evidence_claim, reference
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    from mission_kernel.fresh_review_completion import decode_completion_evidence
    record, command, contract = completed_carrier
    raw = json.loads(base64.b64decode(command.output_base64))
    raw['criterion_results'][0]['findings'][0]['command_id'] = 'unregistered'
    replays = (freeze_json_value(dict(finding_id='finding-1', reason_code='replay-unsupported', replay=None)),)
    coverage, findings = completed_evidence(decode_output(raw), record.request, contract, replays)
    terminal = command.receipt.thaw()
    terminal['coverage_receipt']['evidence_ref'] = reference(evidence_claim('fresh-review-coverage', coverage))
    terminal['findings'] = [reference(evidence_claim('fresh-review-finding', item)) for item in findings]
    decoded = decode_completion_evidence(record.request, decode_terminal_receipt(terminal),
                                         json.loads(coverage), tuple(json.loads(item) for item in findings))
    finding = decoded.findings[0].thaw()
    assert finding['actual'] == {} and finding['replay'] is None
    assert (finding['status'], finding['resolution']) == ('blocked', 'open')


@pytest.fixture
def gate_state(published):
    from mission_kernel import decode_snapshot
    from mission_kernel.fresh_review import FreshReviewProjection
    from mission_kernel.model import Phase
    from .mission_state_fixture_corpus import issue483_corpus
    record, _, contract, _, _ = published
    state = decode_snapshot(canonical_bytes(issue483_corpus()['v4'])).state
    document = state.legacy_passthrough.thaw()
    document['acceptance_contract'] = contract
    return replace(state, fresh_review=FreshReviewProjection((record,)),
                   control=replace(state.control, phase=Phase.SCORING, terminal_outcome=None,
                                   passes=False, loop_active=True),
                   legacy_passthrough=freeze_json_value(document))


def test_gate_and_preflight_reject_tampered_carrier_without_changing_state(gate_state, published):
    import copy
    from mission_kernel.commands import MarkPass
    from mission_kernel.fresh_review_completion import decode_completion_evidence
    from mission_kernel.transitions import acceptance_completion_rejection, decide
    record, terminal, _, coverage, findings = published
    carrier = decode_completion_evidence(record.request, terminal, json.loads(coverage),
                                         tuple(json.loads(item) for item in findings))
    forged = carrier.coverage.thaw()
    forged['open_finding_ids'] = []
    command = MarkPass(fresh_review_evidence=(replace(carrier, coverage=freeze_json_value(forged)),))
    before = copy.deepcopy(gate_state)
    assert acceptance_completion_rejection(gate_state, command) == 'acceptance-fresh-review-evidence-mismatch'
    decision = decide(gate_state, command)
    assert decision.rejection.code == 'acceptance-fresh-review-evidence-mismatch'
    assert decision.transition is None and decision.effects == () and decision.events == ()
    assert gate_state == before


def test_application_reads_bound_evidence_and_reobserves_request_inputs(published, tmp_path, monkeypatch):
    from mission_application.fresh_review_completion import observe_completion_inputs
    from mission_application import fresh_review as prepare
    from mission_kernel.fresh_review import FreshReviewProjection, projection_document, canonical_digest
    from types import SimpleNamespace
    record, terminal, contract, coverage, findings = published
    for ref, content in zip((terminal.coverage_receipt.evidence_ref, *terminal.findings), (coverage, *findings)):
        path = tmp_path / ref.relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    seen = []
    def capture(root, commands):
        seen.append((root, set(commands)))
        return {key: SimpleNamespace(digest='sha256:' + str(index) * 64, files=())
                for index, key in enumerate(sorted(commands), 1)}
    monkeypatch.setattr(prepare, '_capture', capture)
    data = dict(acceptance_contract=contract, fresh_review=projection_document(FreshReviewProjection((record,))))
    result = observe_completion_inputs(data, root=tmp_path, load_policy=lambda _: contract['verifier_policy'])
    assert result.evidence[0].findings[0].thaw()['resolution'] == 'open'
    assert set(dict(result.bindings.candidate_snapshots)) == {'command-1', 'replay-1'}
    snapshots = capture(tmp_path, contract['verifier_policy']['commands'])
    expected = canonical_digest(prepare.build_input_packet(contract, record.request.perspective, snapshots))
    assert result.bindings.input_digests == ((record.request.request_id, expected),)
    assert expected != record.request.input_digest
    assert seen[0] == (tmp_path, {'command-1', 'replay-1'})


def test_application_carries_identical_inputs_to_preflight_and_authoritative_command(published, monkeypatch):
    import contextlib
    from types import SimpleNamespace
    from mission_application import review
    from mission_application.fresh_review_completion import FreshReviewCompletionInputs
    from mission_kernel.fresh_review_completion import decode_completion_evidence
    from mission_kernel.fresh_review_coverage import FreshReviewBindings
    record, terminal, contract, coverage, findings = published
    evidence = decode_completion_evidence(record.request, terminal, json.loads(coverage),
                                         tuple(json.loads(item) for item in findings))
    observed = FreshReviewCompletionInputs((evidence,), FreshReviewBindings(
        record.request.contract_digest, ((record.request.request_id, record.request.input_digest),),
        tuple((item.command_id, item.snapshot_digest) for item in record.request.candidate_bindings)))
    data = dict(acceptance_contract=contract)
    captured = []
    repo = SimpleNamespace(transaction=contextlib.nullcontext, load=lambda: data,
        execute=lambda command, **_: captured.append(command) or SimpleNamespace(
            decision=SimpleNamespace(accepted=False, rejection=SimpleNamespace(code='acceptance-fresh-review-pending'))))
    services = review.MarkPassServices(
        verify_force_approval=lambda _: {}, validate_force_terminal=lambda *_: None,
        validate_score_evidence=lambda *_: None, validate_artifact_gate=lambda *_: None,
        validate_specialist_gate=lambda *_: None, transition_phase=lambda *_: None,
        optional_unclosed_skills=lambda _: [], selection_id=lambda _: None,
        capture_fresh_review_completion=lambda _: observed)
    monkeypatch.setattr(review, 'decode_mission_state', lambda _: object())
    # Deliberately let preflight proceed; the authoritative command must still
    # carry the very same frozen facts. This grants no real pass authority.
    monkeypatch.setattr(review, 'acceptance_completion_rejection', lambda _, command: captured.append(command))
    with pytest.raises(review.ReviewFailure, match='acceptance-fresh-review-pending'):
        review.mark_pass(repo, review.MarkPassRequest(True, 'fixture', True, '', '2026-01-01T00:00:00Z'), services)
    assert len(captured) == 2
    for command in captured:
        assert command.fresh_review_evidence is observed.evidence
        assert command.fresh_review_bindings is observed.bindings


@pytest.mark.parametrize('scope', ['coverage', 'finding'])
@pytest.mark.parametrize('value', [None, True, [], {}, '', 'forged', 0, 1])
def test_content_tampering_cannot_supply_a_clean_verdict(gate_state, evidence_carrier, scope, value):
    from mission_kernel.commands import MarkPass
    from mission_kernel.transitions import acceptance_completion_rejection, decide
    if scope == 'coverage':
        raw = evidence_carrier.coverage.thaw()
        raw['open_finding_ids'] = value
        forged = replace(evidence_carrier, coverage=freeze_json_value(raw))
    else:
        raw = evidence_carrier.findings[0].thaw()
        raw['resolution'] = value
        forged = replace(evidence_carrier, findings=(freeze_json_value(raw),))
    command = MarkPass(fresh_review_evidence=(forged,))
    assert acceptance_completion_rejection(gate_state, command) == 'acceptance-fresh-review-evidence-mismatch'
    result = decide(gate_state, command)
    assert result.rejection.code == 'acceptance-fresh-review-evidence-mismatch'
    assert result.transition is None and result.effects == ()


@pytest.mark.parametrize('case', ['wrong-request', 'duplicate', 'missing-finding', 'untyped-coverage',
                                  'untyped-finding', 'list-findings', 'list-evidence', 'null-evidence'])
def test_malformed_or_cross_request_carriers_are_not_ignored(gate_state, evidence_carrier, case):
    from mission_kernel.commands import MarkPass
    from mission_kernel.transitions import acceptance_completion_rejection
    changes = {'wrong-request': {'request_id': 'other'}, 'missing-finding': {'findings': ()},
        'untyped-coverage': {'coverage': {}}, 'untyped-finding': {'findings': ({},)},
        'list-findings': {'findings': list(evidence_carrier.findings)}}
    evidence = (replace(evidence_carrier, **changes.get(case, {})),)
    if case == 'duplicate':
        evidence *= 2
    elif case == 'list-evidence':
        evidence = list(evidence)
    elif case == 'null-evidence':
        evidence = None
    assert acceptance_completion_rejection(gate_state, MarkPass(fresh_review_evidence=evidence)) == (
        'acceptance-fresh-review-evidence-invalid')


@pytest.mark.parametrize('identifier', [[], {}, True, None])
def test_malformed_carrier_identity_rejects_before_dictionary_lookup(gate_state, evidence_carrier, identifier):
    from mission_kernel.commands import MarkPass
    from mission_kernel.transitions import acceptance_completion_rejection
    assert acceptance_completion_rejection(gate_state, MarkPass(fresh_review_evidence=(
        replace(evidence_carrier, request_id=identifier),))) == 'acceptance-fresh-review-evidence-invalid'


def test_supplied_carrier_cannot_omit_an_older_completed_attempt(gate_state, evidence_carrier):
    from mission_kernel.commands import MarkPass
    from mission_kernel.fresh_review import FreshReviewProjection
    from mission_kernel.transitions import acceptance_completion_rejection
    record = gate_state.fresh_review.requests[0]
    other = replace(record, request=replace(record.request, request_id='older-request', nonce='older-nonce'))
    state = replace(gate_state, fresh_review=FreshReviewProjection((other, record)))
    assert acceptance_completion_rejection(state, MarkPass(fresh_review_evidence=(evidence_carrier,))) == (
        'acceptance-fresh-review-evidence-invalid')


@pytest.mark.parametrize('scope', ['coverage', 'finding'])
def test_kernel_checks_ref_size_independently_of_digest(gate_state, evidence_carrier, scope):
    from mission_kernel.commands import MarkPass
    from mission_kernel.fresh_review import FreshReviewProjection
    from mission_kernel.transitions import acceptance_completion_rejection
    record = gate_state.fresh_review.requests[0]
    terminal = record.result.thaw()
    reference = terminal['coverage_receipt']['evidence_ref'] if scope == 'coverage' else terminal['findings'][0]
    reference['size'] += 1
    state = replace(gate_state, fresh_review=FreshReviewProjection((
        replace(record, result=freeze_json_value(terminal)),)))
    assert acceptance_completion_rejection(state, MarkPass(fresh_review_evidence=(evidence_carrier,))) == (
        'acceptance-fresh-review-evidence-mismatch')


def test_same_size_finding_cannot_change_its_requirement_binding(gate_state, evidence_carrier):
    from mission_kernel.commands import MarkPass
    from mission_kernel.transitions import acceptance_completion_rejection
    finding = evidence_carrier.findings[0].thaw()
    finding['requirement_ids'] = ['R2']
    assert len(canonical_bytes(finding)) == len(canonical_bytes(evidence_carrier.findings[0].thaw()))
    forged = replace(evidence_carrier, findings=(freeze_json_value(finding),))
    assert acceptance_completion_rejection(gate_state, MarkPass(fresh_review_evidence=(forged,))) == (
        'acceptance-fresh-review-evidence-mismatch')


@pytest.mark.parametrize('case', ['contract', 'input-missing', 'input-extra', 'input-duplicate',
    'replay-missing', 'snapshot-extra', 'snapshot-duplicate', 'input-shape', 'snapshot-shape',
    'input-digest', 'snapshot-digest', 'input-id', 'untyped'])
def test_bindings_require_exact_request_and_command_keys(gate_state, published, case):
    from mission_kernel.commands import MarkPass
    from mission_kernel.fresh_review_coverage import FreshReviewBindings
    from mission_kernel.transitions import acceptance_completion_rejection, decide
    record = published[0]
    bindings = FreshReviewBindings(record.request.contract_digest,
        ((record.request.request_id, record.request.input_digest),),
        tuple((item.command_id, item.snapshot_digest) for item in record.request.candidate_bindings))
    changes = {'contract': {'contract_digest': 'sha256:' + '0' * 64},
        'input-missing': {'input_digests': ()}, 'input-extra': {'input_digests': (*bindings.input_digests, ('other', record.request.input_digest))},
        'input-duplicate': {'input_digests': bindings.input_digests * 2},
        'replay-missing': {'candidate_snapshots': bindings.candidate_snapshots[:1]},
        'snapshot-extra': {'candidate_snapshots': (*bindings.candidate_snapshots, ('other', record.request.input_digest))},
        'snapshot-duplicate': {'candidate_snapshots': bindings.candidate_snapshots * 2},
        'input-shape': {'input_digests': []}, 'snapshot-shape': {'candidate_snapshots': [('command-1', True)]},
        'input-digest': {'input_digests': ((record.request.request_id, True),)},
        'snapshot-digest': {'candidate_snapshots': (('command-1', 'invalid'),)},
        'input-id': {'input_digests': (([], record.request.input_digest),)}}
    malformed = {} if case == 'untyped' else replace(bindings, **changes[case])
    command = MarkPass(fresh_review_bindings=malformed)
    assert acceptance_completion_rejection(gate_state, command) == 'acceptance-fresh-review-bindings-invalid'
    assert decide(gate_state, command).rejection.code == 'acceptance-fresh-review-bindings-invalid'


@pytest.mark.parametrize('closed', [False, True])
def test_valid_carriers_are_inert_and_absent_carriers_remain_compatible(gate_state, evidence_carrier, published, closed):
    from mission_kernel.commands import MarkPass
    from mission_kernel.fresh_review_coverage import FreshReviewBindings
    from mission_kernel.transitions import acceptance_completion_rejection
    state = gate_state if not closed else replace(gate_state, legacy_passthrough=None,
                                                 extensions=gate_state.legacy_passthrough)
    record = published[0]
    bindings = FreshReviewBindings(record.request.contract_digest,
        ((record.request.request_id, 'sha256:' + '1' * 64),),
        tuple((item.command_id, 'sha256:' + '2' * 64) for item in record.request.candidate_bindings))
    assert acceptance_completion_rejection(state, MarkPass()) == 'acceptance-coverage-pending'
    assert acceptance_completion_rejection(state, MarkPass(fresh_review_evidence=(evidence_carrier,),
        fresh_review_bindings=bindings)) == 'acceptance-coverage-pending'
    legacy = state.legacy_passthrough.thaw() if not closed else state.extensions.thaw()
    legacy.pop('acceptance_contract')
    key = 'legacy_passthrough' if not closed else 'extensions'
    assert acceptance_completion_rejection(replace(state, **{key: freeze_json_value(legacy)}),
        MarkPass(fresh_review_evidence=None, fresh_review_bindings=True)) is None
    legacy['acceptance_contract'] = None
    assert acceptance_completion_rejection(replace(state, **{key: freeze_json_value(legacy)}), MarkPass()) == (
        'acceptance-contract-invalid')


def test_matching_carrier_does_not_enable_the_unconditional_fresh_review_gate(gate_state, evidence_carrier):
    from acceptance_contract import canonical_contract_digest, verifier_definition_digest
    from mission_kernel.commands import MarkPass
    from mission_kernel.transitions import acceptance_completion_rejection, decide
    # Only the persisted contract coverage fixture is advanced; no public
    # producer or CLI success is claimed. Fresh-review pending remains final.
    document = gate_state.legacy_passthrough.thaw()
    contract = document['acceptance_contract']
    contract['coverage'] = {'status': 'valid'}
    digest = canonical_contract_digest(contract)
    candidate = 'sha256:' + 'a' * 64
    document['verification_receipts'] = [dict(criterion_id='AC1', status='passed',
        contract_digest=digest, verifier_policy_digest=contract['verifier_policy']['digest'],
        verifier_definition_digest=verifier_definition_digest(contract['verifier_policy']['commands']['command-1']),
        candidate_digest=candidate)]
    state = replace(gate_state, legacy_passthrough=freeze_json_value(document))
    command = MarkPass(fresh_review_evidence=(evidence_carrier,),
                       acceptance_candidate_digests=freeze_json_value({'AC1': candidate}))
    assert acceptance_completion_rejection(state, command) == 'acceptance-fresh-review-pending'
    assert decide(state, command).rejection.code == 'acceptance-fresh-review-pending'


@pytest.mark.parametrize('case,reason', [
    ('digest', 'mismatch'), ('size', 'mismatch'), ('missing', 'unavailable'),
    ('symlink', 'unavailable'), ('directory', 'unavailable'), ('oversized', 'unavailable'),
    ('duplicate-key', 'invalid'), ('invalid-json', 'invalid'), ('non-object', 'invalid'),
    ('invalid-utf8', 'invalid'),
    ('deep-object', 'invalid'), ('deep-array', 'invalid'),
])
def test_application_never_decodes_unbound_or_unsafe_bytes(published, tmp_path, case, reason):
    import hashlib
    from mission_application.fresh_review_completion import _read
    reference = published[1].coverage_receipt.evidence_ref
    raw = published[3]
    if case == 'size':
        reference = replace(reference, size=reference.size + 1)
    elif case == 'digest':
        raw = raw.replace(b'AC1', b'AC2')
    elif case == 'oversized':
        raw = b'x' * (262144 + 1)
    elif case in ('duplicate-key', 'invalid-json', 'non-object', 'invalid-utf8', 'deep-object', 'deep-array'):
        raw = {'duplicate-key': b'{"id":1,"id":2}', 'invalid-json': b'{',
               'non-object': b'[]', 'invalid-utf8': b'\xff',
               'deep-object': b'{"a":' * 20000 + b'0' + b'}' * 20000,
               'deep-array': b'{"a":' + b'[' * 100000 + b'0' + b']' * 100000 + b'}'}[case]
        reference = replace(reference, digest='sha256:' + hashlib.sha256(raw).hexdigest(), size=len(raw))
    path = tmp_path / reference.relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if case == 'symlink':
        source = tmp_path / 'source'
        source.write_bytes(raw)
        path.symlink_to(source)
    elif case == 'directory':
        path.mkdir()
    elif case != 'missing':
        path.write_bytes(raw)
    with pytest.raises(FreshReviewError, match='^acceptance-fresh-review-evidence-' + reason + '$'):
        try:
            _read(tmp_path, reference)
        except RecursionError:
            pytest.fail('evidence recursion escaped the reason-code boundary', pytrace=False)


def test_real_prepare_packet_is_reobserved_and_candidate_change_is_not_hidden(completion_session, run_cli):
    from .test_issue895_fresh_review import _prepare
    from mission_application.fresh_review_completion import observe_completion_inputs
    from mission_application.verifier_policy import load
    from .test_issue878_verification_runner import _replay_policy
    from mission_kernel.fresh_review import canonical_digest
    root, state, schema = completion_session
    policy = _replay_policy()
    policy['commands'][1]['declared_untracked'] = ['replay-only.txt']
    (root / 'replay-only.txt').write_text('replay input')
    (root / '.mission/verifiers.json').write_bytes(canonical_bytes(policy))
    state['acceptance_contract'].update(verifier_policy_digest=canonical_digest(policy),
        verifier_policy=dict(digest=canonical_digest(policy), commands={item['id']: item for item in policy['commands']}))
    root, request = _prepare((root, state, schema), run_cli)
    state = json.loads(run_cli('get', cwd=root).stdout)
    observed = observe_completion_inputs(state, root=root, load_policy=load)
    assert observed.bindings.input_digests == ((request['request_id'], request['input_digest']),)
    assert dict(observed.bindings.candidate_snapshots) == {
        item['command_id']: item['snapshot_digest'] for item in request['candidate_bindings']}
    assert observed.evidence == ()
    assert len(set(dict(observed.bindings.candidate_snapshots).values())) == 2
    (root / 'replay-only.txt').write_text('changed replay input')
    replay_changed = observe_completion_inputs(state, root=root, load_policy=load)
    assert dict(replay_changed.bindings.candidate_snapshots)['project-test'] == dict(observed.bindings.candidate_snapshots)['project-test']
    assert dict(replay_changed.bindings.candidate_snapshots)['replay-test'] != dict(observed.bindings.candidate_snapshots)['replay-test']
    assert replay_changed.bindings.input_digests != observed.bindings.input_digests
    (root / 'app.txt').write_text('changed candidate')
    changed = observe_completion_inputs(state, root=root, load_policy=load)
    assert changed.bindings.input_digests != observed.bindings.input_digests
    assert changed.bindings.candidate_snapshots != observed.bindings.candidate_snapshots


@pytest.mark.parametrize('scope,key,value', [
    ('coverage', 'schema', 'future'), ('coverage', 'request_id', 'other'),
    ('coverage', 'request_digest', 'sha256:' + '0' * 64), ('coverage', 'candidate_digest', 'sha256:' + '0' * 64),
    ('coverage', 'status', 'open'), ('coverage', 'open_requirement_ids', ['../escape']),
    ('coverage', 'open_finding_ids', ['duplicate', 'duplicate']),
    ('coverage', 'criterion_results', None), ('coverage', 'criterion_results', []),
    ('coverage', 'criterion_results', [dict(criterion_id='AC1', status='future', reason_code='none')]),
    ('coverage', 'requirements', []), ('finding', 'schema', 'future'),
    ('finding', 'request_id', 'other'), ('finding', 'request_digest', 'sha256:' + '0' * 64),
    ('finding', 'candidate_digest', 'sha256:' + '0' * 64), ('finding', 'status', 'future'),
    ('finding', 'resolution', 'resolved'), ('finding', 'replay', []), ('finding', 'severity', 'Critical'),
    ('finding', 'criterion_id', 'unselected'), ('finding', 'reason_code', None),
    ('finding', 'replay_evidence_ref', None), ('finding', 'extra', True),
])
def test_authenticated_but_malformed_evidence_is_reason_coded(published, scope, key, value):
    from mission_kernel.fresh_review import canonical_digest
    from mission_kernel.fresh_review_completion import decode_completion_evidence
    record, terminal, _, coverage, findings = published
    raw, rows = json.loads(coverage), [json.loads(item) for item in findings]
    target = raw if scope == 'coverage' else rows[0]
    target[key] = value
    def rebind(ref, document):
        digest = canonical_digest(document)
        return replace(ref, digest=digest, size=len(canonical_bytes(document)),
                       relative_path='evidence/fresh-review/' + digest[7:] + '.json')
    terminal = replace(terminal, coverage_receipt=replace(terminal.coverage_receipt,
        evidence_ref=rebind(terminal.coverage_receipt.evidence_ref, raw)),
        findings=tuple(rebind(ref, document) for ref, document in zip(terminal.findings, rows)))
    with pytest.raises(FreshReviewError, match='^acceptance-fresh-review-evidence-invalid$'):
        decode_completion_evidence(record.request, terminal, raw, tuple(rows))


def test_observation_uses_each_request_subset_and_perspective(published, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from mission_application.fresh_review_completion import observe_completion_inputs
    from mission_application import fresh_review as prepare
    from mission_kernel.fresh_review import (
        FreshReviewProjection, FreshReviewRecord, canonical_digest, candidate_identity, projection_document)
    record, _, contract, _, _ = published
    first = FreshReviewRecord(record.request, 'prepare-first', record.prepare_intent_digest, record.prepare_payload_digest)
    request = replace(record.request, request_id='request-2', nonce='nonce-2', perspective='edge-cases',
        candidate_bindings=record.request.candidate_bindings[:1],
        candidate_digest=candidate_identity({'command-1': record.request.candidate_bindings[0].snapshot_digest}))
    second = FreshReviewRecord(request, 'prepare-second', record.prepare_intent_digest, record.prepare_payload_digest)
    snapshots = {key: SimpleNamespace(digest='sha256:' + 'b' * 64, files=())
                 for key in ('command-1', 'replay-1')}
    monkeypatch.setattr(prepare, '_capture', lambda *_: snapshots)
    data = dict(acceptance_contract=contract, fresh_review=projection_document(FreshReviewProjection((first, second))))
    observed = observe_completion_inputs(data, root=tmp_path, load_policy=lambda _: contract['verifier_policy'])
    assert dict(observed.bindings.input_digests) == {
        first.request.request_id: canonical_digest(prepare.build_input_packet(contract, first.request.perspective, snapshots)),
        second.request.request_id: canonical_digest(prepare.build_input_packet(contract, second.request.perspective,
                                                                            {'command-1': snapshots['command-1']}))}
    assert observed.evidence == ()


def test_kernel_completion_input_module_has_no_io_or_outer_layer_imports():
    import ast
    from pathlib import Path
    source = Path(__file__).parents[1] / 'lib/mission_kernel/fresh_review_completion.py'
    tree = ast.parse(source.read_text())
    names = [node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    names += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert not any(name.startswith(('mission_application', 'mission_persistence', 'pathlib', 'os', 'subprocess')) for name in names)
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'open'
                   for node in ast.walk(tree))


def test_contractless_observation_does_not_read_store_or_capture_candidates(tmp_path, monkeypatch):
    from mission_application import fresh_review_completion as app
    def unexpected(*_args, **_kwargs):
        pytest.fail('contractless completion must not invoke fresh-review observations')
    monkeypatch.setattr(app, '_read', unexpected)
    monkeypatch.setattr(app.prepare, '_capture', unexpected)
    observed = app.observe_completion_inputs({'fresh_review': 'legacy diagnostic'}, root=tmp_path, load_policy=unexpected)
    assert observed.evidence == () and observed.bindings is None


@pytest.mark.parametrize('case', ['missing-command', 'policy-changed', 'candidate-changed-during-read'])
def test_unavailable_current_bindings_do_not_return_saved_request_digests(published, tmp_path, monkeypatch, case):
    from types import SimpleNamespace
    from mission_application import fresh_review_completion as app
    from mission_kernel.fresh_review import FreshReviewProjection, FreshReviewRecord, projection_document
    record, _, contract, _, _ = published
    pending = FreshReviewRecord(record.request, 'prepare', record.prepare_intent_digest, record.prepare_payload_digest)
    data = dict(acceptance_contract=contract, fresh_review=projection_document(FreshReviewProjection((pending,))))
    if case == 'missing-command':
        data['acceptance_contract']['verifier_policy']['commands'].pop('replay-1')
        # A closed frozen policy must remain valid; remove its replay registration
        # too. The old request still names the missing command.
        data['acceptance_contract']['verifier_policy']['commands']['command-1'].pop('replay')
    calls = []
    def capture(_, commands):
        calls.append(True)
        return {key: SimpleNamespace(digest='sha256:' + str(len(calls)) * 64, files=()) for key in commands}
    monkeypatch.setattr(app.prepare, '_capture', capture)
    policy = {'digest': 'sha256:' + '0' * 64} if case == 'policy-changed' else contract['verifier_policy']
    with pytest.raises(FreshReviewError, match='^acceptance-fresh-review-bindings-unavailable$'):
        app.observe_completion_inputs(data, root=tmp_path, load_policy=lambda _: policy)


@pytest.mark.parametrize('policy', [None, [], 'policy', 1, {}, {'digest': None}, {'digest': []},
                                   KeyError('digest'), TypeError('policy'), RuntimeError('read'),
                                   OSError('read'), ValueError('policy'),
                                   RecursionError('policy')])
def test_policy_read_or_shape_failure_has_a_bindings_reason(published, tmp_path, monkeypatch, policy):
    from mission_application import fresh_review_completion as app
    data = dict(acceptance_contract=published[2])
    def load_policy(_):
        if isinstance(policy, BaseException):
            raise policy
        return policy
    monkeypatch.setattr(app.prepare, '_capture', lambda *_: pytest.fail('invalid policy reached capture'))
    with pytest.raises(FreshReviewError, match='^acceptance-fresh-review-bindings-unavailable$'):
        app.observe_completion_inputs(data, root=tmp_path, load_policy=load_policy)


@pytest.fixture
def assert_capture_rejection(published, tmp_path):
    import contextlib
    from types import SimpleNamespace
    from mission_application import review
    data = dict(acceptance_contract=published[2], passes=False, loop_active=True)
    original = json.dumps(data).encode()
    state_path = tmp_path / 'state.json'
    state_path.write_bytes(original)
    evidence_path = tmp_path / 'coverage.json'
    evidence_path.write_bytes(published[3])
    def unreachable(*_, **__):
        pytest.fail('capture rejection reached a gate or persistence')
    repo = SimpleNamespace(transaction=contextlib.nullcontext, load=lambda: data, execute=unreachable)
    services = review.MarkPassServices(
        verify_force_approval=unreachable, validate_force_terminal=unreachable,
        validate_score_evidence=unreachable, validate_artifact_gate=unreachable,
        validate_specialist_gate=unreachable, transition_phase=unreachable,
        optional_unclosed_skills=unreachable, selection_id=unreachable)
    def check(field, capture, reason):
        with pytest.raises(review.ReviewFailure) as error:
            review.mark_pass(repo, review.MarkPassRequest(True, 'fixture', True, '', '2026-01-01T00:00:00Z'),
                             replace(services, **{field: capture}))
        assert error.value.reason == reason
        assert json.dumps(data).encode() == original
        assert state_path.read_bytes() == original
        assert evidence_path.read_bytes() == published[3]
    return check


@pytest.mark.parametrize('field', ['capture_acceptance_candidates', 'capture_fresh_review_completion'])
@pytest.mark.parametrize('error_type', [KeyError, TypeError, RecursionError])
def test_mark_pass_capture_errors_are_review_failures_without_publication(assert_capture_rejection, field, error_type):
    def capture(_):
        raise error_type('capture')
    assert_capture_rejection(field, capture, 'acceptance-candidate-unavailable')


def test_mark_pass_preserves_the_policy_bindings_failure(assert_capture_rejection, tmp_path):
    from mission_application.fresh_review_completion import FreshReviewCompletionServices
    def load_policy(_):
        raise RuntimeError('policy read failed')
    assert_capture_rejection('capture_fresh_review_completion',
                             FreshReviewCompletionServices(tmp_path, load_policy),
                             'acceptance-fresh-review-bindings-unavailable')
