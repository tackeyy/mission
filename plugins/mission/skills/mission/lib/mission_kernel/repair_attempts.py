"""Bounded repair obligations. E2a never issues a resolution receipt."""
from dataclasses import replace
import hashlib
import json

from .fresh_review import FreshReviewError, canonical_bytes, canonical_digest, _closed, _digest, _identifier, _integer
from .json_codec import freeze_json_value
from .model import FencedLease, FrozenJsonObject

INVALID = 'repair-attempt-invalid'
TERMINAL_REASONS = frozenset(('publication-result-lost', 'effects-unavailable', 'replay-failed'))


def absent(reason):
    return dict(kind='absent', reason=reason)


def reference(kind, content):
    digest = 'sha256:' + hashlib.sha256(content).hexdigest()
    return dict(kind=kind, relative_path='evidence/' + kind + '/' + digest[7:] + '.json', digest=digest, size=len(content))


def _reference(value, kind):
    _closed(value, ('kind', 'relative_path', 'digest', 'size'), INVALID)
    _digest(value['digest']); _integer(value['size'], INVALID)
    if (value['kind'] != kind or value['relative_path'] != 'evidence/' + kind + '/' + value['digest'][7:] + '.json'
            or not 1 <= value['size'] <= 262144):
        raise FreshReviewError(INVALID)


def attempt_id(lineage, operation):
    return canonical_digest(dict(domain='mission-repair-attempt/1', lineage_id=lineage, operation_id=operation))


def terminal_operation(identifier, status, reason):
    return 'repair-terminal:' + canonical_digest(dict(domain='mission-repair-terminal/1',
        attempt_id=identifier, outcome=status, result_digest=canonical_digest(dict(reason=reason))))[7:]


def terminal(identifier, status, reason, epoch):
    return dict(outcome=status, reason=reason, operation_id=terminal_operation(identifier, status, reason),
        fencing_epoch=epoch, event_ref=absent('events-not-enabled'), comparison_ref=absent('comparison-not-enabled'))


def validate_attempts(row):
    attempts = row['attempts']
    if type(attempts) is not list:
        raise FreshReviewError(INVALID)
    seen = set()
    for index, item in enumerate(attempts):
        _closed(item, ('attempt_id', 'operation_id', 'fencing_epoch', 'history_ref', 'status', 'terminal'), INVALID)
        _digest(item['attempt_id']); _identifier(item['operation_id']); _integer(item['fencing_epoch'], INVALID)
        _reference(item['history_ref'], 'repair-attempt')
        if item['attempt_id'] != attempt_id(row['lineage_id'], item['operation_id']) or item['attempt_id'] in seen:
            raise FreshReviewError(INVALID)
        seen.add(item['attempt_id'])
        if item['status'] == 'pending':
            if index != len(attempts) - 1 or item['terminal'] != absent('not-terminal'):
                raise FreshReviewError(INVALID)
        elif item['status'] in ('failed', 'blocked'):
            end = _closed(item['terminal'], ('outcome', 'reason', 'operation_id', 'fencing_epoch', 'event_ref', 'comparison_ref'), INVALID)
            _integer(end['fencing_epoch'], INVALID)
            if end['fencing_epoch'] < item['fencing_epoch']:
                raise FreshReviewError(INVALID)
            if end['reason'] not in TERMINAL_REASONS or (item['status'] == 'failed') != (end['reason'] == 'replay-failed'):
                raise FreshReviewError(INVALID)
            if end != terminal(item['attempt_id'], item['status'], end['reason'], end['fencing_epoch']):
                raise FreshReviewError(INVALID)
        else:
            raise FreshReviewError(INVALID)
    if row['lifecycle'] != ('repairing' if attempts and attempts[-1]['status'] == 'pending' else 'open'):
        raise FreshReviewError(INVALID)


def maximum_attempt():
    identifier = 'sha256:' + 'f' * 64
    row = dict(attempt_id=identifier, operation_id='x' * 128, fencing_epoch=2**63 - 1,
        history_ref=reference('repair-attempt', b'x'), status='blocked',
        terminal=terminal(identifier, 'blocked', 'publication-result-lost', 2**63 - 1))
    row['history_ref'].update(size=262144, digest=identifier,
        relative_path='evidence/repair-attempt/' + 'f' * 64 + '.json')
    return row


# Full maximum summary, including the deepest supported pretty-printed layout,
# is an upper bound on replacing its smaller pending predecessor. No payload
# body or candidate map is charged here: they live in immutable effects.
_MAXIMUM_ENVELOPE = dict(extensions=dict(repair_lineage=dict(lineages=[dict(attempts=[maximum_attempt()])])))
REPAIR_TERMINAL_DELTA = len(json.dumps(_MAXIMUM_ENVELOPE, ensure_ascii=False, sort_keys=True, indent=2).encode())


def _document(state):
    return state.legacy_passthrough.thaw() if state.legacy_passthrough is not None else state.extensions.thaw()


def find_attempt(document, identifier):
    from .repair_lineage import decode_projection
    matches = []
    for line in decode_projection(document).lineages:
        row = line.document.thaw()
        matches.extend((row, item) for item in row.get('attempts', ()) if item['attempt_id'] == identifier)
    if len(matches) != 1:
        raise FreshReviewError('repair-attempt-unavailable')
    return matches[0]


def _store(state, document):
    from .repair_lineage import decode_projection
    projection = decode_projection(document)
    key = 'legacy_passthrough' if state.legacy_passthrough is not None else 'extensions'
    return replace(state, repair=projection, snapshot_provenance=None, **{key: freeze_json_value(document)})


def baseline_receipts(document, row):
    # Retain the latest normal and same-counterexample observations, rather
    # than copying an ever-growing receipt history into every attempt.
    latest = {}
    for receipt in document.get('verification_receipts', []):
        if isinstance(receipt, dict) and receipt.get('criterion_id') == row['criterion_id']:
            repro = receipt.get('repro_input_digest')
            if repro in (None, row['repro']['repro_input_digest']):
                latest[repro or 'normal'] = receipt
    return [dict(criterion_id=row['criterion_id'], repro_input_digest=latest[key].get('repro_input_digest'),
        receipt_digest=canonical_digest(latest[key])) for key in sorted(latest)]


def mutate_attempt(state, command):
    from .commands import BeginFindingRepair, FreshReviewInputEffectClaim
    from .repair_lineage import import_origins, decode_projection
    if not isinstance(state.lease, FencedLease):
        raise FreshReviewError('repair-lineage-stale-fence')
    epoch = state.lease.fencing_epoch
    if isinstance(command, BeginFindingRepair):
        state = import_origins(state, command.evidence)
        document = _document(state)
        rows = [r.document.thaw() for r in decode_projection(document).lineages if r.document.thaw()['lineage_id'] == command.lineage_id]
        if len(rows) != 1 or rows[0]['kind'] != 'finding':
            raise FreshReviewError('repair-replay-unsupported')
        row = rows[0]
        _identifier(command.operation_id)
        identifier = attempt_id(command.lineage_id, command.operation_id)
        old = next((a for a in row['attempts'] if a['attempt_id'] == identifier), None)
        if old is not None:
            if command.history is not None and old['history_ref'] != reference('repair-attempt', canonical_bytes(command.history.thaw())):
                raise FreshReviewError('repair-operation-conflict')
            return state
        if row['attempts'] and row['attempts'][-1]['status'] == 'pending':
            raise FreshReviewError('repair-attempt-pending')
        if type(command.history) is not FrozenJsonObject:
            raise FreshReviewError(INVALID)
        body = _closed(command.history.thaw(), ('schema', 'lineage_id', 'attempt_id', 'before_candidate',
            'plan_ref', 'repro_input_ref', 'repro_input_digest', 'baseline_receipts'), INVALID)
        finding = next(f.thaw() for e in command.evidence for f in e.findings if canonical_digest(f.thaw()) == row['finding_ref']['digest'])
        repro = finding['repro_input']
        if (body['schema'] != 'mission-repair-attempt/1' or body['lineage_id'] != command.lineage_id
                or body['attempt_id'] != identifier or body['repro_input_digest'] != row['repro']['repro_input_digest']
                or canonical_digest(repro) != row['repro']['repro_input_digest']):
            raise FreshReviewError(INVALID)
        from acceptance_contract import canonical_contract_digest
        candidate = _closed(body['before_candidate'], ('snapshots', 'contract_digest', 'requirement_digest', 'verifier_policy_digest', 'iteration'), INVALID)
        contract = document['acceptance_contract']
        if (candidate['contract_digest'] != canonical_contract_digest(contract)
                or candidate['requirement_digest'] != contract['requirement_digest']
                or candidate['verifier_policy_digest'] != contract['verifier_policy']['digest']
                or candidate['iteration'] != state.control.iteration):
            raise FreshReviewError('repair-contract-stale')
        _integer(candidate['iteration'], INVALID)
        # Snapshot values are application observations of the filesystem.
        # Kernel state has historical bindings, not a current capture; only
        # snapshot keys/format are checked here. E2b reverify must not use
        # before_candidate as authority for a failed/passed replay.
        if type(candidate['snapshots']) is not dict or set(candidate['snapshots']) != set(row['introduced_candidate']['snapshots']):
            raise FreshReviewError(INVALID)
        for value in candidate['snapshots'].values():
            _digest(value)
        if type(body['baseline_receipts']) is not list or body['baseline_receipts'] != baseline_receipts(document, row):
            raise FreshReviewError(INVALID)
        _reference(body['plan_ref'], 'repair-plan'); _reference(body['repro_input_ref'], 'repair-repro')
        contents = (canonical_bytes(body), command.plan.encode('utf-8'), canonical_bytes(repro))
        refs = (reference('repair-attempt', contents[0]), body['plan_ref'], body['repro_input_ref'])
        for claim, ref, content in zip((command.history_effect, command.plan_effect, command.repro_effect), refs, contents):
            if type(claim) is not FreshReviewInputEffectClaim or type(claim.size) is not int:
                raise FreshReviewError('repair-effect-invalid')
            if ref != reference(ref['kind'], content) or not 1 <= len(content) <= 262144 or (
                    claim.kind, claim.target, claim.digest, claim.size) != (ref['kind'], ref['relative_path'], ref['digest'], ref['size']):
                raise FreshReviewError('repair-effect-invalid')
        item = dict(attempt_id=identifier, operation_id=command.operation_id, fencing_epoch=epoch,
            history_ref=refs[0], status='pending', terminal=absent('not-terminal'))
        row['attempts'].append(item); row['lifecycle'] = 'repairing'
    else:
        document = _document(state)
        row, item = find_attempt(document, command.attempt_id)
        if command.reason not in ('publication-result-lost', 'effects-unavailable'):
            raise FreshReviewError(INVALID)
        expected = terminal_operation(command.attempt_id, 'blocked', command.reason)
        if command.operation_id != expected:
            raise FreshReviewError('repair-operation-conflict')
        if epoch < item['fencing_epoch']:
            raise FreshReviewError('repair-lineage-stale-fence')
        if item['status'] != 'pending':
            return state
        item.update(status='blocked', terminal=terminal(command.attempt_id, 'blocked', command.reason, epoch))
        row['lifecycle'] = 'open'
    surface = document['repair_lineage']['lineages']
    surface[next(i for i, r in enumerate(surface) if r['lineage_id'] == row['lineage_id'])] = row
    return _store(state, document)
