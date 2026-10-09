"""Public D2 import uses host observations and the repository evidence commit."""
import json
import hashlib

import pytest

from .completion_cli_fixtures import completion_cli_code, completion_template, run_cli

from .test_issue912_fresh_review_dispatch import completion_session, reviewer, invoke
from .test_issue879_completion_cli import _reject_unchanged


def import_output(run_cli, reviewer, **env):
    root, request, environment, _ = reviewer
    return run_cli('fresh-review', 'import', '--request', request['request_id'],
                   '--adapter', 'neutral', cwd=root,
                   env_extra={**environment, 'MISSION_OPERATION_ID': 'import-one', **env})


def test_bound_invalid_output_publishes_diagnostic_and_terminal_together(reviewer, run_cli):
    root, _, _, journal = reviewer
    running = invoke(run_cli, reviewer)
    observed = json.loads(journal.read_text())
    observed.update(process_exited=True, exit_code=0, budget_used=dict(wall_time_sec=1, tool_calls=0, replays=0, output_bytes=len(observed['output'].encode())))
    journal.write_text(json.dumps(observed))
    result = import_output(run_cli, reviewer)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads(result.stdout)['record']
    assert record['status'] == 'failed'
    receipt = record['result']
    assert receipt['reason'] == 'output-invalid'
    assert receipt['dispatch_operation_id'] == running['dispatch']['operation_id']
    assert receipt['commit_operation_id'] == 'import-one'
    content = json.loads(journal.read_text())['output'].encode()
    assert receipt['output_digest'] == 'sha256:' + hashlib.sha256(content).hexdigest()
    assert (root / receipt['output_ref']['relative_path']).read_bytes() == content
    state_root = root / '.mission-state'
    head = json.loads((state_root / 'sessions/test.json').read_text())
    manifest = json.loads((state_root / head['state_generation']['path']).read_text())
    terminal_state = json.loads((state_root / manifest['state']['object']).read_text())
    assert terminal_state['fresh_review']['requests'][0]['result'] == receipt
    assert len(manifest['blobs']) == 1
    assert manifest['blobs'][0]['digest'] == receipt['output_digest']
    assert (state_root / manifest['blobs'][0]['object']).read_bytes() == content
    assert json.loads(import_output(run_cli, reviewer).stdout)['record'] == record
    _reject_unchanged(run_cli, root, ['mark-passes'], 'acceptance-coverage-pending')


def exited(journal, *, output=None, exit_code=0):
    stored = json.loads(journal.read_text())
    if output is not None:
        stored['output'] = output
    stored.update(process_exited=True, exit_code=exit_code, budget_used=dict(
        wall_time_sec=1, tool_calls=0, replays=0, output_bytes=len(stored['output'].encode())))
    journal.write_text(json.dumps(stored))


@pytest.mark.parametrize('outcome', ['failed', 'completed'])
def test_reconcile_imports_bound_failure_after_takeover_without_another_launch(reviewer, run_cli, outcome):
    from .test_issue879_completion_cli import _rewrite_fixture_document
    root, request, env, journal = reviewer
    unknown = invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE='crash')
    exited(journal, output=json.dumps(bound_output(request)) if outcome == 'completed' else 'invalid')
    _rewrite_fixture_document(root, lambda state: state.update(lease_expires_at='2000-01-01T00:00:00Z'))
    record = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one',
                    MISSION_LEASE_ID='takeover-lease')
    assert record['status'] == outcome
    receipt = record['result']
    assert receipt['dispatch_fencing_epoch'] == unknown['dispatch']['fencing_epoch']
    assert receipt['commit_fencing_epoch'] > receipt['dispatch_fencing_epoch']
    assert receipt['commit_operation_id'] == 'reconcile-one'
    assert json.loads(journal.read_text())['count'] == 1
    assert invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one',
                  MISSION_LEASE_ID='takeover-lease') == record


def bound_output(request):
    from dataclasses import replace
    from mission_kernel.fresh_review import decode_request
    from .test_issue896_output_kernel import output_document, running
    return output_document(replace(running(), request=decode_request(request)))


@pytest.mark.parametrize('case,reason', [('binding', 'binding-mismatch'),
    ('stale', 'binding-mismatch'), ('child', 'child-failed'), ('overflow', 'output-over-import-limit'),
    ('budget', 'budget-exceeded'), ('inline', 'output-invalid')])
def test_bound_failures_consume_closed_variant_without_success(reviewer, run_cli, case, reason):
    from mission_kernel.fresh_review import canonical_bytes
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt
    from .test_issue896_output_kernel import finding
    root, request, env, journal = reviewer
    invoke(run_cli, reviewer, **({'FIXTURE_REVIEW_MODE': 'inline'} if case == 'inline' else {}))
    raw = bound_output(request)
    if case == 'binding':
        raw['nonce'] = 'foreign'
    elif case == 'overflow':
        raw['criterion_results'][0]['findings'] = [finding(index) for index in range(62)]
    elif case == 'stale':
        (root / 'app.txt').write_text('changed candidate')
    exited(journal, output=canonical_bytes(raw).decode() if case != 'inline' else None,
           exit_code=7 if case == 'child' else 0)
    if case == 'budget':
        stored = json.loads(journal.read_text())
        stored['budget_used']['wall_time_sec'] = 301
        journal.write_text(json.dumps(stored))
    result = import_output(run_cli, reviewer)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)['record']
    assert record['status'] == 'failed' and record['result']['reason'] == reason
    assert not hasattr(decode_terminal_receipt(record['result']), 'coverage_receipt')
    assert record['independent'] is (case != 'inline')
    reference = record['result']['output_ref']
    assert (root / reference['relative_path']).read_bytes() == json.loads(journal.read_text())['output'].encode()
    _reject_unchanged(run_cli, root, ['fresh-review', 'import', '--request', request['request_id'],
        '--adapter', 'neutral'], 'fresh-review-consumed', env={**env, 'MISSION_OPERATION_ID': 'import-two'})
    if case == 'overflow':
        next_attempt = run_cli('fresh-review', 'prepare', '--perspective', 'counterexamples',
            '--adapter-registration-digest', request['adapter_registration_digest'], cwd=root,
            env_extra={**env, 'MISSION_OPERATION_ID': 'prepare-two'})
        assert next_attempt.returncode == 0, next_attempt.stderr
        requests = json.loads(run_cli('fresh-review', 'status', cwd=root).stdout)['requests']
        assert requests[0]['result'] == record['result']
        assert requests[1]['request']['nonce'] != request['nonce']
        _reject_unchanged(run_cli, root, ['mark-passes'], 'acceptance-coverage-pending')


@pytest.mark.parametrize('case', ['sender', 'unfinished'])
def test_rejected_report_leaves_running_unchanged(reviewer, run_cli, case):
    from mission_kernel.fresh_review import canonical_bytes
    root, request, env, journal = reviewer
    invoke(run_cli, reviewer)
    exited(journal)
    stored = json.loads(journal.read_text())
    if case == 'sender':
        stored['launch']['child_identity'] = 'foreign-child'
    elif case == 'unfinished':
        stored['process_exited'] = False
    journal.write_text(json.dumps(stored))
    code = {'sender': 'fresh-review-output-sender-mismatch', 'unfinished': 'fresh-review-output-observation-invalid'}[case]
    _reject_unchanged(run_cli, root, ['fresh-review', 'import', '--request', request['request_id'],
        '--adapter', 'neutral'], code, env={**env, 'MISSION_OPERATION_ID': 'import-one'})


def test_import_parser_is_owned_and_caller_cannot_supply_outcome_or_identity():
    from .test_command_inventory import _load_mission_state_module
    from mission_application.command_owners import COMMAND_OWNER_REGISTRY
    module = _load_mission_state_module()
    parser = module._build_parser()
    args = parser.parse_args(['fresh-review', 'import', '--request', 'request', '--adapter', 'neutral'])
    assert args.func == module.cmd_fresh_review_import
    assert COMMAND_OWNER_REGISTRY['fresh-review import'] == 'A2.review'
    for flag in ('--completed', '--independent', '--output', '--fencing-epoch'):
        with pytest.raises(SystemExit):
            parser.parse_args(['fresh-review', 'import', '--request', 'request', '--adapter', 'neutral', flag])


@pytest.fixture
def prepared_failure(monkeypatch):
    from types import SimpleNamespace
    from mission_application import fresh_review_publish as publisher
    from .test_issue896_output_kernel import running, observation
    record = running()
    monkeypatch.setattr(publisher, '_record', lambda *_: record)
    monkeypatch.setattr(publisher, '_candidate', lambda *_args, **_kwargs: record.request.candidate_digest)
    services = SimpleNamespace(now=lambda: '2026-01-01T00:00:01.000000Z')
    def prepare(raw=b'bad json', *, observed=None):
        observed = observed or {**observation(record), 'budget_used': dict(
            wall_time_sec=1, tool_calls=0, replays=0, output_bytes=len(raw) if type(raw) is bytes else 0)}
        return publisher.prepare_failed_output({}, request_id=record.request.request_id,
            operation='import', epoch=3, observation=observed, raw=raw, root=None, services=services)
    return record, prepare


@pytest.mark.parametrize('field', ['effect', 'receipt', 'output_base64', 'observation', 'budget_used'])
def test_kernel_rechecks_import_evidence_instead_of_trusting_writer_carrier(prepared_failure, field):
    from dataclasses import replace
    from mission_kernel.fresh_review import FreshReviewError
    from mission_kernel.fresh_review_publish import validate_failed_import
    from mission_kernel.json_codec import freeze_json_value
    record, prepare = prepared_failure
    command = prepare().command
    validate_failed_import(record, command)
    if field == 'effect':
        value = replace(command.effect, digest='sha256:' + 'a'*64)
    elif field == 'receipt':
        raw = command.receipt.thaw()
        raw['reason'] = 'child-failed'
        value = freeze_json_value(raw)
    elif field == 'output_base64':
        value = 'bm90IHRoZSBzYW1l'
    else:
        raw = getattr(command, field).thaw()
        raw['child_identity' if field == 'observation' else 'wall_time_sec'] = 'foreign' if field == 'observation' else 2
        value = freeze_json_value(raw)
    with pytest.raises(FreshReviewError):
        validate_failed_import(record, replace(command, **{field: value}))


def test_writer_rejects_typed_output_carrier_and_does_not_bypass_import_limit(prepared_failure):
    from mission_kernel.fresh_review_output import decode_output
    from mission_kernel.fresh_review import FreshReviewError, canonical_bytes
    from .test_issue896_output_kernel import output_document, finding
    record, prepare = prepared_failure
    raw = output_document(record)
    raw['criterion_results'][0]['findings'] = [finding(index) for index in range(62)]
    with pytest.raises(FreshReviewError, match='output-observation-invalid'):
        prepare(decode_output(raw))
    prepared = prepare(canonical_bytes(raw))
    assert prepared.command.receipt.thaw()['reason'] == 'output-over-import-limit'
    assert len(prepared.effects) == 1 <= 64
    assert prepared.effects[0].content == canonical_bytes(raw)


def test_failed_writer_has_no_effect_without_diagnostic_bytes(prepared_failure):
    from mission_kernel.fresh_review_publish import validate_failed_import
    record, prepare = prepared_failure
    prepared = prepare(None)
    assert prepared.effects == () and prepared.command.effect is None
    assert 'output_ref' not in prepared.command.receipt.thaw()
    validate_failed_import(record, prepared.command)


def test_failed_writer_shape_is_below_e0_constant_at_maximum_fields(monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from mission_application import fresh_review_publish as publisher
    from mission_kernel.fresh_review import canonical_digest, request_document, ToolCapability
    from mission_kernel.fresh_review_receipts import FRESH_REVIEW_MAX_ENCODED_BYTES
    from mission_kernel.json_codec import freeze_json_value, encode_json_value
    from .test_issue896_output_kernel import running, observation
    from .test_issue917_fresh_review_bounds import maximum_launch
    record = running()
    request = replace(record.request, request_id='r'*128, nonce='n'*128,
                      allowed_tools=(ToolCapability.READ_CANDIDATE, ToolCapability.REPLAY_VERIFIER))
    launch = maximum_launch()
    launch.update(request_id=request.request_id, nonce=request.nonce,
                  request_digest=canonical_digest(request_document(request)))
    record = replace(record, request=request, launch=freeze_json_value(launch), independent=False,
        dispatch=freeze_json_value(dict(operation_id=launch['operation_id'], fencing_epoch=launch['fencing_epoch'],
                                       parent_identity=launch['parent_identity'])))
    monkeypatch.setattr(publisher, '_record', lambda *_: record)
    monkeypatch.setattr(publisher, '_candidate', lambda *_args, **_kwargs: request.candidate_digest)
    observed = {**observation(record), **{key: launch[key] for key in
        ('operation_id', 'fencing_epoch', 'request_id', 'nonce', 'child_identity')}, 'exit_code': 1,
        'budget_used': dict.fromkeys(('wall_time_sec', 'tool_calls', 'replays', 'output_bytes'), 2**63-1)}
    prepared = publisher.prepare_failed_output({}, request_id=request.request_id, operation='c'*128,
        epoch=2**63-1, observation=observed, raw=b'x'*262144, root=None,
        services=SimpleNamespace(now=lambda: '9999-12-31T23:59:59.999999Z'))
    assert len(encode_json_value(prepared.command.receipt)) <= FRESH_REVIEW_MAX_ENCODED_BYTES['failed']
    assert len(prepared.effects) == 1 <= 64
    assert prepared.effects[0].size == 262144


@pytest.mark.parametrize('outcome', ['failed', 'completed'])
@pytest.mark.parametrize('point,committed', [('after-generation-publish', False), ('after-head-replace', True)])
def test_interrupted_publication_never_exposes_terminal_without_output(reviewer, run_cli, monkeypatch, point, committed, outcome):
    from .test_command_inventory import _load_mission_state_module
    from .test_issue879_completion_cli import _persisted_fixture_document
    from mission_application.fresh_review_publish import prepare_failed_output
    from mission_application.evidence import execute_evidence_operation
    root, request, env, journal = reviewer
    invoke(run_cli, reviewer)
    exited(journal, output=json.dumps(bound_output(request)) if outcome == 'completed' else 'invalid')
    monkeypatch.chdir(root)
    monkeypatch.setenv('MISSION_SESSION_ID', 'test')
    monkeypatch.setenv('MISSION_LEASE_ID', 'test-lease')
    module = _load_mission_state_module()
    services = module._ACCEPTANCE_CONTRACT_CLI_SERVICES
    sf = services.resolve_state_file(root)
    state = _persisted_fixture_document(root)
    saved = json.loads(journal.read_text())
    launch = saved['launch']
    observed = {key: launch[key] for key in ('operation_id', 'fencing_epoch', 'request_id', 'nonce', 'child_identity')}
    observed.update(process_exited=True, exit_code=0, budget_used=saved['budget_used'])
    prepared = prepare_failed_output(state, request_id=request['request_id'], operation='fault-import',
        epoch=state['fencing_epoch'], observation=observed, raw=saved['output'].encode(), root=root, services=services)
    operation, operation_command = services.canonical_operation('test', 'fresh-review-import',
        {'request_id': request['request_id'], 'adapter': 'neutral'}, caller_operation_id='fault-import')
    repository = services.repository(root, sf, stamp=True, strict_read=True, pre_admit_lease=True,
        session_id='test', operation_id=operation, operation_command=operation_command,
        operation_command_type='fresh-review-import')
    class Interrupted(Exception):
        pass
    def stop(reached):
        if reached == point:
            raise Interrupted(point)
    repository._repository.fault_injector = stop
    with pytest.raises(Interrupted):
        execute_evidence_operation(repository, lambda _: prepared)
    persisted = _persisted_fixture_document(root)
    assert persisted['fresh_review']['requests'][0]['status'] == (outcome if committed else 'running')
    for effect in prepared.effects:
        target = root / effect.target
        if committed:
            assert target.read_bytes() == effect.content
        else:
            assert not target.exists()
    repository._repository.fault_injector = None
    execute_evidence_operation(repository, lambda _: prepared)
    assert _persisted_fixture_document(root)['fresh_review']['requests'][0]['status'] == outcome
    for effect in prepared.effects:
        assert (root / effect.target).read_bytes() == effect.content


@pytest.mark.parametrize('observed', [None, [], True, 7, 'observation'])
def test_malformed_application_observation_is_reason_coded(monkeypatch, observed):
    from mission_application import fresh_review_publish as publisher
    from mission_kernel.fresh_review import FreshReviewError
    from .test_issue896_output_kernel import running
    from types import SimpleNamespace
    record = running()
    monkeypatch.setattr(publisher, '_record', lambda *_: record)
    monkeypatch.setattr(publisher, '_candidate', lambda *_args, **_kwargs: record.request.candidate_digest)
    with pytest.raises(FreshReviewError, match='fresh-review-output-sender-mismatch'):
        publisher.prepare_failed_output({}, request_id=record.request.request_id, operation='import', epoch=3,
            observation=observed, raw=b'bad', root=None, services=SimpleNamespace(now=lambda: 'unused'))


def test_old_writer_fence_and_forged_effect_are_rejected_by_public_transition(reviewer, run_cli, monkeypatch):
    from dataclasses import replace
    from .test_command_inventory import _load_mission_state_module
    from .test_issue879_completion_cli import _persisted_fixture_document
    from mission_application.fresh_review_publish import prepare_failed_output
    from mission_persistence.legacy_v4 import _legacy_command_state
    from mission_kernel.transitions import decide, bind_transition_effects, TransitionTableError
    root, request, _, journal = reviewer
    invoke(run_cli, reviewer)
    exited(journal)
    module = _load_mission_state_module()
    services = module._ACCEPTANCE_CONTRACT_CLI_SERVICES
    state = _persisted_fixture_document(root)
    saved = json.loads(journal.read_text())
    observed = {key: saved['launch'][key] for key in
                ('operation_id', 'fencing_epoch', 'request_id', 'nonce', 'child_identity')}
    observed.update(process_exited=True, exit_code=0, budget_used=saved['budget_used'])
    prepared = prepare_failed_output(state, request_id=request['request_id'], operation='import-one',
        epoch=state['fencing_epoch'], observation=observed, raw=saved['output'].encode(), root=root, services=services)
    typed = _legacy_command_state(state, prepared.command)
    decision = decide(typed, prepared.command)
    assert decision.accepted
    for mutation in ('size', 'digest-and-path'):
        forged = _forge_diagnostic_reference(prepared.command, mutation)
        assert decide(typed, forged).rejection.code == 'fresh-review-output-effect-invalid'
    with pytest.raises(TransitionTableError, match='invalid-transition-effect-binding'):
        bind_transition_effects(decision.transition, ())
    with pytest.raises(TransitionTableError, match='invalid-transition-effect-binding'):
        bind_transition_effects(decision.transition, (replace(prepared.effects[0], digest='sha256:'+'b'*64),))
    newer = replace(typed, lease=replace(typed.lease, fencing_epoch=state['fencing_epoch'] + 1))
    assert decide(newer, prepared.command).rejection.code == 'fresh-review-stale-fence'
    bind_transition_effects(decision.transition, prepared.effects)


def test_import_schema_exposes_only_request_and_adapter():
    from mission_application.contract_schemas import render_contract_schema
    schema = json.loads(render_contract_schema('fresh-review-import'))
    assert set(schema['required']) == {'request', 'adapter'}
    assert schema['closed'] is True
    assert schema['terminal_outcomes'] == ['failed', 'completed']


@pytest.mark.parametrize('command', ['import', 'reconcile'])
@pytest.mark.parametrize('exit_code,reason', [(7, 'child-failed'), (0, 'output-invalid')])
def test_confirmed_exit_without_output_has_same_failed_terminal(reviewer, run_cli, command, exit_code, reason):
    root, request, env, journal = reviewer
    invoke(run_cli, reviewer)
    stored = json.loads(journal.read_text())
    stored.update(output=None, process_exited=True, exit_code=exit_code,
                  budget_used=dict(wall_time_sec=1, tool_calls=0, replays=0, output_bytes=0))
    journal.write_text(json.dumps(stored))
    result = run_cli('fresh-review', command, '--request', request['request_id'], '--adapter', 'neutral',
        cwd=root, env_extra={**env, 'MISSION_OPERATION_ID': 'consume-one'})
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)['record']
    assert record['status'] == 'failed' and record['result']['reason'] == reason
    assert 'output_ref' not in record['result'] and 'output_digest' not in record['result']
    _reject_unchanged(run_cli, root, ['mark-passes'], 'acceptance-coverage-pending')


def test_import_checks_entire_launch_receipt_beyond_sender_fields(reviewer, run_cli):
    root, request, env, journal = reviewer
    invoke(run_cli, reviewer)
    exited(journal)
    stored = json.loads(journal.read_text())
    receipt = dict(stored['launch'], started_at='2027-01-01T00:00:00.000000Z')
    assert receipt != stored['launch']
    stored['observation_updates'] = {'launch_receipt': receipt}
    journal.write_text(json.dumps(stored))
    _reject_unchanged(run_cli, root, ['fresh-review', 'import', '--request', request['request_id'],
        '--adapter', 'neutral'], 'fresh-review-output-sender-mismatch',
        env={**env, 'MISSION_OPERATION_ID': 'import-one'})


def _forge_diagnostic_reference(command, mutation):
    from dataclasses import replace
    from mission_kernel.json_codec import freeze_json_value
    raw = command.receipt.thaw()
    effect = command.effect
    if mutation == 'size':
        raw['output_ref']['size'] = 1
    else:
        digest = 'sha256:'+'b'*64
        target = 'evidence/fresh-review/'+'b'*64+'.json'
        raw['output_ref'].update(digest=digest, relative_path=target)
        raw['output_digest'] = digest
        effect = replace(effect, target=target)
    return replace(command, receipt=freeze_json_value(raw), effect=effect)


@pytest.mark.parametrize('mutation', ['size', 'digest-and-path'])
def test_failed_import_binds_reference_size_and_digest_to_diagnostic(prepared_failure, mutation):
    from mission_kernel.fresh_review import FreshReviewError
    from mission_kernel.fresh_review_publish import validate_failed_import
    record, prepare = prepared_failure
    forged = _forge_diagnostic_reference(prepare().command, mutation)
    with pytest.raises(FreshReviewError, match='fresh-review-output-effect-invalid'):
        validate_failed_import(record, forged)


