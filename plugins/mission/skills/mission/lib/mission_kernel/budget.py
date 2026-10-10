"""F1: closed, inert budget types and pure calculations; never reads a clock.

Policy intake and dispatch/lifecycle writers are intentionally not public here.
All persisted integers are bounded to make maximum state growth measurable.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import hashlib
import json
import re
from types import MappingProxyType

from .errors import CanonicalStateEncodingError

POLICY_SCHEMA = 'mission-budget-policy/1'
LEDGER_SCHEMA = 'mission-budget-ledger/1'
INT_MAX = 2**63 - 1
STOP_SLOTS_MAX_BYTES = 16 * 1024
PHASES = ('planning', 'implementation', 'verification', 'repair', 'final')
BASIS_POINTS = (1000, 4000, 2000, 2000, 1000)
# Stable entry names, not CLI spellings or caller-selected phase labels.
BUDGET_SPAWN_ENTRIES = MappingProxyType({**dict.fromkeys((
    'verification-run', 'repair-reverify', 'invoke-command', 'invoke-prepared',
    'verify-approval', 'force-approval', 'fresh-review-run',
    'repair-disposition-run', 'recover', 'system-recover', 'repair-begin'), 'pending'),
    **{'invoke-command': 'covered', 'invoke-prepared': 'covered', 'verification-run': 'covered'}})
REPAIR_ENTRIES = ('repair-reverify', 'repair-disposition-run')
# Recovery inherits the class of the dispatch it recovers (design 881 §3.6).
CLASS_INHERITING_ENTRIES = ('recover', 'system-recover')
# Counts bound the stored rows; seconds bound one dispatch envelope.
POLICY_COUNT_MAXIMA = MappingProxyType({'max_concurrent_dispatches': 8, 'max_dispatches_per_phase': 64,
                                        'no_progress_limit': 32})
POLICY_SECONDS_MAX = 86400
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
_DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')
_TIME = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z')


class BudgetError(CanonicalStateEncodingError):
    def __init__(self, code):
        super().__init__(code, code)


def _fail(reason):
    raise BudgetError('budget-' + reason)


def _wire(value, depth=0):
    if depth > 32 or type(value) not in (dict, list, str, int, float, bool, type(None)):
        _fail('json-invalid')
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                _fail('json-invalid')
            _wire(child, depth + 1)
    elif type(value) is list:
        for child in value:
            _wire(child, depth + 1)


def _shape(value, keys, reason):
    if type(value) is not dict or set(value) != set(keys.split()):
        _fail(reason)
    return value


def _int(value, minimum=0, maximum=INT_MAX):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail('integer-invalid')
    return value


def _text(value, pattern=_ID):
    if type(value) is not str or not pattern.fullmatch(value):
        _fail('text-invalid')
    return value


def _at(value):
    _text(value, _TIME)
    try:
        return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except ValueError:
        _fail('timestamp-invalid')


def _iso(value):
    return value.isoformat(timespec='seconds').replace('+00:00', 'Z')


def _digest(value):
    return 'sha256:' + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
        allow_nan=False).encode('ascii')).hexdigest()


def total_seconds(budget_minutes):
    if type(budget_minutes) not in (int, float):
        _fail('total-invalid')
    try:
        value = Decimal(repr(budget_minutes)) * 60
        if not value.is_finite() or value <= 0 or value > INT_MAX:
            _fail('total-invalid')
        seconds = int(value.to_integral_value(rounding=ROUND_FLOOR))
    except (InvalidOperation, ValueError, OverflowError):
        _fail('total-invalid')
    if seconds < 1:
        _fail('total-too-small')
    return seconds


@dataclass(frozen=True)
class BudgetPolicy:
    total_sec: int
    external_deadline_at: str | None
    reserve_basis_points: tuple[int, ...]
    max_concurrent_dispatches: int
    max_dispatches_per_phase: int
    term_grace_sec: int
    kill_wait_sec: int
    collect_sec: int
    post_run_sec: int
    adapter_call_sec: int
    cancel_call_sec: int
    commit_margin_sec: int
    closeout_margin_sec: int
    min_dispatch_sec: int
    system_recovery_sec: int
    no_progress_limit: int
    reactivate: str
    provenance: str = 'experimental-initial'

    @property
    def phase_reserves(self):
        return {phase: self.total_sec * points // 10000
                for phase, points in zip(PHASES, self.reserve_basis_points)}

    @property
    def final_reserve_effective(self):
        return self.phase_reserves['final'] - self.system_recovery_sec

    @property
    def min_final_run_sec(self):
        return self.min_dispatch_sec + max(cleanup_sec(self, entry) for entry in (
            'verification-run', 'invoke-command', 'fresh-review-run', 'force-approval')) + self.commit_margin_sec

    @property
    def digest(self):
        return _digest(policy_document(self))


def default_policy_document(total_sec):
    """Experimental fixture/default builder, not a policy acceptance route."""
    return dict(schema=POLICY_SCHEMA, total_sec=total_sec, external_deadline_at=None,
                reserve_basis_points=dict(zip(PHASES, BASIS_POINTS)), protected_phases=['repair', 'final'],
                max_concurrent_dispatches=2, max_dispatches_per_phase=32, term_grace_sec=2,
                kill_wait_sec=1, collect_sec=2, post_run_sec=10, adapter_call_sec=30,
                cancel_call_sec=10, commit_margin_sec=10, closeout_margin_sec=30,
                min_dispatch_sec=1, system_recovery_sec=60, no_progress_limit=2,
                provenance='experimental-initial', reactivate='allowed')


def policy_document(policy):
    doc = asdict(policy)
    doc.update(schema=POLICY_SCHEMA, protected_phases=['repair', 'final'],
               reserve_basis_points=dict(zip(PHASES, policy.reserve_basis_points)))
    return doc


def _entry(value):
    # Membership on a mapping hashes first; reject non-str before it can raise TypeError.
    if type(value) is not str or value not in BUDGET_SPAWN_ENTRIES:
        _fail('entry-invalid')
    return value


def cleanup_sec(policy, entry):
    _entry(entry)
    group = policy.term_grace_sec + policy.kill_wait_sec
    if entry in ('verification-run', 'repair-reverify', 'repair-begin'):
        return 1 + policy.post_run_sec + group
    if entry in ('invoke-command', 'invoke-prepared'):
        return group + policy.collect_sec
    if entry in ('fresh-review-run', 'repair-disposition-run'):
        return 2 * group + policy.cancel_call_sec
    if entry in ('verify-approval', 'force-approval'):
        return 6 + policy.kill_wait_sec
    return group


def decode_policy(value, *, budget_minutes=None):
    _wire(value)
    _shape(value, ' '.join(default_policy_document(1)), 'policy-shape-invalid')
    if value['schema'] != POLICY_SCHEMA:
        _fail('policy-schema-invalid')
    total = _int(value['total_sec'], 1)
    if budget_minutes is not None and total != total_seconds(budget_minutes):
        _fail('total-mismatch')
    external = value['external_deadline_at']
    if external is not None:
        _at(external)
    points = _shape(value['reserve_basis_points'], ' '.join(PHASES), 'phase-invalid')
    basis = tuple(_int(points[phase], 0, 10000) for phase in PHASES)
    if sum(basis) != 10000 or value['protected_phases'] != ['repair', 'final']:
        _fail('phase-invalid')
    if value['provenance'] != 'experimental-initial' or value['reactivate'] not in ('allowed', 'forbidden'):
        _fail('policy-control-invalid')
    ints = {key: _int(value[key], 1, POLICY_COUNT_MAXIMA.get(key, POLICY_SECONDS_MAX))
            for key in default_policy_document(1)
            if key not in ('schema', 'total_sec', 'external_deadline_at', 'reserve_basis_points',
                           'protected_phases', 'provenance', 'reactivate')}
    policy = BudgetPolicy(total, external, basis, **ints, reactivate=value['reactivate'])
    if policy.system_recovery_sec < policy.adapter_call_sec + cleanup_sec(policy, 'recover') + policy.commit_margin_sec:
        _fail('recovery-reserve-too-small')
    if policy.final_reserve_effective < policy.min_final_run_sec:
        _fail('final-reserve-too-small')
    return policy


@dataclass(frozen=True)
class ActiveClock:
    closed_sec: int
    opened_at: str | None
    last_observed_at: str
    reserve_erosion_sec: int = 0


@dataclass(frozen=True)
class DispatchReservation:
    reservation_id: str
    entry: str
    budget_class: str
    target: str
    operation_id: str
    fencing_epoch: int
    reserved_at: str
    child_deadline_at: str
    settle_by: str
    reserved_sec: int
    reserved_bytes: int


@dataclass(frozen=True)
class PhaseCharge:
    charged_sec: int = 0
    reservation_count: int = 0
    open_count: int = 0


@dataclass(frozen=True)
class Observation:
    at: str
    elapsed_sec: int | None


@dataclass(frozen=True)
class Telemetry:
    tool_calls: int | None
    replays: int | None
    output_bytes: int | None


@dataclass(frozen=True)
class Settlement:
    reservation_id: str
    outcome: str
    charged_sec: int
    late_settlement_sec: int
    observed: Observation
    telemetry: Telemetry


@dataclass(frozen=True)
class ProgressSignature:
    entry: str
    target: str
    candidate_digest: str
    result_digest: str
    consecutive_count: int


@dataclass(frozen=True)
class FinalLatch:
    at: str
    reason: str


@dataclass(frozen=True)
class Exhaustion:
    at: str
    cause: str


@dataclass(frozen=True)
class BudgetStop:
    scope: str
    reason_code: str
    at: str
    ledger_digest: str
    open_reservations: int
    reserve_erosion_sec: int


@dataclass(frozen=True)
class SystemRecovery:
    used_sec: int = 0
    reservation_count: int = 0
    reservation: DispatchReservation | None = None


@dataclass(frozen=True)
class FinalRun:
    reservation_id: str
    at: str


@dataclass(frozen=True)
class StopSlots:
    refusal_count: int = 0
    last_refusal: str | None = None
    final_latch: FinalLatch | None = None
    exhaustion: Exhaustion | None = None
    budget_stop: BudgetStop | None = None
    system_recovery: SystemRecovery = SystemRecovery()
    final_run: FinalRun | None = None


@dataclass(frozen=True)
class BudgetProjection:
    policy: BudgetPolicy | None = None
    clock: ActiveClock | None = None
    reservations: tuple[DispatchReservation, ...] = ()
    phase_charges: tuple[PhaseCharge, ...] = ()
    settlements: tuple[Settlement, ...] = ()
    progress: tuple[ProgressSignature, ...] = ()
    stop_slots: StopSlots = StopSlots()


def new_ledger(policy, at, *, active=True):
    _at(at)
    return BudgetProjection(policy, ActiveClock(0, at if active else None, at),
                            phase_charges=tuple(PhaseCharge() for _ in PHASES))


def ledger_document(ledger):
    if ledger.policy is None:
        return None
    doc = asdict(ledger)
    doc['stop_slots'] = stop_slots_document(ledger.stop_slots)
    doc.update(schema=LEDGER_SCHEMA, policy=policy_document(ledger.policy),
               policy_digest=ledger.policy.digest,
               phase_charges=dict(zip(PHASES, map(asdict, ledger.phase_charges))))
    for key in ('reservations', 'settlements', 'progress'):
        doc[key] = list(doc[key])
    return doc


def stop_slots_document(slots):
    """Preallocate the largest wire shape. Stop-only updates never grow bytes.

    Pad for the deepest v5 extension placement in legacy pretty encoding; the
    canonical and shallower v4 forms shrink when an empty slot is filled.
    Recovery reservation changes remain normal writes, not stop-only writes.
    """
    row = {**asdict(slots), 'padding': ''}
    envelope = {'extensions': {'budget_ledger': {'stop_slots': row}}}
    size = len(json.dumps(envelope, ensure_ascii=True, indent=2).encode('ascii'))
    if size > STOP_SLOTS_MAX_BYTES:
        _fail('slot-limit-exceeded')
    row['padding'] = ' ' * (STOP_SLOTS_MAX_BYTES - size)
    return row


def _record(value, cls):
    _shape(value, ' '.join(cls.__dataclass_fields__), 'ledger-shape-invalid')
    return cls(**value)


def _reservation(value, policy, last, *, system=False):
    item = _record(value, DispatchReservation)
    for name in ('reservation_id', 'target', 'operation_id'):
        _text(getattr(item, name))
    if _entry(item.entry) == 'repair-begin':
        _fail('entry-invalid')
    if (item.entry == 'system-recover') != system or type(item.budget_class) is not str \
            or item.budget_class not in PHASES:
        _fail('class-invalid')
    # design 881 §3.1: the class is derived from the entry; only repair entries use repair.
    if item.entry not in CLASS_INHERITING_ENTRIES \
            and (item.entry in REPAIR_ENTRIES) != (item.budget_class == 'repair'):
        _fail('class-invalid')
    _int(item.fencing_epoch, 1)
    _int(item.reserved_sec, 1)
    _int(item.reserved_bytes)
    start, child, settle = map(_at, (item.reserved_at, item.child_deadline_at, item.settle_by))
    if not start <= child <= settle or start > last:
        _fail('reservation-time-invalid')
    if int((settle - start).total_seconds()) != item.reserved_sec:
        _fail('reservation-seconds-invalid')
    if (child - start).total_seconds() < policy.min_dispatch_sec:
        _fail('reservation-time-invalid')
    # Approval verifiers have a fixed run envelope rather than a timeout.
    cleanup = cleanup_sec(policy, item.entry)
    if item.entry in ('verify-approval', 'force-approval'):
        cleanup -= 6
        if (child - start).total_seconds() != 6:
            _fail('reservation-time-invalid')
    if (settle - child).total_seconds() != cleanup + policy.commit_margin_sec:
        _fail('reservation-time-invalid')
    return item


def _unique(items, key):
    keys = [key(item) for item in items]
    if len(keys) != len(set(keys)):
        _fail('duplicate-id')


def decode_ledger(document, *, embedded=False):
    """Missing key alone means absent. v4/v5 share this closed wire decoder."""
    if type(document) is not dict:
        _fail('ledger-shape-invalid')
    raw = document
    # A v4 extensions property is retained user data, not the v5 projection.
    if not embedded and document.get('schema_version') == 5:
        if 'budget_ledger' in document:
            _fail('ledger-location-invalid')
        raw = document.get('extensions', {})
        if type(raw) is not dict:
            _fail('ledger-shape-invalid')
    if 'budget_ledger' not in raw:
        return BudgetProjection()
    value = raw['budget_ledger']
    _wire(value)
    _shape(value, 'schema policy policy_digest clock reservations phase_charges settlements progress stop_slots',
           'ledger-shape-invalid')
    if value['schema'] != LEDGER_SCHEMA:
        _fail('ledger-schema-invalid')
    # A ledger binds total_sec to budget_minutes; a missing total cannot be matched.
    if raw.get('budget_minutes') is None:
        _fail('total-mismatch')
    policy = decode_policy(value['policy'], budget_minutes=raw['budget_minutes'])
    if _text(value['policy_digest'], _DIGEST) != policy.digest:
        _fail('policy-digest-mismatch')
    clock = _record(value['clock'], ActiveClock)
    _int(clock.closed_sec)
    _int(clock.reserve_erosion_sec)
    last = _at(clock.last_observed_at)
    if clock.opened_at is not None and _at(clock.opened_at) > last:
        _fail('clock-regressed')
    for name, bound in (('reservations', policy.max_concurrent_dispatches),
                        ('settlements', 32), ('progress', 5 * policy.max_dispatches_per_phase)):
        if type(value[name]) is not list or len(value[name]) > bound:
            _fail('ledger-limit-exceeded')
    reservations = tuple(_reservation(item, policy, last) for item in value['reservations'])
    _unique(reservations, lambda item: item.reservation_id)
    charges = _shape(value['phase_charges'], ' '.join(PHASES), 'phase-invalid')
    phase_charges = tuple(_record(charges[phase], PhaseCharge) for phase in PHASES)
    for phase, charge in zip(PHASES, phase_charges):
        _int(charge.charged_sec)
        _int(charge.reservation_count, 0, policy.max_dispatches_per_phase)
        _int(charge.open_count, 0, charge.reservation_count)
        if charge.open_count != sum(item.budget_class == phase for item in reservations):
            _fail('open-count-mismatch')
    settlements = []
    for row in value['settlements']:
        row = dict(_shape(row, ' '.join(Settlement.__dataclass_fields__), 'ledger-shape-invalid'))
        row['observed'] = _record(row['observed'], Observation)
        row['telemetry'] = _record(row['telemetry'], Telemetry)
        item = Settlement(**row)
        _text(item.reservation_id)
        if item.outcome not in ('settled', 'charged-full-unknown', 'kill-unconfirmed'):
            _fail('settlement-outcome-invalid')
        _int(item.charged_sec)
        _int(item.late_settlement_sec)
        if _at(item.observed.at) > last:
            _fail('clock-regressed')
        for observed in (item.observed.elapsed_sec, *asdict(item.telemetry).values()):
            if observed is not None:
                _int(observed)
        settlements.append(item)
    _unique(settlements, lambda item: item.reservation_id)
    progress = tuple(_record(row, ProgressSignature) for row in value['progress'])
    for item in progress:
        _entry(item.entry)
        _text(item.target)
        _text(item.candidate_digest, _DIGEST)
        _text(item.result_digest, _DIGEST)
        _int(item.consecutive_count)
    _unique(progress, lambda item: (item.entry, item.target))
    slots = dict(_shape(value['stop_slots'], ' '.join(StopSlots.__dataclass_fields__) + ' padding', 'slot-shape-invalid'))
    padding = slots.pop('padding')
    if type(padding) is not str:
        _fail('slot-shape-invalid')
    _int(slots['refusal_count'])
    if slots['last_refusal'] is not None:
        _text(slots['last_refusal'])
    for key, cls in (('final_latch', FinalLatch), ('exhaustion', Exhaustion),
                     ('budget_stop', BudgetStop), ('final_run', FinalRun)):
        if slots[key] is not None:
            slots[key] = _record(slots[key], cls)
            if _at(slots[key].at) > last:
                _fail('clock-regressed')
    if slots['final_latch'] is not None:
        _text(slots['final_latch'].reason)
    if slots['exhaustion'] is not None and slots['exhaustion'].cause not in (
            'overall-deadline', 'closeout-margin', 'final-infeasible'):
        _fail('exhaustion-cause-invalid')
    stop = slots['budget_stop']
    if stop is not None:
        _text(stop.scope)
        _text(stop.reason_code)
        _text(stop.ledger_digest, _DIGEST)
        _int(stop.open_reservations, 0, policy.max_concurrent_dispatches + 1)
        _int(stop.reserve_erosion_sec)
    recovery = dict(_shape(slots['system_recovery'], ' '.join(SystemRecovery.__dataclass_fields__), 'slot-shape-invalid'))
    _int(recovery['used_sec'], 0, policy.system_recovery_sec)
    _int(recovery['reservation_count'])
    if recovery['reservation'] is not None:
        recovery['reservation'] = _reservation(recovery['reservation'], policy, last, system=True)
        held = recovery['reservation']
        killed = next((item for item in settlements if item.reservation_id == held.reservation_id
                       and item.outcome == 'kill-unconfirmed'), None)
        if killed is not None:
            if killed.charged_sec != held.reserved_sec or recovery['used_sec'] < killed.charged_sec:
                _fail('settlement-charge-invalid')
        elif recovery['used_sec'] + held.reserved_sec > policy.system_recovery_sec:
            _fail('recovery-reserve-too-small')
        _unique((*reservations, recovery['reservation']), lambda item: item.reservation_id)
    slots['system_recovery'] = SystemRecovery(**recovery)
    opened = {r.reservation_id for r in reservations}
    if recovery['reservation'] is not None:
        opened.add(recovery['reservation'].reservation_id)
    if any(item.reservation_id in opened and item.outcome != 'kill-unconfirmed' for item in settlements):
        _fail('settlement-open-invalid')
    if slots['final_run'] is not None:
        _text(slots['final_run'].reservation_id)
        if slots['final_latch'] is None or _at(slots['final_run'].at) < _at(slots['final_latch'].at):
            _fail('final-run-invalid')
    decoded_slots = StopSlots(**slots)
    if stop_slots_document(decoded_slots) != value['stop_slots']:
        _fail('slot-padding-invalid')
    return BudgetProjection(policy, clock, reservations, phase_charges, tuple(settlements), progress, decoded_slots)


def validate_projection_backing(document, projection, *, embedded=False):
    if decode_ledger(document, embedded=embedded) != projection:
        _fail('projection-backing-mismatch')


def active_seconds(clock, at):
    now = _at(at)
    if now < _at(clock.last_observed_at):
        _fail('clock-regressed')
    return clock.closed_sec + (int((now - _at(clock.opened_at)).total_seconds())
                               if clock.opened_at is not None else 0)


@dataclass(frozen=True)
class Deadlines:
    overall: str
    final: str
    repair: str
    unprotected: str
    repair_unspent: int
    final_unspent: int


def deadlines(ledger, at):
    policy = ledger.policy
    consumed = active_seconds(ledger.clock, at)
    try:
        overall = _at(at) + timedelta(seconds=policy.total_sec - consumed)
        if policy.external_deadline_at is not None:
            overall = min(overall, _at(policy.external_deadline_at))
        final = overall - timedelta(seconds=policy.closeout_margin_sec + policy.system_recovery_sec)
        unspent = {}
        for phase in ('repair', 'final'):
            target = policy.phase_reserves[phase] if phase == 'repair' else policy.final_reserve_effective
            charge = ledger.phase_charges[PHASES.index(phase)].charged_sec
            opened = sum(item.reserved_sec for item in ledger.reservations if item.budget_class == phase)
            unspent[phase] = max(0, target - charge - opened)
        repair = final - timedelta(seconds=unspent['final'])
        shared = repair - timedelta(seconds=unspent['repair'])
    except (OverflowError, ValueError):
        _fail('time-overflow')
    return Deadlines(_iso(overall), _iso(final), _iso(repair), _iso(shared), unspent['repair'], unspent['final'])


def final_infeasible(ledger, at):
    return (_at(deadlines(ledger, at).final) - _at(at)).total_seconds() < ledger.policy.min_final_run_sec


def exhaustion(ledger, at):
    d = deadlines(ledger, at)  # Checks clock regression even when a slot exists.
    if ledger.stop_slots.exhaustion is not None:
        return ledger.stop_slots.exhaustion
    opened = any(item.budget_class == 'final' for item in ledger.reservations)
    if _at(at) >= _at(d.overall):
        return Exhaustion(at, 'overall-deadline')
    if _at(at) >= _at(d.overall) - timedelta(seconds=ledger.policy.closeout_margin_sec) and not opened:
        return Exhaustion(at, 'closeout-margin')
    if final_infeasible(ledger, at) and ledger.stop_slots.final_run is None and not opened:
        return Exhaustion(at, 'final-infeasible')
    return None


def child_deadlines(ledger, at, entry, budget_class, policy_timeout):
    """Pure timing envelope. Admission/writers must separately check eligibility."""
    d = deadlines(ledger, at)
    policy = ledger.policy
    _int(policy_timeout, 1)
    if budget_class not in PHASES:
        _fail('class-invalid')
    bound = d.repair if budget_class == 'repair' else d.final if budget_class == 'final' else d.unprotected
    cleanup = cleanup_sec(policy, entry)
    now = _at(at)
    if entry in ('verify-approval', 'force-approval'):
        cleanup = policy.kill_wait_sec
        run_sec = 6
        if run_sec + cleanup + policy.commit_margin_sec > (_at(bound) - now).total_seconds():
            _fail('dispatch-time-insufficient')
    elif entry == 'system-recover':
        recovery = ledger.stop_slots.system_recovery
        if recovery.reservation is not None:
            _fail('recovery-open')
        left = policy.system_recovery_sec - recovery.used_sec
        run_sec = min(policy.adapter_call_sec,
                      int((_at(d.overall) - now).total_seconds()) - cleanup - policy.commit_margin_sec,
                      left - cleanup - policy.commit_margin_sec)
    else:
        # Clip integer durations before calendar arithmetic: a large, valid
        # timeout must not overflow timedelta before min() selects the bound.
        run_sec = min(policy_timeout, int((_at(bound) - now).total_seconds()) - cleanup - policy.commit_margin_sec)
    if run_sec < policy.min_dispatch_sec:
        _fail('recovery-allowance-exhausted' if entry == 'system-recover' else 'dispatch-time-insufficient')
    reserved = run_sec + cleanup + policy.commit_margin_sec
    return _iso(now + timedelta(seconds=run_sec)), _iso(now + timedelta(seconds=reserved)), reserved



def reserve_erosion_sec(ledger, at):
    """Accrued erosion plus the current observation interval, never halt time."""
    active_seconds(ledger.clock, at)
    erosion = ledger.clock.reserve_erosion_sec
    if ledger.clock.opened_at is None or any(r.budget_class == 'final' for r in ledger.reservations):
        return erosion
    start = _at(deadlines(ledger, at).unprotected)
    if ledger.stop_slots.final_latch is not None:
        start = min(start, _at(ledger.stop_slots.final_latch.at))
    start = max(start, _at(ledger.clock.last_observed_at), _at(ledger.clock.opened_at))
    return erosion + max(0, int((_at(at) - start).total_seconds()))


def observe_clock(ledger, at, *, active):
    """Pure clock segment update, ready for subsequent lifecycle writers."""
    seconds = active_seconds(ledger.clock, at)
    erosion = reserve_erosion_sec(ledger, at)
    return ActiveClock(seconds, at if active else None, at, erosion)


def budget_status(ledger, at, capacity=None):
    unmeasured = {'status': 'unmeasured', 'reason': 'no-producer'}
    if ledger.policy is None:
        return dict(enforcement='advisory-only', token=unmeasured, cost=unmeasured)
    policy = ledger.policy
    consumed = active_seconds(ledger.clock, at)
    d = deadlines(ledger, at)
    phases = {phase: dict(target_sec=policy.phase_reserves[phase], **asdict(charge),
                         open_reserved_sec=sum(r.reserved_sec for r in ledger.reservations if r.budget_class == phase))
              for phase, charge in zip(PHASES, ledger.phase_charges)}
    exhausted = exhaustion(ledger, at)
    slots = {**asdict(ledger.stop_slots), 'exhaustion': asdict(exhausted) if exhausted is not None else None}
    return dict(enforcement='advisory-only', activation='pending', policy=policy_document(policy), policy_digest=policy.digest,
                provenance=policy.provenance, total_sec=policy.total_sec, consumed_sec=consumed,
                remaining_sec=max(0, policy.total_sec - consumed), external_deadline_at=policy.external_deadline_at,
                final_reserve_effective=policy.final_reserve_effective, phases=phases, deadlines=asdict(d),
                reservations=[asdict(r) for r in ledger.reservations],
                progress=[asdict(p) for p in ledger.progress], **slots,
                state_capacity_verdict=capacity, token=unmeasured, cost=unmeasured,
                reserve_erosion_sec=reserve_erosion_sec(ledger, at))
