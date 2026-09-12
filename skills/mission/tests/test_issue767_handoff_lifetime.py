"""Issue #767 PR B: handoff の寿命を plan binding で終わらせる.

設計は Issue #767 の本文（3 round の設計レビューで確定）。PR B の範囲は D1 / D2 / D4 / D5。

iteration 1 の後に planning へ戻ると、2 つの経路のどちらも通らなかった。

| 経路 | 何が起きるか |
| --- | --- |
| 同じ plan のまま planning → executing | `handoff-already-exists` |
| 新しい plan を adopt-core してから executing | 旧 handoff が新 plan と一致せず decode が落ちる |

ここで固定するのは 4 つ。

1. **D2 の表**（どの handoff を破棄してよく、どれが人の判断を要するか）
2. その表が **kernel と application の両方で同じ答えを出す**こと（D4）
3. 表に無い status は **拒否する**こと（D4 の fail-closed）
4. 案内文が **実在するコマンドだけ**を名指しすること（D5）
"""

from __future__ import annotations

import json

import pytest

from .test_issue550_c2_stage_b_batch1 import (
    _env,
    _prepare_handoff,
    _public_state,
)


# --- D2 の表（kernel の純関数） ------------------------------------------------


@pytest.mark.parametrize(
    ("status", "decisions", "discardable"),
    [
        ("absent", 0, True),
        ("prepared", 0, True),
        ("prepared", 1, False),
        ("prepared", 5, False),
        ("consuming", 0, False),
        ("consuming", 3, False),
        ("consumed", 0, True),
        ("consumed", 2, True),
        ("rejected", 0, True),
        ("rejected", 2, True),
    ],
    ids=[
        "absent", "prepared-clean", "prepared-one-step", "prepared-many-steps",
        "consuming-clean", "consuming-with-steps",
        "consumed", "consumed-with-steps", "rejected", "rejected-with-steps",
    ],
)
def test_the_discard_table_matches_the_settled_design(status, decisions, discardable):
    """D2 の表を全 status ぶん固定する（受け入れ条件 9）."""
    from mission_kernel.transitions import handoff_discard_refusal

    refusal = handoff_discard_refusal(status, decisions)

    assert (refusal is None) is discardable, (status, decisions, refusal)


@pytest.mark.parametrize(
    ("status", "decisions", "expected"),
    [
        ("prepared", 1, "handoff-has-recorded-steps"),
        ("consuming", 0, "handoff-in-flight"),
    ],
    ids=["prepared-with-steps", "consuming"],
)
def test_each_refusal_names_its_own_reason(status, decisions, expected):
    """拒否の理由を区別する.

    **2 つを同じコードにすると、直し方が違う失敗を同じ案内へ倒すことになる。**
    `consuming` は step を終えれば解けるが、`prepared` + decision は
    「完了済み step を失う」という別の理由で止めている。
    """
    from mission_kernel.transitions import handoff_discard_refusal

    assert handoff_discard_refusal(status, decisions) == expected


@pytest.mark.parametrize(
    "status",
    ["", "unknown", "Prepared", "CONSUMING", "in-flight", None, 3, ["prepared"]],
    ids=["empty", "unknown", "wrong-case", "upper", "invented", "none", "int", "list"],
)
def test_a_status_outside_the_table_is_refused(status):
    """表に無い status は拒否する（D4 の fail-closed・受け入れ条件 10）.

    **「`consuming` 以外なら通す」と書くと、将来 status が増えたときに黙って通る。**
    """
    from mission_kernel.transitions import handoff_discard_refusal

    assert handoff_discard_refusal(status, 0) == "handoff-status-unknown"


@pytest.mark.parametrize(
    "decisions",
    [-1, None, "0", 1.0, True],
    ids=["negative", "none", "str", "float", "bool"],
)
def test_a_decision_count_that_is_not_a_whole_number_is_refused(decisions):
    """decision 数が整数でなければ拒否する.

    `prepared` の可否は件数で決まるので、**数えられない値を 0 として扱うと
    完了済み step を持つ handoff を破棄しうる。** `True` は `int` の派生だが、
    件数としては意味を持たないので受け付けない。
    """
    from mission_kernel.transitions import handoff_discard_refusal

    assert handoff_discard_refusal("prepared", decisions) == "handoff-decisions-unknown"


# --- CLI: 受け入れ条件 1〜5・7・8・11 ------------------------------------------


def _back_to_planning(raw_run_cli, root, session_id, operation_id):
    """executing から planning へ戻す.

    `set phase=` は専用 command を使えと拒否されるので `advance` を使う
    （`--phase planning` は通ることを実測で確認した）。
    """
    return raw_run_cli(
        "advance", "--phase", "planning",
        cwd=root, env_extra=_env(session_id, operation_id=operation_id),
    )


def test_the_same_plan_can_be_re_entered_when_no_step_was_recorded(
    raw_run_cli, tmp_path
):
    """受け入れ条件 1。`prepared` + decision 0 件なら、同じ plan のまま通る."""
    session_id = "r767-same-plan"
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    assert _public_state(raw_run_cli, tmp_path, session_id)[
        "executor_handoff"
    ]["status"] == "prepared"

    assert _back_to_planning(
        raw_run_cli, tmp_path, session_id, "op-back"
    ).returncode == 0

    result = raw_run_cli(
        "advance", "--phase", "executing",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-advance-2"),
    )

    assert result.returncode == 0, result.stderr
    state = _public_state(raw_run_cli, tmp_path, session_id)
    assert state["phase"] == "executing"
    assert state["executor_handoff"]["status"] == "prepared"


def test_re_entering_starts_from_a_handoff_with_no_recorded_steps(
    raw_run_cli, tmp_path
):
    """受け入れ条件 8。置き換え後の handoff は decision 0 件から始まる."""
    session_id = "r767-fresh"
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    before = _public_state(raw_run_cli, tmp_path, session_id)["executor_handoff"]
    assert _back_to_planning(
        raw_run_cli, tmp_path, session_id, "op-back"
    ).returncode == 0
    assert raw_run_cli(
        "advance", "--phase", "executing",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-advance-2"),
    ).returncode == 0

    state = _public_state(raw_run_cli, tmp_path, session_id)

    assert state["executor_handoff"]["status"] == "prepared"
    assert not [
        item for item in state.get("decisions", [])
        if item.get("handoff_id") == state["executor_handoff"]["handoff_id"]
    ]
    # 別の handoff として始まっている。
    assert state["executor_handoff"]["handoff_id"] != before["handoff_id"]


@pytest.mark.parametrize(
    "reason", ["operator-abort", "plan-superseded"], ids=["operator", "superseded"]
)
def test_an_aborted_handoff_no_longer_blocks_the_next_iteration(
    raw_run_cli, tmp_path, reason
):
    """受け入れ条件 11。abort で `rejected` にした handoff から advance が通る.

    **PR A はこの出口を作っただけで、その先は通らなかった。** `rejected` は D2 で
    破棄してよい側だが、それを実装するのが本 PR である。
    """
    session_id = "r767-aborted-" + reason
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    assert raw_run_cli(
        "executor-handoff", "abort", "--reason", reason,
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-abort"),
    ).returncode == 0
    assert _public_state(raw_run_cli, tmp_path, session_id)[
        "executor_handoff"
    ]["status"] == "rejected"
    assert _back_to_planning(
        raw_run_cli, tmp_path, session_id, "op-back"
    ).returncode == 0

    result = raw_run_cli(
        "advance", "--phase", "executing",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-advance-2"),
    )

    assert result.returncode == 0, result.stderr
    assert _public_state(raw_run_cli, tmp_path, session_id)[
        "executor_handoff"
    ]["status"] == "prepared"


def test_a_consumed_handoff_no_longer_blocks_the_next_iteration(
    raw_run_cli, tmp_path
):
    """受け入れ条件 3。`consumed` は終端なので破棄してよい."""
    session_id = "r767-consumed"
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    for operation_id, command in [
        ("op-begin", ("begin",)),
        ("op-s1", ("record-step", "--step-id", "s1", "--result", "ok")),
        ("op-s2", ("record-step", "--step-id", "s2", "--result", "ok")),
        ("op-complete", ("complete",)),
    ]:
        assert raw_run_cli(
            "executor-handoff", *command,
            cwd=tmp_path, env_extra=_env(session_id, operation_id=operation_id),
        ).returncode == 0, command
    assert _public_state(raw_run_cli, tmp_path, session_id)[
        "executor_handoff"
    ]["status"] == "consumed"
    assert _back_to_planning(
        raw_run_cli, tmp_path, session_id, "op-back"
    ).returncode == 0

    result = raw_run_cli(
        "advance", "--phase", "executing",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-advance-2"),
    )

    assert result.returncode == 0, result.stderr


def test_advancing_to_reviewing_preserves_a_completed_handoffs_decisions(
    raw_run_cli, tmp_path
):
    """同じ handoff のまま reviewing へ進んでも decision を消さない."""
    session_id = "r767-reviewing-decisions"
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    for operation_id, command in [
        ("op-begin", ("begin",)),
        ("op-s1", ("record-step", "--step-id", "s1", "--result", "ok")),
        ("op-s2", ("record-step", "--step-id", "s2", "--result", "ok")),
        ("op-complete", ("complete",)),
    ]:
        assert raw_run_cli(
            "executor-handoff", *command,
            cwd=tmp_path, env_extra=_env(session_id, operation_id=operation_id),
        ).returncode == 0, command
    before = _public_state(raw_run_cli, tmp_path, session_id)["decisions"]
    assert len(before) == 2, before

    result = raw_run_cli(
        "advance", "--phase", "reviewing",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-reviewing"),
    )

    assert result.returncode == 0, result.stderr
    after = _public_state(raw_run_cli, tmp_path, session_id)
    assert after["phase"] == "reviewing"
    assert after["decisions"] == before


def test_a_consuming_handoff_still_blocks_and_names_real_commands(
    raw_run_cli, tmp_path
):
    """受け入れ条件 5・7。in-flight は拒否し、案内は実在するコマンドだけを名指しする."""
    session_id = "r767-consuming"
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    assert raw_run_cli(
        "executor-handoff", "begin",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-begin"),
    ).returncode == 0
    assert _back_to_planning(
        raw_run_cli, tmp_path, session_id, "op-back"
    ).returncode == 0

    result = raw_run_cli(
        "advance", "--phase", "executing",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-advance-2"),
    )

    assert result.returncode == 2
    message = result.stderr + result.stdout
    assert "executor-handoff complete" in message
    assert "executor-handoff abort" in message
    # **存在しないコマンドを名指ししない。** これが本 issue の原因の半分だった。
    assert "handoff resume" not in message
    assert _public_state(raw_run_cli, tmp_path, session_id)["phase"] == "planning"


def test_a_prepared_handoff_with_recorded_steps_still_blocks(raw_run_cli, tmp_path):
    """受け入れ条件 4。`prepared` + decision 1 件以上は拒否する.

    `record-step` は `consuming` を要求しないので、**`prepared` のまま decision を
    持つ状態が到達可能**である。黙って置き換えると完了済み step を失う。
    """
    session_id = "r767-prepared-steps"
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    assert raw_run_cli(
        "executor-handoff", "record-step", "--step-id", "s1", "--result", "ok",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-s1"),
    ).returncode == 0
    state = _public_state(raw_run_cli, tmp_path, session_id)
    assert state["executor_handoff"]["status"] == "prepared"
    assert state["decisions"]
    assert _back_to_planning(
        raw_run_cli, tmp_path, session_id, "op-back"
    ).returncode == 0

    result = raw_run_cli(
        "advance", "--phase", "executing",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-advance-2"),
    )

    assert result.returncode == 2
    message = result.stderr + result.stdout
    assert "executor-handoff complete" in message
    assert "executor-handoff abort" in message
    assert "handoff resume" not in message


# --- D1: adopt-core -----------------------------------------------------------


def _adopt_core(raw_run_cli, root, session_id, operation_id, *, source_id="core-again"):
    plan = root / ".mission-state" / "plans" / "adopted.json"
    plan.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "objective": "re-adopt a plan for the next iteration",
        "scope": {
            "resources": [],
            "actions": [{"type": "analyze", "effect_class": "reversible"}],
        },
        "assumptions": [
            {
                "id": "isolated-fixture",
                "statement": "the test workspace is isolated",
                "validation": "use the pytest temporary directory",
            }
        ],
        "steps": [
            {
                "id": "t1",
                "action": "analyze",
                "inputs": [],
                "outputs": ["finding"],
                "depends_on": [],
                "acceptance_checks": ["the step is recorded"],
                "risk": "low",
                "rollback": "none",
            }
        ],
        "global_acceptance": ["the new plan becomes canonical"],
        "stop_conditions": ["the lifecycle refuses a bound operation"],
    }
    plan.write_bytes(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    return raw_run_cli(
        "planning", "adopt-core", "--input", str(plan), "--source-id", source_id,
        cwd=root, env_extra=_env(session_id, operation_id=operation_id),
    )


def test_adopting_a_new_plan_discards_a_handoff_that_may_go(raw_run_cli, tmp_path):
    """受け入れ条件 2。新しい plan を採用してから executing が通る.

    **旧 handoff が残ると decode の時点で落ちる。** D1 は「binding が変わったら
    D2 の表に従う」と決めており、`prepared` + decision 0 件は破棄してよい側である。
    """
    session_id = "r767-adopt"
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    assert _back_to_planning(
        raw_run_cli, tmp_path, session_id, "op-back"
    ).returncode == 0

    adopted = _adopt_core(raw_run_cli, tmp_path, session_id, "op-adopt")

    assert adopted.returncode == 0, adopted.stderr
    # 旧 handoff は残っていない。
    assert _public_state(raw_run_cli, tmp_path, session_id).get(
        "executor_handoff"
    ) in (None, {})

    result = raw_run_cli(
        "advance", "--phase", "executing",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-advance-2"),
    )

    assert result.returncode == 0, result.stderr


def test_adopting_a_new_plan_is_refused_while_a_handoff_is_in_flight(
    raw_run_cli, tmp_path
):
    """受け入れ条件 4・5。破棄できない handoff があるなら adopt-core 自体を拒否する.

    **plan だけ差し替えて handoff を残すと、codec の binding invariant に反する。**
    どちらを残しても壊れるので、入口で止める。
    """
    session_id = "r767-adopt-blocked"
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    assert raw_run_cli(
        "executor-handoff", "begin",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-begin"),
    ).returncode == 0
    assert _back_to_planning(
        raw_run_cli, tmp_path, session_id, "op-back"
    ).returncode == 0
    before = _public_state(raw_run_cli, tmp_path, session_id)

    adopted = _adopt_core(raw_run_cli, tmp_path, session_id, "op-adopt")

    assert adopted.returncode == 2
    message = adopted.stderr + adopted.stdout
    assert "executor-handoff complete" in message
    assert "executor-handoff abort" in message
    assert "handoff resume" not in message
    after = _public_state(raw_run_cli, tmp_path, session_id)
    # plan も handoff も動いていない。
    assert after["canonical_plan"] == before["canonical_plan"]
    assert after["executor_handoff"] == before["executor_handoff"]


# --- kernel を直接叩く（D4: 認可の正典は kernel） -------------------------------
#
# CLI 経由のテストだけだと、**application 層の早期拒否が先に効くので kernel の
# ガードを外す変異が通る。** 受け入れ条件 9 が「kernel を直接叩くテスト」を
# 求めているのはこのためである。


def _digest(seed: str = "a") -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _planning_document() -> dict:
    """planning phase の v4 文書。**CLI を起動せずに組む.**

    `generate_cli_state_corpus` は多数のサブプロセスを起こすので、変異注入の
    ように何度も回す検査には重すぎる。ここで要るのは「canonical plan があり
    planning にいる」state だけなので、文書を直接書く。
    """
    return {
        "schema_version": 4,
        "mission": "re-enter executing after an iteration",
        "mission_id": "mission-767",
        "session_id": "portable-session",
        "phase": "planning",
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
        "decisions": [],
    }


def _kernel_states():
    """planning の state と、そこから executing へ進む command を返す."""
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import AdvancePhase
    from mission_kernel.model import Phase, PreparedHandoff

    state = decode_mission_state(
        json.dumps(_planning_document()).encode("utf-8")
    )
    handoff = PreparedHandoff(
        schema="mission-handoff/1",
        handoff_id="handoff-767-next",
        plan=state.plan,
        ordered_step_ids=("step-1", "step-2"),
    )
    return state, AdvancePhase(Phase.EXECUTING, handoff)


def _with_handoff(state, kind, decisions=()):
    """既存 handoff と decision を持つ state を組み立てる."""
    from dataclasses import replace
    from mission_kernel.a4 import ExecutorStepDecision
    from mission_kernel.model import (
        ConsumedHandoff,
        ConsumingHandoff,
        PreparedHandoff,
        RejectedHandoff,
    )

    common = ("mission-handoff/1", "handoff-767-existing", state.plan, ("step-1",))
    existing = {
        "prepared": lambda: PreparedHandoff(*common),
        "consuming": lambda: ConsumingHandoff(*common, "2030-01-01T00:00:00Z"),
        "consumed": lambda: ConsumedHandoff(
            *common, "2030-01-01T00:00:00Z", "2030-01-01T00:00:01Z"
        ),
        "rejected": lambda: RejectedHandoff(*common, "operator-abort", None),
    }[kind]()
    recorded = tuple(
        ExecutorStepDecision(
            handoff_id="handoff-767-existing",
            plan_digest=state.plan.digest,
            plan_generation=state.plan.generation,
            plan_source=state.plan.source.value,
            source_id=state.plan.source_id,
            selection_source=state.plan.selection_source,
            iteration=state.plan.iteration,
            step_id=step_id,
            result="ok",
        )
        for step_id in decisions
    )
    return replace(
        state,
        handoff=existing,
        a4=replace(state.a4, current_handoff_decisions=recorded),
    )


@pytest.mark.parametrize(
    ("kind", "decisions", "expected"),
    [
        ("prepared", (), None),
        ("prepared", ("step-1",), "handoff-has-recorded-steps"),
        ("consuming", (), "handoff-in-flight"),
        ("consumed", (), None),
        ("rejected", (), None),
    ],
    ids=["prepared-clean", "prepared-with-step", "consuming", "consumed", "rejected"],
)
def test_the_kernel_itself_applies_the_table(kind, decisions, expected):
    """受け入れ条件 9。**kernel が単独で D2 の表を適用する.**

    application 層の早期拒否を外しても、ここが通る限り誤った advance は commit されない。
    逆に kernel のガードだけを外す変異は、CLI 経由のテストでは落ちない。
    """
    from mission_kernel.transitions import decide

    planning, command = _kernel_states()

    result = decide(_with_handoff(planning, kind, decisions), command)

    if expected is None:
        assert result.accepted is True, result.rejection
        return
    assert result.accepted is False
    assert result.rejection is not None
    assert result.rejection.code == expected


# --- binding 比較と decision の数え方（unit） ----------------------------------


def test_re_adopting_identical_content_still_moves_the_binding():
    """同じ内容の再採用でも generation が上がる（D1）.

    **digest だけを見ると「変わっていない」と読める。** そこで no-op にすると、
    旧 plan に束縛された handoff が新しい generation の plan と共に残り、
    decode が落ちる。
    """
    from mission_application.planning import _plan_binding_changes

    current = {
        "path": ".mission-state/plans/p.json", "digest": "sha256:" + "a" * 64,
        "generation": 1, "source": "core", "source_id": "s1",
        "selection_source": "core", "iteration": 0,
    }
    same_content_next_generation = dict(current, generation=2)

    assert _plan_binding_changes({"canonical_plan": current}, current) is False
    assert _plan_binding_changes(
        {"canonical_plan": current}, same_content_next_generation
    ) is True


@pytest.mark.parametrize(
    "current",
    [None, "plan", 3, [], {"digest": "sha256:" + "a" * 64}],
    ids=["none", "str", "int", "list", "partial"],
)
def test_a_binding_that_cannot_be_read_counts_as_changed(current):
    """読めない binding を「変わっていない」としない.

    **変わっていないと読むと handoff が残り、codec の invariant に反する。**
    何も示せないときは変わった側へ倒す。
    """
    from mission_application.planning import _plan_binding_changes

    plan = {
        "path": ".mission-state/plans/p.json", "digest": "sha256:" + "b" * 64,
        "generation": 1, "source": "core", "source_id": "s1",
        "selection_source": "core", "iteration": 0,
    }

    assert _plan_binding_changes({"canonical_plan": current}, plan) is True


def test_only_this_handoffs_decisions_are_counted():
    """decision は `handoff_id` で絞って数える.

    **絞らないと、前の iteration の decision が残っているだけで
    「完了済み step を持つ」と読み、破棄してよい handoff を拒否する。**
    """
    from mission_application.planning import recorded_handoff_steps

    state = {
        "decisions": [
            {"handoff_id": "other", "step_id": "s1"},
            {"handoff_id": "other", "step_id": "s2"},
            {"handoff_id": "mine", "step_id": "s1"},
        ]
    }

    assert recorded_handoff_steps(state, {"handoff_id": "mine"}) == 1
    assert recorded_handoff_steps(state, {"handoff_id": "other"}) == 2
    assert recorded_handoff_steps(state, {"handoff_id": "absent"}) == 0


@pytest.mark.parametrize(
    "state",
    [
        {"decisions": None},
        {"decisions": "s1"},
        {"decisions": {"handoff_id": "mine"}},
    ],
    ids=["none", "str", "dict"],
)
def test_decisions_that_cannot_be_counted_are_not_reported_as_zero(state):
    """数えられない decision を 0 にしない.

    **0 にすると、完了済み step を持つ handoff を破棄可と判定しうる。**
    数えられないことを伝えて、共有の表に拒否させる。
    """
    from mission_application.planning import recorded_handoff_steps

    assert recorded_handoff_steps(state, {"handoff_id": "mine"}) is None


def test_a_missing_decisions_key_counts_as_none_recorded():
    """`decisions` キーが無い場合は 0 件として数える.

    **codec は欠落を空リストとして読む**（`decode_v4_a4_projection`）ので、
    ここで「数えられない」と読むと **kernel が通す state を application が拒否する。**
    異系統レビューが、2 層の答えが食い違うと指摘した。
    """
    from mission_application.planning import recorded_handoff_steps

    assert recorded_handoff_steps({}, {"handoff_id": "mine"}) == 0


@pytest.mark.parametrize(
    "handoff", [None, {}], ids=["none", "empty-object"]
)
def test_the_absent_handoff_spellings_match_the_codec(handoff):
    """codec が absent として読む綴りを、application も absent として読む.

    codec は **キー欠落・`None`・空オブジェクト**を absent とする。
    application がそのどれかを「読めない handoff」として拒否すると、
    **kernel が通す state を application が止める。**
    """
    from mission_application.planning import (
        plan_adoption_handoff_refusal,
        raw_handoff_is_absent,
    )

    assert raw_handoff_is_absent(handoff) is True
    assert plan_adoption_handoff_refusal({"executor_handoff": handoff}, {}) is None
    # キー欠落も同じ。
    assert plan_adoption_handoff_refusal({}, {}) is None


def test_a_present_handoff_is_not_read_as_absent():
    """対照。**空でない handoff は absent にしない.**"""
    from mission_application.planning import raw_handoff_is_absent

    assert raw_handoff_is_absent({"status": "prepared"}) is False
    assert raw_handoff_is_absent("") is False
    assert raw_handoff_is_absent(0) is False


def test_an_unreadable_handoff_refuses_plan_adoption():
    """読めない handoff は、adopt-core で捨てずに拒否する."""
    from mission_application.planning import plan_adoption_handoff_refusal

    assert plan_adoption_handoff_refusal(
        {"executor_handoff": "broken"}, {}
    ) == "handoff-status-unknown"


def test_adopting_a_plan_keeps_an_empty_handoff_spelling():
    """codec が absent と読む空 object は adopt-core でも削除しない."""
    from mission_application.planning import commit_plan_evidence

    state = {
        "canonical_plan": {
            "schema": "mission-plan/1", "path": "p.json",
            "digest": "sha256:" + "a" * 64, "source": "core", "source_id": "s1",
            "source_digest": "sha256:" + "b" * 64, "selection_source": "core",
            "iteration": 0, "generation": 1, "validated_at": "2030-01-01T00:00:00Z",
        },
        "executor_handoff": {},
        "decisions": [],
    }
    plan = dict(state["canonical_plan"], generation=2)

    commit_plan_evidence(
        state=state, plan=plan, lease_verified=True, publish=lambda _binding: None
    )

    assert state["executor_handoff"] == {}


def test_the_adoption_refusal_keeps_a_machine_readable_code():
    """拒否の理由が、案内文だけになって機械可読なコードを失わないこと.

    adapter は例外の文字列をそのまま gate の理由コードへ入れる。
    **案内文だけにすると、記録から `handoff-in-flight` のようなコードが消える。**
    """
    from mission_application.planning import PlanningFailure, commit_plan_evidence

    state = {
        "canonical_plan": {
            "schema": "mission-plan/1", "path": "p.json",
            "digest": "sha256:" + "a" * 64, "source": "core", "source_id": "s1",
            "source_digest": "sha256:" + "b" * 64, "selection_source": "core",
            "iteration": 0, "generation": 1, "validated_at": "2030-01-01T00:00:00Z",
        },
        "executor_handoff": {"status": "consuming", "handoff_id": "h1"},
        "decisions": [],
    }
    plan = dict(state["canonical_plan"], generation=2)

    with pytest.raises(PlanningFailure) as caught:
        commit_plan_evidence(
            state=state, plan=plan, lease_verified=True, publish=lambda _b: None
        )

    message = str(caught.value)
    assert message.startswith("handoff-in-flight:")
    # 案内文も残っている。
    assert "executor-handoff abort" in message


# --- #774 からの引き継ぎ: complete / verify の replay ---------------------------
#
# #774 では不変条件を公開 projection という proxy で固定しており、この 2 つの
# operation は replay されていなかった。**本 PR が handoff の寿命を変えることで、
# replay 後に別の handoff が作られる経路が初めて到達可能になる。**


def test_replaying_verify_step_survives_a_later_handoff_replacement(
    raw_run_cli, tmp_path
):
    """`verify-step` の replay が、その後に作られた**別の** handoff を返さない.

    replay の応答は `replayed_state` から組むので、あとで handoff が入れ替わっても
    元の operation が見た handoff を返す。
    """
    session_id = "r767-replay-verify"
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    assert raw_run_cli(
        "executor-handoff", "begin",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-begin"),
    ).returncode == 0
    first = raw_run_cli(
        "executor-handoff", "verify-step", "--step-id", "s1",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-verify"),
    )
    assert first.returncode == 0, first.stderr
    original = json.loads(first.stdout)["executor_handoff"]["handoff_id"]

    # handoff を終わらせ、次の iteration で別の handoff を作る。
    assert raw_run_cli(
        "executor-handoff", "abort", "--reason", "operator-abort",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-abort"),
    ).returncode == 0
    assert _back_to_planning(
        raw_run_cli, tmp_path, session_id, "op-back"
    ).returncode == 0
    assert raw_run_cli(
        "advance", "--phase", "executing",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-advance-2"),
    ).returncode == 0
    replacement = _public_state(raw_run_cli, tmp_path, session_id)["executor_handoff"]
    assert replacement["handoff_id"] != original

    replayed = raw_run_cli(
        "executor-handoff", "verify-step", "--step-id", "s1",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-verify"),
    )

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout) == json.loads(first.stdout)
    assert json.loads(replayed.stdout)["executor_handoff"]["handoff_id"] == original


def test_replaying_complete_survives_a_later_handoff_replacement(
    raw_run_cli, tmp_path
):
    """`complete` の replay が、その後に作られた**別の** handoff を返さない.

    `complete` は handoff を `consumed` にする。その後 D2 が `consumed` を破棄して
    新しい handoff を作っても、replay は元の `consumed` を返さなければならない。
    """
    session_id = "r767-replay-complete"
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    for operation_id, command in [
        ("op-begin", ("begin",)),
        ("op-s1", ("record-step", "--step-id", "s1", "--result", "ok")),
        ("op-s2", ("record-step", "--step-id", "s2", "--result", "ok")),
    ]:
        assert raw_run_cli(
            "executor-handoff", *command,
            cwd=tmp_path, env_extra=_env(session_id, operation_id=operation_id),
        ).returncode == 0, command
    first = raw_run_cli(
        "executor-handoff", "complete",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-complete"),
    )
    assert first.returncode == 0, first.stderr
    original = json.loads(first.stdout)["executor_handoff"]
    assert original["status"] == "consumed"

    assert _back_to_planning(
        raw_run_cli, tmp_path, session_id, "op-back"
    ).returncode == 0
    assert raw_run_cli(
        "advance", "--phase", "executing",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-advance-2"),
    ).returncode == 0
    replacement = _public_state(raw_run_cli, tmp_path, session_id)["executor_handoff"]
    assert replacement["status"] == "prepared"
    assert replacement["handoff_id"] != original["handoff_id"]

    replayed = raw_run_cli(
        "executor-handoff", "complete",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-complete"),
    )

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout) == json.loads(first.stdout)
    assert json.loads(replayed.stdout)["executor_handoff"]["status"] == "consumed"


def test_replacing_a_handoff_does_not_duplicate_the_recorded_decisions(
    raw_run_cli, tmp_path
):
    """置き換え時に、前の handoff の decision が二重に保存されないこと.

    **異系統レビューが見つけたデータ破損。** projection は「新しい handoff の id を
    持たない decision」を historical として残し、そこへ current の decision を足す。
    置き換えのときに current を引き継ぐと、**同じ decision が 2 回書かれる。**

    受け入れ条件 8 を「新しい handoff の decision が 0 件」とだけ見ていると、
    **古い decision が増えていることに気づけない。** 件数そのものを固定する。
    """
    session_id = "r767-no-dup"
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    for operation_id, command in [
        ("op-begin", ("begin",)),
        ("op-s1", ("record-step", "--step-id", "s1", "--result", "ok")),
        ("op-s2", ("record-step", "--step-id", "s2", "--result", "ok")),
        ("op-complete", ("complete",)),
    ]:
        assert raw_run_cli(
            "executor-handoff", *command,
            cwd=tmp_path, env_extra=_env(session_id, operation_id=operation_id),
        ).returncode == 0, command
    before = _public_state(raw_run_cli, tmp_path, session_id)["decisions"]
    assert len(before) == 2, before

    assert _back_to_planning(
        raw_run_cli, tmp_path, session_id, "op-back"
    ).returncode == 0
    assert raw_run_cli(
        "advance", "--phase", "executing",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-advance-2"),
    ).returncode == 0

    after = _public_state(raw_run_cli, tmp_path, session_id)["decisions"]

    # 履歴は 1 回だけ残る。増えていたら二重保存である。
    assert after == before, (len(before), len(after))
