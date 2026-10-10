"""Reentry must survive a spent guard budget without allowing first calls."""
import json
import os
import io
import sys
from types import SimpleNamespace

import pytest
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HOOK = ROOT / "scripts" / "mission-stop-guard.sh"


def test_shell_reentry_never_starts_python(tmp_path):
    marker = tmp_path / "python-started"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    python = fake_bin / "python3"
    python.write_text('#!/bin/sh\nprintf started > "' + str(marker) + '"\nexit 1\n')
    python.chmod(0o755)
    env = {"PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
           "MISSION_GUARD_DEADLINE": "0.000001", "MISSION_STATE_TIMEOUT": "3"}
    result = subprocess.run(["/bin/bash", str(HOOK)],
                            input='{"stop_hook_active":true}', env=env,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", "reentry uses the runtime's silent skip reply"
    assert not marker.exists(), "Python startup must be skipped even with no budget"


def test_python_reentry_with_exhausted_budget(tmp_path):
    import sys

    env = {"PATH": os.environ["PATH"], "MISSION_GUARD_DEADLINE": "0.000001",
           "MISSION_GUARD_CONTINUATION": "1"}
    result = subprocess.run(
        [sys.executable, str(ROOT / "skills/mission/bin/mission-state.py"),
         "stop-verdict", "--hook-input", "-", "--json"],
        input='{"stop_hook_active":true}', env=env, cwd=tmp_path,
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["decision"] == "skip"
    assert payload["reason"] == "stop-hook-reentry"
    assert payload["shell_text"] == ""
    assert payload["command"] == {"kind": "none"}


def test_shell_detector_accepts_only_the_reentry_probe():
    from .test_issue615_guard_decision import analyze_guard_shell

    source = HOOK.read_text()
    assert analyze_guard_shell(source) == []
    from .test_issue615_guard_decision import _STOP_REENTRY_PROBE

    probe = _STOP_REENTRY_PROBE
    for changed in (probe.replace("== true", "!= false"),
                    probe.replace(".stop_hook_active", ".other"),
                    probe.replace('"$INPUT"', '"$STATE"')):
        violations = analyze_guard_shell(source.replace(probe, changed))
        assert "jq-input-not-guard-decision" in {v.code for v in violations}


# Shared at the preflight boundary: these inputs expose different ways a broad
# truthiness check, substring search or permissive JSON parser could skip a guard.
INPUTS = [
    ("boolean", '{"stop_hook_active":true}', True),
    ("whitespace", ' \n { "stop_hook_active" : true } \t', True),
    ("escaped-key", '{"stop_hook_\\u0061ctive":true}', True),
    ("other-true-with-reentry", '{"stop_hook_active":true,"other":true}', True),
    ("string-escapes", json.dumps({"stop_hook_active": True, "x": '"\\NaN\n'}), True),
    ("duplicate-last-true", '{"stop_hook_active":false,"stop_hook_active":true}', True),
    ("false", '{"stop_hook_active":false}', False),
    ("missing", '{}', False),
    ("other-true", '{"other":true}', False),
    ("nested", '{"x":{"stop_hook_active":true}}', False),
    ("array", '[{"stop_hook_active":true}]', False),
    ("root-true", 'true', False),
    ("root-string", '"stop_hook_active: true"', False),
    ("duplicate-last-false", '{"stop_hook_active":true,"stop_hook_active":false}', False),
    ("text-value", '{"stop_hook_active":false,"x":"stop_hook_active true"}', False),
    ("empty", '', False),
    ("truncated", '{"stop_hook_active":true', False),
    ("trailing-data", '{"stop_hook_active":true} garbage', False),
    ("two-documents", '{"stop_hook_active":true}\n{}', False),
    ("trailing-comma", '{"stop_hook_active":true,}', False),
    ("comment", '{"stop_hook_active":true /* x */}', False),
    ("bom", '\ufeff{"stop_hook_active":true}', False),
    ("bad-escape", '{"stop_hook_active":true,"x":"\\q"}', False),
    ("control-in-string", '{"stop_hook_active":true,"x":"\t"}', False),
    ("nul-tail", '{"stop_hook_active":true}\x00{}', False),
    ("nul-in-key", '{"stop_hook_\x00active":true}', False),
    ("escaped-nul", '{"stop_hook_active":true,"x":"\\u0000"}', True),
    ("wrong-case", '{"Stop_hook_active":true}', False),
]
INPUTS += [("flag-" + name, json.dumps({"stop_hook_active": value}), False)
           for name, value in [("string", "true"), ("one", 1), ("null", None),
                               ("list", [True]), ("object", {"x": True})]]
INPUTS += [("nonstandard-" + str(i), '{"stop_hook_active":true,"x":' + value + '}', False)
           for i, value in enumerate(["NaN", "Infinity", "-Infinity", "+1", "01", "-01",
                                      ".1", "1.", "0x1", "1e", "undefined"])]
INPUTS += [("number-" + str(i), '{"stop_hook_active":true,"x":' + value + '}', True)
           for i, value in enumerate(["0", "-0", "1", "-12", "0.1", "-0.2", "1e3",
                                      "1E-3", "1e+3", "1.2e-3"])]


@pytest.mark.parametrize("_name,raw,reentry", INPUTS, ids=[case[0] for case in INPUTS])
def test_shell_preflight_preserves_fail_closed(tmp_path, _name, raw, reentry):
    broken = tmp_path / "broken.py"
    # The fallback cannot masquerade as a successful verdict. The preflight
    # must avoid it only for a proven reentry, independently of Python startup.
    broken.write_text("raise SystemExit(1)\n")
    env = {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
           "MISSION_STATE_PY": str(broken), "MISSION_STATE_TIMEOUT": "3"}
    result = subprocess.run(["/bin/bash", str(HOOK)], input=raw, env=env,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    if reentry:
        assert result.stdout == ""
    else:
        assert json.loads(result.stdout)["decision"] == "block"


@pytest.mark.parametrize("_name,raw,reentry", INPUTS, ids=[case[0] for case in INPUTS])
def test_decorator_preflight_preserves_fail_closed(monkeypatch, capsys, _name, raw, reentry):
    from mission_application.guard_timeout import bounded_by_guard_timeout

    monkeypatch.setenv("MISSION_GUARD_DEADLINE", "0.000001")
    monkeypatch.setenv("MISSION_GUARD_CONTINUATION", "1")
    stream = io.StringIO(raw)
    monkeypatch.setattr(sys, "stdin", stream)

    @bounded_by_guard_timeout
    def cmd_stop_verdict(args):
        pytest.fail("no execution budget: the body must not run")

    cmd_stop_verdict(SimpleNamespace(hook_input="-"))
    assert sys.stdin is stream, "preflight replay is scoped to one command"
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == ("skip" if reentry else "block")
    assert payload["reason"] == ("stop-hook-reentry" if reentry else "guard-budget-exhausted")
    assert payload["command"] == {"kind": "none"}
    if reentry:
        assert payload["shell_text"] == ""
    else:
        assert json.loads(payload["shell_text"])["decision"] == "block"


@pytest.mark.parametrize("route", ["shell", "python"])
@pytest.mark.parametrize("active", [True, False])
@pytest.mark.parametrize("flag", [False, None, True], ids=["false", "missing", "reentry"])
def test_real_guard_keeps_first_call_and_inactive_behavior(tmp_path, route, active, flag):
    if active:
        from .test_stop_hook import _write_session
        _write_session(tmp_path, "cc-own")
    payload = {"cwd": str(tmp_path)}
    if flag is not None:
        payload["stop_hook_active"] = flag
    env = {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
           "CLAUDE_CODE_SESSION_ID": "own"}
    command = (["/bin/bash", str(HOOK)] if route == "shell" else
               [sys.executable, str(ROOT / "skills/mission/bin/mission-state.py"),
                "stop-verdict", "--hook-input", "-", "--json"])
    result = subprocess.run(command, input=json.dumps(payload), env=env, cwd=tmp_path,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    if route == "shell":
        if active and flag is not True:
            assert json.loads(result.stdout)["decision"] == "block"
        else:
            assert result.stdout == ""
    else:
        verdict = json.loads(result.stdout)
        assert verdict["decision"] == ("block" if active and flag is not True else "skip")
        if flag is True:
            assert verdict["reason"] == "stop-hook-reentry"


@pytest.mark.parametrize("output", ["", "not-json", '{}', '{"shell_text":null}'])
def test_shell_invalid_verdict_only_blocks_first_calls(tmp_path, output):
    fake = tmp_path / "invalid.py"
    fake.write_text("print(" + repr(output) + ")\n")
    env = {"PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
           "MISSION_STATE_PY": str(fake)}
    for flag in (False, True):
        result = subprocess.run(["/bin/bash", str(HOOK)],
                                input=json.dumps({"stop_hook_active": flag}), env=env,
                                capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        if flag:
            assert result.stdout == ""
        else:
            assert json.loads(result.stdout)["decision"] == "block"


@pytest.mark.parametrize("raise_error", [False, True])
def test_first_call_stdin_is_replayed_and_restored(monkeypatch, raise_error):
    from mission_application.guard_timeout import bounded_by_guard_timeout, active_deadline

    monkeypatch.delenv("MISSION_GUARD_DEADLINE", raising=False)
    monkeypatch.delenv("MISSION_GUARD_CONTINUATION", raising=False)
    raw = ' \n {"stop_hook_active":false,"cwd":"/project"}\n'
    original = io.StringIO(raw)
    monkeypatch.setattr(sys, "stdin", original)

    @bounded_by_guard_timeout
    def cmd_stop_verdict(args):
        assert sys.stdin.read() == raw
        if raise_error:
            raise ValueError("body failed")
        return "body result"

    if raise_error:
        with pytest.raises(ValueError, match="body failed"):
            cmd_stop_verdict(args=SimpleNamespace(hook_input="-"))
    else:
        assert cmd_stop_verdict(args=SimpleNamespace(hook_input="-")) == "body result"
    assert sys.stdin is original
    assert active_deadline() is None


def test_input_read_timeout_is_a_terminal_block(monkeypatch, capsys):
    from mission_application.guard_timeout import bounded_by_guard_timeout, GuardTimeout, active_deadline

    monkeypatch.delenv("MISSION_GUARD_DEADLINE", raising=False)
    monkeypatch.delenv("MISSION_GUARD_CONTINUATION", raising=False)

    class InterruptedInput:
        def read(self):
            raise GuardTimeout("input did not arrive")

    original = InterruptedInput()
    monkeypatch.setattr(sys, "stdin", original)

    @bounded_by_guard_timeout
    def cmd_stop_verdict(args):
        pytest.fail("unknown input cannot enter the body")

    cmd_stop_verdict(SimpleNamespace(hook_input="-"))
    assert json.loads(capsys.readouterr().out)["decision"] == "block"
    assert sys.stdin is original
    assert active_deadline() is None
