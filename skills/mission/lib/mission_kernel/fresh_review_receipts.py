"""Pure D2b receipt contracts. No publication, IO, or completion authority.

Findings and coverage evidence are content-addressed references; their output
content, replay validation and atomic publication belong to the D2 importer.
The failed-output writer binds diagnostics to its terminal; completed output
publication and replay validation remain separate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
import re
from typing import Union
from types import MappingProxyType

from .fresh_review import (
    FreshReviewError, ToolCapability, _closed, _digest, _identifier,
    _json_builtins, _field_codes, _ID_JSON_CODES, _DIGEST_JSON_CODES,
    _unique_strings, validate_budgets, canonical_digest, FRESH_REVIEW_FINDINGS_LIMIT,
    FRESH_REVIEW_INT_MAX, FRESH_REVIEW_EVIDENCE_MAX_BYTES, FRESH_REVIEW_ID_MAX_CHARS,
)

from .model import ContentAddressedRef

# Exact maxima of accepted shapes under the canonical state JSON encoder.
# Completed usage remains bounded by the enforced budget; other outcomes may
# report usage up to INT_MAX. These are shape bytes, not state mutation deltas.
FRESH_REVIEW_MAX_ENCODED_BYTES = MappingProxyType({
    'launch': 1498, 'completed': 17925, 'failed': 3100, 'blocked': 1211,
    'abandoned-unknown': 2765, 'intent': 1797, 'running': 3455,
})

LAUNCH_SCHEMA = 'mission-fresh-review-launch/1'

TERMINAL_SCHEMA = 'mission-fresh-review-terminal/1'
_TIMESTAMP = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}Z\Z')


class ContextMode(str, Enum):
    FRESH = 'fresh'
    INLINE = 'inline'
    SHARED = 'shared'


@dataclass(frozen=True)
class EnforcedBudget:
    wall_time_sec: int
    max_tool_calls: int
    max_replays: int
    max_output_bytes: int
    max_packet_bytes: int


@dataclass(frozen=True)
class FreshReviewLaunchReceipt:
    schema: str
    request_id: str
    request_digest: str
    nonce: str
    operation_id: str
    fencing_epoch: int
    adapter_registration_digest: str
    parent_identity: str
    child_identity: str
    context_identity: str
    context_mode: ContextMode
    received_input_digest: str
    started_at: str
    enforced_tools: tuple[ToolCapability, ...]
    enforced_budget: EnforcedBudget


def _choice(value, enum, code):
    try:
        if type(value) is not str:
            raise ValueError('not a string')
        return enum(value)
    except (TypeError, ValueError) as exc:
        raise FreshReviewError(code) from exc


def _integer(value, code):
    if type(value) is not int or not 0 <= value <= FRESH_REVIEW_INT_MAX:
        raise FreshReviewError(code)
    return value


def _timestamp(value):
    try:
        if type(value) is not str or not _TIMESTAMP.fullmatch(value):
            raise ValueError('not a canonical timestamp')
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.utcoffset() is None:
            raise ValueError('naive')
    except (ValueError, TypeError, OverflowError) as exc:
        raise FreshReviewError('fresh-review-timestamp-invalid') from exc
    return parsed


_LAUNCH_JSON_CODES = {
    **_ID_JSON_CODES, **_DIGEST_JSON_CODES,
    **_field_codes('launch-schema-invalid', 'schema'),
    **_field_codes('fence-invalid', 'fencing_epoch'),
    **_field_codes('timestamp-invalid', 'started_at'),
    **_field_codes('context-invalid', 'context_mode'),
    **_field_codes('tools-invalid', 'enforced_tools'),
    **_field_codes('budget-invalid', 'enforced_budget'),
}
_REFERENCE_JSON_CODES = _field_codes('evidence-ref-invalid', 'kind relative_path size')
_REFERENCE_JSON_CODES.update(_field_codes('digest-invalid', 'digest'))
_TERMINAL_JSON_CODES = {
    **_ID_JSON_CODES, **_DIGEST_JSON_CODES,
    **_field_codes('terminal-schema-invalid', 'schema'),
    **_field_codes('outcome-invalid', 'outcome'),
    **_field_codes('reason-invalid', 'reason'),
    **_field_codes('fence-invalid', 'dispatch_fencing_epoch commit_fencing_epoch'),
    **_field_codes('budget-used-invalid', 'budget_used'),
    **_field_codes('timestamp-invalid', 'ended_at'),
    **_field_codes('independent-invalid', 'independent'),
    **_field_codes('launch-attempted-invalid', 'launch_attempted'),
    **_field_codes('cancel-invalid', 'cancel_result'),
    'launch_receipt': ('launch-shape-invalid', _LAUNCH_JSON_CODES),
    'output_ref': ('evidence-ref-invalid', _REFERENCE_JSON_CODES),
    'coverage_receipt': ('coverage-invalid', {
        'evidence_ref': ('evidence-ref-invalid', _REFERENCE_JSON_CODES)}),
    'findings': ('findings-invalid', {None: ('evidence-ref-invalid', _REFERENCE_JSON_CODES)}),
}
_DISPATCH_JSON_CODES = {
    **_ID_JSON_CODES, **_DIGEST_JSON_CODES,
    **_field_codes('fence-invalid', 'fencing_epoch'),
    **_field_codes('timestamp-invalid', 'deadline_at'),
    **_field_codes('budget-class-invalid', 'budget_class'),
}
_RUNNING_JSON_CODES = {
    **_field_codes('digest-invalid', 'launch_digest'),
    'dispatch': ('dispatch-invalid', _DISPATCH_JSON_CODES),
    'launch_receipt': ('launch-shape-invalid', _LAUNCH_JSON_CODES),
}


def decode_launch_receipt(value):
    _json_builtins(value, 'launch-shape-invalid', _LAUNCH_JSON_CODES)
    fields = dict(_closed(value, FreshReviewLaunchReceipt.__dataclass_fields__,
                          'fresh-review-launch-shape-invalid'))
    if fields['schema'] != LAUNCH_SCHEMA:
        raise FreshReviewError('fresh-review-launch-schema-invalid')
    for key in ('request_id', 'nonce', 'operation_id', 'parent_identity',
                'child_identity', 'context_identity'):
        _identifier(fields[key])
    for key in ('request_digest', 'adapter_registration_digest', 'received_input_digest'):
        _digest(fields[key])
    _integer(fields['fencing_epoch'], 'fresh-review-fence-invalid')
    _timestamp(fields['started_at'])
    fields['context_mode'] = _choice(fields['context_mode'], ContextMode,
                                     'fresh-review-context-invalid')
    try:
        fields['enforced_tools'] = _unique_strings(fields['enforced_tools'], ToolCapability,
                                                   nonempty=False)
    except (TypeError, ValueError) as exc:
        raise FreshReviewError('fresh-review-tools-invalid') from exc
    fields['enforced_budget'] = EnforcedBudget(**validate_budgets(fields['enforced_budget']))
    return FreshReviewLaunchReceipt(**fields)


class TerminalOutcome(str, Enum):
    COMPLETED = 'completed'
    FAILED = 'failed'
    BLOCKED = 'blocked'
    ABANDONED_UNKNOWN = 'abandoned-unknown'


class TerminalReason(str, Enum):
    NONE = 'none'
    CHILD_FAILED = 'child-failed'
    OUTPUT_INVALID = 'output-invalid'
    OUTPUT_OVER_IMPORT_LIMIT = 'output-over-import-limit'
    BUDGET_EXCEEDED = 'budget-exceeded'
    BINDING_MISMATCH = 'binding-mismatch'
    TIMEOUT = 'timeout'
    INTERRUPTED = 'interrupted'
    LAUNCH_UNAVAILABLE = 'launch-unavailable'
    INPUT_TOO_LARGE = 'input-too-large'
    REGISTRATION_MISMATCH = 'registration-mismatch'
    IDENTITY_UNOBSERVABLE = 'identity-unobservable'
    INPUT_UNOBSERVABLE = 'input-unobservable'
    CAPABILITY_UNENFORCEABLE = 'capability-unenforceable'
    LAUNCH_INVALID = 'launch-invalid'
    CHILD_UNOBSERVABLE = 'child-unobservable'
    OUTPUT_UNOBSERVABLE = 'output-unobservable'


REASONS_BY_OUTCOME = {
    TerminalOutcome.COMPLETED: frozenset({TerminalReason.NONE}),
    TerminalOutcome.FAILED: frozenset({TerminalReason.CHILD_FAILED, TerminalReason.OUTPUT_INVALID,
        TerminalReason.BUDGET_EXCEEDED, TerminalReason.BINDING_MISMATCH,
        TerminalReason.TIMEOUT, TerminalReason.INTERRUPTED, TerminalReason.OUTPUT_OVER_IMPORT_LIMIT}),
    TerminalOutcome.BLOCKED: frozenset({TerminalReason.LAUNCH_UNAVAILABLE,
        TerminalReason.INPUT_TOO_LARGE, TerminalReason.REGISTRATION_MISMATCH,
        TerminalReason.IDENTITY_UNOBSERVABLE, TerminalReason.INPUT_UNOBSERVABLE,
        TerminalReason.CAPABILITY_UNENFORCEABLE, TerminalReason.LAUNCH_INVALID,
        TerminalReason.BINDING_MISMATCH}),
    TerminalOutcome.ABANDONED_UNKNOWN: frozenset({TerminalReason.CHILD_UNOBSERVABLE,
        TerminalReason.OUTPUT_UNOBSERVABLE, TerminalReason.INTERRUPTED}),
}


class CancelResult(str, Enum):
    NOT_REQUESTED = 'not-requested'
    CANCELLED = 'cancelled'
    FAILED = 'failed'
    UNKNOWN = 'unknown'


class CoverageStatus(str, Enum):
    PENDING = 'pending'
    VALID = 'valid'
    OPEN = 'open'


@dataclass(frozen=True)
class BudgetUsed:
    wall_time_sec: int
    tool_calls: int
    replays: int
    output_bytes: int


@dataclass(frozen=True)
class FreshReviewCoverageReceipt:
    """Disposition plus the immutable ledger-check evidence, not imported coverage."""
    status: CoverageStatus
    evidence_ref: ContentAddressedRef


@dataclass(frozen=True)
class FreshReviewTerminalReceipt:
    schema: str
    request_id: str
    request_digest: str
    nonce: str
    dispatch_operation_id: str
    dispatch_fencing_epoch: int
    commit_operation_id: str
    commit_fencing_epoch: int
    outcome: TerminalOutcome
    reason: TerminalReason
    candidate_digest: str
    budget_used: BudgetUsed
    ended_at: str


@dataclass(frozen=True)
class CompletedFreshReview(FreshReviewTerminalReceipt):
    launch_receipt: FreshReviewLaunchReceipt
    launch_digest: str
    output_ref: ContentAddressedRef
    output_digest: str
    coverage_receipt: FreshReviewCoverageReceipt
    findings: tuple[ContentAddressedRef, ...]
    independent: bool


@dataclass(frozen=True)
class FailedFreshReview(FreshReviewTerminalReceipt):
    launch_receipt: FreshReviewLaunchReceipt
    launch_digest: str
    output_ref: ContentAddressedRef | None = None
    output_digest: str | None = None


@dataclass(frozen=True)
class BlockedFreshReview(FreshReviewTerminalReceipt):
    launch_attempted: bool
    cancel_result: CancelResult


@dataclass(frozen=True)
class AbandonedFreshReview(FreshReviewTerminalReceipt):
    launch_receipt: FreshReviewLaunchReceipt | None = None
    launch_digest: str | None = None


FreshReviewTerminal = Union[CompletedFreshReview, FailedFreshReview, BlockedFreshReview, AbandonedFreshReview]
_VARIANTS = dict(zip(TerminalOutcome, (CompletedFreshReview, FailedFreshReview,
                                     BlockedFreshReview, AbandonedFreshReview)))


def _reference(value, kind, *, minimum_size=1):
    fields = _closed(value, ContentAddressedRef.__dataclass_fields__, 'fresh-review-evidence-ref-invalid')
    digest = _digest(fields['digest'])
    if (fields['kind'] != kind or fields['relative_path'] != 'evidence/fresh-review/' + digest[7:] + '.json'
            or type(fields['size']) is not int
            or not minimum_size <= fields['size'] <= FRESH_REVIEW_EVIDENCE_MAX_BYTES):
        raise FreshReviewError('fresh-review-evidence-ref-invalid')
    return ContentAddressedRef(**fields)


def decode_terminal_receipt(value):
    _json_builtins(value, 'terminal-shape-invalid', _TERMINAL_JSON_CODES)
    if type(value) is not dict:
        raise FreshReviewError('fresh-review-terminal-shape-invalid')
    outcome = _choice(value.get('outcome'), TerminalOutcome, 'fresh-review-outcome-invalid')
    variant = _VARIANTS[outcome]
    required = set(variant.__dataclass_fields__)
    # Optional evidence is absent on the wire, never null, and always paired.
    if outcome == TerminalOutcome.FAILED:
        pair = {'output_ref', 'output_digest'}
    elif outcome == TerminalOutcome.ABANDONED_UNKNOWN:
        pair = {'launch_receipt', 'launch_digest'}
    else:
        pair = set()
    required -= pair
    if (pair & value.keys() or outcome == TerminalOutcome.FAILED
            and value.get('reason') == TerminalReason.OUTPUT_OVER_IMPORT_LIMIT):
        required |= pair
    fields = dict(_closed(value, required, 'fresh-review-terminal-shape-invalid'))
    if any(item is None for item in fields.values()):
        raise FreshReviewError('fresh-review-terminal-null-invalid')
    if fields['schema'] != TERMINAL_SCHEMA:
        raise FreshReviewError('fresh-review-terminal-schema-invalid')
    fields['outcome'] = outcome
    fields['reason'] = _choice(fields['reason'], TerminalReason, 'fresh-review-reason-invalid')
    if fields['reason'] not in REASONS_BY_OUTCOME[outcome]:
        raise FreshReviewError('fresh-review-reason-invalid')
    for key in ('request_id', 'nonce', 'dispatch_operation_id', 'commit_operation_id'):
        _identifier(fields[key])
    for key in ('request_digest', 'candidate_digest'):
        _digest(fields[key])
    for key in ('dispatch_fencing_epoch', 'commit_fencing_epoch'):
        _integer(fields[key], 'fresh-review-fence-invalid')
    if fields['commit_fencing_epoch'] < fields['dispatch_fencing_epoch']:
        raise FreshReviewError('fresh-review-fence-order-invalid')
    ended = _timestamp(fields['ended_at'])
    used = _closed(fields['budget_used'], BudgetUsed.__dataclass_fields__, 'fresh-review-budget-used-invalid')
    fields['budget_used'] = BudgetUsed(**{key: _integer(item, 'fresh-review-budget-used-invalid')
                                        for key, item in used.items()})
    if 'launch_receipt' in fields:
        raw = fields['launch_receipt']
        fields['launch_receipt'] = launch = decode_launch_receipt(raw)
        if ended < _timestamp(launch.started_at):
            raise FreshReviewError('fresh-review-timestamp-order-invalid')
        if _digest(fields['launch_digest']) != canonical_digest(raw):
            raise FreshReviewError('fresh-review-launch-digest-mismatch')
        if (launch.request_id, launch.request_digest, launch.nonce, launch.operation_id, launch.fencing_epoch) != (
                fields['request_id'], fields['request_digest'], fields['nonce'],
                fields['dispatch_operation_id'], fields['dispatch_fencing_epoch']):
            raise FreshReviewError('fresh-review-launch-binding-mismatch')
    if 'output_ref' in fields:
        fields['output_ref'] = _reference(fields['output_ref'], 'fresh-review-output',
                                           minimum_size=0 if outcome == TerminalOutcome.FAILED else 1)
        if _digest(fields['output_digest']) != fields['output_ref'].digest:
            raise FreshReviewError('fresh-review-output-digest-mismatch')
    if outcome == TerminalOutcome.COMPLETED:
        limits = fields['launch_receipt'].enforced_budget
        if fields['output_ref'].size > limits.max_output_bytes:
            raise FreshReviewError('fresh-review-budget-exceeded')
        if any(getattr(fields['budget_used'], used) > getattr(limits, maximum)
               for used, maximum in (('wall_time_sec', 'wall_time_sec'), ('tool_calls', 'max_tool_calls'),
                                     ('replays', 'max_replays'), ('output_bytes', 'max_output_bytes'))):
            raise FreshReviewError('fresh-review-budget-exceeded')
        if type(fields['independent']) is not bool:
            raise FreshReviewError('fresh-review-independent-invalid')
        launch = fields['launch_receipt']
        if fields['independent'] and (launch.context_mode != ContextMode.FRESH
                or launch.child_identity == launch.parent_identity
                or launch.context_identity == launch.parent_identity):
            raise FreshReviewError('fresh-review-independent-invalid')
        coverage = _closed(fields['coverage_receipt'], ('status', 'evidence_ref'), 'fresh-review-coverage-invalid')
        status = _choice(coverage['status'], CoverageStatus, 'fresh-review-coverage-invalid')
        if status == CoverageStatus.PENDING:
            raise FreshReviewError('fresh-review-coverage-invalid')
        fields['coverage_receipt'] = FreshReviewCoverageReceipt(status,
            _reference(coverage['evidence_ref'], 'fresh-review-coverage'))
        if type(fields['findings']) is not list:
            raise FreshReviewError('fresh-review-findings-invalid')
        if len(fields['findings']) > FRESH_REVIEW_FINDINGS_LIMIT:
            raise FreshReviewError('fresh-review-findings-over-limit')
        fields['findings'] = tuple(_reference(item, 'fresh-review-finding') for item in fields['findings'])
        if len(set(fields['findings'])) != len(fields['findings']):
            raise FreshReviewError('fresh-review-findings-invalid')
    if outcome == TerminalOutcome.BLOCKED:
        if type(fields['launch_attempted']) is not bool:
            raise FreshReviewError('fresh-review-launch-attempted-invalid')
        if fields['launch_attempted'] and fields['reason'] in (
                TerminalReason.LAUNCH_UNAVAILABLE, TerminalReason.INPUT_TOO_LARGE):
            raise FreshReviewError('fresh-review-launch-attempted-invalid')
        fields['cancel_result'] = _choice(fields['cancel_result'], CancelResult, 'fresh-review-cancel-invalid')
        if fields['launch_attempted'] != (fields['cancel_result'] != CancelResult.NOT_REQUESTED):
            raise FreshReviewError('fresh-review-cancel-invalid')
    return variant(**fields)


def receipt_document(receipt):
    """Render a typed receipt without turning absent diagnostic evidence into null."""
    if not isinstance(receipt, (FreshReviewLaunchReceipt, CompletedFreshReview,
                                FailedFreshReview, BlockedFreshReview, AbandonedFreshReview)):
        raise FreshReviewError('fresh-review-receipt-invalid')

    def wire(value):
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {key: wire(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return [wire(item) for item in value]
        return value

    value = wire(asdict(receipt))
    if isinstance(receipt, (FailedFreshReview, AbandonedFreshReview)):
        for key in ('output_ref', 'output_digest') if isinstance(receipt, FailedFreshReview) else ('launch_receipt', 'launch_digest'):
            if value[key] is None:
                del value[key]
    return value


# Bounded additions to the D1 record, not copies of its variable-length request.
# D2c must use these closed shapes; new fields require updating the size contract.
FRESH_REVIEW_DISPATCH_SHAPE = MappingProxyType({
    'invocation_id': 'id', 'operation_id': 'id', 'outbound_packet_digest': 'digest',
    'iteration': 'integer', 'fencing_epoch': 'integer',
    'status': 'dispatch-unknown', 'lifecycle_state': 'dispatch-unknown',
    'parent_identity': 'id', 'adapter_id': 'id', 'deadline_at': 'timestamp',
    'reservation_id': 'id', 'budget_class': 'ascii',
})
FRESH_REVIEW_RUNNING_SHAPE = MappingProxyType({
    'status': 'running', 'dispatch': 'dispatch', 'launch_receipt': 'launch',
    'launch_digest': 'digest', 'independent': 'bool',
})
FRESH_REVIEW_BUDGET_CLASS_MAX_CHARS = FRESH_REVIEW_ID_MAX_CHARS


def validate_dispatch_intent(value):
    """Pure field bounds for the future writer; no dispatch or budget authority."""
    _json_builtins(value, 'dispatch-invalid', _DISPATCH_JSON_CODES)
    fields = _closed(value, FRESH_REVIEW_DISPATCH_SHAPE, 'fresh-review-dispatch-invalid')
    for key, bound in FRESH_REVIEW_DISPATCH_SHAPE.items():
        item = fields[key]
        if bound == 'id':
            _identifier(item)
        elif bound == 'digest':
            _digest(item)
        elif bound == 'integer':
            _integer(item, 'fresh-review-fence-invalid' if key == 'fencing_epoch'
                     else 'fresh-review-dispatch-invalid')
        elif bound == 'timestamp':
            _timestamp(item)
        elif bound == 'ascii':
            # Extension point: no fixed vocabulary or ID grammar. JSON escaping
            # can expand an ASCII control character to six encoded bytes.
            if (type(item) is not str or not item.isascii()
                    or not 1 <= len(item) <= FRESH_REVIEW_BUDGET_CLASS_MAX_CHARS):
                raise FreshReviewError('fresh-review-budget-class-invalid')
        elif item != bound or type(item) is not str:
            raise FreshReviewError('fresh-review-dispatch-invalid')
    return dict(fields)


def validate_running_record(value):
    """Bounded running additions; the dispatch writer owns transition bindings."""
    _json_builtins(value, 'running-invalid', _RUNNING_JSON_CODES)
    fields = _closed(value, FRESH_REVIEW_RUNNING_SHAPE, 'fresh-review-running-invalid')
    if fields['status'] != 'running' or type(fields['independent']) is not bool:
        raise FreshReviewError('fresh-review-running-invalid')
    validate_dispatch_intent(fields['dispatch'])
    decode_launch_receipt(fields['launch_receipt'])
    _digest(fields['launch_digest'])
    return dict(fields)
