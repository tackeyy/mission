"""Fenced fresh-review dispatch using the existing provider saga primitives."""
from __future__ import annotations
from pathlib import Path
import json
import secrets
import base64

from mission_application.cli_operation import prepare_cli_operation, CliOperationRejected
from mission_application.planning import record_dispatch_intent, record_provider_receipt, reconcile_dispatch_unknown, PlanningFailure
from mission_application.fresh_review import _capture
from mission_application.verifier_policy import validate, VerifierPolicyError, SCHEMA
from mission_kernel.commands import BeginFreshReviewDispatch, CommitFreshReviewResult, RecordFreshReviewLaunch
from mission_kernel.fresh_review import (
    FreshReviewError, canonical_digest, decode_projection, candidate_identity, request_document,
)
from mission_kernel.fresh_review_receipts import decode_terminal_receipt, receipt_document, TERMINAL_SCHEMA
from mission_kernel.fresh_review_dispatch import validate_launch
from mission_kernel.json_codec import freeze_json_value


def _record(state, request_id):
    matches = [item for item in decode_projection(state).requests if item.request.request_id == request_id]
    if len(matches) != 1:
        raise FreshReviewError('fresh-review-request-unavailable')
    return matches[0]


def _candidate(state, root, request, services):
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
    if digest != request.candidate_digest:
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


def _terminal(record, operation, epoch, outcome, reason, now, *, attempted=False, cancel='not-requested'):
    request, dispatch = record.request, record.dispatch.thaw()
    raw = dict(schema=TERMINAL_SCHEMA, request_id=request.request_id,
               request_digest=canonical_digest(request_document(request)), nonce=request.nonce,
               dispatch_operation_id=dispatch['operation_id'], dispatch_fencing_epoch=dispatch['fencing_epoch'],
               commit_operation_id=operation, commit_fencing_epoch=epoch, outcome=outcome, reason=reason,
               candidate_digest=request.candidate_digest, ended_at=now,
               budget_used=dict(wall_time_sec=0, tool_calls=0, replays=0, output_bytes=0))
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
                session_id=sf.stem, operation_id=operation + suffix,
                operation_command=identity.operation_command, operation_command_type=command_type)

        reader = repo(':read', False)
        with reader.transaction():
            record = _record(reader.load(), args.request)
        if args.fresh_review_command == 'reconcile':
            return _reconcile(record, args, operation, repo, root, services, host)
        if record.status != 'pending':
            if (record.operation_id, record.intent_digest, record.payload_digest) != (operation, intent, payload):
                raise FreshReviewError('fresh-review-operation-conflict')
            return json.dumps({'ok': True, 'record': _wire(record)})
        reason = 'registration-mismatch'
        parent = 'unobservable'
        try:
            pin = host.resolve(args.adapter)
            if pin.registration.digest != record.request.adapter_registration_digest:
                raise FreshReviewError('fresh-review-adapter-pin-changed')
            parent = host.observe(pin)['parent_identity']
        except (FreshReviewError, OSError) as exc:
            reason = 'identity-unobservable' if getattr(exc, 'code', '') == 'fresh-review-identity-unobservable' else 'registration-mismatch'
            pin = None

        def begin(state):
            current = _record(state, args.request)
            epoch = state.get('fencing_epoch', 0)
            dispatch = record_dispatch_intent([], dict(invocation_id='inv_' + canonical_digest(args.request)[7:39], operation_id=operation,
                outbound_packet_digest=current.request.input_digest, iteration=current.request.iteration, fencing_epoch=epoch))
            dispatch.update(parent_identity=parent, adapter_id=args.adapter)
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
        # A launch writer retains its dispatch fence. It cannot publish under a
        # takeover writer's epoch just because repository admission observed it.
        epoch = record.dispatch.thaw()['fencing_epoch']
        record = _execute(repo(':terminal'), lambda state: CommitFreshReviewResult(args.request, operation,
            epoch, _terminal(_record(state, args.request), operation, epoch,
                             'blocked', reason, services.now(), attempted=attempted, cancel=cancel)))
        return json.dumps({'ok': True, 'record': _wire(record)})
    except (FreshReviewError, CliOperationRejected, VerifierPolicyError, PlanningFailure) as exc:
        services.fail(getattr(exc, 'code', str(exc)), 2)


def _reconcile(record, args, operation, repo, root, services, host):
    if operation == record.operation_id:
        raise FreshReviewError('fresh-review-operation-conflict')
    if record.dispatch is None or record.dispatch.thaw()['adapter_id'] != args.adapter:
        raise FreshReviewError('fresh-review-adapter-pin-changed')
    if record.status in ('blocked', 'abandoned-unknown'):
        if record.result.thaw()['commit_operation_id'] != operation:
            raise FreshReviewError('fresh-review-operation-conflict')
        return json.dumps({'ok': True, 'record': _wire(record)})
    if record.status not in ('dispatch-unknown', 'running'):
        raise FreshReviewError('fresh-review-not-dispatch-unknown')
    observed = {}
    reason = 'child-unobservable'
    try:
        pin = host.resolve(args.adapter)
        if pin.registration.digest != record.request.adapter_registration_digest:
            raise FreshReviewError('fresh-review-adapter-pin-changed')
        observed = host.recover(pin, record.dispatch.thaw())
    except (FreshReviewError, OSError):
        pass
    raw = observed.get('observation', {}).get('launch_receipt')
    if raw is not None:
        # The stored dispatch epoch belongs to the exact child; the current
        # commit epoch belongs to this reconciler. Neither substitutes for the other.
        launch, _ = validate_launch(record.request, record.dispatch.thaw(), raw)
        if record.launch is not None and raw != record.launch.thaw():
            raise FreshReviewError('fresh-review-launch-binding-mismatch')
        reason = 'output-unobservable'
        try:
            output = base64.b64decode(observed['output'], validate=True)
            if len(output) > record.request.max_output_bytes:
                raise ValueError('oversized')
        except (KeyError, TypeError, ValueError):
            output = None
        if output is not None:
            if record.status == 'dispatch-unknown':
                record_provider_receipt([record.dispatch.thaw()], _saga_intent(record),
                                        {'kind': 'provider', 'identity': launch.child_identity})
                record = _execute(repo(':running'), lambda state: RecordFreshReviewLaunch(
                    args.request, operation, state['fencing_epoch'], freeze_json_value(raw),
                    _candidate(state, root, record.request, services)))
            else:
                reader = repo(':candidate', False)
                with reader.transaction():
                    _candidate(reader.load(), root, record.request, services)
            return json.dumps({'ok': True, 'record': _wire(record)})
    if record.status == 'dispatch-unknown':
        reconcile_dispatch_unknown([record.dispatch.thaw()], _saga_intent(record), observed_receipt=None)
    record = _execute(repo(':terminal'), lambda state: CommitFreshReviewResult(args.request, operation,
        state['fencing_epoch'], _terminal(_record(state, args.request), operation, state['fencing_epoch'],
                                          'abandoned-unknown', reason, services.now())))
    return json.dumps({'ok': True, 'record': _wire(record)})
