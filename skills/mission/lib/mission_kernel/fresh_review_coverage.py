"""Pure latest-attempt judgements. This module does not enable completion.

Attempt order is PrepareFreshReview publication order, supplied as an immutable
sequence, never timestamps. Current input digests are recaptured per request;
command snapshots are compared only for commands bound by the selected request.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .fresh_review import (
    FreshReviewError, FreshReviewRequest, _digest, _identifier, candidate_identity,
    canonical_digest, decode_request, request_document,
    WithdrawnFreshReviewRecord, FreshReviewProjection, decode_projection, projection_document,
)
from .fresh_review_receipts import (
    CompletedFreshReview, ContextMode, CoverageStatus, FreshReviewTerminal,
    decode_terminal_receipt, receipt_document,
)


class FreshReviewReason(str, Enum):
    MISSING = 'acceptance-fresh-review-missing'
    STALE = 'acceptance-fresh-review-stale'
    PENDING = 'acceptance-fresh-review-pending'
    NON_INDEPENDENT = 'acceptance-fresh-review-non-independent'
    COVERAGE_OPEN = 'acceptance-coverage-open'


# Cross-scope failures use the same priority as the decision table, not criterion
# iteration order. This applies only to fresh-review decisions, not other gates.
REASON_ORDER = tuple(FreshReviewReason)
_ACTIVE = ('pending', 'dispatch-unknown', 'running')
_TERMINAL = ('completed', 'failed', 'blocked', 'abandoned-unknown')


@dataclass(frozen=True)
class FreshReviewAttempt:
    # Withdrawn attempts retain selection identity/scope, but no request bindings.
    request: FreshReviewRequest | WithdrawnFreshReviewRecord
    status: str
    terminal_receipt: FreshReviewTerminal | None = None


@dataclass(frozen=True)
class FreshReviewBindings:
    contract_digest: str
    input_digests: tuple[tuple[str, str], ...]
    candidate_snapshots: tuple[tuple[str, str], ...]
    repair_results: tuple = ()


@dataclass(frozen=True)
class FreshReviewDecision:
    effective_coverage: CoverageStatus
    reason_code: FreshReviewReason | None
    request_id: str | None


def _pairs(values):
    if type(values) is not tuple:
        raise FreshReviewError('fresh-review-current-bindings-invalid')
    result = {}
    for pair in values:
        if type(pair) is not tuple or len(pair) != 2:
            raise FreshReviewError('fresh-review-current-bindings-invalid')
        key, digest = pair
        _identifier(key); _digest(digest)
        if key in result:
            raise FreshReviewError('fresh-review-current-bindings-invalid')
        result[key] = digest
    return result


def _validated(attempts, current):
    if type(attempts) is not tuple or not isinstance(current, FreshReviewBindings):
        raise FreshReviewError('fresh-review-attempt-invalid')
    _digest(current.contract_digest)
    inputs, snapshots = _pairs(current.input_digests), _pairs(current.candidate_snapshots)
    ids, nonces = set(), set()
    for item in attempts:
        if not isinstance(item, FreshReviewAttempt) or not isinstance(item.status, str):
            raise FreshReviewError('fresh-review-attempt-invalid')
        if isinstance(item.request, WithdrawnFreshReviewRecord):
            if item.status != 'withdrawn' or item.terminal_receipt is not None:
                raise FreshReviewError('fresh-review-attempt-invalid')
            request = decode_projection({'fresh_review': projection_document(
                FreshReviewProjection((item.request,)))}).requests[0]
        else:
            if item.status not in _ACTIVE + _TERMINAL:
                raise FreshReviewError('fresh-review-attempt-invalid')
            request = decode_request(request_document(item.request))
        if request.request_id in ids or request.nonce in nonces:
            raise FreshReviewError('fresh-review-identity-reused')
        ids.add(request.request_id); nonces.add(request.nonce)
        if item.status == 'withdrawn':
            continue
        if item.status in _ACTIVE:
            if item.terminal_receipt is not None:
                raise FreshReviewError('fresh-review-attempt-invalid')
            continue
        receipt = decode_terminal_receipt(receipt_document(item.terminal_receipt))
        if (receipt.outcome != item.status or receipt.request_id != request.request_id
                or receipt.nonce != request.nonce or receipt.request_digest != canonical_digest(request_document(request))
                or receipt.candidate_digest != request.candidate_digest):
            raise FreshReviewError('fresh-review-terminal-binding-mismatch')
        launch = getattr(receipt, 'launch_receipt', None)
        if launch is not None and (launch.adapter_registration_digest != request.adapter_registration_digest
                or launch.received_input_digest != request.input_digest
                or request.input_ref.size > launch.enforced_budget.max_packet_bytes
                or not set(launch.enforced_tools).issubset(request.allowed_tools)
                or any(getattr(launch.enforced_budget, key) > getattr(request, key)
                       for key in launch.enforced_budget.__dataclass_fields__)):
            raise FreshReviewError('fresh-review-launch-binding-mismatch')
        if isinstance(receipt, CompletedFreshReview):
            independent = (launch.context_mode == ContextMode.FRESH
                           and launch.child_identity != launch.parent_identity
                           and launch.context_identity != launch.parent_identity)
            if receipt.independent != independent:
                raise FreshReviewError('fresh-review-independent-invalid')
    return inputs, snapshots


def _criteria(values):
    if type(values) is not tuple:
        raise FreshReviewError('fresh-review-criterion-invalid')
    for key in values:
        _identifier(key)
    if len(set(values)) != len(values):
        raise FreshReviewError('fresh-review-criterion-invalid')
    return frozenset(values)


def _judge(attempts, criteria, current, inputs, snapshots):
    latest = next((item for item in reversed(attempts) if criteria.issubset(item.request.criterion_ids)), None)
    if latest is None:
        return FreshReviewDecision(CoverageStatus.PENDING, FreshReviewReason.MISSING, None)
    request = latest.request
    # Withdrawal has no bindings: missing must precede every freshness check.
    if latest.status == 'withdrawn':
        return FreshReviewDecision(CoverageStatus.PENDING, FreshReviewReason.MISSING, request.request_id)
    command_ids = {binding.command_id for binding in request.candidate_bindings}
    if (request.contract_digest != current.contract_digest or inputs.get(request.request_id) != request.input_digest
            or not command_ids.issubset(snapshots)
            or request.candidate_digest != candidate_identity({key: snapshots[key] for key in command_ids})):
        return FreshReviewDecision(CoverageStatus.OPEN, FreshReviewReason.STALE, request.request_id)
    if latest.status in _ACTIVE:
        return FreshReviewDecision(CoverageStatus.PENDING, FreshReviewReason.PENDING, request.request_id)
    receipt = latest.terminal_receipt
    if isinstance(receipt, CompletedFreshReview) and not receipt.independent:
        return FreshReviewDecision(CoverageStatus.OPEN, FreshReviewReason.NON_INDEPENDENT, request.request_id)
    if not isinstance(receipt, CompletedFreshReview) or receipt.coverage_receipt.status == CoverageStatus.OPEN:
        return FreshReviewDecision(CoverageStatus.OPEN, FreshReviewReason.COVERAGE_OPEN, request.request_id)
    return FreshReviewDecision(CoverageStatus.VALID, None, request.request_id)


def derive_effective_coverage(attempts, required_criterion_ids, current):
    """Apply design section 4 to the latest whole attempt, in all states."""
    inputs, snapshots = _validated(attempts, current)
    return _judge(attempts, _criteria(required_criterion_ids), current, inputs, snapshots)


def judge_criterion(attempts, criterion_id, current):
    """Section 5 condition 3 uses exactly the section 4 ordering and codes."""
    inputs, snapshots = _validated(attempts, current)
    return _judge(attempts, _criteria((criterion_id,)), current, inputs, snapshots)


def judge_fresh_review(attempts, required_criterion_ids, current):
    """Combine whole and criterion decisions by table priority, not input order.

    This does not evaluate verification, search results, open obligations or
    findings, and is not a replacement for the completion gate. Equal-priority
    failures select whole coverage first, then criteria in identifier order.
    """
    inputs, snapshots = _validated(attempts, current)
    required = _criteria(required_criterion_ids)
    decisions = [_judge(attempts, required, current, inputs, snapshots)]
    decisions.extend(_judge(attempts, frozenset((key,)), current, inputs, snapshots)
                     for key in sorted(required))
    failures = [decision for decision in decisions if decision.reason_code is not None]
    return min(failures, key=lambda item: REASON_ORDER.index(item.reason_code)) if failures else decisions[0]
