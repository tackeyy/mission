"""Completed D2 publishes observed evidence without granting completion."""
import json
import pytest
from .test_issue912_fresh_review_dispatch import completion_session, reviewer, invoke
from .test_issue896_publish import import_output, bound_output, exited
from .test_issue879_completion_cli import _reject_unchanged


@pytest.mark.parametrize('mode,finding_count', [('fresh', 0), ('inline', 0), ('fresh', 61)])
def test_completed_output_and_coverage_share_one_public_commit(reviewer, run_cli, mode, finding_count):
    from mission_kernel.fresh_review import canonical_bytes
    root, request, _, journal = reviewer
    running = invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE=mode)
    from .test_issue896_output_kernel import finding
    raw = bound_output(request)
    raw['criterion_results'][0]['findings'] = [dict(finding(index), command_id='unregistered', prohibited_side_effect_ids=[]) for index in range(finding_count)]
    exited(journal, output=canonical_bytes(raw).decode())
    result = import_output(run_cli, reviewer)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)['record']
    assert record['status'] == 'completed'
    receipt = record['result']
    assert receipt['independent'] is (mode == 'fresh')
    assert receipt['launch_receipt'] == running['launch']
    assert receipt['coverage_receipt']['status'] == 'valid'
    coverage = json.loads((root / receipt['coverage_receipt']['evidence_ref']['relative_path']).read_bytes())
    assert len(coverage['open_finding_ids']) == finding_count
    head = json.loads((root / '.mission-state/sessions/test.json').read_text())
    manifest = json.loads((root / '.mission-state' / head['state_generation']['path']).read_text())
    assert len(manifest['blobs']) == 2 + finding_count <= 63
    for reference in [receipt['output_ref'], receipt['coverage_receipt']['evidence_ref'], *receipt['findings']]:
        assert any(item['digest'] == reference['digest'] for item in manifest['blobs'])
        assert (root / reference['relative_path']).stat().st_size == reference['size']
    assert json.loads(import_output(run_cli, reviewer).stdout)['record'] == record
    _reject_unchanged(run_cli, root, ['fresh-review', 'import', '--request', request['request_id'],
        '--adapter', 'neutral'], 'fresh-review-consumed', env={**reviewer[2], 'MISSION_OPERATION_ID': 'import-two'})
    _reject_unchanged(run_cli, root, ['mark-passes'], 'acceptance-receipt-missing')


@pytest.fixture
def replay_reviewer(completion_session, run_cli, tmp_path, request):
    import hashlib
    from .test_issue878_verification_runner import _replay_policy
    root, state, schema = completion_session
    policy = _replay_policy()
    case = getattr(request, 'param', None)
    if case == 'timeout':
        policy['commands'][1].update(timeout_sec=1, argv=policy['commands'][1]['argv'][:1] + ['-c', 'import time; time.sleep(2)'])
    if case == 'stale-toolchain':
        policy['commands'][1]['toolchain']['digest'] = 'sha256:' + '0' * 64
    if case in ('path-conflict', 'budget-path-conflict'):
        policy['commands'][0]['replay']['relative_path'] = 'app.txt'
    if case == 'budget-path-conflict':
        from datetime import datetime, timezone
        from mission_kernel.budget import decode_policy, default_policy_document, ledger_document, new_ledger
        state['budget_minutes'] = 30
        state['budget_ledger'] = ledger_document(new_ledger(decode_policy(default_policy_document(1800)),
            datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')))
    encoded = json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()
    (root / '.mission/verifiers.json').write_bytes(encoded)
    digest = 'sha256:' + hashlib.sha256(encoded).hexdigest()
    state['acceptance_contract'].update(verifier_policy_digest=digest,
        verifier_policy=dict(digest=digest, commands={item['id']: item for item in policy['commands']}))
    return reviewer.__wrapped__((root, state, schema), run_cli, tmp_path)


def test_fixture_child_counterexample_has_replay_facts_and_completion_stays_closed(replay_reviewer, run_cli):
    root, request, _, journal = replay_reviewer
    running = invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    assert running['status'] == 'running', running
    result = import_output(run_cli, replay_reviewer)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads(result.stdout)['record']
    assert record['status'] == 'completed'
    receipt = record['result']
    assert receipt['launch_receipt']['received_input_digest'] == request['input_digest']
    assert receipt['launch_receipt']['child_identity'] != receipt['launch_receipt']['parent_identity']
    assert receipt['coverage_receipt']['status'] == 'valid'
    assert len(receipt['findings']) == 1
    finding = json.loads((root / receipt['findings'][0]['relative_path']).read_bytes())
    assert finding['criterion_id'] == 'AC1' and finding['requirement_ids'] == ['R1']
    assert finding['actual']['exit_code'] == 1  # actual includes runner facts, beyond the authored exit code
    assert finding['replay']['status'] == 'failed'
    assert finding['replay']['repro_input_digest'].startswith('sha256:')
    assert finding['replay']['candidate_digest'] == next(b['snapshot_digest'] for b in request['candidate_bindings'] if b['role'] == 'replay')
    assert finding['status'] == 'verified' and finding['resolution'] == 'open'
    assert json.loads(journal.read_text())['count'] == 1
    assert json.loads(run_cli('get', cwd=root).stdout).get('verification_receipts', []) == []
    _reject_unchanged(run_cli, root, ['mark-passes'], 'acceptance-receipt-missing')


@pytest.mark.parametrize('expired', [False, True])
def test_matching_sender_with_changed_launch_cancels_only_after_deadline(reviewer, run_cli, expired):
    from .test_issue912_fresh_review_dispatch import _expire_dispatch
    root, request, env, journal = reviewer
    running = invoke(run_cli, reviewer)
    stored = json.loads(journal.read_text())
    stored['launch']['context_identity'] = 'changed-context'
    stored.update(process_exited=True, output='invalid', exit_code=0,
        budget_used=dict(wall_time_sec=1, tool_calls=0, replays=0, output_bytes=7))
    journal.write_text(json.dumps(stored))
    if not expired:
        _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', request['request_id'],
            '--adapter', 'neutral'], 'fresh-review-launch-binding-mismatch',
            env={**env, 'MISSION_OPERATION_ID': 'reconcile-one'})
        assert not journal.with_suffix('.cancel').exists()
    else:
        _expire_dispatch(reviewer)
        record = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one')
        assert record['status'] == 'abandoned-unknown'
        assert record['result']['reason'] == 'child-unobservable'
        assert record['result']['launch_receipt'] == running['launch']
        assert journal.with_suffix('.cancel').read_text() == 'running'


@pytest.mark.parametrize('case,reason', [('late', 'timeout'), ('ledger', 'output-invalid'), ('reference', 'output-invalid')])
def test_bound_output_fails_closed_for_deadline_and_contract_content(reviewer, run_cli, case, reason):
    from .test_issue912_fresh_review_dispatch import _expire_dispatch
    from mission_kernel.fresh_review import canonical_bytes
    root, request, _, journal = reviewer
    invoke(run_cli, reviewer)
    raw = bound_output(request)
    if case == 'late':
        _expire_dispatch(reviewer)
    elif case == 'ledger':
        raw['coverage'][0]['requirement_id'] = 'foreign'
    else:
        from .test_issue896_output_kernel import finding
        raw['criterion_results'][0]['findings'] = [{**finding(), 'requirement_ids': ['foreign']}]
    exited(journal, output=canonical_bytes(raw).decode())
    result = import_output(run_cli, reviewer)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads(result.stdout)['record']
    assert record['status'] == 'failed' and record['result']['reason'] == reason
    assert 'coverage_receipt' not in record['result']
    assert (root / record['result']['output_ref']['relative_path']).read_bytes() == canonical_bytes(raw)


@pytest.mark.parametrize('case', ['unsupported', 'budget', 'claim', 'passed'])
def test_unverified_replay_is_kept_as_an_open_finding(replay_reviewer, run_cli, case):
    from mission_kernel.fresh_review import canonical_bytes
    root, _, _, journal = replay_reviewer
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    stored = json.loads(journal.read_text())
    raw = json.loads(stored['output'])
    finding = raw['criterion_results'][0]['findings'][0]
    if case == 'unsupported':
        finding['command_id'] = 'unregistered'
    elif case == 'budget':
        stored['budget_used']['replays'] = 16
    elif case in ('claim', 'passed'):
        finding['actual'] = {'exit_code': 0}
        if case == 'passed':
            finding['repro_input']['content'] = 'proof'
    stored['output'] = canonical_bytes(raw).decode()
    stored['budget_used']['output_bytes'] = len(stored['output'].encode())
    journal.write_text(json.dumps(stored))
    result = import_output(run_cli, replay_reviewer)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)['record']['result']
    evidence = json.loads((root / receipt['findings'][0]['relative_path']).read_bytes())
    assert evidence['status'] == 'blocked' and evidence['resolution'] == 'open'
    assert evidence['reason_code'] == {'unsupported': 'replay-unsupported', 'budget': 'replay-budget-exceeded',
                                     'claim': 'replay-claim-unconfirmed', 'passed': 'replay-claim-unconfirmed'}[case]
    if case == 'passed':
        assert evidence['replay']['status'] == 'passed' and evidence['actual']['exit_code'] == 0
    _reject_unchanged(run_cli, root, ['mark-passes'], 'acceptance-receipt-missing')


@pytest.mark.parametrize('replay_reviewer,reason', [('timeout','timeout'), ('stale-toolchain','toolchain-stale'),
    ('path-conflict','replay-input-path-conflict'), ('budget-path-conflict','replay-input-path-conflict')], indirect=['replay_reviewer'])
def test_replay_execution_failure_retains_an_open_finding(replay_reviewer, run_cli, reason):
    root, _, _, _ = replay_reviewer
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    result = import_output(run_cli, replay_reviewer)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)['record']['result']
    evidence = json.loads((root / receipt['findings'][0]['relative_path']).read_bytes())
    assert evidence['status'] == 'blocked' and evidence['reason_code'] == reason
    assert evidence['resolution'] == 'open'
    if reason == 'replay-input-path-conflict':
        from datetime import datetime
        replay = evidence['replay']
        parse = lambda value: datetime.fromisoformat(value.replace('Z', '+00:00'))
        assert parse(receipt['launch_receipt']['started_at']).replace(microsecond=0) <= parse(replay['started_at'])
        assert parse(replay['started_at']) <= parse(replay['finished_at']) <= parse(receipt['ended_at'])
        assert replay['candidate_digest'] != 'sha256:' + '0'*64


def test_completed_writer_maximum_shape_effects_and_remaining_reservation(replay_reviewer, run_cli, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from .test_issue879_completion_cli import _persisted_fixture_document
    from .test_issue917_fresh_review_bounds import maximum_launch
    from mission_application import fresh_review_publish as publisher
    from mission_kernel.fresh_review import decode_projection, canonical_digest, canonical_bytes, request_document, ToolCapability
    from mission_kernel.fresh_review_receipts import FRESH_REVIEW_MAX_ENCODED_BYTES
    from mission_kernel.json_codec import freeze_json_value, encode_json_value
    from mission_kernel.state_capacity import residual_reservation, lineage_stage_delta, _diff_is_record_status_advance
    root, _, _, journal = replay_reviewer
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    state = _persisted_fixture_document(root)
    record = decode_projection(state).requests[0]
    request = replace(record.request, request_id='r'*128, nonce='n'*128,
        allowed_tools=(ToolCapability.READ_CANDIDATE, ToolCapability.REPLAY_VERIFIER))
    launch = maximum_launch()
    launch.update(request_id=request.request_id, nonce=request.nonce,
        request_digest=canonical_digest(request_document(request)),
        adapter_registration_digest=request.adapter_registration_digest, received_input_digest=request.input_digest,
        started_at='9999-12-30T00:00:00.000000Z')
    record = replace(record, request=request, launch=freeze_json_value(launch), independent=False,
        dispatch=freeze_json_value({**record.dispatch.thaw(), 'operation_id': launch['operation_id'],
            'fencing_epoch': launch['fencing_epoch'], 'parent_identity': launch['parent_identity'],
            'deadline_at': '9999-12-31T23:59:59.999999Z'}))
    raw = bound_output(request_document(request))
    hypothesis = json.loads(json.loads(journal.read_text())['output'])['criterion_results'][0]['findings'][0]
    raw['criterion_results'][0]['findings'] = [{**hypothesis, 'finding_id': str(i)+'f'*125,
        'command_id': 'unregistered'} for i in range(61)]
    payload = canonical_bytes(raw)
    observed = {key: launch[key] for key in ('operation_id','fencing_epoch','request_id','nonce','child_identity')}
    observed.update(process_exited=True, exit_code=0, budget_used=dict(
        wall_time_sec=300, tool_calls=64, replays=16, output_bytes=262144))
    monkeypatch.setattr(publisher, '_record', lambda *_: record)
    monkeypatch.setattr(publisher, '_candidate', lambda *_args, **_kw: request.candidate_digest)
    prepared = publisher.prepare_failed_output(state, request_id=request.request_id, operation='c'*128,
        epoch=2**63-1, observation=observed, raw=payload, root=root,
        services=SimpleNamespace(now=lambda: '9999-12-30T23:59:59.999999Z'))
    receipt = prepared.command.receipt.thaw()
    assert receipt['outcome'] == 'completed'
    assert len(prepared.effects) == 63 and len(prepared.effects) + 1 == 64  # event slot
    assert len(encode_json_value(receipt)) <= FRESH_REVIEW_MAX_ENCODED_BYTES['completed']
    assert all(effect.size <= 262144 for effect in prepared.effects)
    # Use the real request to ensure the completed writer releases only D's
    # terminal reservation, retaining E's unresolved-lineage reservation.
    original = decode_projection(state).requests[0]
    simple = json.loads(json.loads(journal.read_text())['output'])
    simple['criterion_results'][0]['findings'] = []
    saved = json.loads(journal.read_text())
    monkeypatch.setattr(publisher, '_record', lambda *_: original)
    monkeypatch.setattr(publisher, '_candidate', lambda *_args, **_kw: original.request.candidate_digest)
    obs = {key: original.launch.thaw()[key] for key in observed if key in original.launch.thaw()}
    obs.update(process_exited=True, exit_code=0, budget_used=saved['budget_used'])
    terminal = publisher.prepare_failed_output(state, request_id=original.request.request_id, operation='import-one',
        epoch=state['fencing_epoch'], observation=obs, raw=canonical_bytes(simple), root=root,
        services=SimpleNamespace(now=lambda: original.launch.thaw()['started_at'])).command.receipt.thaw()
    after = json.loads(json.dumps(state))
    after['fresh_review']['requests'][0].update(status='completed', result=terminal)
    assert _diff_is_record_status_advance(state, after)
    assert residual_reservation(after) == lineage_stage_delta(after, original.request) > 0


@pytest.fixture
def completed_carrier():
    """Neutral runner observations for pure import-boundary counterexamples."""
    from dataclasses import replace
    from mission_kernel.fresh_review import canonical_bytes, canonical_digest, request_document
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.commands import ImportFreshReviewOutput
    from mission_kernel.fresh_review_output import decode_output
    from mission_kernel.fresh_review_publish import completed_evidence, evidence_claim, reference
    from .test_issue896_output_kernel import running, observation, replay_fixture, coverage_fixture
    from mission_application.fresh_review_dispatch import _terminal
    import base64
    request, hypothesis, policy = replay_fixture()
    from acceptance_contract import canonical_contract_digest
    _, _, contract = coverage_fixture()
    contract.update(schema='mission-acceptance-contract/2', verifier_policy_digest=policy['digest'], verifier_policy=policy)
    request = replace(request, contract_digest=canonical_contract_digest(contract), requirement_digest=contract['requirement_digest'])
    record = running()
    launch = record.launch.thaw()
    launch.update(request_digest=canonical_digest(request_document(request)))
    record = replace(record, request=request, launch=freeze_json_value(launch))
    raw = bound_output(request_document(request))
    raw['criterion_results'][0]['findings'] = [__import__('dataclasses').asdict(hypothesis)]
    raw['criterion_results'][0]['findings'][0].update(requirement_ids=['R1'], prohibited_side_effect_ids=['AC1:0'],
        actual={'exit_code': 1}, expected={'criterion_id': 'AC1'}, repro_input={'artifact_kind': 'text', 'content': '0'})
    raw['coverage'].append(dict(requirement_id='R2', classification_confirmed=True, criterion_ids=[],
        status='valid', reason_code='none', reason='Context.'))
    command = policy['commands']['replay-1']
    replay = dict(schema='mission-verification-receipt/1', contract_digest=request.contract_digest,
        criterion_id='AC1', candidate_digest=request.candidate_bindings[1].snapshot_digest,
        verifier_policy_digest=request.verifier_policy_digest, verifier_definition_digest=canonical_digest(command),
        argv=command['argv'], relative_cwd='.', started_at='2026-01-01T00:00:00Z',
        finished_at='2026-01-01T00:00:01Z', exit_code=1, timed_out=False, executed_count=None,
        output_digest='sha256:'+'a'*64, observed_output_bytes=1, output_truncated=False, status='failed',
        runner_provenance='mission-public-cli/1', repro_input_digest='sha256:'+__import__('hashlib').sha256(b'text' + b'\0' + b'repro.txt' + b'\0' + b'0').hexdigest(),
        block_reason=None)
    replays = (freeze_json_value(dict(finding_id=hypothesis.finding_id,reason_code='none',replay=replay)),)
    output = decode_output(raw)
    content, findings = completed_evidence(output, request, contract, replays)
    payload = canonical_bytes(raw)
    claim = evidence_claim('fresh-review-output', payload)
    coverage = evidence_claim('fresh-review-coverage', content)
    finding_claims = tuple(evidence_claim('fresh-review-finding', item) for item in findings)
    used = dict(wall_time_sec=1, tool_calls=0, replays=1, output_bytes=len(payload))
    receipt = _terminal(record, 'import', 3, 'failed', 'child-failed', '2026-01-01T00:00:02.000000Z').thaw()
    receipt.update(outcome='completed', reason='none', budget_used=used, independent=True,
        output_ref=reference(claim), output_digest=claim.digest,
        coverage_receipt=dict(status='valid',evidence_ref=reference(coverage)),
        findings=[reference(item) for item in finding_claims])
    result = ImportFreshReviewOutput(request.request_id, 'import', 3, freeze_json_value(receipt),
        freeze_json_value(observation(record)), freeze_json_value(used), request.candidate_digest,
        base64.b64encode(payload).decode(), claim, coverage, finding_claims, replays)
    return record, result, contract


@pytest.mark.parametrize('value', [None, '1', [], {}])
def test_import_rejects_malformed_replay_budget_without_crashing(completed_carrier, value):
    from dataclasses import replace
    from mission_kernel.fresh_review import FreshReviewError
    from mission_kernel.fresh_review_publish import validate_failed_import
    from mission_kernel.json_codec import freeze_json_value
    record, command, contract = completed_carrier
    used = command.budget_used.thaw()
    used['replays'] = value
    with pytest.raises(FreshReviewError, match='fresh-review-output-observation-invalid'):
        validate_failed_import(record, replace(command, budget_used=freeze_json_value(used)), contract)


@pytest.mark.parametrize('status,exit_code,actual,expected_status', [
    ('passed', 0, {'exit_code': 0}, 'blocked'),
    ('passed', 0, {'status': 'passed'}, 'blocked'),
    ('failed', 1, {'exit_code': 1}, 'verified'),
    ('failed', 0, {'exit_code': 0}, 'blocked'),
    ('failed', -1, {'exit_code': -1}, 'blocked'),
])
def test_only_observed_command_failure_verifies_counterexample(completed_carrier, status, exit_code, actual, expected_status):
    import base64
    from mission_kernel.fresh_review_output import decode_output
    from mission_kernel.fresh_review_publish import completed_evidence
    from mission_kernel.json_codec import freeze_json_value
    record, command, contract = completed_carrier
    output = json.loads(base64.b64decode(command.output_base64))
    output['criterion_results'][0]['findings'][0]['actual'] = actual
    carrier = command.replay_results[0].thaw()
    carrier['replay'].update(status=status, exit_code=exit_code)
    _, findings = completed_evidence(decode_output(output), record.request, contract, (freeze_json_value(carrier),))
    finding = json.loads(findings[0])
    assert finding['status'] == expected_status and finding['resolution'] == 'open'
    assert finding['reason_code'] == ('none' if expected_status == 'verified' else 'replay-claim-unconfirmed')


def _rebind_replay_finding(record, command, contract, carrier):
    """Rebuild artifact claims too, so only the replay binding guard rejects."""
    import base64
    from dataclasses import replace
    from mission_kernel.fresh_review_output import decode_output
    from mission_kernel.fresh_review_publish import completed_evidence, evidence_claim, reference
    from mission_kernel.fresh_review import canonical_bytes
    from mission_kernel.json_codec import freeze_json_value
    output = decode_output(json.loads(base64.b64decode(command.output_base64)))
    _, findings = completed_evidence(output, record.request, contract, command.replay_results)
    finding = json.loads(findings[0])
    # Derive the valid projection, then replace its embedded observation. This
    # lets the test construct coherent forged evidence without disabling guards.
    finding['replay'] = carrier['replay']
    claims = (evidence_claim('fresh-review-finding', canonical_bytes(finding)),)
    receipt = command.receipt.thaw()
    receipt['findings'] = [reference(item) for item in claims]
    return replace(command, receipt=freeze_json_value(receipt), findings_effect=claims,
                   replay_results=(freeze_json_value(carrier),))


@pytest.mark.parametrize('field', ['contract_digest', 'criterion_id', 'candidate_digest',
    'verifier_definition_digest', 'verifier_policy_digest', 'repro_input_digest',
    'argv', 'runner_provenance', 'relative_cwd'])
def test_completed_import_rejects_forged_replay_binding(completed_carrier, field):
    from mission_kernel.fresh_review import FreshReviewError
    from mission_kernel.fresh_review_publish import validate_failed_import
    record, command, contract = completed_carrier
    carrier = command.replay_results[0].thaw()
    carrier['replay'][field] = ('sha256:' + 'b' * 64 if field.endswith('_digest') else
                              ['other-verifier'] if field == 'argv' else 'other')
    forged = _rebind_replay_finding(record, command, contract, carrier)
    with pytest.raises(FreshReviewError, match='^fresh-review-replay-binding-invalid$'):
        validate_failed_import(record, forged, contract)


def test_completed_import_rejects_underreported_replay_usage(completed_carrier):
    from dataclasses import replace
    from mission_kernel.fresh_review import FreshReviewError
    from mission_kernel.fresh_review_publish import validate_failed_import
    from mission_kernel.json_codec import freeze_json_value
    record, command, contract = completed_carrier
    validate_failed_import(record, command, contract)
    used = command.budget_used.thaw()
    used['replays'] = 0
    receipt = command.receipt.thaw()
    receipt['budget_used'] = used
    with pytest.raises(FreshReviewError, match='^fresh-review-replay-budget-invalid$'):
        validate_failed_import(record, replace(command, budget_used=freeze_json_value(used),
            receipt=freeze_json_value(receipt)), contract)


@pytest.mark.parametrize('field', ['coverage_effect', 'findings_effect'])
def test_import_checks_claims_even_when_receipt_references_are_valid(completed_carrier, field):
    from dataclasses import replace
    from mission_kernel.fresh_review import FreshReviewError
    from mission_kernel.fresh_review_publish import validate_failed_import
    record, command, contract = completed_carrier
    claim = command.coverage_effect if field == 'coverage_effect' else command.findings_effect[0]
    forged = replace(claim, size=claim.size + 1)
    with pytest.raises(FreshReviewError, match='^fresh-review-output-effect-invalid$'):
        validate_failed_import(record, replace(command, **{field: forged if field == 'coverage_effect' else (forged,)}), contract)


def test_import_checks_terminal_independence_directly(completed_carrier):
    from dataclasses import replace
    from mission_kernel.fresh_review import FreshReviewError
    from mission_kernel.fresh_review_publish import validate_failed_import
    from mission_kernel.json_codec import freeze_json_value
    record, command, contract = completed_carrier
    receipt = command.receipt.thaw()
    receipt['independent'] = False
    with pytest.raises(FreshReviewError, match='^fresh-review-output-effect-invalid$'):
        validate_failed_import(record, replace(command, receipt=freeze_json_value(receipt)), contract)


def test_decode_checks_terminal_independence_directly(completed_carrier):
    from mission_kernel.fresh_review import FreshReviewError, canonical_digest
    from mission_kernel.fresh_review_dispatch import decode_dispatch_record, reservation_id_for_operation, budget_class_for_fresh_review_dispatch
    from .test_issue917_fresh_review_bounds import maximum_intent
    record, command, _ = completed_carrier
    fields = {name: getattr(record, name) for name in record.__dataclass_fields__}
    dispatch = maximum_intent()
    dispatch.update(operation_id=record.operation_id, fencing_epoch=2,
        invocation_id='inv_' + canonical_digest(record.request.request_id)[7:39],
        parent_identity=record.launch.thaw()['parent_identity'], outbound_packet_digest=record.request.input_digest,
        iteration=record.request.iteration, reservation_id=reservation_id_for_operation(record.operation_id),
        budget_class=budget_class_for_fresh_review_dispatch())
    fields.update(status='completed', launch=record.launch.thaw(), dispatch=dispatch,
        intent_digest='sha256:'+'a'*64, payload_digest='sha256:'+'a'*64, result=command.receipt.thaw())
    decode_dispatch_record(dict(fields))
    fields['result']['independent'] = False
    with pytest.raises(FreshReviewError, match='^fresh-review-independent-invalid$'):
        decode_dispatch_record(fields)


@pytest.mark.parametrize('expired', [False, True])
def test_replay_candidate_drift_is_reason_coded_and_never_publishes_success(replay_reviewer, run_cli, monkeypatch, capsys, expired):
    import base64
    from types import SimpleNamespace
    from .test_command_inventory import _load_mission_state_module
    from .test_issue879_completion_cli import _public_bytes, _persisted_fixture_document
    from .test_issue912_fresh_review_dispatch import _expire_dispatch
    from mission_application import fresh_review_publish as publisher
    root, request, env, journal = replay_reviewer
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    if expired:
        _expire_dispatch(replay_reviewer)
    saved = json.loads(journal.read_text())
    launch = saved['launch']
    observation = {key: launch[key] for key in ('operation_id', 'fencing_epoch', 'request_id', 'nonce', 'child_identity')}
    observation.update(process_exited=True, exit_code=0, budget_used=saved['budget_used'], launch_receipt=launch)
    host = SimpleNamespace(resolve=lambda _: SimpleNamespace(registration=SimpleNamespace(digest=request['adapter_registration_digest'])),
        recover=lambda *_: dict(observation=observation, output=base64.b64encode(saved['output'].encode()).decode()))
    runner = publisher.run_contract_verifier
    def drift(*args, **kwargs):
        return {**runner(*args, **kwargs), 'candidate_digest': 'sha256:' + 'b'*64}
    monkeypatch.setattr(publisher, 'run_contract_verifier', drift)
    monkeypatch.chdir(root)
    for key, value in {**env, 'MISSION_OPERATION_ID': 'import-one', 'MISSION_SESSION_ID': 'test', 'MISSION_LEASE_ID': 'test-lease'}.items():
        monkeypatch.setenv(key, value)
    before = _public_bytes(root)
    services = _load_mission_state_module()._ACCEPTANCE_CONTRACT_CLI_SERVICES
    args = SimpleNamespace(request=request['request_id'], adapter='neutral')
    if expired:
        result = json.loads(publisher.run_fresh_review_import_cli(args, services, host))
        assert result['record']['status'] == 'failed' and result['record']['result']['reason'] == 'timeout'
    else:
        with pytest.raises(SystemExit) as error:
            publisher.run_fresh_review_import_cli(args, services, host)
        assert error.value.code == 2
        assert 'fresh-review-replay-binding-invalid' in capsys.readouterr().err
        assert _public_bytes(root) == before
        assert _persisted_fixture_document(root)['fresh_review']['requests'][0]['status'] == 'running'


@pytest.mark.parametrize('field,value', [(field, value) for field in ('started_at', 'finished_at')
    for value in (None, True, 'invalid', '2026-01-01T00:00:00Q', '2025-01-01T00:00:00Z', '2027-01-01T00:00:00Z')])
def test_replay_time_must_be_real_ordered_and_within_the_launch(completed_carrier, field, value):
    from dataclasses import replace
    from mission_kernel.fresh_review import FreshReviewError
    from mission_kernel.fresh_review_output import decode_output
    from mission_kernel.fresh_review_publish import completed_evidence, evidence_claim, reference, validate_failed_import
    from mission_kernel.json_codec import freeze_json_value
    import base64
    record, command, contract = completed_carrier
    carrier = command.replay_results[0].thaw()
    carrier['replay'][field] = value
    replays = (freeze_json_value(carrier),)
    # Bind the forged bytes too: rejecting a stale digest would not detect the
    # missing temporal check found by the independent input exploration.
    output = decode_output(json.loads(base64.b64decode(command.output_base64)))
    try:
        _, findings = completed_evidence(output, record.request, contract, replays)
    except FreshReviewError:
        return
    claims = tuple(evidence_claim('fresh-review-finding', item) for item in findings)
    receipt = command.receipt.thaw()
    receipt['findings'] = [reference(item) for item in claims]
    with pytest.raises(FreshReviewError):
        validate_failed_import(record, replace(command, receipt=freeze_json_value(receipt),
            replay_results=replays, findings_effect=claims), contract)


@pytest.mark.parametrize('outcome,reason', [('blocked', 'registration-mismatch'), ('abandoned-unknown', 'child-unobservable')])
def test_no_output_terminal_writers_remain_within_e0(outcome, reason):
    from dataclasses import replace
    from mission_application.fresh_review_dispatch import _terminal
    from mission_kernel.json_codec import freeze_json_value, encode_json_value
    from mission_kernel.fresh_review_receipts import FRESH_REVIEW_MAX_ENCODED_BYTES
    from .test_issue896_output_kernel import running
    from .test_issue917_fresh_review_bounds import maximum_launch
    from mission_kernel.fresh_review import canonical_digest, request_document
    record = running()
    request = replace(record.request, request_id='r'*128, nonce='n'*128)
    launch = maximum_launch()
    launch['request_digest'] = canonical_digest(request_document(request))
    record = replace(record, request=request, launch=freeze_json_value(launch),
        dispatch=freeze_json_value(dict(operation_id=launch['operation_id'], fencing_epoch=2**63-1)))
    terminal = _terminal(record, 'c'*128, 2**63-1, outcome, reason,
        '9999-12-31T23:59:59.999999Z', attempted=True, cancel='cancelled', wall_time_sec=2**63-1)
    assert len(encode_json_value(terminal)) <= FRESH_REVIEW_MAX_ENCODED_BYTES[outcome]


def test_expanded_replay_evidence_overflow_preserves_only_failed_diagnostics(replay_reviewer, run_cli):
    from mission_kernel.fresh_review import canonical_bytes
    root, request, env, journal = replay_reviewer
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    raw = json.loads(json.loads(journal.read_text())['output'])
    hypothesis = raw['criterion_results'][0]['findings'][0]
    hypothesis['summary'] = ''
    hypothesis['summary'] = 'x' * (262144 - len(canonical_bytes(raw)))
    payload = canonical_bytes(raw)
    assert len(payload) == 262144
    exited(journal, output=payload.decode())
    result = import_output(run_cli, replay_reviewer)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)['record']
    assert record['status'] == 'failed' and record['result']['reason'] == 'output-over-import-limit'
    assert 'coverage_receipt' not in record['result'] and 'findings' not in record['result']
    ref = record['result']['output_ref']
    assert (root / ref['relative_path']).read_bytes() == payload
    assert json.loads(import_output(run_cli, replay_reviewer).stdout)['record'] == record
    next_attempt = run_cli('fresh-review', 'prepare', '--perspective', 'counterexamples',
        '--adapter-registration-digest', request['adapter_registration_digest'], cwd=root,
        env_extra={**env, 'MISSION_OPERATION_ID': 'prepare-two'})
    assert next_attempt.returncode == 0, next_attempt.stderr
    requests = json.loads(run_cli('fresh-review', 'status', cwd=root).stdout)['requests']
    assert requests[0]['result'] == record['result']
    _reject_unchanged(run_cli, root, ['mark-passes'], 'acceptance-receipt-missing')


def test_sender_rejection_precedes_malformed_output_and_replay(completed_carrier):
    from dataclasses import replace
    from mission_kernel.fresh_review import FreshReviewError
    from mission_kernel.fresh_review_publish import validate_failed_import
    from mission_kernel.json_codec import freeze_json_value
    record, command, contract = completed_carrier
    observation = command.observation.thaw()
    observation['nonce'] = 'foreign'
    replay = command.replay_results[0].thaw()
    replay['replay']['started_at'] = None
    with pytest.raises(FreshReviewError, match='fresh-review-output-sender-mismatch'):
        validate_failed_import(record, replace(command, observation=freeze_json_value(observation),
            output_base64='!', replay_results=(freeze_json_value(replay),)), contract)
