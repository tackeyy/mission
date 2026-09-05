"""#747 項目 2: 公開前と公開後の CAS を別の code で区別する.

`head-cas-mismatch` は 2 地点から出る。初回 precondition の CAS は retry で
きるが、head を差し替える直前の final authority CAS は retry できない。同じ
code のままでは、retry してよい失敗と、してはいけない失敗を呼び出し側が
区別できない。
"""
import pytest


def test_the_two_cas_points_do_not_share_one_code():
    """The reader has to tell a retryable move from a final one."""
    from mission_persistence.fenced_commit import (
        FINAL_AUTHORITY_CAS_CODE,
        PRECONDITION_CAS_CODE,
    )

    assert PRECONDITION_CAS_CODE != FINAL_AUTHORITY_CAS_CODE


def test_only_the_precondition_move_is_retryable():
    from mission_persistence.fenced_commit import (
        FINAL_AUTHORITY_CAS_CODE,
        PRECONDITION_CAS_CODE,
        is_retryable_cas_code,
    )

    assert is_retryable_cas_code(PRECONDITION_CAS_CODE) is True
    assert is_retryable_cas_code(FINAL_AUTHORITY_CAS_CODE) is False


def test_an_unrelated_code_is_not_retryable():
    """Unknown means unknown, not retryable.

    Treating anything unrecognised as retryable would replay operations whose
    failure had nothing to do with the base moving.
    """
    from mission_persistence.fenced_commit import is_retryable_cas_code

    for code in ("lease-precondition-changed", "record-invalid", "", None):
        assert is_retryable_cas_code(code) is False


def test_the_precondition_code_keeps_the_original_name():
    """The old name stays where callers already branch on it.

    `head-cas-mismatch` is the code the precondition path has always raised;
    moving it would change behaviour nobody asked to change.
    """
    from mission_persistence.fenced_commit import PRECONDITION_CAS_CODE

    assert PRECONDITION_CAS_CODE == "head-cas-mismatch"
