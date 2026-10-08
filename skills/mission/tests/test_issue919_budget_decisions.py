"""F1 reducers keep budget policy state durable and deterministic."""
from dataclasses import replace
import pytest


def _state():
    from mission_kernel import decode_snapshot
    from mission_kernel.budget import decode_policy, default_policy_document, ledger_document, new_ledger
    from .mission_state_fixture_corpus import current_v5_open_state
    import json
    raw = current_v5_open_state()
    raw['extensions']['budget_ledger'] = ledger_document(
        new_ledger(decode_policy(default_policy_document(1800)), '2026-01-01T00:00:00Z'))
    global _GUIDANCE
    snapshot = decode_snapshot(json.dumps(raw).encode())
    _GUIDANCE = snapshot.guidance
    return snapshot.state


def _reserve(at, entry, target, operation, bytes_=0):
    from mission_kernel.commands import ReserveDispatchBudget
    return ReserveDispatchBudget(at, entry, target, operation, 1, 60, bytes_, 'sha256:' + 'a' * 64,
                                 guidance=_GUIDANCE)


def test_reserve_settle_and_final_latch_keep_projection_and_backing_together():
    from mission_kernel.commands import EnterFinalPhase, ReserveDispatchBudget, SettleDispatchBudget
    from mission_kernel.transitions import decide
    from .mission_state_fixture_corpus import current_v5_open_state
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
    settled = decide(state, SettleDispatchBudget('2026-01-01T00:01:02Z', row.reservation_id, 'settled', 1,
                                                  'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64))
    assert settled.accepted
    assert settled.transition.new_state.budget.stop_slots.final_run is None
    final_reservation = _reserve('2026-01-01T00:01:03Z', 'verification-run', 'target:two', 'op:two', 42)
    final_state = decide(settled.transition.new_state, final_reservation).transition.new_state
    final_row = final_state.budget.reservations[0]
    final_settlement = decide(final_state, SettleDispatchBudget('2026-01-01T00:01:04Z', final_row.reservation_id,
        'settled', 1, 'sha256:' + 'c' * 64, 'sha256:' + 'd' * 64, completed=True))
    assert final_settlement.accepted
    assert final_settlement.transition.new_state.budget.stop_slots.final_run is not None


def test_reserve_uses_trusted_phase_not_caller_and_is_idempotent_per_operation():
    from mission_kernel.commands import ReserveDispatchBudget
    from mission_kernel.transitions import decide
    state = _state()
    command = _reserve('2026-01-01T00:01:00Z', 'invoke-command', 'target:one', 'op:one')
    first = decide(state, command)
    assert first.accepted
    again = decide(first.transition.new_state, command)
    assert again.accepted
    assert again.transition.new_state == first.transition.new_state


def test_lifecycle_closes_opens_clock_and_pass_rejects_open_or_exhausted_budget():
    from mission_kernel.commands import MarkHalt, MarkPass, Reactivate, ReserveDispatchBudget
    from mission_kernel.model import HaltCategory, Phase
    from mission_kernel.transitions import decide
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


def test_verification_retries_reserve_again_and_kill_unconfirmed_remains_open():
    from mission_kernel.commands import ReserveDispatchBudget, SettleDispatchBudget
    from mission_kernel.transitions import decide
    state = _state()
    first = _reserve('2026-01-01T00:01:00Z', 'verification-run', 'target:one', 'op:one')
    held = decide(state, first).transition.new_state
    second = _reserve('2026-01-01T00:01:01Z', 'verification-run', 'target:one', 'op:one')
    retried = decide(held, second).transition.new_state
    assert len(retried.budget.reservations) == 2
    row = retried.budget.reservations[0]
    killed = decide(retried, SettleDispatchBudget('2026-01-01T00:01:02Z', row.reservation_id,
        'kill-unconfirmed', None, 'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64))
    assert killed.accepted
    ledger = killed.transition.new_state.budget
    assert any(item.reservation_id == row.reservation_id for item in ledger.reservations)
    assert ledger.settlements[-1].charged_sec == row.reserved_sec


def test_admission_refuses_insufficient_trusted_capacity_evidence():
    from mission_kernel.budget_decisions import CapacityEvidence, admit
    state = _state()
    refused = admit(state.budget, state, '2026-01-01T00:00:01Z', entry='verification-run',
        target='target:one', operation_id='op:one', fencing_epoch=1, policy_timeout=60,
        reserved_bytes=8, candidate_digest='sha256:' + 'a' * 64,
        capacity=CapacityEvidence(headroom=7, reservation_delta=1))
    assert refused.reason == 'budget-state-capacity-exhausted'
    assert refused.ledger.stop_slots.last_refusal == refused.reason


def test_real_reserve_rejects_four_megabyte_row_with_e0_capacity_verdict():
    from mission_kernel.commands import ReserveDispatchBudget
    from mission_kernel.transitions import decide
    state = _state()
    result = decide(state, _reserve('2026-01-01T00:00:01Z', 'verification-run', 'target:one', 'op:one', 4 * 1024 * 1024))
    assert not result.accepted
    assert result.rejection.code == 'budget-state-capacity-exhausted'


def test_v5_reserve_without_snapshot_guidance_fails_closed():
    from mission_kernel.commands import ReserveDispatchBudget
    from mission_kernel.transitions import decide
    state = _state()
    result = decide(state, ReserveDispatchBudget('2026-01-01T00:00:01Z', 'verification-run', 'target:one',
        'op:one', 1, 60, 0, 'sha256:' + 'a' * 64))
    assert result.rejection.code == 'budget-capacity-evidence-required'


def test_budget_commands_reject_absent_policy_without_mutation():
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import BudgetStop, EnterFinalPhase, RecordBudgetRefusal, ReserveDispatchBudget, SettleDispatchBudget
    from mission_kernel.transitions import decide
    from .mission_state_fixture_corpus import current_v5_open_state
    state = decode_mission_state(__import__('json').dumps(current_v5_open_state()).encode())
    request = ReserveDispatchBudget('2026-01-01T00:00:01Z', 'verification-run', 'target:one', 'op:one', 1, 60, 0, 'sha256:' + 'a' * 64)
    for command in (request, SettleDispatchBudget('2026-01-01T00:00:01Z', 'reservation:x', 'settled', 1, 'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64),
                    EnterFinalPhase('2026-01-01T00:00:01Z', 'explicit'), BudgetStop('2026-01-01T00:00:01Z', 'all', 'exhausted'),
                    RecordBudgetRefusal('2026-01-01T00:00:01Z', request)):
        result = decide(state, command)
        assert not result.accepted and result.rejection.code == 'budget-policy-absent'


@pytest.mark.parametrize('schema', [4, 5])
def test_all_budget_mutations_roundtrip(schema):
    """Every F1 mutation leaves a decoder-valid durable projection."""
    from mission_kernel import decode_mission_state, decode_snapshot, project_legacy_document
    from mission_kernel.budget import decode_policy, default_policy_document, ledger_document, new_ledger
    from mission_kernel.codec_v5 import encode_v5_state
    from mission_kernel.commands import (BudgetStop, EnterFinalPhase, RecordBudgetRefusal,
        ReserveDispatchBudget, SettleDispatchBudget)
    from mission_kernel.transitions import decide
    from .mission_state_fixture_corpus import current_v5_open_state
    import json
    if schema == 5:
        raw = current_v5_open_state()
        raw['extensions']['budget_ledger'] = ledger_document(new_ledger(decode_policy(default_policy_document(1800)), '2026-01-01T00:00:00Z'))
        snapshot = decode_snapshot(json.dumps(raw).encode())
        state, guidance = snapshot.state, snapshot.guidance
    else:
        raw = {'schema_version': 4, 'phase': 'planning', 'loop_active': True,
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
    result = decide(state, SettleDispatchBudget('2026-01-01T00:00:02Z', row.reservation_id, 'settled', 1,
        'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64))
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
    from mission_kernel.commands import RecordBudgetRefusal
    from mission_kernel.transitions import decide
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
    from mission_kernel.commands import MarkHalt, RecordBudgetRefusal
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide
    halted = decide(_state(), MarkHalt(HaltCategory.PARTIAL_DONE, 'pause', at='2026-01-01T00:00:01Z')).transition.new_state
    request = _reserve('2026-01-01T00:00:02Z', 'verification-run', 'target:one', 'op:one')
    result = decide(halted, RecordBudgetRefusal(request.at, request))
    assert result.rejection.code == 'budget-loop-inactive'


def test_system_recovery_uses_its_bounded_allowance_only_after_normal_recovery_time_fails():
    from mission_kernel.commands import ReserveDispatchBudget
    from mission_kernel.transitions import decide
    state = _state()
    held_command = ReserveDispatchBudget('2026-01-01T00:01:00Z', 'fresh-review-run', 'target:held', 'op:held',
        1, 1200, 0, 'sha256:' + 'a' * 64, guidance=_GUIDANCE)
    held = decide(state, held_command).transition.new_state
    recovery = _reserve('2026-01-01T00:20:29Z', 'system-recover', 'target:held', 'op:recover')
    result = decide(held, recovery)
    assert result.accepted
    assert result.transition.new_state.budget.stop_slots.system_recovery.reservation is not None
    expired = _reserve('2026-01-01T00:30:00Z', 'system-recover', 'target:held', 'op:too-late')
    result = decide(held, expired)
    assert not result.accepted


def test_repair_entries_fail_closed_without_an_e2_attempt_and_budget_class_has_no_caller_phase():
    from mission_kernel.budget_decisions import admit, budget_class
    state = _state()
    for entry in ('repair-reverify', 'repair-disposition-run', 'repair-begin'):
        refusal = admit(state.budget, state, '2026-01-01T00:00:01Z', entry=entry,
            target='target:repair', operation_id='op:repair', fencing_epoch=1, policy_timeout=60,
            reserved_bytes=0, candidate_digest='sha256:' + 'a' * 64)
        assert refusal.reason == 'budget-repair-attempt-missing'
    assert budget_class('verification-run', state, state.budget, '2026-01-01T00:00:01Z') == 'verification'


def test_kill_unconfirmed_charges_once_and_confirmation_closes_without_recharge():
    from mission_kernel.commands import SettleDispatchBudget
    from mission_kernel.transitions import decide
    state = decide(_state(), _reserve('2026-01-01T00:01:00Z', 'verification-run', 'target:one', 'op:one')).transition.new_state
    row = state.budget.reservations[0]
    kill = SettleDispatchBudget('2026-01-01T00:01:01Z', row.reservation_id, 'kill-unconfirmed', None,
        'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64)
    killed = decide(state, kill).transition.new_state
    charged = killed.budget.phase_charges[2].charged_sec
    repeated = decide(killed, kill)
    assert repeated.accepted and repeated.transition.new_state == killed
    assert killed.budget.phase_charges[2].charged_sec == charged
    confirmed = decide(killed, SettleDispatchBudget('2026-01-01T00:01:02Z', row.reservation_id, 'settled', 1,
        'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64, completed=True))
    assert confirmed.accepted
    ledger = confirmed.transition.new_state.budget
    assert not ledger.reservations and ledger.phase_charges[2].charged_sec == charged
    for bad in (
        SettleDispatchBudget('2026-01-01T00:01:03Z', row.reservation_id, 'settled', 1,
            'not-a-digest', 'sha256:' + 'b' * 64),
        SettleDispatchBudget('2026-01-01T00:01:03Z', row.reservation_id, 'settled', 1,
            'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64, tool_calls=True),
        SettleDispatchBudget('2026-01-01T00:01:03Z', row.reservation_id, 'settled', 1,
            'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64, completed=1),
    ):
        assert not decide(confirmed.transition.new_state, bad).accepted


def test_expired_open_reservation_is_charged_full_unknown():
    from mission_kernel.budget_decisions import admit
    state = _state()
    admitted = admit(state.budget, state, '2026-01-01T00:01:00Z', entry='verification-run',
        target='target:one', operation_id='op:one', fencing_epoch=1, policy_timeout=60,
        reserved_bytes=0, candidate_digest='sha256:' + 'a' * 64)
    from mission_kernel.budget_decisions import reserve
    held = reserve(state.budget, admitted)
    expired = admit(held, state, '2026-01-01T00:20:31Z', entry='verification-run',
        target='target:two', operation_id='op:two', fencing_epoch=1, policy_timeout=60,
        reserved_bytes=0, candidate_digest='sha256:' + 'b' * 64)
    assert expired.ledger.settlements[-1].outcome == 'charged-full-unknown'
    assert expired.ledger.settlements[-1].charged_sec == held.reservations[0].reserved_sec


def test_final_completed_settlement_records_final_run():
    from mission_kernel.commands import EnterFinalPhase, SettleDispatchBudget
    from mission_kernel.transitions import decide
    state = decide(_state(), EnterFinalPhase('2026-01-01T00:01:00Z', 'explicit')).transition.new_state
    state = decide(state, _reserve('2026-01-01T00:01:01Z', 'verification-run', 'target:final', 'op:final')).transition.new_state
    row = state.budget.reservations[0]
    result = decide(state, SettleDispatchBudget('2026-01-01T00:01:02Z', row.reservation_id, 'settled', 1,
        'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64, completed=True))
    assert result.accepted
    assert result.transition.new_state.budget.stop_slots.final_run.reservation_id == row.reservation_id


def test_reserve_command_serialization_uses_closed_guidance_projection_not_snapshot_binding():
    from mission_kernel.commands import ReserveDispatchBudget, encode_kernel_command
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
    from dataclasses import replace
    from mission_kernel.budget import INT_MAX, StopSlots, decode_ledger, ledger_document
    from mission_kernel.budget_decisions import CapacityEvidence, admit
    state = _state()
    expired = admit(state.budget, state, '2026-01-01T00:30:00Z', entry='verification-run',
        target='target:one', operation_id='op:one', fencing_epoch=1, policy_timeout=60,
        reserved_bytes=0, candidate_digest='sha256:' + 'a' * 64)
    assert expired.reason == 'budget-exhausted'
    assert decode_ledger({'budget_ledger': ledger_document(expired.ledger)}) == expired.ledger
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
    from mission_kernel.commands import SettleDispatchBudget
    from mission_kernel.transitions import decide
    state = decide(_state(), _reserve('2026-01-01T00:01:00Z', 'verification-run', 'target:done', 'op:done')).transition.new_state
    row = state.budget.reservations[0]
    state = decide(state, SettleDispatchBudget('2026-01-01T00:01:01Z', row.reservation_id, 'settled', 1,
        'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64, completed=True)).transition.new_state
    recovery = decide(state, _reserve('2026-01-01T00:01:02Z', 'system-recover', 'target:done', 'op:recover'))
    assert recovery.rejection.code == 'budget-recovery-target-missing'


def test_settled_dispatch_does_not_block_a_new_operation_and_candidate():
    from mission_kernel.commands import ReserveDispatchBudget, SettleDispatchBudget
    from mission_kernel.transitions import decide
    from mission_kernel.commands import ReserveDispatchBudget
    first = ReserveDispatchBudget('2026-01-01T00:01:00Z', 'fresh-review-run', 'target:next', 'op:one', 1, 60, 0,
        'sha256:' + 'a' * 64, guidance=_GUIDANCE)
    state = decide(_state(), first).transition.new_state
    row = state.budget.reservations[0]
    state = decide(state, SettleDispatchBudget('2026-01-01T00:01:01Z', row.reservation_id, 'settled', 1,
        'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64)).transition.new_state
    fresh = ReserveDispatchBudget('2026-01-01T00:01:02Z', 'fresh-review-run', 'target:next', 'op:two', 1, 60, 0,
        'sha256:' + 'c' * 64, guidance=_GUIDANCE)
    assert decide(state, fresh).accepted


def test_settled_de_operation_replays_without_a_new_reservation():
    from mission_kernel.commands import ReserveDispatchBudget, SettleDispatchBudget
    from mission_kernel.transitions import decide
    first = ReserveDispatchBudget('2026-01-01T00:01:00Z', 'fresh-review-run', 'target:replay', 'op:one', 1, 60, 0,
        'sha256:' + 'a' * 64, guidance=_GUIDANCE)
    state = decide(_state(), first).transition.new_state
    row = state.budget.reservations[0]
    state = decide(state, SettleDispatchBudget('2026-01-01T00:01:01Z', row.reservation_id, 'settled', 1,
        'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64)).transition.new_state
    replay = ReserveDispatchBudget('2026-01-01T00:01:02Z', 'fresh-review-run', 'target:replay', 'op:one', 1, 60, 0,
        'sha256:' + 'a' * 64, guidance=_GUIDANCE)
    result = decide(state, replay)
    assert result.accepted and not result.transition.new_state.budget.reservations
    event = result.transition.events[0]
    assert event.type == 'budget-dispatch-replayed'
    assert event.settlement.reservation_id == row.reservation_id


def test_spawn_entry_inventory_is_closed_and_inert_until_f2_coverage():
    from mission_kernel.budget import BUDGET_SPAWN_ENTRIES
    expected = {
        'verification-run', 'repair-reverify', 'invoke-command', 'invoke-prepared',
        'verify-approval', 'force-approval', 'fresh-review-run',
        'repair-disposition-run', 'recover', 'system-recover', 'repair-begin',
    }
    assert set(BUDGET_SPAWN_ENTRIES) == expected
    assert set(BUDGET_SPAWN_ENTRIES.values()) == {'pending'}
    with pytest.raises(TypeError):
        BUDGET_SPAWN_ENTRIES['verification-run'] = 'covered'


def test_de_replay_fails_closed_when_a_retained_legacy_reservation_id_is_unverifiable():
    from dataclasses import replace
    from mission_kernel.budget import Observation, Settlement, Telemetry
    from mission_kernel.budget_decisions import admit
    state = _state()
    ledger = replace(state.budget, settlements=(Settlement('reservation:legacy', 'settled', 1, 0,
        Observation('2026-01-01T00:00:00Z', 1), Telemetry(None, None, None)),))
    refusal = admit(ledger, state, '2026-01-01T00:00:01Z', entry='fresh-review-run', target='target:legacy',
        operation_id='op:next', fencing_epoch=1, policy_timeout=60, reserved_bytes=0,
        candidate_digest='sha256:' + 'a' * 64)
    assert refusal.reason == 'budget-operation-replay-unverifiable'


def test_system_recovery_refuses_open_non_adapter_dispatch():
    from mission_kernel.commands import ReserveDispatchBudget
    from mission_kernel.transitions import decide
    held = ReserveDispatchBudget('2026-01-01T00:01:00Z', 'verification-run', 'target:b', 'op:b', 1, 1200, 0,
        'sha256:' + 'a' * 64, guidance=_GUIDANCE)
    state = decide(_state(), held).transition.new_state
    recovery = _reserve('2026-01-01T00:20:29Z', 'system-recover', 'target:b', 'op:recover')
    assert decide(state, recovery).rejection.code == 'budget-recovery-target-missing'


def test_expiry_only_charges_after_settle_by_boundary():
    from datetime import datetime, timedelta, timezone
    from mission_kernel.budget_decisions import admit, expire_reservations, reserve
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
    from mission_kernel.commands import SettleDispatchBudget
    from mission_kernel.transitions import decide
    state = decide(_state(), _reserve('2026-01-01T00:01:00Z', 'verification-run', 'target:duplicate', 'op:one')).transition.new_state
    row = state.budget.reservations[0]
    settled = decide(state, SettleDispatchBudget('2026-01-01T00:01:01Z', row.reservation_id, 'settled', 1,
        'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64)).transition.new_state
    invalid = decide(settled, SettleDispatchBudget('2026-01-01T00:01:02Z', row.reservation_id, 'unknown-outcome', 1,
        'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64))
    assert invalid.rejection.code == 'budget-settlement-outcome-invalid'
    contradictory = decide(settled, SettleDispatchBudget('2026-01-01T00:01:01Z', row.reservation_id, 'settled', 1,
        'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64, tool_calls=1))
    assert contradictory.rejection.code == 'budget-settlement-duplicate'


def test_system_recovery_kill_charges_allowance_once_and_confirmation_preserves_charge():
    from mission_kernel.commands import ReserveDispatchBudget, SettleDispatchBudget
    from mission_kernel.transitions import decide
    state = _state()
    held = ReserveDispatchBudget('2026-01-01T00:01:00Z', 'fresh-review-run', 'target:system', 'op:held',
        1, 1200, 0, 'sha256:' + 'a' * 64, guidance=_GUIDANCE)
    state = decide(state, held).transition.new_state
    recovery = _reserve('2026-01-01T00:20:29Z', 'system-recover', 'target:system', 'op:recover')
    state = decide(state, recovery).transition.new_state
    row = state.budget.stop_slots.system_recovery.reservation
    killed = decide(state, SettleDispatchBudget('2026-01-01T00:20:30Z', row.reservation_id, 'kill-unconfirmed', None,
        'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64))
    assert killed.accepted
    held_ledger = killed.transition.new_state.budget
    assert held_ledger.stop_slots.system_recovery.reservation is not None
    assert held_ledger.stop_slots.system_recovery.used_sec == row.reserved_sec
    confirmed = decide(killed.transition.new_state, SettleDispatchBudget('2026-01-01T00:20:31Z', row.reservation_id,
        'settled', 1, 'sha256:' + 'a' * 64, 'sha256:' + 'b' * 64))
    assert confirmed.accepted
    closed = confirmed.transition.new_state.budget.stop_slots.system_recovery
    assert closed.reservation is None and closed.used_sec == row.reserved_sec


@pytest.mark.parametrize('target,operation,epoch,timeout,bytes_', [
    ('', 'op:one', 1, 1, 0), ('target:one', '', 1, 1, 0),
    ('target:one', 'op:one', True, 1, 0), ('target:one', 'op:one', 0, 1, 0),
    ('target:one', 'op:one', 1, True, 0), ('target:one', 'op:one', 1, 1, -1),
])
def test_admission_rejects_invalid_closed_request_scalars(target, operation, epoch, timeout, bytes_):
    from mission_kernel.budget_decisions import admit
    state = _state()
    refused = admit(state.budget, state, '2026-01-01T00:00:01Z', entry='verification-run', target=target,
        operation_id=operation, fencing_epoch=epoch, policy_timeout=timeout, reserved_bytes=bytes_,
        candidate_digest='sha256:' + 'a' * 64)
    assert refused.reason.startswith('budget-')
