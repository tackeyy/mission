"""Same-counterexample repair requires a new, observed candidate."""
import hashlib
import json
from dataclasses import replace
import pytest
from .test_issue879_completion_cli import _persist_fixture
from .test_issue912_fresh_review_dispatch import completion_session, reviewer, invoke
from .test_issue896_publish import import_output
from .test_issue906_repair_attempts import repair_state, published, gate_state, completed_carrier

@pytest.fixture
def repair_reviewer(completion_session, run_cli, tmp_path):
    from .test_issue878_verification_runner import _replay_policy
    root, state, schema = completion_session
    policy = _replay_policy()
    policy['commands'][1]['argv'][-1] = f"from pathlib import Path; import time; print(time.time_ns()); assert Path('repro.json').read_text() == '0'; assert Path('app.txt').read_text() == 'repaired' or Path({str(root / '.mission/replay-passed')!r}).exists()"
    raw = json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()
    (root / '.mission/verifiers.json').write_bytes(raw)
    digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
    state['acceptance_contract'].update(verifier_policy_digest=digest, verifier_policy=dict(digest=digest, commands={c['id']:c for c in policy['commands']}))
    return reviewer.__wrapped__((root, state, schema), run_cli, tmp_path)


def begin(run_cli, fixture, schema=5):
    root = fixture[0]
    invoke(run_cli, fixture, FIXTURE_REVIEW_MODE='counterexample')
    imported = import_output(run_cli, fixture)
    assert imported.returncode == 0, imported.stdout + imported.stderr
    state = json.loads(run_cli('get', cwd=root).stdout)
    if schema == 4:
        _persist_fixture(root, state, schema)
    row = state['repair_lineage']['lineages'][0]
    (root / 'plan.json').write_text('repair the observed counterexample')
    result = run_cli('repair', 'begin', '--finding', row['lineage_id'], '--plan-ref', 'plan.json', cwd=root, check=True)
    return row, json.loads(result.stdout)['attempt']

@pytest.mark.parametrize('schema', [4, 5])
def test_public_same_counterexample_failed_new_candidate_passed(repair_reviewer, run_cli, schema):
    root = repair_reviewer[0]
    row, attempt = begin(run_cli, repair_reviewer, schema)
    (root / 'app.txt').write_text('repaired')
    result = run_cli('repair', 'reverify', '--attempt', attempt['attempt_id'], cwd=root)
    assert result.returncode == 0, result.stdout + result.stderr
    ended = json.loads(result.stdout)['attempt']
    assert ended['status'] == 'verified'
    state = json.loads(run_cli('get', cwd=root).stdout)
    assert state['repair_lineage']['lineages'][0]['lifecycle'] == 'verified'
    assert not state.get('verification_receipts')  # Replay never substitutes normal verification.
    again = run_cli('repair', 'reverify', '--attempt', attempt['attempt_id'], cwd=root, check=True)
    assert json.loads(again.stdout)['attempt'] == ended
    refused = run_cli('repair', 'begin', '--finding', row['lineage_id'], '--plan-ref', 'plan.json', cwd=root,
        env_extra={'MISSION_OPERATION_ID': 'begin-verified'})
    assert refused.returncode != 0 and 'repair-lineage-already-verified' in refused.stderr
    assert json.loads(run_cli('get', cwd=root).stdout)['repair_lineage']['lineages'][0]['lifecycle'] == 'verified'
    (root / 'app.txt').write_text('changed-again')
    reopened = run_cli('repair', 'begin', '--finding', row['lineage_id'], '--plan-ref', 'plan.json', cwd=root,
        env_extra={'MISSION_OPERATION_ID': 'begin-stale'}, check=True)
    assert json.loads(reopened.stdout)['attempt']['status'] == 'pending'
    changed = json.loads(run_cli('get', cwd=root).stdout)['repair_lineage']['lineages'][0]
    assert changed['last_candidate_change']['generation'] > ended['terminal']['generation']

@pytest.fixture
def reverified(repair_state):
    from mission_kernel.commands import BeginFindingReverification, CommitFindingReverification, FreshReviewInputEffectClaim
    from mission_kernel.repair_attempts import reference
    from mission_kernel.repair_reverification import next_generation
    from mission_kernel.fresh_review import canonical_bytes, canonical_digest
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import decide
    from .test_issue906_repair_attempts import begun_state
    state, begun = begun_state(repair_state)
    row = state.repair.lineages[0].document.thaw(); item = row['attempts'][0]
    candidates = dict(row['introduced_candidate']['snapshots']); candidates[row['repro']['command_id']] = 'sha256:' + 'b'*64
    dispatched = decide(state, BeginFindingReverification(item['attempt_id'], 'dispatch', freeze_json_value(candidates), 'reservation', 'repair',
        started_at='2026-01-01T00:00:00Z', deadline_at='2026-01-01T00:00:05Z'))
    assert dispatched.accepted, dispatched.rejection
    state = dispatched.transition.new_state; row = state.repair.lineages[0].document.thaw(); item = row['attempts'][0]
    finding = begun.evidence[0].findings[0].thaw()
    receipt = dict(finding['replay'], candidate_digest=candidates[row['repro']['command_id']], status='passed', exit_code=0, executed_count=1, started_at='2026-01-01T00:00:00Z', finished_at='2026-01-01T00:00:01Z')
    body = dict(schema='mission-repair-reverification/1', lineage_id=row['lineage_id'], attempt_id=item['attempt_id'],
        criterion_id=row['criterion_id'], command_id=row['repro']['command_id'], repro_input_digest=row['repro']['repro_input_digest'],
        candidate=candidates, receipt=receipt, receipt_ref=reference('repair-replay', canonical_bytes(receipt)),
        dispatch=item['intent'], generation=next_generation(state.legacy_passthrough.thaw()))
    def claim(ref):
        return FreshReviewInputEffectClaim(ref['kind'], ref['relative_path'], ref['digest'], ref['size'])
    ref = reference('repair-reverification', canonical_bytes(body))
    command = CommitFindingReverification(item['attempt_id'], 'repair-result:' + ref['digest'][7:], freeze_json_value(body), claim(ref), claim(body['receipt_ref']), begun.evidence)
    decision = decide(state, command)
    assert decision.accepted, decision.rejection
    return state, command, decision.transition.new_state


def test_verified_lineage_leaves_completion_blockers_only_with_current_evidence(reverified):
    from mission_kernel.fresh_review_coverage import FreshReviewBindings
    from mission_kernel.repair_lineage import effective_unresolved_findings
    state, command, verified = reverified
    body = command.result.thaw(); contract = state.legacy_passthrough.thaw()['acceptance_contract']
    bindings = FreshReviewBindings(body['receipt']['contract_digest'], (), tuple(body['candidate'].items()), (command.result,))
    assert effective_unresolved_findings(verified.repair, verified.fresh_review, command.evidence, contract, bindings) == ()
    assert effective_unresolved_findings(verified.repair, verified.fresh_review, command.evidence, contract)
    stale = replace(bindings, candidate_snapshots=tuple((key, 'sha256:'+'c'*64) for key in body['candidate']))
    assert effective_unresolved_findings(verified.repair, verified.fresh_review, command.evidence, contract, stale)


def test_first_repair_invalidates_normal_receipts_even_if_only_replay_snapshot_changed(reverified):
    state, command, _ = reverified
    row = state.repair.lineages[0].document.thaw(); body = command.result.thaw()
    assert any(body['candidate'][key] == digest for key, digest in row['introduced_candidate']['snapshots'].items())
    assert row['last_candidate_change'].get('generation', 0) > 0
    assert row['last_candidate_change']['generation'] < body['generation']

@pytest.mark.parametrize('schema', [4, 5])
@pytest.mark.parametrize('claim', ['repro', 'old-candidate', 'self-report'])
def test_public_reverify_cannot_replace_saved_input_candidate_or_claim(repair_reviewer, run_cli, schema, claim):
    from .test_issue879_completion_cli import _public_bytes
    root = repair_reviewer[0]; row, item = begin(run_cli, repair_reviewer, schema)
    (root / 'app.txt').write_text('repaired')
    source = root / 'other.json'; source.write_text('{"artifact_kind":"counterexample","content":"proof"}')
    extra = {'repro': ['--repro-input', str(source)], 'old-candidate': ['--candidate', row['introduced_candidate']['snapshots'][row['repro']['command_id']]],
        'self-report': ['--status', 'verified']}[claim]
    before = _public_bytes(root)
    refused = run_cli('repair', 'reverify', '--attempt', item['attempt_id'], *extra, cwd=root)
    assert refused.returncode != 0 and _public_bytes(root) == before


@pytest.mark.parametrize('schema', [4, 5])
@pytest.mark.parametrize('returned', ['introduced', 'previous-attempt'])
def test_public_passed_replay_on_any_earlier_failed_candidate_is_not_verified(repair_reviewer, run_cli, schema, returned):
    root = repair_reviewer[0]; original = (root / 'app.txt').read_text()
    row, item = begin(run_cli, repair_reviewer, schema)
    target = original if returned == 'introduced' else 'failed-before'
    for number, contents in enumerate((['failed-later'] if returned == 'introduced' else ['failed-before', 'failed-later'])):
        (root / 'app.txt').write_text(contents)
        failed = run_cli('repair', 'reverify', '--attempt', item['attempt_id'], cwd=root, check=True)
        assert json.loads(failed.stdout)['attempt']['status'] == 'failed'
        item = json.loads(run_cli('repair', 'begin', '--finding', row['lineage_id'], '--plan-ref', 'plan.json', cwd=root,
            env_extra={'MISSION_OPERATION_ID': 'return-failed-' + str(number)}, check=True).stdout)['attempt']
    (root / 'app.txt').write_text(target)
    # An opaque fixture verifier changes its answer, without changing its snapshot.
    (root / '.mission/replay-passed').write_text('passed')
    result = run_cli('repair', 'reverify', '--attempt', item['attempt_id'], cwd=root)
    assert result.returncode != 0 and 'repair-candidate-not-new' in result.stderr
    ended = json.loads(run_cli('get', cwd=root).stdout)['repair_lineage']['lineages'][0]
    assert ended['lifecycle'] == 'open' and ended['attempts'][-1]['status'] == 'blocked'


def test_committed_candidate_change_remains_stale_after_bytes_return(reverified):
    from mission_kernel.commands import BeginFindingReverification, RecordVerificationReceipt
    from mission_kernel.transitions import decide
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.repair_lineage import effective_unresolved_findings
    from mission_kernel.fresh_review_coverage import FreshReviewBindings
    state, command, verified = reverified
    body = command.result.thaw(); contract = state.legacy_passthrough.thaw()['acceptance_contract']
    receipt = dict(body['receipt'], candidate_digest='sha256:'+'c'*64)
    observed = decide(verified, RecordVerificationReceipt(receipt['finished_at'], freeze_json_value(receipt)))
    assert observed.accepted, observed.rejection
    current = observed.transition.new_state
    marker = current.repair.lineages[0].document.thaw()['last_candidate_change']
    assert marker['generation'] >= body['generation']
    assert current.legacy_passthrough.thaw()['verification_receipts'][-1]['repair_generation'] > marker['generation']
    bindings = FreshReviewBindings(body['receipt']['contract_digest'], (), tuple(body['candidate'].items()), (command.result,))
    assert effective_unresolved_findings(current.repair, current.fresh_review, command.evidence, contract, bindings)

@pytest.mark.parametrize('encoding', ['canonical', 'pretty'])
@pytest.mark.parametrize('outcome', ['verified', 'failed', 'blocked'])
def test_reserved_intent_and_terminal_fit_near_limit(reverified, encoding, outcome):
    from mission_kernel import state_capacity as sc
    from mission_kernel.transitions import decide
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.fresh_review import canonical_bytes
    from mission_kernel.commands import ReconcileFindingRepair, FreshReviewInputEffectClaim
    from mission_kernel.repair_attempts import terminal_operation, reference, REPAIR_TERMINAL_DELTA
    from mission_persistence.capacity_gate import check_state_capacity
    state, command, verified = reverified
    doc = state.legacy_passthrough.thaw()
    doc['repair_lineage']['lineages'][0]['attempts'][-1].pop('intent')
    doc['repair_lineage']['lineages'][0]['last_candidate_change'] = dict(kind='absent', reason='no-resolution-binding')
    mode = sc.StateEncoding.CANONICAL if encoding == 'canonical' else sc.StateEncoding.LEGACY_PRETTY
    def encode(document):
        return json.dumps(document, ensure_ascii=False, sort_keys=True, **({'separators':(',',':')} if encoding == 'canonical' else {'indent':2})).encode()
    doc['padding'] = ''
    doc['padding'] = 'x' * (sc.STATE_LIMIT - sc.system_remaining(doc) - sc.residual_reservation(doc, encoding=mode) - len(encode(doc)) - 16)
    from mission_kernel.repair_attempts import _store
    from mission_kernel.commands import BeginFindingReverification
    before = encode(doc); state = _store(state, doc)
    intent = command.result.thaw()['dispatch']
    staged = decide(state, BeginFindingReverification(command.attempt_id, intent['operation_id'],
        freeze_json_value(command.result.thaw()['candidate']), intent['reservation_id'], intent['budget_class'],
        started_at=intent['started_at'], deadline_at=intent['deadline_at']))
    assert staged.accepted, staged.rejection
    state = staged.transition.new_state; after_intent = encode(state.legacy_passthrough.thaw())
    assert check_state_capacity(before, after_intent, encoding=mode).accepted
    before = after_intent
    if outcome == 'failed':
        body = command.result.thaw(); body['receipt'].update(status='failed', exit_code=1)
        body['receipt_ref'] = reference('repair-replay', canonical_bytes(body['receipt']))
        ref = reference('repair-reverification', canonical_bytes(body))
        claim = lambda r: FreshReviewInputEffectClaim(r['kind'], r['relative_path'], r['digest'], r['size'])
        command = replace(command, operation_id='repair-result:'+ref['digest'][7:], result=freeze_json_value(body), effect=claim(ref), receipt_effect=claim(body['receipt_ref']))
    elif outcome == 'blocked':
        command = ReconcileFindingRepair(command.attempt_id, terminal_operation(command.attempt_id, 'blocked', 'publication-result-lost'))
    decided = decide(state, command)
    assert decided.accepted, decided.rejection
    after = decided.transition.new_state.legacy_passthrough.thaw()
    assert len(encode(after)) - len(before) <= REPAIR_TERMINAL_DELTA
    assert check_state_capacity(before, encode(after), encoding=mode).accepted
    assert sc.repair_attempt_reserve(after) == 0


def test_reverify_intent_binds_an_actual_budget_reservation(repair_reviewer, run_cli):
    from datetime import datetime, timezone
    from mission_kernel.budget import decode_policy, default_policy_document, ledger_document, new_ledger, decode_ledger
    root = repair_reviewer[0]; _, item = begin(run_cli, repair_reviewer)
    state = json.loads(run_cli('get', cwd=root).stdout)
    state['budget_minutes'] = 30
    state['budget_ledger'] = ledger_document(new_ledger(decode_policy(default_policy_document(1800)), datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')))
    _persist_fixture(root, state, 5, operation_id="fixture-budget")
    (root / 'app.txt').write_text('repaired')
    result = run_cli('repair', 'reverify', '--attempt', item['attempt_id'], cwd=root)
    assert result.returncode == 0, result.stdout + result.stderr
    ended = json.loads(result.stdout)['attempt']
    state = json.loads(run_cli('get', cwd=root).stdout); ledger = decode_ledger(state)
    assert ended['status'] == 'verified'
    assert ended['intent']['budget_class'] == 'repair'
    assert any(p.entry == 'repair-reverify' and p.target == state['repair_lineage']['lineages'][0]['lineage_id'] for p in ledger.progress)
    assert not ledger.reservations
    assert any(s.reservation_id == ended['intent']['reservation_id'] for s in ledger.settlements)


def test_no_progress_counts_the_same_lineage_candidate_across_attempts(repair_reviewer, run_cli):
    from datetime import datetime, timezone
    from mission_kernel.budget import decode_policy, default_policy_document, ledger_document, new_ledger, decode_ledger
    root = repair_reviewer[0]; row, item = begin(run_cli, repair_reviewer)
    state = json.loads(run_cli('get', cwd=root).stdout)
    policy = default_policy_document(1800); policy['no_progress_limit'] = 2
    state['budget_minutes'] = 30
    state['budget_ledger'] = ledger_document(new_ledger(decode_policy(policy), datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')))
    _persist_fixture(root, state, 5, operation_id='fixture-budget')
    for number in range(2):
        if number:
            item = json.loads(run_cli('repair', 'begin', '--finding', row['lineage_id'], '--plan-ref', 'plan.json', cwd=root,
                env_extra={'MISSION_OPERATION_ID': 'failure-2'}, check=True).stdout)['attempt']
        replay = run_cli('repair', 'reverify', '--attempt', item['attempt_id'], cwd=root, check=True)
        assert json.loads(replay.stdout)['attempt']['status'] == 'failed'
    ledger = decode_ledger(json.loads(run_cli('get', cwd=root).stdout))
    assert any(p.target == row['lineage_id'] and p.consecutive_count == 2 for p in ledger.progress)
    item = json.loads(run_cli('repair', 'begin', '--finding', row['lineage_id'], '--plan-ref', 'plan.json', cwd=root,
        env_extra={'MISSION_OPERATION_ID': 'failure-3'}, check=True).stdout)['attempt']
    refused = run_cli('repair', 'reverify', '--attempt', item['attempt_id'], cwd=root)
    assert refused.returncode != 0 and 'budget-no-new-evidence' in refused.stderr


@pytest.mark.parametrize('recovery', ['live', 'expired', 'legacy', 'cleanup'])
def test_replayed_dispatch_never_spawns_again(repair_reviewer, run_cli, monkeypatch, recovery):
    from .test_command_inventory import _load_mission_state_module
    from mission_application import repair_reverification as app
    from mission_application.repair import run_repair_cli
    from types import SimpleNamespace
    root = repair_reviewer[0]; _, item = begin(run_cli, repair_reviewer)
    (root / 'app.txt').write_text('repaired')
    if recovery == 'cleanup':
        from datetime import datetime, timezone
        from mission_kernel.budget import decode_policy, default_policy_document, ledger_document, new_ledger
        state = json.loads(run_cli('get', cwd=root).stdout); state['budget_minutes'] = 30
        state['budget_ledger'] = ledger_document(new_ledger(decode_policy(default_policy_document(1800)), datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')))
        _persist_fixture(root, state, 5, operation_id='fixture-budget')
    monkeypatch.chdir(root); monkeypatch.setenv('MISSION_SESSION_ID', 'test'); monkeypatch.setenv('MISSION_LEASE_ID', 'test-lease')
    original = app.execute_evidence_operation
    def peer_dispatch(repository, prepare):
        result = original(repository, prepare)
        return dict(result, dispatch_replayed=True)
    def forbidden(*args, **kwargs):
        pytest.fail('replayed durable dispatch spawned the verifier again')
    monkeypatch.setattr(app, 'execute_evidence_operation', peer_dispatch)
    monkeypatch.setattr(app, 'run_contract_verifier', forbidden)
    monkeypatch.setattr(app, 'execute_verification', forbidden)
    args = SimpleNamespace(repair_command='reverify', attempt=item['attempt_id'])
    services = _load_mission_state_module()._ACCEPTANCE_CONTRACT_CLI_SERVICES
    result = json.loads(run_repair_cli(args, services))
    assert result['attempt']['status'] == 'pending'
    monkeypatch.setattr(app, 'execute_evidence_operation', original)
    if recovery in ('expired', 'cleanup'):
        from datetime import timedelta
        from mission_kernel.budget import _at
        late = (_at(result['attempt']['intent']['deadline_at']) + timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        services = replace(services, now=lambda: late)
        if recovery == 'cleanup':
            state = json.loads(run_cli('get', cwd=root).stdout)
            settled_by = state['budget_ledger']['reservations'][0]['settle_by']
            assert _at(late) < _at(settled_by)
            with pytest.raises(SystemExit):
                run_repair_cli(args, services)
            assert json.loads(run_cli('get', cwd=root).stdout)['repair_lineage']['lineages'][0]['attempts'][-1]['status'] == 'pending'
            late = (_at(settled_by) + timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        ended = json.loads(run_repair_cli(args, services))['attempt']
        assert ended['status'] == 'blocked' and ended['terminal']['reason'] == 'publication-result-lost'
    else:
        if recovery == 'legacy':
            state = json.loads(run_cli('get', cwd=root).stdout)
            intent = state['repair_lineage']['lineages'][0]['attempts'][-1]['intent']
            intent.pop('started_at'); intent.pop('deadline_at')
            _persist_fixture(root, state, 5, operation_id='legacy-intent')
        with pytest.raises(SystemExit):
            run_repair_cli(args, services)
        assert json.loads(run_cli('get', cwd=root).stdout)['repair_lineage']['lineages'][0]['attempts'][-1]['status'] == 'pending'


def test_same_candidate_pass_cannot_verify(reverified):
    from mission_kernel.repair_reverification import validate_result
    from mission_kernel.fresh_review import canonical_digest, canonical_bytes, FreshReviewError
    from mission_kernel.repair_attempts import reference
    state, command, _ = reverified
    row = state.repair.lineages[0].document.thaw(); item = row['attempts'][-1]
    finding = command.evidence[0].findings[0].thaw(); body = command.result.thaw()
    body['candidate'][row['repro']['command_id']] = finding['replay']['candidate_digest']
    body['receipt']['candidate_digest'] = finding['replay']['candidate_digest']
    item['intent'].update(snapshot_digest=finding['replay']['candidate_digest'], candidate_map_digest=canonical_digest(body['candidate']))
    body['dispatch'] = item['intent']; body['receipt_ref'] = reference('repair-replay', canonical_bytes(body['receipt']))
    with pytest.raises(FreshReviewError, match='repair-candidate-not-new'):
        validate_result(state.legacy_passthrough.thaw(), row, item, body, finding)


def test_reverification_rejects_unbound_results(reverified):
    import copy
    from mission_kernel.transitions import decide
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.fresh_review import canonical_bytes
    from mission_kernel.repair_attempts import reference
    from mission_kernel.commands import FreshReviewInputEffectClaim
    state, command, _ = reverified
    seed = command.result.thaw(); cases = []
    for key in seed:
        for value in (None, False, ''):
            body = copy.deepcopy(seed); body[key] = value; cases.append(body)
    for key in seed['receipt']:
        body = copy.deepcopy(seed); body['receipt'][key] = False if key == 'executed_count' or seed['receipt'][key] is None else None; cases.append(body)
    for key in seed['dispatch']:
        body = copy.deepcopy(seed); body['dispatch'][key] = None; cases.append(body)
    cases.append(dict(seed, unknown='claim'))
    assert len(cases) >= 50
    def claim(ref):
        return FreshReviewInputEffectClaim(ref['kind'], ref['relative_path'], ref['digest'], ref['size'])
    for index, body in enumerate(cases):
        if isinstance(body['receipt'], dict) and body['receipt'] != seed['receipt']:
            body['receipt_ref'] = reference('repair-replay', canonical_bytes(body['receipt']))
        ref = reference('repair-reverification', canonical_bytes(body))
        bad = replace(command, operation_id='repair-result:'+ref['digest'][7:], result=freeze_json_value(body),
            effect=claim(ref), receipt_effect=claim(body['receipt_ref']) if isinstance(body['receipt_ref'], dict) else command.receipt_effect)
        decision = decide(state, bad)
        assert not decision.accepted, index
        assert decision.transition is None, index


def test_changed_non_replay_candidate_remains_stale(reverified):
    from mission_kernel.commands import RecordVerificationReceipt
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import decide
    state, command, verified = reverified
    body = command.result.thaw(); candidates = dict(body['candidate'])
    other = next(k for k in candidates if k != body['command_id'])
    candidates[other] = 'sha256:'+'c'*64
    receipt = dict(body['receipt'], candidate_digest='sha256:'+'c'*64)
    result = decide(verified, RecordVerificationReceipt(receipt['finished_at'], freeze_json_value(receipt), candidate=freeze_json_value(candidates)))
    assert result.accepted, result.rejection
    row = result.transition.new_state.repair.lineages[0].document.thaw()
    assert row['last_candidate_change']['generation'] >= body['generation']
    again = decide(result.transition.new_state, RecordVerificationReceipt(receipt['finished_at'],
        freeze_json_value(receipt), candidate=freeze_json_value(candidates)))
    assert again.accepted, again.rejection
    assert again.transition.new_state.repair.lineages[0].document.thaw()['last_candidate_change'] == row['last_candidate_change']


def test_maximum_intent_and_verified_terminal_fit_existing_deltas(reverified):
    from mission_kernel.state_capacity import FRESH_REVIEW_DISPATCH_STAGE_DELTA
    from mission_kernel.repair_attempts import REPAIR_TERMINAL_DELTA, validate_attempts
    state, command, verified = reverified
    row = verified.repair.lineages[0].document.thaw(); item = row['attempts'][-1]
    item['intent'].update(operation_id='o'*128, reservation_id='r'*128, fencing_epoch=2**63-1)
    item['terminal'].update(fencing_epoch=2**63-1, generation=2**63-1)
    item['terminal']['comparison_ref']['size'] = 262144
    validate_attempts(row)
    marker = dict(generation=2**63-1, candidate_map_digest='sha256:'+'f'*64, operation_id='o'*128)
    envelope = lambda a, m=marker: json.dumps(dict(extensions=dict(repair_lineage=dict(lineages=[dict(attempts=[a], last_candidate_change=m)]))), indent=2, sort_keys=True).encode()
    import copy
    pending = copy.deepcopy(item); pending.update(status='pending', terminal={'kind':'absent','reason':'not-terminal'})
    before_intent = copy.deepcopy(pending); before_intent.pop('intent')
    assert len(envelope(pending)) - len(envelope(before_intent, dict(kind='absent', reason='no-resolution-binding'))) <= FRESH_REVIEW_DISPATCH_STAGE_DELTA
    assert len(envelope(item)) - len(envelope(pending)) <= REPAIR_TERMINAL_DELTA


@pytest.mark.parametrize('schema', [4, 5])
def test_public_verified_lineage_allows_fresh_completion(repair_reviewer, run_cli, schema):
    root, old_request, env, journal = repair_reviewer
    _, item = begin(run_cli, repair_reviewer)
    (root / 'app.txt').write_text('repaired')
    replay = run_cli('repair', 'reverify', '--attempt', item['attempt_id'], cwd=root)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    run_cli('verification', 'run', '--criterion', 'AC1', cwd=root, check=True)
    prepared = run_cli('fresh-review', 'prepare', '--perspective', 'counterexamples',
        '--adapter-registration-digest', old_request['adapter_registration_digest'], cwd=root,
        env_extra={**env, 'MISSION_OPERATION_ID':'prepare-after-repair'}, check=True)
    request = json.loads(prepared.stdout)['request']
    current = root, request, {**env, 'MISSION_OPERATION_ID':'dispatch-after-repair'}, journal
    invoke(run_cli, current, FIXTURE_REVIEW_MODE='completion-clean')
    imported = import_output(run_cli, current, MISSION_OPERATION_ID='import-after-repair')
    assert imported.returncode == 0, imported.stdout + imported.stderr
    if schema == 4:
        _persist_fixture(root, json.loads(run_cli('get', cwd=root).stdout), 4)
    result = run_cli('mark-passes', cwd=root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(run_cli('get', cwd=root).stdout)['passes'] is True


def test_unpublished_replay_is_blocked_and_never_respawned(repair_reviewer, run_cli, monkeypatch):
    from .test_command_inventory import _load_mission_state_module
    from mission_application import repair_reverification as app
    from mission_application.repair import run_repair_cli
    from types import SimpleNamespace
    root = repair_reviewer[0]; _, item = begin(run_cli, repair_reviewer)
    (root / 'app.txt').write_text('repaired')
    monkeypatch.chdir(root); monkeypatch.setenv('MISSION_SESSION_ID', 'test'); monkeypatch.setenv('MISSION_LEASE_ID', 'test-lease')
    def unavailable(*args):
        raise OSError('publication unavailable')
    monkeypatch.setattr(app, 'make_evidence_effect', unavailable)
    args = SimpleNamespace(repair_command='reverify', attempt=item['attempt_id'])
    services = _load_mission_state_module()._ACCEPTANCE_CONTRACT_CLI_SERVICES
    ended = json.loads(run_repair_cli(args, services))['attempt']
    assert ended['status'] == 'blocked' and ended['terminal']['reason'] == 'effects-unavailable'
    result = run_cli('repair', 'reverify', '--attempt', item['attempt_id'], cwd=root)
    assert result.returncode != 0 and 'already-terminal' in result.stderr


def test_partial_current_candidate_map_is_rejected(reverified):
    from mission_kernel.commands import RecordVerificationReceipt
    from mission_kernel.transitions import decide
    from mission_kernel.json_codec import freeze_json_value
    _, command, verified = reverified
    body = command.result.thaw()
    candidates = {body['command_id']: body['candidate'][body['command_id']]}
    result = decide(verified, RecordVerificationReceipt(body['receipt']['finished_at'], freeze_json_value(body['receipt']),
        candidate=freeze_json_value(candidates)))
    assert not result.accepted and result.rejection.code == 'verification-candidate-invalid'


@pytest.mark.parametrize('field', ['effect', 'receipt_effect', 'evidence'])
def test_reverification_requires_both_effects_and_origin(reverified, field):
    from mission_kernel.transitions import decide
    _, command, _ = reverified
    bad = () if field == 'evidence' else replace(getattr(command, field), size=getattr(command, field).size+1)
    result = decide(reverified[0], replace(command, **{field:bad}))
    assert not result.accepted and result.transition is None


@pytest.mark.parametrize('returned', ['introduced', 'previous-attempt'])
def test_earlier_failed_candidate_cannot_verify_after_a_different_failure(reverified, returned):
    import copy
    from mission_kernel.repair_reverification import validate_result
    from mission_kernel.fresh_review import canonical_bytes, canonical_digest, FreshReviewError
    from mission_kernel.repair_attempts import reference
    from mission_kernel.json_codec import freeze_json_value
    state, command, _ = reverified
    row = state.repair.lineages[0].document.thaw(); item = row['attempts'][-1]
    finding = command.evidence[0].findings[0].thaw(); body = command.result.thaw()
    prior = copy.deepcopy(body); prior['receipt'].update(status='failed', exit_code=1, candidate_digest='sha256:'+'c'*64)
    previous = copy.deepcopy(item); previous.update(attempt_id='sha256:'+'d'*64, status='failed',
        terminal=dict(generation=1, comparison_ref=reference('repair-reverification', canonical_bytes(prior))))
    row['attempts'].insert(0, previous)
    earlier = copy.deepcopy(prior); earlier['receipt']['candidate_digest'] = 'sha256:'+'e'*64
    earlier_attempt = copy.deepcopy(previous); earlier_attempt['attempt_id'] = 'sha256:'+'e'*64
    earlier_attempt['terminal']['comparison_ref'] = reference('repair-reverification', canonical_bytes(earlier))
    row['attempts'].insert(0, earlier_attempt)
    candidate = finding['replay']['candidate_digest'] if returned == 'introduced' else earlier['receipt']['candidate_digest']
    body['candidate'][body['command_id']] = candidate
    body['receipt']['candidate_digest'] = candidate
    item['intent'].update(snapshot_digest=body['receipt']['candidate_digest'], candidate_map_digest=canonical_digest(body['candidate']))
    body['dispatch'] = item['intent']; body['receipt_ref'] = reference('repair-replay', canonical_bytes(body['receipt']))
    with pytest.raises(FreshReviewError, match='repair-candidate-not-new'):
        validate_result(state.legacy_passthrough.thaw(), row, item, body, finding,
            previous_results=(freeze_json_value(earlier), freeze_json_value(prior)))


@pytest.mark.parametrize('change', [dict(output_truncated=True), dict(started_at='2025-12-31T23:59:59Z'),
    dict(finished_at='2026-01-01T00:01:00Z'), dict(started_at='2026-01-01T00:00:02Z')])
def test_pass_requires_complete_output_and_dispatch_time_window(reverified, change):
    from mission_kernel.repair_reverification import validate_result
    from mission_kernel.fresh_review import canonical_bytes, FreshReviewError
    from mission_kernel.repair_attempts import reference
    state, command, _ = reverified
    row = state.repair.lineages[0].document.thaw(); item = row['attempts'][-1]
    body = command.result.thaw(); body['receipt'].update(change)
    body['receipt_ref'] = reference('repair-replay', canonical_bytes(body['receipt']))
    with pytest.raises(FreshReviewError):
        validate_result(state.legacy_passthrough.thaw(), row, item, body, command.evidence[0].findings[0].thaw())


def test_partial_legacy_observation_invalidates_unknown_dependencies(reverified):
    from mission_kernel.commands import RecordVerificationReceipt
    from mission_kernel.transitions import decide
    from mission_kernel.json_codec import freeze_json_value
    _, command, verified = reverified
    result = decide(verified, RecordVerificationReceipt(command.result.thaw()['receipt']['finished_at'],
        freeze_json_value(command.result.thaw()['receipt'])))
    assert result.accepted, result.rejection
    row = result.transition.new_state.repair.lineages[0].document.thaw()
    assert row['last_candidate_change']['generation'] > command.result.thaw()['generation']


def test_concurrent_reverify_does_not_terminate_running_dispatch(repair_reviewer, run_cli, monkeypatch):
    from .test_command_inventory import _load_mission_state_module
    from mission_application import repair_reverification as app
    from mission_application.repair import run_repair_cli
    from types import SimpleNamespace
    root = repair_reviewer[0]; _, item = begin(run_cli, repair_reviewer)
    (root / 'app.txt').write_text('repaired')
    monkeypatch.chdir(root); monkeypatch.setenv('MISSION_SESSION_ID', 'test'); monkeypatch.setenv('MISSION_LEASE_ID', 'test-lease')
    args = SimpleNamespace(repair_command='reverify', attempt=item['attempt_id'])
    services = _load_mission_state_module()._ACCEPTANCE_CONTRACT_CLI_SERVICES
    original = app.run_contract_verifier
    def running(*args_, **kwargs):
        with pytest.raises(SystemExit):
            run_repair_cli(args, services)
        state = json.loads(run_cli('get', cwd=root).stdout)
        assert state['repair_lineage']['lineages'][0]['attempts'][-1]['status'] == 'pending'
        return original(*args_, **kwargs)
    monkeypatch.setattr(app, 'run_contract_verifier', running)
    assert json.loads(run_repair_cli(args, services))['attempt']['status'] == 'verified'
