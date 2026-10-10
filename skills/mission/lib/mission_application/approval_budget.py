"""Short fenced admission and settlement for the two approval spawn entries."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import secrets
import time

from mission_kernel.budget import decode_ledger
from mission_kernel.budget_decisions import completion_rejection, approval_result_digest
from mission_kernel.commands import ReserveDispatchBudget, RecordBudgetRefusal, SettleDispatchBudget
from .provider_budget import ProviderBudget, _apply


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


@dataclass
class ApprovalBudget:
    permit: ProviderBudget
    result: dict | None = None
    failure: BaseException | None = None
    unstarted: bool = True


def admit_approval(repository, entry, target):
    """The reservation save must finish before entering the caller's transaction."""
    with repository.transaction():
        document = repository.load()
        ledger = decode_ledger(document)
        if ledger.policy is None:
            return None
        at = now()
        if entry == 'force-approval':
            reason = completion_rejection(ledger, at)
            if reason is not None:
                raise ValueError(reason)
            from scoring_provenance import terminal_state_digest
            terminal = dict(document, passes=True, loop_active=False, passes_forced=True, terminal_outcome='completed_pass')
            candidate = terminal_state_digest(terminal)
        else:
            pointer = (document.get('provider_preflights') or {}).get(target)
            if not isinstance(pointer, dict) or pointer.get('status') != 'awaiting-approval':
                raise ValueError('preflight-not-awaiting-approval')
            candidate = pointer['outbound_packet_digest']
        command = ReserveDispatchBudget(at, entry, target, 'approval:' + secrets.token_hex(16),
            int(document.get('fencing_epoch') or 1), 5, 64 * 1024, candidate)
        started, wall = time.monotonic(), datetime.now(timezone.utc)
        decision = _apply(document, command)
        if not decision.accepted:
            _apply(document, RecordBudgetRefusal(at, command))
            repository.save(document)
            raise ValueError(decision.rejection.code)
        held = decode_ledger(document)
        row = next(r for r in held.reservations if r.operation_id == command.operation_id)
        deadline = started + (datetime.fromisoformat(row.child_deadline_at.replace('Z', '+00:00')) - wall).total_seconds()
        repository.save(document)
        return ApprovalBudget(ProviderBudget(row, held.policy, command.candidate_digest, deadline, started))


def approval_settlement(budget, at, *, completed=False):
    failure = budget.failure
    confirmed = str(failure) != 'kill-unconfirmed'
    reason = None
    # Caller gates and user interruptions are not deadline-enforcement refusals.
    if budget.unstarted and isinstance(failure, Exception):
        reason = getattr(failure, 'reason_code', None) or (
            'budget-deadline' if isinstance(failure, TimeoutError) else 'budget-deadline-unenforceable')
    result = budget.result if budget.result is not None else {'failure': type(failure).__name__}
    permit = budget.permit
    return SettleDispatchBudget(at, permit.reservation.reservation_id,
        'settled' if confirmed else 'kill-unconfirmed',
        (0 if budget.unstarted else max(0, int(time.monotonic() - permit.started))) if confirmed else None,
        permit.candidate_digest, approval_result_digest(result), completed=completed, refusal_reason=reason)


def settle_approval(document, budget, *, completed=False):
    if budget is None:
        return
    decision = _apply(document, approval_settlement(budget, now(), completed=completed))
    if not decision.accepted:
        raise ValueError(decision.rejection.code)


def settle_rejected_approval(repository, budget):
    if budget is None:
        return
    with repository.transaction():
        document = repository.load()
        settle_approval(document, budget)
        repository.save(document)
