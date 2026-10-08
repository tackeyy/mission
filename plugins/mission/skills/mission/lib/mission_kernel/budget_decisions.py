"""Pure F1 budget admission and ledger reducers.

This module deliberately has no persistence or process-launch dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import re

from .budget import (
    BudgetError, BudgetProjection, BudgetStop, DispatchReservation, Exhaustion,
    FinalLatch, FinalRun, Observation, PHASES, PhaseCharge, ProgressSignature,
    Settlement, SystemRecovery, Telemetry, active_seconds, child_deadlines,
    deadlines, exhaustion, observe_clock, reserve_erosion_sec,
    _int, _text,
    _digest, ledger_document, INT_MAX,
)


@dataclass(frozen=True)
class Admission:
    reservation: DispatchReservation | None
    ledger: BudgetProjection | None = None
    replay: bool = False
    saved_settlement: Settlement | None = None


@dataclass(frozen=True)
class Refusal:
    reason: str
    ledger: BudgetProjection | None = None


@dataclass(frozen=True)
class CapacityEvidence:
    """Trusted E0 result supplied by an internal writer, never a CLI field."""
    headroom: int
    reservation_delta: int

    def permits(self, reserved_bytes: int) -> bool:
        return (type(self.headroom) is int and type(self.reservation_delta) is int
                and self.headroom >= 0 and self.reservation_delta >= 0
                and type(reserved_bytes) is int and reserved_bytes >= 0
                and self.headroom >= self.reservation_delta + reserved_bytes)


def _reject(code, ledger=None, at=None):
    if ledger is not None:
        slots = ledger.stop_slots
        if at is not None:
            try:
                exhausted = exhaustion(ledger, at)
            except BudgetError:
                # A malformed/regressed observation must not turn an otherwise
                # valid projection into an unencodable refusal record.
                return Refusal('budget-' + code, ledger)
            if exhausted is not None and slots.exhaustion is None:
                slots = replace(slots, exhaustion=exhausted)
        slots = replace(slots, refusal_count=min(INT_MAX, slots.refusal_count + 1),
                        last_refusal='budget-' + code)
        ledger = replace(ledger, stop_slots=slots)
    return Refusal('budget-' + code, ledger)


def _phase(state):
    value = getattr(getattr(state, 'control', state), 'phase', None)
    return getattr(value, 'value', value)


def budget_class(entry, state, ledger, at):
    """Classify from state only; command-line phase labels never enter here."""
    if entry not in ('verification-run', 'repair-reverify', 'invoke-command',
                     'invoke-prepared', 'verify-approval', 'force-approval',
                     'fresh-review-run', 'repair-disposition-run', 'recover',
                     'system-recover', 'repair-begin'):
        raise BudgetError('budget-entry-invalid')
    latched = ledger.stop_slots.final_latch is not None or at >= deadlines(ledger, at).repair
    if entry in ('repair-reverify', 'repair-disposition-run', 'repair-begin'):
        return _reject('final-latched') if latched else _reject('repair-attempt-missing')
    if entry in ('invoke-command', 'invoke-prepared', 'verify-approval'):
        if latched and _phase(state) not in ('reviewing', 'scoring', 'critic'):
            return _reject('final-latched')
        if latched:
            return 'final'
        return 'planning' if _phase(state) == 'planning' else 'implementation'
    return 'final' if latched else 'verification'


def _reservation_id(entry, target, operation_id, fencing_epoch, ordinal):
    source = '\x1f'.join((entry, target, operation_id, str(fencing_epoch), str(ordinal))).encode()
    return f'reservation:{ordinal}:' + hashlib.sha256(source).hexdigest()[:32]


_RECOVERABLE_ADAPTER_ENTRIES = frozenset((
    'fresh-review-run', 'repair-disposition-run', 'repair-reverify',
))
_REPLAY_UNVERIFIABLE = object()


def _recovery_target(ledger, target):
    """Return the class only while the original reservation is durable."""
    exact = [row for row in ledger.reservations if row.reservation_id == target
             and row.entry in _RECOVERABLE_ADAPTER_ENTRIES]
    if exact:
        return exact[0].budget_class
    matching = [row for row in ledger.reservations if row.target == target
                and row.entry in _RECOVERABLE_ADAPTER_ENTRIES]
    if len(matching) == 1:
        return matching[0].budget_class
    return None


def _settled_operation_replay(ledger, entry, target, operation_id, fencing_epoch):
    """Recognize retained D/E operation identities without widening the wire schema."""
    if entry not in _RECOVERABLE_ADAPTER_ENTRIES:
        return None
    for settlement in ledger.settlements:
        match = re.fullmatch(r'reservation:([1-9][0-9]{0,18}):([0-9a-f]{32})', settlement.reservation_id)
        if match is None:
            return _REPLAY_UNVERIFIABLE
        ordinal = int(match.group(1))
        if ordinal > ledger.policy.max_dispatches_per_phase:
            continue
        if settlement.reservation_id == _reservation_id(entry, target, operation_id, fencing_epoch, ordinal):
            return settlement
    return None


def admit(ledger, state, at, *, entry, target, operation_id, fencing_epoch,
          policy_timeout, reserved_bytes, candidate_digest, fallback_reason=None, capacity=None):
    """Return an immutable reservation or a reason; do not mutate state."""
    if ledger.policy is None:
        return _reject('policy-absent')
    try:
        # Direct kernel callers get the same durable observation as reducer
        # callers.  A regressed clock is rejected without producing a record.
        ledger = with_clock(ledger, at, active=ledger.clock.opened_at is not None)
        ledger = expire_reservations(ledger, at)
        if capacity is not None and (not isinstance(capacity, CapacityEvidence) or not capacity.permits(reserved_bytes)):
            return _reject('state-capacity-exhausted', ledger, at)
        _text(target)
        _text(operation_id)
        _int(fencing_epoch, 1)
        _int(policy_timeout, 1)
        _int(reserved_bytes)
        _text(candidate_digest, re.compile(r'sha256:[0-9a-f]{64}\Z'))
        if entry == 'system-recover':
            recovery = ledger.stop_slots.system_recovery
            if recovery.reservation is not None:
                return _reject('recovery-open')
            held_class = _recovery_target(ledger, target)
            if held_class is None:
                return _reject('recovery-target-missing')
            normal_refused = exhaustion(ledger, at) is not None
            if not normal_refused:
                if held_class == 'repair' and (ledger.stop_slots.final_latch is not None or at >= deadlines(ledger, at).repair):
                    normal_refused = True
                else:
                    try:
                        child_deadlines(ledger, at, 'recover', held_class, policy_timeout)
                    except BudgetError:
                        normal_refused = True
            if not normal_refused:
                return _reject('system-recovery-not-fallback', ledger, at)
            child, settle_by, seconds = child_deadlines(ledger, at, entry, 'final', policy_timeout)
            row = DispatchReservation(_reservation_id(entry, target, operation_id, fencing_epoch,
                recovery.reservation_count + 1), entry, 'final', target, operation_id, fencing_epoch,
                at, child, settle_by, seconds, reserved_bytes)
            return Admission(row, ledger)
        if entry != 'verification-run' and any(r.entry == entry and r.target == target and r.operation_id == operation_id
               and r.fencing_epoch == fencing_epoch for r in ledger.reservations):
            return Admission(next(r for r in ledger.reservations if r.entry == entry and r.target == target
                                  and r.operation_id == operation_id and r.fencing_epoch == fencing_epoch), ledger)
        settled_replay = _settled_operation_replay(ledger, entry, target, operation_id, fencing_epoch)
        if settled_replay is _REPLAY_UNVERIFIABLE:
            return _reject('operation-replay-unverifiable', ledger, at)
        if settled_replay is not None:
            return Admission(None, ledger, replay=True, saved_settlement=settled_replay)
        if exhaustion(ledger, at) is not None and entry != 'system-recover':
            return _reject('exhausted', ledger, at)
        if entry == 'recover':
            budget = _recovery_target(ledger, target)
            if budget is None:
                return _reject('recovery-target-missing')
        else:
            budget = budget_class(entry, state, ledger, at)
        if isinstance(budget, Refusal):
            return _reject(budget.reason.removeprefix('budget-'), ledger, at)
        if len(ledger.reservations) >= ledger.policy.max_concurrent_dispatches:
            return _reject('concurrent-limit', ledger, at)
        index = PHASES.index(budget)
        if ledger.phase_charges[index].reservation_count >= ledger.policy.max_dispatches_per_phase:
            return _reject('phase-limit', ledger, at)
        matching = next((p for p in ledger.progress if p.entry == entry and p.target == target), None)
        if matching and matching.candidate_digest == candidate_digest and matching.consecutive_count >= ledger.policy.no_progress_limit:
            return _reject('no-new-evidence', ledger, at)
        timeout = min(policy_timeout, ledger.policy.adapter_call_sec) if entry == 'recover' else policy_timeout
        child, settle, seconds = child_deadlines(ledger, at, entry, budget, timeout)
        ordinal = ledger.phase_charges[index].reservation_count + 1
        return Admission(DispatchReservation(_reservation_id(entry, target, operation_id, fencing_epoch, ordinal),
            entry, budget, target, operation_id, fencing_epoch, at, child, settle, seconds, reserved_bytes), ledger)
    except BudgetError as exc:
        return Refusal('budget-' + str(exc).removeprefix('budget-'), ledger)


def expire_reservations(ledger, at):
    """Conservatively charge overdue unknown work; known kill holds stay open."""
    for row in tuple(ledger.reservations):
        if row.settle_by >= at:
            continue
        known = next((item for item in ledger.settlements if item.reservation_id == row.reservation_id), None)
        if known is not None and known.outcome == 'kill-unconfirmed':
            continue
        ledger = settle(ledger, at, reservation_id=row.reservation_id,
            outcome='charged-full-unknown', elapsed_sec=None,
            candidate_digest='sha256:' + '0' * 64, result_digest='sha256:' + '0' * 64)
    return ledger


def reserve(ledger, admission):
    if not isinstance(admission, Admission):
        raise BudgetError('budget-admission-required')
    ledger = admission.ledger or ledger
    row = admission.reservation
    if admission.replay:
        return ledger
    if row is None:
        raise BudgetError('budget-admission-required')
    if row.entry == 'system-recover':
        recovery = ledger.stop_slots.system_recovery
        if recovery.reservation is not None:
            raise BudgetError('budget-recovery-open')
        return replace(ledger, stop_slots=replace(ledger.stop_slots,
            system_recovery=SystemRecovery(recovery.used_sec, recovery.reservation_count + 1, row)))
    if any(item.reservation_id == row.reservation_id for item in ledger.reservations):
        return ledger
    index = PHASES.index(row.budget_class)
    charge = ledger.phase_charges[index]
    charges = list(ledger.phase_charges)
    charges[index] = replace(charge, reservation_count=charge.reservation_count + 1, open_count=charge.open_count + 1)
    return replace(ledger, reservations=(*ledger.reservations, row), phase_charges=tuple(charges))


def settle(ledger, at, *, reservation_id, outcome, elapsed_sec, candidate_digest, result_digest,
           tool_calls=None, replays=None, output_bytes=None, completed=False):
    _text(reservation_id)
    _text(candidate_digest, re.compile(r'sha256:[0-9a-f]{64}\Z'))
    _text(result_digest, re.compile(r'sha256:[0-9a-f]{64}\Z'))
    if elapsed_sec is not None:
        _int(elapsed_sec)
    for value in (tool_calls, replays, output_bytes):
        if value is not None:
            _int(value)
    if type(completed) is not bool:
        raise BudgetError('budget-settlement-invalid')
    if outcome not in ('settled', 'charged-full-unknown', 'kill-unconfirmed'):
        raise BudgetError('budget-settlement-outcome-invalid')
    telemetry = Telemetry(tool_calls, replays, output_bytes)
    row = next((item for item in ledger.reservations if item.reservation_id == reservation_id), None)
    system = ledger.stop_slots.system_recovery.reservation
    if row is None and system is not None and system.reservation_id == reservation_id:
        row = system
    if row is None:
        prior = next((item for item in ledger.settlements if item.reservation_id == reservation_id), None)
        if prior is not None:
            if (prior.outcome != outcome or prior.observed.at != at
                    or prior.observed.elapsed_sec != elapsed_sec or prior.telemetry != telemetry):
                raise BudgetError('budget-settlement-duplicate')
            return ledger
        raise BudgetError('budget-reservation-missing')
    prior_settlement = next((item for item in ledger.settlements
                             if item.reservation_id == reservation_id), None)
    # A kill-unconfirmed charge deliberately leaves the reservation open.
    # Retrying that observation must not charge it a second time; a later
    # confirmed terminal result closes the held reservation while preserving
    # the conservative first full charge.
    if prior_settlement is not None:
        if prior_settlement.outcome != 'kill-unconfirmed':
            raise BudgetError('budget-settlement-duplicate')
        if outcome == 'kill-unconfirmed':
            if (prior_settlement.observed.at != at
                    or prior_settlement.observed.elapsed_sec != elapsed_sec
                    or prior_settlement.telemetry != telemetry):
                raise BudgetError('budget-settlement-duplicate')
            return ledger
    charged = row.reserved_sec if outcome != 'settled' or elapsed_sec is None else min(row.reserved_sec, elapsed_sec)
    if prior_settlement is not None:
        charged = prior_settlement.charged_sec
    if type(charged) is not int or charged < 0:
        raise BudgetError('budget-settlement-invalid')
    late = max(0, int((datetime.fromisoformat(at.replace('Z', '+00:00')) -
                       datetime.fromisoformat(row.settle_by.replace('Z', '+00:00'))).total_seconds()))
    record = Settlement(row.reservation_id, outcome, charged, late,
                        Observation(at, elapsed_sec), telemetry)
    if outcome == 'kill-unconfirmed':
        charges = list(ledger.phase_charges)
        if row.entry != 'system-recover':
            index = PHASES.index(row.budget_class)
            charge = charges[index]
            charges[index] = replace(charge, charged_sec=charge.charged_sec + charged)
            return replace(ledger, phase_charges=tuple(charges), settlements=(*ledger.settlements[-31:], record))
        recovery = ledger.stop_slots.system_recovery
        slots = replace(ledger.stop_slots, system_recovery=SystemRecovery(
            recovery.used_sec + charged, recovery.reservation_count, row))
        return replace(ledger, phase_charges=tuple(charges), settlements=(*ledger.settlements[-31:], record),
                       stop_slots=slots)
    if row.entry == 'system-recover':
        recovery = ledger.stop_slots.system_recovery
        slots = replace(ledger.stop_slots, system_recovery=SystemRecovery(
            recovery.used_sec + (0 if prior_settlement is not None else charged), recovery.reservation_count, None))
        settlements = tuple(record if item.reservation_id == reservation_id else item
                            for item in ledger.settlements)
        if prior_settlement is None:
            settlements = (*settlements[-31:], record)
        return replace(ledger, settlements=settlements, stop_slots=slots)
    index = PHASES.index(row.budget_class)
    charges = list(ledger.phase_charges)
    charge = charges[index]
    charges[index] = replace(charge, charged_sec=charge.charged_sec + (0 if prior_settlement is not None else charged),
                             open_count=charge.open_count - 1)
    progress = [p for p in ledger.progress if (p.entry, p.target) != (row.entry, row.target)]
    prior = next((p for p in ledger.progress if (p.entry, p.target) == (row.entry, row.target)), None)
    count = prior.consecutive_count + 1 if prior and prior.candidate_digest == candidate_digest and prior.result_digest == result_digest else 1
    progress.append(ProgressSignature(row.entry, row.target, candidate_digest, result_digest, count))
    slots = ledger.stop_slots
    if row.budget_class == 'final' and outcome == 'settled' and completed and slots.final_latch is not None and at >= row.reserved_at:
        slots = replace(slots, final_run=FinalRun(row.reservation_id, at))
    settlements = tuple(record if item.reservation_id == reservation_id else item
                        for item in ledger.settlements)
    if prior_settlement is None:
        settlements = (*settlements[-31:], record)
    return replace(ledger, reservations=tuple(r for r in ledger.reservations if r.reservation_id != reservation_id),
                   phase_charges=tuple(charges), settlements=settlements, progress=tuple(progress),
                   stop_slots=slots)


def enter_final(ledger, at, reason):
    if ledger.policy is None:
        raise BudgetError('budget-policy-absent')
    if exhaustion(ledger, at) is not None:
        raise BudgetError('budget-exhausted')
    if ledger.stop_slots.final_latch is not None:
        return ledger
    return replace(ledger, stop_slots=replace(ledger.stop_slots, final_latch=FinalLatch(at, reason)))


def with_clock(ledger, at, *, active):
    return replace(ledger, clock=observe_clock(ledger, at, active=active))


def record_exhaustion(ledger, at):
    exhausted = exhaustion(ledger, at)
    if exhausted is None or ledger.stop_slots.exhaustion is not None:
        return ledger
    return replace(ledger, stop_slots=replace(ledger.stop_slots, exhaustion=exhausted))


def completion_rejection(ledger, at):
    if ledger.policy is None:
        return None
    if ledger.reservations or ledger.stop_slots.system_recovery.reservation is not None:
        return 'budget-dispatch-unsettled'
    if exhaustion(ledger, at) is not None:
        return 'budget-exhausted'
    return None


def stop(ledger, at, scope, reason_code):
    exhausted = exhaustion(ledger, at)
    if exhausted is None:
        raise BudgetError('budget-not-exhausted')
    ledger_digest = _digest(ledger_document(ledger))
    slots = replace(ledger.stop_slots, exhaustion=exhausted,
                    budget_stop=BudgetStop(scope, reason_code, at, ledger_digest,
                        len(ledger.reservations), reserve_erosion_sec(ledger, at)))
    return replace(ledger, stop_slots=slots)
