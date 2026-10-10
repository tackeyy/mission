"""Publish bound output, replay evidence and its terminal in one evidence commit."""
from __future__ import annotations
import base64
import binascii
import json
import secrets
import time
import math
from datetime import datetime
from pathlib import Path

from mission_application.artifact import EvidenceFailure, make_evidence_effect
from mission_application.cli_operation import prepare_cli_operation, CliOperationRejected
from mission_application.evidence import PreparedEvidenceOperation, execute_evidence_operation
from mission_application.fresh_review_dispatch import _record, _wire, _candidate, _terminal, _utc
from mission_application.verification_runner import VerificationRunnerError
from mission_application.verifier_policy import VerifierPolicyError
from mission_kernel.commands import ImportFreshReviewOutput, FreshReviewInputEffectClaim
from mission_kernel.fresh_review import FreshReviewError, WithdrawnFreshReviewRecord, record_operation_ids
from mission_kernel.fresh_review_output import replay_eligibility, validate_output_sender
from mission_application.verification_execution import run_contract_verifier
from mission_kernel.fresh_review_publish import completed_evidence, evidence_claim, reference, inspect_import
from mission_kernel.fresh_review_receipts import decode_terminal_receipt, receipt_document
from mission_kernel.json_codec import freeze_json_value


def _replay_hypotheses(state, record, output, used, root, services):
    replays, count = [], 0
    started = time.monotonic()
    limits = record.launch.thaw()['enforced_budget']
    for result in output.criterion_results:
        for hypothesis in result.findings:
            reason = replay_eligibility(record.request, hypothesis, state['acceptance_contract']['verifier_policy'])
            replay = None
            if reason is None:
                definition = state['acceptance_contract']['verifier_policy']['commands'][hypothesis.command_id]
                seconds = (datetime.fromisoformat(record.dispatch.thaw()['deadline_at'].replace('Z', '+00:00'))
                           - datetime.fromisoformat(services.now().replace('Z', '+00:00'))).total_seconds()
                if (count >= limits['max_replays'] - used['replays'] or definition['timeout_sec'] > seconds
                        or definition['timeout_sec'] > limits['wall_time_sec'] - used['wall_time_sec']
                            - (time.monotonic() - started)):
                    reason = 'replay-budget-exceeded'
                else:
                    count += 1
                    try:
                        replay = run_contract_verifier(state, project_root=root,
                            criterion_id=hypothesis.criterion_id, repro_input=hypothesis.repro_input.thaw())
                    except (EvidenceFailure, VerificationRunnerError, OSError):
                        reason = 'replay-unavailable'
                    if replay is not None and replay['candidate_digest'] == 'sha256:' + '0' * 64:
                        reason, replay = replay['block_reason'], None
            replays.append(freeze_json_value(dict(finding_id=hypothesis.finding_id,
                reason_code=reason or 'none', replay=replay)))
    return tuple(replays), dict(used, replays=used['replays'] + count,
        wall_time_sec=used['wall_time_sec'] + (math.ceil(time.monotonic() - started) if count else 0))


def prepare_failed_output(state, *, request_id, operation, epoch, observation, raw, root, services):
    """Prepare failed diagnostics or completed evidence through the same commit."""
    record = _record(state, request_id)
    if isinstance(record, WithdrawnFreshReviewRecord):
        raise FreshReviewError('fresh-review-request-withdrawn')
    validate_output_sender(record, observation)
    candidate = _candidate(state, root, record.request, services, allow_changed=True)
    used = observation.get('budget_used') if type(observation) is dict else None
    receipt = _terminal(record, operation, epoch, 'failed', 'child-failed', services.now()).thaw()
    contract = state.get('acceptance_contract')
    def inspect(replays=None):
        return inspect_import(record, observation, raw, candidate_digest=candidate, budget_used=used,
            contract=contract, ended_at=receipt['ended_at'], replays=replays)
    decision, replays = inspect(), ()
    if decision.outcome == 'completed':
        replays, used = _replay_hypotheses(state, record, decision.output, used, root, services)
        candidate = _candidate(state, root, record.request, services, allow_changed=True)
        receipt['ended_at'] = max(receipt['ended_at'], _utc(services.now()))
        decision = inspect(replays)
    receipt.update(reason=decision.reason.value, budget_used=used)
    effects, claim = (), None
    if decision.diagnostic_bytes is not None:
        claim = evidence_claim('fresh-review-output', decision.diagnostic_bytes)
        effects = (make_evidence_effect(claim.kind, claim.target, decision.diagnostic_bytes),)
        receipt.update(output_ref=reference(claim), output_digest=claim.digest)
    coverage_claim, finding_claims = None, ()
    if decision.outcome == 'completed':
        content, findings = completed_evidence(decision.output, record.request, contract, replays)
        coverage_claim = evidence_claim('fresh-review-coverage', content)
        finding_claims = tuple(evidence_claim('fresh-review-finding', item) for item in findings)
        effects += tuple(make_evidence_effect(claim.kind, claim.target, item)
                         for claim, item in zip((coverage_claim, *finding_claims), (content, *findings)))
        receipt.update(outcome='completed', independent=decision.independent,
            coverage_receipt=dict(status=json.loads(content)['status'], evidence_ref=reference(coverage_claim)),
            findings=[reference(item) for item in finding_claims])
    elif decision.reason != 'output-over-import-limit':
        replays = ()
    from .fresh_review_completion import observe_lineage_evidence
    evidence = observe_lineage_evidence(state, root=root, read_evidence=getattr(services, 'read_evidence', None))
    command = ImportFreshReviewOutput(request_id, operation, epoch,
        freeze_json_value(receipt_document(decode_terminal_receipt(receipt))),
        freeze_json_value(observation), freeze_json_value(used), candidate,
        None if raw is None else base64.b64encode(raw).decode('ascii'), claim, coverage_claim, finding_claims, replays, evidence)
    return PreparedEvidenceOperation(command, effects, {})


def publish_failed_output(repository, *, request_id, operation, epoch, observation, raw, root, services):
    execute_evidence_operation(repository, lambda state: prepare_failed_output(
        state, request_id=request_id, operation=operation, epoch=epoch, observation=observation,
        raw=raw, root=root, services=services))
    # execute_evidence_operation validates the decision and publication; read the
    # persisted terminal, including retries whose response was lost.
    reader = services.repository(root, services.resolve_state_file(root), stamp=False, strict_read=True)
    with reader.transaction():
        return _record(reader.load(), request_id)


def run_fresh_review_import_cli(args, services, host):
    root = Path.cwd()
    sf = services.resolve_state_file(root)
    if not sf.exists():
        services.fail('fresh-review-state-missing', 2)
    try:
        identity = prepare_cli_operation('fresh-review-import', {'request_id': args.request, 'adapter': args.adapter},
            session_id=sf.stem, compatibility_arguments=services.compatibility_arguments,
            canonical_operation=services.canonical_operation)
        operation = identity.operation_id or 'import:' + secrets.token_hex(16)
        # Obtain the repository's admitted epoch, including takeover, before
        # preparing evidence. The publishing repository independently fences it.
        reader = services.repository(root, sf, stamp=False, strict_read=True, pre_admit_lease=True)
        with reader.transaction():
            state = reader.load()
            record = _record(state, args.request)
            epoch = state.get('fencing_epoch', 0)
        if isinstance(record, WithdrawnFreshReviewRecord):
            raise FreshReviewError('fresh-review-request-withdrawn')
        if record.status in ('failed', 'completed'):
            if record.result.thaw()['commit_operation_id'] != operation:
                raise FreshReviewError('fresh-review-consumed')
            if record.dispatch.thaw()['adapter_id'] != args.adapter:
                raise FreshReviewError('fresh-review-adapter-pin-changed')
            return json.dumps({'ok': True, 'record': _wire(record)})
        if operation in record_operation_ids(record):
            raise FreshReviewError('fresh-review-operation-conflict')
        if record.status != 'running':
            raise FreshReviewError('fresh-review-output-request-unavailable')
        capacity = services.capacity_status(sf)
        if capacity['code'] is not None:
            raise FreshReviewError(capacity['code'])
        pin = host.resolve(args.adapter)
        if (pin.registration.digest != record.request.adapter_registration_digest
                or record.dispatch.thaw()['adapter_id'] != args.adapter):
            raise FreshReviewError('fresh-review-adapter-pin-changed')
        observed = host.recover(pin, record.dispatch.thaw())
        try:
            raw = None if observed.get('output') is None else base64.b64decode(observed['output'], validate=True)
        except (TypeError, ValueError, binascii.Error) as exc:
            raise FreshReviewError('fresh-review-output-observation-invalid') from exc
        observation = observed.get('observation')
        if type(observation) is not dict or observation.get('launch_receipt') != record.launch.thaw():
            raise FreshReviewError('fresh-review-output-sender-mismatch')
        repository = services.repository(root, sf, stamp=True, strict_read=True, pre_admit_lease=True,
            session_id=sf.stem, operation_id=identity.operation_id, operation_command=identity.operation_command,
            operation_command_type=identity.command_type)
        record = publish_failed_output(repository, request_id=args.request, operation=operation,
            epoch=epoch, observation=observation, raw=raw, root=root, services=services)
        return json.dumps({'ok': True, 'record': _wire(record)})
    except OSError:
        services.fail('fresh-review-io-unavailable', 2)
    except (FreshReviewError, EvidenceFailure, CliOperationRejected, VerifierPolicyError, VerificationRunnerError) + services.commit_errors as exc:
        services.fail(getattr(exc, 'code', str(exc)), 2)
