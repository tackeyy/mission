"""Closed, immutable D-origin projection. E1 grants no resolution authority."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace, asdict

from .fresh_review import (FreshReviewError, FreshReviewRecord, canonical_digest,
    decode_projection as decode_reviews, _closed, _identifier, _digest, _integer,
    _unique_strings)
from .fresh_review_receipts import decode_terminal_receipt, CompletedFreshReview, FailedFreshReview, receipt_document
from .json_codec import freeze_json_value
from .model import FrozenJsonObject

SCHEMA = 'mission-repair-lineage/1'
SHAPE = 'repair-lineage-shape-invalid'
MISMATCH = 'repair-lineage-origin-mismatch'


@dataclass(frozen=True)
class FindingLineage:
    document: FrozenJsonObject


@dataclass(frozen=True)
class RepairProjection:
    lineages: tuple[FindingLineage, ...] = ()


def lineage_id(origin, criterion_id):
    return canonical_digest(dict(domain='mission-repair-finding/1',
        mission_id=origin['mission_id'], session_id=origin['session_id'],
        request_id=origin['request_id'], finding_id=origin['finding_id'], criterion_id=criterion_id))


def _overflow_id(request, output_digest):
    return canonical_digest(dict(domain='mission-repair-unimported/1', mission_id=request.mission_id,
        session_id=request.session_id, request_id=request.request_id, output_digest=output_digest))


def require_completion_contract(document, *, reviews=None, repair=None):
    """A missing contract cannot erase retained origin obligations."""
    if 'acceptance_contract' in document:
        return
    if 'repair_lineage' in document or repair is not None and repair.lineages:
        raise FreshReviewError('acceptance-contract-missing')
    # Non-projection legacy diagnostics have no origins. Wire shape validation
    # remains with the authoritative reader and state codecs.
    if reviews is None and not isinstance(document.get('fresh_review'), dict):
        return
    reviews = decode_reviews(document) if reviews is None else reviews
    for record in reviews.requests:
        if not isinstance(record, FreshReviewRecord) or record.result is None:
            continue
        terminal = decode_terminal_receipt(record.result.thaw())
        if (isinstance(terminal, CompletedFreshReview) and terminal.findings
                or isinstance(terminal, FailedFreshReview) and terminal.reason == 'output-over-import-limit'):
            raise FreshReviewError('acceptance-contract-missing')


def projection_document(projection):
    return dict(schema=SCHEMA, lineages=[row.document.thaw() for row in projection.lineages])


def origin_request_id(row):
    return row['origin']['request_id'] if row['kind'] == 'finding' else row['request_id']


def _absent(reason):
    return dict(kind='absent', reason=reason)


def _ref(value, expected_kind):
    from .fresh_review_receipts import _reference
    return _reference(value, expected_kind)


def decode_projection(document):
    """Only absence is empty; neither corruption nor future lifecycle is success."""
    if 'repair_lineage' not in document:
        return RepairProjection()
    try:
        raw = _closed(document['repair_lineage'], ('schema', 'lineages'), SHAPE)
        if raw['schema'] != SCHEMA:
            raise FreshReviewError('repair-lineage-schema-invalid')
        if type(raw['lineages']) is not list:
            raise FreshReviewError(SHAPE)
        rows, seen = [], set()
        for row in raw['lineages']:
            common = ('lineage_id', 'kind', 'output_digest', 'lifecycle', 'last_candidate_change')
            fields = ('criterion_id', 'requirement_ids', 'prohibited_side_effect_ids', 'severity',
                'origin', 'terminal_digest', 'finding_ref', 'replay', 'repro', 'introduced_candidate',
                'observations', 'attempts') if isinstance(row, dict) and row.get('kind') == 'finding' else ('request_id', 'disposition')
            _closed(row, (*common, *fields), SHAPE)
            for key in ('lineage_id', 'output_digest'):
                _digest(row[key])
            if row['lineage_id'] in seen:
                raise FreshReviewError('repair-lineage-duplicate-id')
            seen.add(row['lineage_id'])
            # E2/E3 will add authenticated resolution variants. Authored status
            # cannot grant a successful replay or independent disposition in E1.
            if row['lifecycle'] != 'open':
                raise FreshReviewError('repair-lineage-lifecycle-invalid')
            marker = row['last_candidate_change']
            if marker != _absent('no-resolution-binding'):
                _closed(marker, ('generation', 'candidate_map_digest', 'operation_id'), SHAPE)
                _integer(marker['generation'], SHAPE); _digest(marker['candidate_map_digest']); _identifier(marker['operation_id'])
            if row['kind'] == 'finding':
                _identifier(row['criterion_id'])
                for key in ('requirement_ids', 'prohibited_side_effect_ids'):
                    _unique_strings(row[key], _identifier, nonempty=False)
                if row['severity'] not in ('High', 'Medium', 'Low') or row['observations'] != [] or row['attempts'] != []:
                    raise FreshReviewError(SHAPE)
                origin = _closed(row['origin'], ('mission_id', 'session_id', 'request_id', 'finding_id'), SHAPE)
                for value in origin.values():
                    _identifier(value)
                if row['lineage_id'] != lineage_id(origin, row['criterion_id']):
                    raise FreshReviewError(MISMATCH)
                _digest(row['terminal_digest']); _ref(row['finding_ref'], 'fresh-review-finding')
                replay = row['replay']
                if replay.get('kind') == 'absent':
                    _closed(replay, ('kind', 'reason'), SHAPE); _identifier(replay['reason'])
                else:
                    _closed(replay, ('digest', 'evidence_ref'), SHAPE)
                    _digest(replay['digest']); _digest(replay['evidence_ref'])
                    if replay['evidence_ref'] != row['finding_ref']['digest']:
                        raise FreshReviewError(MISMATCH)
                repro = _closed(row['repro'], ('command_id', 'verifier_definition_digest', 'repro_input_ref',
                    'repro_input_digest', 'repro_digest', 'runner_repro_digest'), SHAPE)
                _identifier(repro['command_id'])
                for key in ('verifier_definition_digest', 'runner_repro_digest'):
                    absent = (_absent('replay-unregistered'), _absent('replay-not-materialized')) if key == 'runner_repro_digest' else (_absent('replay-unregistered'),)
                    if repro[key] not in absent:
                        _digest(repro[key])
                _digest(repro['repro_input_digest']); _digest(repro['repro_digest'])
                if (repro['repro_input_ref'] != row['finding_ref']['digest'] or repro['repro_digest'] != canonical_digest(dict(
                        domain='mission-repair-repro/1', command_id=repro['command_id'], repro_input_digest=repro['repro_input_digest']))):
                    raise FreshReviewError(MISMATCH)
                candidate = _closed(row['introduced_candidate'], ('snapshots', 'contract_digest',
                    'requirement_digest', 'verifier_policy_digest', 'iteration'), SHAPE)
                for key in ('contract_digest', 'requirement_digest', 'verifier_policy_digest'):
                    _digest(candidate[key])
                _integer(candidate['iteration'], SHAPE)
                if type(candidate['snapshots']) is not dict:
                    raise FreshReviewError(SHAPE)
                for key, value in candidate['snapshots'].items():
                    _identifier(key); _digest(value)
            elif row['kind'] != 'unimported-findings' or row['disposition'] != _absent('disposition-not-enabled'):
                raise FreshReviewError(SHAPE)
            _identifier(origin_request_id(row))
            rows.append(FindingLineage(freeze_json_value(row)))
        projection = RepairProjection(tuple(rows))
        validate_origins(document, projection)
        return projection
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        if isinstance(exc, FreshReviewError) and exc.code.startswith('repair-lineage-'):
            raise
        raise FreshReviewError(SHAPE) from exc


def validate_origins(document, projection):
    records = {r.request.request_id: r for r in decode_reviews(document).requests if isinstance(r, FreshReviewRecord)}
    order = {key: index for index, key in enumerate(records)}
    previous = (-1, -1)
    refs = set()
    for item in projection.lineages:
        row = item.document.thaw()
        record = records.get(origin_request_id(row))
        if record is None or record.result is None:
            raise FreshReviewError(MISMATCH)
        request, terminal = record.request, decode_terminal_receipt(record.result.thaw())
        if row['output_digest'] != terminal.output_digest:
            raise FreshReviewError(MISMATCH)
        index = -1 if row['kind'] == 'unimported-findings' else next((i for i, ref in enumerate(getattr(terminal, 'findings', ())) if asdict(ref) == row['finding_ref']), -1)
        position = (order[origin_request_id(row)], index)
        if position < previous:
            raise FreshReviewError('repair-lineage-order-invalid')
        previous = position
        if row['kind'] == 'unimported-findings':
            if not isinstance(terminal, FailedFreshReview) or terminal.reason != 'output-over-import-limit' or row['lineage_id'] != _overflow_id(request, terminal.output_digest):
                raise FreshReviewError(MISMATCH)
        else:
            origin = row['origin']
            ref_key = (origin_request_id(row), row['finding_ref']['digest'])
            if ref_key in refs:
                raise FreshReviewError(MISMATCH)
            refs.add(ref_key)
            if (not isinstance(terminal, CompletedFreshReview) or row['finding_ref'] not in [asdict(r) for r in terminal.findings]
                    or origin['mission_id'] != request.mission_id or origin['session_id'] != request.session_id
                    or row['criterion_id'] not in request.criterion_ids or row['terminal_digest'] != canonical_digest(record.result.thaw())
                    or row['introduced_candidate'] != _candidate(request)):
                raise FreshReviewError(MISMATCH)


def validate_projection_backing(document, projection):
    if type(projection) is not RepairProjection or decode_projection(document) != projection:
        raise FreshReviewError('repair-lineage-projection-mismatch')


def _candidate(request):
    return dict(snapshots={b.command_id: b.snapshot_digest for b in request.candidate_bindings},
        contract_digest=request.contract_digest, requirement_digest=request.requirement_digest,
        verifier_policy_digest=request.verifier_policy_digest, iteration=request.iteration)


def _common(request, terminal):
    return dict(output_digest=terminal.output_digest, lifecycle='open',
        last_candidate_change=_absent('no-resolution-binding'))


def _finding(request, terminal, finding, reference, contract):
    origin = dict(mission_id=request.mission_id, session_id=request.session_id,
        request_id=request.request_id, finding_id=finding['finding_id'])
    definition = contract.get('verifier_policy', {}).get('commands', {}).get(finding['command_id'])
    binding = next((b for b in request.candidate_bindings if b.command_id == finding['command_id'] and b.role == 'replay'), None)
    path = next((c['replay']['relative_path'] for c in contract.get('verifier_policy', {}).get('commands', {}).values()
                 if c.get('replay') and c['replay']['command_id'] == finding['command_id']), None)
    repro = finding['repro_input']
    runner_digest = _absent('replay-unregistered') if path is None else 'sha256:' + hashlib.sha256(
        (repro['artifact_kind'] + '\0' + path + '\0' + repro['content']).encode('utf-8')).hexdigest()
    if finding['replay'] is not None:
        recorded = finding['replay']['repro_input_digest']
        # B can reject before materialising input. Preserve explicit absence with
        # the blocked receipt; it is never an executed replay or resolution.
        if finding['replay']['status'] == 'blocked' and recorded is None:
            runner_digest = _absent('replay-not-materialized')
        elif recorded != runner_digest:
            raise FreshReviewError('repair-lineage-repro-mismatch')
        else:
            runner_digest = recorded
    input_digest = canonical_digest(repro)
    return dict(_common(request, terminal), lineage_id=lineage_id(origin, finding['criterion_id']), kind='finding',
        criterion_id=finding['criterion_id'], requirement_ids=finding['requirement_ids'],
        prohibited_side_effect_ids=finding['prohibited_side_effect_ids'], severity=finding['severity'], origin=origin,
        terminal_digest=canonical_digest(receipt_document(terminal)), finding_ref=reference,
        replay=_absent(finding['reason_code']) if finding['replay'] is None else dict(
            digest=canonical_digest(finding['replay']), evidence_ref=reference['digest']),
        repro=dict(command_id=finding['command_id'], verifier_definition_digest=binding.definition_digest if binding else
            canonical_digest(definition) if definition else _absent('replay-unregistered'), repro_input_ref=reference['digest'],
            repro_input_digest=input_digest, repro_digest=canonical_digest(dict(domain='mission-repair-repro/1',
                command_id=finding['command_id'], repro_input_digest=input_digest)), runner_repro_digest=runner_digest),
        introduced_candidate=_candidate(request), observations=[], attempts=[])


def origin_documents(projection, evidence, contract):
    """Authenticate all completed origins, including superseded attempts."""
    from .fresh_review_completion import FreshReviewCompletionEvidence, decode_completion_evidence
    if type(evidence) is not tuple or any(type(e) is not FreshReviewCompletionEvidence for e in evidence):
        raise FreshReviewError(MISMATCH)
    by_id = {e.request_id: e for e in evidence}
    if len(by_id) != len(evidence):
        raise FreshReviewError(MISMATCH)
    completed = {r.request.request_id for r in projection.requests if isinstance(r, FreshReviewRecord) and r.status == 'completed'}
    if set(by_id) != completed:
        raise FreshReviewError('repair-lineage-evidence-incomplete')
    rows = []
    for record in projection.requests:
        if not isinstance(record, FreshReviewRecord) or record.result is None or record.status not in ('completed', 'failed'):
            continue
        request, terminal = record.request, decode_terminal_receipt(record.result.thaw())
        if isinstance(terminal, FailedFreshReview) and terminal.reason == 'output-over-import-limit':
            rows.append(dict(_common(request, terminal), lineage_id=_overflow_id(request, terminal.output_digest),
                kind='unimported-findings', request_id=request.request_id, disposition=_absent('disposition-not-enabled')))
        elif isinstance(terminal, CompletedFreshReview):
            carrier = by_id[request.request_id]
            decoded = decode_completion_evidence(request, terminal, carrier.coverage.thaw(), tuple(f.thaw() for f in carrier.findings))
            rows.extend(_finding(request, terminal, finding.thaw(), asdict(ref), contract)
                        for finding, ref in zip(decoded.findings, terminal.findings))
    return rows


def import_origins(state, evidence):
    document = state.legacy_passthrough.thaw() if state.legacy_passthrough is not None else state.extensions.thaw()
    validate_projection_backing(document, state.repair)
    rows = origin_documents(state.fresh_review, evidence, document.get('acceptance_contract', {}))
    existing = {item.document.thaw()['lineage_id']: item.document.thaw() for item in state.repair.lineages}
    expected = {row['lineage_id']: row for row in rows}
    if any(key not in expected or value != expected[key] for key, value in existing.items()):
        raise FreshReviewError(MISMATCH)
    if not rows and 'repair_lineage' not in document:
        return state
    projection = RepairProjection(tuple(FindingLineage(freeze_json_value(row)) for row in rows))
    document['repair_lineage'] = projection_document(projection)
    validate_projection_backing(document, projection)
    key = 'legacy_passthrough' if state.legacy_passthrough is not None else 'extensions'
    return replace(state, repair=projection, snapshot_provenance=None, **{key: freeze_json_value(document)})


def _obligation_indices(contract):
    """Use the completion gate's contract boundary before origin/status lookup."""
    from acceptance_contract import POLICY_BOUND_SCHEMA, frozen_verifier_commands, validate as validate_contract
    try:
        validated = validate_contract({key: value for key, value in contract.items() if key != 'imported_at'}
            if isinstance(contract, dict) else contract)
        if validated['schema'] == POLICY_BOUND_SCHEMA:
            frozen_verifier_commands(validated)
    except (TypeError, ValueError, RecursionError) as exc:
        raise FreshReviewError('acceptance-contract-invalid') from exc
    return ({r['id']: r['classification'] for r in validated['requirements']},
            {c['id']: c for c in validated['criteria']})


def effective_unresolved_findings(projection, reviews, evidence, contract):
    requirements, criteria = _obligation_indices(contract)
    rows = origin_documents(reviews, evidence, contract)
    if type(projection) is not RepairProjection or any(type(r) is not FindingLineage or type(r.document) is not FrozenJsonObject for r in projection.lineages):
        raise FreshReviewError(SHAPE)
    existing = {r.document.thaw()['lineage_id']: r.document.thaw() for r in projection.lineages}
    if len(existing) != len(projection.lineages):
        raise FreshReviewError('repair-lineage-duplicate-id')
    expected = {r['lineage_id']: r for r in rows}
    if any(key not in expected or value != expected[key] for key, value in existing.items()):
        raise FreshReviewError(MISMATCH)
    return tuple(row['lineage_id'] for row in rows if row['kind'] == 'unimported-findings'
        or row['prohibited_side_effect_ids'] or row['criterion_id'] not in criteria
        or any(requirements.get(key, 'unknown') != 'context' for key in criteria[row['criterion_id']]['requirement_ids']))
