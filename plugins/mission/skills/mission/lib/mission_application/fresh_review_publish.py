"""Publish bound invalid output and its failed terminal in one evidence commit."""
from __future__ import annotations
import base64
import binascii
import json
import secrets
from pathlib import Path

from mission_application.artifact import EvidenceFailure, make_evidence_effect
from mission_application.cli_operation import prepare_cli_operation, CliOperationRejected
from mission_application.evidence import PreparedEvidenceOperation, execute_evidence_operation
from mission_application.fresh_review_dispatch import _record, _wire, _candidate, _terminal
from mission_application.verification_runner import VerificationRunnerError
from mission_application.verifier_policy import VerifierPolicyError
from mission_kernel.commands import ImportFreshReviewOutput, FreshReviewInputEffectClaim
from mission_kernel.fresh_review import FreshReviewError, WithdrawnFreshReviewRecord, record_operation_ids
from mission_kernel.fresh_review_output import inspect_output
from mission_kernel.fresh_review_receipts import decode_terminal_receipt, receipt_document
from mission_kernel.json_codec import freeze_json_value


def prepare_failed_output(state, *, request_id, operation, epoch, observation, raw, root, services):
    record = _record(state, request_id)
    if isinstance(record, WithdrawnFreshReviewRecord):
        raise FreshReviewError('fresh-review-request-withdrawn')
    candidate = _candidate(state, root, record.request, services, allow_changed=True)
    used = observation.get('budget_used') if type(observation) is dict else None
    decision = inspect_output(record, observation, raw, candidate_digest=candidate, budget_used=used)
    if decision.outcome != 'failed':
        # Replay/coverage effects are a separate bounded writer; until available,
        # a valid hypothesis envelope remains running and grants no completion.
        raise FreshReviewError('fresh-review-completed-import-unavailable')
    receipt = _terminal(record, operation, epoch, 'failed', 'child-failed', services.now()).thaw()
    receipt.update(reason=decision.reason.value, budget_used=used)
    effects, claim = (), None
    if decision.diagnostic_bytes is not None:
        target = 'evidence/fresh-review/' + decision.diagnostic_digest[7:] + '.json'
        effect = make_evidence_effect('fresh-review-output', target, decision.diagnostic_bytes)
        claim = FreshReviewInputEffectClaim(effect.kind, effect.target, effect.digest, effect.size)
        effects = (effect,)
        receipt.update(output_ref=dict(kind=effect.kind, relative_path=effect.target,
                                      digest=effect.digest, size=effect.size), output_digest=effect.digest)
    command = ImportFreshReviewOutput(request_id, operation, epoch,
        freeze_json_value(receipt_document(decode_terminal_receipt(receipt))),
        freeze_json_value(observation), freeze_json_value(used), candidate,
        None if raw is None else base64.b64encode(raw).decode('ascii'), claim)
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
        if record.status == 'failed':
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
