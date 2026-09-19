"""Issue #779: the Stop guard applies its closed command set in one process."""

from __future__ import annotations

import ast
import importlib.util
import types
import json
import os
import shlex
import subprocess
import sys
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


def _run_hook(tmp_path, state_dir, *, stop_hook_active=False, env_overrides=None,
              hook=HOOK):
    shim_dir, log = _counting_python(tmp_path)
    env = {**os.environ, **_GUARD_ENV, "PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}",
           "MISSION_STATE_PY": str(STATE_PY)}
    _apply_env(env, env_overrides)
    result = subprocess.run(
        ["bash", str(hook)],
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
    shim = tmp_path / "mission-state.py"
    shim.write_text(
        "import json, runpy, sys\n"
        # One JSON line per invocation, argv and all.  Writing the arguments
        # as text loses the invocation that has none: an empty line and no
        # line read the same, so a second process started without arguments
        # disappeared from the count (#796 cross-model review round 1).
        f"open({str(log)!r}, 'a').write(json.dumps(sys.argv[1:]) + '\\n')\n"
        f"sys.argv[0] = {str(STATE_PY)!r}\n"
        f"runpy.run_path({str(STATE_PY)!r}, run_name='__main__')\n",
        encoding="utf-8",
    )
    return shim, log


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
        else:
            tree[name] = ("file", status.st_mode, path.read_bytes())
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
    shim, log = _recording_state_py(tmp_path)
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


def _tree_after_shapes_hook(tmp_path, inserted, *, state=None):
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
    shim, log = _recording_state_py(tmp_path)
    hook = _hook_with(tmp_path, inserted)
    result, _launches = _run_hook(
        tmp_path, root, hook=hook,
        env_overrides={
            "MISSION_STATE_PY": str(shim),
            "MISSION_TEST_PYTHON": sys.executable,
            "MISSION_TEST_OUTSIDE": str(tmp_path / "outside-target"),
        },
    )
    return _state_tree(root), _subcommands(log), result.returncode


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
    baseline, base_subcommands, base_code = _tree_after_shapes_hook(
        tmp_path / "baseline", "", state=state
    )
    written, subcommands, code = _tree_after_shapes_hook(
        tmp_path / "written",
        # The hook runs with the repository as its working directory, which is
        # how it finds the state in the first place.
        'printf %s x > .mission-state/direct-write',
        state=state,
    )

    assert (base_code, code) == (0, 0), (base_code, code)
    # The count says nothing here -- that is the point of this row.
    assert base_subcommands == subcommands == ["stop-verdict"], (base_subcommands, subcommands)
    assert set(written) - set(baseline) == {".mission-state/direct-write"}, (
        sorted(set(written) - set(baseline))
    )


def test_the_baseline_tree_is_the_same_twice_so_the_comparison_means_something(tmp_path):
    """Two runs of the same path have to agree, or the row above proves nothing.

    A verdict that writes a timestamp, a pid or a session id would make every
    comparison report a difference, and the row above would pass whatever the
    hook did.
    """
    first, _first_subcommands, first_code = _tree_after_shapes_hook(
        tmp_path / "first", "", state=_STALE
    )
    second, _second_subcommands, second_code = _tree_after_shapes_hook(
        tmp_path / "second", "", state=_STALE
    )

    assert (first_code, second_code) == (0, 0), (first_code, second_code)
    assert set(first) == set(second), (sorted(set(first) ^ set(second)))


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
