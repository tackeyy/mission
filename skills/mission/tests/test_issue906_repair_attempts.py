"""Begun repair obligations survive publication loss without granting success."""
import json
import pytest
from mission_kernel.state_capacity import FRESH_REVIEW_DISPATCH_STAGE_DELTA

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
    assert repair_attempt_reserve(doc) == REPAIR_TERMINAL_DELTA + FRESH_REVIEW_DISPATCH_STAGE_DELTA
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


@pytest.mark.parametrize('field,value', [('before_candidate', None), 
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
    assert repair_attempt_reserve(doc) == 2 * (REPAIR_TERMINAL_DELTA + FRESH_REVIEW_DISPATCH_STAGE_DELTA)
    item.update(status='blocked', terminal=terminal(item['attempt_id'], 'blocked', 'effects-unavailable', item['fencing_epoch']))
    other['lifecycle'] = 'open'
    assert repair_attempt_reserve(doc) == REPAIR_TERMINAL_DELTA + FRESH_REVIEW_DISPATCH_STAGE_DELTA


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


@pytest.mark.parametrize('forged', ['reason', 'operation_id'])
def test_terminal_reconcile_validates_intent_before_already_terminal_rejection(repair_state, forged):
    from mission_kernel.commands import ReconcileFindingRepair
    from mission_kernel.repair_attempts import terminal_operation
    from mission_kernel.transitions import decide
    state, _ = begun_state(repair_state)
    identifier = state.repair.lineages[0].document.thaw()['attempts'][0]['attempt_id']
    command = ReconcileFindingRepair(identifier, terminal_operation(identifier, 'blocked', 'publication-result-lost'))
    ended = decide(state, command).transition.new_state
    valid = decide(ended, command)
    assert not valid.accepted and valid.rejection.code == 'repair-attempt-already-terminal'
    assert valid.transition is None and valid.effects == ()
    result = decide(ended, replace(command, **{forged: 'forged'}))
    assert not result.accepted
    assert result.rejection.code == ('repair-attempt-invalid' if forged == 'reason' else 'repair-operation-conflict')
    assert result.transition is None and result.effects == ()


@pytest.mark.parametrize('already_terminal', [False, True])
def test_reconcile_rejects_fence_older_than_begin(repair_state, already_terminal):
    from mission_kernel.commands import ReconcileFindingRepair
    from mission_kernel.repair_attempts import terminal_operation
    from mission_kernel.transitions import decide
    state, _ = begun_state(repair_state)
    item = state.repair.lineages[0].document.thaw()['attempts'][0]
    command = ReconcileFindingRepair(item['attempt_id'], terminal_operation(item['attempt_id'], 'blocked', 'publication-result-lost'))
    if already_terminal:
        state = decide(state, command).transition.new_state
    state = replace(state, lease=replace(state.lease, fencing_epoch=item['fencing_epoch'] - 1))
    result = decide(state, command)
    assert not result.accepted and result.rejection.code == 'repair-lineage-stale-fence'
    assert result.transition is None and result.effects == ()


@pytest.mark.parametrize('status,reason', [('blocked', 'publication-result-lost'), ('failed', 'replay-failed')])
def test_saved_terminal_cannot_precede_begin_fence(repair_state, status, reason):
    from mission_kernel.repair_attempts import terminal
    from mission_kernel.repair_lineage import decode_projection
    state, _ = begun_state(repair_state)
    document = state.legacy_passthrough.thaw()
    row = document['repair_lineage']['lineages'][0]
    item = row['attempts'][0]
    row['lifecycle'] = 'open'
    item.update(status=status, terminal=terminal(item['attempt_id'], status, reason, item['fencing_epoch']))
    decode_projection(document)  # Same epoch is a valid terminal.
    item['terminal']['fencing_epoch'] -= 1
    with pytest.raises(FreshReviewError, match='repair-lineage-shape-invalid'):
        decode_projection(document)


@pytest.mark.parametrize('schema', [4, 5])
def test_retry_history_read_failure_keeps_healthy_pending(replay_reviewer, run_cli, monkeypatch, schema):
    from .test_command_inventory import _load_mission_state_module
    from .test_issue879_completion_cli import _public_bytes
    from mission_application import repair as app
    root = replay_reviewer[0]
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    assert import_output(run_cli, replay_reviewer).returncode == 0
    state = json.loads(run_cli('get', cwd=root).stdout)
    if schema == 4:
        _persist_fixture(root, state, schema)
    lineage = state['repair_lineage']['lineages'][0]['lineage_id']
    (root / 'repair-plan.json').write_text('repair plan')
    arguments = ('repair', 'begin', '--finding', lineage, '--plan-ref', 'repair-plan.json')
    env = {'MISSION_OPERATION_ID': 'retry-read'}
    item = json.loads(run_cli(*arguments, cwd=root, env_extra=env, check=True).stdout)['attempt']
    before = _public_bytes(root)
    monkeypatch.chdir(root)
    for key, value in dict(env, MISSION_SESSION_ID='test', MISSION_LEASE_ID='test-lease').items():
        monkeypatch.setenv(key, value)
    services = _load_mission_state_module()._ACCEPTANCE_CONTRACT_CLI_SERVICES
    original = app._read
    def unavailable(root, reference, **kwargs):
        if reference.relative_path == item['history_ref']['relative_path']:
            raise OSError('temporary history read failure')
        return original(root, reference, **kwargs)
    monkeypatch.setattr(app, '_read', unavailable)
    args = SimpleNamespace(repair_command='begin', finding=lineage, plan_ref='repair-plan.json')
    with pytest.raises(SystemExit):
        app.run_repair_cli(args, services)
    assert _public_bytes(root) == before
    assert (root / item['history_ref']['relative_path']).is_file()
    retry = run_cli(*arguments, cwd=root, env_extra=env, check=True)
    assert json.loads(retry.stdout)['attempt'] == item
    ended = run_cli('repair', 'reconcile', '--attempt', item['attempt_id'], cwd=root, check=True)
    assert json.loads(ended.stdout)['attempt']['status'] == 'blocked'


@pytest.mark.parametrize('drift', [False, True])
def test_public_begin_uses_application_capture_and_refuses_drift(replay_reviewer, run_cli, monkeypatch, drift):
    from .test_command_inventory import _load_mission_state_module
    from .test_issue879_completion_cli import _public_bytes
    from mission_application import repair as app
    from acceptance_contract import frozen_verifier_commands
    root = replay_reviewer[0]
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    assert import_output(run_cli, replay_reviewer).returncode == 0
    state = json.loads(run_cli('get', cwd=root).stdout)
    row = state['repair_lineage']['lineages'][0]
    (root / 'app.txt').write_text('new repair candidate')
    (root / 'repair-plan.json').write_text('repair plan')
    before = _public_bytes(root)
    rejected = run_cli('repair', 'begin', '--finding', row['lineage_id'], '--plan-ref', 'repair-plan.json',
        '--before-candidate', json.dumps(row['introduced_candidate']), cwd=root)
    assert rejected.returncode != 0 and _public_bytes(root) == before
    monkeypatch.chdir(root)
    monkeypatch.setenv('MISSION_SESSION_ID', 'test')
    monkeypatch.setenv('MISSION_LEASE_ID', 'test-lease')
    services = _load_mission_state_module()._ACCEPTANCE_CONTRACT_CLI_SERVICES
    commands = frozen_verifier_commands(state['acceptance_contract'])
    selected = {key: commands[key] for key in row['introduced_candidate']['snapshots']}
    observations = []
    original = app._capture
    def capture(root, commands):
        assert commands == selected
        snapshots = original(root, commands)
        if drift and observations:
            snapshots = {key: SimpleNamespace(digest='sha256:' + 'f' * 64) for key in snapshots}
        observations.append({key: snapshot.digest for key, snapshot in snapshots.items()})
        return snapshots
    monkeypatch.setattr(app, '_capture', capture)
    args = SimpleNamespace(repair_command='begin', finding=row['lineage_id'], plan_ref='repair-plan.json')
    if drift:
        with pytest.raises(SystemExit):
            app.run_repair_cli(args, services)
        assert observations and _public_bytes(root) == before
        assert not list((root / 'evidence/repair-attempt').glob('*.json'))
    else:
        item = json.loads(app.run_repair_cli(args, services))['attempt']
        body = json.loads((root / item['history_ref']['relative_path']).read_text())
        assert observations and body['before_candidate']['snapshots'] == observations[-1]
        assert body['before_candidate']['snapshots'] != row['introduced_candidate']['snapshots']



def test_peer_begin_before_admission_is_not_blocked_by_retry_read_failure(replay_reviewer, run_cli, monkeypatch):
    from .test_command_inventory import _load_mission_state_module
    from .test_issue879_completion_cli import _public_bytes
    from mission_application import repair as app
    root = replay_reviewer[0]
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    assert import_output(run_cli, replay_reviewer).returncode == 0
    row = json.loads(run_cli('get', cwd=root).stdout)['repair_lineage']['lineages'][0]
    (root / 'repair-plan.json').write_text('repair plan')
    arguments = ('repair', 'begin', '--finding', row['lineage_id'], '--plan-ref', 'repair-plan.json')
    env = {'MISSION_OPERATION_ID': 'peer-begin'}
    monkeypatch.chdir(root)
    for key, value in dict(env, MISSION_SESSION_ID='test', MISSION_LEASE_ID='test-lease').items():
        monkeypatch.setenv(key, value)
    services = _load_mission_state_module()._ACCEPTANCE_CONTRACT_CLI_SERVICES
    peer = {}
    def repository(*args, **kwargs):
        if kwargs.get('operation_command_type') == 'repair-begin' and not peer:
            peer['attempt'] = json.loads(run_cli(*arguments, cwd=root, env_extra=env, check=True).stdout)['attempt']
            peer['bytes'] = _public_bytes(root)
        return services.repository(*args, **kwargs)
    original = app._read
    def unavailable(root, reference, **kwargs):
        if reference.kind == 'repair-attempt':
            raise OSError('temporary history read failure')
        return original(root, reference, **kwargs)
    monkeypatch.setattr(app, '_read', unavailable)
    args = SimpleNamespace(repair_command='begin', finding=row['lineage_id'], plan_ref='repair-plan.json')
    with pytest.raises(SystemExit):
        app.run_repair_cli(args, replace(services, repository=repository))
    assert peer and _public_bytes(root) == peer['bytes']
    assert json.loads(run_cli(*arguments, cwd=root, env_extra=env, check=True).stdout)['attempt'] == peer['attempt']


@pytest.mark.parametrize('schema', [4, 5])
@pytest.mark.parametrize('kind', ['begin', 'reconcile'])
def test_competing_repair_writer_does_not_publish_a_second_generation(replay_reviewer, run_cli, monkeypatch, capsys, schema, kind):
    from functools import partial
    from .test_command_inventory import _load_mission_state_module
    from .test_issue879_completion_cli import _public_bytes
    from mission_application import repair as app
    from mission_application.evidence import execute_evidence_operation
    from mission_kernel.repair_attempts import terminal_operation
    root = replay_reviewer[0]
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    assert import_output(run_cli, replay_reviewer).returncode == 0
    state = json.loads(run_cli('get', cwd=root).stdout)
    if schema == 4:
        _persist_fixture(root, state, schema)
    lineage = state['repair_lineage']['lineages'][0]['lineage_id']
    (root / 'repair-plan.json').write_text('repair plan')
    begin = ('repair', 'begin', '--finding', lineage, '--plan-ref', 'repair-plan.json')
    env = {'MISSION_OPERATION_ID': 'competing-begin'}
    if kind == 'reconcile':
        item = json.loads(run_cli(*begin, cwd=root, env_extra=env, check=True).stdout)['attempt']
    monkeypatch.chdir(root)
    for key, value in dict(env, MISSION_SESSION_ID='test', MISSION_LEASE_ID='test-lease').items():
        monkeypatch.setenv(key, value)
    services = _load_mission_state_module()._ACCEPTANCE_CONTRACT_CLI_SERVICES
    peer = {}
    def repository(*args, **kwargs):
        if kwargs.get('operation_command_type') == 'repair-' + kind and kwargs.get('operation_id') and not peer:
            if kind == 'begin':
                peer['attempt'] = json.loads(run_cli(*begin, cwd=root, env_extra=env, check=True).stdout)['attempt']
            else:
                operation = terminal_operation(item['attempt_id'], 'blocked', 'effects-unavailable')
                _, intent = services.canonical_operation('test', 'repair-reconcile', dict(operation_id=operation), caller_operation_id=operation)
                writer = services.repository(root, services.resolve_state_file(root), stamp=True, strict_read=True, pre_admit_lease=True,
                    session_id='test', operation_id=operation, operation_command=intent, operation_command_type='repair-reconcile')
                execute_evidence_operation(writer, partial(app.prepare_reconcile, identifier=item['attempt_id'], reason='effects-unavailable'))
                peer['attempt'] = json.loads(run_cli('get', cwd=root).stdout)['repair_lineage']['lineages'][0]['attempts'][0]
            peer['bytes'] = _public_bytes(root)
        return services.repository(*args, **kwargs)
    args = (SimpleNamespace(repair_command='begin', finding=lineage, plan_ref='repair-plan.json') if kind == 'begin'
            else SimpleNamespace(repair_command='reconcile', attempt=item['attempt_id']))
    if kind == 'begin' and schema == 5:  # Strict same-operation replay bypasses the reducer.
        assert json.loads(app.run_repair_cli(args, replace(services, repository=repository)))['attempt'] == peer['attempt']
    else:
        with pytest.raises(SystemExit):
            app.run_repair_cli(args, replace(services, repository=repository))
        output = capsys.readouterr()
        assert ('repair-attempt-already-terminal' if kind == 'reconcile' else 'repair-attempt-already-started') in output.out + output.err
    assert peer and _public_bytes(root) == peer['bytes']  # Includes heads, generations, commits and operation records.
    assert json.loads(run_cli(*begin, cwd=root, env_extra=env, check=True).stdout)['attempt'] == peer['attempt']
    assert _public_bytes(root) == peer['bytes']


@pytest.mark.parametrize('comparison,code', [('contract', 'repair-contract-stale'), ('iteration', 'repair-contract-stale'),
    ('baseline', 'repair-attempt-invalid'), ('snapshot-keys', 'repair-attempt-invalid'),
    ('plan-content', 'repair-effect-invalid'), ('repro-content', 'repair-effect-invalid')])
def test_closed_history_reaches_each_binding_comparison(repair_state, comparison, code):
    from mission_kernel.commands import FreshReviewInputEffectClaim
    from mission_kernel.repair_attempts import reference
    from mission_kernel.transitions import decide
    state, evidence = repair_state
    command = begin_command(state, evidence)
    assert decide(state, command).accepted
    body = command.history.thaw()
    if comparison == 'contract':
        body['before_candidate']['contract_digest'] = 'sha256:' + 'b' * 64
    elif comparison == 'iteration':
        body['before_candidate']['iteration'] += 1
    elif comparison == 'baseline':
        body['baseline_receipts'][0]['receipt_digest'] = 'sha256:' + 'b' * 64
    elif comparison == 'snapshot-keys':
        body['before_candidate']['snapshots'].pop(next(iter(body['before_candidate']['snapshots'])))
    else:
        kind = 'repair-plan' if comparison == 'plan-content' else 'repair-repro'
        field = 'plan_ref' if comparison == 'plan-content' else 'repro_input_ref'
        effect = 'plan_effect' if comparison == 'plan-content' else 'repro_effect'
        body[field] = reference(kind, b'z' * body[field]['size'])
        ref = body[field]
        command = replace(command, **{effect: FreshReviewInputEffectClaim(ref['kind'], ref['relative_path'], ref['digest'], ref['size'])})
    ref = reference('repair-attempt', canonical_bytes(body))
    command = replace(command, history=freeze_json_value(body), history_effect=FreshReviewInputEffectClaim(
        ref['kind'], ref['relative_path'], ref['digest'], ref['size']))
    result = decide(state, command)
    assert not result.accepted and result.rejection.code == code
    assert result.transition is None and result.effects == ()


def test_existing_begin_checks_history_conflict_before_duplicate_rejection(repair_state):
    from mission_kernel.transitions import decide
    state, command = begun_state(repair_state)
    assert decide(state, command).rejection.code == 'repair-attempt-already-started'
    body = command.history.thaw()
    body['before_candidate']['iteration'] += 1
    result = decide(state, replace(command, history=freeze_json_value(body)))
    assert not result.accepted and result.rejection.code == 'repair-operation-conflict'
    assert result.transition is None and result.effects == ()


@pytest.mark.parametrize('schema', [4, 5])
def test_undecodable_pending_reservation_blocks_state_writes(repair_state, schema):
    from mission_kernel import state_capacity as sc
    from mission_kernel.repair_attempts import REPAIR_TERMINAL_DELTA
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    state, _ = begun_state(repair_state)
    document = state.legacy_passthrough.thaw()
    surface = document if schema == 4 else dict(schema_version=5, extensions=document, lease={}, control={})
    assert sc.repair_attempt_reserve(surface) == REPAIR_TERMINAL_DELTA + FRESH_REVIEW_DISPATCH_STAGE_DELTA
    check_state_capacity(None, canonical_bytes(surface), encoding=sc.StateEncoding.CANONICAL)
    document['repair_lineage']['lineages'][0]['attempts'][0]['status'] = 'unknown'
    assert sc.repair_attempt_reserve(surface) == sc.STATE_LIMIT
    with pytest.raises(CapacityWriteError, match='state-capacity-exhausted'):
        check_state_capacity(None, canonical_bytes(surface), encoding=sc.StateEncoding.CANONICAL)


@pytest.mark.parametrize('schema', [4, 5])
def test_same_begin_operation_cannot_change_only_its_plan(replay_reviewer, run_cli, schema):
    from .test_issue879_completion_cli import _public_bytes
    root = replay_reviewer[0]
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    assert import_output(run_cli, replay_reviewer).returncode == 0
    state = json.loads(run_cli('get', cwd=root).stdout)
    if schema == 4:
        _persist_fixture(root, state, schema)
    (root / 'repair-plan.json').write_text('original plan')
    args = ('repair', 'begin', '--finding', state['repair_lineage']['lineages'][0]['lineage_id'], '--plan-ref', 'repair-plan.json')
    env = {'MISSION_OPERATION_ID': 'same-plan-operation'}
    run_cli(*args, cwd=root, env_extra=env, check=True)
    before = _public_bytes(root)
    (root / 'repair-plan.json').write_text('changed plan')
    result = run_cli(*args, cwd=root, env_extra=env)
    assert result.returncode != 0 and 'repair-operation-conflict' in result.stdout + result.stderr
    assert _public_bytes(root) == before
