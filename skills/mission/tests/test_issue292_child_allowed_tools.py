"""#292: CC 2.1.219 hardening 下でも child claude が動くよう --allowedTools を明示する.

CC 2.1.219 では CC セッション配下の `claude -p` に allowed_non_write_users hardening が
発動し、`--permission-mode acceptEdits` が default に強制降格される。#268 の
SCRUB=0 opt-out は効かない (2026-07-25 最小再現)。警告文が案内する正規回避は
allowedTools の明示。両 arm 同条件で付与し、arm 間比較の妥当性を保つ。
"""

import importlib.util
from pathlib import Path

BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "mission-vs-goal"


def _load():
    path = BENCH / "run_claude_goal_vs_mission.py"
    spec = importlib.util.spec_from_file_location("run_claude_goal_vs_mission", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()


def test_both_arms_declare_allowed_tools_explicitly():
    for arm in ("claude_code_goal_command", "mission"):
        command = MODULE.build_child_command(arm, "prompt text", 10.0)
        assert "--allowedTools" in command, f"{arm}: --allowedTools がない"
        tools = command[command.index("--allowedTools") + 1]
        for needed in ("Write", "Edit", "Read", "Bash", "Grep", "Glob"):
            assert needed in tools, f"{arm}: {needed} が allowedTools にない"


def test_mission_arm_allows_agent_and_skill_for_reviewers():
    command = MODULE.build_child_command("mission", "prompt text", 10.0)
    tools = command[command.index("--allowedTools") + 1]
    assert "Agent" in tools and "Skill" in tools


def test_arms_share_identical_allowed_tools():
    """arm 間比較の妥当性: allowedTools は両 arm 完全同一."""
    goal = MODULE.build_child_command("claude_code_goal_command", "p", 10.0)
    mission = MODULE.build_child_command("mission", "p", 10.0)
    assert goal[goal.index("--allowedTools") + 1] == mission[mission.index("--allowedTools") + 1]


def test_mission_arm_keeps_plugin_dir_and_prompt_last():
    command = MODULE.build_child_command("mission", "prompt text", 10.0)
    assert "--plugin-dir" in command
    assert command[-1] == "prompt text"
    goal = MODULE.build_child_command("claude_code_goal_command", "prompt text", 10.0)
    assert "--plugin-dir" not in goal
    assert goal[-1] == "prompt text"
