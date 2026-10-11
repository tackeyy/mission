"""Reread retained disposition artifacts; provide no completion or writer authority."""
from __future__ import annotations

import hashlib

from mission_kernel.finding_disposition import (
    DispositionArtifact, DispositionEvidence, decode_request, validate_ref,
    validate_dispositions, validate_disposition_evidence, validate_source_ref,
)
from mission_kernel.fresh_review import FreshReviewError, canonical_bytes, FRESH_REVIEW_EVIDENCE_MAX_BYTES
from mission_kernel.fresh_review_receipts import decode_launch_receipt
from mission_kernel.json_codec import decode_json_object, freeze_json_value
from mission_kernel.repair_attempts import absent
from mission_persistence.strict_reader import read_stable_bytes_beneath


UNAVAILABLE = 'disposition-evidence-unavailable'
INVALID = 'disposition-evidence-invalid'
LIMIT = 262144


def observe_disposition_results(document, *, root, candidates):
    """Return immutable original artifacts after validating every latest proof.

    Empty legacy histories require no observations. Any nonterminal, blocked,
    deferred, invalid, or stale latest disposition fails without older fallback.
    The returned carrier must be revalidated by its consumer; it grants no
    resolution, persisted marker, or candidate-observation authority.
    """
    artifacts = {}
    captures = {}

    def read(ref, kind, *, limit=LIMIT, source=False):
        if source:
            validate_source_ref(ref, kind)
        else:
            validate_ref(ref, kind, limit)
        path = ref['relative_path']
        if path in artifacts:
            if artifacts[path].reference.thaw() != ref:
                raise FreshReviewError(INVALID)
            return artifacts[path].content.thaw()
        try:
            capture = read_stable_bytes_beneath(root, path, limit=limit)
        except (OSError, ValueError, TypeError) as exc:
            raise FreshReviewError(UNAVAILABLE) from exc
        raw = capture.payload
        if len(raw) != ref['size'] or 'sha256:' + hashlib.sha256(raw).hexdigest() != ref['digest']:
            raise FreshReviewError(INVALID)
        try:
            content = decode_json_object(raw, limit=limit)
        except (ValueError, RecursionError) as exc:
            raise FreshReviewError(INVALID) from exc
        # Pure validators bind the same canonical bytes, not a reserialization
        # with a different digest, size, duplicate key, or numeric spelling.
        if canonical_bytes(content.thaw()) != raw:
            raise FreshReviewError(INVALID)
        captures[path] = (capture, limit)
        artifacts[path] = DispositionArtifact(freeze_json_value(ref), content)
        return content.thaw()

    try:
        from mission_kernel.repair_lineage import decode_projection
        rows = [row.document.thaw() for row in decode_projection(document).lineages]
        active = [row for row in rows if row.get('dispositions')]
        if not active:
            return DispositionEvidence(())
        for row in rows:
            if row['kind'] == 'finding':
                read(row['finding_ref'], 'fresh-review-finding', limit=FRESH_REVIEW_EVIDENCE_MAX_BYTES, source=True)
        for row in active:
            validate_dispositions(row)
            item = row['dispositions'][-1]
            if item['stage'] != 'terminal' or item['terminal']['outcome'] != 'rejected':
                raise FreshReviewError('disposition-unresolved')
            request = decode_request(read(item['request_ref'], 'finding-disposition-request')).document.thaw()
            read(request['source_ref'], 'fresh-review-finding' if row['kind'] == 'finding' else 'fresh-review-output',
                limit=FRESH_REVIEW_EVIDENCE_MAX_BYTES, source=True)
            launch = decode_launch_receipt(read(item['launch_ref'], 'finding-disposition-launch'))
            read(request['input_ref'], 'finding-disposition-input',
                limit=min(1048576, request['max_packet_bytes'], launch.enforced_budget.max_packet_bytes))
            terminal = read(item['terminal']['ref'], 'finding-disposition')
            output = read(terminal['output_ref'], 'finding-disposition-output',
                limit=min(LIMIT, request['max_output_bytes'], launch.enforced_budget.max_output_bytes))
            if terminal['mapping_ref'] != absent('no-mapping'):
                read(terminal['mapping_ref'], 'finding-disposition-mapping')
            for ref in output['evidence']:
                # D references keep their existing path contract, while newly
                # reread E3 supporting evidence has the E3 individual size cap.
                read(ref, ref['kind'], source=True)
        carrier = DispositionEvidence(tuple(artifacts.values()))
        validate_disposition_evidence(document, carrier, candidates)
        # strict_reader closes descriptors after each read. Compare the entire
        # captured read set again so a previously read path cannot be replaced
        # while a later artifact is being observed, even with identical bytes.
        for path, (capture, limit) in captures.items():
            try:
                repeated = read_stable_bytes_beneath(root, path, limit=limit)
            except (OSError, ValueError, TypeError) as exc:
                raise FreshReviewError(UNAVAILABLE) from exc
            if repeated != capture:
                raise FreshReviewError(UNAVAILABLE)
        return carrier
    except FreshReviewError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as exc:
        raise FreshReviewError(INVALID) from exc
