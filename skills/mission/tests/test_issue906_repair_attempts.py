"""Begun repair obligations survive publication loss without granting success."""
import json
import pytest

from .test_issue896_completed import replay_reviewer
from .test_issue912_fresh_review_dispatch import completion_session, invoke
from .test_issue896_publish import import_output
from .test_issue879_completion_cli import _persist_fixture, _reject_unchanged


@pytest.mark.parametrize('schema', [4, 5])
def test_public_begin_and_reconcile_preserve_unresolved_history(replay_reviewer, run_cli, schema):
    root = replay_reviewer[0]
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    assert import_output(run_cli, replay_reviewer).returncode == 0
    state = json.loads(run_cli('get', cwd=root).stdout)
    if schema == 4:
        _persist_fixture(root, state, schema)
    lineage = state['repair_lineage']['lineages'][0]['lineage_id']
    (root / 'repair-plan.json').write_text('{"change":"repair the reproduced defect"}')
    begun = run_cli('repair', 'begin', '--finding', lineage, '--plan-ref', 'repair-plan.json', cwd=root, env_extra={'MISSION_OPERATION_ID': 'begin-public'})
    assert begun.returncode == 0, begun.stdout + begun.stderr
    attempt = json.loads(begun.stdout)['attempt']
    assert attempt['status'] == 'pending'
    retry = run_cli('repair', 'begin', '--finding', lineage, '--plan-ref', 'repair-plan.json', cwd=root, env_extra={'MISSION_OPERATION_ID': 'begin-public'})
    assert retry.returncode == 0, retry.stdout + retry.stderr
    assert json.loads(retry.stdout)['attempt'] == attempt
    state = json.loads(run_cli('get', cwd=root).stdout)
    row = state['repair_lineage']['lineages'][0]
    assert row['lifecycle'] == 'repairing' and row['attempts'] == [attempt]
    body = json.loads((root / attempt['history_ref']['relative_path']).read_text())
    assert body['repro_input_digest'] == row['repro']['repro_input_digest']
    terminal = run_cli('repair', 'reconcile', '--attempt', attempt['attempt_id'], cwd=root)
    assert terminal.returncode == 0, terminal.stdout + terminal.stderr
    ended = json.loads(terminal.stdout)['attempt']
    assert ended['status'] == 'blocked'
    assert ended['terminal']['reason'] == 'publication-result-lost'
    assert ended['terminal']['event_ref'] == {'kind': 'absent', 'reason': 'events-not-enabled'}
    again = run_cli('repair', 'reconcile', '--attempt', attempt['attempt_id'], cwd=root, check=True)
    assert json.loads(again.stdout)['attempt'] == ended
    state = json.loads(run_cli('get', cwd=root).stdout)
    assert state['repair_lineage']['lineages'][0]['lifecycle'] == 'open'
    _reject_unchanged(run_cli, root, ['set', 'repair_lineage.lineages=[]'], 'dedicated')
    assert run_cli('repair', 'reverify', '--attempt', attempt['attempt_id'], cwd=root).returncode != 0

from dataclasses import replace
from types import SimpleNamespace
from mission_kernel.fresh_review import canonical_bytes, canonical_digest, FreshReviewError
from mission_kernel.json_codec import freeze_json_value
from .test_issue913_completion_inputs import published, gate_state
from .test_issue896_completed import completed_carrier


@pytest.fixture
def repair_state(published, gate_state):
    from mission_kernel.repair_lineage import import_origins
    from mission_kernel.fresh_review_completion import decode_completion_evidence
    from mission_kernel.fresh_review import projection_document
    record, end, _, coverage, findings = published
    evidence = decode_completion_evidence(record.request, end, json.loads(coverage), tuple(map(json.loads, findings)))
    doc = gate_state.legacy_passthrough.thaw()
    doc['fresh_review'] = projection_document(gate_state.fresh_review)
    from mission_kernel.model import FencedLease
    return import_origins(replace(gate_state, lease=FencedLease('session', 'lease', 2, '2099-01-01T00:00:00Z', ()),
        legacy_passthrough=freeze_json_value(doc)), (evidence,)), evidence


def begin_command(state, evidence):
    from mission_kernel.commands import BeginFindingRepair, FreshReviewInputEffectClaim
    from mission_kernel.repair_attempts import reference, attempt_id, baseline_receipts
    row = state.repair.lineages[0].document.thaw()
    repro = evidence.findings[0].thaw()['repro_input']
    plan = 'bounded repair plan'
    body = dict(schema='mission-repair-attempt/1', lineage_id=row['lineage_id'], attempt_id=attempt_id(row['lineage_id'], 'begin'),
        before_candidate=dict(row['introduced_candidate'], iteration=state.control.iteration), plan_ref=reference('repair-plan', plan.encode()),
        repro_input_ref=reference('repair-repro', canonical_bytes(repro)), repro_input_digest=canonical_digest(repro), baseline_receipts=baseline_receipts(state.legacy_passthrough.thaw(), row))
    refs = (reference('repair-attempt', canonical_bytes(body)), body['plan_ref'], body['repro_input_ref'])
    claims = tuple(FreshReviewInputEffectClaim(r['kind'], r['relative_path'], r['digest'], r['size']) for r in refs)
    return BeginFindingRepair(row['lineage_id'], 'begin', freeze_json_value(body), plan, *claims, (evidence,))


def begun_state(pair):
    from mission_kernel.transitions import decide
    state, evidence = pair
    command = begin_command(state, evidence)
    decision = decide(state, command)
    assert decision.accepted, decision.rejection
    return decision.transition.new_state, command


def test_maximum_terminal_fits_reserved_bytes_and_reimport_preserves_attempt(repair_state):
    from mission_kernel.repair_attempts import maximum_attempt, REPAIR_TERMINAL_DELTA, terminal, validate_attempts
    from mission_kernel.repair_lineage import import_origins
    from mission_kernel.state_capacity import repair_attempt_reserve
    state, command = begun_state(repair_state)
    doc = state.legacy_passthrough.thaw()
    assert repair_attempt_reserve(doc) == REPAIR_TERMINAL_DELTA
    assert import_origins(state, command.evidence).repair == state.repair
    maximum = maximum_attempt()
    for status, reason in [('failed', 'replay-failed'), ('blocked', 'publication-result-lost')]:
        row = doc['repair_lineage']['lineages'][0]
        maximum.update(attempt_id=canonical_digest(dict(domain='mission-repair-attempt/1', lineage_id=row['lineage_id'], operation_id=maximum['operation_id'])), status=status)
        maximum['terminal'] = terminal(maximum['attempt_id'], status, reason, 2**63 - 1)
        row.update(attempts=[maximum], lifecycle='open')
        validate_attempts(row)
        assert len(json.dumps(row['attempts'], indent=2).encode()) < REPAIR_TERMINAL_DELTA


@pytest.mark.parametrize('encoding', ['canonical', 'pretty'])
def test_pending_reservation_protects_other_mutations_and_terminal_at_limit(repair_state, encoding):
    from mission_kernel import state_capacity as sc
    from mission_kernel.commands import ReconcileFindingRepair
    from mission_kernel.repair_attempts import terminal_operation
    from mission_kernel.transitions import decide
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    state, _ = begun_state(repair_state)
    doc = state.legacy_passthrough.thaw()
    mode = sc.StateEncoding.CANONICAL if encoding == 'canonical' else sc.StateEncoding.LEGACY_PRETTY
    encode = lambda d: json.dumps(d, ensure_ascii=False, sort_keys=True, **({'separators': (',', ':')} if encoding == 'canonical' else {'indent': 2})).encode()
    reserve = sc.residual_reservation(doc, encoding=mode)
    doc['padding'] = ''
    doc['padding'] = 'x' * (sc.STATE_LIMIT - sc.system_remaining(doc) - reserve - len(encode(doc)) - 32)
    original = encode(doc)
    proposed = dict(doc, padding=doc['padding'] + 'y' * 64)
    with pytest.raises(CapacityWriteError, match='state-capacity-exhausted'):
        check_state_capacity(original, encode(proposed), encoding=mode)
    identifier = doc['repair_lineage']['lineages'][0]['attempts'][0]['attempt_id']
    state = replace(state, legacy_passthrough=freeze_json_value(doc))
    decision = decide(state, ReconcileFindingRepair(identifier, terminal_operation(identifier, 'blocked', 'publication-result-lost')))
    assert decision.accepted, decision.rejection
    terminal_doc = decision.transition.new_state.legacy_passthrough.thaw()
    assert check_state_capacity(original, encode(terminal_doc), encoding=mode).accepted
    assert sc.repair_attempt_reserve(terminal_doc) == 0


def test_terminal_uses_current_fence_and_cannot_grant_verified(repair_state):
    from mission_kernel.commands import ReconcileFindingRepair
    from mission_kernel.repair_attempts import terminal_operation
    from mission_kernel.transitions import decide
    from mission_kernel.repair_lineage import decode_projection
    state, _ = begun_state(repair_state)
    identifier = state.repair.lineages[0].document.thaw()['attempts'][0]['attempt_id']
    epoch = state.lease.fencing_epoch + 1
    state = replace(state, lease=replace(state.lease, fencing_epoch=epoch))
    command = ReconcileFindingRepair(identifier, terminal_operation(identifier, 'blocked', 'publication-result-lost'))
    decision = decide(state, command)
    assert decision.accepted
    row = decision.transition.new_state.repair.lineages[0].document.thaw()
    assert row['attempts'][0]['terminal']['fencing_epoch'] == epoch
    doc = decision.transition.new_state.legacy_passthrough.thaw()
    doc['repair_lineage']['lineages'][0]['lifecycle'] = 'verified'
    with pytest.raises(FreshReviewError):
        decode_projection(doc)
    assert not decide(state, replace(command, reason='passed')).accepted
    assert not decide(state, replace(command, operation_id='forged-terminal')).accepted
    doc['repair_lineage']['lineages'][0]['lifecycle'] = 'open'
    doc['repair_lineage']['lineages'][0]['attempts'][0]['status'] = 'verified'
    with pytest.raises(FreshReviewError):
        decode_projection(doc)


@pytest.mark.parametrize('field,value', [('before_candidate', None), ('baseline_receipts', 'passed'),
    ('before_candidate', dict(contract_digest=None)), ('repro_input_digest', 'sha256:' + 'b' * 64)])
def test_begin_refuses_unbound_history_body(repair_state, field, value):
    from mission_kernel.commands import FreshReviewInputEffectClaim
    from mission_kernel.repair_attempts import reference
    from mission_kernel.transitions import decide
    state, evidence = repair_state
    command = begin_command(state, evidence)
    body = command.history.thaw()
    body[field] = value
    ref = reference('repair-attempt', canonical_bytes(body))
    command = replace(command, history=freeze_json_value(body), history_effect=FreshReviewInputEffectClaim(
        ref['kind'], ref['relative_path'], ref['digest'], ref['size']))
    assert not decide(state, command).accepted


@pytest.mark.parametrize('point,recoverable', [('after-stage', False), ('after-prepare', False),
    ('after-projection:0', False), ('after-head-replace', True)])
def test_crashed_begin_recovers_obligation_through_public_reconcile(replay_reviewer, run_cli, monkeypatch, point, recoverable):
    from functools import partial
    from .test_command_inventory import _load_mission_state_module
    from mission_application.repair import prepare_begin
    from mission_application.evidence import execute_evidence_operation
    from mission_kernel.repair_attempts import attempt_id
    root = replay_reviewer[0]
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    assert import_output(run_cli, replay_reviewer).returncode == 0
    state = json.loads(run_cli('get', cwd=root).stdout)
    lineage = state['repair_lineage']['lineages'][0]['lineage_id']
    monkeypatch.chdir(root)
    monkeypatch.setenv('MISSION_SESSION_ID', 'test')
    monkeypatch.setenv('MISSION_LEASE_ID', 'test-lease')
    services = _load_mission_state_module()._ACCEPTANCE_CONTRACT_CLI_SERVICES
    operation, intent = services.canonical_operation('test', 'repair-begin', {}, caller_operation_id='crash-begin')
    repository = services.repository(root, services.resolve_state_file(root), stamp=True, strict_read=True,
        pre_admit_lease=True, session_id='test', operation_id=operation, operation_command=intent, operation_command_type='repair-begin')
    class Interrupted(Exception):
        pass
    def stop(reached):
        if reached == point:
            raise Interrupted(point)
    repository._repository.fault_injector = stop
    with pytest.raises(Interrupted):
        execute_evidence_operation(repository, partial(prepare_begin, root=root, lineage_id=lineage,
            operation=operation, plan='crash recovery plan', services=services))
    result = run_cli('repair', 'reconcile', '--attempt', attempt_id(lineage, operation), cwd=root)
    if recoverable:
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)['attempt']['status'] == 'blocked'
        assert json.loads(run_cli('get', cwd=root).stdout)['repair_lineage']['lineages'][0]['lifecycle'] == 'open'
    else:
        assert result.returncode != 0
        assert json.loads(run_cli('get', cwd=root).stdout)['repair_lineage']['lineages'][0]['attempts'] == []


@pytest.mark.parametrize('schema', [4, 5])
def test_public_capacity_refuses_begin_without_publishing(replay_reviewer, run_cli, schema):
    from .test_issue879_completion_cli import _rewrite_fixture_document, _public_bytes
    from mission_kernel import state_capacity as sc
    root = replay_reviewer[0]
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    assert import_output(run_cli, replay_reviewer).returncode == 0
    state = json.loads(run_cli('get', cwd=root).stdout)
    if schema == 4:
        _persist_fixture(root, state, schema)
    lineage = state['repair_lineage']['lineages'][0]['lineage_id']
    (root / 'repair-plan.json').write_text('bounded plan')
    def fill(document):
        document['padding'] = ''
        encode = lambda d: json.dumps(d, sort_keys=True, separators=(',', ':')).encode()
        document['padding'] = 'x' * (sc.STATE_LIMIT - sc.system_remaining(document)
            - sc.residual_reservation(document, encoding=sc.StateEncoding.CANONICAL) - len(encode(document)) - 64)
    _rewrite_fixture_document(root, fill)
    before = _public_bytes(root)
    result = run_cli('repair', 'begin', '--finding', lineage, '--plan-ref', 'repair-plan.json', cwd=root)
    assert result.returncode != 0 and 'state-capacity-exhausted' in result.stderr + result.stdout
    assert _public_bytes(root) == before
    assert not list((root / 'evidence' / 'repair-attempt').glob('*.json'))


@pytest.mark.parametrize('point,blocked', [('after-stage', False), ('after-head-replace', True)])
def test_effects_io_failure_never_creates_success(replay_reviewer, run_cli, monkeypatch, point, blocked):
    from .test_command_inventory import _load_mission_state_module
    from mission_application.repair import run_repair_cli
    root = replay_reviewer[0]
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    assert import_output(run_cli, replay_reviewer).returncode == 0
    state = json.loads(run_cli('get', cwd=root).stdout)
    lineage = state['repair_lineage']['lineages'][0]['lineage_id']
    (root / 'repair-plan.json').write_text('repair plan')
    monkeypatch.chdir(root)
    monkeypatch.setenv('MISSION_SESSION_ID', 'test')
    monkeypatch.setenv('MISSION_LEASE_ID', 'test-lease')
    services = _load_mission_state_module()._ACCEPTANCE_CONTRACT_CLI_SERVICES
    def repository(*args, **kwargs):
        result = services.repository(*args, **kwargs)
        if kwargs.get('operation_command_type') == 'repair-begin':
            def stop(reached):
                if reached == point:
                    raise OSError('disk unavailable')
            result._repository.fault_injector = stop
        return result
    args = SimpleNamespace(repair_command='begin', finding=lineage, plan_ref='repair-plan.json')
    failing = replace(services, repository=repository)
    if blocked:
        assert json.loads(run_repair_cli(args, failing))['attempt']['status'] == 'blocked'
    else:
        with pytest.raises(SystemExit):
            run_repair_cli(args, failing)
    row = json.loads(run_cli('get', cwd=root).stdout)['repair_lineage']['lineages'][0]
    assert row['lifecycle'] == 'open'
    assert [a['status'] for a in row['attempts']] == (['blocked'] if blocked else [])


@pytest.mark.parametrize('point', ['after-prepare', 'after-head-replace'])
def test_pending_terminal_crash_survives_takeover(replay_reviewer, run_cli, monkeypatch, point):
    from functools import partial
    from .test_command_inventory import _load_mission_state_module
    from mission_application.repair import prepare_reconcile
    from mission_application.evidence import execute_evidence_operation
    from mission_kernel.repair_attempts import terminal_operation
    root = replay_reviewer[0]
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    assert import_output(run_cli, replay_reviewer).returncode == 0
    state = json.loads(run_cli('get', cwd=root).stdout)
    lineage = state['repair_lineage']['lineages'][0]['lineage_id']
    (root / 'repair-plan.json').write_text('repair plan')
    begun = json.loads(run_cli('repair', 'begin', '--finding', lineage, '--plan-ref', 'repair-plan.json', cwd=root, check=True).stdout)['attempt']
    monkeypatch.chdir(root)
    monkeypatch.setenv('MISSION_SESSION_ID', 'test')
    monkeypatch.setenv('MISSION_LEASE_ID', 'test-lease')
    services = _load_mission_state_module()._ACCEPTANCE_CONTRACT_CLI_SERVICES
    identifier = begun['attempt_id']
    operation = terminal_operation(identifier, 'blocked', 'publication-result-lost')
    _, intent = services.canonical_operation('test', 'repair-reconcile', dict(operation_id=operation), caller_operation_id=operation)
    repository = services.repository(root, services.resolve_state_file(root), stamp=True, strict_read=True,
        pre_admit_lease=True, session_id='test', operation_id=operation, operation_command=intent, operation_command_type='repair-reconcile')
    class Interrupted(Exception):
        pass
    def stop(reached):
        if reached == point:
            raise Interrupted(point)
    repository._repository.fault_injector = stop
    with pytest.raises(Interrupted):
        execute_evidence_operation(repository, partial(prepare_reconcile, identifier=identifier, reason='publication-result-lost'))
    current = json.loads(run_cli('get', cwd=root).stdout)['repair_lineage']['lineages'][0]['attempts'][0]
    assert current['status'] == ('pending' if point == 'after-prepare' else 'blocked')
    result = run_cli('repair', 'reconcile', '--attempt', identifier, cwd=root,
        env_extra={'MISSION_LEASE_ID': 'takeover-lease', 'MISSION_STATE_NOW': '2200-01-01T00:00:00Z'})
    assert result.returncode == 0, result.stdout + result.stderr
    ended = json.loads(result.stdout)['attempt']
    assert ended['status'] == 'blocked'
    assert len(json.loads(run_cli('get', cwd=root).stdout)['repair_lineage']['lineages'][0]['attempts']) == 1
    if point == 'after-prepare':
        assert ended['terminal']['fencing_epoch'] > begun['fencing_epoch']
    else:
        assert ended == current


def test_all_pending_attempts_are_reserved_and_terminal_releases_only_its_share(repair_state):
    import copy
    from mission_kernel.repair_lineage import lineage_id, decode_projection
    from mission_kernel.repair_attempts import attempt_id, REPAIR_TERMINAL_DELTA, terminal
    from mission_kernel.state_capacity import repair_attempt_reserve
    state, _ = begun_state(repair_state)
    doc = state.legacy_passthrough.thaw()
    other = copy.deepcopy(doc['repair_lineage']['lineages'][0])
    other['origin']['finding_id'] = 'finding-2'
    other['lineage_id'] = lineage_id(other['origin'], other['criterion_id'])
    item = other['attempts'][0]
    item['attempt_id'] = attempt_id(other['lineage_id'], item['operation_id'])
    ref = other['finding_ref']
    ref['relative_path'] = ref['relative_path'].replace(ref['digest'][7:], 'b' * 64)
    ref['digest'] = 'sha256:' + 'b' * 64
    other['replay']['evidence_ref'] = ref['digest']
    other['repro']['repro_input_ref'] = ref['digest']
    result = doc['fresh_review']['requests'][0]['result']
    result['findings'].append(copy.deepcopy(ref))
    for row in (doc['repair_lineage']['lineages'][0], other):
        row['terminal_digest'] = canonical_digest(result)
    doc['repair_lineage']['lineages'].append(other)
    decode_projection(doc)
    assert repair_attempt_reserve(doc) == 2 * REPAIR_TERMINAL_DELTA
    item.update(status='blocked', terminal=terminal(item['attempt_id'], 'blocked', 'effects-unavailable', item['fencing_epoch']))
    other['lifecycle'] = 'open'
    assert repair_attempt_reserve(doc) == REPAIR_TERMINAL_DELTA


def test_large_receipt_history_does_not_expand_attempt_body(repair_state):
    from mission_kernel.transitions import decide
    state, evidence = repair_state
    document = state.legacy_passthrough.thaw()
    document['verification_receipts'] *= 1000
    document['verification_receipts'][-1] = dict(document['verification_receipts'][-1], argv=['x' * 262144])
    assert len(canonical_bytes(document['verification_receipts'])) > 262144
    state = replace(state, legacy_passthrough=freeze_json_value(document))
    command = begin_command(state, evidence)
    assert len(canonical_bytes(command.history.thaw())) < 4096
    assert decide(state, command).accepted


@pytest.mark.parametrize('field', ['history_effect', 'plan_effect', 'repro_effect'])
def test_each_history_effect_is_bound_before_state_changes(repair_state, field):
    from mission_kernel.transitions import decide
    state, evidence = repair_state
    command = begin_command(state, evidence)
    forged = replace(getattr(command, field), size=1)
    result = decide(state, replace(command, **{field: forged}))
    assert not result.accepted and result.rejection.code == 'repair-effect-invalid'
    assert result.effects == () and result.transition is None


@pytest.mark.parametrize('final', [False, True])
def test_reconcile_retries_only_before_publication(replay_reviewer, run_cli, monkeypatch, final):
    from .test_command_inventory import _load_mission_state_module
    from mission_application import repair as app
    from mission_persistence.fenced_commit import FencedCommitError, FINAL_AUTHORITY_CAS_CODE
    root = replay_reviewer[0]
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    assert import_output(run_cli, replay_reviewer).returncode == 0
    state = json.loads(run_cli('get', cwd=root).stdout)
    (root / 'repair-plan.json').write_text('repair plan')
    item = json.loads(run_cli('repair', 'begin', '--finding', state['repair_lineage']['lineages'][0]['lineage_id'],
        '--plan-ref', 'repair-plan.json', cwd=root, check=True).stdout)['attempt']
    monkeypatch.chdir(root)
    monkeypatch.setenv('MISSION_SESSION_ID', 'test')
    monkeypatch.setenv('MISSION_LEASE_ID', 'test-lease')
    services = _load_mission_state_module()._ACCEPTANCE_CONTRACT_CLI_SERVICES
    calls = []
    prepare = app.prepare_reconcile
    def observe(state, **kwargs):
        calls.append(state)
        if len(calls) == 1 and not final:
            # A real competing mutation changes the base between read/admit.
            run_cli('set', 'repair_probe=base-moved', cwd=root, check=True)
        return prepare(state, **kwargs)
    monkeypatch.setattr(app, 'prepare_reconcile', observe)
    def repository(*args, **kwargs):
        result = services.repository(*args, **kwargs)
        if final and kwargs.get('operation_id') is not None:
            def stop(point):
                if point == 'before-head-replace':
                    raise FencedCommitError(FINAL_AUTHORITY_CAS_CODE, 'head moved at final authority')
            result._repository.fault_injector = stop
        return result
    args = SimpleNamespace(repair_command='reconcile', attempt=item['attempt_id'])
    if final:
        with pytest.raises(SystemExit):
            app.run_repair_cli(args, replace(services, repository=repository))
        assert len(calls) == 1
        assert json.loads(run_cli('get', cwd=root).stdout)['repair_lineage']['lineages'][0]['attempts'][0]['status'] == 'pending'
        assert json.loads(run_cli('repair', 'reconcile', '--attempt', item['attempt_id'], cwd=root, check=True).stdout)['attempt']['status'] == 'blocked'
    else:
        assert json.loads(app.run_repair_cli(args, services))['attempt']['status'] == 'blocked'
        assert len(calls) == 2
