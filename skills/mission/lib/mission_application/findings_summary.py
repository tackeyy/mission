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

FINDINGS_SUMMARY_SOURCE = "review-aggregate"


def derive_findings_summary(aggregate: object) -> list[dict[str, object]]:
    """Project the findings of one verified ``mission-review-aggregate/1``."""
    from scoring_provenance import project_findings_summary

    if not isinstance(aggregate, dict):
        raise ValueError("review aggregate must be an object")
    return project_findings_summary(aggregate.get("inputs"))


def findings_summary_fields(summary: object) -> dict[str, object]:
    """Return the score_history fields for a derived summary.

    ``None`` means the entry has no review aggregate behind it (a manual score
    import), so it contributes no fields at all.  An empty list, by contrast,
    is a real observation: the reviewers raised nothing that iteration.  The
    source marker is what lets the manifest tell those two apart later.
    """
    if summary is None:
        return {}
    if not isinstance(summary, list):
        raise ValueError("findings summary must be a list")
    return {
        "findings_summary": summary,
        "findings_summary_source": FINDINGS_SUMMARY_SOURCE,
    }
