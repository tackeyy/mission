"""A1 lifecycle command ownership and adapter-shape contracts."""

from __future__ import annotations

import ast
from pathlib import Path


A1_COMMANDS = {
    "activity-end",
    "activity-start",
    "advance",
    "cleanup-stale",
    "halt",
    "init",
    "mark-halt",
    "reactivate",
    "refresh-pid",
    "resume",
    "set",
    "update-project-root",
}


def test_a1_registry_has_one_owner_for_every_lifecycle_command():
    from mission_application.lifecycle import LIFECYCLE_COMMAND_OWNERS

    assert set(LIFECYCLE_COMMAND_OWNERS) == A1_COMMANDS
    assert all(owner == "A1.lifecycle" for owner in LIFECYCLE_COMMAND_OWNERS.values())


def test_extracted_command_functions_do_not_own_persistence_or_mutation_logic():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"cmd_activity_start", "cmd_activity_end", "cmd_advance"}
    }

    forbidden = {"json.loads", "StateLock", "backup_state", "atomic_write_json"}
    for name, function in functions.items():
        calls = set()
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            if isinstance(call.func, ast.Name):
                calls.add(call.func.id)
            elif isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                calls.add(f"{call.func.value.id}.{call.func.attr}")
        assert calls.isdisjoint(forbidden), (name, calls & forbidden)

    advance_calls = {
        node.func.attr
        for node in ast.walk(functions["cmd_advance"])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "read_text"
    }
    assert advance_calls == set()


def test_a1_command_ast_has_no_direct_legacy_mutation_calls():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    target_names = {
        "cmd_activity_end",
        "cmd_activity_start",
        "cmd_advance",
        "cmd_cleanup_stale",
        "cmd_halt",
        "cmd_init",
        "cmd_mark_halt",
        "cmd_reactivate",
        "cmd_refresh_pid",
        "cmd_resume",
        "cmd_set",
        "cmd_update_project_root",
    }
    forbidden = {
        "json.loads",
        "_transition_phase",
        "_write_terminal_outcome",
        "_add_to_aggregate",
    }

    violations = []
    for function in (
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in target_names
    ):
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            called = None
            if isinstance(call.func, ast.Name):
                called = call.func.id
            elif isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
                called = f"{call.func.value.id}.{call.func.attr}"
            if called in forbidden:
                violations.append((function.name, called, call.lineno))
    assert violations == []


def test_init_command_routes_through_application_use_case():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "cmd_init"
    )
    called_names = {
        call.func.id
        for call in ast.walk(function)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
    }
    assert "run_initialize" in called_names


def test_lifecycle_module_has_no_process_or_stdout_io_dependency():
    source = Path(__file__).resolve().parents[1] / "lib" / "mission_application" / "lifecycle.py"
    text = source.read_text(encoding="utf-8")
    tree = ast.parse(text)
    imported_roots = {
        alias.name.partition(".")[0]
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        node.module.partition(".")[0]
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    )

    assert imported_roots.isdisjoint({"os", "subprocess"})
    assert "sys.stdout" not in text


def test_parser_routes_each_a1_command_to_its_single_registered_adapter():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    parser_names = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "add_parser"
            and node.value.args
            and isinstance(node.value.args[0], ast.Constant)
            and isinstance(node.value.args[0].value, str)
        ):
            variable = node.targets[0].id
            name = node.value.args[0].value
            if variable in {"p_activity_start", "p_activity_end"}:
                name = "activity-" + name
            parser_names[variable] = name

    routes = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "set_defaults"
            and isinstance(node.func.value, ast.Name)
        ):
            function = next(
                (
                    keyword.value.id
                    for keyword in node.keywords
                    if keyword.arg == "func" and isinstance(keyword.value, ast.Name)
                ),
                None,
            )
            parser_name = parser_names.get(node.func.value.id)
            if function and function in {
                "cmd_activity_end",
                "cmd_activity_start",
                "cmd_advance",
                "cmd_cleanup_stale",
                "cmd_halt",
                "cmd_init",
                "cmd_mark_halt",
                "cmd_reactivate",
                "cmd_refresh_pid",
                "cmd_resume",
                "cmd_set",
                "cmd_update_project_root",
            }:
                routes[parser_name] = function

    assert routes == {
        "activity-end": "cmd_activity_end",
        "activity-start": "cmd_activity_start",
        "advance": "cmd_advance",
        "cleanup-stale": "cmd_cleanup_stale",
        "halt": "cmd_halt",
        "init": "cmd_init",
        "mark-halt": "cmd_mark_halt",
        "reactivate": "cmd_reactivate",
        "refresh-pid": "cmd_refresh_pid",
        "resume": "cmd_resume",
        "set": "cmd_set",
        "update-project-root": "cmd_update_project_root",
    }
