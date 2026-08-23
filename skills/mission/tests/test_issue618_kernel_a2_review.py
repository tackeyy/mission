"""Issue #618 批1-b: A2.review 系の完了隣接 command を kernel 化する.

実測: closeout は cmd_mark_passes（実 state decide(MarkPass) 経由）+ next の
合成、push-score / aggregate-reviews / manual-score-capture の完了隣接言及は
読みのみ。実書き込みは supersede-reviews の superseded 世代 terminalization
だけであり、これを decide(MarkHalt stale) + #630 claims 検証経由へ移す。
"""

from __future__ import annotations

import json

import pytest


def test_monotonic_halt_decision_accepts_stale_supersede():
    from mission_application.lifecycle import monotonic_halt_decision
    from mission_kernel.model import HaltCategory, Phase
    from mission_kernel.transitions import transition_control_claims

    decision = monotonic_halt_decision(
        {"phase": "reviewing"}, "stale", "superseded by a replacement run"
    )

    assert decision.accepted is True
    assert decision.rule_id == "mark-halt"
    claims = transition_control_claims(decision.transition)
    assert claims == {
        "phase": Phase.HALTED,
        "loop_active": False,
        "halt_category": HaltCategory.STALE,
    }


def test_monotonic_halt_decision_rejects_unknown_category():
    from mission_application.lifecycle import LifecycleFailure, monotonic_halt_decision

    with pytest.raises(LifecycleFailure) as failure:
        monotonic_halt_decision({"phase": "reviewing"}, "bogus-category", "reason")
    assert failure.value.reason == "unknown-halt-category"


def test_monotonic_halt_decision_rejects_invalid_reason():
    from mission_application.lifecycle import monotonic_halt_decision

    decision = monotonic_halt_decision({"phase": "reviewing"}, "stale", "   ")
    assert decision.accepted is False
    assert decision.rejection.code == "invalid-reason"


def test_extension_fields_decision_accepts_supersedes_index():
    from mission_application.lifecycle import extension_fields_decision

    decision = extension_fields_decision(
        {"phase": "planning"}, {"supersedes": ["old-session"]}
    )
    assert decision.accepted is True
    assert decision.rule_id == "set-extension-fields"


@pytest.mark.parametrize(
    "field,code",
    (
        ("passes", "frozen-field"),
        ("terminal_outcome", "frozen-field"),
        ("loop_active", "dedicated-field"),
        ("halt_reason", "dedicated-field"),
    ),
)
def test_extension_fields_decision_rejects_completion_adjacent_fields(field, code):
    from mission_application.lifecycle import extension_fields_decision

    decision = extension_fields_decision({"phase": "planning"}, {field: True})
    assert decision.accepted is False
    assert decision.rejection.code == code


def test_supersede_terminalizes_old_generation_with_full_stale_shape(
    legacy_run_cli, tmp_path
):
    """E2E 回帰: superseded 世代は kernel gate + claims 検証を通った stale halt
    の完全形（phase / halt_category / halt_reason / terminal_outcome）になる。"""
    common = [
        "init", "review issue", "--force-mission", "--issue-ref", "618",
        "--review-group-id", "issue-618", "--review-perspective", "quality",
    ]
    old = legacy_run_cli(
        *common, "--base-sha", "a" * 40, "--head-sha", "b" * 40,
        cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "old"},
    )
    assert old.returncode == 0, old.stderr
    current = legacy_run_cli(
        *common, "--base-sha", "c" * 40, "--head-sha", "d" * 40,
        cwd=tmp_path, env_extra={"MISSION_SESSION_ID": "current"},
    )
    assert current.returncode == 0, current.stderr

    result = legacy_run_cli(
        "supersede-reviews", "--group", "issue-618", cwd=tmp_path,
        env_extra={
            "MISSION_SESSION_ID": "current",
            "MISSION_OPERATION_ID": "supersede-issue-618",
        },
    )
    assert result.returncode == 0, result.stderr

    sessions = tmp_path / ".mission-state" / "sessions"
    old_state = json.loads((sessions / "old.json").read_text())
    current_state = json.loads((sessions / "current.json").read_text())

    assert old_state["phase"] == "halted"
    assert old_state["passes"] is False
    assert old_state["loop_active"] is False
    assert old_state["halt_category"] == "stale"
    assert old_state["halt_reason"] == "superseded by a replacement run"
    assert old_state["terminal_outcome"] == "stale_superseded"

    assert current_state["supersedes"] == ["old"]
    assert current_state["loop_active"] is True
    assert current_state["phase"] == "planning"
    assert "halt_category" not in current_state or not current_state["halt_category"]


def test_supersede_is_idempotent_for_already_terminal_generations(
    legacy_run_cli, tmp_path
):
    common = [
        "init", "review issue", "--force-mission",
        "--review-group-id", "issue-618-idem",
    ]
    for sid in ("old", "current"):
        result = legacy_run_cli(
            *common, cwd=tmp_path, env_extra={"MISSION_SESSION_ID": sid}
        )
        assert result.returncode == 0, result.stderr

    first = legacy_run_cli(
        "supersede-reviews", "--group", "issue-618-idem", cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "current",
                   "MISSION_OPERATION_ID": "supersede-idem-1"},
    )
    assert first.returncode == 0, first.stderr
    sessions = tmp_path / ".mission-state" / "sessions"
    after_first = (sessions / "old.json").read_text()

    second = legacy_run_cli(
        "supersede-reviews", "--group", "issue-618-idem", cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "current",
                   "MISSION_OPERATION_ID": "supersede-idem-2"},
    )
    assert second.returncode == 0, second.stderr
    old_state = json.loads((sessions / "old.json").read_text())
    assert old_state["terminal_outcome"] == "stale_superseded"
    assert old_state["halt_category"] == "stale"
    assert json.loads(after_first)["terminal_outcome"] == "stale_superseded"


def test_pass_gate_semantics_unchanged_for_current_generation(
    legacy_run_cli, tmp_path
):
    """supersede 後の current 世代でも pass gate（score-required）は不変。"""
    common = [
        "init", "review issue", "--force-mission",
        "--review-group-id", "issue-618-gate",
    ]
    for sid in ("old", "current"):
        result = legacy_run_cli(
            *common, cwd=tmp_path, env_extra={"MISSION_SESSION_ID": sid}
        )
        assert result.returncode == 0, result.stderr
    result = legacy_run_cli(
        "supersede-reviews", "--group", "issue-618-gate", cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "current",
                   "MISSION_OPERATION_ID": "supersede-gate"},
    )
    assert result.returncode == 0, result.stderr

    gate = legacy_run_cli(
        "mark-passes", cwd=tmp_path,
        env_extra={"MISSION_SESSION_ID": "current"},
    )
    assert gate.returncode == 2
    # gate 順序も不変: artifact gate（pending 拒否）が score gate より先に立つ
    assert "artifact applicability is pending" in gate.stderr
