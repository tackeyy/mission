"""Issue #144: Codex must establish an active mission state before task setup."""

from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def _isolated_env(tmp_path: Path) -> dict[str, str]:
    return {
        "MISSION_CLAUDE_HOME": str(tmp_path / "neutral-claude-home"),
        "CODEX_HOME": str(tmp_path / "neutral-codex-home"),
    }


def test_strict_preflight_blocks_every_pre_init_action_boundary(tmp_path, run_cli):
    result = run_cli(
        "codex-preflight",
        "--json",
        "--strict",
        "--hook-config",
        str(tmp_path / "hooks.json"),
        cwd=tmp_path,
        env_extra=_isolated_env(tmp_path),
    )

    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["state_guard"]["present"] is False
    assert payload["state_guard"]["active"] is False
    assert payload["mechanical_guard"] == "none"
    assert payload["next_action"] == "init"

    required = " ".join(payload["required_actions"]).lower()
    for boundary in ("task setup", "worktree", "implementation", "final"):
        assert boundary in required


def test_strict_preflight_allows_initialized_skills_only_start(tmp_path, run_cli):
    env = _isolated_env(tmp_path)
    run_cli(
        "init",
        "codex guarded mission",
        "--complexity",
        "Standard",
        cwd=tmp_path,
        env_extra=env,
        check=True,
    )

    result = run_cli(
        "codex-preflight",
        "--json",
        "--strict",
        "--hook-config",
        str(tmp_path / "missing-hooks.json"),
        cwd=tmp_path,
        env_extra=env,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["state_guard"]["active"] is True
    assert payload["mechanical_guard"] == "state-next-fallback"
    assert payload["next_action"] == "run-planner"


def test_codex_contract_requires_strict_preflight_before_setup_and_terminal_gate_before_final():
    skill = (REPO_ROOT / "skills/mission/SKILL.md").read_text(encoding="utf-8")
    setup = (REPO_ROOT / "skills/mission/refs/codex-setup.md").read_text(encoding="utf-8")
    state_management = (REPO_ROOT / "skills/mission/refs/state-management.md").read_text(encoding="utf-8")

    compact = skill.split("## Compact Instructions", 1)[1].split("## state.json 操作", 1)[0]
    assert "codex-preflight --json --strict" in compact
    assert "worktree" in compact
    assert "final" in compact

    start = setup.split("**開始時の正", 1)[1].split("## Local authoring", 1)[0]
    assert "codex-preflight --json --strict" in start
    assert "exit 0" in start
    assert "worktree" in start
    assert "final" in start
    assert "mission-state.py next" in setup
    assert "report-complete" in setup
    assert "report-blocker" in setup
    assert "passed / halted は terminal state" in setup
    assert "inactive / passed / halted。作業開始・final 報告は禁止" not in setup

    startup = state_management.split("# Codex startup health check", 1)[1].split(
        "# 空 .mission-state/", 1
    )[0]
    assert "codex-preflight --json --strict" in startup
    assert "診断専用" in startup
    assert "--require-stop-hook" in startup
