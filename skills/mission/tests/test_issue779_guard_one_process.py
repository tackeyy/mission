"""Issue #779: the Stop guard applies its closed command set in one process."""

from __future__ import annotations

import ast
import importlib.util
import types
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_PY = REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py"
HOOK = REPO_ROOT / "scripts" / "mission-stop-guard.sh"
EXPECTED_KINDS = {"none", "mark-halt", "cleanup-stale", "stop-guard-observe"}


class _Buffer:
    """A stand-in for `io.StringIO`, so the unit tests need no real stdout."""

    def __init__(self):
        self._text = ""

    def getvalue(self):
        return self._text


class _no_redirect:
    """A stand-in for `contextlib.redirect_stdout`."""

    def __init__(self, _buffer):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def project_root_of_for_test(args):
    from mission_application.guard_application import project_root_of

    return project_root_of(args, lambda: "/the/process/cwd", str)


def _module():
    spec = importlib.util.spec_from_file_location("mission_state_issue779", STATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_the_adapter_has_one_closed_dispatch_table():
    module = _module()

    assert set(module._GUARD_COMMAND_APPLIERS) == EXPECTED_KINDS
    assert len(module._GUARD_COMMAND_APPLIERS) == 4


@pytest.mark.parametrize("removed", EXPECTED_KINDS)
def test_the_closed_dispatch_rejects_a_missing_or_unknown_kind(monkeypatch, removed):
    module = _module()
    table = dict(module._GUARD_COMMAND_APPLIERS)
    del table[removed]
    monkeypatch.setattr(module, "_GUARD_COMMAND_APPLIERS", table)

    with pytest.raises(ValueError, match="guard-command-dispatch-mismatch"):
        module._validate_guard_command_dispatch()

    table = dict(module._GUARD_COMMAND_APPLIERS)
    table["unknown"] = lambda _command: (0, "")
    monkeypatch.setattr(module, "_GUARD_COMMAND_APPLIERS", table)
    with pytest.raises(ValueError, match="guard-command-dispatch-mismatch"):
        module._validate_guard_command_dispatch()


def test_the_dispatch_is_defined_once_and_the_hook_has_no_case_block():
    tree = ast.parse(STATE_PY.read_text(encoding="utf-8"))
    tables = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "_GUARD_COMMAND_APPLIERS" for target in node.targets)
    ]
    assert len(tables) == 1
    # Narrow to branching on the decision's command.  The hook keeps an
    # unrelated `case` that validates the timeout string, and forbidding every
    # `case` would forbid that too -- a check that fails for the wrong reason
    # gets relaxed, and then it stops checking the right one.
    hook = HOOK.read_text(encoding="utf-8")
    assert "COMMAND_KIND" not in hook
    assert "GUARD_DECISION_DISPATCH_BEGIN" not in hook


def test_the_hook_starts_mission_state_once_for_each_guard_path(tmp_path):
    recorder = tmp_path / "calls"
    fake_state = tmp_path / "state.py"
    fake_state.write_text(
        "from pathlib import Path\n"
        "import os\n"
        "Path(os.environ['ISSUE779_RECORDER']).write_text('called\\n', encoding='utf-8')\n"
        "print('{\\\"shell_text\\\": \\\"{\\\\\\\"decision\\\\\\\": \\\\\\\"skip\\\\\\\", \\\\\\\"reason\\\\\\\": \\\\\\\"ok\\\\\\\", \\\\\\\"outcome_kind\\\\\\\": \\\\\\\"expected-gate\\\\\\\"}\\\\n\\\"}')\n",
        encoding="utf-8",
    )
    for path in ("missing", "halted", "active", "stale", "lease-expired", "observe-failed", "multiple-orphans"):
        recorder.unlink(missing_ok=True)
        result = subprocess.run(
            ["/bin/bash", str(HOOK)], input="{}", text=True, capture_output=True,
            env={**__import__("os").environ, "MISSION_STATE_PY": str(fake_state), "ISSUE779_RECORDER": str(recorder)},
        )
        assert result.returncode == 0, (path, result.stderr)
        assert recorder.read_text(encoding="utf-8").splitlines() == ["called"], path


# --- The count that is the point of #779 ------------------------------------
#
# The guard used to spend one process per command: decide, run `mark-halt`,
# decide again from the receipt, and so on, up to seven for one decision.  All
# of them shared one 8-second budget, so on a loaded host the budget went to
# process startup and the guard returned `guard-budget-exhausted` -- five times
# in one session.
#
# The measurement is the launch count, not the duration.  A duration is a
# property of the host's load, and a test written against it fails for reasons
# that have nothing to do with this change (#771 closed exactly that).


def _counting_python(tmp_path):
    """A `python3` that records every launch and then execs the real one."""
    log = tmp_path / "launches.log"
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    shim = shim_dir / "python3"
    shim.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim_dir, log


def _run_hook(tmp_path, state_dir, *, stop_hook_active=False):
    shim_dir, log = _counting_python(tmp_path)
    env = {
        **os.environ,
        "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
        "MISSION_STATE_PY": str(STATE_PY),
        "CLAUDE_CODE_SESSION_ID": "own",
        "MISSION_STATE_NOW": "2026-08-23T01:00:00Z",
        "MISSION_STOP_GUARD_NOW_EPOCH": "1000",
    }
    env.pop("MISSION_SESSION_ID", None)
    result = subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"stop_hook_active": stop_hook_active, "cwd": str(state_dir)}),
        capture_output=True,
        text=True,
        env=env,
        cwd=str(state_dir),
        timeout=120,
    )
    launches = [
        line for line in log.read_text(encoding="utf-8").splitlines()
        if "mission-state.py" in line
    ] if log.exists() else []
    return result, launches


def _write_state(root, **fields):
    sessions = root / ".mission-state" / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    payload = {
        "mission": "m",
        "mission_id": "abcdef12",
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "phase": "executing",
        "iteration": 1,
        "project_root": str(root),
        "updated_at": "2026-08-23T00:00:00Z",
        "pid": 1,
    }
    payload.update(fields)
    path = sessions / "cc-own.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_an_active_session_costs_one_launch(tmp_path):
    """The path that used to cost three: decide, observe, decide again."""
    root = tmp_path / "repo"
    root.mkdir()
    _write_state(root)

    result, launches = _run_hook(tmp_path, root)

    assert result.returncode == 0, result.stderr
    assert len(launches) == 1, launches


def test_a_session_with_no_state_costs_one_launch(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".mission-state" / "sessions").mkdir(parents=True)

    result, launches = _run_hook(tmp_path, root)

    assert result.returncode == 0, result.stderr
    assert len(launches) == 1, launches


def test_a_halted_session_costs_one_launch(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _write_state(root, loop_active=False, halt_reason="done by hand")

    result, launches = _run_hook(tmp_path, root)

    assert result.returncode == 0, result.stderr
    assert len(launches) == 1, launches


# --- Condition 5b: the budget's exhaustion is not a failed command ----------
#
# `GuardTimeout` is a `BaseException` raised by the alarm so the outermost
# decorator can emit `guard-budget-exhausted`.  Converting it into a receipt
# would resolve it as an ordinary command failure: the terminal reason would
# change, and a fired alarm would be swallowed, so a retry could run unbounded.


def test_the_budget_running_out_is_not_turned_into_a_receipt():
    from mission_application.guard_application import capture_application
    from mission_application.guard_timeout import GuardTimeout

    def apply(_args):
        raise GuardTimeout("budget spent")

    with pytest.raises(GuardTimeout):
        capture_application(
            apply, object(), redirect_stdout=_no_redirect, buffer_factory=_Buffer
        )


def test_a_command_failing_on_its_own_terms_is_a_receipt():
    """The other half: without this, refusing to catch anything would pass."""
    from mission_application.guard_application import capture_application

    def apply(_args):
        raise SystemExit(2)

    assert capture_application(
        apply, object(), redirect_stdout=_no_redirect, buffer_factory=_Buffer
    ) == (2, "")


def test_the_appliers_call_the_command_without_its_own_budget():
    """A nested bound reports exhaustion as `SystemExit(2)` -- a failed receipt.

    The budget belongs to `cmd_stop_verdict`; applying through the decorated
    command would put a second one inside it, and `_report_exhaustion` turns
    that into an exit for every command except `stop-verdict`.
    """
    source = STATE_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    appliers = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("_guard_apply_")
    ]
    assert appliers, "the appliers were renamed"
    for applier in appliers:
        unwrapped = [
            node for node in ast.walk(applier)
            if isinstance(node, ast.Attribute) and node.attr == "__wrapped__"
        ]
        assert unwrapped, f"{applier.name} applies through the decorated command"


# --- Condition 6: the command's own root and session, not the process's -----


def test_the_root_comes_from_the_command_not_the_process():
    """`os.chdir` was rejected by the design: it would move ~40 other lookups.

    Passing the root is what replaces it, so a command that names a root must
    reach that root even when the process sits somewhere else entirely.
    """
    from mission_application.guard_application import project_root_of

    args = types.SimpleNamespace(guard_project_root="/named/by/command")
    assert project_root_of(args, lambda: "/the/process/cwd", str) == "/named/by/command"


def test_the_root_falls_back_to_the_process_for_the_cli():
    args = types.SimpleNamespace()
    assert project_root_of_for_test(args) == "/the/process/cwd"


def test_the_session_comes_from_the_command_not_the_environment():
    """`MISSION_SESSION_ID` is the guard's own session, not the one being halted.

    Writing it into the process was rejected for that reason: a lost restore
    changes which session the *next* decision treats as its own.
    """
    from mission_application.guard_application import state_file_of

    args = types.SimpleNamespace(guard_session_id="cc-target")
    seen = {}

    def session_file(root, session_id):
        seen["root"], seen["session_id"] = root, session_id
        return "state-file"

    def resolve_for_process(_root):
        raise AssertionError("the environment was consulted for a named session")

    assert state_file_of(args, "/root", session_file, resolve_for_process) == "state-file"
    assert seen == {"root": "/root", "session_id": "cc-target"}


def test_the_session_falls_back_to_the_environment_for_the_cli():
    from mission_application.guard_application import state_file_of

    args = types.SimpleNamespace()
    assert state_file_of(
        args, "/root", lambda *_: "named", lambda root: f"process:{root}"
    ) == "process:/root"
