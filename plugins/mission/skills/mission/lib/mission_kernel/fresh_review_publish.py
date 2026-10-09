"""Failed output publication rechecks the raw bytes; no typed carrier bypass."""
from __future__ import annotations
import base64
import binascii

from .fresh_review import FreshReviewError
from .fresh_review_output import inspect_output
from .fresh_review_receipts import decode_terminal_receipt, FailedFreshReview
from .model import FrozenJsonObject
from .commands import FreshReviewInputEffectClaim


def validate_failed_import(record, command):
    if not isinstance(command.observation, FrozenJsonObject) or not isinstance(command.budget_used, FrozenJsonObject):
        raise FreshReviewError('fresh-review-output-observation-invalid')
    try:
        raw = None if command.output_base64 is None else base64.b64decode(command.output_base64, validate=True)
    except (TypeError, ValueError, binascii.Error) as exc:
        raise FreshReviewError('fresh-review-output-observation-invalid') from exc
    decision = inspect_output(record, command.observation.thaw(), raw,
        candidate_digest=command.candidate_digest, budget_used=command.budget_used.thaw())
    receipt = decode_terminal_receipt(command.receipt.thaw())
    if (not isinstance(receipt, FailedFreshReview) or decision.outcome != 'failed'
            or receipt.reason != decision.reason or command.receipt.thaw()['budget_used'] != command.budget_used.thaw()):
        raise FreshReviewError('fresh-review-output-import-required')
    reference = receipt.output_ref
    expected = decision.diagnostic_bytes
    if expected is None:
        if reference is not None or command.effect is not None:
            raise FreshReviewError('fresh-review-output-effect-invalid')
    elif (reference is None or type(command.effect) is not FreshReviewInputEffectClaim
            or (command.effect.kind, command.effect.target, command.effect.digest, command.effect.size) !=
            (reference.kind, reference.relative_path, decision.diagnostic_digest, len(expected))):
        raise FreshReviewError('fresh-review-output-effect-invalid')
