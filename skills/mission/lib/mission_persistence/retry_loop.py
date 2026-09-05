"""#747: base が動いた attempt をやり直し、予算で打ち切る.

やり直してよいのは公開前の CAS だけである。stage が書かれた後の失敗を
やり直すと、同じ generation を二度公開する。
"""

from __future__ import annotations

from mission_application.evidence_publication import (
    MAX_BASE_RETRIES,
    EvidencePublicationError,
    next_attempt,
)
from mission_persistence.evidence_order import EvidenceOrderError, is_base_moved
from mission_persistence.fenced_commit import FencedCommitError, is_retryable_cas_code


def run_with_base_retry(plan, attempt):
    """Run one plan until it commits, the base stops moving, or the budget ends.

    The plan is the same object on every attempt: retrying must not turn one
    operation into several, so nothing about its identity is recomputed here.

    Only a moved base earns another attempt.  Any other failure is raised as
    it is -- an unrecognised error had nothing to do with the base, and
    replaying it would repeat whatever went wrong.
    """
    number = 1
    while True:
        try:
            return attempt(number)
        except (FencedCommitError, EvidenceOrderError) as exc:
            # Two failures mean the base moved: the CAS at admission, and the
            # check #746 added between the read and the admission.  The second
            # is the one this loop was written for, so leaving it out would
            # end every run on its first attempt.
            moved = is_base_moved(exc) or (
                isinstance(exc, FencedCommitError)
                and is_retryable_cas_code(getattr(exc, "code", None))
            )
            if not moved:
                raise
            if number >= MAX_BASE_RETRIES:
                # Ending here publishes nothing: the caller sees that the base
                # never held still, rather than a file written against a base
                # nobody confirmed.
                raise EvidencePublicationError(
                    "base-retry-exhausted",
                    "the base moved on every attempt; nothing was published",
                ) from exc
            number = next_attempt(number)
