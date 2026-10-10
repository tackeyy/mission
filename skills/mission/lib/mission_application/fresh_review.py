"""Build immutable reviewer input and publish a typed request; no runtime launch."""
from __future__ import annotations

import base64
from functools import partial
import json
from pathlib import Path
import secrets

from acceptance_contract import canonical_contract_digest
from mission_kernel.commands import PrepareFreshReview, FreshReviewInputEffectClaim
from mission_kernel.fresh_review import (
    BUDGET_LIMITS, REQUEST_SCHEMA, FreshReviewError, WithdrawnFreshReviewRecord, canonical_bytes,
    canonical_digest, candidate_identity, decode_projection, decode_request, request_document,
    validate_budgets, record_operation_ids,
)
from mission_kernel.json_codec import freeze_json_value
from mission_application.artifact import EvidenceFailure, make_evidence_effect
from mission_application.cli_operation import prepare_cli_operation, CliOperationRejected
from mission_application.evidence import PreparedEvidenceOperation, execute_evidence_operation
from mission_application.verification_runner import capture_candidate, verifier_definition_digest, VerificationRunnerError
from mission_application.verifier_policy import validate, VerifierPolicyError, SCHEMA as POLICY_SCHEMA


def _options(args):
    budgets = validate_budgets({key: getattr(args, key) for key in BUDGET_LIMITS})
    criteria = getattr(args, 'criterion', None)
    if criteria is not None and (len(set(criteria)) != len(criteria) or not criteria):
        raise FreshReviewError('fresh-review-criterion-invalid')
    # Option order is not a different intent. Default selection is frozen in the request.
    return {'perspective': args.perspective, 'criterion_ids': sorted(criteria) if criteria else None,
            'adapter_registration_digest': args.adapter_registration_digest,
            'allowed_tools': sorted(args.allowed_tool), **budgets}


def _historical(state, operation_id, intent_digest, payload_digest):
    for item in decode_projection(state).requests:
        if isinstance(item, WithdrawnFreshReviewRecord):
            if operation_id == item.prepare_operation_id:
                raise FreshReviewError('fresh-review-request-withdrawn')
            if operation_id == item.withdraw_operation_id:
                raise FreshReviewError('fresh-review-operation-conflict')
            continue
        if operation_id in record_operation_ids(item):
            if (item.prepare_operation_id, item.prepare_intent_digest, item.prepare_payload_digest) != (operation_id, intent_digest, payload_digest):
                raise FreshReviewError('fresh-review-operation-conflict')
            return item.request
    return None


def _capture(root, commands):
    result = {}
    for identifier, command in sorted(commands.items()):
        result[identifier] = capture_candidate(root, declared_untracked=command['declared_untracked'],
                                              external_inputs=command['external_inputs'])
    return result


def build_input_packet(contract, perspective, snapshots):
    """Shared prepare/completion packet calculation from recaptured snapshots."""
    criteria, policy = contract['criteria'], contract['verifier_policy']
    packet = {
        'schema': 'mission-fresh-review-input/1', 'requirement_text': contract['requirement_text'],
        'requirements': contract['requirements'], 'criteria': criteria, 'verifier_policy': policy,
        'perspective': perspective, 'reviewer_instructions_version': 'counterexamples/1',
        'snapshots': {identifier: {'digest': snapshot.digest, 'files': [
            {'path': item.path, 'mode': item.mode,
             'content_base64': None if item.content is None else base64.b64encode(item.content).decode('ascii')}
            for item in snapshot.files]} for identifier, snapshot in snapshots.items()},
    }
    return packet


def prepare_fresh_review(state, *, root, options, operation_id, intent_digest, payload_digest, now, load_policy):
    historical = _historical(state, operation_id, intent_digest, payload_digest)
    if historical is not None:
        return PreparedEvidenceOperation(PrepareFreshReview(historical, operation_id, intent_digest, payload_digest, None, None),
                                         (), {'request': request_document(historical)})
    contract = state.get('acceptance_contract')
    if not isinstance(contract, dict) or contract.get('schema') != 'mission-acceptance-contract/2':
        raise FreshReviewError('fresh-review-contract-unavailable')
    policy = contract.get('verifier_policy')
    if not isinstance(policy, dict) or not isinstance(policy.get('commands'), dict):
        raise FreshReviewError('fresh-review-contract-invalid')
    if load_policy(root)['digest'] != policy.get('digest'):
        raise FreshReviewError('verifier-policy-stale')
    # A persisted policy is untrusted on reload. Only this new producer is guarded;
    # existing verification/completion validation is outside D1.
    try:
        validate({'schema': POLICY_SCHEMA, 'commands': list(policy['commands'].values())})
    except (VerifierPolicyError, TypeError, KeyError) as exc:
        raise FreshReviewError('fresh-review-contract-invalid') from exc
    criteria = contract.get('criteria')
    if not isinstance(criteria, list) or any(not isinstance(item, dict) or not isinstance(item.get('id'), str) for item in criteria):
        raise FreshReviewError('fresh-review-contract-invalid')
    by_id = {item['id']: item for item in criteria}
    selected = options['criterion_ids'] or sorted(item['id'] for item in criteria if item.get('required') is True)
    if not selected or any(identifier not in by_id for identifier in selected):
        raise FreshReviewError('fresh-review-criterion-invalid')
    bindings, commands = [], {}
    for identifier in selected:
        command_id = by_id[identifier].get('command_id')
        if not isinstance(command_id, str) or command_id not in policy['commands']:
            raise FreshReviewError('fresh-review-contract-invalid')
        definition = policy['commands'][command_id]
        targets = [('verification', command_id)]
        if definition.get('replay') is not None:
            targets.append(('replay', definition['replay']['command_id']))
        for role, target in targets:
            commands[target] = policy['commands'][target]
            bindings.append({'criterion_id': identifier, 'role': role, 'command_id': target,
                             'definition_digest': verifier_definition_digest(commands[target])})
    snapshots = _capture(root, commands)
    for item in bindings:
        item['snapshot_digest'] = snapshots[item['command_id']].digest
    packet = build_input_packet(contract, options['perspective'], snapshots)
    content = canonical_bytes(packet)
    if len(content) > options['max_packet_bytes']:
        raise FreshReviewError('fresh-review-packet-too-large')
    input_digest = canonical_digest(packet)
    target = 'evidence/fresh-review/' + input_digest[7:] + '.json'
    request = decode_request({
        'schema': REQUEST_SCHEMA, 'request_id': secrets.token_hex(16), 'nonce': secrets.token_hex(32),
        'mission_id': state.get('mission_id') or state['session_id'], 'session_id': state['session_id'],
        'requirement_digest': contract['requirement_digest'], 'contract_digest': canonical_contract_digest(contract),
        'verifier_policy_digest': policy['digest'], 'candidate_digest': candidate_identity({key: value.digest for key, value in snapshots.items()}),
        'input_digest': input_digest, 'adapter_registration_digest': options['adapter_registration_digest'],
        'candidate_bindings': bindings, 'criterion_ids': selected, 'iteration': state['iteration'],
        'perspective': options['perspective'], 'allowed_tools': options['allowed_tools'],
        **{key: options[key] for key in BUDGET_LIMITS},
        'input_ref': {'kind': 'fresh-review-input', 'relative_path': target, 'digest': input_digest, 'size': len(content)},
        'created_at': now,
    })
    # Capture again before publication: neither HEAD nor one verifier can stand
    # in for the complete command-indexed candidate map.
    if {key: value.digest for key, value in _capture(root, commands).items()} != {key: value.digest for key, value in snapshots.items()}:
        raise FreshReviewError('fresh-review-stale')
    effect = make_evidence_effect('fresh-review-input', target, content)
    command = PrepareFreshReview(request, operation_id, intent_digest, payload_digest, freeze_json_value(packet),
                                 FreshReviewInputEffectClaim(effect.kind, effect.target, effect.digest, effect.size))
    return PreparedEvidenceOperation(command, (effect,), {'request': request_document(request)})


def run_fresh_review_prepare_cli(args, services):
    try:
        root = Path.cwd()
        state_file = services.resolve_state_file(root)
        if not state_file.exists():
            services.fail('fresh-review-state-missing', 2)
        options = _options(args)
        intent = canonical_digest({'type': 'fresh-review-prepare', 'options': options})
        payload = canonical_digest(options)
        identity = prepare_cli_operation('fresh-review-prepare', {'intent_digest': intent, 'payload_digest': payload},
                                         session_id=state_file.stem,
                                         compatibility_arguments=services.compatibility_arguments,
                                         canonical_operation=services.canonical_operation)
        operation_id = identity.operation_id or 'prepare:' + secrets.token_hex(16)
        reader = services.repository(root, state_file, stamp=False, strict_read=True, pre_admit_lease=True)
        with reader.transaction():
            state = reader.load()
            historical = _historical(state, operation_id, intent, payload)
            if historical is not None:
                return json.dumps({'ok': True, 'request': request_document(historical)}, ensure_ascii=False, indent=2)
        result = execute_evidence_operation(
            services.repository(root, state_file, stamp=True, pre_admit_lease=True, session_id=state_file.stem,
                                operation_id=identity.operation_id, operation_command=identity.operation_command,
                                operation_command_type=identity.command_type),
            lambda state: prepare_fresh_review(state, root=root, options=options, operation_id=operation_id,
                                               intent_digest=intent, payload_digest=payload, now=services.now(),
                                               load_policy=services.load_verifier_policy),
        )
        return json.dumps({'ok': True, **result}, ensure_ascii=False, indent=2)
    except OSError:
        services.fail('fresh-review-io-unavailable', 2)
    except (FreshReviewError, EvidenceFailure, CliOperationRejected, VerifierPolicyError, VerificationRunnerError) as exc:
        services.fail(getattr(exc, 'code', str(exc)), 2)


def _same_snapshot(loaded, *_args, **_kwargs):
    return loaded


def run_fresh_review_status_cli(args, services):
    root = Path.cwd()
    state_file = services.resolve_state_file(root)
    if not state_file.exists():
        services.fail('fresh-review-state-missing', 2)
    try:
        loaded = services.load_snapshot(state_file)
    except Exception as exc:
        services.fail(getattr(exc, 'code', None) or 'repository-format-invalid', 2)
    try:
        projection = decode_projection(loaded[1])
        # capacity は同じ snapshot から計算し、要求一覧と別の状態を混ぜない。
        capacity = services.capacity_status(state_file, load_snapshot=partial(_same_snapshot, loaded))
        requests = []
        for item in projection.requests:
            if isinstance(item, WithdrawnFreshReviewRecord):
                requests.append({'request_id': item.request_id, 'status': item.status,
                                 'reason': item.reason, 'criterion_ids': list(item.criterion_ids)})
            else:
                requests.append({'request': request_document(item.request), 'status': item.status,
                                 'operation_id': item.operation_id,
                                 'dispatch': item.dispatch.thaw() if item.dispatch is not None else None,
                                 'launch': item.launch.thaw() if item.launch is not None else None,
                                 'independent': item.independent,
                                 'result': item.result.thaw() if item.result is not None else None})
        return json.dumps({'requests': requests, 'capacity': capacity}, ensure_ascii=False, indent=2)
    except FreshReviewError as exc:
        services.fail(exc.code, 2)
