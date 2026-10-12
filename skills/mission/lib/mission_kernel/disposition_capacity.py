"""Maximum closed summary shapes, including the retained diagnostic slot."""
import json
from .repair_attempts import reference
from .finding_disposition import BLOCKED_REASONS


def maximum_disposition():
    digest = 'sha256:' + 'f' * 64
    integer = 2**63 - 1
    def ref(kind):
        return dict(reference(kind, b'x'), size=262144, digest=digest,
            relative_path='evidence/' + kind + '/' + digest[7:] + '.json')
    dispatch = dict(operation_id='disposition-dispatch:' + digest[7:], fencing_epoch=integer,
        reservation_id='x' * 128, budget_class='repair', parent_identity='x' * 128,
        started_at='9999-01-01T00:00:00Z', deadline_at='9999-12-31T23:59:59Z')
    return dict(request_id=digest, request_ref=ref('finding-disposition-request'), full_candidate_digest=digest,
        supersedes_attempt_id=digest, operation_id='x' * 128, fencing_epoch=integer, generation=integer-1,
        stage='terminal', intent=dispatch, launch_ref=ref('finding-disposition-launch'),
        terminal=dict(ref=ref('finding-disposition'), operation_id='x' * 128, fencing_epoch=integer,
            generation=integer, outcome='blocked', reason_code=max(BLOCKED_REASONS, key=len)),
        duplicate_targets=['sha256:' + format(i, '064x') for i in range(61)],
        recovery=dict(operation_id='x' * 128, fencing_epoch=integer, dispatch_operation_id=dispatch['operation_id'],
            dispatch_fencing_epoch=integer, observed_at='9999-12-31T23:59:59.999999Z',
            status='exited-result-unavailable'))


def disposition_stage_remaining(item, *, pretty):
    # Same supported envelope on both sides makes the remaining reservation
    # shrink as a pending summary grows. IDs are ASCII-only; refs and enums
    # are closed, so no unbounded escaped/free text enters this image.
    def encode(value):
        envelope = dict(extensions=dict(repair_lineage=dict(lineages=[dict(dispositions=[value])])))
        options = {'indent': 2} if pretty else {'separators': (',', ':')}
        return len(json.dumps(envelope, ensure_ascii=False, sort_keys=True, **options).encode('utf-8'))
    return max(0, encode(maximum_disposition()) - encode(item)) + 16
