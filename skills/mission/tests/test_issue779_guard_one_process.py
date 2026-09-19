"""Issue #779: the Stop guard applies its closed command set in one process."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import types
import json
import os
import re
import shlex
import stat
import subprocess
import sys
import time
from typing import NamedTuple
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


_GUARD_ENV = {
    "CLAUDE_CODE_SESSION_ID": "own",
    "MISSION_STATE_NOW": "2026-08-23T01:00:00Z",
    "MISSION_STOP_GUARD_NOW_EPOCH": "1000",
    "MISSION_SESSION_ID": None,
}


def _apply_env(env, overrides=None):
    """Apply an override map in place; `None` removes the variable.

    Removal has to be expressible: the orphan branch is only reachable with no
    hook session id at all, and an empty string is not the same as unset.
    """
    for name, value in {**_GUARD_ENV, **(overrides or {})}.items():
        if value is None:
            env.pop(name, None)
        else:
            env[name] = value
    return env


class _Row(NamedTuple):
    """One guard path: the state, the branch, and what the branch leaves."""

    fields: dict | None
    finding: str
    reason: str
    halt: str | None = None
    orphan_processed: bool = False
    env: dict | None = None


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


def _session_members(sid):
    """Every live process in session `sid`, whatever its process group."""
    listing = subprocess.run(["ps", "-A", "-o", "pid="],
                             capture_output=True, text=True, check=True)
    members = []
    for word in listing.stdout.split():
        pid = int(word)
        try:
            if os.getsid(pid) == sid:
                members.append(pid)
        except (ProcessLookupError, PermissionError):
            continue
    return members


def _wait_for_session(sid, *, timeout=60.0):
    """Block until no process is left in the hook's session.

    Waiting for the hook process alone reads the log while its children are
    still on their way to the CLI, and three narrower waits were each walked
    around in review:

    - `wait` inside the hook waits for direct jobs only, so
      `( ( ... & ) & )` hands the work to a grandchild and returns (round 3)
    - the hook's *process group* holds those, but `set -m` turns job control
      on and puts each background job in a group of its own (round 4)

    A session holds all of them.  It is inherited across `fork` and survives
    the death of every intermediate process, and `set -m` does not change it.
    `start_new_session` makes the hook a session leader, so the id is its pid
    and no unrelated process can satisfy the wait.

    A descendant that calls `setsid` leaves the session and is not waited
    for.  Nothing here does, and catching that belongs to the recorder rather
    than the harness -- which is where #779 put it, and why the recorder sits
    in the CLI instead of at `python3`.
    """
    deadline = time.monotonic() + timeout
    while True:
        members = _session_members(sid)
        if not members:
            return
        if time.monotonic() > deadline:
            raise AssertionError(
                f"session {sid} still held {members} after {timeout}s")
        time.sleep(0.02)


def _run_hook(tmp_path, state_dir, *, stop_hook_active=False, env_overrides=None,
              hook=HOOK):
    shim_dir, log = _counting_python(tmp_path)
    env = {**os.environ, **_GUARD_ENV, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
           "MISSION_STATE_PY": str(STATE_PY)}
    _apply_env(env, env_overrides)
    # `start_new_session` gives the hook a session of its own, so its id is
    # the hook's pid and no unrelated process can satisfy the wait below.
    with subprocess.Popen(
        ["bash", str(hook)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(state_dir),
        start_new_session=True,
    ) as process:
        sid = process.pid
        stdout, stderr = process.communicate(
            json.dumps({"stop_hook_active": stop_hook_active, "cwd": str(state_dir)}),
            timeout=120,
        )
    result = subprocess.CompletedProcess(
        process.args, process.returncode, stdout, stderr)
    _wait_for_session(sid)
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


def _verdict(root, env_overrides=None):
    """The decision itself, so a row can assert the branch it reaches.

    This resolves the decision *and applies its command*, which rewrites the
    state it just read.  So it must never run against the state the launch
    count is measured on: the first version shared one directory, the halt
    landed before the hook started, and every row measured the settled state
    instead of the path named in its name.  Callers build a second directory.
    """
    env = {**os.environ, **_GUARD_ENV}
    _apply_env(env, env_overrides)
    result = subprocess.run(
        [sys.executable, str(STATE_PY), "stop-verdict", "--hook-input", "-", "--json"],
        input=json.dumps({"stop_hook_active": False, "cwd": str(root)}),
        capture_output=True, text=True, env=env, cwd=str(root), timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# A lease is only read when all four of its fields are present and well typed
# (`authoritative_reader.py`): owner, id, a non-negative integer epoch, and an
# expiry.  Writing the expiry alone leaves the lease `absent`, which is how the
# first version's `lease-expired` row silently took the ordinary stale path.
def _lease(expires_at):
    return {
        "owner_session_id": "cc-own",
        "lease_id": "L1",
        "fencing_epoch": 1,
        "lease_expires_at": expires_at,
    }


# The orphan branch requires *no* hook session at all: with a session id it is
# the session's own state, and with neither id nor pid override the guard falls
# back to the process pid and reports `pid-owner-mismatch`.  Both have to go.
_NO_HOOK_SESSION = {"CLAUDE_CODE_SESSION_ID": None, "MISSION_HOOK_AGENT_PID": "0"}


# Each row is a state, the branch it must reach, and the launch count.
#
# `finding` and `reason` alone do not separate the rows -- `missing` and
# `orphan` both settle to `none` / `no-eligible-session`, because processing
# the orphan removes the only candidate.  So a row also says what it left
# behind: the halt it wrote, and whether an orphan was processed.  Without
# that, two rows on different paths look identical and the control below
# cannot tell a real table from one that collapsed.
#
# `stale` settles to `finding=stale` with the halt already applied -- which is
# the point of #779: the application happened inside the same process, so the
# hook sees a settled verdict rather than an instruction.
_GUARD_PATHS = {
    "missing": _Row(None, "none", "no-eligible-session"),
    "halted": _Row(
        {"loop_active": False, "halt_reason": "done by hand"},
        "none", "halt-reason", halt="done by hand",
    ),
    "active": _Row({}, "none", "active-unfinished"),
    "stale": _Row(
        {"updated_at": "2020-01-01T00:00:00Z"},
        "stale", "stale-auto-halt-complete", halt="stale: auto-halted",
    ),
    "awaiting-user": _Row(
        {"updated_at": "2020-01-01T00:00:00Z", "awaiting_user": True},
        "awaiting-user", "active-unfinished",
    ),
    "lease-expired": _Row(
        {"updated_at": "2020-01-01T00:00:00Z", **_lease("2020-01-02T00:00:00Z")},
        "lease-expired", "stale-auto-halt-complete", halt="(cleanup-stale)",
    ),
    "lease-held": _Row(
        {"updated_at": "2020-01-01T00:00:00Z", **_lease("2099-01-02T00:00:00Z")},
        "stale", "active-unfinished",
    ),
    "orphan": _Row(
        {"pid": 999999, "updated_at": "2020-01-01T00:00:00Z"},
        "none", "no-eligible-session",
        halt="orphan: pid 999999 dead", orphan_processed=True,
        env=_NO_HOOK_SESSION,
    ),
}


def _populate(root, fields):
    root.mkdir()
    if fields is None:
        (root / ".mission-state" / "sessions").mkdir(parents=True)
    else:
        _write_state(root, **fields)


@pytest.mark.parametrize("path", sorted(_GUARD_PATHS))
def test_the_hook_starts_mission_state_once_for_each_guard_path(tmp_path, path):
    row = _GUARD_PATHS[path]

    # Two directories, because asserting the branch changes the state.
    judged = tmp_path / "judged"
    _populate(judged, row.fields)
    measured = tmp_path / "measured"
    _populate(measured, row.fields)

    verdict = _verdict(judged, row.env)
    assert verdict["finding"] == row.finding, (path, verdict["reason"])
    assert verdict["reason"] == row.reason, path
    processed = verdict["continuation"]["processed_orphan_state_files"]
    assert bool(processed) is row.orphan_processed, (path, processed)

    state_file = judged / ".mission-state" / "sessions" / "cc-own.json"
    halt = (
        json.loads(state_file.read_text(encoding="utf-8")).get("halt_reason")
        if state_file.exists() else None
    )
    if row.halt is None:
        assert not halt, (path, halt)
    else:
        assert halt and row.halt in halt, (path, halt)

    # The measured state must still be the one the row describes.  Sharing
    # one directory with the assertion above would hand the hook a state the
    # decision had already settled, and every row would measure that instead
    # of its own path -- which is what the first version did.
    measured_file = measured / ".mission-state" / "sessions" / "cc-own.json"
    if row.fields is not None:
        assert measured_file.exists(), path
        before = json.loads(measured_file.read_text(encoding="utf-8"))
        assert before.get("halt_reason", "") == row.fields.get("halt_reason", ""), path

    result, launches = _run_hook(tmp_path, measured, env_overrides=row.env)

    assert result.returncode == 0, (path, result.stderr)
    assert len(launches) == 1, (path, launches)


def test_the_rows_reach_more_than_one_branch():
    """A control: if every row collapsed onto one branch, the table would still
    pass its per-row assertions while testing a single path many times.

    Counting distinct findings is not enough -- eight rows can share three
    findings and still be six copies of one path.  Every row has to differ from
    every other in the whole outcome it claims.
    """
    outcomes = [
        (row.finding, row.reason, row.halt, row.orphan_processed)
        for row in _GUARD_PATHS.values()
    ]
    assert len(set(outcomes)) == len(_GUARD_PATHS), outcomes
    assert len({row.finding for row in _GUARD_PATHS.values()}) >= 4, outcomes



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


# --- #796: the count is measured by running, not by reading the shell --------
#
# The static check in `test_issue615_guard_decision.py` reads the hook and
# decides whether it can reach the CLI more than once.  Five review rounds
# found five shells that it read as harmless and that ran the wrapper anyway
# -- a quoted fragment of a name, a call through a variable, an alias, `set --`
# before a forward, a call inside `$( )`.  Each fix moved the reading closer to
# a shell lexer, and the next round found the next one (#796).
#
# Running the hook settles it without reading anything: the shim below records
# every `mission-state.py` launch, so the count and the subcommand are
# observed rather than inferred.  The static check keeps what only it can do --
# refusing a shape before anyone runs it, and the policy rules (no arithmetic,
# no numeric thresholds, no dynamic execution) -- and stops being the thing
# that has to understand bash.


def _recording_state_py(tmp_path):
    """A `mission-state.py` that records its own argv, then runs the real one.

    Recording at `python3` instead was the first shape of this measurement, and
    it observed **the name of an interpreter on PATH**, not the CLI: a second
    call written as `python3.14 "$MISSION_STATE_PY" stop-verdict` reached the
    CLI, returned a real verdict, and was not recorded (#796 review round 6).
    Any other spelling -- `python3.13`, a venv path, `exec` -- would do the
    same, and enumerating them is the same losing game as reading the shell.

    Recording at the entry point ends it: whatever runs this file is counted,
    because the count is taken inside it.
    """
    log = tmp_path / "invocations.log"
    snapshots = tmp_path / "snapshots.log"
    shim = tmp_path / "mission-state.py"
    shim.write_text(
        "import hashlib, json, os, runpy, stat, sys\n"
        f"_snapshots = {str(snapshots)!r}\n"
        # The same reading as `_state_tree`, kept to digests because nothing
        # here compares contents -- only whether they stayed the same.  A
        # non-regular file is named by its kind rather than opened: a FIFO has
        # no writer, so reading one would block for ever.
        "def _tree(root):\n"
        "    out = {}\n"
        "    for base, dirs, files in os.walk(root, followlinks=False):\n"
        "        for name in dirs + files:\n"
        "            path = os.path.join(base, name)\n"
        "            rel = os.path.relpath(path, root)\n"
        "            mode = os.lstat(path).st_mode\n"
        "            if stat.S_ISLNK(mode):\n"
        "                out[rel] = ['symlink', mode, os.readlink(path)]\n"
        "            elif stat.S_ISDIR(mode):\n"
        "                out[rel] = ['dir', mode, None]\n"
        "            elif stat.S_ISREG(mode):\n"
        "                with open(path, 'rb') as handle:\n"
        "                    out[rel] = ['file', mode,\n"
        "                                hashlib.sha256(handle.read()).hexdigest()]\n"
        "            else:\n"
        "                out[rel] = ['other', mode, None]\n"
        "    return out\n"
        # The hook runs with the repository as its working directory, which is
        # how it finds the state at all, so that is the root to read.
        "def _record(phase):\n"
        "    with open(_snapshots, 'a') as handle:\n"
        "        handle.write(\n"
        "            json.dumps({'phase': phase, 'tree': _tree(os.getcwd())}) + '\\n')\n"
        # One JSON line per invocation, argv and all.  Writing the arguments
        # as text loses the invocation that has none: an empty line and no
        # line read the same, so a second process started without arguments
        # disappeared from the count (#796 cross-model review round 1).
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"sys.argv[0] = {str(STATE_PY)!r}\n"
        # The state as the CLI found it and as the CLI left it.  Both are
        # taken inside the one process that is allowed to write, so the run's
        # own lease appears in each and the comparison needs no normalising
        # -- which is what let a lease rewrite through (#802).
        "_record('entry')\n"
        "try:\n"
        f"    runpy.run_path({str(STATE_PY)!r}, run_name='__main__')\n"
        "finally:\n"
        # The CLI leaves through `sys.exit`, so this cannot be written after
        # the call: `SystemExit` would carry straight past it.
        "    _record('exit')\n",
        encoding="utf-8",
    )
    return shim, log, snapshots


def _invocations(log):
    """The argv of each recorded invocation, one entry per process."""
    if not log.exists():
        return []
    return [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _subcommands(log):
    """The first argument of each recorded invocation.

    An invocation with no arguments is reported as ``""`` rather than dropped:
    the count of processes is what #779 is about, and a process that ran
    without arguments still ran.
    """
    return [argv[0] if argv else "" for argv in _invocations(log)]


def _hook_with(tmp_path, inserted):
    """A copy of the hook with `inserted` added after its last `fi`."""
    source = HOOK.read_text(encoding="utf-8")
    marker = "\nfi\n"
    at = source.rindex(marker) + len(marker)
    path = tmp_path / "hook-under-test.sh"
    path.write_text(source[:at] + inserted + "\n" + source[at:], encoding="utf-8")
    return path


def _state_tree(root):
    """Every node under the state directory: kind, mode, and what it holds.

    Reading only the bytes of regular files called three real changes
    "unchanged" (#796 review round 7): a `chmod`, a new empty directory, and a
    symlink put in place of a file with the same contents behind it.  The last
    one matters most -- it points later writes outside `.mission-state` while
    every byte read through it stays the same.

    `lstat` is what makes the symlink visible: `read_bytes` follows it and sees
    the target, so the swap is invisible to anything that reads through.
    """
    tree = {}
    for path in sorted(root.rglob("*")):
        status = path.lstat()
        name = str(path.relative_to(root))
        if path.is_symlink():
            tree[name] = ("symlink", status.st_mode, os.readlink(path))
        elif path.is_dir():
            tree[name] = ("dir", status.st_mode, None)
        elif stat.S_ISREG(status.st_mode):
            tree[name] = ("file", status.st_mode, path.read_bytes())
        else:
            # Named by kind rather than opened.  A FIFO has no writer here, so
            # reading one would block for ever and the run would end in a hang
            # instead of a report -- and the recorder reads the same tree, so
            # the two would disagree about what they are comparing (#801
            # Checker, Low 3).
            tree[name] = ("other", status.st_mode, None)
    return tree


def _run_shapes_hook(tmp_path, inserted, *, state=None):
    """Run a hook and return (subcommands, state left alone, hook exit code).

    The second value is a separate observation, because the first depends on
    the CLI being reached through `$MISSION_STATE_PY`.  A hook that wrote the
    state directly would leave the count at one while changing what the count
    exists to protect.

    The third tells "the shape ran and was not detected" from "the shape did
    not run".  Those look the same from the first two: an inserted line that
    dies takes the count and the state with it, and the row then passes -- or
    fails naming the wrong symptom.  CI showed both, when two shapes named a
    `python3.14` and a `$TMPDIR` that exist here and not there.

    `state` picks which guard path the hook takes.  Leaving it out gives an
    empty `sessions/`, which settles to `no-eligible-session` -- and a hook
    that calls the CLI again *depending on the finding* is invisible there
    (#796 checker round 1).  That is where the loop of #779 lived, so it has
    to be reachable here.
    """
    root = tmp_path / "repo"
    (root / ".mission-state" / "sessions").mkdir(parents=True)
    if state is not None:
        _write_state(root, **state)
    # Seeded so the comparison can see a file *change*, not only appear.
    # Comparing the names alone would pass a hook that rewrote what is already
    # there, which is the more likely way for this to go wrong.
    (root / ".mission-state" / "marker").write_text("seeded\n", encoding="utf-8")
    # A symlink is seeded too, so the comparison can be asked about a change
    # that keeps the kind and the mode and moves only the target.
    (root / ".mission-state" / "link").symlink_to("marker")
    before = _state_tree(root)
    shim, log, _snapshots = _recording_state_py(tmp_path)
    hook = _hook_with(tmp_path, inserted)
    result, _launches = _run_hook(
        tmp_path, root, hook=hook,
        env_overrides={
            "MISSION_STATE_PY": str(shim),
            # Named here rather than written into the shapes: `python3.14` and
            # `$TMPDIR` exist on the machine this was written on and not on the
            # CI runner, so shapes that used them ran nothing there and the
            # rows passed for the wrong reason.  Both are properties of the
            # environment, so the environment supplies them.
            "MISSION_TEST_PYTHON": sys.executable,
            "MISSION_TEST_OUTSIDE": str(tmp_path / "outside-target"),
        },
    )
    return _subcommands(log), _state_tree(root) == before, result.returncode


_LEASE_ID_RE = re.compile(rb'"lease_id":\s*"[0-9a-f]+"')


class _HookRun(NamedTuple):
    """One run of the hook, and the five things the tests ask about it."""

    tree: dict
    subcommands: list
    returncode: int
    outside_the_cli: list
    #: The recorder's own edges, in order.  A call killed part way through
    #: leaves fewer of them, and without reading this an interrupted run looks
    #: clean: nothing was recorded, so the whole run is one gap, and a CLI that
    #: never got to write leaves that gap empty.
    phases: list


def _recorded(snapshots):
    """The recorder's snapshots, in the order it took them."""
    if not snapshots.exists():
        return []
    return [
        json.loads(line)
        for line in snapshots.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _digest_tree(tree):
    """`_state_tree` in the form the recorder writes: contents as digests."""
    return {
        name: [kind, mode,
               hashlib.sha256(payload).hexdigest() if kind == "file" else payload]
        for name, (kind, mode, payload) in tree.items()
    }


def _writes_outside_the_cli(before, after, snapshots):
    """State changes that happened while the CLI was not running.

    The tree comparison folds the values that differ between runs by
    construction -- the temporary root and the lease -- and folding the lease
    hid a hook that rewrote the fencing token and nothing else (#802).  It
    could not be kept raw: two runs take two leases, so every comparison would
    report a difference.

    Reading it inside the run removes the need to fold anything.  The recorder
    takes the tree as the CLI finds it and as the CLI leaves it, so both
    snapshots carry *this* run's lease, and the gaps around them are compared
    byte for byte:

        test before  ==  first entry      nothing written before the CLI ran
        exit N       ==  entry N+1        nothing written between two calls
        last exit    ==  test after       nothing written after the CLI left

    Every write the CLI itself makes falls inside a call, so it never appears
    here; every write the hook makes falls between them, so it always does.

    With no call recorded there is nothing to bracket, and the whole run is
    one gap.
    """
    recorded = _recorded(snapshots)
    edges = [("before the first call", _digest_tree(before))]
    edges += [(entry["phase"], entry["tree"]) for entry in recorded]
    edges += [("after the last call", _digest_tree(after))]

    differences = []
    for (left_name, left), (right_name, right) in zip(edges, edges[1:]):
        # Consecutive edges that belong to the same call are the call itself,
        # and the CLI is the one process allowed to write.
        if (left_name, right_name) == ("entry", "exit"):
            continue
        for name, was, became in _tree_difference(left, right):
            differences.append((f"{left_name} -> {right_name}", name, was, became))
    return differences


def _tree_difference(left, right):
    """What the two trees disagree about, named entry by entry."""
    return sorted(
        (name, left.get(name), right.get(name))
        for name in set(left) | set(right)
        if left.get(name) != right.get(name)
    )


def _normalised_state_tree(root):
    """The state tree with the values that name *this run* folded away.

    Two runs live under different temporary roots and take a fresh lease, so
    comparing the bytes as they stand reports a difference for every run.
    Those two are the only values that differ by construction -- measured by
    running the same path twice and reading what disagreed -- and everything
    else is compared as it is, because noticing what the hook did is the point.

    **Folding the lease already hides one write**: a hook that swaps the
    fencing token and leaves every other byte alone normalises to the
    baseline's tree (#802).  Nothing here can see it, because the value it
    would have to compare against is different in the two runs by
    construction.  `_writes_outside_the_cli` reads that inside one run
    instead, where no folding is needed.

    So this is not the whole comparison, and folding away more than these two
    would widen a hole that is already there.
    """
    tree = _state_tree(root)
    normalised = {}
    for name, (kind, mode, payload) in tree.items():
        if kind == "file" and isinstance(payload, bytes):
            payload = payload.replace(str(root).encode("utf-8"), b"<root>")
            payload = _LEASE_ID_RE.sub(b'"lease_id": "<lease>"', payload)
        elif kind == "symlink" and isinstance(payload, str):
            payload = payload.replace(str(root), "<root>")
        normalised[name] = (kind, mode, payload)
    return normalised


def _tree_after_shapes_hook(tmp_path, inserted, *, state=None, seed=None):
    """Run a hook and return the state tree it leaves behind.

    The comparison in ``_run_shapes_hook`` answers "did anything change", which
    only decides anything where nothing is supposed to: on a path whose verdict
    writes -- ``stale`` resolves to a halt -- everything changes and the answer
    carries nothing.  A write the hook does *itself* hides in that change
    (#796 cross-model review round 1), and a hook that writes directly keeps
    its process count at one, so the count does not see it either.

    Returning the tree lets a path be compared against the same path without
    the inserted line: what ``stop-verdict`` writes on its own is the baseline,
    and anything beyond it came from the hook.
    """
    root = tmp_path / "repo"
    (root / ".mission-state" / "sessions").mkdir(parents=True)
    if state is not None:
        _write_state(root, **state)
    if seed is not None:
        seed(root / ".mission-state")
    before = _state_tree(root)
    shim, log, snapshots = _recording_state_py(tmp_path)
    hook = _hook_with(tmp_path, inserted)
    result, _launches = _run_hook(
        tmp_path, root, hook=hook,
        env_overrides={
            "MISSION_STATE_PY": str(shim),
            "MISSION_TEST_PYTHON": sys.executable,
            "MISSION_TEST_OUTSIDE": str(tmp_path / "outside-target"),
        },
    )
    return _HookRun(
        tree=_normalised_state_tree(root),
        subcommands=_subcommands(log),
        returncode=result.returncode,
        outside_the_cli=_writes_outside_the_cli(before, _state_tree(root), snapshots),
        phases=[entry["phase"] for entry in _recorded(snapshots)],
    )


def test_a_missing_log_reads_as_no_calls_not_as_an_error(tmp_path):
    """Whether the CLI ran at all has to be answerable.

    The log only exists once something has written to it, so "nothing ran" and
    "the recorder is not there" reach the parsing the same way.  Returning an
    empty list keeps the caller's assertion meaningful -- the control expects
    exactly `["stop-verdict"]`, so an empty result fails it rather than
    slipping through as "no violations found".
    """
    assert _subcommands(tmp_path / "absent.log") == []
    assert _invocations(tmp_path / "absent.log") == []

    log = tmp_path / "invocations.log"
    log.write_text(
        '["stop-verdict", "--hook-input", "-", "--json"]\n[]\n["resume"]\n',
        encoding="utf-8",
    )

    # The middle entry is a process started with no arguments.  It has to
    # count: the claim of #779 is about how many processes run, and reading
    # the log as text dropped that one silently (cross-model review round 1).
    assert _subcommands(log) == ["stop-verdict", "", "resume"]
    assert len(_invocations(log)) == 3


def test_the_hook_names_the_cli_only_through_the_variable():
    """The measurement records what goes through `$MISSION_STATE_PY`.

    That is a real limit: a call written against the file's own path would run
    the CLI without passing the recorder, and the count would stay at one
    (#796 review round 7).  Rather than chase it at run time -- the same losing
    game as enumerating interpreter names -- the shape is forbidden outright.

    This is a string check, not a reading of the shell: the name may appear
    only where the variable gets its default.  Whatever a future hook does
    with the CLI, it has to go through the variable to name it at all, and
    then the recorder sees it.
    """
    naming = [
        line for line in HOOK.read_text(encoding="utf-8").splitlines()
        if "mission-state.py" in line and not line.lstrip().startswith("#")
    ]

    assert naming == [
        'MISSION_STATE_PY="${MISSION_STATE_PY:-$SCRIPT_DIR/../skills/mission/bin/'
        'mission-state.py}"'
    ], naming


def test_running_the_hook_shows_one_call_and_which_one(tmp_path):
    """The guarantee, measured: one launch, and it asks for `stop-verdict`."""
    subcommands, untouched, code = _run_shapes_hook(tmp_path, "")

    assert code == 0, code
    assert subcommands == ["stop-verdict"]
    assert untouched, "the guard changed the state it only had to read"


# Each shape was run under bash with a stub wrapper before being listed here:
# all five actually reach the CLI, which is why the static check reading them
# as harmless mattered.  They are kept as the demonstration that running the
# hook detects what reading it did not -- a measurement nobody has shown to
# separate the two cases is not yet an instrument.
_BYPASS_SHAPES = {
    "a name containing a hyphen":
        'function _q-x { python3 "$MISSION_STATE_PY" "$@"; }\n_q-x resume',
    "a call whose name is quoted":
        "'_mission_state_bounded' resume",
    "a name quoted in the middle":
        "_mission_state_'bounded' resume",
    "a call through a variable":
        '_q=_mission_state_bounded\n"$_q" resume',
    "a call through an alias":
        "shopt -s expand_aliases\nalias _al=_mission_state_bounded\n_al resume",
    "positional parameters replaced before forwarding":
        'set -- resume\n_mission_state_bounded "$@"',
    "a call inside a command substitution":
        "x=$(_mission_state_bounded resume)",
    "a call inside backticks":
        "x=`_mission_state_bounded resume`",
    "an operator inside a quoted substitution":
        'x="$(true;_mission_state_bounded resume)"',
    "a call after a semicolon with no space":
        "true;_mission_state_bounded resume",
    "a brace inside a quoted string before the call":
        'printf "%s" "(";_mission_state_bounded resume',
    # Recording at `python3` missed both of these: the first names another
    # interpreter, the second replaces the process.  Neither is exotic, and
    # enumerating interpreter names would be the same losing game as reading
    # the shell -- which is why the recorder sits in the CLI instead.
    "a second call through another interpreter":
        '_state=$MISSION_STATE_PY\n'
        'printf \'%s\' "$INPUT" | "$MISSION_TEST_PYTHON" "$_state" stop-verdict'
        ' --hook-input - --json >/dev/null',
    "a call that replaces the process":
        '( exec "$MISSION_TEST_PYTHON" "$MISSION_STATE_PY" resume'
        ' >/dev/null 2>&1 ) || true',
    # Reading the log as text dropped this one: an invocation with no
    # arguments wrote an empty line, and an empty line and no line are the
    # same text (cross-model review round 1).  The process still ran.
    "a second call with no arguments at all":
        '_state=$MISSION_STATE_PY\n'
        '"$MISSION_TEST_PYTHON" "$_state" >/dev/null 2>&1 || true',
    # Measuring the moment the hook returns missed this one: the second call
    # is launched into the background and reaches the CLI afterwards
    # (cross-model review round 2).  The harness waits for the hook's session,
    # so the log is read after they have run.
    "a second call launched into the background":
        '_q=_mission_state_bounded\n'
        '( sleep 1; "$_q" resume ) >/dev/null 2>&1 &',
    # Waiting for the hook's own jobs was not enough: the outer subshell
    # starts the inner one and exits, so `wait` returns with the work still
    # ahead of it (cross-model review round 3).  The session outlives both.
    "a second call handed to a grandchild":
        '_q=_mission_state_bounded\n'
        '( ( sleep 1; "$_q" resume ) >/dev/null 2>&1 & ) &',
    # Waiting for the hook's process group was not enough either: job
    # control puts each background job in a group of its own, so the group
    # the hook started with empties while the job runs (round 4).  The
    # session is the same one, which is what the harness waits for.
    "a second call backgrounded with job control on":
        'set -m\n'
        '_q=_mission_state_bounded\n'
        '( sleep 1; "$_q" resume ) >/dev/null 2>&1 &',
}


@pytest.mark.parametrize("label", sorted(_BYPASS_SHAPES))
def test_running_the_hook_catches_what_reading_it_missed(tmp_path, label):
    subcommands, _untouched, code = _run_shapes_hook(tmp_path, _BYPASS_SHAPES[label])

    # A shape that failed to run takes the count with it, and the row would
    # then fail naming the measurement instead of the shape.
    assert code == 0, (label, code)

    # `!= ["stop-verdict"]` would also pass on an empty list, which is what a
    # broken harness produces: the shape would look detected because nothing
    # ran at all.  Name what the shape does instead.
    # `!= ["stop-verdict"]` alone also passes on an empty list, which is what a
    # broken recorder produces -- every row would then report success for the
    # wrong reason.  These two say it directly: the hook's own call happened,
    # and the shape added another.  The slice is empty-safe on purpose.
    assert subcommands[:1] == ["stop-verdict"], (label, subcommands)
    assert len(subcommands) > 1, (label, subcommands)


# Changes that leave every byte readable through the tree the same.  Reading
# only the contents of regular files called all three "unchanged".
_SILENT_STATE_CHANGES = {
    "a mode change": 'chmod 0444 "$PWD/.mission-state/marker"',
    "a new empty directory": 'mkdir -p "$PWD/.mission-state/created-empty"',
    # The one with teeth: later writes through this name land outside the
    # state directory, while anything that reads through it sees what it
    # always saw.
    # Same kind, same mode: only the target moves.  Nothing but reading the
    # target tells these apart, and where the target points decides where a
    # later write lands.
    "a symlink repointed outside the state directory":
        'ln -sfn "$MISSION_TEST_OUTSIDE" "$PWD/.mission-state/link"',
    # `stat` follows the link and raises on this one, which would end the test
    # in an error instead of a report.  `lstat` describes the link itself.
    "a dangling symlink":
        'ln -s /nonexistent/target "$PWD/.mission-state/dangling"',
    "a symlink in place of a file with the same contents":
        'cp "$PWD/.mission-state/marker" "$MISSION_TEST_OUTSIDE"'
        ' && rm "$PWD/.mission-state/marker"'
        ' && ln -s "$MISSION_TEST_OUTSIDE" "$PWD/.mission-state/marker"',
}


@pytest.mark.parametrize("label", sorted(_SILENT_STATE_CHANGES))
def test_a_change_that_reads_the_same_is_still_a_change(tmp_path, label):
    subcommands, untouched, code = _run_shapes_hook(
        tmp_path, _SILENT_STATE_CHANGES[label]
    )

    assert code == 0, (label, code)
    assert subcommands == ["stop-verdict"], (label, subcommands)
    assert not untouched, label


def test_rewriting_an_existing_state_file_is_seen(tmp_path):
    """Names are not enough; the comparison has to look at the bytes.

    A file that appears is easy to notice.  A file that is already there and
    comes back different is the shape a guard would actually produce, and
    comparing the listing alone would call it unchanged.
    """
    subcommands, untouched, code = _run_shapes_hook(
        tmp_path, 'printf \'rewritten\' > "$PWD/.mission-state/marker"'
    )

    assert code == 0, code
    assert subcommands == ["stop-verdict"], subcommands
    assert not untouched


def test_writing_the_state_directly_is_seen_even_though_the_count_is_one(tmp_path):
    """The count protects the state; something has to watch the state itself.

    A hook that wrote a session file would reach the CLI once and pass every
    assertion about the count, while doing the thing the count exists to
    prevent.  Counting calls and comparing the state are two observations, and
    this shape separates them: the first stays at one, the second changes.
    """
    subcommands, untouched, code = _run_shapes_hook(
        tmp_path,
        'printf \'{"mission":"x"}\' > "$PWD/.mission-state/sessions/cc-injected.json"',
    )

    assert code == 0, code
    assert subcommands == ["stop-verdict"], subcommands
    assert not untouched


def test_a_path_built_from_pieces_is_not_detected_and_that_is_where_this_stops(
    tmp_path,
):
    """The known limit, pinned with evidence rather than left to be rediscovered.

    A hook that assembles the CLI's path out of fragments reaches it without
    naming it and without passing the recorder:

        _dir=<repo>/skills/mission/bin
        _base=mission ; _suf=-state.py
        python3 "$_dir/$_base$_suf" stop-verdict ...

    All three observations pass while a second `stop-verdict` really runs.
    Nothing here can close it: a test in the same filesystem cannot stop a hook
    from addressing the file directly, and chasing the spellings (`printf`,
    `basename`, an encoding) is the losing game twice over -- rounds 1-5 chased
    ways of writing a call, rounds 6-8 chased ways of hiding from the
    measurement.

    **This is out of scope on purpose.**  What these tests defend against is
    the loop of #779 coming back by accident: an edit that calls the CLI again
    without meaning to.  Every shape of that -- eleven reported ones, other
    interpreters, `exec`, writing the state directly -- is caught.  A change
    written to evade the instrument is a matter for review, not for the
    instrument.

    Closing it would need process-level observation (what actually got
    spawned), which is a different mechanism with different costs on macOS and
    on CI.  **If that arrives, this test fails -- delete it and say so.**
    """
    directory = STATE_PY.parent
    # The witness.  Without it the test passes just as well against a path
    # that does not exist -- and then it pins nothing, while reading as though
    # it had measured something.  It sits outside the state tree, so it does
    # not disturb the comparison.
    witness = tmp_path / "second-call-really-ran"
    # Quoted: pytest's tmp path can contain a space, and an unquoted witness
    # then lands somewhere else -- the test fails, but naming the wrong thing.
    subcommands, untouched, code = _run_shapes_hook(
        tmp_path,
        f"_dir={shlex.quote(str(directory))}\n_base=mission\n_suf=-state.py\n"
        'printf \'%s\' "$INPUT" | "$MISSION_TEST_PYTHON" "$_dir/$_base$_suf"'
        " stop-verdict --hook-input - --json >/dev/null"
        f" && : > {shlex.quote(str(witness))}",
    )

    assert code == 0, code
    assert witness.exists(), "the second call did not run; this pins nothing"
    assert subcommands == ["stop-verdict"], subcommands
    assert untouched


# The loop of #779 was not an unconditional second call.  It ran `mark-halt`
# *because* the decision said stale, then decided again from the receipt.  A
# shape placed behind that condition is invisible on an empty `sessions/`,
# which is the only state the rows above use -- so the branch is exercised
# here.  This is not an adversarial shape: "call again when the finding says
# so" is the shape the change removed.
_STALE = {"updated_at": "2020-01-01T00:00:00Z"}

_BRANCHING_SHAPE = (
    'if [ "$(printf \'%s\' "$GUARD_DECISION" | jq -r \'.finding\')" = "stale" ]; then\n'
    '  ( exec "$MISSION_TEST_PYTHON" "$MISSION_STATE_PY" mark-halt'
    ' --reason regression ) >/dev/null 2>&1 || true\n'
    "fi"
)


def test_a_shape_that_dies_is_reported_as_dying(tmp_path):
    """The third observation, checked on something that actually fails.

    Every other row expects `code == 0`, so none of them shows that the code
    is real rather than a constant.  Here the inserted line fails under the
    hook's `set -e`, and the run has to say so -- otherwise "the shape ran and
    was not detected" and "the shape did not run" stay indistinguishable, which
    is how four rows passed on CI while running nothing at all.
    """
    subcommands, _untouched, code = _run_shapes_hook(tmp_path, "false")

    assert code != 0, code
    assert subcommands == ["stop-verdict"], subcommands


def test_the_stale_path_also_costs_one_call(tmp_path):
    """The control for the branch: deciding *and applying* is still one process.

    `stale` is the path that used to spend a second process, so the count on
    it is the claim of #779, not a repetition of the empty case.  The state
    does change here -- the halt is what the decision resolves to -- so only
    the count is asserted.
    """
    subcommands, _untouched, code = _run_shapes_hook(tmp_path, "", state=_STALE)

    assert code == 0, code
    assert subcommands == ["stop-verdict"], subcommands


@pytest.mark.parametrize("state", (None, _STALE), ids=("empty", "stale"))
def test_a_direct_write_is_caught_on_every_path_not_only_the_quiet_one(tmp_path, state):
    """A hook that writes the state itself, without spending a second process.

    This is the shape the count cannot see: one invocation, no violation to
    read statically, and on ``stale`` the verdict writes anyway, so "did the
    tree change" is true either way.  Comparing against what the same path
    writes *without* the inserted line is what separates them.
    """
    baseline = _tree_after_shapes_hook(tmp_path / "baseline", "", state=state)
    written = _tree_after_shapes_hook(
        tmp_path / "written",
        # The hook runs with the repository as its working directory, which is
        # how it finds the state in the first place.
        'printf %s x > .mission-state/direct-write',
        state=state,
    )

    assert (baseline.returncode, written.returncode) == (0, 0)
    # The count says nothing here -- that is the point of this row.
    assert baseline.subcommands == written.subcommands == ["stop-verdict"], (
        baseline.subcommands, written.subcommands)

    # Compared as whole entries, not as names: a hook that rewrote a file that
    # is already there, changed a mode, or removed something would leave the
    # set of names untouched (cross-model review round 2).
    added = {name: written.tree[name] for name in set(written.tree) - set(baseline.tree)}
    assert set(added) == {".mission-state/direct-write"}, sorted(added)
    remainder = {
        name: entry for name, entry in written.tree.items() if name in baseline.tree
    }
    assert remainder == baseline.tree, _tree_difference(baseline.tree, remainder)


# Rewrites the fencing token and nothing else.  Every byte around it stays,
# the file keeps its name, its kind and its mode, and the process count stays
# at one -- so this passes the count, the static check, and the tree
# comparison that folds the lease away (#802).
_LEASE_REWRITE = (
    'python3 - <<\'REWRITE\'\n'
    'import pathlib, re\n'
    'p = pathlib.Path(".mission-state/sessions/cc-own.json")\n'
    's = p.read_text()\n'
    'p.write_text(re.sub(r\'("lease_id":\\s*)"[0-9a-f]+"\', r\'\\1"00bad1d"\', s))\n'
    'REWRITE'
)


def test_rewriting_only_the_lease_is_caught(tmp_path):
    """The fencing token is state; changing it is writing the state.

    `_normalised_state_tree` folds the lease away because it differs between
    runs by construction, and folding it hid exactly this: a hook that keeps
    every other byte and swaps the token reaches the CLI once and leaves a
    tree that normalises to the baseline's (#802, found by the independent
    Checker on #801).
    """
    baseline = _tree_after_shapes_hook(tmp_path / "baseline", "", state=_STALE)
    written = _tree_after_shapes_hook(
        tmp_path / "written", _LEASE_REWRITE, state=_STALE
    )

    assert (baseline.returncode, written.returncode) == (0, 0)
    # Neither observation that came before this one can see the rewrite: the
    # count is one, and the normalised trees are equal.  Both are asserted
    # rather than described, so the row fails if it stops being the case and
    # nobody has to trust this comment.
    assert baseline.subcommands == written.subcommands == ["stop-verdict"], (
        baseline.subcommands, written.subcommands)
    assert written.tree == baseline.tree, "the normalised trees differ, so this row is moot"

    assert written.outside_the_cli, "the lease rewrite was not observed"
    assert not baseline.outside_the_cli, baseline.outside_the_cli


def test_which_gaps_are_read_and_which_belong_to_the_cli(tmp_path):
    """The three gaps, and the one stretch that is not a gap.

    The hook's own write lands after the CLI, so that is the gap the run above
    exercises.  The other two are asserted here rather than left to be
    believed: written by hand because the insertion point in `_hook_with` is
    after the last call, so a hook that writes *before* it cannot be built
    there.  What this cannot show is that the recorder ever produces these
    shapes -- the run above shows that for the last gap.
    """
    snapshots = tmp_path / "snapshots.log"
    # The ends are read by `_state_tree`, which holds contents; the recorder
    # writes digests.  Both sides are built from the same bytes so that the
    # fixture cannot disagree with itself.
    before, after = {".mission-state/f": ("file", 0o100600, b"a")}, \
        {".mission-state/f": ("file", 0o100600, b"b")}
    seeded, changed = _digest_tree(before), _digest_tree(after)

    def record(*entries):
        snapshots.write_text(
            "".join(json.dumps({"phase": phase, "tree": tree}) + "\n"
                    for phase, tree in entries),
            encoding="utf-8",
        )

    # Written between the test's reading and the CLI's: the hook got there
    # first.
    record(("entry", changed), ("exit", changed))
    gaps = _writes_outside_the_cli(before, after, snapshots)
    assert [gap for gap, _name, _was, _became in gaps] == ["before the first call -> entry"]

    # The same change inside the call is the CLI doing its job.
    record(("entry", seeded), ("exit", changed))
    assert _writes_outside_the_cli(before, after, snapshots) == []

    # Between two calls is a gap as much as either end is.
    record(("entry", seeded), ("exit", seeded), ("entry", changed), ("exit", changed))
    gaps = _writes_outside_the_cli(before, after, snapshots)
    assert [gap for gap, _name, _was, _became in gaps] == ["exit -> entry"]

    # With nothing recorded the run is one stretch, and a change in it is the
    # hook's: reading an absent log as "no writes" would turn a CLI that never
    # ran into a clean report.
    assert not snapshots.with_name("absent.log").exists()
    gaps = _writes_outside_the_cli(before, after, snapshots.with_name("absent.log"))
    assert [gap for gap, _name, _was, _became in gaps] == [
        "before the first call -> after the last call"]


def test_a_pipe_in_the_state_is_named_rather_than_opened(tmp_path):
    """Both readings of the tree have to survive a file with no contents.

    A FIFO has no writer here, so opening one blocks for ever: the run would
    end in a hang instead of a report, and with no pytest-timeout in this repo
    the shard would be cancelled on its own limit (#801 Checker, Low 3).  Both
    the test's reading and the recorder's walk the same tree, so both have to
    stop at the kind rather than read the contents -- otherwise they disagree
    about what they are comparing.

    A hang is what failure looks like here, which no assertion can express;
    the timeout is the observation.
    """
    def seed(state_dir):
        os.mkfifo(state_dir / "pipe")

    run = _tree_after_shapes_hook(tmp_path, "", state=_STALE, seed=seed)

    assert run.returncode == 0, run.returncode
    assert run.subcommands == ["stop-verdict"], run.subcommands
    # Without this the row passes on a recorder that blocked on the pipe: the
    # call is killed by the guard's own budget, nothing is recorded, and a run
    # in which the CLI never wrote leaves every other assertion here happy.
    assert run.phases == ["entry", "exit"], run.phases
    # Named by kind in the test's reading...
    assert run.tree[".mission-state/pipe"][0] == "other", run.tree[".mission-state/pipe"]
    # ...and undisturbed in the recorder's, which is the one that ran with it
    # already in place.
    assert run.outside_the_cli == [], run.outside_the_cli


def test_the_baseline_tree_is_the_same_twice_so_the_comparison_means_something(tmp_path):
    """Two runs of the same path have to agree, or the row above proves nothing.

    A verdict that writes a timestamp, a pid or a session id would make every
    comparison report a difference, and the row above would pass whatever the
    hook did.
    """
    first = _tree_after_shapes_hook(tmp_path / "first", "", state=_STALE)
    second = _tree_after_shapes_hook(tmp_path / "second", "", state=_STALE)

    assert (first.returncode, second.returncode) == (0, 0)
    assert first.tree == second.tree, _tree_difference(first.tree, second.tree)


def test_a_second_call_behind_the_finding_is_caught(tmp_path):
    """And the same state shows the second call that the empty one hides."""
    empty, _untouched, empty_code = _run_shapes_hook(tmp_path / "empty", _BRANCHING_SHAPE)
    stale, _changed, stale_code = _run_shapes_hook(
        tmp_path / "stale", _BRANCHING_SHAPE, state=_STALE
    )

    assert (empty_code, stale_code) == (0, 0), (empty_code, stale_code)
    # Named together on purpose: the first line is why the second is needed.
    assert empty == ["stop-verdict"], empty
    assert stale == ["stop-verdict", "mark-halt"], stale


# The one shape that looked like the others and is not.  `\\ ` escapes the
# space, so bash looks for a command named `_mission_state_bounded resume`,
# finds none, and the wrapper is never entered.  The static check let it
# through, and that was correct: there is nothing to catch.  Without this
# record the next reader spends the same time re-deriving it.
def test_an_escaped_space_reaches_nothing(tmp_path):
    subcommands, untouched, code = _run_shapes_hook(
        tmp_path, "_mission_state_bounded\\ resume"
    )

    assert subcommands == ["stop-verdict"]
    assert untouched
