"""Inert, immutable completion inputs bound to published review evidence.

Decoding is not a clean-review verdict. Search/coverage/finding facts, including
blocked replays and open Low findings, remain exactly as published.
"""
from __future__ import annotations

from dataclasses import dataclass

from .fresh_review import (
    FreshReviewError, canonical_bytes, canonical_digest, request_document,
    _closed, _identifier, _unique_strings, _json_builtins,
)
from .fresh_review_receipts import CompletedFreshReview
from .json_codec import freeze_json_value
from .model import FrozenJsonObject

INVALID = 'acceptance-fresh-review-evidence-invalid'
MISMATCH = 'acceptance-fresh-review-evidence-mismatch'
INCOMPLETE = 'acceptance-fresh-review-completion-carrier-incomplete'


@dataclass(frozen=True)
class FreshReviewCompletionEvidence:
    request_id: str
    coverage: FrozenJsonObject
    findings: tuple[FrozenJsonObject, ...]


def _bound(value, reference):
    _json_builtins(value, 'completion-evidence-invalid')
    if (canonical_digest(value) != reference.digest
            or len(canonical_bytes(value)) != reference.size):
        raise FreshReviewError(MISMATCH)


def _decode_completion_evidence(request, terminal, coverage, findings):
    """Decode published facts, checking canonical bytes against state-owned refs."""
    from .fresh_review_output import decode_output, _hypothesis, FindingHypothesis
    if (not isinstance(terminal, CompletedFreshReview) or type(findings) is not tuple
            or len(findings) != len(terminal.findings)):
        raise FreshReviewError(INVALID)
    _bound(coverage, terminal.coverage_receipt.evidence_ref)
    _closed(coverage, ('schema', 'request_id', 'request_digest', 'candidate_digest',
        'status', 'open_requirement_ids', 'open_finding_ids', 'criterion_results', 'requirements'), INVALID)
    identity = dict(request_id=request.request_id,
        request_digest=canonical_digest(request_document(request)), candidate_digest=request.candidate_digest)
    if (coverage['schema'] != 'mission-fresh-review-coverage/1'
            or any(coverage[key] != value for key, value in identity.items())
            or coverage['status'] != terminal.coverage_receipt.status.value):
        raise FreshReviewError(INVALID)
    for key in ('open_requirement_ids', 'open_finding_ids'):
        _unique_strings(coverage[key], _identifier, nonempty=False)
    if type(coverage['criterion_results']) is not list:
        raise FreshReviewError(INVALID)
    results = []
    for item in coverage['criterion_results']:
        _closed(item, ('criterion_id', 'status', 'reason_code'), INVALID)
        results.append(dict(item, findings=[]))
    # Reuse the output's closed search/requirement/hypothesis decoder. The
    # published finding adds runner observations; it never authorizes a replay.
    output = {key: getattr(request, key) for key in ('mission_id', 'session_id',
        'requirement_digest', 'contract_digest', 'verifier_policy_digest', 'candidate_digest',
        'input_digest', 'adapter_registration_digest', 'iteration')}
    output.update(schema='mission-fresh-review-output/1', nonce=request.nonce, **identity,
                  criterion_results=results, coverage=coverage['requirements'])
    seen = set()
    for value, reference in zip(findings, terminal.findings):
        _bound(value, reference)
        extras = ('schema', 'request_id', 'request_digest', 'candidate_digest',
                  'status', 'reason_code', 'resolution', 'replay')
        hypothesis = {key: item for key, item in value.items() if key not in extras}
        _closed(value, (*(key for key in FindingHypothesis.__dataclass_fields__
                         if key != 'replay_evidence_ref'), *extras), INVALID)
        hypothesis['replay_evidence_ref'] = None
        decoded = _hypothesis(hypothesis, value.get('criterion_id'), published=True)
        if (value['schema'] != 'mission-fresh-review-finding/1'
                or any(value[key] != item for key, item in identity.items())
                or value['status'] not in ('verified', 'blocked') or value['resolution'] != 'open'
                or type(value['replay']) not in (dict, type(None))
                or decoded.finding_id in seen):
            raise FreshReviewError(INVALID)
        _identifier(value['reason_code'])
        seen.add(decoded.finding_id)
        result = next((item for item in results if item['criterion_id'] == decoded.criterion_id), None)
        if result is None:
            raise FreshReviewError(INVALID)
        # Search/requirement syntax is decoded below; published findings have
        # already been decoded with their distinct empty-observation boundary.
    decode_output(output)
    return FreshReviewCompletionEvidence(request.request_id, freeze_json_value(coverage),
                                         tuple(freeze_json_value(item) for item in findings))


def decode_completion_evidence(request, terminal, coverage, findings):
    """Fail closed on malformed evidence with stable completion reason codes."""
    try:
        return _decode_completion_evidence(request, terminal, coverage, findings)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        if isinstance(exc, FreshReviewError) and exc.code == MISMATCH:
            raise
        raise FreshReviewError(INVALID) from exc


def validate_completion_carriers(projection, evidence, bindings, contract_digest):
    """Authenticate optional carriers; absent inputs retain pre-carrier behavior.

    The default empty tuple with no bindings skips authentication. Once either
    input is supplied, both are required; an empty evidence tuple with bindings
    must still match the completed request set. Reject incomplete carriers before
    evidence shape/content, then bindings shape/content.
    Current snapshots/input digests may differ from a request: those are facts
    for the later completion judgement, not a reason to enable completion here.
    """
    from .fresh_review import FreshReviewRecord, _digest
    from .fresh_review_receipts import decode_terminal_receipt
    from .fresh_review_coverage import FreshReviewBindings, _pairs
    if type(evidence) is tuple and not evidence and bindings is None:
        return
    if evidence is None or bindings is None:
        raise FreshReviewError(INCOMPLETE)
    if type(evidence) is not tuple:
        raise FreshReviewError(INVALID)
    records = tuple(item for item in projection.requests if isinstance(item, FreshReviewRecord))
    completed = {item.request.request_id: item for item in records if item.status == 'completed'}
    seen = set()
    for item in evidence:
        if (type(item) is not FreshReviewCompletionEvidence or type(item.request_id) is not str
                or type(item.coverage) is not FrozenJsonObject
                or type(item.findings) is not tuple
                or any(type(finding) is not FrozenJsonObject for finding in item.findings)
                or item.request_id not in completed or item.request_id in seen):
            raise FreshReviewError(INVALID)
        seen.add(item.request_id)
        record = completed[item.request_id]
        try:
            decode_completion_evidence(record.request, decode_terminal_receipt(record.result.thaw()),
                item.coverage.thaw(), tuple(finding.thaw() for finding in item.findings))
        except RecursionError as exc:
            raise FreshReviewError(INVALID) from exc
    if seen != set(completed):
        raise FreshReviewError(INVALID)
    code = 'acceptance-fresh-review-bindings-invalid'
    try:
        if type(bindings) is not FreshReviewBindings:
            raise FreshReviewError(code)
        _digest(bindings.contract_digest)
        inputs, snapshots = _pairs(bindings.input_digests), _pairs(bindings.candidate_snapshots)
        if (bindings.contract_digest != contract_digest
                or set(inputs) != {item.request.request_id for item in records}
                or set(snapshots) != {binding.command_id for item in records
                                     for binding in item.request.candidate_bindings}):
            raise FreshReviewError(code)
    except (ValueError, TypeError, AttributeError) as exc:
        raise FreshReviewError(code) from exc
