"""#681: verification 記録を kind ごとに分離する回帰テスト。"""

import json

from mission_gate_outcome import false_negative_summary


IMPLEMENTATION_PREFIX = "implementation-verified:"


def _payload(*, kind=None, checks):
    payload = {"schema": "mission-verification/1", "checks": checks}
    if kind is not None:
        payload["kind"] = kind
    return json.dumps(payload)


def _record(run_cli, state_dir, *, kind=None, checks, iteration=1):
    return run_cli(
        "verification", "record", "--iteration", str(iteration), "--stdin",
        cwd=state_dir.parent,
        input_text=_payload(kind=kind, checks=checks),
    )


def _check(name, ok=True):
    return {"name": name, "ok": ok, "detail": "evidence"}


def _state(history):
    return {
        "mission": "portable mission",
        "passes": True,
        "score_history": [{"iteration": 1, "composite": 4.5}],
        "verification_history": history,
    }


def test_kind_omitted_payload_remains_execution_and_is_recorded(state_dir, run_cli, read_state):
    result = _record(run_cli, state_dir, checks=[_check("tests")])

    assert result.returncode == 0, result.stderr
    assert read_state(state_dir)["verification_history"][-1]["kind"] == "execution"


def test_implementation_read_payload_is_recorded_with_its_kind(state_dir, run_cli, read_state):
    result = _record(
        run_cli,
        state_dir,
        kind="implementation-read",
        checks=[_check(f"{IMPLEMENTATION_PREFIX}module")],
    )

    assert result.returncode == 0, result.stderr
    assert read_state(state_dir)["verification_history"][-1]["kind"] == "implementation-read"


def test_kind_rejects_checks_from_the_other_stream(state_dir, run_cli):
    implementation_with_execution = _record(
        run_cli,
        state_dir,
        kind="implementation-read",
        checks=[_check("tests")],
    )
    execution_with_implementation = _record(
        run_cli,
        state_dir,
        kind="execution",
        checks=[_check(f"{IMPLEMENTATION_PREFIX}module")],
    )

    assert implementation_with_execution.returncode != 0
    assert execution_with_implementation.returncode != 0


def test_unknown_kind_is_rejected(state_dir, run_cli):
    result = _record(run_cli, state_dir, kind="other", checks=[_check("tests")])

    assert result.returncode != 0


def test_implementation_read_failure_does_not_contribute_to_false_negatives():
    summary = false_negative_summary(
        [_state([{"kind": "implementation-read", "status": "failed"}])]
    )

    assert summary["status"] == "unmeasurable"


def test_implementation_read_pass_does_not_hide_execution_failure():
    summary = false_negative_summary(
        [_state([
            {"kind": "execution", "status": "failed"},
            {"kind": "implementation-read", "status": "passed"},
        ])]
    )

    assert summary["count"] == 1


def test_legacy_entry_without_kind_is_counted_as_execution():
    summary = false_negative_summary([_state([{"status": "failed"}])])

    assert summary["count"] == 1
