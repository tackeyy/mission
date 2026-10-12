"""Internal disposition preparation; execution and resolution remain disabled."""
from acceptance_contract import canonical_contract_digest
from .commands import FreshReviewInputEffectClaim
from .finding_disposition import decode_request, origin_record
from .fresh_review import FreshReviewError, canonical_bytes, canonical_digest
from .model import FencedLease, FrozenJsonObject
from .repair_attempts import _document, _store, absent, reference, terminal
from .repair_lineage import import_origins, origin_identity
from .repair_reverification import full_candidate_digest, next_generation


def prepare_disposition(state, command):
    if (type(state.lease) is not FencedLease or type(command.fencing_epoch) is not int
            or command.fencing_epoch != state.lease.fencing_epoch):
        raise FreshReviewError('repair-lineage-stale-fence')
    if any(type(value) is not FrozenJsonObject for value in
            (command.request, command.packet, command.observed_candidates)):
        raise FreshReviewError('disposition-invalid')
    body = decode_request(command.request.thaw()).document.thaw()
    state = import_origins(state, command.evidence)
    document = _document(state)
    matches = [r for r in document['repair_lineage']['lineages'] if r['lineage_id'] == body['lineage_id']]
    if len(matches) != 1:
        raise FreshReviewError('disposition-origin-mismatch')
    row = matches[0]
    record = origin_record(document, row)
    contract = document['acceptance_contract']
    source = row['finding_ref'] if row['kind'] == 'finding' else record.result.thaw()['output_ref']
    if (body['origin_digest'] != canonical_digest(origin_identity(row)) or body['source_ref'] != source
            or (body['mission_id'], body['session_id']) != (record.request.mission_id, record.request.session_id)):
        raise FreshReviewError('disposition-origin-mismatch')
    if (body['contract_digest'] != canonical_contract_digest(contract)
            or body['requirement_digest'] != contract['requirement_digest']
            or body['verifier_policy_digest'] != contract['verifier_policy']['digest']
            or body['full_candidate_digest'] != full_candidate_digest(contract, command.observed_candidates.thaw())):
        raise FreshReviewError('disposition-stale')
    marker = row['last_candidate_change'].get('generation', 0)
    if row['lifecycle'] == 'verified':
        latest = row['attempts'][-1]
        snapshots = command.observed_candidates.thaw()
        selected = {key: snapshots[key] for key in row['introduced_candidate']['snapshots']}
        if (canonical_digest(selected) == latest['intent']['candidate_map_digest']
                and marker < latest['terminal']['generation']):
            raise FreshReviewError('repair-lineage-already-verified')
    if row['lifecycle'] == 'rejected':
        latest = row['dispositions'][-1]
        if latest['full_candidate_digest'] == body['full_candidate_digest'] and marker < latest['generation']:
            raise FreshReviewError('disposition-already-rejected')
    contents = (canonical_bytes(body), canonical_bytes(command.packet.thaw()))
    refs = (reference('finding-disposition-request', contents[0]), body['input_ref'])
    if type(command.effects) is not tuple or len(command.effects) != 2:
        raise FreshReviewError('disposition-effect-invalid')
    for claim, ref, content, limit in zip(command.effects, refs, contents, (262144, 1048576)):
        if (type(claim) is not FreshReviewInputEffectClaim or type(claim.size) is not int
                or not 1 <= len(content) <= limit or ref != reference(ref['kind'], content)
                or (claim.kind, claim.target, claim.digest, claim.size) !=
                    (ref['kind'], ref['relative_path'], ref['digest'], ref['size'])):
            raise FreshReviewError('disposition-effect-invalid')
    items = row.setdefault('dispositions', [])
    if any(item['operation_id'] == body['operation_id'] for item in items):
        raise FreshReviewError('disposition-operation-conflict')
    if items and items[-1]['stage'] != 'terminal':
        raise FreshReviewError('disposition-pending')
    pending = [a for a in row.get('attempts', ()) if a['status'] == 'pending']
    supersedes = body['supersedes_attempt_id']
    if pending:
        attempt = pending[-1]
        if ('intent' in attempt or supersedes != attempt['attempt_id']
                or command.fencing_epoch < attempt['fencing_epoch']):
            raise FreshReviewError('disposition-supersedes-invalid')
        end = terminal(attempt['attempt_id'], 'blocked', 'disposition-requested', command.fencing_epoch)
        end['operation_id'] = body['operation_id']
        attempt.update(status='blocked', terminal=end)
    elif supersedes != absent('no-superseded-attempt'):
        raise FreshReviewError('disposition-supersedes-invalid')
    row['dispositions'].append(dict(request_id=body['request_id'], request_ref=refs[0],
        full_candidate_digest=body['full_candidate_digest'], supersedes_attempt_id=body['supersedes_attempt_id'],
        operation_id=body['operation_id'], fencing_epoch=command.fencing_epoch, generation=next_generation(document),
        stage='prepared', intent=absent('not-dispatched'), launch_ref=absent('not-launched'),
        terminal=absent('not-terminal'), duplicate_targets=[], recovery=absent('not-observed')))
    row['lifecycle'] = 'open'
    return _store(state, document)
