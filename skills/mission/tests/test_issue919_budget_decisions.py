"""F1 reducers keep budget policy state durable and deterministic."""
import json
import pytest
from .mission_state_fixture_corpus import current_v5_open_state
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from mission_kernel import commands, decode_mission_state, decode_snapshot, project_legacy_document
from mission_kernel.budget import (
    BUDGET_SPAWN_ENTRIES, BudgetError, INT_MAX, Observation, ProgressSignature,
    Settlement, StopSlots, Telemetry, decode_ledger, decode_policy,
    default_policy_document, ledger_document, new_ledger,
)
from mission_kernel.budget_decisions import (
    CapacityEvidence, admit, budget_class, enter_final, expire_reservations,
    reserve, settle, stop, with_clock,
)
from mission_kernel.codec_v5 import encode_v5_state
from mission_kernel.commands import (
    BudgetStop, EnterFinalPhase, MarkHalt, MarkPass, Reactivate,
    RecordBudgetRefusal, ReserveDispatchBudget, SettleDispatchBudget, encode_kernel_command,
)
from mission_kernel.model import HaltCategory, Phase
from mission_kernel.state_capacity import (
    BUDGET_RESERVATION_ROW_DELTA, BUDGET_SETTLEMENT_ROW_DELTA, StateEncoding, state_capacity_verdict,
)
from mission_kernel.transitions import decide


def _state():
    raw = current_v5_open_state()
    raw['extensions']['budget_minutes'] = 30
    raw['extensions']['budget_ledger'] = ledger_document(
        new_ledger(decode_policy(default_policy_document(1800)), '2026-01-01T00:00:00Z'))
    global _GUIDANCE
    snapshot = decode_snapshot(json.dumps(raw).encode())
    _GUIDANCE = snapshot.guidance
    return snapshot.state


def _reserve(at, entry, target, operation, bytes_=0):
    return ReserveDispatchBudget(at, entry, target, operation, 1, 60, bytes_, 'sha256:' + 'a' * 64,
                                 guidance=_GUIDANCE)


def _settlement(at, reservation_id, outcome, elapsed_sec, **kwargs):
    return SettleDispatchBudget(at, reservation_id, outcome, elapsed_sec,
                               'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64, **kwargs)


def test_reserve_settle_and_final_latch_keep_projection_and_backing_together():
    state = _state()
    reserve = _reserve('2026-01-01T00:01:00Z', 'verification-run', 'target:one', 'op:one', 42)
    reserved = decide(state, reserve)
    assert reserved.accepted
    state = reserved.transition.new_state
    row = state.budget.reservations[0]
    assert row.budget_class == 'verification'
    assert state.extensions.thaw()['budget_ledger']['reservations'][0]['reservation_id'] == row.reservation_id
    final = decide(state, EnterFinalPhase('2026-01-01T00:01:01Z', 'explicit'))
    assert final.accepted
    state = final.transition.new_state
    settled = decide(state, _settlement('2026-01-01T00:01:02Z', row.reservation_id, 'settled', 1))
    assert settled.accepted
    assert settled.transition.new_state.budget.stop_slots.final_run is None
    final_reservation = _reserve('2026-01-01T00:01:03Z', 'verification-run', 'target:two', 'op:two', 42)
    final_state = decide(settled.transition.new_state, final_reservation).transition.new_state
    final_row = final_state.budget.reservations[0]
    final_settlement = decide(final_state, SettleDispatchBudget('2026-01-01T00:01:04Z', final_row.reservation_id,
        'settled', 1, 'sha256:' + 'c' * 64, 'sha256:' + 'd' * 64, completed=True))
    assert final_settlement.accepted
    assert final_settlement.transition.new_state.budget.stop_slots.final_run.reservation_id == final_row.reservation_id


def test_reserve_uses_trusted_phase_not_caller_and_is_idempotent_per_operation():
    state = _state()
    command = _reserve('2026-01-01T00:01:00Z', 'invoke-command', 'target:one', 'op:one')
    first = decide(state, command)
    assert first.accepted
    again = decide(first.transition.new_state, command)
    assert again.accepted
    assert again.transition.new_state == first.transition.new_state


def test_lifecycle_closes_opens_clock_and_pass_rejects_open_or_exhausted_budget():
    state = _state()
    halted = decide(state, MarkHalt(HaltCategory.PARTIAL_DONE, 'pause', at='2026-01-01T00:01:00Z'))
    assert halted.accepted and halted.transition.new_state.budget.clock.opened_at is None
    resumed = decide(halted.transition.new_state, Reactivate(HaltCategory.PARTIAL_DONE, 'continue', True,
                                                              Phase.PLANNING, at='2026-01-01T00:02:00Z'))
    assert resumed.accepted and resumed.transition.new_state.budget.clock.opened_at == '2026-01-01T00:02:00Z'
    held = decide(resumed.transition.new_state, _reserve('2026-01-01T00:02:01Z', 'verification-run', 'target:one', 'op:one')).transition.new_state
    rejected = decide(held, MarkPass(force=True, force_approval_verified=True, artifact_gate_satisfied=True,
                                     at='2026-01-01T00:02:02Z'))
    assert rejected.rejection.code == 'budget-dispatch-unsettled'
    exhausted = decide(_state(), MarkPass(force=True, force_approval_verified=True, artifact_gate_satisfied=True,
                                         at='2026-01-01T00:30:00Z'))
    assert exhausted.rejection.code == 'budget-exhausted'


def test_verification_retries_reserve_again_and_kill_unconfirmed_remains_open():
    state = _state()
    first = _reserve('2026-01-01T00:01:00Z', 'verification-run', 'target:one', 'op:one')
    held = decide(state, first).transition.new_state
    second = _reserve('2026-01-01T00:01:01Z', 'verification-run', 'target:one', 'op:one')
    retried = decide(held, second).transition.new_state
    assert len(retried.budget.reservations) == 2
    row = retried.budget.reservations[0]
    killed = decide(retried, _settlement('2026-01-01T00:01:02Z', row.reservation_id, 'kill-unconfirmed', None))
    assert killed.accepted
    ledger = killed.transition.new_state.budget
    assert any(item.reservation_id == row.reservation_id for item in ledger.reservations)
    assert ledger.settlements[-1].charged_sec == row.reserved_sec


def test_admission_refuses_insufficient_trusted_capacity_evidence():
    state = _state()
    refused = admit(state.budget, state, '2026-01-01T00:00:01Z', entry='verification-run',
        target='target:one', operation_id='op:one', fencing_epoch=1, policy_timeout=60,
        reserved_bytes=8, candidate_digest='sha256:' + 'a' * 64,
        capacity=CapacityEvidence(headroom=7, reservation_delta=1))
    assert refused.reason == 'budget-state-capacity-exhausted'
    assert refused.ledger.stop_slots.last_refusal == refused.reason


def test_v5_reserve_without_snapshot_guidance_fails_closed():
    state = _state()
    result = decide(state, ReserveDispatchBudget('2026-01-01T00:00:01Z', 'verification-run', 'target:one',
        'op:one', 1, 60, 0, 'sha256:' + 'a' * 64))
    assert result.rejection.code == 'budget-capacity-evidence-required'


def test_budget_commands_reject_absent_policy_without_mutation():
    state = decode_mission_state(__import__('json').dumps(current_v5_open_state()).encode())
    request = ReserveDispatchBudget('2026-01-01T00:00:01Z', 'verification-run', 'target:one', 'op:one', 1, 60, 0, 'sha256:' + 'a' * 64)
    for command in (request, _settlement('2026-01-01T00:00:01Z', 'reservation:x', 'settled', 1),
                    EnterFinalPhase('2026-01-01T00:00:01Z', 'explicit'), BudgetStop('2026-01-01T00:00:01Z', 'all', 'exhausted'),
                    RecordBudgetRefusal('2026-01-01T00:00:01Z', request)):
        result = decide(state, command)
        assert not result.accepted and result.rejection.code == 'budget-policy-absent'


@pytest.mark.parametrize('schema', [4, 5])
def test_all_budget_mutations_roundtrip(schema):
    """Every F1 mutation leaves a decoder-valid durable projection."""
    if schema == 5:
        state = _state()
        guidance = _GUIDANCE
    else:
        raw = {'schema_version': 4, 'phase': 'planning', 'loop_active': True, 'budget_minutes': 30,
               'budget_ledger': ledger_document(new_ledger(decode_policy(default_policy_document(1800)), '2026-01-01T00:00:00Z'))}
        state, guidance = decode_mission_state(json.dumps(raw).encode()), None
    def reload(value):
        if schema == 5:
            return decode_snapshot(encode_v5_state(value, guidance)).state
        return decode_mission_state(project_legacy_document(value))
    command = ReserveDispatchBudget('2026-01-01T00:00:01Z', 'verification-run', 'target:one', 'op:one', 1, 60, 0,
        'sha256:' + 'a' * 64, guidance=guidance)
    result = decide(state, command)
    assert result.accepted
    state = reload(result.transition.new_state)
    row = state.budget.reservations[0]
    result = decide(state, _settlement('2026-01-01T00:00:02Z', row.reservation_id, 'settled', 1))
    assert result.accepted
    state = reload(result.transition.new_state)
    result = decide(state, EnterFinalPhase('2026-01-01T00:00:03Z', 'explicit'))
    assert result.accepted
    state = reload(result.transition.new_state)
    assert state.budget.stop_slots.final_latch is not None
    refusal = ReserveDispatchBudget('2026-01-01T00:29:59Z', 'verification-run', 'target:refused',
        'op:refused', 1, 60, 0, 'sha256:' + 'c' * 64, guidance=guidance)
    result = decide(state, RecordBudgetRefusal(refusal.at, refusal))
    assert result.accepted
    state = reload(result.transition.new_state)
    assert state.budget.stop_slots.last_refusal is not None
    result = decide(state, BudgetStop('2026-01-01T00:30:00Z', 'all', 'exhausted'))
    assert result.accepted
    stopped = reload(result.transition.new_state)
    assert stopped.budget.stop_slots.budget_stop is not None
    assert stopped.budget.stop_slots.budget_stop.ledger_digest.startswith('sha256:')


def test_refusal_recomputes_the_identical_capacity_and_latch_snapshot_as_reserve():
    state = _state()
    request = _reserve('2026-01-01T00:00:01Z', 'verification-run', 'target:one', 'op:one', 4 * 1024 * 1024)
    reserve = decide(state, request)
    assert reserve.rejection.code == 'budget-state-capacity-exhausted'
    refusal = decide(state, RecordBudgetRefusal(request.at, request))
    assert refusal.accepted
    ledger = refusal.transition.new_state.budget
    assert ledger.stop_slots.last_refusal == reserve.rejection.code
    assert not ledger.reservations


def test_refusal_never_records_a_non_admission_failure():
    halted = decide(_state(), MarkHalt(HaltCategory.PARTIAL_DONE, 'pause', at='2026-01-01T00:00:01Z')).transition.new_state
    request = _reserve('2026-01-01T00:00:02Z', 'verification-run', 'target:one', 'op:one')
    result = decide(halted, RecordBudgetRefusal(request.at, request))
    assert result.rejection.code == 'budget-loop-inactive'


def test_system_recovery_uses_its_bounded_allowance_only_after_normal_recovery_time_fails():
    state = _state()
    held_command = ReserveDispatchBudget('2026-01-01T00:01:00Z', 'fresh-review-run', 'target:held', 'op:held',
        1, 1200, 0, 'sha256:' + 'a' * 64, guidance=_GUIDANCE)
    held = decide(state, held_command).transition.new_state
    recovery = _reserve('2026-01-01T00:20:29Z', 'system-recover', 'target:held', 'op:recover')
    result = decide(held, recovery)
    assert result.accepted
    assert result.transition.new_state.budget.stop_slots.system_recovery.reservation is not None
    refused = decide(result.transition.new_state, RecordBudgetRefusal(recovery.at, replace(recovery, operation_id='op:again')))
    assert refused.accepted and refused.transition.new_state.budget.stop_slots.last_refusal == 'budget-recovery-open'
    expired_state = decide(result.transition.new_state, EnterFinalPhase('2026-01-01T00:22:00Z', 'explicit')).transition.new_state
    assert expired_state.budget.stop_slots.system_recovery.reservation is None
    assert expired_state.budget.stop_slots.system_recovery.used_sec > 0
    expired = _reserve('2026-01-01T00:30:00Z', 'system-recover', 'target:held', 'op:too-late')
    result = decide(held, expired)
    assert not result.accepted


def test_repair_entries_fail_closed_without_an_e2_attempt_and_budget_class_has_no_caller_phase():
    state = _state()
    for entry in ('repair-reverify', 'repair-disposition-run', 'repair-begin'):
        refusal = admit(state.budget, state, '2026-01-01T00:00:01Z', entry=entry,
            target='target:repair', operation_id='op:repair', fencing_epoch=1, policy_timeout=60,
            reserved_bytes=0, candidate_digest='sha256:' + 'a' * 64)
        assert refusal.reason == 'budget-repair-attempt-missing'
    assert budget_class('verification-run', state, state.budget, '2026-01-01T00:00:01Z') == 'verification'


def test_kill_unconfirmed_charges_once_and_confirmation_closes_without_recharge():
    state = decide(_state(), _reserve('2026-01-01T00:01:00Z', 'verification-run', 'target:one', 'op:one')).transition.new_state
    row = state.budget.reservations[0]
    kill = _settlement('2026-01-01T00:01:01Z', row.reservation_id, 'kill-unconfirmed', None)
    killed = decide(state, kill).transition.new_state
    charged = killed.budget.phase_charges[2].charged_sec
    repeated = decide(killed, kill)
    assert repeated.accepted and repeated.transition.new_state == killed
    assert killed.budget.phase_charges[2].charged_sec == charged
    confirmed = decide(killed, _settlement('2026-01-01T00:01:02Z', row.reservation_id, 'settled', 1, completed=True))
    assert confirmed.accepted
    ledger = confirmed.transition.new_state.budget
    assert not ledger.reservations and ledger.phase_charges[2].charged_sec == charged
    for bad in (
        SettleDispatchBudget('2026-01-01T00:01:03Z', row.reservation_id, 'settled', 1,
            'not-a-digest', 'sha256:' + 'b' * 64),
        _settlement('2026-01-01T00:01:03Z', row.reservation_id, 'settled', 1, tool_calls=True),
        _settlement('2026-01-01T00:01:03Z', row.reservation_id, 'settled', 1, completed=1),
    ):
        assert not decide(confirmed.transition.new_state, bad).accepted


def test_expired_open_reservation_is_charged_full_unknown():
    state = _state()
    admitted = admit(state.budget, state, '2026-01-01T00:01:00Z', entry='verification-run',
        target='target:one', operation_id='op:one', fencing_epoch=1, policy_timeout=60,
        reserved_bytes=0, candidate_digest='sha256:' + 'a' * 64)
    held = reserve(state.budget, admitted)
    expired = admit(held, state, '2026-01-01T00:20:31Z', entry='verification-run',
        target='target:two', operation_id='op:two', fencing_epoch=1, policy_timeout=60,
        reserved_bytes=0, candidate_digest='sha256:' + 'b' * 64)
    assert expired.ledger.settlements[-1].outcome == 'charged-full-unknown'
    assert expired.ledger.settlements[-1].charged_sec == held.reservations[0].reserved_sec


def test_reserve_command_serialization_uses_closed_guidance_projection_not_snapshot_binding():
    _state()
    guidance_one = _GUIDANCE
    _state()
    guidance_two = _GUIDANCE
    object.__setattr__(guidance_one, '_snapshot_binding', object())
    object.__setattr__(guidance_two, '_snapshot_binding', object())
    one = ReserveDispatchBudget('2026-01-01T00:01:00Z', 'verification-run', 'target:one', 'op:one', 1, 60, 0,
        'sha256:' + 'a' * 64, guidance=guidance_one)
    two = ReserveDispatchBudget('2026-01-01T00:01:00Z', 'verification-run', 'target:one', 'op:one', 1, 60, 0,
        'sha256:' + 'a' * 64, guidance=guidance_two)
    assert encode_kernel_command(one) == encode_kernel_command(two)


def test_direct_refusals_keep_a_closed_projection_and_clock_regression_keeps_input_unchanged():
    state = _state()
    expired = admit(state.budget, state, '2026-01-01T00:30:00Z', entry='verification-run',
        target='target:one', operation_id='op:one', fencing_epoch=1, policy_timeout=60,
        reserved_bytes=0, candidate_digest='sha256:' + 'a' * 64)
    assert expired.reason == 'budget-exhausted'
    assert decode_ledger({'budget_minutes': 30, 'budget_ledger': ledger_document(expired.ledger)}) == expired.ledger
    regressed = admit(state.budget, state, '2025-12-31T23:59:59Z', entry='verification-run',
        target='target:one', operation_id='op:one', fencing_epoch=1, policy_timeout=60,
        reserved_bytes=0, candidate_digest='sha256:' + 'a' * 64)
    assert regressed.reason == 'budget-clock-regressed' and regressed.ledger == state.budget
    saturated = replace(state.budget, stop_slots=replace(state.budget.stop_slots, refusal_count=INT_MAX))
    refusal = admit(saturated, state, '2026-01-01T00:00:01Z', entry='verification-run',
        target='target:one', operation_id='op:one', fencing_epoch=1, policy_timeout=60,
        reserved_bytes=1, candidate_digest='sha256:' + 'a' * 64,
        capacity=CapacityEvidence(0, 1))
    assert refusal.ledger.stop_slots.refusal_count == INT_MAX


def test_completed_dispatch_progress_is_not_a_system_recovery_target():
    state = decide(_state(), _reserve('2026-01-01T00:01:00Z', 'verification-run', 'target:done', 'op:done')).transition.new_state
    row = state.budget.reservations[0]
    state = decide(state, _settlement('2026-01-01T00:01:01Z', row.reservation_id, 'settled', 1, completed=True)).transition.new_state
    recovery = decide(state, _reserve('2026-01-01T00:01:02Z', 'system-recover', 'target:done', 'op:recover'))
    assert recovery.rejection.code == 'budget-recovery-target-missing'


def test_settled_dispatch_does_not_block_a_new_operation_and_candidate():
    state = _state()
    first = _reserve('2026-01-01T00:01:00Z', 'fresh-review-run', 'target:next', 'op:one', 0)
    state = decide(state, first).transition.new_state
    row = state.budget.reservations[0]
    state = decide(state, _settlement('2026-01-01T00:01:01Z', row.reservation_id, 'settled', 1)).transition.new_state
    fresh = ReserveDispatchBudget('2026-01-01T00:01:02Z', 'fresh-review-run', 'target:next', 'op:two', 1, 60, 0,
        'sha256:' + 'c' * 64, guidance=_GUIDANCE)
    assert decide(state, fresh).accepted


def test_settled_de_operation_replays_without_a_new_reservation():
    state = _state()
    first = _reserve('2026-01-01T00:01:00Z', 'fresh-review-run', 'target:replay', 'op:one', 0)
    state = decide(state, first).transition.new_state
    row = state.budget.reservations[0]
    state = decide(state, _settlement('2026-01-01T00:01:01Z', row.reservation_id, 'settled', 1)).transition.new_state
    replay = _reserve('2026-01-01T00:01:02Z', 'fresh-review-run', 'target:replay', 'op:one', 4 * 1024 * 1024)
    result = decide(state, replay)
    assert result.accepted and not result.transition.new_state.budget.reservations
    event = result.transition.events[0]
    assert event.type == 'budget-dispatch-replayed'
    assert event.settlement.reservation_id == row.reservation_id


def test_spawn_entry_inventory_is_closed_and_inert_until_f2_coverage():
    expected = {
        'verification-run', 'repair-reverify', 'invoke-command', 'invoke-prepared',
        'verify-approval', 'force-approval', 'fresh-review-run',
        'repair-disposition-run', 'recover', 'system-recover', 'repair-begin',
    }
    assert set(BUDGET_SPAWN_ENTRIES) == expected
    assert {key for key, value in BUDGET_SPAWN_ENTRIES.items() if value == 'pending'} == expected - {'invoke-command', 'invoke-prepared', 'verification-run', 'verify-approval', 'force-approval'}
    with pytest.raises(TypeError):
        BUDGET_SPAWN_ENTRIES['verification-run'] = 'covered'


def test_de_replay_fails_closed_when_a_retained_legacy_reservation_id_is_unverifiable():
    state = _state()
    ledger = replace(state.budget, settlements=(Settlement('reservation:legacy', 'settled', 1, 0,
        Observation('2026-01-01T00:00:00Z', 1), Telemetry(None, None, None)),))
    refusal = admit(ledger, state, '2026-01-01T00:00:01Z', entry='fresh-review-run', target='target:legacy',
        operation_id='op:next', fencing_epoch=1, policy_timeout=60, reserved_bytes=0,
        candidate_digest='sha256:' + 'a' * 64)
    assert refusal.reason == 'budget-operation-replay-unverifiable'


def test_system_recovery_refuses_open_non_adapter_dispatch():
    state = _state()
    held = ReserveDispatchBudget('2026-01-01T00:01:00Z', 'verification-run', 'target:b', 'op:b', 1, 1200, 0,
        'sha256:' + 'a' * 64, guidance=_GUIDANCE)
    state = decide(state, held).transition.new_state
    recovery = _reserve('2026-01-01T00:20:29Z', 'system-recover', 'target:b', 'op:recover')
    assert decide(state, recovery).rejection.code == 'budget-recovery-target-missing'


def test_expiry_only_charges_after_settle_by_boundary():
    state = _state()
    admitted = admit(state.budget, state, '2026-01-01T00:01:00Z', entry='verification-run',
        target='target:boundary', operation_id='op:boundary', fencing_epoch=1, policy_timeout=60,
        reserved_bytes=0, candidate_digest='sha256:' + 'a' * 64)
    held = reserve(state.budget, admitted)
    settle_by = held.reservations[0].settle_by
    assert expire_reservations(held, settle_by) == held
    after = (datetime.fromisoformat(settle_by.replace('Z', '+00:00')) + timedelta(seconds=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    assert expire_reservations(held, after).settlements[-1].outcome == 'charged-full-unknown'


def test_duplicate_settlement_validates_stored_outcome_and_telemetry():
    state = decide(_state(), _reserve('2026-01-01T00:01:00Z', 'verification-run', 'target:duplicate', 'op:one')).transition.new_state
    row = state.budget.reservations[0]
    settled = decide(state, _settlement('2026-01-01T00:01:01Z', row.reservation_id, 'settled', 1)).transition.new_state
    invalid = decide(settled, _settlement('2026-01-01T00:01:02Z', row.reservation_id, 'unknown-outcome', 1))
    assert invalid.rejection.code == 'budget-settlement-outcome-invalid'
    contradictory = decide(settled, _settlement('2026-01-01T00:01:01Z', row.reservation_id, 'settled', 1, tool_calls=1))
    assert contradictory.rejection.code == 'budget-settlement-duplicate'


def test_system_recovery_kill_charges_allowance_once_and_confirmation_preserves_charge():
    state = _state()
    held = ReserveDispatchBudget('2026-01-01T00:01:00Z', 'fresh-review-run', 'target:system', 'op:held',
        1, 1200, 0, 'sha256:' + 'a' * 64, guidance=_GUIDANCE)
    state = decide(state, held).transition.new_state
    recovery = _reserve('2026-01-01T00:20:29Z', 'system-recover', 'target:system', 'op:recover')
    state = decide(state, recovery).transition.new_state
    row = state.budget.stop_slots.system_recovery.reservation
    killed = decide(state, _settlement('2026-01-01T00:20:30Z', row.reservation_id, 'kill-unconfirmed', None))
    assert killed.accepted
    held_ledger = killed.transition.new_state.budget
    assert held_ledger.stop_slots.system_recovery.reservation is not None
    assert held_ledger.stop_slots.system_recovery.used_sec == row.reserved_sec
    confirmed = decide(killed.transition.new_state, _settlement('2026-01-01T00:20:31Z', row.reservation_id, 'settled', 1))
    assert confirmed.accepted
    closed = confirmed.transition.new_state.budget.stop_slots.system_recovery
    assert closed.reservation is None and closed.used_sec == row.reserved_sec


@pytest.mark.parametrize('target,operation,epoch,timeout,bytes_', [
    ('', 'op:one', 1, 1, 0), ('target:one', '', 1, 1, 0),
    ('target:one', 'op:one', True, 1, 0), ('target:one', 'op:one', 0, 1, 0),
    ('target:one', 'op:one', 1, True, 0), ('target:one', 'op:one', 1, 1, -1),
])
def test_admission_rejects_invalid_closed_request_scalars(target, operation, epoch, timeout, bytes_):
    state = _state()
    refused = admit(state.budget, state, '2026-01-01T00:00:01Z', entry='verification-run', target=target,
        operation_id=operation, fencing_epoch=epoch, policy_timeout=timeout, reserved_bytes=bytes_,
        candidate_digest='sha256:' + 'a' * 64)
    assert refused.reason.startswith('budget-')
    assert refused.ledger.stop_slots.refusal_count == 1


def test_late_settlement_is_never_charged_below_the_reservation():
    # design 881 §4.2: lateness is recorded; the charge does not drop below the reserved seconds.
    state = decide(_state(), _reserve('2026-01-01T00:01:00Z', 'verification-run', 'target:one', 'op:one')).transition.new_state
    row = state.budget.reservations[0]
    on_time = decide(state, _settlement('2026-01-01T00:01:05Z', row.reservation_id, 'settled', 1))
    assert on_time.transition.new_state.budget.settlements[-1].charged_sec == 1
    late = decide(state, _settlement('2026-01-01T00:10:00Z', row.reservation_id, 'settled', 1))
    assert late.accepted
    record = late.transition.new_state.budget.settlements[-1]
    assert record.charged_sec == row.reserved_sec and record.late_settlement_sec > 0


@pytest.mark.parametrize('make', (
    lambda c: c.EnterFinalPhase('2026-01-01T00:01:00Z', ''),
    lambda c: c.EnterFinalPhase('2026-01-01T00:01:00Z', 'a\nb'),
    lambda c: c.EnterFinalPhase('2026-01-01T00:01:00Z', 'x' * 100000),
    lambda c: c.MarkPass(force=True, force_approval_verified=True, artifact_gate_satisfied=True, at='garbage'),
    lambda c: c.MarkPass(force=True, force_approval_verified=True, artifact_gate_satisfied=True,
                         at='2026-01-01T09:00:00+09:00'),
))
def test_malformed_budget_command_input_is_a_rejection_not_an_exception(make):
    decision = decide(_state(), make(commands))
    assert not decision.accepted and decision.rejection.code.startswith('budget-')


def test_reactivate_before_the_halt_time_is_a_rejection():
    halted = decide(_state(), MarkHalt(HaltCategory.PARTIAL_DONE, 'pause', at='2026-01-01T00:05:00Z')).transition.new_state
    decision = decide(halted, Reactivate(HaltCategory.PARTIAL_DONE, 'continue', True, Phase.PLANNING,
                                         at='2026-01-01T00:04:00Z'))
    assert not decision.accepted and decision.rejection.code == 'budget-clock-regressed'


@pytest.mark.parametrize('system', [False, True])
def test_open_kill_charge_survives_settlement_window_and_expiry(system):
    state = _state()
    ledger = replace(state.budget, policy=replace(state.budget.policy, max_dispatches_per_phase=64))
    def hold(ledger, at, entry, target, timeout=60):
        return reserve(ledger, admit(ledger, state, at, entry=entry, target=target, operation_id=target,
            fencing_epoch=1, policy_timeout=timeout, reserved_bytes=0, candidate_digest='sha256:' + 'a' * 64))
    ledger = hold(ledger, '2026-01-01T00:01:00Z', 'fresh-review-run', 'target:held', 1200)
    if system:
        ledger = hold(ledger, '2026-01-01T00:20:29Z', 'system-recover', 'target:held')
    row = ledger.stop_slots.system_recovery.reservation if system else ledger.reservations[0]
    at = '2026-01-01T00:20:30Z' if system else '2026-01-01T00:01:01Z'
    kill = dict(reservation_id=row.reservation_id, outcome='kill-unconfirmed', elapsed_sec=None,
                candidate_digest='sha256:' + 'a' * 64, result_digest='sha256:' + 'b' * 64)
    ledger = enter_final(settle(ledger, at, **kill), at, "explicit")
    for i in range(33):
        ledger = hold(ledger, at, 'verification-run', f'target:{i}')
        other = ledger.reservations[-1]
        ledger = settle(ledger, at, **dict(kill, reservation_id=other.reservation_id, outcome='settled', elapsed_sec=1))
    assert len(ledger.settlements) == 32
    assert any(s.reservation_id == row.reservation_id for s in ledger.settlements)
    assert settle(ledger, at, **kill) == ledger
    ledger = with_clock(expire_reservations(ledger, '2026-01-01T00:29:00Z'), '2026-01-01T00:29:00Z', active=True)
    assert decode_ledger({'budget_minutes': 30, 'budget_ledger': ledger_document(ledger)}) == ledger
    assert row == (ledger.stop_slots.system_recovery.reservation if system else ledger.reservations[0])
    confirmed = settle(ledger, '2026-01-01T00:29:00Z', **dict(kill, outcome='settled', elapsed_sec=1))
    assert confirmed.phase_charges[2].charged_sec == ledger.phase_charges[2].charged_sec
    assert confirmed.stop_slots.system_recovery.used_sec == ledger.stop_slots.system_recovery.used_sec


@pytest.mark.parametrize('entry,at,reason', [
    ('verification-run', '2026-01-01T00:20:29Z', 'dispatch-time-insufficient'),
    ('system-recover', '2026-01-01T00:01:00Z', 'recovery-target-missing'),
    ('recover', '2026-01-01T00:01:00Z', 'recovery-target-missing'),
])
def test_refusal_records_timing_and_missing_recovery_target(entry, at, reason):
    state = _state()
    result = decide(state, RecordBudgetRefusal(at, _reserve(at, entry, 'target:missing', 'op:one')))
    assert result.accepted
    assert result.transition.new_state.budget.stop_slots.refusal_count == 1
    assert result.transition.new_state.budget.stop_slots.last_refusal == 'budget-' + reason


def _headroom(state):
    payload = encode_v5_state(state, _GUIDANCE)
    return state_capacity_verdict(None, json.loads(payload), len(payload), encoding=StateEncoding.CANONICAL).metrics.headroom


def test_expiry_releases_capacity_before_next_admission():
    state = _state()
    bytes_ = _headroom(state) * 3 // 4
    state = decide(state, _reserve('2026-01-01T00:00:00Z', 'verification-run', 'target:old', 'op:old', bytes_)).transition.new_state
    result = decide(state, _reserve('2026-01-01T00:02:00Z', 'verification-run', 'target:new', 'op:new', bytes_))
    assert result.accepted
    assert result.transition.new_state.budget.settlements[-1].outcome == 'charged-full-unknown'
    assert len(result.transition.new_state.budget.reservations) == 1


@pytest.mark.parametrize('opened', [0, 1, 2])
def test_capacity_reserves_settlement_write_for_new_and_open_rows(opened):
    state = _state()
    if opened == 1:
        state = decide(state, _reserve('2026-01-01T00:00:00Z', 'verification-run', 'target:old', 'op:old', 8)).transition.new_state
    elif opened == 2:
        state = decide(state, replace(_reserve('2026-01-01T00:01:00Z', 'fresh-review-run', 'target:old', 'op:old'), policy_timeout=1200)).transition.new_state
        state = decide(state, _reserve('2026-01-01T00:20:29Z', 'system-recover', 'target:old', 'op:recover')).transition.new_state
        state = decide(state, EnterFinalPhase('2026-01-01T00:20:29Z', 'explicit')).transition.new_state
    bytes_ = _headroom(state) - BUDGET_RESERVATION_ROW_DELTA - BUDGET_SETTLEMENT_ROW_DELTA
    request = _reserve(state.budget.clock.last_observed_at, 'verification-run', 'target:new', 'op:new', bytes_)
    assert decide(state, request).accepted
    assert decide(state, replace(request, reserved_bytes=bytes_ + 1)).rejection.code == 'budget-state-capacity-exhausted'


def _admit(ledger, state, **changes):
    args = dict(entry='verification-run', target='target:one', operation_id='op:one', fencing_epoch=1,
                policy_timeout=60, reserved_bytes=0, candidate_digest='sha256:' + 'a' * 64)
    args.update(changes)
    return admit(ledger, state, '2026-01-01T00:01:00Z', **args)


@pytest.mark.parametrize('guard', ['concurrent-limit', 'phase-limit', 'no-new-evidence'])
def test_admission_limits_refuse_and_record_the_reason(guard):
    state = _state()
    ledger = state.budget
    if guard == 'concurrent-limit':
        ledger = replace(ledger, policy=replace(ledger.policy, max_concurrent_dispatches=1))
        ledger = reserve(ledger, _admit(ledger, state))
    elif guard == 'phase-limit':
        charges = list(ledger.phase_charges)
        charges[2] = replace(charges[2], reservation_count=ledger.policy.max_dispatches_per_phase)
        ledger = replace(ledger, phase_charges=tuple(charges))
    else:
        ledger = replace(ledger, progress=(ProgressSignature('verification-run', 'target:one',
            'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64, ledger.policy.no_progress_limit),))
    refused = _admit(ledger, state)
    assert refused.reason == 'budget-' + guard
    assert refused.ledger.stop_slots.refusal_count == 1
    if guard == 'no-new-evidence':
        assert _admit(ledger, state, candidate_digest='sha256:' + 'c' * 64).reservation is not None


def test_final_latch_rejects_provider_outside_final_phases_and_stop_requires_exhaustion():
    state = _state()
    ledger = enter_final(state.budget, '2026-01-01T00:00:01Z', 'explicit')
    assert _admit(ledger, state, entry='invoke-command').reason == 'budget-final-latched'
    reviewing = replace(state, control=replace(state.control, phase=Phase.REVIEWING))
    assert _admit(ledger, reviewing, entry='invoke-command').reservation.budget_class == 'final'
    with pytest.raises(BudgetError, match='budget-not-exhausted'):
        stop(ledger, '2026-01-01T00:01:00Z', 'all', 'exhausted')


@pytest.mark.parametrize('entry', [[], {}])
def test_unhashable_entry_is_recorded_as_a_refusal(entry):
    state = _state()
    refused = _admit(state.budget, state, entry=entry)
    assert refused.reason == 'budget-entry-invalid'
    assert refused.ledger.stop_slots.refusal_count == 1


@pytest.mark.parametrize('layout', ['v4', 'v5'])
@pytest.mark.parametrize('system', [False, True])
def test_other_writers_cannot_spend_open_budget_terminal_bytes(layout, system):
    from mission_kernel import state_capacity as sc
    from mission_persistence.capacity_gate import check_state_capacity, CapacityWriteError
    from .test_issue933_state_capacity_verdict import _base
    state = _state()
    held = decide(state, replace(_reserve('2026-01-01T00:01:00Z',
        'fresh-review-run', 'target:held', 'op:held', 65536), policy_timeout=1200)).transition.new_state
    if system:
        held = decide(held, _reserve('2026-01-01T00:20:29Z',
            'system-recover', 'target:held', 'op:recover', 65536)).transition.new_state
    document, encoding = _base(layout)
    target = document if layout == 'v4' else document['extensions']
    target.update(budget_minutes=30, budget_ledger=ledger_document(held.budget))
    encode = lambda: json.dumps(document, indent=2 if layout == 'v4' else None).encode()
    before = encode()
    target['padding'] = ''
    target['padding'] = 'x' * (sc.STATE_LIMIT - sc.system_remaining(document) - 32768 - len(encode()))
    expected = (2 if system else 1) * (65536 + BUDGET_SETTLEMENT_ROW_DELTA)
    verdict = state_capacity_verdict(None, document, len(encode()), encoding=encoding)
    assert verdict.metrics.reserved == expected
    assert not verdict.accepted
    with pytest.raises(CapacityWriteError, match='state-capacity-exhausted'):
        check_state_capacity(before, encode(), encoding=encoding)


def test_only_verification_supervisor_can_reserve_before_candidate_observation():
    state = _state()
    # Inventory needs a child itself. Its reserved supervisor applies the real
    # candidate gate before starting the verifier; other entries must know it.
    assert _admit(state.budget, state, candidate_digest=None).reservation is not None
    refusal = _admit(state.budget, state, entry='invoke-command', candidate_digest=None)
    assert refusal.reason == 'budget-text-invalid'
