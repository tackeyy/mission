"""#312/#506: phase changes use atomic ``advance`` with activity segments."""

import importlib.util
import json
from pathlib import Path

import pytest


def _load():
    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MS = _load()


def _sessions(tmp_path):
    directory = tmp_path / ".mission-state" / "sessions"
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def _use_legacy_planning_policy(tmp_path):
    path = _sessions(tmp_path)[0]
    state = json.loads(path.read_text(encoding="utf-8"))
    state.pop("planning_policy_version", None)
    path.write_text(json.dumps(state), encoding="utf-8")


def _next_for(phase, iteration=1):
    return MS._derive_next_action(
        {
            "mission": "m",
            "mission_id": "x",
            "loop_active": True,
            "passes": False,
            "halt_reason": "",
            "phase": phase,
            "iteration": iteration,
            "reviewer_count": 2,
        }
    )


def test_planning_hint_uses_advance():
    hint = _next_for("planning")["command_hint"]
    assert "set phase" not in hint
    assert "advance --phase executing --activity active:implementation" in hint


def test_executing_hint_uses_advance():
    hint = _next_for("executing")["command_hint"]
    assert "set phase" not in hint
    assert "advance --phase reviewing --activity reviewer-wait:review-response" in hint


@pytest.mark.parametrize("phase", ("planning", "executing", "reviewing"))
def test_set_cannot_bypass_advance_phase_authority(run_cli, tmp_path, phase):
    run_cli("init", "m", "--complexity", "Standard", cwd=tmp_path, check=True)
    path = _sessions(tmp_path)[0]
    before = path.read_bytes()

    result = run_cli("set", f"phase={phase}", cwd=tmp_path)

    assert result.returncode == 2
    assert path.read_bytes() == before


def test_advance_opens_the_requested_activity_segment(run_cli, tmp_path):
    run_cli(
        "init",
        "m",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        check=True,
    )
    _use_legacy_planning_policy(tmp_path)
    run_cli(
        "advance",
        "--phase",
        "executing",
        "--activity",
        "active:implementation",
        cwd=tmp_path,
        check=True,
    )

    state = json.loads(_sessions(tmp_path)[0].read_text(encoding="utf-8"))
    current = state["activity_current"]
    assert state["phase"] == "executing"
    assert (current["kind"], current["reason"], current["phase"]) == (
        "active",
        "implementation",
        "executing",
    )


def test_advance_to_reviewing_opens_reviewer_wait(run_cli, tmp_path):
    run_cli(
        "init",
        "m",
        "--complexity",
        "Standard",
        "--artifact-applicability",
        "not-applicable",
        cwd=tmp_path,
        check=True,
    )
    _use_legacy_planning_policy(tmp_path)
    run_cli(
        "advance",
        "--phase",
        "executing",
        "--activity",
        "active:implementation",
        cwd=tmp_path,
        check=True,
    )
    run_cli(
        "advance",
        "--phase",
        "reviewing",
        "--activity",
        "reviewer-wait:review-response",
        cwd=tmp_path,
        check=True,
    )

    state = json.loads(_sessions(tmp_path)[0].read_text(encoding="utf-8"))
    current = state["activity_current"]
    assert (current["kind"], current["reason"], current["phase"]) == (
        "reviewer-wait",
        "review-response",
        "reviewing",
    )
