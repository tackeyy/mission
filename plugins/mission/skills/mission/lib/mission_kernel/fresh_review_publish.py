"""Output publication rechecks raw bytes and bound runner evidence."""
from __future__ import annotations
import base64
import binascii
import json
import hashlib
from dataclasses import replace

from .fresh_review import FreshReviewError, canonical_bytes, canonical_digest, request_document, FRESH_REVIEW_EVIDENCE_MAX_BYTES, _closed, _integer
from .fresh_review_output import inspect_output, derive_output_coverage, replay_eligibility, output_document, validate_output_sender
from .fresh_review_receipts import decode_terminal_receipt, FailedFreshReview, CompletedFreshReview, TerminalOutcome, TerminalReason, BudgetUsed, _timestamp
from .model import FrozenJsonObject
from .json_codec import freeze_json_value
from .commands import FreshReviewInputEffectClaim, RecordVerificationReceipt
from .evidence import project_verification_receipt, EvidenceRuleError


def completed_evidence(output, request, contract, replays=()):
    """Derive published coverage/findings from hypotheses and runner observations.

    Observations are application facts, not child-supplied replay receipts. They
    never enter normal verification history or resolve an open finding.
    A command criterion expects its registered check to pass. Only a bound,
    observed positive exit code confirms a counterexample to that check.
    Matching authored facts alone do not establish a violation: passed replays,
    signal termination, prose expectations and unobserved prohibited effects
    remain unconfirmed.
    """
    coverage = derive_output_coverage(output, request, contract)
    hypotheses = [item for result in output.criterion_results for item in result.findings]
    if (type(replays) is not tuple or len(replays) != len(hypotheses)
            or any(not isinstance(item, FrozenJsonObject) for item in replays)):
        raise FreshReviewError('fresh-review-replay-invalid')
    findings = []
    for hypothesis, carrier in zip(hypotheses, replays):
        observed = carrier.thaw()
        if set(observed) != {'finding_id', 'reason_code', 'replay'} or observed['finding_id'] != hypothesis.finding_id:
            raise FreshReviewError('fresh-review-replay-invalid')
        eligibility = replay_eligibility(request, hypothesis, contract['verifier_policy'])
        replay, reason = observed['replay'], observed['reason_code']
        status = 'blocked'
        if replay is None:
            if reason not in ((eligibility,) if eligibility else ('replay-budget-exceeded', 'replay-unavailable', 'replay-input-path-conflict')):
                raise FreshReviewError('fresh-review-replay-invalid')
        else:
            if eligibility is not None or reason != 'none':
                raise FreshReviewError('fresh-review-replay-invalid')
            try:
                project_verification_receipt(RecordVerificationReceipt('observed', freeze_json_value(replay)))
            except (EvidenceRuleError, TypeError, ValueError) as exc:
                raise FreshReviewError('fresh-review-replay-invalid') from exc
            binding = next(item for item in request.candidate_bindings if item.role == 'replay'
                           and item.criterion_id == hypothesis.criterion_id)
            source = next(item for item in request.candidate_bindings if item.role == 'verification'
                          and item.criterion_id == hypothesis.criterion_id)
            commands = contract['verifier_policy']['commands']
            definition = commands[binding.command_id]
            policy = commands[source.command_id]['replay']
            repro = hypothesis.repro_input.thaw()
            digest = 'sha256:' + hashlib.sha256(repro['artifact_kind'].encode() + b'\0' +
                policy['relative_path'].encode() + b'\0' + repro['content'].encode()).hexdigest()
            if any(replay.get(key) != value for key, value in dict(contract_digest=request.contract_digest,
                criterion_id=hypothesis.criterion_id, candidate_digest=binding.snapshot_digest,
                verifier_policy_digest=request.verifier_policy_digest, verifier_definition_digest=binding.definition_digest,
                argv=definition['argv'], relative_cwd=definition['relative_cwd'],
                runner_provenance='mission-public-cli/1', repro_input_digest=(None if replay.get('status') == 'blocked' and replay.get('repro_input_digest') is None else digest)).items()):
                raise FreshReviewError('fresh-review-replay-binding-invalid')
            actual = hypothesis.actual.thaw()
            expected = hypothesis.expected.thaw()
            criterion = next(item for item in contract['criteria'] if item['id'] == hypothesis.criterion_id)
            supported = (criterion['verification_kind'] == 'command' and criterion['command_id'] == source.command_id
                and replay['status'] == 'failed' and not replay['timed_out']
                and replay['exit_code'] is not None and replay['exit_code'] > 0 and not replay['output_truncated']
                and expected.get('criterion_id') == hypothesis.criterion_id
                and all(key in replay and type(replay[key]) is type(value) and replay[key] == value
                        for key, value in actual.items()))
            status = 'verified' if supported else 'blocked'
            reason = 'none' if supported else replay['block_reason'] or 'replay-claim-unconfirmed'
        raw = next(item for result in output_document(output)['criterion_results'] for item in result['findings']
                   if item['finding_id'] == hypothesis.finding_id)
        raw.update(schema='mission-fresh-review-finding/1', request_id=request.request_id,
            request_digest=canonical_digest(request_document(request)), candidate_digest=request.candidate_digest,
            status=status, reason_code=reason, resolution='open',
            actual={} if replay is None else {key: replay[key] for key in
                ('status','exit_code','timed_out','executed_count','output_digest','observed_output_bytes','output_truncated')},
            replay=replay)
        raw.pop('replay_evidence_ref')  # The full bound receipt is embedded, no separate effect.
        findings.append(canonical_bytes(raw))
    payload = dict(schema='mission-fresh-review-coverage/1', request_id=request.request_id,
        request_digest=canonical_digest(request_document(request)), candidate_digest=request.candidate_digest,
        status=coverage.status, open_requirement_ids=list(coverage.open_requirement_ids),
        open_finding_ids=list(coverage.open_finding_ids),
        criterion_results=[dict(criterion_id=item.criterion_id,status=item.status,reason_code=item.reason_code)
                           for item in output.criterion_results],
        requirements=output_document(output)['coverage'])
    return canonical_bytes(payload), tuple(findings)


def evidence_claim(kind, content):
    digest = 'sha256:' + hashlib.sha256(content).hexdigest()
    if len(content) > FRESH_REVIEW_EVIDENCE_MAX_BYTES:
        raise FreshReviewError('fresh-review-output-over-import-limit')
    return FreshReviewInputEffectClaim(kind, 'evidence/fresh-review/' + digest[7:] + '.json', digest, len(content))


def reference(claim):
    return dict(kind=claim.kind, relative_path=claim.target, digest=claim.digest, size=claim.size)



def inspect_import(record, observation, raw, *, candidate_digest, budget_used, contract, ended_at, replays=None):
    decision = inspect_output(record, observation, raw, candidate_digest=candidate_digest, budget_used=budget_used)
    if decision.outcome != 'completed':
        return decision
    def failed(reason):
        return replace(decision, outcome=TerminalOutcome.FAILED, reason=reason, output=None)
    deadline = record.dispatch.thaw().get('deadline_at')
    if deadline is not None and ended_at >= deadline:
        return failed(TerminalReason.TIMEOUT)
    try:
        derive_output_coverage(decision.output, record.request, contract)
    except FreshReviewError:
        return failed(TerminalReason.OUTPUT_INVALID)
    if replays is not None:
        coverage, findings = completed_evidence(decision.output, record.request, contract, replays)
        if any(len(content) > FRESH_REVIEW_EVIDENCE_MAX_BYTES for content in (coverage, *findings)):
            return failed(TerminalReason.OUTPUT_OVER_IMPORT_LIMIT)
    return decision

def validate_failed_import(record, command, contract=None):
    if not isinstance(command.observation, FrozenJsonObject) or not isinstance(command.budget_used, FrozenJsonObject):
        raise FreshReviewError('fresh-review-output-observation-invalid')
    validate_output_sender(record, command.observation.thaw())
    try:
        raw = None if command.output_base64 is None else base64.b64decode(command.output_base64, validate=True)
    except (TypeError, ValueError, binascii.Error) as exc:
        raise FreshReviewError('fresh-review-output-observation-invalid') from exc
    if type(command.replay_results) is not tuple or any(not isinstance(item, FrozenJsonObject) for item in command.replay_results):
        raise FreshReviewError('fresh-review-replay-invalid')
    used = _closed(command.budget_used.thaw(), BudgetUsed.__dataclass_fields__,
                   'fresh-review-output-observation-invalid')
    for value in used.values():
        _integer(value, 'fresh-review-output-observation-invalid')
    if used['replays'] < sum(
            item.thaw().get('replay') is not None for item in command.replay_results
            if isinstance(item, FrozenJsonObject)):
        raise FreshReviewError('fresh-review-replay-budget-invalid')
    receipt = decode_terminal_receipt(command.receipt.thaw())
    for carrier in command.replay_results:
        if not isinstance(carrier, FrozenJsonObject):
            raise FreshReviewError('fresh-review-replay-invalid')
        replay = carrier.thaw().get('replay')
        if replay is not None:
            if type(replay) is not dict:
                raise FreshReviewError('fresh-review-replay-invalid')
            def parsed(value):
                # B records UTC seconds; D records fixed-width microseconds.
                return _timestamp(value[:-1] + '.000000Z' if type(value) is str and len(value) == 20 and value.endswith('Z') else value)
            start, finish = parsed(replay.get('started_at')), parsed(replay.get('finished_at'))
            if not (_timestamp(record.launch.thaw()['started_at']).replace(microsecond=0)
                    <= start <= finish <= _timestamp(receipt.ended_at)):
                raise FreshReviewError('fresh-review-replay-time-invalid')
    decision = inspect_import(record, command.observation.thaw(), raw,
        candidate_digest=command.candidate_digest, budget_used=command.budget_used.thaw(), contract=contract,
        ended_at=receipt.ended_at, replays=command.replay_results)
    if isinstance(receipt, CompletedFreshReview) and decision.outcome == 'completed':
        content, findings = completed_evidence(decision.output, record.request, contract, command.replay_results)
        finding_claims = tuple(evidence_claim('fresh-review-finding', item) for item in findings)
        coverage = evidence_claim('fresh-review-coverage', content)
        if (command.coverage_effect != coverage or command.findings_effect != finding_claims
                or receipt_document_for_coverage(receipt) != dict(status=json.loads(content)['status'], evidence_ref=reference(coverage))
                or command.receipt.thaw()['findings'] != [reference(item) for item in finding_claims]
                or receipt.independent != decision.independent
                or command.receipt.thaw()['budget_used'] != command.budget_used.thaw()):
            raise FreshReviewError('fresh-review-output-effect-invalid')
    elif (not isinstance(receipt, FailedFreshReview) or decision.outcome != 'failed'
            or receipt.reason != decision.reason or command.receipt.thaw()['budget_used'] != command.budget_used.thaw()):
        raise FreshReviewError('fresh-review-output-import-required')
    if isinstance(receipt, FailedFreshReview) and (command.coverage_effect is not None or command.findings_effect
            or command.replay_results and decision.reason != TerminalReason.OUTPUT_OVER_IMPORT_LIMIT):
        raise FreshReviewError('fresh-review-output-effect-invalid')
    output_reference = receipt.output_ref
    expected = decision.diagnostic_bytes
    if expected is None:
        if output_reference is not None or command.effect is not None:
            raise FreshReviewError('fresh-review-output-effect-invalid')
    elif (output_reference is None or type(command.effect) is not FreshReviewInputEffectClaim
            or (output_reference.digest, output_reference.size) != (decision.diagnostic_digest, len(expected))
            or (command.effect.kind, command.effect.target, command.effect.digest, command.effect.size) !=
            (output_reference.kind, output_reference.relative_path, decision.diagnostic_digest, len(expected))):
        raise FreshReviewError('fresh-review-output-effect-invalid')


def receipt_document_for_coverage(receipt):
    from .fresh_review_receipts import receipt_document
    return receipt_document(receipt)['coverage_receipt']
