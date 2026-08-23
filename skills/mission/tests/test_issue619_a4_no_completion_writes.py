"""Issue #619 批1-c: A4.specialist-planning は完了隣接フィールドを書かない.

実測: 対象 9 command（planning adopt-core / promote-provider-plan / reselect、
specialists invoke-command / log-invocation / plan-import /
prepare-invocation / reconcile-invocation / verify-approval）の完了隣接言及は
すべて読み（phase 検証・invocation record の属性）であり、書き込みは存在
しない。本テストはその不在を AST で固定し、A4 に completion authority が
後から混入する backslide を CI で検出する（provider は evidence provider に
限定し pass / review / score authority を持たせない境界の静的固定）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


MISSION_STATE_SOURCE = (
    Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
)

# 批1-c の対象 9 command の実装関数（parser の set_defaults から確定）
A4_COMMAND_FUNCTIONS = (
    "cmd_planning_adopt_core",
    "cmd_planning_promote_provider_plan",
    "cmd_planning_reselect",
    "cmd_invoke_command_provider",
    "cmd_log_specialist_invocation",
    "cmd_plan_import",
    "cmd_prepare_provider_invocation",
    "cmd_reconcile_provider_invocation",
    "cmd_verify_provider_approval",
)

# 完了隣接 control フィールド + provider が持ってはならない score authority
FORBIDDEN_WRITE_KEYS = frozenset(
    {
        "terminal_outcome",
        "phase",
        "passes",
        "loop_active",
        "halt_reason",
        "halt_category",
        "score_history",
    }
)

# completion authority を直接呼ぶヘルパー（A4 からの呼び出しは境界違反）
FORBIDDEN_HELPER_CALLS = frozenset(
    {
        "_transition_phase",
        "_write_terminal_outcome",
        "derive_terminal_outcome",
        "run_mark_halt",
        "run_mark_pass",
        "monotonic_halt_decision",
    }
)


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _literal_keys(node: ast.expr) -> set[str]:
    if isinstance(node, ast.Dict):
        return {
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
    return set()


def find_completion_write_violations(function: ast.FunctionDef) -> list[str]:
    """Return human-readable violations of the A4 no-completion-write contract."""
    violations: list[str] = []
    for node in ast.walk(function):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                    and target.slice.value in FORBIDDEN_WRITE_KEYS
                ):
                    violations.append(
                        f"L{node.lineno}: assigns forbidden key "
                        f"{target.slice.value!r}"
                    )
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in FORBIDDEN_HELPER_CALLS:
                violations.append(f"L{node.lineno}: calls forbidden helper {name}")
            if name == "update":
                for argument in node.args:
                    forbidden = _literal_keys(argument) & FORBIDDEN_WRITE_KEYS
                    if forbidden:
                        violations.append(
                            f"L{node.lineno}: update() with forbidden keys "
                            f"{sorted(forbidden)}"
                        )
            if name == "setdefault" and node.args:
                first = node.args[0]
                if (
                    isinstance(first, ast.Constant)
                    and isinstance(first.value, str)
                    and first.value in FORBIDDEN_WRITE_KEYS
                ):
                    violations.append(
                        f"L{node.lineno}: setdefault of forbidden key "
                        f"{first.value!r}"
                    )
    return violations


def _module_functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(MISSION_STATE_SOURCE.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


def test_a4_command_functions_exist():
    functions = _module_functions()
    missing = [name for name in A4_COMMAND_FUNCTIONS if name not in functions]
    assert missing == []


@pytest.mark.parametrize("name", A4_COMMAND_FUNCTIONS)
def test_a4_commands_do_not_write_completion_adjacent_fields(name):
    functions = _module_functions()
    violations = find_completion_write_violations(functions[name])
    assert violations == [], f"{name} writes completion authority: {violations}"


def test_guard_detects_forbidden_subscript_assignment():
    """ガード自体の検出力を固定する（劣化した検査で null を信じない）。"""
    source = (
        "def offender(data):\n"
        "    data[\"passes\"] = True\n"
        "    data.update({\"phase\": \"done\"})\n"
        "    data.setdefault(\"halt_reason\", \"x\")\n"
        "    _write_terminal_outcome(data)\n"
    )
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    violations = find_completion_write_violations(function)
    assert len(violations) == 4


def test_guard_accepts_reads_and_neutral_writes():
    source = (
        "def reader(data, args):\n"
        "    if data.get(\"phase\") != \"planning\":\n"
        "        return\n"
        "    record = {\"phase\": str(args.phase)}\n"
        "    data[\"specialists_selected\"] = []\n"
        "    data[\"updated_at\"] = \"2026-08-23T00:00:00Z\"\n"
        "    return record\n"
    )
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    assert find_completion_write_violations(function) == []


def test_provider_generic_writes_cannot_reach_pass_authority():
    """provider 由来の汎用書き込み（extension_fields_decision 経由）は kernel の
    frozen 分類により pass / score authority へ到達できない。"""
    from mission_application.lifecycle import extension_fields_decision

    for field in ("passes", "score_history", "terminal_outcome", "threshold"):
        decision = extension_fields_decision(
            {"phase": "planning"}, {field: "provider-forged"}
        )
        assert decision.accepted is False
        assert decision.rejection.code == "frozen-field"
