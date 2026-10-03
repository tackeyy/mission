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

from .json_codec import freeze_json_value
from .model import ContentAddressedRef, FrozenJsonObject

REQUEST_SCHEMA = 'mission-fresh-review-request/1'
PROJECTION_SCHEMA = 'mission-fresh-review/1'
BUDGET_LIMITS = {'wall_time_sec': 300, 'max_tool_calls': 64, 'max_replays': 16,
                 'max_output_bytes': 256 * 1024, 'max_packet_bytes': 1024 * 1024}
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z')
_DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')


class FreshReviewError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def canonical_bytes(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                          allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError) as exc:
        raise FreshReviewError('fresh-review-json-invalid') from exc


def canonical_digest(value):
    return 'sha256:' + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _closed(value, fields, code):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise FreshReviewError(code)
    return value


def _identifier(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise FreshReviewError('fresh-review-identity-invalid')
    return value


def _digest(value):
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise FreshReviewError('fresh-review-digest-invalid')
    return value


def _unique_strings(value, validator, *, nonempty=True):
    if not isinstance(value, list) or nonempty and not value:
        raise FreshReviewError('fresh-review-list-invalid')
    items = tuple(validator(item) for item in value)
    if len(set(items)) != len(items):
        raise FreshReviewError('fresh-review-list-invalid')
    return items


def validate_budgets(values):
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
    if not isinstance(perspective, str) or not perspective or perspective != perspective.strip() or len(perspective) > 128 or '\x00' in perspective:
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
    if not isinstance(bindings, list) or not bindings:
        raise FreshReviewError('fresh-review-candidate-invalid')
    decoded, snapshots, definitions, roles = [], {}, {}, set()
    for item in bindings:
        _closed(item, CandidateBinding.__dataclass_fields__, 'fresh-review-candidate-invalid')
        for key in ('criterion_id', 'command_id'):
            _identifier(item[key])
        for key in ('definition_digest', 'snapshot_digest'):
            _digest(item[key])
        if not isinstance(item['role'], str) or item['role'] not in ('verification', 'replay'):
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


@dataclass(frozen=True)
class FreshReviewProjection:
    requests: tuple[FreshReviewRecord, ...] = ()


def projection_document(projection):
    if not isinstance(projection, FreshReviewProjection) or type(projection.requests) is not tuple or any(not isinstance(item, FreshReviewRecord) for item in projection.requests):
        raise FreshReviewError('fresh-review-projection-shape-invalid')
    records = []
    for record in projection.requests:
        fields = {key: getattr(record, key) for key in FreshReviewRecord.__dataclass_fields__}
        fields['request'] = request_document(record.request)
        if record.result is not None and not isinstance(record.result, FrozenJsonObject):
            raise FreshReviewError('fresh-review-result-invalid')
        fields['result'] = record.result.thaw() if record.result is not None else None
        records.append(fields)
    return {'schema': PROJECTION_SCHEMA, 'requests': records}


def decode_projection(document):
    if not isinstance(document, dict):
        raise FreshReviewError('fresh-review-projection-shape-invalid')
    if 'fresh_review' not in document:
        return FreshReviewProjection()
    value = _closed(document['fresh_review'], ('schema', 'requests'), 'fresh-review-projection-shape-invalid')
    if value['schema'] != PROJECTION_SCHEMA:
        raise FreshReviewError('fresh-review-projection-schema-invalid')
    if not isinstance(value['requests'], list):
        raise FreshReviewError('fresh-review-projection-shape-invalid')
    records, ids, nonces, operations = [], set(), set(), set()
    for item in value['requests']:
        _closed(item, FreshReviewRecord.__dataclass_fields__, 'fresh-review-record-invalid')
        fields = dict(item)
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
                if not isinstance(fields['result'], dict) or len(canonical_bytes(fields['result'])) > request.max_output_bytes:
                    raise FreshReviewError('fresh-review-result-invalid')
                fields['result'] = freeze_json_value(fields['result'])
        else:
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
        if record.prepare_operation_id == operation_id or record.operation_id == operation_id and record.request.nonce != request.nonce:
            raise FreshReviewError('fresh-review-operation-conflict')
    matches = [record for record in projection.requests if record.request.nonce == request.nonce]
    if len(matches) != 1:
        raise FreshReviewError('fresh-review-request-unavailable')
    record = matches[0]
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
        if command.operation_id in (record.prepare_operation_id, record.operation_id):
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
