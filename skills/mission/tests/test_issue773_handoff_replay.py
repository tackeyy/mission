"""Issue #773: replay は元の operation の結果を再現する.

設計は `docs/design/773-executor-handoff-replay.md`（3 round の設計レビューで確定）。

v5 executor は "A' order"（read → prepare → admit）を持つため、**prepare は replay 検出より
前に走る**。その結果、replay なのに現在の state に対する検証が動いて失敗していた。さらに
応答が `execution.projection`（現在の head）と `prepared.result["rejection"]`（その回の
prepare が作った値）から組まれていた。

ここで固定するのは 4 つ。

1. replay では prepare の失敗を結果にしない（D1）
2. replay の応答は現在の prepare から projection も rejection も読まない（D2）
3. `operation` だけは現在の invocation から取る（historical state では区別できないため）
4. 非 replay では placeholder が何も commit せず、**保持した元の例外**が外へ出る
"""

from __future__ import annotations

import json

import pytest

from .test_issue550_c2_stage_b_batch1 import (
    _env,
    _head,
    _prepare_handoff,
    _public_state,
)


def _drift(plan) -> None:
    """canonical plan を書き換えて digest をずらす."""
    plan.write_text('{"schema":"mission-plan/1","steps":[]}', encoding="utf-8")


# --- D1 + D2: replay が元の結果を返す -----------------------------------------


def test_replay_of_begin_survives_a_canonical_drift_that_happened_after_it(
    raw_run_cli, tmp_path
):
    """受け入れ条件 1。

    1 度目の `begin` は成功して commit 済み。その後 plan がずれても、**同じ operation id の
    再実行はその記録を返す**。いま prepare が失敗するかどうかは、commit しない replay には
    関係がない。
    """
    session_id = "r773-drift"
    plan = _prepare_handoff(raw_run_cli, tmp_path, session_id)
    first = raw_run_cli(
        "executor-handoff", "begin",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-begin"),
    )
    assert first.returncode == 0, first.stderr
    _drift(plan)
    head_before = _head(tmp_path, session_id)

    replayed = raw_run_cli(
        "executor-handoff", "begin",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-begin"),
    )

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout) == json.loads(first.stdout)
    # 受け入れ条件 5: replay は何も commit しない。
    assert _head(tmp_path, session_id) == head_before


def test_replay_of_begin_survives_an_abort_that_happened_after_it(
    raw_run_cli, tmp_path
):
    """受け入れ条件 2。

    `abort` は handoff を `rejected` にする。現在の head から応答を組むと、**成功した
    `begin` の retry が「abort された」と読める。**
    """
    session_id = "r773-abort"
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    first = raw_run_cli(
        "executor-handoff", "begin",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-begin"),
    )
    assert first.returncode == 0, first.stderr
    aborted = raw_run_cli(
        "executor-handoff", "abort", "--reason", "operator-abort",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-abort"),
    )
    assert aborted.returncode == 0, aborted.stderr
    assert _public_state(raw_run_cli, tmp_path, session_id)[
        "executor_handoff"
    ]["status"] == "rejected"
    head_before = _head(tmp_path, session_id)

    replayed = raw_run_cli(
        "executor-handoff", "begin",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-begin"),
    )

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout) == json.loads(first.stdout)
    assert _head(tmp_path, session_id) == head_before


def test_replay_of_a_drift_rejected_begin_reproduces_that_failure(
    raw_run_cli, tmp_path
):
    """受け入れ条件 3。**元が失敗なら replay も失敗する。** 成功に化けない.

    1 度目の `begin` は canonical drift で拒否され、handoff を `rejected` にする transition を
    commit したうえで rc=2 を返す。その operation の replay は同じ理由で失敗しなければ
    ならない。
    """
    session_id = "r773-drift-first"
    plan = _prepare_handoff(raw_run_cli, tmp_path, session_id)
    _drift(plan)

    first = raw_run_cli(
        "executor-handoff", "begin",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-begin"),
    )

    assert first.returncode == 2
    assert "canonical-plan-digest-drift" in first.stderr
    assert _public_state(raw_run_cli, tmp_path, session_id)[
        "executor_handoff"
    ]["status"] == "rejected"
    head_before = _head(tmp_path, session_id)

    replayed = raw_run_cli(
        "executor-handoff", "begin",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-begin"),
    )

    assert replayed.returncode == 2
    assert "canonical-plan-digest-drift" in replayed.stderr
    assert _head(tmp_path, session_id) == head_before


def test_replay_of_abort_succeeds_even_though_the_handoff_is_rejected(
    raw_run_cli, tmp_path
):
    """受け入れ条件 4。

    `rejected` は abort が求めた結果なので失敗ではない。**historical state 経由でも
    #767 の免除が効くこと**を固定する。
    """
    session_id = "r773-abort-replay"
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    first = raw_run_cli(
        "executor-handoff", "abort", "--reason", "plan-superseded",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-abort"),
    )
    assert first.returncode == 0, first.stderr

    replayed = raw_run_cli(
        "executor-handoff", "abort", "--reason", "plan-superseded",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-abort"),
    )

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout) == json.loads(first.stdout)


def test_replay_reports_the_subcommand_the_caller_actually_ran(
    raw_run_cli, tmp_path
):
    """受け入れ条件 9。

    `consuming` な handoff は成功した `begin` / `verify` / `record` を区別しないので、
    `operation` は historical state から復元できない。**現在の invocation から取る。**
    それが元と一致することは、operation が違えば command が違い replay にならないことから従う。
    """
    session_id = "r773-operation"
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    assert raw_run_cli(
        "executor-handoff", "begin",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-begin"),
    ).returncode == 0
    first = raw_run_cli(
        "executor-handoff", "record-step", "--step-id", "s1", "--result", "ok",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-record"),
    )
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["operation"] == "record"

    replayed = raw_run_cli(
        "executor-handoff", "record-step", "--step-id", "s1", "--result", "ok",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-record"),
    )

    assert replayed.returncode == 0, replayed.stderr
    assert json.loads(replayed.stdout)["operation"] == "record"


def test_the_same_operation_id_with_a_different_subcommand_is_not_a_replay(
    raw_run_cli, tmp_path
):
    """受け入れ条件 7。**遅延が collision 検出を弱めていないこと.**

    operation が違えば command が違い、intent digest が違う。同じ operation id を別の
    subcommand で使い回しても replay にならず、拒否される。
    """
    session_id = "r773-collision"
    _prepare_handoff(raw_run_cli, tmp_path, session_id)
    assert raw_run_cli(
        "executor-handoff", "begin",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="shared-id"),
    ).returncode == 0
    head_before = _head(tmp_path, session_id)

    conflicting = raw_run_cli(
        "executor-handoff", "complete",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="shared-id"),
    )

    assert conflicting.returncode == 2
    assert _head(tmp_path, session_id) == head_before


# --- D1: 非 replay では元の例外が出て、何も commit されない ---------------------


def test_a_fresh_operation_whose_prepare_fails_reports_that_failure(
    raw_run_cli, tmp_path
):
    """受け入れ条件 11・12。

    prepare の失敗を遅延させる仕組みが、**非 replay で元の失敗を握り潰していないこと。**
    placeholder が kernel に拒否された事実（`unknown-command`）ではなく、prepare が
    報告した理由が出る。
    """
    session_id = "r773-fresh-failure"
    plan = _prepare_handoff(raw_run_cli, tmp_path, session_id)
    assert raw_run_cli(
        "executor-handoff", "begin",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-begin"),
    ).returncode == 0
    plan.unlink()
    head_before = _head(tmp_path, session_id)

    # 新しい operation id なので replay ではない。
    result = raw_run_cli(
        "executor-handoff", "record-step", "--step-id", "s1", "--result", "ok",
        cwd=tmp_path, env_extra=_env(session_id, operation_id="op-record"),
    )

    assert result.returncode == 2
    assert "unknown-command" not in result.stderr
    assert "canonical-plan" in result.stderr
    assert _head(tmp_path, session_id) == head_before


# --- placeholder の性質 --------------------------------------------------------


def test_the_placeholder_is_always_refused_by_the_kernel():
    """受け入れ条件 10。

    placeholder が accepted になりうる command だと、**保持した元の例外を握り潰したまま
    何かが commit される。** kernel の決定表に載っていないことを固定する。
    """
    from mission_application.planning import UnpreparedOperation
    from mission_kernel.commands import kernel_command_type, kernel_command_type_names
    from mission_kernel.transitions import TRANSITION_TABLE

    command = UnpreparedOperation()

    assert not any(
        isinstance(command, rule.command_type) for rule in TRANSITION_TABLE
    )
    # 型名の閉じた語彙にも入らない（入ると encode されうる）。
    with pytest.raises(TypeError):
        kernel_command_type(command)
    assert "unprepared-operation" not in kernel_command_type_names()


def test_the_placeholder_carries_nothing():
    """placeholder は effects も result も運ばない.

    effects を持つと `_assert_replay_materializes` が記録された materialization と
    比較しうる。result を持つと、**そこから読まれていないことが読み手に分からない**
    （応答の `operation` は現在の invocation から渡され、placeholder の result は
    どこからも読まれない）。
    """
    from mission_application.planning import unprepared_operation

    prepared = unprepared_operation()

    assert prepared.effects == ()
    assert prepared.result == {}


# --- D2: 応答の組み立て（unit） ------------------------------------------------


def _replay_execution(handoff: dict):
    from mission_application.ports import LegacyCommandExecutionResult
    from mission_kernel.json_codec import freeze_json_value

    frozen = freeze_json_value({"executor_handoff": handoff})
    return LegacyCommandExecutionResult(
        None, freeze_json_value({"executor_handoff": {"status": "prepared"}}),
        replayed=True, replayed_state=frozen,
    )


def _prepared(operation: str, **result):
    from mission_application.ports import PreparedTransitionOperation
    from mission_kernel.commands import CanonicalPlanRejectionCode, RejectExecutorHandoff

    return PreparedTransitionOperation(
        command=RejectExecutorHandoff(
            "2030-01-01T00:00:01Z", "begin", CanonicalPlanRejectionCode.DIGEST_DRIFT
        ),
        effects=(),
        result={"operation": operation, **result},
    )


def test_replay_reads_the_handoff_from_history_not_from_the_current_head():
    """D2。projection は `replayed_state` から取る."""
    from mission_application.planning import executor_handoff_response

    execution = _replay_execution(
        {"status": "consuming", "handoff_id": "handoff_x", "begun_at": "2030-01-01T00:00:00Z"}
    )

    response = executor_handoff_response(_prepared("begin"), execution, operation="begin")

    assert response["ok"] is True
    # 現在の head は `prepared` だが、返るのは historical な `consuming`。
    assert response["executor_handoff"]["status"] == "consuming"


def test_replay_ignores_a_rejection_produced_by_the_current_prepare():
    """D2。`prepared.result["rejection"]` は replay では読まない.

    replay で現在の canonical 検査が失敗すると、prepare は例外ではなく rejection transition を
    返す。それを読むと、**元の operation が成功していても失敗が返る。**
    """
    from mission_application.planning import executor_handoff_response

    execution = _replay_execution(
        {"status": "consuming", "handoff_id": "handoff_x", "begun_at": "2030-01-01T00:00:00Z"}
    )

    response = executor_handoff_response(
        _prepared("begin", rejection="canonical-plan-digest-drift"),
        execution,
        operation="begin",
    )

    assert response["ok"] is True
    assert response["executor_handoff"]["status"] == "consuming"


@pytest.mark.parametrize(
    ("status", "reason", "expected_ok"),
    [
        ("consuming", None, True),
        ("consumed", None, True),
        ("prepared", None, True),
        ("rejected", "canonical-plan-digest-drift", False),
        ("rejected", "canonical-plan-generation-mismatch", False),
        ("rejected", "operator-abort", True),
        ("rejected", "plan-superseded", True),
        ("rejected", "executor-abandoned", True),
        ("rejected", "something-else", False),
        ("rejected", None, False),
        # 前置き判定へ退化した実装を落とすための境界。`canonical` で始まるが
        # `CanonicalPlanRejectionCode` には無い値。**どちらも失敗にはなるが、
        # 名乗る理由が違う**（下の assert で見分ける）。
        ("rejected", "canonical-plan-made-up", False),
    ],
    ids=[
        "consuming", "consumed", "prepared",
        "drift-digest", "drift-generation",
        "abort-operator", "abort-superseded", "abort-abandoned",
        "unknown-reason", "missing-reason", "canonical-prefix-but-unknown",
    ],
)
def test_replay_success_is_decided_by_closed_membership_of_the_reason(
    status, reason, expected_ok
):
    """D2 の復元表。**前置き判定ではなく、閉じた集合への所属で決める.**

    未知の理由は fail-closed（受け入れ条件 8）。
    """
    from mission_application.planning import PlanningFailure, executor_handoff_response

    handoff = {"status": status, "handoff_id": "handoff_x"}
    if reason is not None:
        handoff["rejected_reason"] = reason
    execution = _replay_execution(handoff)

    from mission_application.planning import (
        UNKNOWN_REPLAY_REASON,
        _REPLAY_FAILURE_REASONS,
    )

    if expected_ok:
        response = executor_handoff_response(
            _prepared("begin"), execution, operation="begin"
        )
        assert response["ok"] is True
        assert response["executor_handoff"]["status"] == status
        return

    with pytest.raises(PlanningFailure) as caught:
        executor_handoff_response(_prepared("begin"), execution, operation="begin")

    # **名乗る理由まで固定する。** 閉じた集合に属する理由だけがそのまま出て、
    # それ以外は「不明」と名乗る。ここを緩めると、前置きで判定する実装
    # (`reason.startswith("canonical")`) が通ってしまう。
    expected = reason if reason in _REPLAY_FAILURE_REASONS else UNKNOWN_REPLAY_REASON
    assert str(caught.value) == expected


# **非 replay で rejection を読む経路**は production の CLI テストが押さえている
# (`test_replay_of_a_drift_rejected_begin_reproduces_that_failure` の 1 度目の呼び出しが
# rc=2 と drift のコードを assert する)。ここで unit として書き直さないのは、
# `LegacyCommandExecutionResult` が「replayed でないなら decision は非 None」を不変条件に
# しており、合成した execution ではその経路を正しく作れないため。
