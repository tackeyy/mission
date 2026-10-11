"""Dispatch a saved counterexample once, then publish an observed terminal."""
from __future__ import annotations
from datetime import timedelta
from functools import partial
from acceptance_contract import frozen_verifier_commands
from mission_kernel.commands import BeginFindingReverification, CommitFindingReverification, FreshReviewInputEffectClaim
from mission_kernel.fresh_review import FreshReviewError, canonical_bytes, canonical_digest, ContentAddressedRef
from mission_kernel.json_codec import freeze_json_value
from mission_kernel.repair_attempts import find_attempt, reference, terminal_operation
from mission_kernel.repair_reverification import replay_binding, next_generation
from .fresh_review import _capture
from .fresh_review_completion import _read, observe_lineage_evidence, observe_repair_results
from .evidence import PreparedEvidenceOperation, execute_evidence_operation
from .artifact import make_evidence_effect, EvidenceFailure
from .verification_execution import run_contract_verifier
from .verification_budget import reserve_verification, execute_verification, verification_settlement, verification_terminal_bytes
from mission_kernel.commands import ReserveDispatchBudget
from mission_kernel.errors import StateBoundaryError


def capture(state, row, root, services):
    replay_binding(state, row)
    if services.load_verifier_policy(root)['digest'] != state['acceptance_contract']['verifier_policy']['digest']:
        raise FreshReviewError('repair-contract-stale')
    commands = frozen_verifier_commands(state['acceptance_contract'])
    return {key: value.digest for key, value in _capture(root, commands).items()}


def reverify(identifier, *, read, repo, root, services):
    from .repair import prepare_reconcile
    state = read(); row, item = find_attempt(state, identifier)
    if item['status'] == 'verified':
        return
    if item['status'] != 'pending':
        raise FreshReviewError('repair-attempt-already-terminal')
    if 'intent' in item:
        # A durable dispatch from another invocation is never automatically run.
        def expired(document):
            from mission_kernel.budget import _at
            _, pending = find_attempt(document, identifier)
            saved = pending['intent']
            held = next((r for r in document.get('budget_ledger', {}).get('reservations', ())
                if r['reservation_id'] == saved['reservation_id']), None)
            deadline = saved.get('deadline_at') if held is None else held['settle_by']
            if deadline is None or _at(services.now()) <= _at(deadline):
                raise FreshReviewError('repair-attempt-busy')
            return prepare_reconcile(document, identifier=identifier, reason='publication-result-lost')
        execute_evidence_operation(repo(terminal_operation(identifier, 'blocked', 'publication-result-lost')),
            expired)
        return
    history = _read(root, ContentAddressedRef(**item['history_ref']), read_evidence=services.read_evidence)
    repro = _read(root, ContentAddressedRef(**history['repro_input_ref']), read_evidence=services.read_evidence)
    finding = _read(root, ContentAddressedRef(**row['finding_ref']), read_evidence=services.read_evidence)
    if canonical_digest(repro) != row['repro']['repro_input_digest'] or repro != finding['repro_input']:
        raise FreshReviewError('repair-repro-mismatch')
    observed_candidates = capture(state, row, root, services)
    candidates = {key: observed_candidates[key] for key in row['introduced_candidate']['snapshots']}
    operation = 'repair-dispatch:' + identifier[7:]
    budget = None
    def intent(document):
        nonlocal budget
        current_row, current_item = find_attempt(document, identifier)
        if capture(document, current_row, root, services) != observed_candidates:
            raise FreshReviewError('repair-reverification-stale')
        definition = replay_binding(document, current_row)
        at = services.now(); size = verification_terminal_bytes(definition)
        budget, refusal = reserve_verification(document, current_row['lineage_id'], definition['timeout_sec'], at,
            candidates[current_row['repro']['command_id']], size, entry='repair-reverify', operation=operation)
        if refusal is not None:
            raise FreshReviewError(refusal)
        request = None if budget is None else ReserveDispatchBudget(at, 'repair-reverify', current_row['lineage_id'], operation,
            budget.reservation.fencing_epoch, definition['timeout_sec'], size, budget.candidate_digest)
        from mission_kernel.budget import _at
        deadline = ((_at(at) + timedelta(seconds=definition['timeout_sec'])).strftime('%Y-%m-%dT%H:%M:%SZ')
            if budget is None else budget.reservation.child_deadline_at)
        return PreparedEvidenceOperation(BeginFindingReverification(identifier, operation,
            freeze_json_value(candidates), 'repair:' + identifier[7:] if budget is None else budget.reservation.reservation_id,
            'repair', request, at, deadline, freeze_json_value(observed_candidates)), (), {})
    dispatch = execute_evidence_operation(repo(operation, command_type='repair-reverify-begin'), intent)
    if dispatch.get("dispatch_replayed"):
        return
    try:
        observed = read(); row, item = find_attempt(observed, identifier)
        if item['status'] != 'pending' or item['intent']['operation_id'] != operation:
            raise FreshReviewError('repair-dispatch-already-started')
        if capture(observed, row, root, services) != observed_candidates:
            raise FreshReviewError('repair-reverification-stale')
        receipt = (run_contract_verifier(observed, project_root=root, criterion_id=row['criterion_id'], repro_input=repro)
            if budget is None else execute_verification(observed, root, row['criterion_id'], repro, budget,
                replay_binding(observed, row), session_id='repair-reverify'))
        if capture(observed, row, root, services) != observed_candidates:
            raise FreshReviewError('repair-reverification-stale')
        def publish(document):
            current_row, current_item = find_attempt(document, identifier)
            if capture(document, current_row, root, services) != observed_candidates:
                raise FreshReviewError('repair-reverification-stale')
            body = dict(schema='mission-repair-reverification/1', lineage_id=row['lineage_id'], attempt_id=identifier,
                criterion_id=row['criterion_id'], command_id=row['repro']['command_id'], repro_input_digest=canonical_digest(repro),
                candidate=candidates, receipt=receipt, receipt_ref=reference('repair-replay', canonical_bytes(receipt)),
                dispatch=current_item['intent'], generation=next_generation(document))
            content = canonical_bytes(body); ref = reference('repair-reverification', content)
            effect = make_evidence_effect(ref['kind'], ref['relative_path'], content)
            replay_ref = body['receipt_ref']
            replay_effect = make_evidence_effect(replay_ref['kind'], replay_ref['relative_path'], canonical_bytes(receipt))
            return PreparedEvidenceOperation(CommitFindingReverification(identifier, 'repair-result:' + ref['digest'][7:],
                freeze_json_value(body), FreshReviewInputEffectClaim(effect.kind, effect.target, effect.digest, effect.size),
                FreshReviewInputEffectClaim(replay_effect.kind, replay_effect.target, replay_effect.digest, replay_effect.size),
                observe_lineage_evidence(document, root=root, read_evidence=services.read_evidence),
                observe_repair_results(document, root=root, read_evidence=services.read_evidence),
                None if budget is None else verification_settlement(budget, services.now(), receipt),
                freeze_json_value(observed_candidates)), (effect, replay_effect), {})
        prepared = publish(read())
        services.run_with_base_retry(None, lambda _number: execute_evidence_operation(
            repo(prepared.command.operation_id, command_type='repair-reverify-commit'), publish))
    except (OSError, ValueError, EvidenceFailure, StateBoundaryError) as exc:
        # Recover committed results first. Unpublished observations never grant success.
        if find_attempt(read(), identifier)[1]['status'] == 'pending':
            execute_evidence_operation(repo(terminal_operation(identifier, 'blocked', 'effects-unavailable')),
                partial(prepare_reconcile, identifier=identifier, reason='effects-unavailable'))
        if not isinstance(exc, (OSError, StateBoundaryError)):
            raise
