"""Application projection of archived review findings for bounded context (#690).

``score_history[*].findings_summary`` feeds the bounded context manifest that
diff reviewers receive.  Nothing wrote that field before this module existed,
so every manifest carried an empty ``prior_findings`` list while the tests that
covered it synthesised the state they then asserted on.

The projection deliberately runs against the review aggregate that
``push-score`` has just digest-verified, not against the scoring payload handed
in by the caller.  Deriving from the verified archive keeps a single producer:
state and archive cannot disagree, because state is computed from the archive.
"""
from __future__ import annotations

# The kernel projection compares the stored marker against this exact value, so
# the two must not drift.  Import it rather than restating it here.
from mission_kernel.evidence import FINDINGS_SUMMARY_SOURCE


def findings_summary_fields(aggregate: object) -> dict[str, object]:
    """Return the score_history fields projected from a verified aggregate.

    ``None`` means there is no review aggregate behind this entry (a manual
    score import), so it contributes no fields at all.  An empty list, by
    contrast, is a real observation: the reviewers raised nothing that
    iteration.  The source marker is what lets the manifest tell those apart.

    Only ``push-score`` calls this.  Keeping it off the ``mark-passes`` path is
    deliberate: this data is observation, and observation must not be able to
    decide whether a mission passes.
    """
    from scoring_provenance import project_findings_summary

    if aggregate is None:
        return {}
    if not isinstance(aggregate, dict):
        raise ValueError("review aggregate must be an object")
    return {
        "findings_summary": project_findings_summary(aggregate.get("inputs")),
        "findings_summary_source": FINDINGS_SUMMARY_SOURCE,
    }
