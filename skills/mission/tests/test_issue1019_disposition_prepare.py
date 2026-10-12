"""Internal preparation persists an obligation without granting resolution."""
from dataclasses import replace
import pytest
from mission_kernel.fresh_review import canonical_bytes
from mission_kernel.json_codec import freeze_json_value
from mission_kernel.repair_attempts import reference
from mission_kernel.transitions import decide
from .test_issue906_repair_attempts import repair_state, published, gate_state, completed_carrier
from .test_issue907_disposition import request_body, rejected
from .test_issue906_reverification import reverified


def prepare_command(pair, *, supersedes=None):
    from mission_kernel.commands import PrepareFindingDisposition, FreshReviewInputEffectClaim
    state, evidence = pair
    row = state.repair.lineages[0].document.thaw()
    packet = dict(purpose='Review the original obligation', lineage_id=row['lineage_id'])
    body = request_body(state, row, supersedes=supersedes)
    body['input_ref'] = reference('finding-disposition-input', canonical_bytes(packet))
    refs = (reference('finding-disposition-request', canonical_bytes(body)), body['input_ref'])
    claims = tuple(FreshReviewInputEffectClaim(r['kind'], r['relative_path'], r['digest'], r['size']) for r in refs)
    return PrepareFindingDisposition(freeze_json_value(body), freeze_json_value(packet), state.lease.fencing_epoch,
        claims, (evidence,), freeze_json_value(row['introduced_candidate']['snapshots']))


def test_prepare_persists_request_and_keeps_original_obligation_open(repair_state):
    state, _ = repair_state
    command = prepare_command(repair_state)
    decision = decide(state, command)
    assert decision.accepted, decision.rejection
    row = decision.transition.new_state.repair.lineages[0].document.thaw()
    assert row['lifecycle'] == 'open'
    assert row['origin'] == state.repair.lineages[0].document.thaw()['origin']
    item, = row['dispositions']
    assert item['stage'] == 'prepared'
    assert item['request_ref'] == reference('finding-disposition-request', canonical_bytes(command.request.thaw()))
    assert item['operation_id'] == command.request.thaw()['operation_id']


def test_prepare_supersedes_only_unstarted_pending_repair_atomically(repair_state):
    from .test_issue906_repair_attempts import begun_state
    state, _ = begun_state(repair_state)
    identifier = state.repair.lineages[0].document.thaw()['attempts'][-1]['attempt_id']
    command = prepare_command((state, repair_state[1]), supersedes=identifier)
    decision = decide(state, command)
    assert decision.accepted, decision.rejection
    row = decision.transition.new_state.repair.lineages[0].document.thaw()
    attempt = row['attempts'][-1]
    assert attempt['status'] == 'blocked'
    assert attempt['terminal']['reason'] == 'disposition-requested'
    assert attempt['terminal']['operation_id'] == row['dispositions'][-1]['operation_id']
    assert state.repair.lineages[0].document.thaw()['attempts'][-1]['status'] == 'pending'


def test_new_prepare_generation_follows_all_disposition_history(repair_state):
    from mission_kernel.repair_attempts import _store
    state, _ = repair_state
    decision = decide(state, prepare_command(repair_state))
    document = decision.transition.new_state.legacy_passthrough.thaw()
    item = document['repair_lineage']['lineages'][0]['dispositions'][-1]
    item.update(stage='terminal', terminal=dict(ref={'kind':'absent','reason':'effects-unavailable'},
        operation_id='end', fencing_epoch=2, generation=41, outcome='blocked', reason_code='effects-unavailable'))
    state = _store(state, document)
    command = prepare_command((state, repair_state[1]))
    body = command.request.thaw()
    body['operation_id'] = 'next-prepare'
    from mission_kernel.finding_disposition import request_id
    body['request_id'] = request_id(body)
    command = with_request(command, body)
    decision = decide(state, command)
    assert decision.accepted, decision.rejection
    assert decision.transition.new_state.repair.lineages[0].document.thaw()['dispositions'][-1]['generation'] == 42


def with_request(command, body):
    from mission_kernel.commands import FreshReviewInputEffectClaim
    ref = reference('finding-disposition-request', canonical_bytes(body))
    return replace(command, request=freeze_json_value(body), effects=(
        FreshReviewInputEffectClaim(ref['kind'], ref['relative_path'], ref['digest'], ref['size']), command.effects[1]))


def test_prepare_starts_with_explicit_unobserved_recovery_slot(repair_state):
    decision = decide(repair_state[0], prepare_command(repair_state))
    item = decision.transition.new_state.repair.lineages[0].document.thaw()['dispositions'][-1]
    assert item['recovery'] == {'kind':'absent','reason':'not-observed'}


@pytest.mark.parametrize('encoding', ['canonical', 'legacy-pretty'])
def test_prepare_reserves_future_terminal_and_recovery_in_each_encoding(repair_state, encoding):
    from mission_kernel import state_capacity as sc
    state = decide(repair_state[0], prepare_command(repair_state)).transition.new_state
    document = state.legacy_passthrough.thaw()
    assert sc.disposition_reserve(document) > 0
    assert sc.residual_reservation(document, encoding=sc.StateEncoding(encoding)) >= sc.disposition_reserve(document, encoding=sc.StateEncoding(encoding))


def test_recovery_is_bounded_diagnostic_and_preserves_pending(repair_state):
    from mission_kernel.finding_disposition import validate_dispositions
    state = decide(repair_state[0], prepare_command(repair_state)).transition.new_state
    row = state.repair.lineages[0].document.thaw()
    item = row['dispositions'][-1]
    item.update(stage='dispatched', intent=dict(operation_id='disposition-dispatch:'+item['request_id'][7:],
        fencing_epoch=2, reservation_id='reservation', budget_class='repair', parent_identity='parent',
        started_at='2026-01-01T00:00:00Z', deadline_at='2026-01-01T00:05:00Z'))
    item['recovery'] = dict(operation_id='recovery', fencing_epoch=3,
        dispatch_operation_id=item['intent']['operation_id'], dispatch_fencing_epoch=2,
        observed_at='2026-01-01T00:00:01.999999Z', status='exited-result-available')
    validate_dispositions(row)
    assert row['lifecycle'] == 'open'


@pytest.mark.parametrize('field,value', [('schema','mission-finding-disposition-request/2'),
    ('request_schema','mission-finding-disposition-request/2'), ('origin_digest','sha256:'+'b'*64),
    ('full_candidate_digest','sha256:'+'b'*64), ('session_id','other'), ('resolved',True)])
def test_prepare_rejects_unbound_or_future_request(repair_state, field, value):
    command = prepare_command(repair_state)
    body = command.request.thaw(); body[field] = value
    if field == 'session_id':
        from mission_kernel.finding_disposition import request_id
        body['request_id'] = request_id(body)
    command = with_request(command, body)
    assert not decide(repair_state[0], command).accepted
    assert 'dispositions' not in repair_state[0].repair.lineages[0].document.thaw()


@pytest.mark.parametrize('mutate', [
    lambda c: replace(c, fencing_epoch=1), lambda c: replace(c, fencing_epoch=True),
    lambda c: replace(c, effects=()), lambda c: replace(c, effects=c.effects+c.effects[:1]),
    lambda c: replace(c, effects=(replace(c.effects[0], kind='other'), c.effects[1])),
    lambda c: replace(c, effects=(c.effects[0], replace(c.effects[1], target='other'))),
    lambda c: replace(c, effects=(c.effects[0], replace(c.effects[1], digest='sha256:'+'b'*64))),
    lambda c: replace(c, effects=(c.effects[0], replace(c.effects[1], size=True))),
    lambda c: replace(c, packet=freeze_json_value({'changed':True})),
    lambda c: replace(c, observed_candidates=freeze_json_value({})),
])
def test_prepare_rejects_stale_fence_effect_or_capture(repair_state, mutate):
    assert not decide(repair_state[0], mutate(prepare_command(repair_state))).accepted


def test_pending_excludes_second_prepare_and_started_repair(repair_state):
    from .test_issue906_repair_attempts import begun_state
    from mission_kernel.repair_attempts import _store
    command = prepare_command(repair_state)
    state = decide(repair_state[0], command).transition.new_state
    assert not decide(state, command).accepted
    body = command.request.thaw(); body['operation_id'] = 'other'
    from mission_kernel.finding_disposition import request_id
    body['request_id'] = request_id(body)
    assert not decide(state, with_request(command, body)).accepted
    state, _ = begun_state(repair_state)
    document = state.legacy_passthrough.thaw()
    attempt = document['repair_lineage']['lineages'][0]['attempts'][-1]
    command = prepare_command((state, repair_state[1]), supersedes=attempt['attempt_id'])
    attempt['intent'] = dict(operation_id='started', fencing_epoch=2, reservation_id='reservation', budget_class='repair',
        candidate_map_digest='sha256:'+'a'*64, snapshot_digest='sha256:'+'a'*64)
    state = _store(state, document)
    assert not decide(state, command).accepted
    assert state.repair.lineages[0].document.thaw()['attempts'][-1]['status'] == 'pending'


@pytest.mark.parametrize('encoding', ['canonical','legacy-pretty'])
def test_actual_maximum_stage_replacements_fit_reserved_bytes_at_limit(repair_state, encoding):
    import copy, json
    from mission_kernel import state_capacity as sc
    from mission_kernel.disposition_capacity import maximum_disposition
    from mission_kernel.finding_disposition import validate_dispositions
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    from .test_issue906_repair_attempts import begun_state
    state, _ = begun_state(repair_state)
    attempt = state.repair.lineages[0].document.thaw()['attempts'][-1]
    state = decide(state, prepare_command((state, repair_state[1]), supersedes=attempt['attempt_id'])).transition.new_state
    document = state.legacy_passthrough.thaw()
    mode = sc.StateEncoding(encoding)
    def encode(d):
        return json.dumps(d, ensure_ascii=False, sort_keys=True,
            **({'indent':2} if encoding == 'legacy-pretty' else {'separators':(',',':')})).encode()
    reserve = sc.residual_reservation(document, encoding=mode)
    document['escaped_fixture'] = 'quote:" newline:\n unicode:日本語 slash:\\'
    document['padding'] = ''
    document['padding'] = 'x' * (sc.STATE_LIMIT - sc.system_remaining(document) - reserve - len(encode(document)) - 32)
    original = encode(document)
    with pytest.raises(CapacityWriteError, match='state-capacity-exhausted'):
        check_state_capacity(original, encode(dict(document, padding=document['padding']+'x'*64)), encoding=mode)
    for stage in ('dispatched','running','terminal'):
        for outcome in ('rejected','blocked'):
            proposed = copy.deepcopy(document)
            row = proposed['repair_lineage']['lineages'][0]
            old = row['dispositions'][-1]
            item = maximum_disposition()
            item.update(request_id=old['request_id'], request_ref=old['request_ref'], operation_id=old['operation_id'],
                supersedes_attempt_id=old['supersedes_attempt_id'], fencing_epoch=old['fencing_epoch'], stage=stage)
            item['intent']['operation_id'] = 'disposition-dispatch:'+item['request_id'][7:]
            item['recovery']['dispatch_operation_id'] = item['intent']['operation_id']
            if stage != 'terminal':
                item['terminal'] = {'kind':'absent','reason':'not-terminal'}
                item['duplicate_targets'] = []
            else:
                item['terminal'].update(outcome=outcome, reason_code='duplicate' if outcome == 'rejected' else 'disposition-mapping-over-limit')
            if stage == 'dispatched':
                item['launch_ref'] = {'kind':'absent','reason':'not-launched'}
            row['dispositions'][-1] = item
            row['lifecycle'] = outcome if stage == 'terminal' and outcome == 'rejected' else 'open'
            validate_dispositions(row)
            assert len(encode(proposed))-len(original) <= sc.disposition_reserve(document)
            assert check_state_capacity(original, encode(proposed), encoding=mode).accepted


def test_internal_preparation_binds_bytes_and_operation_readback(repair_state):
    from mission_application.disposition import prepare_disposition
    from mission_application.evidence import execute_evidence_operation
    from mission_application.ports import LegacyCommandExecutionResult
    state, _ = repair_state
    command = prepare_command(repair_state)
    prepared = prepare_disposition(command)
    transition = decide(state, command).transition
    source = transition.new_state.legacy_passthrough.thaw()
    class Repository:
        def execute_evidence_transition_effects(self, prepare):
            return prepared, LegacyCommandExecutionResult(None, freeze_json_value({}), replayed=True, replayed_state=freeze_json_value(source))
    # The current head contains nothing; replay must use its own committed image.
    prepared = replace(prepared, command=replace(command, fencing_epoch=3))
    result = execute_evidence_operation(Repository(), lambda _: prepared)
    assert result['disposition'] == source['repair_lineage']['lineages'][0]['dispositions'][0]
    assert [e.content for e in prepared.effects] == [canonical_bytes(command.request.thaw()), canonical_bytes(command.packet.thaw())]


@pytest.mark.parametrize('point,committed', [('after-stage',False), ('after-prepare',False), ('after-head-replace',True)])
def test_uow_prepare_crash_keeps_pair_atomic_and_replays_original_operation(repair_state, tmp_path, monkeypatch, point, committed):
    import json
    from mission_application.disposition import prepare_disposition
    from mission_application.evidence import execute_evidence_operation
    from .test_issue879_completion_cli import _persist_fixture
    from .test_command_inventory import _load_mission_state_module
    from mission_kernel import decode_snapshot
    root = tmp_path
    path = root / '.mission-state' / 'sessions' / 'test.json'
    path.parent.mkdir(parents=True)
    from .test_issue906_repair_attempts import begun_state
    state, _ = begun_state(repair_state)
    document = state.legacy_passthrough.thaw()
    attempt = document['repair_lineage']['lineages'][0]['attempts'][-1]
    attempt['fencing_epoch'] = 1  # The fixture genesis owns epoch one.
    identifier = attempt['attempt_id']
    path.write_text(json.dumps(document))
    _persist_fixture(root, document, 5)
    monkeypatch.chdir(root)
    monkeypatch.setenv('MISSION_SESSION_ID','test')
    monkeypatch.setenv('MISSION_LEASE_ID','test-lease')
    services = _load_mission_state_module()._ACCEPTANCE_CONTRACT_CLI_SERVICES
    command = prepare_command((state, repair_state[1]), supersedes=identifier)
    operation = command.request.thaw()['operation_id']
    _, intent = services.canonical_operation('test', 'finding-disposition-prepare', {}, caller_operation_id=operation)
    def repository():
        return services.repository(root, path, stamp=True, strict_read=True, pre_admit_lease=True,
            session_id='test', operation_id=operation, operation_command=intent, operation_command_type='finding-disposition-prepare')
    def prepare(document):
        typed = decode_snapshot(json.dumps(document).encode()).state
        return prepare_disposition(replace(command, fencing_epoch=typed.lease.fencing_epoch))
    repo = repository()
    class Interrupted(Exception):
        pass
    def stop(reached):
        if reached == point:
            raise Interrupted(point)
    repo._repository.fault_injector = stop
    with pytest.raises(Interrupted):
        execute_evidence_operation(repo, prepare)
    reader = services.repository(root, path, stamp=False, strict_read=True, session_id='test')
    with reader.transaction():
        source = reader.load()
    row = source['repair_lineage']['lineages'][0]
    assert bool(row.get('dispositions')) == committed
    assert row['attempts'][-1]['status'] == ('blocked' if committed else 'pending')
    result = execute_evidence_operation(repository(), prepare)
    assert result['disposition']['stage'] == 'prepared'
    assert result['disposition']['operation_id'] == operation


@pytest.mark.parametrize('field,value', [('status','stopped'), ('operation_id','x'*129),
    ('fencing_epoch',True), ('dispatch_fencing_epoch',3), ('dispatch_operation_id','other'),
    ('observed_at','2025-01-01T00:00:00Z'), ('output','unbounded text')])
def test_closed_recovery_slot_rejects_unbound_observation(repair_state, field, value):
    from mission_kernel.finding_disposition import validate_dispositions
    from mission_kernel.fresh_review import FreshReviewError
    row = decide(repair_state[0], prepare_command(repair_state)).transition.new_state.repair.lineages[0].document.thaw()
    item = row['dispositions'][-1]
    item.update(stage='dispatched', intent=dict(operation_id='disposition-dispatch:'+item['request_id'][7:],
        fencing_epoch=2, reservation_id='reservation', budget_class='repair', parent_identity='parent',
        started_at='2026-01-01T00:00:00Z', deadline_at='2026-01-01T00:05:00Z'))
    item['recovery'] = dict(operation_id='recovery', fencing_epoch=3, dispatch_operation_id=item['intent']['operation_id'],
        dispatch_fencing_epoch=2, observed_at='2026-01-01T00:00:01Z', status='stop-unconfirmed')
    item['recovery'][field] = value
    with pytest.raises(FreshReviewError):
        validate_dispositions(row)


def test_effect_seal_rejects_every_identity_field_and_count(repair_state):
    from mission_application.disposition import prepare_disposition
    from mission_kernel.transitions import bind_transition_effects, TransitionTableError
    command = prepare_command(repair_state)
    transition = decide(repair_state[0], command).transition
    effects = prepare_disposition(command).effects
    assert bind_transition_effects(transition, effects).effects == effects
    variants = [(), effects+effects[:1]]
    variants += [(replace(effects[0], **{field:value}), effects[1]) for field,value in
        [('kind','other'), ('target','other'), ('digest','sha256:'+'b'*64), ('size',1)]]
    for forged in variants:
        with pytest.raises(TransitionTableError):
            bind_transition_effects(transition, forged)


@pytest.mark.parametrize('fixture_name', ['reverified','rejected'])
def test_current_resolution_refuses_redundant_prepare_but_stale_candidate_reopens(request, fixture_name):
    from mission_kernel.repair_reverification import full_candidate_digest
    from mission_kernel.finding_disposition import request_id
    fixture = request.getfixturevalue(fixture_name)
    if fixture_name == 'reverified':
        state, evidence = fixture[2], fixture[1].evidence[0]
        snapshots = fixture[1].result.thaw()['candidate']
    else:
        state, evidence = fixture[1], fixture[2].evidence[0]
        snapshots = fixture[2].observed_candidates.thaw()
    command = prepare_command((state,evidence))
    body = command.request.thaw()
    body['operation_id'] = 'next-prepare'
    body['request_id'] = request_id(body)
    contract = state.legacy_passthrough.thaw()['acceptance_contract']
    body['full_candidate_digest'] = full_candidate_digest(contract,snapshots)
    command = with_request(replace(command, observed_candidates=freeze_json_value(snapshots)),body)
    assert not decide(state, command).accepted
    snapshots = {key:'sha256:'+'c'*64 for key in snapshots}
    body['full_candidate_digest'] = full_candidate_digest(contract,snapshots)
    command = with_request(replace(command, observed_candidates=freeze_json_value(snapshots)),body)
    assert decide(state, command).accepted
