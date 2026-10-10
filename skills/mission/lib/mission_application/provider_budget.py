"""Budget decisions composed into the provider's existing fenced commits."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import time

from mission_kernel import decode_mission_state
from mission_kernel.budget import BudgetPolicy, DispatchReservation, decode_ledger, ledger_document
from mission_kernel.commands import ReserveDispatchBudget, RecordBudgetRefusal, SettleDispatchBudget
from mission_kernel.transitions import decide

# Terminal metadata, receipt pointer, outcome and selection updates; output is
# archived separately. Kernel settlement-row bytes are reserved by F1 itself.
PROVIDER_TERMINAL_BYTES = 64 * 1024


@dataclass(frozen=True)
class ProviderBudget:
    reservation: DispatchReservation
    policy: BudgetPolicy
    candidate_digest: str | None
    deadline: float
    started: float


def _apply(document, command):
    state = decode_mission_state(json.dumps(document, allow_nan=False).encode())
    decision = decide(state, command)
    if decision.accepted:
        document['budget_ledger'] = ledger_document(decision.transition.new_state.budget)
    return decision


def reserve_provider(document, entry, timeout, at, *, prepared, enforceable=True, provider=None):
    """Caller holds the repository lock; returning is not a spawn permit yet."""
    ledger = decode_ledger(document)
    if ledger.policy is None:
        return None, None
    if not enforceable:
        return None, 'budget-deadline-unenforceable'
    terminal_bytes = PROVIDER_TERMINAL_BYTES + 2 * len(json.dumps(provider or {}, ensure_ascii=True).encode())
    command = ReserveDispatchBudget(at, 'invoke-prepared' if prepared else 'invoke-command',
        entry['invocation_id'], entry['operation_id'], entry['fencing_epoch'], timeout,
        terminal_bytes, entry['outbound_packet_digest'])
    start = time.monotonic()
    wall = datetime.now(timezone.utc)
    result = _apply(document, command)
    if not result.accepted:
        _apply(document, RecordBudgetRefusal(at, command))
        return None, result.rejection.code
    held = decode_ledger(document)
    row = next(r for r in held.reservations if r.entry == command.entry
               and r.target == command.target and r.operation_id == command.operation_id
               and r.fencing_epoch == command.fencing_epoch)
    remaining = (datetime.fromisoformat(row.child_deadline_at.replace('Z', '+00:00')) -
                 wall).total_seconds()
    return ProviderBudget(row, held.policy, command.candidate_digest, start + remaining, start), None


def settle_provider(document, budget, at, result_digest, *, confirmed=True, output_bytes=None, completed=False, unstarted=False, refusal_reason=None):
    if budget is None:
        return
    result = _apply(document, SettleDispatchBudget(at, budget.reservation.reservation_id,
        'settled' if confirmed else 'kill-unconfirmed',
        (0 if unstarted else max(0, int(time.monotonic() - budget.started))) if confirmed else None,
        budget.candidate_digest or 'sha256:' + '0' * 64, result_digest,
        output_bytes=output_bytes, completed=completed, refusal_reason=refusal_reason))
    if not result.accepted:
        raise ValueError(result.rejection.code)
