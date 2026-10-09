"""Withdraw one pending request through the E0 fenced shrink-only gate."""
from __future__ import annotations
from pathlib import Path
import json
import secrets

from mission_application.cli_operation import prepare_cli_operation, CliOperationRejected
from mission_application.fresh_review_dispatch import _execute, _record, _wire
from mission_kernel.commands import WithdrawFreshReviewRequest
from mission_kernel.fresh_review import FreshReviewError, WithdrawnFreshReviewRecord


def run_fresh_review_withdraw_cli(args, services):
    root = Path.cwd()
    sf = services.resolve_state_file(root)
    if not sf.exists():
        services.fail('fresh-review-state-missing', 2)
    try:
        identity = prepare_cli_operation('fresh-review-withdraw', {'request_id': args.request},
            session_id=sf.stem, compatibility_arguments=services.compatibility_arguments,
            canonical_operation=services.canonical_operation)
        operation = identity.operation_id or 'withdraw:' + secrets.token_hex(16)
        reader = services.repository(root, sf, stamp=False, strict_read=True)
        with reader.transaction():
            record = _record(reader.load(), args.request)
        if isinstance(record, WithdrawnFreshReviewRecord):
            if record.withdraw_operation_id != operation:
                raise FreshReviewError('fresh-review-request-withdrawn')
            return json.dumps({'ok': True, 'record': _wire(record)})
        # No candidate scan or adapter callback. Withdrawal drops just the
        # pending body; persistence derives write_kind and enforces E0 bounds.
        repository = services.repository(root, sf, stamp=False, strict_read=True, pre_admit_lease=True,
            session_id=sf.stem, operation_id=identity.operation_id, operation_command=identity.operation_command,
            operation_command_type=identity.command_type)
        record = _execute(repository, lambda state: WithdrawFreshReviewRequest(
            args.request, operation, state.get('fencing_epoch', 0)))
        return json.dumps({'ok': True, 'record': _wire(record)})
    except (FreshReviewError, CliOperationRejected) + services.commit_errors as exc:
        services.fail(exc.code, 2)
