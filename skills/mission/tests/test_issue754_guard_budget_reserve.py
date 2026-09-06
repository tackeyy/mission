"""#754: stop guard の総予算を実行 6 + 予約 2 に分け、初回期限を保持し、枯渇を終端 verdict で返す.

#749 の測定と決定レビュー（3 巡・GO）を実装へ渡す。受入条件は Issue #754 の 1〜8。
"""
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB_ROOT = REPO_ROOT / "skills" / "mission" / "lib"
GUARD_SH = REPO_ROOT / "scripts" / "mission-stop-guard.sh"
STATE_PY = REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py"

sys.path.insert(0, str(LIB_ROOT))

from mission_application import guard_timeout as gt  # noqa: E402
from mission_application.guard_timeout import (  # noqa: E402
    CONTINUATION_ENV_VAR,
    DEADLINE_ENV_VAR,
    DEFAULT_GUARD_TIMEOUT_SECONDS,
    EXHAUSTED_REASON,
    RAW_ENV_VAR,
    RESERVE_SECONDS,
    GuardTimeout,
    active_deadline,
    bounded_by_guard_timeout,
    budget_clamped_from,
    deadline_token,
    exhausted_verdict_payload,
    resolve_deadline,
    resolve_guard_timeout,
)


def _load_state_module():
    spec = importlib.util.spec_from_file_location("mission_state_cli_754", STATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("MISSION_")}
    env.update(extra)
    return env


# ---------------------------------------------------------------- 1. 内訳と受理


class TestBudgetSplit:
    def test_the_reserve_is_two_seconds_of_the_eight(self):
        assert DEFAULT_GUARD_TIMEOUT_SECONDS == 8
        assert RESERVE_SECONDS == 2

    @pytest.mark.parametrize("raw", ["3", "4", "5", "6", "7", "8"])
    def test_three_to_eight_are_honoured(self, raw):
        assert resolve_guard_timeout(raw) == int(raw)

    @pytest.mark.parametrize("raw", ["1", "2"])
    def test_one_and_two_fall_back_to_the_default(self, raw):
        """An execution budget of `value - reserve` would be zero or negative: a
        permanent block.  #742 decision review M1."""
        assert resolve_guard_timeout(raw) == DEFAULT_GUARD_TIMEOUT_SECONDS

    @pytest.mark.parametrize("raw", ["0", "9", "abc", "", "08", " 3", "3 "])
    def test_everything_else_still_falls_back(self, raw):
        assert resolve_guard_timeout(raw) == DEFAULT_GUARD_TIMEOUT_SECONDS


# ---------------------------------------------------------------- 4. raw の契約


class TestClampedFrom:
    @pytest.mark.parametrize("raw,expected", [("1", 1), ("2", 2)])
    def test_only_the_two_clamped_values_are_reported(self, raw, expected):
        assert budget_clamped_from({RAW_ENV_VAR: raw}) == expected

    @pytest.mark.parametrize("raw", ["3", "8", "0", "9", "", " 1", "1 ", "01", "１", "abc"])
    def test_anything_else_is_not_a_clamp(self, raw):
        """Exact ASCII `1` / `2` only: other bad values fall back silently as before."""
        assert budget_clamped_from({RAW_ENV_VAR: raw}) is None

    def test_a_missing_raw_is_not_a_clamp(self):
        assert budget_clamped_from({}) is None


# ---------------------------------------------------------------- 3. 初回期限の保持


class TestDeadlineRetention:
    def test_the_deadline_decided_at_entry_is_reused_until_the_call_returns(self, monkeypatch):
        """`deadline_token()` used to re-run `resolve_deadline()` and re-issue a
        deadline `now + budget`, later than the one the decorator enforced."""
        for name in (DEADLINE_ENV_VAR, CONTINUATION_ENV_VAR, gt.TIMEOUT_ENV_VAR):
            monkeypatch.delenv(name, raising=False)
        seen = {}

        @bounded_by_guard_timeout
        def body():
            seen["entry"] = active_deadline()
            first = deadline_token()
            time.sleep(0.05)
            second = deadline_token()
            return first, second

        first, second = body()
        assert first == second == "{:.3f}".format(seen["entry"])
        assert active_deadline() is None, "retention is scoped to the decorated call"

    def test_retention_is_released_even_when_the_body_raises(self, monkeypatch):
        for name in (DEADLINE_ENV_VAR, CONTINUATION_ENV_VAR):
            monkeypatch.delenv(name, raising=False)

        @bounded_by_guard_timeout
        def body():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            body()
        assert active_deadline() is None

    def test_a_carried_deadline_is_the_one_retained(self, monkeypatch):
        carried = time.time() + 5
        monkeypatch.setenv(DEADLINE_ENV_VAR, "{:.3f}".format(carried))
        monkeypatch.setenv(CONTINUATION_ENV_VAR, "1")

        @bounded_by_guard_timeout
        def body():
            return deadline_token()

        assert body() == "{:.3f}".format(carried)


# ---------------------------------------------------------------- 2. 予約の適用


class TestReserveIsSubtracted:
    def test_the_body_gets_left_minus_reserve(self, monkeypatch):
        monkeypatch.setenv(DEADLINE_ENV_VAR, "{:.3f}".format(time.time() + 5))
        monkeypatch.setenv(CONTINUATION_ENV_VAR, "1")
        granted = []

        class _Limit:
            def __init__(self, seconds):
                granted.append(seconds)

            def __enter__(self):
                return None

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(gt, "guard_time_limit", _Limit)

        @bounded_by_guard_timeout
        def body():
            return "ran"

        assert body() == "ran"
        assert len(granted) == 1
        assert granted[0] == pytest.approx(5 - RESERVE_SECONDS, abs=0.2)

    @pytest.mark.parametrize("left", [RESERVE_SECONDS - 0.5, 0.0, -1.0])
    def test_inside_the_reserve_the_body_does_not_run(self, monkeypatch, capsys, left):
        """For a side-effect command the decorator names the reason and exits 2."""
        monkeypatch.setenv(DEADLINE_ENV_VAR, "{:.3f}".format(time.time() + left))
        monkeypatch.setenv(CONTINUATION_ENV_VAR, "1")
        ran = []

        @bounded_by_guard_timeout
        def cmd_cleanup_stale():
            ran.append(True)

        with pytest.raises(SystemExit) as excinfo:
            cmd_cleanup_stale()
        assert ran == []
        assert excinfo.value.code == 2
        assert EXHAUSTED_REASON in capsys.readouterr().err

    def test_for_stop_verdict_the_decorator_emits_the_terminal_verdict(self, monkeypatch, capsys):
        monkeypatch.setenv(DEADLINE_ENV_VAR, "{:.3f}".format(time.time() + 0.5))
        monkeypatch.setenv(CONTINUATION_ENV_VAR, "1")

        @bounded_by_guard_timeout
        def cmd_stop_verdict():  # the decorator keys on the command's name
            raise AssertionError("must not run")

        assert cmd_stop_verdict() is None
        out = capsys.readouterr().out.strip().splitlines()
        assert len(out) == 1
        _assert_terminal(json.loads(out[0]))

    def test_the_terminal_verdict_carries_the_enforced_deadline(self, monkeypatch, capsys):
        """Exhaustion while the body runs must not re-issue `guard_deadline`."""
        carried = time.time() + RESERVE_SECONDS + 0.3  # 0.3 s of execution budget
        monkeypatch.setenv(DEADLINE_ENV_VAR, "{:.3f}".format(carried))
        monkeypatch.setenv(CONTINUATION_ENV_VAR, "1")

        @bounded_by_guard_timeout
        def cmd_stop_verdict():
            time.sleep(5)

        assert cmd_stop_verdict() is None
        payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert payload["guard_deadline"] == "{:.3f}".format(carried)

    def test_a_lost_budget_is_not_reported_as_exhaustion(self, monkeypatch):
        """The continuation fail-closed path (#742) keeps its own handling."""
        from mission_application.guard_timeout import GuardBudgetLost

        monkeypatch.setenv(CONTINUATION_ENV_VAR, "1")
        monkeypatch.delenv(DEADLINE_ENV_VAR, raising=False)

        @bounded_by_guard_timeout
        def cmd_stop_verdict():
            raise AssertionError("must not run")

        with pytest.raises(GuardBudgetLost):
            cmd_stop_verdict()


# ---------------------------------------------------------------- 6. 終端契約


def _assert_terminal(payload: dict):
    assert payload["schema"] == "mission-stop-verdict/1"
    assert payload["decision"] == "block"
    assert payload["reason"] == EXHAUSTED_REASON == "guard-budget-exhausted"
    assert payload["outcome_kind"] == "expected-gate"
    assert payload["command"]["kind"] == "none"
    inner = json.loads(payload["shell_text"])
    assert inner["decision"] == "block"
    assert inner["reason"] == EXHAUSTED_REASON
    assert inner["outcome_kind"] == "expected-gate"
    assert re.fullmatch(r"\d+\.\d{3}", payload["guard_deadline"])


class TestTerminalVerdict:
    def test_the_payload_builder_emits_the_contract(self):
        payload = exhausted_verdict_payload(
            {DEADLINE_ENV_VAR: "1000.000", CONTINUATION_ENV_VAR: "1"}, now=999.0
        )
        _assert_terminal(payload)
        assert "budget_clamped_from" not in payload

    def test_the_payload_carries_the_clamp_when_raw_was_clamped(self):
        payload = exhausted_verdict_payload(
            {DEADLINE_ENV_VAR: "1000.000", CONTINUATION_ENV_VAR: "1", RAW_ENV_VAR: "2"},
            now=999.0,
        )
        assert payload["budget_clamped_from"] == 2
        assert json.loads(payload["shell_text"])["budget_clamped_from"] == 2

    def test_exhaustion_before_the_body_runs_is_a_terminal_verdict_on_stdout(self, tmp_path):
        """The decorator raised before the body: this used to escape as exit 1."""
        env = _clean_env(
            **{DEADLINE_ENV_VAR: "{:.3f}".format(time.time() + 0.5), CONTINUATION_ENV_VAR: "1"}
        )
        result = subprocess.run(
            [sys.executable, str(STATE_PY), "stop-verdict", "--hook-input", "-", "--json"],
            input=json.dumps({"cwd": str(tmp_path)}),
            capture_output=True, text=True, env=env, cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        assert len(lines) == 1, "stdout is one complete JSON document"
        _assert_terminal(json.loads(lines[0]))

    def test_exhaustion_while_the_body_runs_is_the_same_terminal_verdict(
        self, monkeypatch, tmp_path, capsys
    ):
        """The body used to swallow GuardTimeout into guard-decision-unavailable / exit 2."""
        module = _load_state_module()
        monkeypatch.setenv(gt.TIMEOUT_ENV_VAR, "3")  # execution budget 1s
        monkeypatch.delenv(DEADLINE_ENV_VAR, raising=False)
        monkeypatch.delenv(CONTINUATION_ENV_VAR, raising=False)

        def _slow(_request):
            time.sleep(10)

        monkeypatch.setattr(module, "decide_stop_guard", _slow)
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"cwd": str(tmp_path)})))
        args = type("Args", (), {"hook_input": "-", "prior_decision_fd": None,
                                 "state_file": str(tmp_path / "s.json")})()
        started = time.monotonic()
        module.cmd_stop_verdict(args)  # returns normally: exit 0
        assert time.monotonic() - started < 4
        out = capsys.readouterr().out.strip().splitlines()
        assert len(out) == 1
        _assert_terminal(json.loads(out[-1]))

    @pytest.mark.parametrize("command", [
        ["cleanup-stale", "--root", ".", "--execute"],
        ["mark-halt", "--reason", "x", "--category", "user"],
        ["stop-guard-observe", "--session-id", "s754", "--digest", "sha256:" + "0" * 64,
         "--now-epoch", "1", "--ttl-seconds", "1"],
    ])
    def test_the_other_commands_name_the_reason_on_stderr(self, tmp_path, command):
        env = _clean_env(
            **{DEADLINE_ENV_VAR: "{:.3f}".format(time.time() + 0.5), CONTINUATION_ENV_VAR: "1",
               "MISSION_SESSION_ID": "s754"}
        )
        result = subprocess.run(
            [sys.executable, str(STATE_PY), *command],
            capture_output=True, text=True, env=env, cwd=tmp_path,
        )
        assert result.returncode != 0
        assert EXHAUSTED_REASON in result.stderr


# ---------------------------------------------------------------- 4'. 通常 verdict にも clamp を載せる


class TestNormalVerdictCarriesTheClamp:
    def _verdict(self, tmp_path, raw):
        env = _clean_env(**{RAW_ENV_VAR: raw, gt.TIMEOUT_ENV_VAR: "8"})
        result = subprocess.run(
            [sys.executable, str(STATE_PY), "stop-verdict", "--hook-input", "-", "--json"],
            input=json.dumps({"cwd": str(tmp_path)}),
            capture_output=True, text=True, env=env, cwd=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_clamped_from_appears_top_level(self, tmp_path):
        payload = self._verdict(tmp_path, "1")
        assert payload["budget_clamped_from"] == 1

    def test_no_clamp_no_field(self, tmp_path):
        payload = self._verdict(tmp_path, "8")
        assert "budget_clamped_from" not in payload
        assert "budget_clamped_from" not in payload["shell_text"]

    def test_finish_guard_verdict_puts_the_clamp_into_a_non_empty_shell_text(self, monkeypatch):
        """The hook forwards only `shell_text`; a top-level field alone is invisible there.

        Driven directly: a verdict whose `reply.emit` is true is what carries a
        non-empty `shell_text`, and the CLI scenario above happens to emit none.
        """
        from mission_application.guard_timeout import finish_guard_verdict

        monkeypatch.setenv(RAW_ENV_VAR, "2")
        monkeypatch.delenv(DEADLINE_ENV_VAR, raising=False)
        monkeypatch.delenv(CONTINUATION_ENV_VAR, raising=False)
        reply = {"decision": "block", "reason": "x", "outcome_kind": "expected-gate"}
        payload = finish_guard_verdict({"shell_text": json.dumps(reply) + "\n"})
        assert payload["budget_clamped_from"] == 2
        inner = json.loads(payload["shell_text"])
        assert inner["budget_clamped_from"] == 2
        assert inner["decision"] == "block" and inner["reason"] == "x"
        assert payload["shell_text"].endswith("\n") and payload["shell_text"].count("\n") == 1
        assert re.fullmatch(r"\d+\.\d{3}", payload["guard_deadline"])

    def test_finish_guard_verdict_leaves_an_unclamped_verdict_alone(self, monkeypatch):
        from mission_application.guard_timeout import finish_guard_verdict

        monkeypatch.setenv(RAW_ENV_VAR, "8")
        reply = {"decision": "block", "reason": "x", "outcome_kind": "expected-gate"}
        text = json.dumps(reply) + "\n"
        payload = finish_guard_verdict({"shell_text": text})
        assert "budget_clamped_from" not in payload
        assert payload["shell_text"] == text


# ---------------------------------------------------------------- 7. shell 側


class TestHookShell:
    def test_the_raw_value_is_exported_before_normalisation(self):
        source = GUARD_SH.read_text(encoding="utf-8")
        raw_line = source.index('MISSION_STATE_TIMEOUT_RAW=')
        case_line = source.index('case "$MISSION_STATE_TIMEOUT" in')
        assert raw_line < case_line
        assert "export MISSION_STATE_TIMEOUT_RAW" in source

    @pytest.mark.parametrize("raw,normalised", [("1", "8"), ("2", "8"), ("3", "3"), ("8", "8"), ("9", "8"), ("abc", "8")])
    def test_the_case_accepts_three_to_eight_and_keeps_the_raw(self, raw, normalised):
        script = GUARD_SH.read_text(encoding="utf-8")
        start = script.index("MISSION_STATE_TIMEOUT_RAW=")
        end = script.index("export MISSION_STATE_TIMEOUT_RAW")
        block = script[start:end]
        probe = (
            'MISSION_STATE_TIMEOUT="{}"\n'.format(raw)
            + "MISSION_STATE_TIMEOUT_RAW=already-set\n"
            + block
            + '\nprintf "%s %s" "$MISSION_STATE_TIMEOUT" "$MISSION_STATE_TIMEOUT_RAW"\n'
        )
        result = subprocess.run(["/bin/bash", "-c", probe], capture_output=True, text=True)
        assert result.stdout == "{} {}".format(normalised, raw)

    def test_the_hook_still_holds_no_judgment(self):
        from .test_issue615_guard_decision import analyze_guard_shell

        assert analyze_guard_shell(GUARD_SH.read_text(encoding="utf-8")) == []
