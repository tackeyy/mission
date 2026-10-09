"""Read immutable review evidence and recapture completion bindings, without I/O in kernel."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable

from acceptance_contract import canonical_contract_digest, frozen_verifier_commands
from mission_kernel.fresh_review import (
    FreshReviewError, FreshReviewRecord, decode_projection, canonical_digest,
    FRESH_REVIEW_EVIDENCE_MAX_BYTES,
)
from mission_kernel.fresh_review_completion import (
    FreshReviewCompletionEvidence, decode_completion_evidence, INVALID, MISMATCH,
)
from mission_kernel.fresh_review_coverage import FreshReviewBindings
from mission_kernel.fresh_review_receipts import decode_terminal_receipt
from mission_kernel.json_codec import decode_json_object
from mission_persistence.strict_reader import read_stable_bytes_beneath
from . import fresh_review as prepare


@dataclass(frozen=True)
class FreshReviewCompletionInputs:
    evidence: tuple[FreshReviewCompletionEvidence, ...] = ()
    bindings: FreshReviewBindings | None = None


def _read(root, reference):
    try:
        raw = read_stable_bytes_beneath(root, reference.relative_path,
                                       limit=FRESH_REVIEW_EVIDENCE_MAX_BYTES).payload
    except (OSError, ValueError) as exc:
        raise FreshReviewError('acceptance-fresh-review-evidence-unavailable') from exc
    if len(raw) != reference.size or 'sha256:' + hashlib.sha256(raw).hexdigest() != reference.digest:
        raise FreshReviewError(MISMATCH)
    try:
        return decode_json_object(raw, limit=FRESH_REVIEW_EVIDENCE_MAX_BYTES).thaw()
    except (ValueError, RecursionError) as exc:
        raise FreshReviewError(INVALID) from exc


def observe_completion_inputs(state, *, root, load_policy):
    """Observe all non-withdrawn requests, including partial and older attempts.

    Filtering for FreshReviewRecord excludes withdrawn tombstones represented
    by WithdrawnFreshReviewRecord.
    Capture the union of bound commands once, then compute each request's input
    from its command subset and perspective using the same packet as prepare.
    Never substitute a saved request digest for a new observation.
    """
    if 'acceptance_contract' not in state:
        return FreshReviewCompletionInputs()
    contract = state['acceptance_contract']
    commands = frozen_verifier_commands(contract)
    records = tuple(item for item in decode_projection(state).requests if isinstance(item, FreshReviewRecord))
    evidence = []
    for record in records:
        if record.status == 'completed':
            terminal = decode_terminal_receipt(record.result.thaw())
            evidence.append(decode_completion_evidence(record.request, terminal,
                _read(root, terminal.coverage_receipt.evidence_ref),
                tuple(_read(root, ref) for ref in terminal.findings)))
    code = 'acceptance-fresh-review-bindings-unavailable'
    ids = {binding.command_id for item in records for binding in item.request.candidate_bindings}
    if not ids.issubset(commands):
        raise FreshReviewError(code)
    try:
        policy = load_policy(root)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        # RuntimeError also covers recursion while loading the current policy.
        raise FreshReviewError(code) from exc
    if not isinstance(policy, dict) or policy.get('digest') != contract['verifier_policy']['digest']:
        raise FreshReviewError(code)
    selected = {key: commands[key] for key in ids}
    snapshots = prepare._capture(root, selected)
    inputs = tuple((item.request.request_id, canonical_digest(prepare.build_input_packet(
        contract, item.request.perspective,
        {key: snapshots[key] for key in sorted({binding.command_id for binding in item.request.candidate_bindings})})))
        for item in records)
    candidates = tuple(sorted((key, value.digest) for key, value in snapshots.items()))
    if dict(candidates) != {key: value.digest for key, value in prepare._capture(root, selected).items()}:
        raise FreshReviewError(code)
    return FreshReviewCompletionInputs(tuple(evidence), FreshReviewBindings(
        canonical_contract_digest(contract), inputs, candidates))


@dataclass(frozen=True)
class FreshReviewCompletionServices:
    project_root: object
    load_policy: Callable

    def __call__(self, state):
        return observe_completion_inputs(state, root=self.project_root, load_policy=self.load_policy)
