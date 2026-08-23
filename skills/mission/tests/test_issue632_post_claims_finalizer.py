"""Issue #632: post-claims finalizer and terminal outcome unification."""

from __future__ import annotations


def test_supersede_marker_matches_legacy_string_normalization():
    from mission_common import is_supersede_marked, terminal_outcome_for_halt

    assert is_supersede_marked(" Superseded ", "") is True
    assert is_supersede_marked("", "SUPERSEDED BY A REPLACEMENT RUN") is True
    assert terminal_outcome_for_halt(
        "blocked-external", "implementer", superseded=True
    ) == "stale_superseded"
    assert terminal_outcome_for_halt(
        "evidence-submitted", "checker", superseded=False
    ) == "completed_evidence"


def test_markhalt_rejects_a_non_bool_supersede_marker(tmp_path):
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide

    from .mission_state_fixture_corpus import generate_cli_state_bytes

    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    state = decode_snapshot(source).state
    decision = decide(
        state,
        MarkHalt(HaltCategory.BLOCKED_EXTERNAL, "blocked", superseded=1),
    )

    assert decision.accepted is False
    assert decision.rejection.code == "invalid-supersede-marker"


def test_execute_calls_finalize_after_claims(tmp_path):
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide
    from mission_persistence.legacy_v4 import LegacyV4Repository
    from .mission_state_fixture_corpus import generate_cli_state_bytes
    import contextlib

    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    transition = decide(
        decode_snapshot(source).state, MarkHalt(HaltCategory.BLOCKED_EXTERNAL, "blocked")
    ).transition
    repository = LegacyV4Repository(lock=contextlib.nullcontext, read_state=lambda: {}, write_state=lambda state: None, backup_state=lambda: None)
    result = repository.execute(
        {"phase": "planning", "loop_active": True, "passes": False},
        lambda document: None,
        transition,
        lambda document: document.update({"finalized": document["terminal_outcome"]}),
    )
    assert result["finalized"] == "blocked_external"
