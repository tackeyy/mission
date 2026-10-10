"""Authenticate same-counterexample replay pairs and preserve invalidation."""
from dataclasses import replace
from acceptance_contract import canonical_contract_digest, frozen_verifier_commands
from .fresh_review import FreshReviewError, canonical_bytes, canonical_digest, _closed, _identifier, _integer, _digest
from .json_codec import freeze_json_value
from .repair_attempts import reference, _reference, _document, _store, find_attempt

INVALID = 'repair-reverification-invalid'


def next_generation(document):
    return 1 + max((max(row['last_candidate_change'].get('generation', 0),
        *(a['terminal'].get('generation', 0) for a in row.get('attempts', ())), 0)
        for row in document.get('repair_lineage', {}).get('lineages', ())), default=0)


def replay_binding(document, row):
    commands = frozen_verifier_commands(document['acceptance_contract'])
    criterion = next(c for c in document['acceptance_contract']['criteria'] if c['id'] == row['criterion_id'])
    replay = commands[criterion['command_id']].get('replay')
    if not replay or replay['command_id'] != row['repro']['command_id']:
        raise FreshReviewError('repair-replay-unsupported')
    command = commands[replay['command_id']]
    if canonical_digest(command) != row['repro']['verifier_definition_digest']:
        raise FreshReviewError('repair-contract-stale')
    contract = document['acceptance_contract']
    if (canonical_contract_digest(contract) != row['introduced_candidate']['contract_digest']
            or contract['verifier_policy']['digest'] != row['introduced_candidate']['verifier_policy_digest']):
        raise FreshReviewError('repair-contract-stale')
    return command


def validate_result(document, row, item, result, finding, *, current=None, previous_results=()):
    from .commands import RecordVerificationReceipt
    from .evidence import project_verification_receipt
    body = _closed(result, ('schema', 'lineage_id', 'attempt_id', 'criterion_id', 'command_id',
        'repro_input_digest', 'candidate', 'receipt', 'receipt_ref', 'dispatch', 'generation'), INVALID)
    if (body['schema'] != 'mission-repair-reverification/1' or body['lineage_id'] != row['lineage_id']
            or body['attempt_id'] != item['attempt_id'] or body['criterion_id'] != row['criterion_id']
            or body['command_id'] != row['repro']['command_id']
            or body['repro_input_digest'] != row['repro']['repro_input_digest']
            or canonical_digest(finding['repro_input']) != body['repro_input_digest']
            or canonical_digest(finding) != row['finding_ref']['digest'] or body['dispatch'] != item.get('intent')):
        raise FreshReviewError('repair-repro-mismatch')
    _integer(body['generation'], INVALID)
    candidate = body['candidate']
    if type(candidate) is not dict or set(candidate) != set(row['introduced_candidate']['snapshots']):
        raise FreshReviewError(INVALID)
    for value in candidate.values():
        _digest(value)
    if canonical_digest(candidate) != item['intent']['candidate_map_digest'] or current is not None and candidate != current:
        raise FreshReviewError('repair-reverification-stale')
    command = replay_binding(document, row)
    receipt = body['receipt']
    project_verification_receipt(RecordVerificationReceipt(receipt['finished_at'], freeze_json_value(receipt)))
    from .budget import _at
    if (_at(receipt['finished_at']) < _at(receipt['started_at'])
            or receipt['status'] == 'passed' and command['kind'] == 'test'
            and (receipt['executed_count'] is None or receipt['executed_count'] < 1)):
        raise FreshReviewError(INVALID)
    _reference(body['receipt_ref'], 'repair-replay')
    if body['receipt_ref'] != reference('repair-replay', canonical_bytes(receipt)):
        raise FreshReviewError(INVALID)
    if (receipt['criterion_id'] != row['criterion_id'] or receipt['contract_digest'] != canonical_contract_digest(document['acceptance_contract'])
            or receipt['verifier_policy_digest'] != row['introduced_candidate']['verifier_policy_digest']
            or receipt['verifier_definition_digest'] != canonical_digest(command)
            or receipt['argv'] != command['argv'] or receipt['relative_cwd'] != command['relative_cwd']
            or receipt['candidate_digest'] != candidate[body['command_id']]
            or receipt['runner_provenance'] != 'mission-public-cli/1'):
        raise FreshReviewError(INVALID)
    if receipt['status'] == 'blocked':
        return 'blocked'
    if receipt['repro_input_digest'] != row['repro']['runner_repro_digest']:
        raise FreshReviewError('repair-repro-mismatch')
    if receipt['status'] == 'failed':
        return 'failed'
    baseline = finding.get('replay')
    for previous in row['attempts']:
        if previous['attempt_id'] == item['attempt_id']:
            break
        if previous['status'] == 'failed' and 'generation' in previous['terminal']:
            ref = previous['terminal']['comparison_ref']
            prior = next((r.thaw() for r in previous_results if canonical_digest(r.thaw()) == ref['digest']), None)
            if prior is None or reference('repair-reverification', canonical_bytes(prior)) != ref:
                raise FreshReviewError('repair-baseline-unavailable')
            baseline = prior['receipt']
    # Never use the application-authored before_candidate as replay evidence.
    if (not isinstance(baseline, dict) or baseline.get('status') != 'failed'
            or baseline.get('repro_input_digest') != row['repro']['runner_repro_digest']
            or baseline.get('criterion_id') != row['criterion_id']
            or baseline.get('verifier_definition_digest') != receipt['verifier_definition_digest']):
        raise FreshReviewError('repair-baseline-unavailable')
    if baseline['candidate_digest'] == receipt['candidate_digest']:
        raise FreshReviewError('repair-candidate-not-new')
    return 'verified'


def mutate_reverification(state, command):
    from .commands import BeginFindingReverification, FreshReviewInputEffectClaim
    from .model import FencedLease
    if not isinstance(state.lease, FencedLease):
        raise FreshReviewError('repair-lineage-stale-fence')
    document = _document(state)
    row, item = find_attempt(document, command.attempt_id)
    if state.lease.fencing_epoch < item['fencing_epoch']:
        raise FreshReviewError('repair-lineage-stale-fence')
    if item['status'] != 'pending':
        raise FreshReviewError('repair-attempt-already-terminal')
    if isinstance(command, BeginFindingReverification):
        if 'intent' in item:
            raise FreshReviewError('repair-dispatch-already-started')
        replay_binding(document, row)
        if command.budget_class != 'repair':
            raise FreshReviewError(INVALID)
        for value in (command.operation_id, command.reservation_id, command.budget_class):
            _identifier(value)
        candidate = command.candidate.thaw()
        if set(candidate) != set(row['introduced_candidate']['snapshots']):
            raise FreshReviewError(INVALID)
        for value in candidate.values():
            _digest(value)
        # Variable candidate maps belong in effects, not capacity summaries.
        item['intent'] = dict(operation_id=command.operation_id, fencing_epoch=state.lease.fencing_epoch,
            reservation_id=command.reservation_id, budget_class=command.budget_class, candidate_map_digest=canonical_digest(candidate),
            snapshot_digest=candidate[row['repro']['command_id']])
    else:
        if 'intent' not in item or item['intent']['fencing_epoch'] != state.lease.fencing_epoch:
            raise FreshReviewError('repair-lineage-stale-fence')
        finding = next(f.thaw() for e in command.evidence for f in e.findings if canonical_digest(f.thaw()) == row['finding_ref']['digest'])
        body = command.result.thaw()
        outcome = validate_result(document, row, item, body, finding, previous_results=command.previous_results)
        if body['generation'] != next_generation(document):
            raise FreshReviewError(INVALID)
        content = canonical_bytes(body); ref = reference('repair-reverification', content)
        if type(command.effect) is not FreshReviewInputEffectClaim or (command.effect.kind, command.effect.target,
                command.effect.digest, command.effect.size) != (ref['kind'], ref['relative_path'], ref['digest'], ref['size']):
            raise FreshReviewError('repair-effect-invalid')
        replay_ref = body['receipt_ref']
        if type(command.receipt_effect) is not FreshReviewInputEffectClaim or (command.receipt_effect.kind, command.receipt_effect.target,
                command.receipt_effect.digest, command.receipt_effect.size) != tuple(replay_ref[k] for k in ('kind', 'relative_path', 'digest', 'size')):
            raise FreshReviewError('repair-effect-invalid')
        if command.operation_id != 'repair-result:' + ref['digest'][7:]:
            raise FreshReviewError('repair-operation-conflict')
        from .repair_attempts import terminal
        item.update(status=outcome, terminal=terminal(item['attempt_id'], outcome,
            'replay-passed' if outcome == 'verified' else 'replay-failed' if outcome == 'failed' else 'replay-blocked', state.lease.fencing_epoch))
        item['terminal'].update(comparison_ref=ref, generation=body['generation'])
        row['lifecycle'] = 'verified' if outcome == 'verified' else 'open'
    document['repair_lineage']['lineages'][next(i for i, r in enumerate(document['repair_lineage']['lineages']) if r['lineage_id'] == row['lineage_id'])] = row
    return _store(state, document)


def resolution_valid(row, evidence, candidates, contract):
    if row['lifecycle'] != 'verified':
        return False
    item = row['attempts'][-1]
    ref = item['terminal']['comparison_ref']
    body = next((r.thaw() for r in evidence if canonical_digest(r.thaw()) == ref['digest']), None)
    if body is None or reference('repair-reverification', canonical_bytes(body)) != ref:
        return False
    selected = {key: candidates.get(key) for key in row['introduced_candidate']['snapshots']}
    marker = row['last_candidate_change']
    if (body['candidate'] != selected or marker.get('generation', 0) >= body['generation']
            or body['receipt']['contract_digest'] != canonical_contract_digest(contract)):
        return False
    return True


def invalidate_observed(state, command, proposed):
    """All accepted candidate observations persist an irreversible stale marker."""
    document = _document(proposed)
    if not proposed.repair.lineages:
        return proposed
    candidates = None
    request = getattr(command, 'request', None)
    if request is not None and hasattr(request, 'candidate_bindings'):
        candidates = {b.command_id: b.snapshot_digest for b in request.candidate_bindings}
    bindings = getattr(command, 'fresh_review_bindings', None)
    if bindings is not None:
        candidates = dict(bindings.candidate_snapshots)
    history = getattr(command, 'history', None)
    if history is not None:
        candidates = history.thaw()['before_candidate']['snapshots']
    captured = getattr(command, 'candidate', None)
    if captured is not None:
        candidates = captured.thaw()
    from .commands import RecordVerificationReceipt
    receipt = command.receipt if isinstance(command, RecordVerificationReceipt) else None
    if receipt is not None and candidates is None:
        receipt = receipt.thaw(); candidates = {}
        commands = frozen_verifier_commands(document['acceptance_contract'])
        candidates = {key: receipt['candidate_digest'] for key, value in commands.items()
            if canonical_digest(value) == receipt['verifier_definition_digest']}
    if candidates is None:
        return proposed
    generation = next_generation(_document(state))
    changed = False
    for row in document['repair_lineage']['lineages']:
        if row['lifecycle'] != 'verified':
            continue
        intent = row['attempts'][-1]['intent']
        # Unknown dependencies: any observed changed command invalidates all.
        selected = {key: candidates[key] for key in row['introduced_candidate']['snapshots'] if key in candidates}
        unknown_changed = False
        if receipt is not None and captured is not None:
            commands = frozen_verifier_commands(document['acceptance_contract'])
            prior = _document(state).get('verification_receipts', ())
            for key in set(candidates) - set(selected):
                previous = next((r for r in reversed(prior) if r['verifier_definition_digest'] == canonical_digest(commands[key])), None)
                if previous is None or previous['candidate_digest'] != candidates[key]:
                    unknown_changed = True
        if (row['repro']['command_id'] in selected and selected[row['repro']['command_id']] != intent['snapshot_digest']
                or set(selected) == set(row['introduced_candidate']['snapshots'])
                and canonical_digest(selected) != intent['candidate_map_digest']
                or unknown_changed
                or receipt is not None and captured is None and row['repro']['command_id'] not in candidates):
            row['last_candidate_change'] = dict(generation=generation,
                candidate_map_digest=canonical_digest(candidates), operation_id=getattr(command, 'operation_id', 'candidate-observation'))
            changed = True
    return _store(proposed, document) if changed else proposed
