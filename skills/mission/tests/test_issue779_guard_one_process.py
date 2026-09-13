"""Issue #779: the Stop guard applies its closed command set in one process."""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
STATE_PY = REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py"
HOOK = REPO_ROOT / "scripts" / "mission-stop-guard.sh"
EXPECTED_KINDS = {"none", "mark-halt", "cleanup-stale", "stop-guard-observe"}


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
    assert "case " not in HOOK.read_text(encoding="utf-8")


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
