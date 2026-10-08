"""F1 contracts: typed budgets remain inert until policy intake is enabled."""
import pytest


def test_policy_decimal_total_and_final_recovery_minima():
    from mission_kernel.budget import BudgetError, decode_policy, default_policy_document, total_seconds
    assert total_seconds(1.1) == 66
    with pytest.raises(BudgetError, match='budget-total-too-small'):
        total_seconds(0.01)
    policy = decode_policy(default_policy_document(870), budget_minutes=14.5)
    assert policy.phase_reserves['repair'] == 174
    assert policy.final_reserve_effective == policy.min_final_run_sec == 27
    assert policy.reactivate == 'allowed'
    with pytest.raises(BudgetError, match='budget-final-reserve-too-small'):
        decode_policy(default_policy_document(869))
    with pytest.raises(BudgetError, match='budget-total-mismatch'):
        decode_policy(default_policy_document(870), budget_minutes=15)


def test_closed_ledger_roundtrip_and_reservation_accounting():
    from copy import deepcopy
    from mission_kernel.budget import BudgetError, decode_ledger, decode_policy, default_policy_document, new_ledger, ledger_document
    policy = decode_policy(default_policy_document(1800))
    wire = ledger_document(new_ledger(policy, '2026-01-01T00:00:00Z'))
    assert decode_ledger({'budget_minutes': 30, 'budget_ledger': wire}).policy == policy
    assert decode_ledger({'schema_version': 5, 'extensions': {'budget_minutes': 30, 'budget_ledger': wire}}) == decode_ledger({'budget_minutes': 30, 'budget_ledger': wire})
    assert decode_ledger({}).policy is None
    for bad in (None, {}, {**wire, 'extra': 1}):
        with pytest.raises(BudgetError):
            decode_ledger({'budget_minutes': 30, 'budget_ledger': bad})
    wire['clock']['last_observed_at'] = '2026-01-01T00:01:00Z'
    reservation = dict(reservation_id='reservation:1', entry='verification-run', budget_class='verification',
                       target='command:1', operation_id='op:1', fencing_epoch=1,
                       reserved_at='2026-01-01T00:00:00Z', child_deadline_at='2026-01-01T00:00:30Z',
                       settle_by='2026-01-01T00:00:54Z', reserved_sec=54, reserved_bytes=1000)
    wire['reservations'] = [reservation]
    wire['phase_charges']['verification'].update(reservation_count=1, open_count=1)
    assert decode_ledger({'budget_minutes': 30, 'budget_ledger': wire}).reservations[0].reserved_sec == 54
    for mutate in (
        lambda x: x['reservations'].append(deepcopy(reservation)),
        lambda x: x['reservations'][0].update(reserved_sec=53),
        lambda x: x['reservations'][0].update(child_deadline_at='2025-12-31T23:59:59Z'),
        lambda x: x['phase_charges']['verification'].update(open_count=0),
        lambda x: x['clock'].update(last_observed_at='2025-12-31T23:59:59Z'),
        lambda x: x.update(policy_digest='sha256:' + '0' * 64),
    ):
        bad = deepcopy(wire)
        mutate(bad)
        with pytest.raises(BudgetError):
            decode_ledger({'budget_minutes': 30, 'budget_ledger': bad})


def test_deadlines_protect_open_reservations_and_exhaustion_waits_for_final():
    from dataclasses import replace
    from mission_kernel.budget import (ActiveClock, DispatchReservation, Exhaustion, FinalLatch, FinalRun,
        PhaseCharge, StopSlots, active_seconds, deadlines, decode_policy, default_policy_document,
        exhaustion, final_infeasible, new_ledger, child_deadlines)
    ledger = new_ledger(decode_policy(default_policy_document(1800)), '2026-01-01T00:00:00Z')
    at = '2026-01-01T00:01:00Z'
    d = deadlines(ledger, at)
    assert active_seconds(ledger.clock, at) == 60
    assert (d.overall, d.final, d.repair, d.unprotected) == (
        '2026-01-01T00:30:00Z', '2026-01-01T00:28:30Z',
        '2026-01-01T00:26:30Z', '2026-01-01T00:20:30Z')
    assert child_deadlines(ledger, at, 'repair-reverify', 'repair', 3600) == (
        '2026-01-01T00:26:06Z', '2026-01-01T00:26:30Z', 1530)
    assert not final_infeasible(ledger, '2026-01-01T00:28:03Z')
    assert exhaustion(ledger, '2026-01-01T00:28:04Z').cause == 'final-infeasible'
    held = DispatchReservation('r', 'verification-run', 'final', 't', 'o', 1,
                               at, '2026-01-01T00:28:06Z', d.final, 1650, 1)
    running = replace(ledger, reservations=(held,))
    assert exhaustion(running, '2026-01-01T00:28:04Z') is None
    assert exhaustion(running, d.overall).cause == 'overall-deadline'
    finished = replace(ledger, stop_slots=StopSlots(final_latch=FinalLatch(at, 'explicit'),
        final_run=FinalRun('r', '2026-01-01T00:28:05Z')))
    assert exhaustion(finished, '2026-01-01T00:28:05Z') is None
    assert exhaustion(finished, '2026-01-01T00:29:30Z').cause == 'closeout-margin'
    latched = replace(finished, stop_slots=replace(finished.stop_slots,
        exhaustion=Exhaustion(at, 'final-infeasible')))
    assert exhaustion(latched, '2026-01-01T00:02:00Z').cause == 'final-infeasible'
    # Closed clocks exclude halt time; child times never enter the active clock.
    halted = replace(ledger, clock=ActiveClock(60, None, at))
    assert active_seconds(halted.clock, '2026-01-01T01:00:00Z') == 60
    external = replace(ledger, policy=replace(ledger.policy, external_deadline_at='2026-01-01T00:20:00Z'))
    assert deadlines(external, at).overall == '2026-01-01T00:20:00Z'
    spent = replace(ledger, phase_charges=(*ledger.phase_charges[:3], PhaseCharge(100, 1), PhaseCharge(20, 1)),
                    reservations=(replace(held, reserved_sec=40),))
    d = deadlines(spent, at)
    assert (d.repair_unspent, d.final_unspent) == (260, 60)
    with pytest.raises(Exception, match='budget-clock-regressed'):
        active_seconds(halted.clock, '2026-01-01T00:00:59Z')


def test_codec_projection_is_bound_and_generic_budget_writes_are_rejected():
    import json
    from dataclasses import replace
    from mission_kernel import decode_mission_state, decode_snapshot, project_legacy_document
    from mission_kernel.codec_v5 import encode_v5_state
    from mission_kernel.budget import BudgetProjection, decode_policy, default_policy_document, new_ledger, ledger_document
    from mission_kernel.commands import SetExtensionFields
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import decide
    from .mission_state_fixture_corpus import current_v5_open_state
    payload = current_v5_open_state()
    ledger = new_ledger(decode_policy(default_policy_document(1800)), '2026-01-01T00:00:00Z')
    payload['extensions']['budget_ledger'] = ledger_document(ledger)
    payload['extensions']['budget_minutes'] = 30
    snapshot = decode_snapshot(json.dumps(payload).encode())
    assert snapshot.state.budget == ledger
    assert decode_mission_state(encode_v5_state(snapshot.state, snapshot.guidance)).budget == ledger
    v4 = dict(schema_version=4, mission='budget fixture', budget_minutes=30, budget_ledger=ledger_document(ledger))
    legacy_state = decode_mission_state(json.dumps(v4).encode())
    assert decode_mission_state(project_legacy_document(legacy_state)).budget == ledger
    with pytest.raises(ValueError, match='budget-projection-backing-mismatch'):
        project_legacy_document(replace(legacy_state, budget=BudgetProjection()))
    for fields in ({'budget_ledger': None}, {'budget_minutes': 45}, {'budget_ledger.policy': {}},
                   {'extensions': {'budget_ledger': None}}):
        result = decide(snapshot.state, SetExtensionFields(freeze_json_value(fields), '2026-01-01T00:00:00Z'))
        assert not result.accepted
    bare = current_v5_open_state()
    state = decode_mission_state(json.dumps(bare).encode())
    assert decide(state, SetExtensionFields(freeze_json_value({'budget_minutes': 45}), '2026-01-01T00:00:00Z')).accepted


def test_status_and_next_are_read_only_and_policy_intake_stays_closed(legacy_run_cli, tmp_path):
    import json
    from datetime import datetime, timedelta, timezone
    from mission_kernel.budget import decode_policy, default_policy_document, new_ledger, ledger_document
    run = legacy_run_cli
    run('init', 'budget fixture', '--budget-minutes', '30', cwd=tmp_path, check=True)
    result = run('budget', 'status', cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['enforcement'] == 'advisory-only'
    assert 'budget' not in json.loads(run('next', cwd=tmp_path, check=True).stdout)
    path = tmp_path / '.mission-state' / 'sessions' / 'test.json'
    doc = json.loads(path.read_bytes())
    now = datetime.now(timezone.utc).replace(microsecond=0)
    at = (now - timedelta(seconds=60)).strftime('%Y-%m-%dT%H:%M:%SZ')
    doc['budget_ledger'] = ledger_document(new_ledger(decode_policy(default_policy_document(1800)), at))
    doc['started_at'] = '2000-01-01T00:00:00Z'
    path.write_text(json.dumps(doc))
    before = path.read_bytes()
    status = json.loads(run('budget', 'status', cwd=tmp_path, check=True).stdout)
    assert status['enforcement'] == 'advisory-only'
    assert status['activation'] == 'pending'
    assert status['consumed_sec'] < 70
    assert status['provenance'] == 'experimental-initial'
    assert status['token'] == status['cost'] == {'status': 'unmeasured', 'reason': 'no-producer'}
    assert 'headroom' in status['state_capacity_verdict']
    out = json.loads(run('next', cwd=tmp_path, check=True).stdout)
    assert out['budget']['total_sec'] == 1800
    assert 'headroom' in out['budget']['state_capacity_verdict']
    assert out['budget_pressure']['basis'] == 'active-clock'
    assert out['budget_pressure']['level'] == 'ok'
    assert out['next_action'] == 'run-planner'
    assert path.read_bytes() == before
    for args in (('set', 'budget_minutes=45'), ('set', 'budget_ledger=null'),
                 ('init', 'replace ledger'), ('init', 'policy', '--budget-policy', 'policy.json')):
        assert run(*args, cwd=tmp_path).returncode != 0
        assert path.read_bytes() == before
    doc['budget_ledger'] = None
    path.write_text(json.dumps(doc))
    assert run('next', cwd=tmp_path).returncode != 0
    assert run('budget', 'status', cwd=tmp_path).returncode != 0


def test_stop_slots_are_preallocated_and_f_maximum_rows_fit_both_encodings():
    import json
    from dataclasses import asdict, replace
    from mission_kernel.budget import (BudgetStop, Exhaustion, FinalLatch, FinalRun, INT_MAX,
        PhaseCharge, ProgressSignature, Settlement, Observation, Telemetry, StopSlots,
        decode_policy, default_policy_document, new_ledger, ledger_document, decode_ledger)
    from mission_kernel.state_capacity import BUDGET_WRITE_DELTAS
    ledger = new_ledger(decode_policy(default_policy_document(1800)), '2026-01-01T00:00:00Z')
    digest = 'sha256:' + 'f' * 64
    token = 'x' * 128
    slots = StopSlots(INT_MAX, token, FinalLatch(ledger.clock.last_observed_at, token),
        Exhaustion(ledger.clock.last_observed_at, 'final-infeasible'),
        BudgetStop(token, token, ledger.clock.last_observed_at, digest, INT_MAX, INT_MAX),
        final_run=FinalRun(token, ledger.clock.last_observed_at))
    # Open-reservations in BudgetStop is a policy-bounded count.
    slots = replace(slots, budget_stop=replace(slots.budget_stop, open_reservations=3))
    full = replace(ledger, stop_slots=slots)
    assert decode_ledger({'budget_minutes': 30, 'budget_ledger': ledger_document(full)}) == full
    for pretty in (False, True):
        def size(row):
            return len(json.dumps(row, ensure_ascii=False, indent=2 if pretty else None,
                separators=None if pretty else (',', ':')).encode())
        before = {'extensions': {'budget_ledger': ledger_document(ledger)}}
        after = {'extensions': {'budget_ledger': ledger_document(full)}}
        assert size(after) <= size(before)
        assert size(before['extensions']['budget_ledger']['stop_slots']) <= BUDGET_WRITE_DELTAS['stop-slots-create']
        reservation = dict(reservation_id=token, entry='repair-disposition-run', budget_class='verification',
            target=token, operation_id=token, fencing_epoch=INT_MAX, reserved_at='0001-01-01T00:00:00Z',
            child_deadline_at='9999-12-31T23:59:46Z', settle_by='9999-12-31T23:59:59Z',
            reserved_sec=INT_MAX, reserved_bytes=INT_MAX)
        # Maximum per-field envelope is conservative even for combinations that
        # cannot occur together (seconds are also checked against timestamps).
        row = {'reservations': [reservation]}
        assert size(row) <= BUDGET_WRITE_DELTAS['reservation-row']
        settlement = Settlement(token, 'charged-full-unknown', INT_MAX, INT_MAX,
            Observation('9999-12-31T23:59:59Z', INT_MAX), Telemetry(INT_MAX, INT_MAX, INT_MAX))
        growth = {'settlements': [asdict(settlement)],
            'phase_charges': asdict(PhaseCharge(INT_MAX, INT_MAX, INT_MAX)),
            'progress': [asdict(ProgressSignature('repair-disposition-run', token, digest, digest, INT_MAX))]}
        assert size(growth) <= BUDGET_WRITE_DELTAS['settlement-row']


@pytest.mark.parametrize('extensions', [None, [], 'legacy-value', {'unrelated': True}])
def test_v4_without_ledger_does_not_interpret_legacy_extensions(extensions):
    import json
    from mission_kernel import decode_mission_state
    payload = {'schema_version': 4, 'mission': 'legacy', 'extensions': extensions}
    assert decode_mission_state(json.dumps(payload).encode()).budget.policy is None


@pytest.mark.parametrize('extra', [{}, {'extensions': {'ordinary': 1}}])
def test_v5_authoritative_consumer_preserves_budget_and_unrelated_extensions(extra):
    import json
    from mission_kernel import decode_mission_state
    from mission_kernel.budget import decode_ledger, decode_policy, default_policy_document, new_ledger, ledger_document
    from mission_persistence.authoritative_reader import authoritative_snapshot_from_document
    from .mission_state_fixture_corpus import current_v5_open_state
    payload = current_v5_open_state()
    ledger = new_ledger(decode_policy(default_policy_document(1800)), '2026-01-01T00:00:00Z')
    payload['extensions'] = {'budget_minutes': 30, 'budget_ledger': ledger_document(ledger), **extra}
    assert decode_mission_state(json.dumps(payload).encode()).budget == ledger
    snapshot = authoritative_snapshot_from_document(json.loads(json.dumps(payload)))
    assert decode_ledger(snapshot.document_copy()) == ledger


@pytest.mark.parametrize('key', ('total_sec', 'max_concurrent_dispatches', 'max_dispatches_per_phase',
    'term_grace_sec', 'kill_wait_sec', 'collect_sec', 'post_run_sec', 'adapter_call_sec',
    'cancel_call_sec', 'commit_margin_sec', 'closeout_margin_sec', 'min_dispatch_sec',
    'system_recovery_sec', 'no_progress_limit'))
@pytest.mark.parametrize('bad', (True, 1.0, '1', None, -1))
def test_all_policy_integer_fields_reject_json_type_confusion(key, bad):
    from mission_kernel.budget import BudgetError, decode_policy, default_policy_document
    wire = default_policy_document(1800)
    wire[key] = bad
    with pytest.raises(BudgetError):
        decode_policy(wire)


def test_clock_updates_exclude_halt_and_preserve_accrued_erosion():
    from dataclasses import replace
    from mission_kernel.budget import decode_policy, default_policy_document, new_ledger, observe_clock, reserve_erosion_sec, active_seconds
    ledger = new_ledger(decode_policy(default_policy_document(1800)), '2026-01-01T00:00:00Z')
    assert reserve_erosion_sec(ledger, '2026-01-01T00:20:40Z') == 10
    halted = replace(ledger, clock=observe_clock(ledger, '2026-01-01T00:20:40Z', active=False))
    assert active_seconds(halted.clock, '2026-01-01T01:00:00Z') == 1240
    resumed = replace(halted, clock=observe_clock(halted, '2026-01-01T01:00:00Z', active=True))
    assert active_seconds(resumed.clock, '2026-01-01T01:00:10Z') == 1250
    assert reserve_erosion_sec(resumed, '2026-01-01T01:00:10Z') == 20


def test_recovery_cannot_be_both_open_and_settled_but_unconfirmed_kill_stays_open():
    from mission_kernel.budget import BudgetError, decode_ledger, decode_policy, default_policy_document, new_ledger, ledger_document
    wire = ledger_document(new_ledger(decode_policy(default_policy_document(1800)), '2026-01-01T00:01:00Z'))
    reservation = dict(reservation_id='same', entry='system-recover', budget_class='final', target='dispatch',
        operation_id='op', fencing_epoch=1, reserved_at='2026-01-01T00:00:00Z',
        child_deadline_at='2026-01-01T00:00:30Z', settle_by='2026-01-01T00:00:43Z', reserved_sec=43, reserved_bytes=100)
    from mission_kernel.budget import StopSlots, SystemRecovery, DispatchReservation, stop_slots_document
    wire['stop_slots'] = stop_slots_document(StopSlots(system_recovery=SystemRecovery(0, 1, DispatchReservation(**reservation))))
    wire['settlements'] = [dict(reservation_id='same', outcome='settled', charged_sec=43, late_settlement_sec=0,
        observed={'at': '2026-01-01T00:01:00Z', 'elapsed_sec': 43},
        telemetry={'tool_calls': None, 'replays': None, 'output_bytes': None})]
    with pytest.raises(BudgetError, match='budget-settlement-open-invalid'):
        decode_ledger({'budget_minutes': 30, 'budget_ledger': wire})
    wire['settlements'][0]['outcome'] = 'kill-unconfirmed'
    wire['stop_slots'] = stop_slots_document(StopSlots(system_recovery=SystemRecovery(43, 1, DispatchReservation(**reservation))))
    assert decode_ledger({'budget_minutes': 30, 'budget_ledger': wire}).stop_slots.system_recovery.reservation.reservation_id == 'same'


def test_child_timeout_is_clipped_before_adding_to_the_calendar():
    from mission_kernel.budget import INT_MAX, child_deadlines, decode_policy, default_policy_document, new_ledger
    ledger = new_ledger(decode_policy(default_policy_document(1800)), '2026-01-01T00:00:00Z')
    assert child_deadlines(ledger, '2026-01-01T00:01:00Z', 'verification-run', 'verification', INT_MAX) == (
        '2026-01-01T00:20:06Z', '2026-01-01T00:20:30Z', 1170)


def _ledger_with_reservation(entry, budget_class):
    from mission_kernel.budget import (BUDGET_SPAWN_ENTRIES, cleanup_sec, decode_policy, default_policy_document,
                                       new_ledger, ledger_document)
    policy = decode_policy(default_policy_document(1800))
    wire = ledger_document(new_ledger(policy, '2026-01-01T00:00:00Z'))
    wire['clock']['last_observed_at'] = '2026-01-01T00:01:00Z'
    # Timings follow the entry's cleanup so only the entry/class pairing varies.
    reserved = 30 + (cleanup_sec(policy, entry) if isinstance(entry, str) and entry in BUDGET_SPAWN_ENTRIES
                     else cleanup_sec(policy, 'verification-run')) + policy.commit_margin_sec
    wire['reservations'] = [dict(
        reservation_id='reservation:1', entry=entry, budget_class=budget_class, target='command:1',
        operation_id='op:1', fencing_epoch=1, reserved_at='2026-01-01T00:00:00Z',
        child_deadline_at='2026-01-01T00:00:30Z', settle_by=f'2026-01-01T00:{reserved // 60:02d}:{reserved % 60:02d}Z',
        reserved_sec=reserved, reserved_bytes=1000)]
    if isinstance(budget_class, str) and budget_class in wire['phase_charges']:
        wire['phase_charges'][budget_class].update(reservation_count=1, open_count=1)
    return {'budget_minutes': 30, 'budget_ledger': wire}


@pytest.mark.parametrize('entry', (['verification-run'], {'verification-run': 1}))
def test_unhashable_entry_is_a_budget_error_not_a_type_error(entry):
    import json
    from mission_kernel import decode_mission_state
    from mission_kernel.budget import BudgetError, cleanup_sec, decode_ledger, decode_policy, default_policy_document
    from mission_kernel.errors import MissionStateDecodeError
    from .mission_state_fixture_corpus import current_v5_open_state
    with pytest.raises(BudgetError, match='budget-entry-invalid'):
        cleanup_sec(decode_policy(default_policy_document(1800)), entry)
    document = _ledger_with_reservation(entry, 'verification')
    with pytest.raises(BudgetError, match='budget-entry-invalid'):
        decode_ledger(document)
    payload = current_v5_open_state()
    payload['extensions'] = document
    with pytest.raises(MissionStateDecodeError, match='budget-entry-invalid'):
        decode_mission_state(json.dumps(payload).encode())


@pytest.mark.parametrize('entry, budget_class, accepted', (
    ('repair-reverify', 'repair', True),
    ('repair-disposition-run', 'repair', True),
    ('verification-run', 'verification', True),
    ('verification-run', 'final', True),
    ('recover', 'repair', True),
    ('repair-reverify', 'verification', False),
    ('repair-reverify', 'final', False),
    ('verification-run', 'repair', False),
    ('invoke-command', 'repair', False),
    ('fresh-review-run', 'repair', False),
    ('verification-run', ['verification'], False),
))
def test_stored_reservation_class_follows_the_entry(entry, budget_class, accepted):
    # design 881 §3.1: only repair entries use the repair class; recovery inherits.
    from mission_kernel.budget import BudgetError, decode_ledger
    document = _ledger_with_reservation(entry, budget_class)
    if accepted:
        assert decode_ledger(document).reservations[0].budget_class == budget_class
    else:
        with pytest.raises(BudgetError, match='budget-class-invalid'):
            decode_ledger(document)


@pytest.mark.parametrize('minutes', ('absent', None))
def test_ledger_without_budget_minutes_is_rejected(minutes):
    from mission_kernel.budget import BudgetError, decode_ledger
    document = _ledger_with_reservation('verification-run', 'verification')
    if minutes == 'absent':
        del document['budget_minutes']
    else:
        document['budget_minutes'] = minutes
    with pytest.raises(BudgetError, match='budget-total-mismatch'):
        decode_ledger(document)
    with pytest.raises(BudgetError, match='budget-total-mismatch'):
        decode_ledger({'schema_version': 5, 'extensions': document})


@pytest.mark.parametrize('key, maximum', (
    ('max_concurrent_dispatches', 8), ('max_dispatches_per_phase', 64), ('no_progress_limit', 32),
    ('closeout_margin_sec', 86400), ('kill_wait_sec', 86400)))
def test_policy_integers_are_bounded(key, maximum):
    from mission_kernel.budget import BudgetError, decode_policy, default_policy_document
    wire = default_policy_document(10 ** 6)
    wire[key] = maximum + 1
    with pytest.raises(BudgetError, match='budget-integer-invalid'):
        decode_policy(wire)
    if key.startswith('max_') or key == 'no_progress_limit':
        wire[key] = maximum
        assert getattr(decode_policy(wire), key) == maximum


@pytest.mark.parametrize('schema', (4, 5))
def test_direct_session_read_rejects_duplicate_keys_in_a_budget_document(tmp_path, schema):
    import json
    from mission_kernel.budget import decode_policy, default_policy_document, new_ledger, ledger_document
    from mission_kernel.errors import MissionStateDecodeError
    from mission_persistence.authoritative_reader import read_session_json
    ledger = json.dumps(ledger_document(new_ledger(decode_policy(default_policy_document(1800)), '2026-01-01T00:00:00Z')))
    if schema == 4:
        source = '{"schema_version": 4, "budget_minutes": 1, "budget_minutes": 30, "budget_ledger": %s}' % ledger
    else:
        source = ('{"schema_version": 5, "extensions": {"budget_minutes": 1, "budget_minutes": 30, '
                  '"budget_ledger": %s}}' % ledger)
    path = tmp_path / 'state.json'
    path.write_text(source)
    with pytest.raises(MissionStateDecodeError, match='duplicate-json-key'):
        read_session_json(path)
    # Without a ledger the historical legacy tolerance is unchanged.
    path.write_text('{"schema_version": 4, "phase": "planning", "phase": "executing"}')
    assert read_session_json(path)['phase'] == 'executing'


def test_ledger_placement_and_time_digits_are_closed():
    from mission_kernel.budget import BudgetError, decode_ledger, decode_policy, default_policy_document, new_ledger, ledger_document
    wire = ledger_document(new_ledger(decode_policy(default_policy_document(1800)), '2026-01-01T00:00:00Z'))
    with pytest.raises(BudgetError, match='budget-ledger-location-invalid'):
        decode_ledger({'schema_version': 5, 'budget_minutes': 30, 'budget_ledger': wire, 'extensions': {}})
    # A v5 document without extensions has no ledger, like the fresh-review projection.
    assert decode_ledger({'schema_version': 5, 'mission': 'x'}).policy is None
    with pytest.raises(BudgetError, match='budget-ledger-shape-invalid'):
        decode_ledger({'schema_version': 5, 'extensions': []})
    wire['clock']['last_observed_at'] = '٢٠٢٦-01-01T00:00:00Z'
    with pytest.raises(BudgetError):
        decode_ledger({'budget_minutes': 30, 'budget_ledger': wire})


def test_closeout_margin_exhausts_only_without_an_open_final_reservation():
    from dataclasses import replace
    from mission_kernel.budget import DispatchReservation, decode_policy, default_policy_document, exhaustion, new_ledger
    ledger = new_ledger(decode_policy(default_policy_document(1800)), '2026-01-01T00:00:00Z')
    at = '2026-01-01T00:29:45Z'  # inside the 30 s closeout margin, before the overall deadline
    assert exhaustion(ledger, at).cause == 'closeout-margin'
    final = DispatchReservation('reservation:1', 'verification-run', 'final', 'command:1', 'op:1', 1,
                                '2026-01-01T00:29:40Z', '2026-01-01T00:29:50Z', '2026-01-01T00:29:58Z', 18, 0)
    opened = replace(ledger, reservations=(final,))
    result = exhaustion(opened, at)
    assert result is None or result.cause != 'closeout-margin'


@pytest.mark.parametrize('source, rejected', (
    # The ledger sits in a shadowed duplicate extensions object.
    ('{"schema_version":5,"extensions":{"budget_minutes":30,"budget_ledger":{}},"extensions":{}}', True),
    # The same with a non-integer v5 spelling and a ledger that is not the last key.
    ('{"schema_version":5.0,"extensions":{"budget_ledger":{},"budget_minutes":30},"extensions":{}}', True),
    ('{"schema_version":5e0,"extensions":{"budget_ledger":{},"budget_minutes":30},"extensions":{}}', True),
    # A shadowed duplicate top-level ledger.
    ('{"schema_version":4,"budget_ledger":{},"budget_ledger":null}', True),
    # The last schema_version decides the placement, as in the collapsed document.
    ('{"schema_version":5.0,"schema_version":4,"mission":"x","phase":"planning","phase":"executing",'
     '"extensions":{"budget_ledger":1}}', False),
    ('{"schema_version":4,"schema_version":5,"extensions":{"budget_ledger":{}},"extensions":{}}', True),
    # v4 extensions.budget_ledger is user data; legacy duplicate tolerance stays.
    ('{"schema_version":4,"mission":"x","phase":"planning","phase":"executing","extensions":{"budget_ledger":1}}', False),
))
def test_budget_duplicate_detection_uses_the_uncollapsed_document(tmp_path, source, rejected):
    from mission_kernel.errors import MissionStateDecodeError
    from mission_persistence.authoritative_reader import read_session_json
    path = tmp_path / 'state.json'
    path.write_text(source)
    if rejected:
        with pytest.raises(MissionStateDecodeError, match='duplicate-json-key'):
            read_session_json(path)
    else:
        assert read_session_json(path)['phase'] == 'executing'


def test_budget_status_reads_the_snapshot_without_entering_the_write_path(tmp_path, monkeypatch):
    # A held lease or a pending transaction must not affect a read-only query.
    import json
    from types import SimpleNamespace
    from mission_application.budget import run_budget_status_cli
    from mission_kernel.budget import decode_policy, default_policy_document, new_ledger, ledger_document
    state = tmp_path / 'state.json'
    state.write_text('{}')
    document = {'budget_minutes': 30, 'budget_ledger': ledger_document(
        new_ledger(decode_policy(default_policy_document(1800)), '2026-01-01T00:00:00Z'))}

    def write_path(*_args, **_kwargs):
        raise AssertionError('budget status entered the repository write path')

    def fail(code, status):
        raise SystemExit((code, status))
    monkeypatch.chdir(tmp_path)
    services = SimpleNamespace(resolve_state_file=lambda root: state, repository=write_path,
                               load_snapshot=lambda path: (None, document), capacity_status=lambda path: None,
                               now=lambda: '2026-01-01T00:01:00Z', fail=fail)
    status = json.loads(run_budget_status_cli(None, services))
    assert status['total_sec'] == 1800 and status['enforcement'] == 'advisory-only'
