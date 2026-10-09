"""Inert import boundary: a wrong sender rejects; a bound bad output fails.

These pure checks do not confer replay verification or publication authority.
Public execution and atomic state publication are the subsequent split.
"""
import copy
from dataclasses import replace
import json

import pytest

from mission_kernel.fresh_review import canonical_bytes, canonical_digest, FreshReviewError
from mission_kernel.json_codec import freeze_json_value
from .test_issue895_fresh_review import _pure_projection, ADAPTER
from .test_issue909_fresh_review_receipts import launch_document


def running():
    from mission_kernel.fresh_review import request_document
    record = _pure_projection().requests[0]
    launch = launch_document()
    launch['request_digest'] = canonical_digest(request_document(record.request))
    return replace(record, status='running', operation_id='dispatch',
                   launch=freeze_json_value(launch), independent=True,
                   dispatch=freeze_json_value({'operation_id': 'dispatch', 'fencing_epoch': 2,
                                               'parent_identity': 'parent'}))


def observation(record=None):
    record = record or running()
    return dict(operation_id='dispatch', fencing_epoch=2, request_id=record.request.request_id,
                nonce=record.request.nonce, child_identity='child', process_exited=True,
                exit_code=0)


def output_document(record=None):
    from mission_kernel.fresh_review import request_document
    record = record or running()
    request = record.request
    return dict(schema='mission-fresh-review-output/1', request_id=request.request_id,
                nonce=request.nonce, request_digest=canonical_digest(request_document(request)),
                **{key: getattr(request, key) for key in ('mission_id', 'session_id',
                    'requirement_digest', 'contract_digest', 'verifier_policy_digest',
                    'candidate_digest', 'input_digest', 'adapter_registration_digest', 'iteration')},
                criterion_results=[dict(criterion_id='AC1', status='searched',
                                        reason_code='none', findings=[])],
                coverage=[dict(requirement_id='R1', classification_confirmed=True,
                               criterion_ids=['AC1'], status='valid', reason_code='none',
                               reason='The required criterion covers this span.')])


def finding(number=1):
    return dict(finding_id='finding-' + str(number), criterion_id='AC1',
                requirement_ids=['R1'], prohibited_side_effect_ids=['AC1:0'], severity='Low',
                summary='Input zero violates the required result.', command_id='replay-1',
                repro_input={'artifact_kind': 'text', 'content': '0'},
                actual={'exit_code': 1}, expected={'criterion_id': 'AC1'},
                replay_evidence_ref=None)


def inspect(raw=None, *, record=None, observed=None, candidate=None, used=None):
    from mission_kernel.fresh_review_output import inspect_output
    record = record or running()
    raw = canonical_bytes(output_document(record)) if raw is None else raw
    used = used or dict(wall_time_sec=1, tool_calls=0, replays=0, output_bytes=len(raw))
    return inspect_output(record, observation(record) if observed is None else observed, raw,
                          candidate_digest=record.request.candidate_digest if candidate is None else candidate,
                          budget_used=used)


def test_valid_bound_output_returns_frozen_hypotheses_without_consuming_request():
    from mission_kernel.fresh_review_output import output_document as wire
    record = running()
    before = copy.deepcopy(record)
    raw = output_document(record)
    result = inspect(canonical_bytes(raw), record=record)
    assert (result.outcome, result.reason) == ('completed', 'none')
    assert wire(result.output) == raw
    assert result.diagnostic_bytes == canonical_bytes(raw)
    assert record == before and record.status == 'running'
    assert result.output.criterion_results[0].findings == ()


@pytest.mark.parametrize('field,value', [('operation_id', 'another'), ('fencing_epoch', 3),
    ('fencing_epoch', True), ('request_id', 'another'), ('nonce', 'another'),
    ('child_identity', 'another')])
def test_sender_mismatch_precedes_even_invalid_output_and_preserves_running(field, value):
    record = running()
    with pytest.raises(FreshReviewError, match='fresh-review-output-sender-mismatch'):
        inspect(b'not json', record=record, observed={**observation(), field: value})
    assert record.status == 'running' and record.result is None


@pytest.mark.parametrize('status', ['pending', 'dispatch-unknown', 'completed', 'failed',
                                    'blocked', 'abandoned-unknown', 'consumed'])
def test_only_running_request_can_accept_a_child_report(status):
    with pytest.raises(FreshReviewError, match='fresh-review-output-request-unavailable'):
        inspect(record=replace(running(), status=status))


def test_takeover_uses_dispatch_epoch_instead_of_commit_epoch():
    from mission_kernel.fresh_review_output import validate_output_sender
    record = running()
    # No current commit epoch is supplied to this child-facing check. The later
    # writer must independently fence its commit against the live lease.
    validate_output_sender(record, observation())
    with pytest.raises(FreshReviewError, match='fresh-review-output-sender-mismatch'):
        validate_output_sender(record, {**observation(), 'fencing_epoch': 3})


@pytest.mark.parametrize('raw', [b'garbage', b'{}', b'null', b'[]', b'\xff',
    b'{"schema": "x", "schema": "y"}', b'{"value": NaN}'])
def test_bound_child_invalid_json_is_failed_instead_of_command_rejection(raw):
    result = inspect(raw)
    assert (result.outcome, result.reason, result.output) == ('failed', 'output-invalid', None)
    assert result.diagnostic_bytes == raw


@pytest.mark.parametrize('field', ['request_digest', 'candidate_digest', 'contract_digest',
    'input_digest', 'adapter_registration_digest', 'requirement_digest', 'verifier_policy_digest',
    'request_id', 'nonce', 'mission_id', 'session_id', 'iteration'])
def test_child_authored_binding_mismatch_is_second_stage_failure(field):
    raw = output_document()
    raw[field] = (2 if field == 'iteration' else 'different' if field.endswith('_id')
                  or field == 'nonce' else 'sha256:' + 'b' * 64)
    result = inspect(canonical_bytes(raw))
    assert (result.outcome, result.reason) == ('failed', 'binding-mismatch')


def test_candidate_recapture_change_is_failed_even_if_child_echoes_original_bindings():
    result = inspect(candidate='sha256:' + 'b' * 64)
    assert result.reason == 'binding-mismatch' and result.output is None


@pytest.mark.parametrize('results', [[], [dict(criterion_id='AC2', status='searched', reason_code='none', findings=[])],
                                      output_document()['criterion_results'] * 2])
def test_missing_unknown_or_duplicate_criterion_never_completes(results):
    raw = output_document()
    raw['criterion_results'] = results
    assert inspect(canonical_bytes(raw)).reason == 'output-invalid'


@pytest.mark.parametrize('field,maximum', [('wall_time_sec', 300), ('tool_calls', 64),
                                         ('replays', 16), ('output_bytes', 262144)])
def test_host_measured_budget_overrun_is_failed(field, maximum):
    raw = canonical_bytes(output_document())
    used = dict(wall_time_sec=1, tool_calls=0, replays=0, output_bytes=len(raw))
    used[field] = maximum + 1
    assert inspect(raw, used=used).reason == 'budget-exceeded'


def test_launch_enforced_budget_is_stricter_than_prepared_budget():
    record = running()
    launch = record.launch.thaw()
    launch['enforced_budget']['max_tool_calls'] = 1
    record = replace(record, launch=freeze_json_value(launch))
    raw = canonical_bytes(output_document(record))
    assert inspect(raw, record=record, used=dict(wall_time_sec=1, tool_calls=2, replays=0,
                                               output_bytes=len(raw))).reason == 'budget-exceeded'


@pytest.mark.parametrize('changes,reason', [({'process_exited': False}, None),
    ({'exit_code': 1}, 'child-failed'), ({'exit_code': -9}, 'child-failed'),
    ({'process_exited': 1}, None), ({'exit_code': False}, None)])
def test_unfinished_or_unobserved_child_never_completes(changes, reason):
    observed = {**observation(), **changes}
    if reason:
        assert inspect(observed=observed).reason == reason
    else:
        with pytest.raises(FreshReviewError, match='fresh-review-output-observation-invalid'):
            inspect(observed=observed)


@pytest.mark.parametrize('context,child', [('inline', 'parent'), ('shared', 'child')])
def test_output_cannot_turn_nonindependent_observations_into_independent_evidence(context, child):
    record = running()
    launch = record.launch.thaw()
    launch.update(context_mode=context, child_identity=child)
    record = replace(record, launch=freeze_json_value(launch), independent=False)
    result = inspect(record=record, observed={**observation(), 'child_identity': child})
    assert result.outcome == 'completed' and result.independent is False


def test_raw_budget_overrun_never_claims_a_truncated_import_diagnostic():
    from mission_kernel.fresh_review import FRESH_REVIEW_EVIDENCE_MAX_BYTES as limit
    result = inspect(b'x' * (limit + 1))
    assert (result.outcome, result.reason, result.output) == ('failed', 'budget-exceeded', None)
    assert result.diagnostic_bytes is None and result.diagnostic_digest is None


def test_finding_limit_is_global_across_all_criteria():
    record = running()
    request = record.request
    request = replace(request, criterion_ids=('AC1', 'AC2'), candidate_bindings=(
        *request.candidate_bindings, replace(request.candidate_bindings[0], criterion_id='AC2')))
    from mission_kernel.fresh_review import request_document
    launch = record.launch.thaw()
    launch['request_digest'] = canonical_digest(request_document(request))
    record = replace(record, request=request, launch=freeze_json_value(launch))
    raw = output_document(record)
    raw['criterion_results'][0]['findings'] = [finding(i) for i in range(31)]
    raw['criterion_results'].append(dict(criterion_id='AC2', status='searched', reason_code='none',
        findings=[{**finding(i), 'criterion_id': 'AC2'} for i in range(31, 62)]))
    assert inspect(canonical_bytes(raw), record=record).reason == 'output-over-import-limit'
    raw['criterion_results'][1]['findings'].pop()
    assert inspect(canonical_bytes(raw), record=record).outcome == 'completed'


@pytest.mark.parametrize('change', [dict(severity='Critical'), dict(finding_id='../escape'),
    dict(criterion_id='AC2'), dict(requirement_ids=['R1', 'R1']), dict(repro_input={'content': 'x'}),
    dict(summary=''), dict(actual=None), dict(expected=None), dict(extra=True)])
def test_malformed_finding_never_becomes_a_completed_hypothesis(change):
    raw = output_document()
    raw['criterion_results'][0]['findings'] = [{**finding(), **change}]
    assert inspect(canonical_bytes(raw)).reason == 'output-invalid'


def test_hypothesis_actual_and_replay_reference_do_not_authorize_verified_findings():
    raw = output_document()
    raw['criterion_results'][0]['findings'] = [finding()]
    result = inspect(canonical_bytes(raw))
    hypothesis = result.output.criterion_results[0].findings[0]
    assert hypothesis.actual.thaw() == {'exit_code': 1}
    assert hypothesis.replay_evidence_ref is None
    assert not hasattr(hypothesis, 'verified')
    # Child-authored replay evidence cannot masquerade as application observation.
    raw['criterion_results'][0]['findings'][0]['replay_evidence_ref'] = {'digest': ADAPTER}
    assert inspect(canonical_bytes(raw)).reason == 'output-invalid'


def test_typed_output_is_immutable_and_roundtrip_does_not_alias_authored_json():
    from mission_kernel.fresh_review_output import decode_output, output_document as wire
    raw = output_document()
    raw['criterion_results'][0]['findings'] = [finding()]
    typed = decode_output(raw)
    raw['criterion_results'][0]['findings'][0]['actual']['exit_code'] = 0
    assert typed.criterion_results[0].findings[0].actual.thaw()['exit_code'] == 1
    changed = wire(typed)
    changed['coverage'][0]['criterion_ids'].clear()
    assert wire(typed)['coverage'][0]['criterion_ids'] == ['AC1']


@pytest.mark.parametrize('path,value', [('classification_confirmed', 1), ('status', 'pending'),
    ('reason', ''), ('criterion_ids', ['AC1', 'AC1']), ('requirement_id', '../escape')])
def test_coverage_shape_cannot_hide_invalid_classification_or_reference(path, value):
    raw = output_document()
    raw['coverage'][0][path] = value
    assert inspect(canonical_bytes(raw)).reason == 'output-invalid'


def replay_fixture(path='repro.txt'):
    from mission_kernel.fresh_review import candidate_identity
    from mission_kernel.fresh_review_output import decode_output
    command = dict(id='command-1', argv=['/usr/bin/fixture'], relative_cwd='.',
        timeout_sec=1, output_limit=1024, kind='command', env={}, declared_untracked=[],
        toolchain={'path': '/usr/bin/fixture', 'digest': ADAPTER}, external_inputs=[],
        replay=dict(command_id='replay-1', allowed_artifact_kinds=['text'],
                    max_bytes=1, relative_path=path))
    target = {key: copy.deepcopy(value) for key, value in command.items() if key != 'replay'}
    target['id'] = 'replay-1'
    policy = dict(digest=ADAPTER, commands={'command-1': command, 'replay-1': target})
    record = running()
    binding = record.request.candidate_bindings[0]
    request = replace(record.request, candidate_bindings=(
        replace(binding, definition_digest=canonical_digest(command)),
        replace(binding, role='replay', command_id='replay-1', definition_digest=canonical_digest(target))),
        candidate_digest=candidate_identity({'command-1': ADAPTER, 'replay-1': ADAPTER}))
    raw = output_document()
    raw['criterion_results'][0]['findings'] = [finding()]
    return request, decode_output(raw).criterion_results[0].findings[0], policy


def test_replay_eligibility_is_bound_to_frozen_request_not_an_arbitrary_command():
    from mission_kernel.fresh_review_output import replay_eligibility
    request, hypothesis, policy = replay_fixture()
    assert replay_eligibility(request, hypothesis, policy) is None
    assert replay_eligibility(request, replace(hypothesis, repro_input=freeze_json_value(
        {'artifact_kind': 'text', 'content': '00'})), policy) == 'replay-input-invalid'
    assert replay_eligibility(running().request, hypothesis, policy) == 'replay-unsupported'


@pytest.mark.parametrize('field,value', [('max_bytes', 2), ('relative_path', 'other.txt'),
    ('allowed_artifact_kinds', ['text', 'json']), ('command_id', 'command-1'),
    ('policy-digest', 'sha256:' + 'b' * 64), ('target-definition', 'changed')])
def test_same_command_id_cannot_replace_frozen_replay_policy(field, value):
    from mission_kernel.fresh_review_output import replay_eligibility
    request, hypothesis, policy = replay_fixture()
    if field == 'policy-digest':
        policy['digest'] = value
    elif field == 'target-definition':
        policy['commands']['replay-1']['argv'].append(value)
    else:
        policy['commands']['command-1']['replay'][field] = value
    assert replay_eligibility(request, hypothesis, policy) == 'replay-unsupported'


def test_hypothesis_cannot_select_another_command_with_an_unchanged_frozen_policy():
    from mission_kernel.fresh_review_output import replay_eligibility
    request, hypothesis, policy = replay_fixture()
    assert replay_eligibility(request, replace(hypothesis, command_id='command-1'), policy) == 'replay-unsupported'


def test_new_kernel_has_no_cli_activation_or_outer_layer_import():
    import ast
    from pathlib import Path
    source = Path(__file__).parents[1] / 'lib/mission_kernel/fresh_review_output.py'
    tree = ast.parse(source.read_text())
    imports = [node.module or '' for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports.extend(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
    assert not any(name.startswith(('mission_application', 'mission_persistence', 'fresh_review_host'))
                   for name in imports)
    assert 'fresh_review_output' not in (source.parents[2] / 'bin/mission-state.py').read_text()


def coverage_fixture():
    import hashlib
    from acceptance_contract import canonical_contract_digest
    contract = dict(schema='mission-acceptance-contract/1', mission_id='mission-1',
        requirement_text='Required.Context.', requirement_digest='sha256:' + hashlib.sha256(
            b'Required.Context.').hexdigest(), revision=1, review_policy='fresh-required',
        requirements=[dict(id='R1', start=0, end=9, text='Required.', classification='obligation'),
                      dict(id='R2', start=9, end=17, text='Context.', classification='context')],
        criteria=[dict(id='AC1', requirement_ids=['R1'], expected='Result is positive.', required=True,
                       prohibited_side_effects=['Do not delete data.'], verification_kind='command',
                       target_path='app.txt', command_id='command-1')], coverage={'status': 'pending'})
    record = running()
    request = replace(record.request, contract_digest=canonical_contract_digest(contract),
                      requirement_digest=contract['requirement_digest'])
    raw = output_document(replace(record, request=request))
    raw['coverage'].append(dict(requirement_id='R2', classification_confirmed=True,
        criterion_ids=[], status='valid', reason_code='none', reason='Background context.'))
    return request, raw, contract


def coverage(raw, request, contract):
    from mission_kernel.fresh_review_output import decode_output, derive_output_coverage
    return derive_output_coverage(decode_output(raw), request, contract)


def test_coverage_checks_full_ledger_and_keeps_imported_contract_pending():
    request, raw, contract = coverage_fixture()
    before = copy.deepcopy(contract)
    decision = coverage(raw, request, contract)
    assert decision.status == 'valid' and decision.open_requirement_ids == ()
    assert contract == before and contract['coverage'] == {'status': 'pending'}
    for mutation in ('omit-context', 'unknown-requirement', 'unknown-criterion', 'unrelated-criterion'):
        changed = copy.deepcopy(raw)
        if mutation == 'omit-context':
            changed['coverage'].pop()
        elif mutation == 'unknown-requirement':
            changed['coverage'][1]['requirement_id'] = 'R3'
        elif mutation == 'unknown-criterion':
            changed['coverage'][0]['criterion_ids'] = ['AC2']
        else:
            changed['coverage'][1]['criterion_ids'] = ['AC1']
        with pytest.raises(FreshReviewError, match='fresh-review-coverage-invalid'):
            coverage(changed, request, contract)


@pytest.mark.parametrize('case', ['unconfirmed-context', 'no-criterion', 'optional-only',
                                'blocked-search', 'reported-open'])
def test_coverage_open_obligations_cannot_be_self_declared_valid(case):
    from acceptance_contract import canonical_contract_digest
    request, raw, contract = coverage_fixture()
    if case == 'unconfirmed-context':
        raw['coverage'][1]['classification_confirmed'] = False
    elif case == 'no-criterion':
        raw['coverage'][0]['criterion_ids'] = []
    elif case == 'optional-only':
        contract['criteria'][0]['required'] = False
        request = replace(request, contract_digest=canonical_contract_digest(contract))
        raw['contract_digest'] = request.contract_digest
        from mission_kernel.fresh_review import request_document
        raw['request_digest'] = canonical_digest(request_document(request))
    elif case == 'blocked-search':
        raw['criterion_results'][0].update(status='blocked', reason_code='budget-exhausted')
    else:
        raw['coverage'][0].update(status='open', reason_code='missing-proof')
    result = coverage(raw, request, contract)
    assert result.status == 'open'
    assert result.open_requirement_ids == (('R2',) if case == 'unconfirmed-context' else ('R1',))


@pytest.mark.parametrize('field,value', [('requirement_ids', ['R2']), ('prohibited_side_effect_ids', ['AC1:1'])])
def test_finding_cannot_claim_an_unrelated_ledger_span_or_nonexistent_prohibited_effect(field, value):
    request, raw, contract = coverage_fixture()
    raw['criterion_results'][0]['findings'] = [{**finding(), field: value}]
    with pytest.raises(FreshReviewError, match='fresh-review-finding-binding-invalid'):
        coverage(raw, request, contract)


def test_required_finding_remains_open_even_for_low_severity():
    request, raw, contract = coverage_fixture()
    raw['criterion_results'][0]['findings'] = [finding()]
    result = coverage(raw, request, contract)
    assert result.open_finding_ids == ('finding-1',)
    assert result.status == 'valid'  # Valid mapping does not resolve the finding.


def test_host_usage_cannot_underreport_collected_output_bytes():
    raw = canonical_bytes(output_document())
    with pytest.raises(FreshReviewError, match='fresh-review-output-observation-invalid'):
        inspect(raw, used=dict(wall_time_sec=1, tool_calls=0, replays=0, output_bytes=len(raw)-1))


@pytest.mark.parametrize('field', ['input_digest', 'candidate_digest', 'request_digest', 'nonce',
                                 'requirement_digest'])
def test_standalone_coverage_judgement_rechecks_every_output_binding(field):
    request, raw, contract = coverage_fixture()
    raw[field] = 'other' if field == 'nonce' else 'sha256:' + 'b' * 64
    with pytest.raises(FreshReviewError, match='fresh-review-coverage-invalid'):
        coverage(raw, request, contract)


@pytest.mark.parametrize('path', ['../escape', '/absolute', 'a/../escape', 'a//b', 'a\\b', 'C:/file', ''])
def test_replay_policy_cannot_materialize_outside_a_portable_relative_file(path):
    from mission_kernel.fresh_review_output import decode_output, replay_eligibility
    request, hypothesis, policy = replay_fixture(path)
    assert replay_eligibility(request, hypothesis, policy) == 'replay-unsupported'


@pytest.mark.parametrize('repro', [{}, {'artifact_kind': 'text'}, {'artifact_kind': 'text', 'content': 1},
                                  {'artifact_kind': 'text', 'content': '\ud800'}])
def test_typed_hypothesis_cannot_bypass_repro_validation(repro):
    from mission_kernel.fresh_review_output import decode_output, replay_eligibility
    request, hypothesis, policy = replay_fixture()
    h = replace(hypothesis, repro_input=freeze_json_value(repro))
    with pytest.raises(FreshReviewError):
        replay_eligibility(request, h, policy)


@pytest.mark.parametrize('kind', ['text', 'application/json', 'évidence'])
@pytest.mark.parametrize('content', ['0', '\x00'])
def test_repro_preserves_utf8_bytes_and_registered_kind_names(kind, content):
    raw = output_document()
    raw['criterion_results'][0]['findings'] = [{**finding(), 'repro_input':
        {'artifact_kind': kind, 'content': content}}]
    result = inspect(canonical_bytes(raw))
    assert result.outcome == 'completed'
    assert result.output.criterion_results[0].findings[0].repro_input.thaw() == {
        'artifact_kind': kind, 'content': content}


@pytest.mark.parametrize('case,reason', [('schema', 'output-invalid'),
    ('finding', 'output-invalid'), ('coverage', 'output-invalid'),
    ('binding', 'binding-mismatch'), ('budget', 'budget-exceeded'),
    ('child', 'child-failed'), ('limit', 'output-over-import-limit')])
def test_import_limit_follows_schema_binding_and_budget_and_retains_complete_output(case, reason):
    import hashlib
    raw = output_document()
    raw['criterion_results'][0]['findings'] = [finding(i) for i in range(62)]
    used = dict(wall_time_sec=1, tool_calls=0, replays=0, output_bytes=0)
    observed = observation()
    if case == 'schema':
        raw['schema'] = 'unknown'
    elif case == 'finding':
        raw['criterion_results'][0]['findings'][-1]['severity'] = 'unknown'
    elif case == 'coverage':
        raw['coverage'][0]['classification_confirmed'] = 1
    elif case == 'binding':
        raw['candidate_digest'] = 'sha256:' + 'b' * 64
    elif case == 'budget':
        used['tool_calls'] = 65
    elif case == 'child':
        observed['exit_code'] = 1
    body = canonical_bytes(raw)
    used['output_bytes'] = len(body)
    result = inspect(body, used=used, observed=observed)
    assert (result.outcome, result.reason, result.output) == ('failed', reason, None)
    assert result.diagnostic_bytes == body
    assert result.diagnostic_digest == 'sha256:' + hashlib.sha256(body).hexdigest()


@pytest.mark.parametrize('selected', [True, False])
def test_child_cannot_omit_a_contract_required_criterion_to_hide_an_open_obligation(selected):
    from acceptance_contract import canonical_contract_digest
    from mission_kernel.fresh_review import request_document
    request, raw, contract = coverage_fixture()
    contract['criteria'].append({**contract['criteria'][0], 'id': 'AC3'})
    request = replace(request, contract_digest=canonical_contract_digest(contract))
    if selected:
        request = replace(request, criterion_ids=('AC1', 'AC3'), candidate_bindings=(
            *request.candidate_bindings, replace(request.candidate_bindings[0], criterion_id='AC3')))
        raw['criterion_results'].append(dict(criterion_id='AC3', status='blocked',
                                            reason_code='budget-exhausted', findings=[]))
    raw.update(contract_digest=request.contract_digest, request_digest=canonical_digest(request_document(request)))
    # Child lists only AC1 even though contract also requires AC3 for R1.
    result = coverage(raw, request, contract)
    assert result.status == 'open' and result.open_requirement_ids == ('R1',)


@pytest.mark.parametrize('severity', ['High', 'Medium', 'Low'])
def test_required_criterion_finding_is_open_even_when_child_omits_its_requirement_ids(severity):
    request, raw, contract = coverage_fixture()
    raw['criterion_results'][0]['findings'] = [{**finding(), 'severity': severity,
        'requirement_ids': [], 'prohibited_side_effect_ids': []}]
    assert coverage(raw, request, contract).open_finding_ids == ('finding-1',)


@pytest.mark.parametrize('statement', ['import mission_persistence',
    'import mission_application as application', 'import fresh_review_host',
    'from mission_persistence import repository'])
def test_kernel_layer_guard_detects_both_import_forms(monkeypatch, statement):
    from pathlib import Path
    read = Path.read_text
    def injected(path, *args, **kwargs):
        source = read(path, *args, **kwargs)
        return source + '\n' + statement if path.name == 'fresh_review_output.py' else source
    monkeypatch.setattr(Path, 'read_text', injected)
    with pytest.raises(AssertionError):
        test_new_kernel_has_no_cli_activation_or_outer_layer_import()


@pytest.mark.parametrize('case,reason', [('schema', 'output-invalid'), ('binding', 'binding-mismatch')])
def test_schema_and_binding_failure_precede_measured_budget_failure(case, reason):
    raw = output_document()
    raw['schema' if case == 'schema' else 'candidate_digest'] = (
        'unknown' if case == 'schema' else 'sha256:' + 'b' * 64)
    body = canonical_bytes(raw)
    assert inspect(body, used=dict(wall_time_sec=301, tool_calls=0, replays=0,
                                   output_bytes=len(body))).reason == reason


def test_replay_eligibility_validates_links_against_the_whole_frozen_policy():
    # command-1 -> replay-1 -> replay-2 is a valid registered chain; the first replay stays eligible.
    from mission_kernel.fresh_review_output import replay_eligibility
    request, hypothesis, policy = replay_fixture()
    target = policy['commands']['replay-1']
    second = {key: copy.deepcopy(value) for key, value in target.items()}
    second['id'] = 'replay-2'
    target['replay'] = dict(command_id='replay-2', allowed_artifact_kinds=['text'], max_bytes=1,
                            relative_path='repro.txt')
    policy['commands']['replay-2'] = second
    source, replay = request.candidate_bindings
    request = replace(request, candidate_bindings=(
        source, replace(replay, definition_digest=canonical_digest(target))))
    assert replay_eligibility(request, hypothesis, policy) is None
    # A dangling link anywhere in the frozen policy still makes it unsupported.
    target['replay']['command_id'] = 'missing'
    request = replace(request, candidate_bindings=(
        source, replace(replay, definition_digest=canonical_digest(target))))
    assert replay_eligibility(request, hypothesis, policy) == 'replay-unsupported'


@pytest.mark.parametrize('broken', [None, [], True, 7, 'command', {}])
def test_replay_eligibility_rejects_malformed_unrelated_frozen_command(broken):
    from mission_kernel.fresh_review_output import replay_eligibility
    request, hypothesis, policy = replay_fixture()
    policy['commands']['unrelated'] = broken
    assert replay_eligibility(request, hypothesis, policy) == 'replay-unsupported'
