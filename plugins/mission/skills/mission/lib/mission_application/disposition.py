"""Inert internal preparation, with no public runner or result import."""
from mission_kernel.fresh_review import canonical_bytes
from .artifact import make_evidence_effect
from .evidence import PreparedEvidenceOperation


def prepare_disposition(command):
    contents = (canonical_bytes(command.request.thaw()), canonical_bytes(command.packet.thaw()))
    effects = tuple(make_evidence_effect(claim.kind, claim.target, content)
        for claim, content in zip(command.effects, contents))
    return PreparedEvidenceOperation(command, effects, {})
