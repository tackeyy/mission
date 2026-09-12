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
