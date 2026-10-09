"""Closed fresh-review requests and deterministic one-use state rules (D1).

Runtime launch and result publication belong to later command families. These
pure reducers reserve and consume a nonce without interpreting reviewer output.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import Enum
import hashlib
import json
import re

from .errors import StateBoundaryError
from .json_codec import freeze_json_value
from .model import ContentAddressedRef, FrozenJsonObject

REQUEST_SCHEMA = 'mission-fresh-review-request/1'
PROJECTION_SCHEMA = 'mission-fresh-review/1'
BUDGET_LIMITS = {'wall_time_sec': 300, 'max_tool_calls': 64, 'max_replays': 16,
                 'max_output_bytes': 256 * 1024, 'max_packet_bytes': 1024 * 1024}
FRESH_REVIEW_INT_MAX = 2**63 - 1
FRESH_REVIEW_ID_MAX_CHARS = 128
FRESH_REVIEW_DIGEST_CHARS = 71
FRESH_REVIEW_TIMESTAMP_CHARS = 27
FRESH_REVIEW_FINDINGS_LIMIT = 61
FRESH_REVIEW_EVIDENCE_MAX_BYTES = BUDGET_LIMITS['max_output_bytes']
FRESH_REVIEW_CAPACITY_WITHDRAWN_REASON = 'capacity-withdrawn'
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
_DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')


class FreshReviewError(StateBoundaryError):
    def __init__(self, code):
        super().__init__(code, code)


def canonical_bytes(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                          allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FreshReviewError('fresh-review-json-invalid') from exc


def canonical_digest(value):
    return 'sha256:' + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _json_builtins(value, code, fields=None):
    """Check the wire tree before trusting any overridable operation.

    Only what a JSON decoder cannot produce is rejected here: subclasses and
    foreign types, non-str keys, cycles and nesting too deep to walk. Every
    exact JSON value passes, so plain JSON keeps the reason codes of the
    closed-shape and field checks that follow (typed fields reject floats
    there). Descriptors retain field reasons through objects and list items
    (None key) for the values this walk does reject.
    """
    ancestors = set()

    def visit(item, reason, children):
        if type(item) not in (dict, list, str, int, float, bool, type(None)):
            raise FreshReviewError('fresh-review-' + reason)
        if type(item) not in (dict, list):
            return
        if id(item) in ancestors:
            raise FreshReviewError('fresh-review-' + reason)
        ancestors.add(id(item))
        try:
            if type(item) is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        raise FreshReviewError('fresh-review-' + reason)
                    descriptor = children.get(key, children.get(None, (reason, {})))
                    visit(child, descriptor[0], descriptor[1])
            else:
                child_code, child_fields = children.get(None, (reason, children))
                for child in item:
                    visit(child, child_code, child_fields)
        finally:
            ancestors.remove(id(item))

    try:
        visit(value, code, fields or {})
    except RecursionError as exc:
        raise FreshReviewError('fresh-review-' + code) from exc


def _field_codes(code, names):
    return {name: (code, {}) for name in names.split()}


_ID_JSON_CODES = _field_codes('identity-invalid',
    'request_id nonce mission_id session_id operation_id prepare_operation_id '
    'dispatch_operation_id commit_operation_id withdraw_operation_id parent_identity '
    'child_identity context_identity invocation_id adapter_id reservation_id criterion_id command_id')
_DIGEST_JSON_CODES = _field_codes('digest-invalid',
    'requirement_digest contract_digest verifier_policy_digest candidate_digest input_digest '
    'adapter_registration_digest request_digest received_input_digest launch_digest output_digest '
    'prepare_intent_digest prepare_payload_digest intent_digest payload_digest '
    'outbound_packet_digest definition_digest snapshot_digest digest')
_CANDIDATE_JSON_CODES = {**_ID_JSON_CODES, **_DIGEST_JSON_CODES}
_REQUEST_JSON_CODES = {
    **_ID_JSON_CODES, **_DIGEST_JSON_CODES,
    **_field_codes('budget-invalid', ' '.join(BUDGET_LIMITS)),
    **_field_codes('control-invalid', 'allowed_tools created_at'),
    **_field_codes('request-schema-invalid', 'schema'),
    **_field_codes('iteration-invalid', 'iteration'),
    **_field_codes('perspective-invalid', 'perspective'),
    'criterion_ids': ('list-invalid', {None: ('identity-invalid', {})}),
    'candidate_bindings': ('candidate-invalid', _CANDIDATE_JSON_CODES),
    'input_ref': ('input-ref-invalid', {}),
}
_RECORD_JSON_CODES = {
    **_ID_JSON_CODES, **_DIGEST_JSON_CODES,
    'request': ('request-shape-invalid', _REQUEST_JSON_CODES),
    'result': ('result-invalid', {}),
    'criterion_ids': ('list-invalid', {None: ('identity-invalid', {})}),
    'reason': ('reason-invalid', {}),
    'withdraw_fencing_epoch': ('fence-invalid', {}),
}
_PROJECTION_JSON_CODES = {
    'fresh_review': ('projection-shape-invalid', {
        'schema': ('projection-schema-invalid', {}),
        'requests': ('projection-shape-invalid', {None: ('record-invalid', _RECORD_JSON_CODES)}),
    }),
}


def _closed(value, fields, code):
    if type(value) is not dict or set(value) != set(fields):
        raise FreshReviewError(code)
    return value


def _identifier(value):
    if type(value) is not str or not _ID.fullmatch(value):
        raise FreshReviewError('fresh-review-identity-invalid')
    return value


def _digest(value):
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise FreshReviewError('fresh-review-digest-invalid')
    return value


def _integer(value, code):
    if type(value) is not int or not 0 <= value <= FRESH_REVIEW_INT_MAX:
        raise FreshReviewError(code)
    return value


def _unique_strings(value, validator, *, nonempty=True):
    if type(value) is not list or nonempty and not value:
        raise FreshReviewError('fresh-review-list-invalid')
    items = tuple(validator(item) for item in value)
    if len(set(items)) != len(items):
        raise FreshReviewError('fresh-review-list-invalid')
    return items


def validate_budgets(values):
    _json_builtins(values, 'budget-invalid')
    _closed(values, BUDGET_LIMITS, 'fresh-review-budget-invalid')
    if any(type(values[key]) is not int or not 1 <= values[key] <= maximum
           for key, maximum in BUDGET_LIMITS.items()):
        raise FreshReviewError('fresh-review-budget-invalid')
    return dict(values)


class ToolCapability(str, Enum):
    READ_CANDIDATE = 'read-candidate'
    REPLAY_VERIFIER = 'replay-verifier'


@dataclass(frozen=True)
class CandidateBinding:
    criterion_id: str
    role: str
    command_id: str
    definition_digest: str
    snapshot_digest: str


@dataclass(frozen=True)
class FreshReviewRequest:
    schema: str
    request_id: str
    nonce: str
    mission_id: str
    session_id: str
    requirement_digest: str
    contract_digest: str
    verifier_policy_digest: str
    candidate_digest: str
    input_digest: str
    adapter_registration_digest: str
    candidate_bindings: tuple[CandidateBinding, ...]
    criterion_ids: tuple[str, ...]
    iteration: int
    perspective: str
    allowed_tools: tuple[ToolCapability, ...]
    wall_time_sec: int
    max_tool_calls: int
    max_replays: int
    max_output_bytes: int
    max_packet_bytes: int
    input_ref: ContentAddressedRef
    created_at: str


def candidate_identity(snapshots):
    _json_builtins(snapshots, 'candidate-invalid', {None: ('digest-invalid', {})})
    if not isinstance(snapshots, dict) or not snapshots:
        raise FreshReviewError('fresh-review-candidate-invalid')
    validated = {_identifier(key): _digest(value) for key, value in snapshots.items()}
    return canonical_digest(dict(sorted(validated.items())))


def request_document(request):
    if not isinstance(request, FreshReviewRequest):
        raise FreshReviewError('fresh-review-request-shape-invalid')
    try:
        value = asdict(request)
        value['candidate_bindings'] = [asdict(item) for item in request.candidate_bindings]
        value['criterion_ids'] = list(request.criterion_ids)
        value['allowed_tools'] = [item.value for item in request.allowed_tools]
        return value
    except (TypeError, AttributeError) as exc:
        raise FreshReviewError('fresh-review-request-shape-invalid') from exc


def decode_request(value):
    _json_builtins(value, 'request-shape-invalid', _REQUEST_JSON_CODES)
    _closed(value, FreshReviewRequest.__dataclass_fields__, 'fresh-review-request-shape-invalid')
    if value['schema'] != REQUEST_SCHEMA:
        raise FreshReviewError('fresh-review-request-schema-invalid')
    fields = dict(value)
    for key in ('request_id', 'nonce', 'mission_id', 'session_id'):
        _identifier(fields[key])
    for key in ('requirement_digest', 'contract_digest', 'verifier_policy_digest', 'candidate_digest',
                'input_digest', 'adapter_registration_digest'):
        _digest(fields[key])
    fields['criterion_ids'] = _unique_strings(fields['criterion_ids'], _identifier)
    if type(fields['iteration']) is not int or fields['iteration'] < 0:
        raise FreshReviewError('fresh-review-iteration-invalid')
    perspective = fields['perspective']
    if type(perspective) is not str or not perspective or perspective != perspective.strip() or len(perspective) > 128 or '\x00' in perspective:
        raise FreshReviewError('fresh-review-perspective-invalid')
    canonical_bytes(perspective)
    validate_budgets({key: fields[key] for key in BUDGET_LIMITS})
    try:
        fields['allowed_tools'] = _unique_strings(fields['allowed_tools'], ToolCapability, nonempty=False)
        timestamp = datetime.fromisoformat(fields['created_at'].replace('Z', '+00:00'))
        if timestamp.utcoffset() is None:
            raise ValueError('naive')
    except (ValueError, TypeError, AttributeError) as exc:
        raise FreshReviewError('fresh-review-control-invalid') from exc
    bindings = fields['candidate_bindings']
    if type(bindings) is not list or not bindings:
        raise FreshReviewError('fresh-review-candidate-invalid')
    decoded, snapshots, definitions, roles = [], {}, {}, set()
    for item in bindings:
        _closed(item, CandidateBinding.__dataclass_fields__, 'fresh-review-candidate-invalid')
        for key in ('criterion_id', 'command_id'):
            _identifier(item[key])
        for key in ('definition_digest', 'snapshot_digest'):
            _digest(item[key])
        if type(item['role']) is not str or item['role'] not in ('verification', 'replay'):
            raise FreshReviewError('fresh-review-candidate-invalid')
        pair = (item['criterion_id'], item['role'])
        if pair in roles or item['criterion_id'] not in fields['criterion_ids']:
            raise FreshReviewError('fresh-review-candidate-invalid')
        roles.add(pair)
        command = item['command_id']
        for mapping, field in ((snapshots, 'snapshot_digest'), (definitions, 'definition_digest')):
            if command in mapping and mapping[command] != item[field]:
                raise FreshReviewError('fresh-review-candidate-invalid')
            mapping[command] = item[field]
        decoded.append(CandidateBinding(**item))
    if {identifier for identifier, role in roles if role == 'verification'} != set(fields['criterion_ids']) or candidate_identity(snapshots) != fields['candidate_digest']:
        raise FreshReviewError('fresh-review-candidate-invalid')
    fields['candidate_bindings'] = tuple(decoded)
    reference = _closed(fields['input_ref'], ContentAddressedRef.__dataclass_fields__, 'fresh-review-input-ref-invalid')
    expected_path = 'evidence/fresh-review/' + fields['input_digest'][7:] + '.json'
    if reference['kind'] != 'fresh-review-input' or reference['relative_path'] != expected_path or reference['digest'] != fields['input_digest'] or type(reference['size']) is not int or not 1 <= reference['size'] <= fields['max_packet_bytes']:
        raise FreshReviewError('fresh-review-input-ref-invalid')
    fields['input_ref'] = ContentAddressedRef(**reference)
    return FreshReviewRequest(**fields)


@dataclass(frozen=True)
class FreshReviewRecord:
    request: FreshReviewRequest
    prepare_operation_id: str
    prepare_intent_digest: str
    prepare_payload_digest: str
    status: str = 'pending'
    operation_id: str | None = None
    intent_digest: str | None = None
    payload_digest: str | None = None
    result: FrozenJsonObject | None = None
    dispatch: FrozenJsonObject | None = None
    launch: FrozenJsonObject | None = None
    launch_operation_id: str | None = None
    independent: bool | None = None


@dataclass(frozen=True)
class WithdrawnFreshReviewRecord:
    """A pending request taken out of capacity without launching D.

    The tombstone replaces the pending record in place; it keeps the
    one-use identities (request_id/nonce/prepare_operation_id) so neither
    can be reused, but drops the request body so the encoded form is always
    strictly smaller than the pending record it replaces.
    """

    request_id: str
    nonce: str
    prepare_operation_id: str
    request_digest: str
    criterion_ids: tuple[str, ...]
    withdraw_operation_id: str
    withdraw_fencing_epoch: int
    status: str = 'withdrawn'
    reason: str = FRESH_REVIEW_CAPACITY_WITHDRAWN_REASON


@dataclass(frozen=True)
class FreshReviewProjection:
    requests: tuple[FreshReviewRecord | WithdrawnFreshReviewRecord, ...] = ()


def record_operation_ids(record):
    if isinstance(record, WithdrawnFreshReviewRecord):
        return (record.prepare_operation_id, record.withdraw_operation_id)
    commit = (record.result.thaw()['commit_operation_id']
              if record.status in ('blocked', 'abandoned-unknown', 'failed') else None)
    return (record.prepare_operation_id, record.operation_id, record.launch_operation_id, commit)


def projection_document(projection):
    if not isinstance(projection, FreshReviewProjection) or type(projection.requests) is not tuple:
        raise FreshReviewError('fresh-review-projection-shape-invalid')
    records = []
    for record in projection.requests:
        if isinstance(record, WithdrawnFreshReviewRecord):
            fields = {key: getattr(record, key) for key in WithdrawnFreshReviewRecord.__dataclass_fields__}
            fields['criterion_ids'] = list(record.criterion_ids)
            records.append(fields)
            continue
        if not isinstance(record, FreshReviewRecord):
            raise FreshReviewError('fresh-review-projection-shape-invalid')
        fields = {key: getattr(record, key) for key in FreshReviewRecord.__dataclass_fields__
                  if key not in ('dispatch', 'launch', 'launch_operation_id', 'independent')}
        fields['request'] = request_document(record.request)
        if record.result is not None and not isinstance(record.result, FrozenJsonObject):
            raise FreshReviewError('fresh-review-result-invalid')
        fields['result'] = record.result.thaw() if record.result is not None else None
        for key in ('dispatch', 'launch', 'launch_operation_id', 'independent'):
            value = getattr(record, key)
            if value is not None:
                fields[key] = value.thaw() if key in ('dispatch', 'launch') else value
        records.append(fields)
    return {'schema': PROJECTION_SCHEMA, 'requests': records}


def decode_projection(document):
    _json_builtins(document, 'projection-shape-invalid', _PROJECTION_JSON_CODES)
    if not isinstance(document, dict):
        raise FreshReviewError('fresh-review-projection-shape-invalid')
    if 'fresh_review' not in document:
        return FreshReviewProjection()
    value = _closed(document['fresh_review'], ('schema', 'requests'), 'fresh-review-projection-shape-invalid')
    if value['schema'] != PROJECTION_SCHEMA:
        raise FreshReviewError('fresh-review-projection-schema-invalid')
    if type(value['requests']) is not list:
        raise FreshReviewError('fresh-review-projection-shape-invalid')
    records, ids, nonces, operations = [], set(), set(), set()
    for item in value['requests']:
        if not isinstance(item, dict):
            raise FreshReviewError('fresh-review-record-invalid')
        if item.get('status') == 'withdrawn':
            _closed(item, WithdrawnFreshReviewRecord.__dataclass_fields__, 'fresh-review-record-invalid')
            fields = dict(item)
            request_id = _identifier(fields['request_id']); nonce = _identifier(fields['nonce'])
            prepare_operation_id = _identifier(fields['prepare_operation_id'])
            _digest(fields['request_digest'])
            fields['criterion_ids'] = _unique_strings(fields['criterion_ids'], _identifier)
            if fields['reason'] != FRESH_REVIEW_CAPACITY_WITHDRAWN_REASON:
                raise FreshReviewError('fresh-review-reason-invalid')
            withdraw_operation_id = _identifier(fields['withdraw_operation_id'])
            _integer(fields['withdraw_fencing_epoch'], 'fresh-review-fence-invalid')
            if request_id in ids or nonce in nonces or prepare_operation_id in operations:
                raise FreshReviewError('fresh-review-identity-reused')
            ids.add(request_id); nonces.add(nonce); operations.add(prepare_operation_id)
            # Checked after this record's own prepare id, as reserved records do.
            if withdraw_operation_id in operations:
                raise FreshReviewError('fresh-review-operation-conflict')
            operations.add(withdraw_operation_id)
            records.append(WithdrawnFreshReviewRecord(**fields))
            continue
        fields = {'dispatch': None, 'launch': None, 'launch_operation_id': None, 'independent': None, **item}
        _closed(fields, FreshReviewRecord.__dataclass_fields__, 'fresh-review-record-invalid')
        fields = dict(fields)
        fields['request'] = request = decode_request(fields['request'])
        _identifier(fields['prepare_operation_id'])
        _digest(fields['prepare_intent_digest']); _digest(fields['prepare_payload_digest'])
        if request.request_id in ids or request.nonce in nonces or fields['prepare_operation_id'] in operations:
            raise FreshReviewError('fresh-review-identity-reused')
        ids.add(request.request_id); nonces.add(request.nonce); operations.add(fields['prepare_operation_id'])
        if fields['status'] == 'pending':
            if any(fields[key] is not None for key in ('operation_id', 'intent_digest', 'payload_digest', 'result')):
                raise FreshReviewError('fresh-review-record-invalid')
        elif fields['status'] in ('reserved', 'consumed'):
            operation = _identifier(fields['operation_id'])
            if operation in operations:
                raise FreshReviewError('fresh-review-operation-conflict')
            operations.add(operation)
            _digest(fields['intent_digest']); _digest(fields['payload_digest'])
            if fields['status'] == 'reserved' and fields['result'] is not None:
                raise FreshReviewError('fresh-review-record-invalid')
            if fields['status'] == 'consumed':
                if type(fields['result']) is not dict or len(canonical_bytes(fields['result'])) > request.max_output_bytes:
                    raise FreshReviewError('fresh-review-result-invalid')
                fields['result'] = freeze_json_value(fields['result'])
        elif fields['status'] in ('dispatch-unknown', 'running', 'blocked', 'abandoned-unknown', 'failed'):
            from .fresh_review_dispatch import decode_dispatch_record
            fields = decode_dispatch_record(fields)
            operation = fields['operation_id']
            if operation in operations:
                raise FreshReviewError('fresh-review-operation-conflict')
            operations.add(operation)
            launch_operation = fields['launch_operation_id']
            if launch_operation is not None and launch_operation != operation:
                if launch_operation in operations:
                    raise FreshReviewError('fresh-review-operation-conflict')
                operations.add(launch_operation)
        else:
            raise FreshReviewError('fresh-review-record-invalid')
        if fields['status'] in ('blocked', 'abandoned-unknown', 'failed'):
            commit = fields['result'].thaw()['commit_operation_id']
            if commit not in (fields['operation_id'], fields['launch_operation_id']):
                if commit in operations:
                    raise FreshReviewError('fresh-review-operation-conflict')
                operations.add(commit)
        if fields['status'] in ('pending', 'reserved', 'consumed') and any(fields[key] is not None for key in ('dispatch', 'launch', 'launch_operation_id', 'independent')):
            raise FreshReviewError('fresh-review-record-invalid')
        records.append(FreshReviewRecord(**fields))
    return FreshReviewProjection(tuple(records))


def validate_projection_backing(document, projection):
    if decode_projection(document) != projection:
        raise FreshReviewError('fresh-review-projection-mismatch')
    # Validate typed values as well: direct dataclass construction is not decoding.
    if decode_projection({'fresh_review': projection_document(projection)}) != projection:
        raise FreshReviewError('fresh-review-projection-invalid')


def _record(projection, request, operation_id, intent_digest, payload_digest):
    _identifier(operation_id); _digest(intent_digest); _digest(payload_digest)
    for record in projection.requests:
        if isinstance(record, WithdrawnFreshReviewRecord):
            if operation_id in (record.prepare_operation_id, record.withdraw_operation_id):
                raise FreshReviewError('fresh-review-operation-conflict')
            continue
        if record.prepare_operation_id == operation_id or operation_id in record_operation_ids(record) and record.request.nonce != request.nonce:
            raise FreshReviewError('fresh-review-operation-conflict')
    matches = [record for record in projection.requests
               if (record.nonce if isinstance(record, WithdrawnFreshReviewRecord) else record.request.nonce) == request.nonce]
    if len(matches) != 1:
        raise FreshReviewError('fresh-review-request-unavailable')
    record = matches[0]
    if isinstance(record, WithdrawnFreshReviewRecord):
        raise FreshReviewError('fresh-review-request-withdrawn')
    if canonical_bytes(request_document(record.request)) != canonical_bytes(request_document(request)):
        raise FreshReviewError('fresh-review-stale')
    if record.operation_id is not None:
        if record.operation_id != operation_id:
            raise FreshReviewError('fresh-review-nonce-reused')
        if (record.intent_digest, record.payload_digest) != (intent_digest, payload_digest):
            raise FreshReviewError('fresh-review-operation-conflict')
    return record


def _replace_record(projection, old, new):
    return FreshReviewProjection(tuple(new if item == old else item for item in projection.requests))


def withdraw_request(projection, *, request_id, operation_id, fencing_epoch):
    """Pure replacement of one pending record with its withdrawn tombstone.

    Capacity admission (whether a withdrawal is needed, and that the base is
    over capacity) is the caller's concern (D2c); this reducer only enforces
    the pending -> withdrawn transition, idempotent resend, and the identity
    rules that keep nonce/request_id/operation ids one-use.
    """
    _identifier(request_id); _identifier(operation_id)
    _integer(fencing_epoch, 'fresh-review-fence-invalid')
    matches = [record for record in projection.requests
               if (record.request_id if isinstance(record, WithdrawnFreshReviewRecord)
                   else record.request.request_id) == request_id]
    if len(matches) != 1:
        raise FreshReviewError('fresh-review-request-unavailable')
    target = matches[0]
    if isinstance(target, WithdrawnFreshReviewRecord):
        if target.withdraw_operation_id == operation_id:
            if target.withdraw_fencing_epoch != fencing_epoch:
                raise FreshReviewError('fresh-review-operation-conflict')
            return projection
        raise FreshReviewError('fresh-review-request-withdrawn')
    if target.status != 'pending':
        raise FreshReviewError('fresh-review-request-not-pending')
    for record in projection.requests:
        other_operations = ((record.prepare_operation_id, record.withdraw_operation_id)
                            if isinstance(record, WithdrawnFreshReviewRecord)
                            else record_operation_ids(record))
        if operation_id in other_operations:
            raise FreshReviewError('fresh-review-operation-conflict')
    tombstone = WithdrawnFreshReviewRecord(
        request_id=target.request.request_id, nonce=target.request.nonce,
        prepare_operation_id=target.prepare_operation_id,
        request_digest=canonical_digest(request_document(target.request)),
        criterion_ids=tuple(target.request.criterion_ids),
        withdraw_operation_id=operation_id, withdraw_fencing_epoch=fencing_epoch)
    return _replace_record(projection, target, tombstone)


def reserve_request(projection, request, *, operation_id, intent_digest, payload_digest):
    record = _record(projection, request, operation_id, intent_digest, payload_digest)
    if record.status != 'pending':
        return projection  # Historical checkpoint; never redispatch.
    return _replace_record(projection, record, replace(record, status='reserved', operation_id=operation_id,
                                                       intent_digest=intent_digest, payload_digest=payload_digest))


def consume_request(projection, request, *, operation_id, intent_digest, payload_digest, result):
    record = _record(projection, request, operation_id, intent_digest, payload_digest)
    if not isinstance(result, dict) or len(canonical_bytes(result)) > request.max_output_bytes:
        raise FreshReviewError('fresh-review-result-invalid')
    frozen = freeze_json_value(result)
    if record.status == 'consumed':
        if record.result != frozen:
            raise FreshReviewError('fresh-review-operation-conflict')
        return projection
    if record.status != 'reserved':
        raise FreshReviewError('fresh-review-not-reserved')
    return _replace_record(projection, record, replace(record, status='consumed', result=frozen))


def prepare_request_state(state, command):
    """Validate bindings and install one immutable request in its reserved slot."""
    from acceptance_contract import canonical_contract_digest, verifier_definition_digest
    validate_projection_backing(state.legacy_passthrough.thaw() if state.legacy_passthrough is not None else state.extensions.thaw(), state.fresh_review)
    request = decode_request(request_document(command.request))
    _identifier(command.operation_id); _digest(command.intent_digest); _digest(command.payload_digest)
    for record in state.fresh_review.requests:
        if isinstance(record, WithdrawnFreshReviewRecord):
            if command.operation_id == record.prepare_operation_id:
                raise FreshReviewError('fresh-review-request-withdrawn')
            if command.operation_id == record.withdraw_operation_id:
                raise FreshReviewError('fresh-review-operation-conflict')
            if record.nonce == request.nonce or record.request_id == request.request_id:
                raise FreshReviewError('fresh-review-nonce-reused')
            continue
        if command.operation_id in record_operation_ids(record):
            if (record.prepare_operation_id, record.prepare_intent_digest, record.prepare_payload_digest, record.request) != (command.operation_id, command.intent_digest, command.payload_digest, request):
                raise FreshReviewError('fresh-review-operation-conflict')
            return state
        if record.request.nonce == request.nonce or record.request.request_id == request.request_id:
            raise FreshReviewError('fresh-review-nonce-reused')
    document = state.legacy_passthrough.thaw() if state.legacy_passthrough is not None else state.extensions.thaw()
    contract = document.get('acceptance_contract')
    if not isinstance(contract, dict) or contract.get('schema') != 'mission-acceptance-contract/2':
        raise FreshReviewError('fresh-review-contract-unavailable')
    if request.mission_id != (document.get('mission_id') or document.get('session_id')) or request.session_id != state.identity.session_id or request.iteration != state.control.iteration:
        raise FreshReviewError('fresh-review-stale')
    policy = contract.get('verifier_policy')
    if not isinstance(policy, dict) or not isinstance(policy.get('commands'), dict):
        raise FreshReviewError('fresh-review-contract-invalid')
    if (request.requirement_digest, request.contract_digest, request.verifier_policy_digest) != (contract.get('requirement_digest'), canonical_contract_digest(contract), policy.get('digest')):
        raise FreshReviewError('fresh-review-stale')
    criteria = contract.get('criteria')
    if not isinstance(criteria, list) or any(not isinstance(item, dict) or not isinstance(item.get('id'), str) for item in criteria):
        raise FreshReviewError('fresh-review-contract-invalid')
    by_id = {item['id']: item for item in criteria}
    expected = []
    for identifier in request.criterion_ids:
        criterion = by_id.get(identifier)
        if criterion is None or not isinstance(criterion.get('command_id'), str):
            raise FreshReviewError('fresh-review-criterion-invalid')
        command_id = criterion['command_id']
        definition = policy['commands'].get(command_id)
        if not isinstance(definition, dict):
            raise FreshReviewError('fresh-review-contract-invalid')
        expected.append((identifier, 'verification', command_id, verifier_definition_digest(definition)))
        replay = definition.get('replay')
        if replay is not None:
            if not isinstance(replay, dict) or not isinstance(replay.get('command_id'), str) or not isinstance(policy['commands'].get(replay['command_id']), dict):
                raise FreshReviewError('fresh-review-contract-invalid')
            expected.append((identifier, 'replay', replay['command_id'], verifier_definition_digest(policy['commands'][replay['command_id']])))
    observed = [(item.criterion_id, item.role, item.command_id, item.definition_digest) for item in request.candidate_bindings]
    if observed != expected:
        raise FreshReviewError('fresh-review-stale')
    if not isinstance(command.packet, FrozenJsonObject):
        raise FreshReviewError('fresh-review-input-invalid')
    packet = command.packet.thaw()
    content = canonical_bytes(packet)
    if canonical_digest(packet) != request.input_digest or len(content) != request.input_ref.size:
        raise FreshReviewError('fresh-review-stale')
    if (packet.get('requirement_text'), packet.get('requirements'), packet.get('criteria'), packet.get('verifier_policy'), packet.get('perspective')) != (contract.get('requirement_text'), contract.get('requirements'), criteria, policy, request.perspective):
        raise FreshReviewError('fresh-review-stale')
    snapshots = packet.get('snapshots')
    if not isinstance(snapshots, dict) or {item.command_id: item.snapshot_digest for item in request.candidate_bindings} != {key: value.get('digest') for key, value in snapshots.items() if isinstance(value, dict)}:
        raise FreshReviewError('fresh-review-stale')
    claim = command.effect
    if claim is None or tuple(getattr(claim, key, None) for key in ('kind', 'target', 'digest', 'size')) != (request.input_ref.kind, request.input_ref.relative_path, request.input_digest, len(content)):
        raise FreshReviewError('fresh-review-input-effect-invalid')
    record = FreshReviewRecord(request, command.operation_id, command.intent_digest, command.payload_digest)
    projection = FreshReviewProjection((*state.fresh_review.requests, record))
    document['fresh_review'] = projection_document(projection)
    frozen = freeze_json_value(document)
    changes = {'fresh_review': projection, 'snapshot_provenance': None}
    changes['legacy_passthrough' if state.legacy_passthrough is not None else 'extensions'] = frozen
    return replace(state, **changes)
