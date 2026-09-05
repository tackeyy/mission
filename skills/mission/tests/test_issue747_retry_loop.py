"""#747 項目 2: base が動いた attempt をやり直し、3 回で打ち切る.

retry してよいのは公開前の CAS だけで、それ以外の失敗は再実行しない。
使い切ったときは publish せずに終える。
"""
import pytest


def _plan(publication_path="build/m.json"):
    from mission_application.retry_plan import ContextManifestRetryPlan

    return ContextManifestRetryPlan(
        now="2026-01-01T00:00:00Z",
        iteration=1,
        publication_path=publication_path,
    )


def test_a_successful_attempt_runs_once():
    from mission_persistence.retry_loop import run_with_base_retry

    attempts = []

    def _attempt(number):
        attempts.append(number)
        return "committed"

    assert run_with_base_retry(_plan(), _attempt) == "committed"
    assert attempts == [1]


def test_a_moved_base_is_retried():
    from mission_persistence.fenced_commit import (
        FencedCommitError,
        PRECONDITION_CAS_CODE,
    )
    from mission_persistence.retry_loop import run_with_base_retry

    attempts = []

    def _attempt(number):
        attempts.append(number)
        if number < 3:
            raise FencedCommitError(PRECONDITION_CAS_CODE, "base moved")
        return "committed"

    assert run_with_base_retry(_plan(), _attempt) == "committed"
    assert attempts == [1, 2, 3]


def test_the_budget_stops_at_three_attempts():
    from mission_application.evidence_publication import EvidencePublicationError
    from mission_persistence.fenced_commit import (
        FencedCommitError,
        PRECONDITION_CAS_CODE,
    )
    from mission_persistence.retry_loop import run_with_base_retry

    attempts = []

    def _attempt(number):
        attempts.append(number)
        raise FencedCommitError(PRECONDITION_CAS_CODE, "base moved")

    with pytest.raises(EvidencePublicationError) as excinfo:
        run_with_base_retry(_plan(), _attempt)
    assert excinfo.value.code == "base-retry-exhausted"
    assert attempts == [1, 2, 3], "the budget was not three attempts"


def test_a_final_authority_move_is_not_retried():
    """The stage is already written there; replaying would publish twice."""
    from mission_persistence.fenced_commit import (
        FINAL_AUTHORITY_CAS_CODE,
        FencedCommitError,
    )
    from mission_persistence.retry_loop import run_with_base_retry

    attempts = []

    def _attempt(number):
        attempts.append(number)
        raise FencedCommitError(FINAL_AUTHORITY_CAS_CODE, "authority moved")

    with pytest.raises(FencedCommitError) as excinfo:
        run_with_base_retry(_plan(), _attempt)
    assert excinfo.value.code == FINAL_AUTHORITY_CAS_CODE
    assert attempts == [1], "a final authority move was retried"


def test_an_unrelated_failure_is_not_retried():
    """Only a moved base earns another attempt."""
    from mission_persistence.fenced_commit import FencedCommitError
    from mission_persistence.retry_loop import run_with_base_retry

    attempts = []

    def _attempt(number):
        attempts.append(number)
        raise FencedCommitError("record-invalid", "something else")

    with pytest.raises(FencedCommitError):
        run_with_base_retry(_plan(), _attempt)
    assert attempts == [1]


def test_the_plan_identity_is_the_same_on_every_attempt():
    """Retrying must not turn one operation into several."""
    from mission_persistence.fenced_commit import (
        FencedCommitError,
        PRECONDITION_CAS_CODE,
    )
    from mission_persistence.retry_loop import run_with_base_retry

    plan = _plan()
    seen = []

    def _attempt(number):
        seen.append((plan.resolved_operation_id(), plan.semantic_intent()))
        if number < 3:
            raise FencedCommitError(PRECONDITION_CAS_CODE, "base moved")
        return "committed"

    run_with_base_retry(plan, _attempt)
    assert len(set(seen)) == 1, seen
