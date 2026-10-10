"""Fenced admission for the public verification supervisor (policy remains inert)."""
from __future__ import annotations

from datetime import datetime, timezone
import secrets
import time

from mission_kernel.budget import decode_ledger, cleanup_sec
from mission_kernel.commands import ReserveDispatchBudget, RecordBudgetRefusal
from .provider_budget import ProviderBudget, _apply
from .artifact import EvidenceFailure


def reserve_verification(document, criterion, timeout, at, candidate_digest, terminal_bytes=64 * 1024, *, entry="verification-run", operation=None):
    if decode_ledger(document).policy is None:
        return None, None
    command = ReserveDispatchBudget(at, entry, criterion,
        operation or 'verification:' + secrets.token_hex(16), int(document.get('fencing_epoch') or 1),
        timeout, terminal_bytes, candidate_digest)
    started, wall = time.monotonic(), datetime.now(timezone.utc)
    result = _apply(document, command)
    if not result.accepted:
        _apply(document, RecordBudgetRefusal(at, command))
        return None, result.rejection.code
    ledger = decode_ledger(document)
    row = next(r for r in ledger.reservations if r.operation_id == command.operation_id)
    remaining = (datetime.fromisoformat(row.child_deadline_at.replace('Z', '+00:00')) - wall).total_seconds()
    return ProviderBudget(row, ledger.policy, candidate_digest, started + remaining, started), None


def admit_verification(repository, criterion, timeout, at, candidate_digest, command):
    with repository.transaction():
        document = repository.load()
        budget, reason = reserve_verification(document, criterion, timeout, at, candidate_digest, verification_terminal_bytes(command))
        if budget is not None or reason is not None:
            repository.save(document)
    if reason is not None:
        raise EvidenceFailure(reason)
    return budget, document


def execute_verification(document, root, criterion, repro_input, budget, command, *, session_id):
    from budgeted_exec import run_job
    from mission_kernel.budget_decisions import stalled_candidate
    from .verification_execution import _blocked_receipt
    try:
        receipt = run_job('verification', {'contract': document['acceptance_contract'],
            'criterion': criterion, 'repro_input': repro_input, 'deadline': budget.deadline,
            'no_progress_candidate': stalled_candidate(decode_ledger(document), budget.reservation.entry, budget.reservation.target)},
            root / '.mission-state' / 'exec-jobs', deadline=budget.deadline,
            collect_deadline=budget.deadline + 1 + budget.policy.post_run_sec,
            term_grace=budget.policy.term_grace_sec, kill_wait=budget.policy.kill_wait_sec,
            reservation_id=budget.reservation.reservation_id.replace(':', '_'), cwd=root, session_id=session_id)
        run_sec = budget.reservation.reserved_sec - cleanup_sec(budget.policy, budget.reservation.entry) - budget.policy.commit_margin_sec
        if receipt['block_reason'] == 'budget-deadline' and receipt['exit_code'] is not None and run_sec >= command['timeout_sec']:
            receipt['block_reason'] = 'timeout'
        return receipt
    except Exception as exc:
        # Without a supervisor frame we cannot prove its nested verifier group
        # was reclaimed, even when the outer supervisor group was reclaimed.
        reason = 'kill-unconfirmed'
        contract = document['acceptance_contract']
        if getattr(exc, 'exec_unstarted', False):
            reason = 'budget-deadline' if isinstance(exc, TimeoutError) else getattr(exc, 'reason_code', 'budget-deadline-unenforceable')
        receipt = _blocked_receipt(contract, contract['verifier_policy'], criterion, command, reason)
        receipt.update(started_at=budget.reservation.reserved_at,
            finished_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            timed_out=isinstance(exc, TimeoutError), output_truncated=isinstance(exc, TimeoutError))
        return receipt


def verification_settlement(budget, at, receipt):
    from mission_kernel.commands import SettleDispatchBudget
    from mission_kernel.budget_decisions import verification_result_digest, verification_refusal_reason
    confirmed = receipt['block_reason'] != 'kill-unconfirmed'
    refusal = verification_refusal_reason(receipt)
    return SettleDispatchBudget(at, budget.reservation.reservation_id,
        'settled' if confirmed else 'kill-unconfirmed',
        (0 if refusal is not None and refusal != 'budget-no-new-evidence' else max(0, int(time.monotonic() - budget.started))) if confirmed else None,
        receipt['candidate_digest'], verification_result_digest(receipt),
        output_bytes=receipt['observed_output_bytes'], completed=receipt['status'] in ('passed', 'failed'),
        refusal_reason=refusal)


def settle_failed_verification(services, root, state_file, budget, reason):
    from .provider_budget import settle_provider
    import hashlib
    repository = services.repository(root, state_file, stamp=True, pre_admit_lease=True, session_id=state_file.stem)
    with repository.transaction():
        document = repository.load()
        settle_provider(document, budget, services.now(), 'sha256:' + hashlib.sha256(reason.encode()).hexdigest(), unstarted=True)
        repository.save(document)


def settle_collected_verification(services, root, state_file, budget, receipt):
    from dataclasses import replace
    repository = services.repository(root, state_file, stamp=True, pre_admit_lease=True, session_id=state_file.stem)
    with repository.transaction():
        document = repository.load()
        result = _apply(document, replace(verification_settlement(budget, services.now(), receipt), completed=False))
        if not result.accepted:
            raise EvidenceFailure(result.rejection.code)
        repository.save(document)


def verification_terminal_bytes(command):
    """Reserve variable argv/cwd bytes plus worst-depth pretty-encoding slack."""
    import json
    variable = {'argv': command['argv'], 'relative_cwd': command['relative_cwd']}
    return 64 * 1024 + len(json.dumps(variable, ensure_ascii=True, indent=2).encode()) + 32 * len(command['argv'])
