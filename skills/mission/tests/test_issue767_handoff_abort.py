"""Issue #767 PR A: `executor-handoff abort` を公開の出口として足す.

設計 v4 の D3。`prepared` / `consuming` の handoff を人の判断で終わらせる手段が
公開されていないため、D2 が拒否側へ置く handoff から抜ける方法が無い
(`complete` は全 step の decision が揃わないと拒否され、内部の `reject` は
canonical drift 専用で CLI に登録されていない)。

ここで固定するのは 3 つ。

1. 理由コードは **新しい有限 enum** `HandoffAbortReason` であり、canonical drift の
   `CanonicalPlanRejectionCode` を流用しない (後の集計が「drift が起きた」と読めるため)
2. abort できるのは `prepared` / `consuming` だけで、`absent` / `consumed` / `rejected` は
   拒否する (二重適用を成功に見せない)
3. `--reason` は必須。破棄は不可逆なので理由を残さず実行させない

**本 PR は abort 単体で、advance / adopt-core は依然 handoff を残す。** 受け入れ条件 11
(abort 後に adopt-core / advance が通る) は PR B が担う。
"""

from __future__ import annotations

import hashlib
import json

import pytest


def _digest(seed: str = "a") -> str:
    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _handoff_document(*, status: str = "prepared", decisions=None) -> dict:
    handoff = {
        "schema": "mission-executor-handoff/1",
        "handoff_id": "handoff_" + "a" * 32,
        "plan_path": ".mission-state/plans/plan.json",
        "plan_digest": _digest(),
        "plan_generation": 4,
        "plan_source": "provider",
        "source_id": "inv_" + "1" * 32,
        "selection_source": "automatic",
        "iteration": 2,
        "step_ids": ["step-1", "step-2"],
        "status": status,
    }
    if status in {"consuming", "consumed"}:
        handoff["begun_at"] = "2029-12-31T23:59:59Z"
    if status == "consumed":
        handoff["consumed_at"] = "2030-01-01T00:00:04Z"
    if status == "rejected":
        handoff["rejected_reason"] = "canonical-plan-digest-drift"
    document = {
        "schema_version": 4,
        "mission": "Abort a typed handoff",
        "mission_id": "mission-767",
        "session_id": "portable-session",
        "phase": "executing",
        "iteration": 2,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "updated_at": "2029-12-31T23:59:58Z",
        "canonical_plan": {
            "schema": "mission-plan/1",
            "path": ".mission-state/plans/plan.json",
            "digest": _digest(),
            "source": "provider",
            "source_id": "inv_" + "1" * 32,
            "source_digest": _digest("b"),
            "selection_source": "automatic",
            "iteration": 2,
            "generation": 4,
            "validated_at": "2030-01-01T00:00:00Z",
        },
        "executor_handoff": handoff,
        "decisions": list(decisions or []),
    }
    if status == "absent":
        document.pop("executor_handoff")
    return document


_UNSET = object()


def _decide_abort(status: str, reason=_UNSET):
    """`reason` を渡さなければ既定の enum を使う。`None` は **None を渡す** 意味。"""
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import AbortExecutorHandoff, HandoffAbortReason
    from mission_kernel.transitions import decide

    state = decode_mission_state(
        json.dumps(_handoff_document(status=status)).encode("utf-8")
    )
    chosen = HandoffAbortReason.EXECUTOR_ABANDONED if reason is _UNSET else reason
    return decide(state, AbortExecutorHandoff("2030-01-01T00:00:01Z", chosen))


# --- kernel: D3 の status 表 --------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected_begun_at"),
    [("prepared", None), ("consuming", "2029-12-31T23:59:59Z")],
    ids=["prepared", "consuming"],
)
def test_abort_ends_an_open_handoff_and_keeps_its_lineage(status, expected_begun_at):
    result = _decide_abort(status)

    assert result.accepted is True
    assert [event.type for event in result.events] == ["executor-handoff-aborted"]
    handoff = result.transition.new_state.handoff
    assert handoff.kind.value == "rejected"
    assert handoff.rejected_reason == "executor-abandoned"
    assert handoff.handoff_id == "handoff_" + "a" * 32
    assert handoff.ordered_step_ids == ("step-1", "step-2")
    # `consuming` からの abort は「いつ始まったか」を失わない。
    assert handoff.begun_at == expected_begun_at


@pytest.mark.parametrize(
    "status", ["absent", "consumed", "rejected"], ids=["absent", "consumed", "rejected"]
)
def test_abort_is_refused_for_every_terminal_status(status):
    """終端の 3 つは abort の対象外。**二重適用を成功に見せない.**"""
    result = _decide_abort(status)

    assert result.accepted is False
    assert result.transition is None


@pytest.mark.parametrize(
    "reason",
    ["executor-abandoned", "canonical-plan-digest-drift", "", None, 3],
    ids=["bare-str", "drift-code", "empty", "none", "int"],
)
def test_kernel_refuses_any_reason_that_is_not_the_typed_enum(reason):
    """kernel は enum のインスタンスだけを受ける.

    文字列を通すと、command を組み立てる側が任意の理由を書けることになり、
    有限集合にした意味が無くなる。**正しい値の文字列 (`executor-abandoned`) も落とす。**
    """
    result = _decide_abort("prepared", reason=reason)

    assert result.accepted is False
    assert result.transition is None


def test_abort_preserves_recorded_decisions():
    """`prepared` + decision ありは D2 が拒否する側で、abort はその出口である.

    abort が decision を消すと、完了済み step を失ったことに気づけないまま
    先へ進める。abort は handoff を終わらせるだけで、decision には触らない。
    """
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import AbortExecutorHandoff, HandoffAbortReason
    from mission_kernel.transitions import decide

    document = _handoff_document()
    document["decisions"] = [
        {
            "handoff_id": "handoff_" + "a" * 32,
            "plan_digest": _digest(),
            "plan_generation": 4,
            "plan_source": "provider",
            "source_id": "inv_" + "1" * 32,
            "selection_source": "automatic",
            "iteration": 2,
            "step_id": "step-1",
            "result": "ok",
        }
    ]
    state = decode_mission_state(json.dumps(document).encode("utf-8"))
    before = state.a4.current_handoff_decisions

    result = decide(
        state,
        AbortExecutorHandoff(
            "2030-01-01T00:00:01Z", HandoffAbortReason.OPERATOR_ABORT
        ),
    )

    assert result.accepted is True
    assert result.transition.new_state.a4.current_handoff_decisions == before


def test_abort_reason_enum_is_exactly_the_three_designed_codes():
    """新しい有限集合。**canonical drift のコードを 1 つも含まない.**"""
    from mission_kernel.commands import CanonicalPlanRejectionCode, HandoffAbortReason

    values = {member.value for member in HandoffAbortReason}

    assert values == {"executor-abandoned", "plan-superseded", "operator-abort"}
    drift = {member.value for member in CanonicalPlanRejectionCode}
    assert values.isdisjoint(drift)


def test_abort_command_is_registered_under_its_own_command_type():
    from mission_kernel.commands import (
        AbortExecutorHandoff,
        HandoffAbortReason,
        kernel_command_type,
        kernel_command_type_names,
    )

    command = AbortExecutorHandoff(
        "2030-01-01T00:00:01Z", HandoffAbortReason.OPERATOR_ABORT
    )

    assert kernel_command_type(command) == "executor-handoff-abort"
    # 既存の drift 用 reject とは別の type であること。
    assert "executor-handoff-abort" in kernel_command_type_names()
    assert "executor-handoff-reject-canonical-drift" in kernel_command_type_names()


# --- application 層 -----------------------------------------------------------


def test_application_builds_the_typed_abort_command_from_a_reason_code():
    from mission_application.planning import prepare_executor_handoff_abort
    from mission_application.ports import PreparedTransitionOperation
    from mission_kernel.commands import AbortExecutorHandoff, HandoffAbortReason

    prepared = prepare_executor_handoff_abort(
        _handoff_document(),
        at="2030-01-01T00:00:01Z",
        reason_code="plan-superseded",
    )

    assert isinstance(prepared, PreparedTransitionOperation)
    assert prepared.command == AbortExecutorHandoff(
        "2030-01-01T00:00:01Z", HandoffAbortReason.PLAN_SUPERSEDED
    )
    assert prepared.effects == ()
    assert prepared.result == {
        "operation": "abort",
        "abort_reason": "plan-superseded",
    }


@pytest.mark.parametrize(
    ("state", "at", "reason_code"),
    [
        (_handoff_document(), "2030-01-01T00:00:01Z", "canonical-plan-digest-drift"),
        (_handoff_document(), "2030-01-01T00:00:01Z", "operator abort"),
        (_handoff_document(), "2030-01-01T00:00:01Z", None),
        (_handoff_document(), "", "operator-abort"),
        (_handoff_document(), None, "operator-abort"),
        ("not-a-mapping", "2030-01-01T00:00:01Z", "operator-abort"),
    ],
    ids=["drift-code", "typo", "missing", "empty-at", "none-at", "state-not-mapping"],
)
def test_application_refuses_inputs_it_cannot_turn_into_a_typed_command(
    state, at, reason_code
):
    from mission_application.planning import prepare_executor_handoff_abort
    from mission_application.planning import PlanningFailure

    with pytest.raises(PlanningFailure):
        prepare_executor_handoff_abort(state, at=at, reason_code=reason_code)


# --- CLI ----------------------------------------------------------------------


@pytest.fixture
def run_cli(legacy_run_cli):
    """Handoff の CLI は C1 の外で v4 所有のまま (#543).

    同じ経路を見ている `tests/test_planning_provider_lifecycle.py` と揃える。
    """
    return legacy_run_cli



def _canonical_core_state(tmp_path):
    state_file = tmp_path / ".mission-state" / "sessions" / "test.json"
    state = json.loads(state_file.read_text())
    plan = tmp_path / ".mission-state" / "plans" / "canonical.json"
    plan.parent.mkdir(exist_ok=True)
    payload = {
        "schema": "mission-plan/1",
        "steps": [{"id": "s1", "depends_on": []}, {"id": "s2", "depends_on": ["s1"]}],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    plan.write_bytes(raw)
    binding = {
        "generation": 1,
        "source": "core",
        "source_id": "planner-1",
        "selection_source": "automatic",
        "iteration": state["iteration"],
    }
    state["canonical_plan"] = {
        "path": str(plan.relative_to(tmp_path)),
        "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        **binding,
    }
    state["planning_source_records"] = {"core:planner-1": binding}
    state_file.write_text(json.dumps(state))
    return state_file, plan


def _prepared_handoff_cli(run_cli, tmp_path):
    run_cli("init", "abort plan", "--complexity", "Complex", cwd=tmp_path, check=True)
    state_file, plan = _canonical_core_state(tmp_path)
    assert run_cli("advance", "--phase", "executing", cwd=tmp_path).returncode == 0
    assert json.loads(state_file.read_text())["executor_handoff"]["status"] == "prepared"
    return state_file, plan


def test_cli_abort_ends_a_prepared_handoff_and_records_the_reason(run_cli, tmp_path):
    state_file, _plan = _prepared_handoff_cli(run_cli, tmp_path)

    result = run_cli(
        "executor-handoff", "abort", "--reason", "operator-abort", cwd=tmp_path
    )

    assert result.returncode == 0, result.stderr
    handoff = json.loads(state_file.read_text())["executor_handoff"]
    assert handoff["status"] == "rejected"
    assert handoff["rejected_reason"] == "operator-abort"


def test_cli_abort_ends_a_consuming_handoff(run_cli, tmp_path):
    state_file, _plan = _prepared_handoff_cli(run_cli, tmp_path)
    assert run_cli("executor-handoff", "begin", cwd=tmp_path).returncode == 0
    assert json.loads(state_file.read_text())["executor_handoff"]["status"] == "consuming"

    result = run_cli(
        "executor-handoff", "abort", "--reason", "executor-abandoned", cwd=tmp_path
    )

    assert result.returncode == 0, result.stderr
    handoff = json.loads(state_file.read_text())["executor_handoff"]
    assert handoff["status"] == "rejected"
    assert handoff["rejected_reason"] == "executor-abandoned"


def test_cli_abort_does_not_need_the_plan_file_to_still_be_readable(run_cli, tmp_path):
    """abort は canonical plan の照合を要求しない.

    executor が放棄した状況では plan が読めないこともある。ここで plan の一致を
    求めると、**まさに抜けたい状態で唯一の出口が塞がる。**
    """
    state_file, plan = _prepared_handoff_cli(run_cli, tmp_path)
    plan.unlink()

    result = run_cli(
        "executor-handoff", "abort", "--reason", "executor-abandoned", cwd=tmp_path
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(state_file.read_text())["executor_handoff"]["status"] == "rejected"


def test_cli_abort_requires_a_reason(run_cli, tmp_path):
    """破棄は不可逆なので、理由を残さずには実行させない.

    **引数を省いた時点で止まることまで固定する。** application 層も理由なしを
    拒否するので「非 0 で終わる」だけなら `required=False` にしても通ってしまい、
    CLI 側の必須指定が外れたことに気づけない (変異 10 が生き残った)。
    """
    state_file, _plan = _prepared_handoff_cli(run_cli, tmp_path)

    result = run_cli("executor-handoff", "abort", cwd=tmp_path)

    assert result.returncode != 0
    # argparse が必須引数の不足として止めたこと。
    assert "--reason" in result.stderr
    assert json.loads(state_file.read_text())["executor_handoff"]["status"] == "prepared"


def test_cli_abort_offers_exactly_the_enum_reasons_as_closed_choices(run_cli, tmp_path):
    """CLI の受理集合が kernel の enum と一致していること.

    CLI 側の `choices` を外しても application 層が弾くため、拒否されるかどうかを
    見るだけでは外れたことが分からない (変異 11 が生き残った)。**help に列挙される
    ことまで見る**と、値が有限集合として提示されなくなった変異を落とせる。
    """
    from mission_kernel.commands import HandoffAbortReason

    result = run_cli("executor-handoff", "abort", "--help", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    for member in HandoffAbortReason:
        assert member.value in result.stdout
    # 有限集合として出ていること (自由入力の `--reason REASON` ではない)。
    assert "--reason REASON" not in result.stdout


@pytest.mark.parametrize(
    "reason",
    ["canonical-plan-digest-drift", "operator abort", "", "OPERATOR-ABORT"],
    ids=["drift-code", "typo", "empty", "wrong-case"],
)
def test_cli_abort_refuses_a_reason_outside_the_enum(run_cli, tmp_path, reason):
    state_file, _plan = _prepared_handoff_cli(run_cli, tmp_path)

    result = run_cli("executor-handoff", "abort", "--reason", reason, cwd=tmp_path)

    assert result.returncode != 0
    assert json.loads(state_file.read_text())["executor_handoff"]["status"] == "prepared"


def test_cli_abort_is_refused_when_there_is_no_handoff(run_cli, tmp_path):
    run_cli("init", "abort plan", "--complexity", "Complex", cwd=tmp_path, check=True)
    state_file, _plan = _canonical_core_state(tmp_path)
    assert "executor_handoff" not in json.loads(state_file.read_text())

    result = run_cli(
        "executor-handoff", "abort", "--reason", "operator-abort", cwd=tmp_path
    )

    assert result.returncode == 2
    assert "executor_handoff" not in json.loads(state_file.read_text())


def test_cli_abort_twice_refuses_the_second_time(run_cli, tmp_path):
    state_file, _plan = _prepared_handoff_cli(run_cli, tmp_path)
    assert (
        run_cli(
            "executor-handoff", "abort", "--reason", "operator-abort", cwd=tmp_path
        ).returncode
        == 0
    )

    result = run_cli(
        "executor-handoff", "abort", "--reason", "plan-superseded", cwd=tmp_path
    )

    assert result.returncode == 2
    handoff = json.loads(state_file.read_text())["executor_handoff"]
    # 最初の理由が二度目に上書きされていない。
    assert handoff["rejected_reason"] == "operator-abort"
