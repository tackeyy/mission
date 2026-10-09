"""Pure dispatch and atomic terminal/output publication bindings."""
from __future__ import annotations
from dataclasses import replace

from acceptance_contract import canonical_contract_digest
from .commands import BeginFreshReviewDispatch, RecordFreshReviewLaunch, WithdrawFreshReviewRequest, ImportFreshReviewOutput
from .fresh_review import (
    FreshReviewError, _closed, _identifier, _digest, canonical_digest, request_document,
    projection_document, _replace_record, validate_projection_backing, record_operation_ids,
    FreshReviewRecord, WithdrawnFreshReviewRecord, withdraw_request,
)
from .fresh_review_receipts import (
    decode_launch_receipt, decode_terminal_receipt, receipt_document, ContextMode,
    BlockedFreshReview, AbandonedFreshReview, FailedFreshReview, CompletedFreshReview, _integer, validate_dispatch_intent,
    FRESH_REVIEW_DISPATCH_SHAPE,
)
from .json_codec import freeze_json_value
from .model import FrozenJsonObject

DISPATCH_FIELDS = tuple(FRESH_REVIEW_DISPATCH_SHAPE)
FRESH_REVIEW_BUDGET_CLASSES = frozenset((
    'planning', 'implementation', 'verification', 'repair', 'final',
))


def reservation_id_for_operation(operation_id):
    """Derive the durable reservation identity from one dispatch operation."""
    _identifier(operation_id)
    return 'reservation:' + canonical_digest({
        'domain': 'fresh-review-dispatch', 'operation_id': operation_id,
    })[7:]


def budget_class_for_fresh_review_dispatch():
    """Verification denotes the unprotected shared pool in design 881 section 3.1.

    Planning/implementation/verification wire labels share that pool;
    repair/final denote the protected pools. D2c has no final latch yet;
    budget integration will select final for this entry after final latch.
    """
    return 'verification'


def validate_launch(request, dispatch, raw):
    launch = decode_launch_receipt(raw)
    if (launch.request_id, launch.request_digest, launch.nonce, launch.operation_id,
            launch.fencing_epoch, launch.adapter_registration_digest, launch.parent_identity,
            launch.received_input_digest) != (
            request.request_id, canonical_digest(request_document(request)), request.nonce,
            dispatch['operation_id'], dispatch['fencing_epoch'], request.adapter_registration_digest,
            dispatch['parent_identity'], request.input_digest):
        raise FreshReviewError('fresh-review-launch-binding-mismatch')
    if (not set(launch.enforced_tools).issubset(request.allowed_tools)
            or request.input_ref.size > launch.enforced_budget.max_packet_bytes
            or any(getattr(launch.enforced_budget, key) > getattr(request, key)
                   for key in launch.enforced_budget.__dataclass_fields__)):
        raise FreshReviewError('fresh-review-capability-unenforceable')
    independent = (launch.context_mode == ContextMode.FRESH
                   and launch.child_identity != launch.parent_identity
                   and launch.context_identity != launch.parent_identity)
    return launch, independent


def decode_dispatch_record(fields):
    request = fields['request']
    dispatch = validate_dispatch_intent(fields['dispatch'])
    for key in ('operation_id', 'parent_identity', 'adapter_id'):
        _identifier(dispatch[key])
    _integer(dispatch['fencing_epoch'], 'fresh-review-fence-invalid')
    if dispatch['fencing_epoch'] < 1 or (dispatch['invocation_id'], dispatch['operation_id'],
            dispatch['outbound_packet_digest'], dispatch['iteration'], dispatch['status'], dispatch['lifecycle_state']) != (
            'inv_' + canonical_digest(request.request_id)[7:39], fields['operation_id'], request.input_digest, request.iteration,
            'dispatch-unknown', 'dispatch-unknown'):
        raise FreshReviewError('fresh-review-dispatch-invalid')
    if (dispatch['reservation_id'] != reservation_id_for_operation(fields['operation_id'])
            or dispatch['budget_class'] != budget_class_for_fresh_review_dispatch()
            or dispatch['budget_class'] not in FRESH_REVIEW_BUDGET_CLASSES):
        raise FreshReviewError('fresh-review-dispatch-invalid')
    _digest(fields['intent_digest']); _digest(fields['payload_digest'])
    launch = fields['launch']
    launch_operation_id = fields.get('launch_operation_id')
    if launch is not None:
        if launch_operation_id is not None:
            _identifier(launch_operation_id)
            if launch_operation_id == fields['prepare_operation_id']:
                raise FreshReviewError('fresh-review-operation-conflict')
        _, independent = validate_launch(request, dispatch, launch)
        if type(fields['independent']) is not bool or fields['independent'] != independent:
            raise FreshReviewError('fresh-review-independent-invalid')
        fields['launch'] = freeze_json_value(launch)
    elif launch_operation_id is not None or fields['independent'] is not None:
        raise FreshReviewError('fresh-review-record-invalid')
    if fields['status'] == 'running' and launch is None or fields['status'] == 'dispatch-unknown' and launch is not None:
        raise FreshReviewError('fresh-review-record-invalid')
    result = fields['result']
    if fields['status'] in ('blocked', 'abandoned-unknown', 'failed', 'completed'):
        receipt = decode_terminal_receipt(result)
        if not isinstance(receipt, (BlockedFreshReview, AbandonedFreshReview, FailedFreshReview, CompletedFreshReview)) or (
                receipt.outcome, receipt.request_id, receipt.request_digest, receipt.nonce,
                receipt.dispatch_operation_id, receipt.dispatch_fencing_epoch, receipt.candidate_digest) != (
                fields['status'], request.request_id, canonical_digest(request_document(request)), request.nonce,
                dispatch['operation_id'], dispatch['fencing_epoch'], request.candidate_digest):
            raise FreshReviewError('fresh-review-terminal-binding-mismatch')
        terminal_launch = getattr(receipt, 'launch_receipt', None)
        if (isinstance(receipt, BlockedFreshReview) and launch is not None
                or (receipt_document(terminal_launch) if terminal_launch is not None else None) != launch):
            raise FreshReviewError('fresh-review-terminal-binding-mismatch')
        if isinstance(receipt, CompletedFreshReview) and receipt.independent != fields['independent']:
            raise FreshReviewError('fresh-review-independent-invalid')
        fields['result'] = freeze_json_value(receipt_document(receipt))
    elif result is not None:
        raise FreshReviewError('fresh-review-record-invalid')
    fields['dispatch'] = freeze_json_value(dispatch)
    return fields


def dispatch_state(state, command):
    document = state.legacy_passthrough.thaw() if state.legacy_passthrough is not None else state.extensions.thaw()
    validate_projection_backing(document, state.fresh_review)
    _identifier(command.operation_id)
    _integer(command.fencing_epoch, 'fresh-review-fence-invalid')
    if command.fencing_epoch != getattr(state.lease, 'fencing_epoch', 0):
        raise FreshReviewError('fresh-review-stale-fence')
    matches = [record for record in state.fresh_review.requests if (record.request_id if isinstance(record, WithdrawnFreshReviewRecord)
                   else record.request.request_id) == command.request_id]
    if len(matches) != 1:
        raise FreshReviewError('fresh-review-request-unavailable')
    record = matches[0]
    if isinstance(command, WithdrawFreshReviewRequest):
        projection = withdraw_request(state.fresh_review, request_id=command.request_id,
            operation_id=command.operation_id, fencing_epoch=command.fencing_epoch)
        return _publish_projection(state, document, projection)
    if isinstance(record, WithdrawnFreshReviewRecord):
        raise FreshReviewError('fresh-review-request-withdrawn')
    request = record.request
    if any(command.operation_id in record_operation_ids(item)
           for item in state.fresh_review.requests if item != record) or command.operation_id == record.prepare_operation_id:
        raise FreshReviewError('fresh-review-operation-conflict')
    if isinstance(command, (BeginFreshReviewDispatch, RecordFreshReviewLaunch)):
        contract = document.get('acceptance_contract')
        if (not isinstance(contract, dict) or request.contract_digest != canonical_contract_digest(contract)
                or request.iteration != state.control.iteration or command.candidate_digest != request.candidate_digest):
            raise FreshReviewError('fresh-review-stale')
    if isinstance(command, BeginFreshReviewDispatch):
        if record.status != 'pending':
            raise FreshReviewError('fresh-review-nonce-reused')
        if any(command.operation_id in record_operation_ids(item) for item in state.fresh_review.requests):
            raise FreshReviewError('fresh-review-operation-conflict')
        if not isinstance(command.dispatch, FrozenJsonObject):
            raise FreshReviewError('fresh-review-dispatch-invalid')
        new = replace(record, status='dispatch-unknown', operation_id=command.operation_id,
                      intent_digest=command.intent_digest, payload_digest=command.payload_digest, dispatch=command.dispatch)
        if command.dispatch.thaw().get('fencing_epoch') != command.fencing_epoch:
            raise FreshReviewError('fresh-review-stale-fence')
    elif isinstance(command, RecordFreshReviewLaunch):
        if record.status != 'dispatch-unknown' or not isinstance(command.launch, FrozenJsonObject):
            raise FreshReviewError('fresh-review-not-dispatch-unknown')
        launch, independent = validate_launch(request, record.dispatch.thaw(), command.launch.thaw())
        if any(isinstance(item, FreshReviewRecord) and item.launch is not None and item.launch.thaw()['child_identity'] == launch.child_identity
               for item in state.fresh_review.requests):
            raise FreshReviewError('fresh-review-child-reused')
        new = replace(record, status='running', launch=command.launch,
                      launch_operation_id=command.operation_id, independent=independent)
    else:
        if record.status not in ('dispatch-unknown', 'running') or not isinstance(command.receipt, FrozenJsonObject):
            raise FreshReviewError('fresh-review-consumed')
        receipt = decode_terminal_receipt(command.receipt.thaw())
        if isinstance(command, ImportFreshReviewOutput):
            from .fresh_review_publish import validate_failed_import
            validate_failed_import(record, command, document.get('acceptance_contract'))
        elif not isinstance(receipt, (BlockedFreshReview, AbandonedFreshReview)):
            raise FreshReviewError('fresh-review-output-import-required')
        if (receipt.commit_operation_id, receipt.commit_fencing_epoch) != (command.operation_id, command.fencing_epoch):
            raise FreshReviewError('fresh-review-stale-fence')
        new = replace(record, status=receipt.outcome.value, result=command.receipt)
    projection = _replace_record(state.fresh_review, record, new)
    return _publish_projection(state, document, projection)


def _publish_projection(state, document, projection):
    document['fresh_review'] = projection_document(projection)
    validate_projection_backing(document, projection)
    key = 'legacy_passthrough' if state.legacy_passthrough is not None else 'extensions'
    return replace(state, fresh_review=projection, snapshot_provenance=None, **{key: freeze_json_value(document)})
