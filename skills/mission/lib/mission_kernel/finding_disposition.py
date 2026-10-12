"""Independent finding dispositions retain original obligation and replay scope."""
from __future__ import annotations


def duplicate_scope(source, target):
    """A duplicate may delegate only the same replay and a containing scope."""
    return (source['criterion_id'] == target['criterion_id']
        and all(source['repro'][key] == target['repro'][key] for key in (
            'command_id', 'verifier_definition_digest', 'repro_input_digest', 'repro_digest', 'runner_repro_digest'))
        and isinstance(source['repro']['verifier_definition_digest'], str)
        and isinstance(source['repro']['runner_repro_digest'], str)
        and all(source['introduced_candidate'][key] == target['introduced_candidate'][key]
                for key in ('contract_digest', 'requirement_digest', 'verifier_policy_digest'))
        and set(source['requirement_ids']).issubset(target['requirement_ids'])
        and set(source['prohibited_side_effect_ids']).issubset(target['prohibited_side_effect_ids']))

from dataclasses import dataclass
from datetime import datetime
from .fresh_review import FreshReviewError, canonical_bytes, canonical_digest, _closed, _identifier, _digest, _integer
from .json_codec import freeze_json_value
from .model import FrozenJsonObject
from .repair_attempts import absent, reference

INVALID = 'disposition-invalid'
REQUEST_SCHEMA = 'mission-finding-disposition-request/1'
BLOCKED_REASONS = ('launch-unavailable', 'registration-mismatch', 'identity-unobservable', 'input-unobservable',
    'output-unobservable', 'output-invalid', 'binding-mismatch', 'capability-unenforceable',
    'publication-result-lost', 'effects-unavailable', 'candidate-stale', 'disposition-mapping-over-limit')


@dataclass(frozen=True)
class DispositionRequest:
    document: FrozenJsonObject


def request_id(body):
    return canonical_digest(dict(domain=REQUEST_SCHEMA, **{key: body[key] for key in (
        'mission_id', 'session_id', 'lineage_id', 'operation_id')}))


def validate_ref(value, kind=None, limit=262144):
    _closed(value, ('kind', 'relative_path', 'digest', 'size'), INVALID)
    _digest(value['digest']); _identifier(value['kind']); _integer(value['size'], INVALID)
    if (kind is not None and value['kind'] != kind or not 1 <= value['size'] <= limit
            or value['relative_path'] != 'evidence/' + value['kind'] + '/' + value['digest'][7:] + '.json'):
        raise FreshReviewError(INVALID)
    return value



def validate_source_ref(value, kind=None):
    """Keep D reference paths while requiring a general, bounded kind ID.

    Supporting evidence permits any valid identifier, not a private enum.
    An expected kind alone cannot validate a self-supplied kind's wire type.
    """
    from .fresh_review_receipts import _reference
    _closed(value, ('kind', 'relative_path', 'digest', 'size'), INVALID)
    _identifier(value['kind'])
    return _reference(value, value['kind'] if kind is None else kind)


def decode_request(value):
    _closed(value, ('schema', 'request_id', 'mission_id', 'session_id', 'lineage_id', 'operation_id',
        'origin_digest', 'contract_digest', 'requirement_digest', 'verifier_policy_digest', 'full_candidate_digest',
        'supersedes_attempt_id', 'input_ref', 'source_ref', 'adapter_registration_digest', 'wall_time_sec',
        'max_packet_bytes', 'max_output_bytes'), INVALID)
    if value['schema'] != REQUEST_SCHEMA or value['request_id'] != request_id(value):
        raise FreshReviewError(INVALID)
    for key in ('request_id', 'lineage_id', 'origin_digest', 'contract_digest', 'requirement_digest',
                'verifier_policy_digest', 'full_candidate_digest', 'adapter_registration_digest'):
        _digest(value[key])
    for key in ('mission_id', 'session_id', 'operation_id'):
        _identifier(value[key])
    supersedes = value['supersedes_attempt_id']
    if supersedes != absent('no-superseded-attempt'):
        _digest(supersedes)
    validate_ref(value['input_ref'], 'finding-disposition-input', 1048576)
    source = _closed(value['source_ref'], ('kind', 'relative_path', 'digest', 'size'), INVALID)
    if source['kind'] not in ('fresh-review-finding', 'fresh-review-output'):
        raise FreshReviewError(INVALID)
    validate_source_ref(source, source['kind'])
    _integer(value['wall_time_sec'], INVALID)
    if (not 1 <= value['wall_time_sec'] <= 300 or value['max_packet_bytes'] != 1048576
            or type(value['max_packet_bytes']) is not int or value['max_output_bytes'] != 262144
            or type(value['max_output_bytes']) is not int):
        raise FreshReviewError(INVALID)
    return DispositionRequest(freeze_json_value(value))


def lifecycle(row):
    attempts = row.get('attempts', ())
    if attempts and attempts[-1]['status'] == 'pending':
        return 'repairing'
    latest = row.get('dispositions', ())
    if latest:
        item = latest[-1]
        repair = attempts[-1] if attempts else None
        if repair and repair['status'] == 'verified' and repair['terminal'].get('generation', 0) > item['terminal'].get('generation', item['generation']):
            return 'verified'
        return item['terminal']['outcome'] if item['stage'] == 'terminal' and item['terminal']['outcome'] in ('rejected', 'deferred') else 'open'
    return 'verified' if attempts and attempts[-1]['status'] == 'verified' else 'open'


def validate_dispositions(row):
    values = row.get('dispositions', [])
    if type(values) is not list:
        raise FreshReviewError(INVALID)
    seen = set()
    for index, item in enumerate(values):
        _closed(item, ('request_id', 'request_ref', 'full_candidate_digest', 'supersedes_attempt_id',
            'operation_id', 'fencing_epoch', 'generation', 'stage', 'intent', 'launch_ref', 'terminal', 'duplicate_targets',
            *(('recovery',) if 'recovery' in item else ())), INVALID)
        for key in ('request_id', 'full_candidate_digest'):
            _digest(item[key])
        _identifier(item['operation_id'])
        for key in ('fencing_epoch', 'generation'):
            _integer(item[key], INVALID)
        validate_ref(item['request_ref'], 'finding-disposition-request')
        if item['request_id'] in seen or item['stage'] not in ('prepared', 'dispatched', 'running', 'terminal'):
            raise FreshReviewError(INVALID)
        if item['stage'] != 'terminal' and any(a['status'] == 'pending' for a in row.get('attempts', ())):
            raise FreshReviewError(INVALID)
        seen.add(item['request_id'])
        if item['stage'] != 'terminal' and (index != len(values)-1 or item['terminal'] != absent('not-terminal')):
            raise FreshReviewError(INVALID)
        if item['stage'] == 'prepared' and (item['intent'] != absent('not-dispatched') or item['launch_ref'] != absent('not-launched')):
            raise FreshReviewError(INVALID)
        if item['intent'] != absent('not-dispatched'):
            intent = _closed(item['intent'], ('operation_id', 'fencing_epoch', 'reservation_id', 'budget_class',
                'parent_identity', 'started_at', 'deadline_at'), INVALID)
            for key in ('operation_id', 'reservation_id', 'parent_identity'):
                _identifier(intent[key])
            _integer(intent['fencing_epoch'], INVALID)
            from .budget import _at
            if (intent['operation_id'] != 'disposition-dispatch:' + item['request_id'][7:]
                    or intent['budget_class'] != 'repair' or intent['fencing_epoch'] < item['fencing_epoch']
                    or _at(intent['started_at']) >= _at(intent['deadline_at'])):
                raise FreshReviewError(INVALID)
        elif item['stage'] in ('dispatched', 'running'):
            raise FreshReviewError(INVALID)
        if item['launch_ref'] != absent('not-launched'):
            validate_ref(item['launch_ref'], 'finding-disposition-launch')
            if item['intent'] == absent('not-dispatched'):
                raise FreshReviewError(INVALID)
        elif item['stage'] == 'running':
            raise FreshReviewError(INVALID)
        validate_recovery(item)
        if item['stage'] == 'terminal':
            end = _closed(item['terminal'], ('ref', 'operation_id', 'fencing_epoch', 'generation', 'outcome', 'reason_code'), INVALID)
            _identifier(end['operation_id']); _integer(end['generation'], INVALID); _integer(end['fencing_epoch'], INVALID)
            if (end['generation'] <= item['generation'] or end['fencing_epoch'] < item['fencing_epoch']
                    or end['outcome'] not in ('rejected', 'deferred', 'blocked')
                    or end['reason_code'] not in ('not-violation', 'duplicate', 'deferred', *BLOCKED_REASONS)):
                raise FreshReviewError(INVALID)
            if end['outcome'] in ('rejected', 'deferred'):
                validate_ref(end['ref'], 'finding-disposition')
                if (item['launch_ref'] == absent('not-launched') or end['reason_code'] not in ('not-violation', 'duplicate', 'deferred')
                        or (end['outcome'] == 'deferred') != (end['reason_code'] == 'deferred')):
                    raise FreshReviewError(INVALID)
            else:
                if end['reason_code'] not in BLOCKED_REASONS:
                    raise FreshReviewError(INVALID)
                if end['ref'] != absent('effects-unavailable'):
                    validate_ref(end['ref'], 'finding-disposition')
        if type(item['duplicate_targets']) is not list or len(item['duplicate_targets']) > 61 or len(set(item['duplicate_targets'])) != len(item['duplicate_targets']):
            raise FreshReviewError(INVALID)
        for target in item['duplicate_targets']:
            _digest(target)
        supersedes = item['supersedes_attempt_id']
        if supersedes != absent('no-superseded-attempt'):
            _digest(supersedes)
            pairs = [a for a in row.get('attempts', ()) if a['attempt_id'] == supersedes and 'intent' not in a
                and a['status'] == 'blocked' and a['terminal']['reason'] == 'disposition-requested'
                and (a['terminal']['operation_id'], a['terminal']['fencing_epoch']) == (item['operation_id'], item['fencing_epoch'])]
            if len(pairs) != 1:
                raise FreshReviewError('disposition-supersedes-invalid')
    for attempt in row.get('attempts', ()):
        if attempt['terminal'].get('reason') == 'disposition-requested' and sum(
                item['supersedes_attempt_id'] == attempt['attempt_id'] for item in values) != 1:
            raise FreshReviewError('disposition-supersedes-invalid')
    if row['lifecycle'] != lifecycle(row):
        raise FreshReviewError(INVALID)



def validate_recovery(item):
    """A bounded diagnostic slot never authorizes stopping or resolution."""
    value = item.get('recovery', absent('not-observed'))
    if value == absent('not-observed'):
        return
    _closed(value, ('operation_id', 'fencing_epoch', 'dispatch_operation_id',
        'dispatch_fencing_epoch', 'observed_at', 'status'), INVALID)
    if item['stage'] == 'prepared' or item['intent'] == absent('not-dispatched'):
        raise FreshReviewError(INVALID)
    for key in ('operation_id', 'dispatch_operation_id'):
        _identifier(value[key])
    for key in ('fencing_epoch', 'dispatch_fencing_epoch'):
        _integer(value[key], INVALID)
        if value[key] < 1:
            raise FreshReviewError(INVALID)
    from .budget import _at, BudgetError
    try:
        from .fresh_review_receipts import _timestamp
        observed = _timestamp(value['observed_at'])
        started = _at(item['intent']['started_at'])
    except BudgetError as exc:
        raise FreshReviewError(INVALID) from exc
    if (value['status'] not in ('running', 'stop-unconfirmed', 'exited-result-available', 'exited-result-unavailable')
            or observed < started or value['fencing_epoch'] < item['intent']['fencing_epoch']
            or (value['dispatch_operation_id'], value['dispatch_fencing_epoch']) !=
                (item['intent']['operation_id'], item['intent']['fencing_epoch'])):
        raise FreshReviewError(INVALID)


@dataclass(frozen=True)
class DispositionArtifact:
    reference: FrozenJsonObject
    content: FrozenJsonObject


@dataclass(frozen=True)
class DispositionEvidence:
    artifacts: tuple[DispositionArtifact, ...]


def artifact_body(evidence, ref):
    if type(evidence) is not DispositionEvidence or type(evidence.artifacts) is not tuple:
        raise FreshReviewError('disposition-evidence-invalid')
    matches = []
    for artifact in evidence.artifacts:
        if type(artifact) is not DispositionArtifact or type(artifact.reference) is not FrozenJsonObject or type(artifact.content) is not FrozenJsonObject:
            raise FreshReviewError('disposition-evidence-invalid')
        if artifact.reference.thaw() == ref:
            body = artifact.content.thaw(); raw = canonical_bytes(body)
            if len(raw) != ref['size'] or canonical_digest(body) != ref['digest']:
                raise FreshReviewError('disposition-evidence-invalid')
            matches.append(body)
    if len(matches) != 1:
        raise FreshReviewError('disposition-evidence-unavailable')
    return matches[0]


def decode_scope(scope):
    from .fresh_review import _unique_strings
    _closed(scope, ('criterion_ids', 'requirement_ids', 'prohibited_side_effect_ids', 'repro_digest'), INVALID)
    for key in ('criterion_ids', 'requirement_ids', 'prohibited_side_effect_ids'):
        _unique_strings(scope[key], _identifier, nonempty=False)
    _digest(scope['repro_digest'])
    return scope


def _scope(scope, source):
    decode_scope(scope)
    return (source['criterion_id'] in scope['criterion_ids'] and scope['repro_digest'] == source['repro']['repro_digest']
        and set(source['requirement_ids']).issubset(scope['requirement_ids'])
        and set(source['prohibited_side_effect_ids']).issubset(scope['prohibited_side_effect_ids']))


def _result_output(value):
    _closed(value, ('schema', 'request_id', 'input_digest', 'verdict', 'reason_code', 'reason', 'scope', 'evidence', 'mapping_ref'), INVALID)
    if (value['schema'] != 'mission-finding-disposition-output/1' or value['verdict'] not in ('deferred', 'not-violation', 'duplicate')
            or value['reason_code'] != value['verdict'] or type(value['reason']) is not str or not value['reason']
            or type(value['evidence']) is not list or not value['evidence']):
        raise FreshReviewError(INVALID)
    _digest(value['request_id']); _digest(value['input_digest']); decode_scope(value['scope'])
    if value['mapping_ref'] != absent('no-mapping'):
        validate_ref(value['mapping_ref'], 'finding-disposition-mapping')
    if value['verdict'] == 'deferred' and value['mapping_ref'] != absent('no-mapping'):
        raise FreshReviewError(INVALID)
    for ref in value['evidence']:
        validate_source_ref(ref)
    return value


def origin_record(document, row):
    from .fresh_review import decode_projection
    identifier = row['origin']['request_id'] if row['kind'] == 'finding' else row['request_id']
    record = next((r for r in decode_projection(document).requests
        if hasattr(r, 'request') and r.request.request_id == identifier), None)
    if record is None or record.result is None:
        raise FreshReviewError('disposition-origin-mismatch')
    return record


def authenticated_finding(document, row, evidence):
    from dataclasses import asdict
    from .fresh_review_receipts import decode_terminal_receipt, CompletedFreshReview
    from .repair_lineage import _finding, origin_identity
    record = origin_record(document, row)
    terminal = decode_terminal_receipt(record.result.thaw())
    if not isinstance(terminal, CompletedFreshReview) or row['finding_ref'] not in [asdict(ref) for ref in terminal.findings]:
        raise FreshReviewError('disposition-origin-mismatch')
    source = artifact_body(evidence, row['finding_ref'])
    derived = _finding(record.request, terminal, source, row['finding_ref'], document['acceptance_contract'])
    if origin_identity(derived) != origin_identity(row):
        raise FreshReviewError('disposition-origin-mismatch')
    return derived


def validate_terminal(document, row, item, evidence, candidates):
    from acceptance_contract import canonical_contract_digest
    from .repair_lineage import origin_identity
    from .repair_reverification import full_candidate_digest
    from .fresh_review_receipts import decode_launch_receipt, ContextMode
    contract = document['acceptance_contract']
    request = decode_request(artifact_body(evidence, item['request_ref'])).document.thaw()
    if (request['request_id'] != item['request_id'] or request['lineage_id'] != row['lineage_id']
            or request['operation_id'] != item['operation_id'] or request['supersedes_attempt_id'] != item['supersedes_attempt_id']
            or request['origin_digest'] != canonical_digest(origin_identity(row))
            or request['full_candidate_digest'] != item['full_candidate_digest']
            or request['contract_digest'] != canonical_contract_digest(contract)
            or request['requirement_digest'] != contract['requirement_digest']
            or request['verifier_policy_digest'] != contract['verifier_policy']['digest']
            or full_candidate_digest(contract, candidates) != request['full_candidate_digest']
            or row['last_candidate_change'].get('generation', 0) >= item['generation']):
        raise FreshReviewError('disposition-stale')
    record = origin_record(document, row)
    if (request['mission_id'], request['session_id']) != (record.request.mission_id, record.request.session_id):
        raise FreshReviewError('disposition-origin-mismatch')
    artifact_body(evidence, request['input_ref'])
    source = artifact_body(evidence, request['source_ref'])
    if row['kind'] == 'finding':
        authenticated_finding(document, row, evidence)
        expected_source = row['finding_ref']
    else:
        from dataclasses import asdict
        from .fresh_review_receipts import decode_terminal_receipt, FailedFreshReview
        origin = decode_terminal_receipt(record.result.thaw())
        if (not isinstance(origin, FailedFreshReview) or origin.reason != 'output-over-import-limit'
                or origin.output_digest != row['output_digest']):
            raise FreshReviewError('disposition-origin-mismatch')
        expected_source = asdict(origin.output_ref)
    if request['source_ref'] != expected_source:
        raise FreshReviewError('disposition-origin-mismatch')
    terminal = artifact_body(evidence, item['terminal']['ref'])
    _closed(terminal, ('schema', 'request_id', 'request_ref', 'launch_ref', 'output_ref', 'mapping_ref',
        'dispatch', 'operation_id', 'fencing_epoch', 'generation', 'outcome', 'reason_code'), INVALID)
    for key in ('fencing_epoch', 'generation'):
        _integer(terminal[key], INVALID)
    if (terminal['schema'] != 'mission-finding-disposition/1' or terminal['request_id'] != item['request_id']
            or terminal['request_ref'] != item['request_ref'] or terminal['launch_ref'] != item['launch_ref']
            or terminal['dispatch'] != item['intent'] or terminal['generation'] <= item['generation']
            or terminal['fencing_epoch'] < item['intent']['fencing_epoch']
            or any(terminal[key] != item['terminal'][key] for key in ('operation_id', 'fencing_epoch', 'generation', 'outcome', 'reason_code'))):
        raise FreshReviewError('disposition-terminal-mismatch')
    if terminal['outcome'] not in ('rejected', 'deferred'):
        raise FreshReviewError('disposition-unresolved')
    launch = decode_launch_receipt(artifact_body(evidence, terminal['launch_ref']))
    intent = item['intent']
    from .budget import _at
    if (launch.request_id != request['request_id'] or launch.request_digest != canonical_digest(request)
            or launch.nonce != request['request_id'][7:39] or launch.operation_id != intent['operation_id']
            or launch.fencing_epoch != intent['fencing_epoch'] or launch.parent_identity != intent['parent_identity']
            or launch.adapter_registration_digest != request['adapter_registration_digest']
            or launch.received_input_digest != request['input_ref']['digest']
            or launch.context_mode != ContextMode.FRESH or launch.child_identity == launch.parent_identity
            or launch.context_identity in (launch.parent_identity, launch.child_identity)
            or not _at(intent['started_at']) <= datetime.fromisoformat(launch.started_at.replace('Z', '+00:00')) < _at(intent['deadline_at'])
            or launch.enforced_budget.wall_time_sec > request['wall_time_sec']
            or launch.enforced_budget.max_packet_bytes > request['max_packet_bytes']
            or launch.enforced_budget.max_output_bytes > request['max_output_bytes']):
        raise FreshReviewError('disposition-not-independent')
    validate_ref(request['input_ref'], 'finding-disposition-input',
        min(1048576, request['max_packet_bytes'], launch.enforced_budget.max_packet_bytes))
    validate_ref(terminal['output_ref'], 'finding-disposition-output',
        min(262144, request['max_output_bytes'], launch.enforced_budget.max_output_bytes))
    output = _result_output(artifact_body(evidence, terminal['output_ref']))
    if (output['request_id'] != request['request_id'] or output['input_digest'] != request['input_ref']['digest']
            or output['mapping_ref'] != terminal['mapping_ref'] or output['reason_code'] != terminal['reason_code']
            or terminal['outcome'] != ('deferred' if output['verdict'] == 'deferred' else 'rejected')):
        raise FreshReviewError('disposition-output-mismatch')
    for ref in output['evidence']:
        artifact_body(evidence, ref)
    if output['verdict'] == 'deferred':
        return 'deferred', ()
    if row['kind'] == 'finding' and output['verdict'] == 'not-violation':
        if not _scope(output['scope'], row) or terminal['mapping_ref'] != absent('no-mapping'):
            raise FreshReviewError('disposition-scope-invalid')
        return 'rejected', ()
    mapping = artifact_body(evidence, terminal['mapping_ref'])
    return 'rejected', validate_mapping(document, row, source, mapping, output, evidence)


def validate_mapping(document, row, source, mapping, output, evidence):
    from .repair_lineage import _finding
    from .fresh_review_receipts import decode_terminal_receipt
    from .fresh_review_output import _hypothesis
    _closed(mapping, ('schema', 'source_digest', 'entries'), INVALID)
    if (mapping['schema'] != 'mission-finding-disposition-mapping/1' or mapping['source_digest'] != canonical_digest(source)
            or len(canonical_bytes(mapping)) > 262144 or type(mapping['entries']) is not list):
        raise FreshReviewError('disposition-mapping-invalid')
    sources = [source] if row['kind'] == 'finding' else [finding for criterion in source['criterion_results'] for finding in criterion['findings']]
    if not sources or len(mapping['entries']) != len(sources):
        raise FreshReviewError('disposition-mapping-incomplete')
    rows = {r['lineage_id']: r for r in document['repair_lineage']['lineages']}
    edges = []
    for index, (finding, entry) in enumerate(zip(sources, mapping['entries'])):
        _closed(entry, ('index', 'element_digest', 'verdict', 'target', 'scope'), INVALID)
        if type(entry['index']) is not int or entry['index'] != index or entry['element_digest'] != canonical_digest(finding):
            raise FreshReviewError('disposition-mapping-incomplete')
        if row['kind'] == 'finding':
            origin = authenticated_finding(document, row, evidence)
            if source != artifact_body(evidence, row['finding_ref']):
                raise FreshReviewError('disposition-origin-mismatch')
        else:
            record = origin_record(document, row)
            _hypothesis(finding, finding['criterion_id'])
            # Scope derivation grants no executed replay or resolution authority.
            unpublished = dict(finding, replay=None, reason_code='replay-unavailable')
            origin = _finding(record.request, decode_terminal_receipt(record.result.thaw()), unpublished,
                reference('fresh-review-finding', canonical_bytes(finding)), document['acceptance_contract'])
        if not _scope(entry['scope'], origin):
            raise FreshReviewError('disposition-scope-invalid')
        if entry['verdict'] == 'not-violation' and entry['target'] == absent('no-target'):
            continue
        target = rows.get(entry['target']) if type(entry['target']) is str else None
        if (entry['verdict'] != 'duplicate' or target is None or target['kind'] != 'finding'
                or target['lineage_id'] == row['lineage_id']):
            raise FreshReviewError('disposition-alias-invalid')
        target = authenticated_finding(document, target, evidence)
        if not duplicate_scope(origin, target):
            raise FreshReviewError('disposition-alias-invalid')
        edges.append(target['lineage_id'])
    targets = tuple(sorted(set(edges)))
    if len(targets) > 61:
        raise FreshReviewError('disposition-mapping-over-limit')
    return targets



def resolved_aliases(edges, resolved, known):
    """Validate the whole alias graph before evaluating its canonical targets."""
    if type(edges) is not dict or type(resolved) is not set or type(known) is not set:
        raise FreshReviewError('disposition-alias-invalid')
    for source, targets in edges.items():
        _digest(source)
        if source not in known or type(targets) is not tuple or not targets or len(targets) > 61 or len(set(targets)) != len(targets):
            raise FreshReviewError('disposition-alias-invalid')
        for target in targets:
            _digest(target)
            if target not in known:
                raise FreshReviewError('disposition-alias-invalid')
    checked, active = set(), set()
    def visit(source):
        if source in active:
            raise FreshReviewError('disposition-alias-invalid')
        if source in checked:
            return
        active.add(source)
        for target in edges.get(source, ()):
            visit(target)
        active.remove(source); checked.add(source)
    for source in edges:
        visit(source)
    memo = {}
    def complete(source):
        if source not in memo:
            memo[source] = source in resolved or source in edges and all(complete(t) for t in edges[source])
        return memo[source]
    return {source for source in edges if complete(source)}


def validate_disposition_evidence(document, evidence, candidates):
    """Check latest proofs and alias closure without declaring any row resolved.

    Revalidate after construction: the carrier contains original evidence,
    never a cached verdict. Resolution against current D/E2 obligations belongs
    to the consumer, including target receipts and full candidate observation.
    """
    from .repair_lineage import decode_projection
    if type(evidence) is not DispositionEvidence or type(evidence.artifacts) is not tuple:
        raise FreshReviewError('disposition-evidence-invalid')
    try:
        rows = [row.document.thaw() for row in decode_projection(document).lineages]
        edges = {}
        for row in rows:
            if not row.get('dispositions'):
                continue
            item = row['dispositions'][-1]
            if item['stage'] != 'terminal' or item['terminal']['outcome'] != 'rejected':
                raise FreshReviewError('disposition-unresolved')
            outcome, targets = validate_terminal(document, row, item, evidence, candidates)
            if outcome != 'rejected':
                raise FreshReviewError('disposition-unresolved')
            if list(targets) != item['duplicate_targets']:
                raise FreshReviewError('disposition-alias-invalid')
            if targets:
                edges[row['lineage_id']] = targets
        resolved_aliases(edges, set(), {row['lineage_id'] for row in rows})
    except FreshReviewError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        raise FreshReviewError('disposition-evidence-invalid') from exc
