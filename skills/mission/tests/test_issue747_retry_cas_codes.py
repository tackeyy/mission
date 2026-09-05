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


def _load_cli_module(name):
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_both_cas_codes_are_expected_gates_to_the_cli():
    """Splitting the code must not turn a concurrency outcome into a crash.

    The CLI classifies fenced failures by code for `outcome_kind`, which is an
    observable value in its JSON output.  The final authority move used to be
    reported as `head-cas-mismatch`, an expected gate; a new code the table
    does not know falls through to `internal-error`.  The full suite stayed
    green through that regression, so this pins it directly.
    """
    from mission_persistence.fenced_commit import (
        FINAL_AUTHORITY_CAS_CODE,
        PRECONDITION_CAS_CODE,
    )

    cli = _load_cli_module("issue747_cas_outcome_kind")
    assert cli._fenced_cli_outcome_kind(PRECONDITION_CAS_CODE) == "expected-gate"
    assert cli._fenced_cli_outcome_kind(FINAL_AUTHORITY_CAS_CODE) == "expected-gate"
    # The table is a closed list; an unknown code must still fall through.
    assert cli._fenced_cli_outcome_kind("no-such-cas-code") == "internal-error"
