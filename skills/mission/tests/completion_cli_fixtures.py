"""Opt-in command runner for completion/publish state and validation tables.

These tables exercise the real parser, main(), repository and child providers.
Interpreter startup is not their contract. Tests of the executable boundary opt
back into raw_run_cli; provider callbacks and verification remain subprocesses.
"""
import contextlib
import io
import json
import shutil
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from .conftest import MISSION_STATE_PY, _SESSION_ENV_VARS


@pytest.fixture(scope="module")
def completion_template(tmp_path_factory):
    """Build once; only independent copies are handed to mutable test cases."""
    from .conftest import raw_run_cli, state_dir
    from .test_issue879_completion_cli import completion_session
    root = tmp_path_factory.mktemp("completion-template")
    directory = state_dir.__wrapped__(root)
    completion_session.__wrapped__(types.SimpleNamespace(param=4), directory,
                                   raw_run_cli.__wrapped__(root))
    original = {path.relative_to(root): path.read_bytes()
                for path in root.rglob("*") if path.is_file()}
    yield root
    assert {path.relative_to(root): path.read_bytes()
            for path in root.rglob("*") if path.is_file()} == original


def copy_completion_template(template, state_dir, schema):
    root = state_dir.parent
    shutil.copytree(template, root, dirs_exist_ok=True)
    path = state_dir / "sessions" / "test.json"
    # The backup is another readable session and must never point back at
    # the template if a case exercises recovery from damaged current state.
    for document in (path, path.with_suffix(".json.bak")):
        state = json.loads(document.read_bytes())
        state["project_root"] = str(root)
        document.write_text(json.dumps(state))
    return root, json.loads(path.read_bytes()), schema


@pytest.fixture(scope="module")
def completion_cli_code():
    # Compile once, execute afresh per command: CLI module globals (leases,
    # warning flags and service closures) must not survive an invocation.
    return compile(MISSION_STATE_PY.read_bytes(), str(MISSION_STATE_PY), "exec")


@pytest.fixture
def run_cli(request, tmp_path, raw_run_cli, completion_cli_code, completion_template):
    executable_contracts = {
        "test_bound_invalid_output_publishes_diagnostic_and_terminal_together",
        "test_reconcile_imports_bound_failure_after_takeover_without_another_launch",
        "test_pending_contract_rejects_public_completion_atomically",
        "test_contractless_completion_and_already_passed_closeout_remain_usable",
        "test_valid_force_approval_cannot_override_acceptance",
    }
    if request.node.originalname in executable_contracts:
        raw_run_cli.completion_template = completion_template
        return raw_run_cli

    def invoke(*args, cwd=None, check=False, env_extra=None, input_text=None):
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("MISSION_") and key not in _SESSION_ENV_VARS}
        sid_keys = ("MISSION_SESSION_ID", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")
        if not (env_extra and any(key in env_extra for key in sid_keys)):
            environment["MISSION_SESSION_ID"] = "test"
        environment["MISSION_LEASE_ID"] = "test-lease"
        for key, value in (env_extra or {}).items():
            if value is None:
                environment.pop(key, None)
            else:
                environment[key] = value
        argv = [str(MISSION_STATE_PY), *args]
        module = types.ModuleType("mission_completion_cli_invocation")
        module.__file__ = str(MISSION_STATE_PY)
        stdout, stderr = io.StringIO(), io.StringIO()
        returncode = 0
        with pytest.MonkeyPatch.context() as patch:
            patch.chdir(Path(cwd or tmp_path).resolve())
            # Update the real process environment too: callbacks spawn with
            # env=None and therefore inherit libc's environment, not a dict.
            for key in set(os.environ) - set(environment):
                patch.delenv(key)
            for key, value in environment.items():
                patch.setenv(key, value)
            patch.setattr(sys, "argv", argv)
            patch.setattr(sys, "stdin", io.StringIO(input_text or ""))
            patch.setitem(sys.modules, module.__name__, module)
            # Match Python's script directory/PYTHONPATH lookup without leaving
            # per-test paths in the parent process after the call.
            paths = [str(MISSION_STATE_PY.parent)]
            paths.extend(environment.get("PYTHONPATH", "").split(os.pathsep)
                         if environment.get("PYTHONPATH") else [])
            patch.setattr(sys, "path", paths + sys.path)
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                exec(completion_cli_code, module.__dict__)
                try:
                    module.main()
                except SystemExit as error:
                    returncode = error.code if isinstance(error.code, int) else (1 if error.code else 0)
                    if error.code and not isinstance(error.code, int):
                        print(error.code, file=sys.stderr)
        result = subprocess.CompletedProcess([sys.executable, *argv], returncode,
                                             stdout.getvalue(), stderr.getvalue())
        if check:
            result.check_returncode()
        return result
    invoke.completion_template = completion_template
    return invoke
