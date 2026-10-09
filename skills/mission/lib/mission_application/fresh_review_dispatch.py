"""Fenced fresh-review dispatch using the existing provider saga primitives."""
from __future__ import annotations
from pathlib import Path
import json
import secrets
import base64
import time
import math
from datetime import datetime, timedelta, timezone

from mission_application.cli_operation import prepare_cli_operation, CliOperationRejected
from mission_application.planning import record_dispatch_intent, record_provider_receipt, reconcile_dispatch_unknown, PlanningFailure
from mission_application.fresh_review import _capture
from mission_application.verification_runner import VerificationRunnerError
from mission_application.verifier_policy import validate, VerifierPolicyError, SCHEMA
from mission_kernel.commands import BeginFreshReviewDispatch, CommitFreshReviewResult, RecordFreshReviewLaunch
from mission_kernel.fresh_review import (
    FreshReviewError, canonical_digest, decode_projection, candidate_identity, request_document,
    WithdrawnFreshReviewRecord, record_operation_ids,
)
from mission_kernel.fresh_review_receipts import decode_terminal_receipt, receipt_document, TERMINAL_SCHEMA
from mission_kernel.fresh_review_dispatch import validate_launch, reservation_id_for_operation, budget_class_for_fresh_review_dispatch
from mission_kernel.json_codec import freeze_json_value


def _record(state, request_id):
    matches = [item for item in decode_projection(state).requests if (item.request_id if isinstance(item, WithdrawnFreshReviewRecord)
                   else item.request.request_id) == request_id]
    if len(matches) != 1:
        raise FreshReviewError('fresh-review-request-unavailable')
    return matches[0]


def _candidate(state, root, request, services, *, allow_changed=False):
    from acceptance_contract import canonical_contract_digest
    contract = state.get('acceptance_contract')
    if (not isinstance(contract, dict) or canonical_contract_digest(contract) != request.contract_digest
            or state['iteration'] != request.iteration):
        raise FreshReviewError('fresh-review-stale')
    policy = contract['verifier_policy']
    validate({'schema': SCHEMA, 'commands': list(policy['commands'].values())})
    if services.load_verifier_policy(root)['digest'] != request.verifier_policy_digest:
        raise FreshReviewError('fresh-review-stale')
    commands = {item.command_id: policy['commands'][item.command_id] for item in request.candidate_bindings}
    digest = candidate_identity({key: value.digest for key, value in _capture(root, commands).items()})
    if digest != request.candidate_digest and not allow_changed:
        raise FreshReviewError('fresh-review-stale')
    return digest


SAGA_INTENT_FIELDS = ('invocation_id', 'operation_id', 'outbound_packet_digest', 'iteration', 'fencing_epoch')


def _saga_intent(record):
    dispatch = record.dispatch.thaw()
    return {key: dispatch[key] for key in SAGA_INTENT_FIELDS}


def _wire(record):
    from mission_kernel.fresh_review import FreshReviewProjection, projection_document
    return projection_document(FreshReviewProjection((record,)))['requests'][0]


def _execute(repository, build):
    # Reuse the provider saga's transaction/load/execute publication path. Lease
    # admission happens before building the command, including a v5 takeover.
    with repository.transaction():
        state = repository.load()
        command = build(state)
        result = repository.execute(command)
        if not result.decision.accepted:
            raise FreshReviewError(result.decision.rejection.code)
        return _record(result.projection, command.request_id)


def _utc(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def _terminal(record, operation, epoch, outcome, reason, now, *, attempted=False, cancel='not-requested', wall_time_sec=0):
    request, dispatch = record.request, record.dispatch.thaw()
    # A recovered host clock can be ahead, or the local clock can roll back.
    # Keep the known launch timestamp as a lower bound on this unknown terminal;
    # this does not assert child completion or invent successful output.
    ended_at = max(_utc(now), record.launch.thaw()['started_at'] if record.launch is not None else _utc(now))
    raw = dict(schema=TERMINAL_SCHEMA, request_id=request.request_id,
               request_digest=canonical_digest(request_document(request)), nonce=request.nonce,
               dispatch_operation_id=dispatch['operation_id'], dispatch_fencing_epoch=dispatch['fencing_epoch'],
               commit_operation_id=operation, commit_fencing_epoch=epoch, outcome=outcome, reason=reason,
               candidate_digest=request.candidate_digest, ended_at=ended_at,
               budget_used=dict(wall_time_sec=wall_time_sec, tool_calls=0, replays=0, output_bytes=0))
    if outcome == 'blocked':
        raw.update(launch_attempted=attempted, cancel_result=cancel)
    elif record.launch is not None:
        raw.update(launch_receipt=record.launch.thaw(), launch_digest=canonical_digest(record.launch.thaw()))
    return freeze_json_value(receipt_document(decode_terminal_receipt(raw)))


def run_fresh_review_dispatch_cli(args, services, host):
    root = Path.cwd()
    sf = services.resolve_state_file(root)
    if not sf.exists():
        services.fail('fresh-review-state-missing', 2)
    arguments = {'request_id': args.request, 'adapter': args.adapter}
    command_type = 'fresh-review-' + args.fresh_review_command
    intent = canonical_digest({'type': command_type, **arguments})
    payload = canonical_digest(arguments)
    try:
        identity = prepare_cli_operation(command_type, arguments, session_id=sf.stem,
                                         compatibility_arguments=services.compatibility_arguments,
                                         canonical_operation=services.canonical_operation)
        operation = identity.operation_id or 'dispatch:' + secrets.token_hex(16)

        def repo(suffix, stamp=True):
            return services.repository(root, sf, stamp=stamp, strict_read=True, pre_admit_lease=stamp,
                session_id=sf.stem, operation_id=('stage:' + canonical_digest({'domain': 'fresh-review-stage',
                    'operation_id': operation, 'stage': suffix})[7:] if identity.operation_id is not None else None),
                operation_command=identity.operation_command, operation_command_type=command_type)

        reader = repo(':read', False)
        with reader.transaction():
            snapshot = reader.load()
            record = _record(snapshot, args.request)
        if args.fresh_review_command == 'reconcile':
            if any(operation in record_operation_ids(item) for item in decode_projection(snapshot).requests if item != record):
                raise FreshReviewError('fresh-review-operation-conflict')
            return _reconcile(record, args, operation, repo, root, services, host)
        if isinstance(record, WithdrawnFreshReviewRecord):
            raise FreshReviewError('fresh-review-request-withdrawn')
        if record.status != 'pending':
            if (record.operation_id, record.intent_digest, record.payload_digest) != (operation, intent, payload):
                raise FreshReviewError('fresh-review-operation-conflict')
            return json.dumps({'ok': True, 'record': _wire(record)})
        capacity = services.capacity_status(sf)
        if capacity['code'] is not None:
            raise FreshReviewError(capacity['code'])
        reason = 'registration-mismatch'
        parent = 'unobservable'
        callback_start, adapter_called = time.monotonic(), False
        try:
            pin = host.resolve(args.adapter)
            if pin.registration.digest != record.request.adapter_registration_digest:
                raise FreshReviewError('fresh-review-adapter-pin-changed')
            adapter_called = True
            parent = host.observe(pin)['parent_identity']
        except (FreshReviewError, OSError) as exc:
            reason = 'identity-unobservable' if getattr(exc, 'code', '') == 'fresh-review-identity-unobservable' else 'registration-mismatch'
            pin = None

        def begin(state):
            current = _record(state, args.request)
            epoch = state.get('fencing_epoch', 0)
            dispatch = record_dispatch_intent([], dict(invocation_id='inv_' + canonical_digest(args.request)[7:39], operation_id=operation,
                outbound_packet_digest=current.request.input_digest, iteration=current.request.iteration, fencing_epoch=epoch))
            deadline = datetime.fromisoformat(services.now().replace('Z', '+00:00')) + timedelta(seconds=current.request.wall_time_sec)
            dispatch.update(parent_identity=parent, adapter_id=args.adapter, deadline_at=_utc(deadline.isoformat()),
                reservation_id=reservation_id_for_operation(operation),
                budget_class=budget_class_for_fresh_review_dispatch())
            return BeginFreshReviewDispatch(args.request, operation, epoch, intent, payload,
                freeze_json_value(dispatch), _candidate(state, root, current.request, services))

        record = _execute(repo(':intent'), begin)
        attempted, cancel = False, 'not-requested'
        if pin is not None:
            try:
                result = host.launch(pin, root, record.request, record.dispatch.thaw())
            except (FreshReviewError, OSError, ValueError):
                result = {'blocked': 'input-unobservable', 'attempted': False}
            if result.get('unknown'):
                return json.dumps({'ok': True, 'record': _wire(record)})
            if 'blocked' in result:
                reason, attempted = result['blocked'], result.get('attempted', False)
            else:
                attempted = True
                try:
                    raw = result.get('launch_receipt')
                    launch, _ = validate_launch(record.request, record.dispatch.thaw(), raw)
                    record_provider_receipt([record.dispatch.thaw()], _saga_intent(record),
                                            {'kind': 'provider', 'identity': launch.child_identity})
                    dispatch_epoch = record.dispatch.thaw()['fencing_epoch']
                    record = _execute(repo(':running'), lambda state: RecordFreshReviewLaunch(
                        args.request, operation, dispatch_epoch, freeze_json_value(raw),
                        _candidate(state, root, record.request, services)))
                    return json.dumps({'ok': True, 'record': _wire(record)})
                except (FreshReviewError, PlanningFailure) as exc:
                    if exc.code in ('fresh-review-stale-fence', 'fresh-review-not-dispatch-unknown', 'fresh-review-consumed'):
                        raise
                    reason = ('identity-unobservable' if isinstance(raw, dict) and
                              any(not raw.get(key) for key in ('parent_identity', 'child_identity', 'context_identity'))
                              else 'binding-mismatch' if exc.code.endswith('binding-mismatch')
                              else 'capability-unenforceable' if exc.code.endswith('unenforceable') else 'launch-invalid')
            if attempted:
                cancel = host.cancel(pin, record.dispatch.thaw())
                if cancel != 'cancelled':
                    raise FreshReviewError('fresh-review-kill-unconfirmed')
        # A launch writer retains its dispatch fence. It cannot publish under a
        # takeover writer's epoch just because repository admission observed it.
        epoch = record.dispatch.thaw()['fencing_epoch']
        def finish(state):
            current = _record(state, args.request)
            _candidate(state, root, current.request, services)
            return CommitFreshReviewResult(args.request, operation, epoch,
                _terminal(current, operation, epoch, 'blocked', reason, services.now(),
                          attempted=attempted, cancel=cancel,
                          wall_time_sec=math.ceil(time.monotonic() - callback_start) if adapter_called else 0))
        record = _execute(repo(':terminal'), finish)
        return json.dumps({'ok': True, 'record': _wire(record)})
    except (FreshReviewError, CliOperationRejected, VerifierPolicyError, VerificationRunnerError, PlanningFailure) + services.commit_errors as exc:
        services.fail(getattr(exc, 'code', str(exc)), 2)


def _reconcile(record, args, operation, repo, root, services, host):
    if isinstance(record, WithdrawnFreshReviewRecord):
        raise FreshReviewError('fresh-review-request-withdrawn')
    if operation in (record.prepare_operation_id, record.operation_id):
        raise FreshReviewError('fresh-review-operation-conflict')
    if record.dispatch is None or record.dispatch.thaw()['adapter_id'] != args.adapter:
        raise FreshReviewError('fresh-review-adapter-pin-changed')
    if record.status in ('blocked', 'abandoned-unknown', 'failed'):
        if record.result.thaw()['commit_operation_id'] != operation:
            raise FreshReviewError('fresh-review-operation-conflict')
        return json.dumps({'ok': True, 'record': _wire(record)})
    if record.status not in ('dispatch-unknown', 'running'):
        raise FreshReviewError('fresh-review-not-dispatch-unknown')
    capacity = services.capacity_status(services.resolve_state_file(root))
    if capacity['code'] is not None:
        raise FreshReviewError(capacity['code'])
    observed, pin = {}, None
    reason = 'child-unobservable'
    callback_start, adapter_called = time.monotonic(), False
    try:
        pin = host.resolve(args.adapter)
        if pin.registration.digest != record.request.adapter_registration_digest:
            raise FreshReviewError('fresh-review-adapter-pin-changed')
        adapter_called = True
        observed = host.recover(pin, record.dispatch.thaw())
    except (FreshReviewError, OSError):
        pin = None
    observation = observed.get('observation')
    raw = observation.get('launch_receipt') if isinstance(observation, dict) else None
    if raw is not None:
        # A saved launch rejects foreign reports. Before launch persistence,
        # binding mismatch can become blocked only after confirmed cancellation.
        try:
            launch, _ = validate_launch(record.request, record.dispatch.thaw(), raw)
            if record.launch is not None and raw != record.launch.thaw():
                raise FreshReviewError('fresh-review-launch-binding-mismatch')
        except FreshReviewError as exc:
            if exc.code == 'fresh-review-launch-binding-mismatch':
                if record.launch is not None:
                    raise
                reason = 'binding-mismatch'
            raw = None
    if raw is not None:
        # Dispatch epoch identifies the child; the current epoch is the writer.
        reason = 'output-unobservable'
        try:
            output = base64.b64decode(observed['output'], validate=True)
        except (KeyError, TypeError, ValueError):
            output = None
        if record.status == 'dispatch-unknown':
            record_provider_receipt([record.dispatch.thaw()], _saga_intent(record),
                                    {'kind': 'provider', 'identity': launch.child_identity})
            record = _execute(repo(':running'), lambda state: RecordFreshReviewLaunch(
                args.request, operation, state['fencing_epoch'], freeze_json_value(raw),
                _candidate(state, root, record.request, services)))
        if output is not None and observation.get('process_exited') is True:
            from mission_application.fresh_review_publish import publish_failed_output
            # Child-facing identity remains the saved dispatch. The writer's
            # epoch comes from an independent current lease admission.
            reader = repo(':output-fence')
            with reader.transaction():
                epoch = reader.load()['fencing_epoch']
            record = publish_failed_output(repo(':output'), request_id=args.request,
                operation=operation, epoch=epoch, observation=observation, raw=output,
                root=root, services=services)
            return json.dumps({'ok': True, 'record': _wire(record)})
        if output is not None:
            reader = repo(':candidate', False)
            with reader.transaction():
                _candidate(reader.load(), root, record.request, services)
            return json.dumps({'ok': True, 'record': _wire(record)})
    exited = raw is not None and observation.get('process_exited') is True
    if _utc(services.now()) < record.dispatch.thaw()['deadline_at'] and not exited:
        return json.dumps({'ok': True, 'record': _wire(record)})
    if record.status == 'dispatch-unknown':
        reconcile_dispatch_unknown([record.dispatch.thaw()], _saga_intent(record), observed_receipt=None)
    def finish(state):
        current = _record(state, args.request)
        if current.status not in ('dispatch-unknown', 'running'):
            raise FreshReviewError('fresh-review-consumed')
        _candidate(state, root, current.request, services)
        if (pin is None or host.cancel(pin, current.dispatch.thaw()) != 'cancelled') and not exited:
            raise FreshReviewError('fresh-review-kill-unconfirmed')
        epoch = state['fencing_epoch']
        return CommitFreshReviewResult(args.request, operation, epoch,
            _terminal(current, operation, epoch, 'blocked' if reason == 'binding-mismatch' else 'abandoned-unknown', reason, services.now(),
                      attempted=reason == 'binding-mismatch', cancel='cancelled',
                      wall_time_sec=math.ceil(time.monotonic() - callback_start) if adapter_called else 0))
    record = _execute(repo(':terminal'), finish)
    return json.dumps({'ok': True, 'record': _wire(record)})
