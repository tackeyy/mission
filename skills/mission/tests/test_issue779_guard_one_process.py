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


# Sorted, not the set itself: parametrising over a set gives each process a
# different order, and xdist rejects the run because the workers collected
# different tests.  A full suite would fail every time.
@pytest.mark.parametrize("removed", sorted(EXPECTED_KINDS))
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


def _verdict(root):
    """The decision itself, so a row can assert the branch it reaches."""
    env = {
        **os.environ,
        "CLAUDE_CODE_SESSION_ID": "own",
        "MISSION_STATE_NOW": "2026-08-23T01:00:00Z",
        "MISSION_STOP_GUARD_NOW_EPOCH": "1000",
    }
    env.pop("MISSION_SESSION_ID", None)
    result = subprocess.run(
        [sys.executable, str(STATE_PY), "stop-verdict", "--hook-input", "-", "--json"],
        input=json.dumps({"stop_hook_active": False, "cwd": str(root)}),
        capture_output=True, text=True, env=env, cwd=str(root), timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# Each row is a state, the branch it must reach, and the launch count.
#
# The branch is asserted because the first version asserted only the count:
# three of its seven rows (`lease-expired`, `orphan-pid`, `awaiting-user`) were
# in fact taking the ordinary active path, so the table looked like seven paths
# and exercised four.  A row that does not reach its branch tests nothing that
# the row above it did not.
#
# `stale` settles to `command=none` with the halt already applied -- which is
# the point of #779: the application happened inside the same process, so the
# hook sees a settled verdict rather than an instruction.
_GUARD_PATHS = {
    "missing": (None, "none", "no-eligible-session"),
    "halted": ({"loop_active": False, "halt_reason": "done by hand"}, "none", "halt-reason"),
    "active": ({}, "none", "active-unfinished"),
    "stale": ({"updated_at": "2020-01-01T00:00:00Z"}, "stale", "stale-auto-halt-complete"),
    "awaiting-user": (
        {"updated_at": "2020-01-01T00:00:00Z", "awaiting_user": True},
        "awaiting-user",
        "active-unfinished",
    ),
    "lease-expired": (
        {
            "updated_at": "2020-01-01T00:00:00Z",
            "lease_id": "abc",
            "lease_expires_at": "2020-01-02T00:00:00Z",
            "lease_owner": "other",
        },
        "stale",
        "stale-auto-halt-complete",
    ),
    "orphan": ({"pid": 999999, "updated_at": "2020-01-01T00:00:00Z"}, "stale", "stale-auto-halt-complete"),
}


@pytest.mark.parametrize("path", sorted(_GUARD_PATHS))
def test_the_hook_starts_mission_state_once_for_each_guard_path(tmp_path, path):
    fields, expected_finding, expected_reason = _GUARD_PATHS[path]
    root = tmp_path / "repo"
    root.mkdir()
    if fields is None:
        (root / ".mission-state" / "sessions").mkdir(parents=True)
    else:
        _write_state(root, **fields)

    verdict = _verdict(root)
    assert verdict["finding"] == expected_finding, (path, verdict["reason"])
    assert verdict["reason"] == expected_reason, path

    result, launches = _run_hook(tmp_path, root)

    assert result.returncode == 0, (path, result.stderr)
    assert len(launches) == 1, (path, launches)


def test_the_rows_reach_more_than_one_branch():
    """A control: if every row collapsed onto one branch, the table would still
    pass its per-row assertions while testing a single path seven times."""
    findings = {finding for _fields, finding, _reason in _GUARD_PATHS.values()}
    assert len(findings) >= 3, findings



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


# --- Progress, not a count (#779 review) ------------------------------------
#
# The walk was bounded by a fixed ceiling of 16.  Sixteen distinct orphans
# processed in a row is progress, not a cycle, and the ceiling rejected it --
# failing on exactly the busy host the guard exists to protect.


class _FakeKind:
    def __init__(self, value):
        self.value = value


class _FakeCommand:
    def __init__(self, kind, **fields):
        self.kind = _FakeKind(kind)
        for name, value in fields.items():
            setattr(self, name, value)


class _FakeDecision:
    def __init__(self, command):
        self.command = command


def _walk(decisions, *, limit=64):
    """Drive the loop over a scripted sequence of decisions."""
    from mission_application.guard_application import resolve_with_applications

    remaining = list(decisions)
    applied = []

    def apply(decision):
        applied.append(decision.command)
        return (0, "")

    def receipt(prior, kind, exit_code, stdout, *, request_for_orphan, hook_input):
        return remaining.pop(0)

    import mission_application.guard_application as ga

    original = ga.receipt_decision
    ga.receipt_decision = receipt
    try:
        settled = resolve_with_applications(
            remaining.pop(0),
            appliers={
                "none": ga.no_application,
                "mark-halt": apply,
                "cleanup-stale": apply,
                "stop-guard-observe": apply,
            },
            request_for_orphan=lambda _hook_input: None,
            hook_input={},
            limit=limit,
        )
    finally:
        ga.receipt_decision = original
    return settled, applied


def test_many_distinct_orphans_are_progress_not_a_cycle():
    """Sixteen different subjects in a row must settle, not raise."""
    decisions = [
        _FakeDecision(_FakeCommand("mark-halt", cwd=f"/repo/{index}", session_id=f"s{index}"))
        for index in range(16)
    ]
    decisions.append(_FakeDecision(_FakeCommand("none")))

    settled, applied = _walk(decisions)

    assert settled.command.kind.value == "none"
    assert len(applied) == 16


def test_the_same_command_on_the_same_subject_twice_is_refused():
    """A decision that keeps asking for one thing is a cycle, at any count."""
    repeated = _FakeDecision(_FakeCommand("mark-halt", cwd="/repo", session_id="s"))
    decisions = [repeated, repeated, repeated]

    with pytest.raises(ValueError, match="did-not-settle"):
        _walk(decisions)


def test_a_retried_observation_is_progress():
    """The attempt counter distinguishes a retry from re-issuing the same one."""
    decisions = [
        _FakeDecision(_FakeCommand("stop-guard-observe", session_id="s", attempt=attempt))
        for attempt in (1, 2, 3)
    ]
    decisions.append(_FakeDecision(_FakeCommand("none")))

    settled, applied = _walk(decisions)

    assert settled.command.kind.value == "none"
    assert [command.attempt for command in applied] == [1, 2, 3]


def test_two_orphans_in_one_repository_are_two_subjects():
    """`cwd` alone made them one, and the walk stopped on the second.

    The reviewer's counter-example: same repository, different sessions.  That
    is two orphans processed in turn -- progress -- and taking the first field
    that happened to be set reported it as a cycle.
    """
    decisions = [
        _FakeDecision(_FakeCommand("mark-halt", cwd="/repo", session_id="a")),
        _FakeDecision(_FakeCommand("mark-halt", cwd="/repo", session_id="b")),
        _FakeDecision(_FakeCommand("none")),
    ]

    settled, applied = _walk(decisions)

    assert settled.command.kind.value == "none"
    assert [command.session_id for command in applied] == ["a", "b"]


def test_the_limit_the_hook_uses_is_a_backstop_not_a_ceiling():
    """The fix has to reach the caller, not only the test's own argument.

    The first attempt raised the limit inside the new tests and left
    `_GUARD_APPLICATION_LIMIT` at 16, so the walk the hook actually runs was
    still refused after sixteen distinct orphans.
    """
    module = _module()

    assert module._GUARD_APPLICATION_LIMIT > 1000, (
        "the limit the hook uses still rejects a long run of real progress"
    )
