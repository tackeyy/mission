"""A1 lifecycle command ownership and adapter-shape contracts."""

from __future__ import annotations

import ast
import argparse
import importlib.util
from pathlib import Path
import sys

import pytest


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

FORBIDDEN_LEGACY_CALLS = {
    "StateLock",
    "_add_to_aggregate",
    "_transition_phase",
    "_write_terminal_outcome",
    "atomic_write_json",
    "backup_state",
    "json.loads",
}

COMMAND_APPLICATION_ROUTES = {
    "cmd_activity_end": "run_activity_end",
    "cmd_activity_start": "run_activity_start",
    "cmd_advance": "run_advance",
    "cmd_cleanup_stale": "run_mark_halt",
    "cmd_halt": "run_mark_halt",
    "cmd_init": "run_initialize",
    "cmd_mark_halt": "run_mark_halt",
    "cmd_reactivate": "run_reactivate",
    "cmd_refresh_pid": "run_refresh_pid",
    "cmd_resume": "run_refresh_pid",
    "cmd_set": "run_set_fields",
    "cmd_update_project_root": "run_update_project_root",
}


def _load_mission_state_module():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state_command_inventory", source)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _leaf_parser_commands(parser, prefix=()):
    subparsers = [
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    ]
    if not subparsers:
        return {" ".join(prefix)}
    commands = set()
    for action in subparsers:
        for name, child in action.choices.items():
            commands.update(_leaf_parser_commands(child, prefix + (name,)))
    return commands


def test_all_parser_commands_have_exactly_one_declared_owner():
    from mission_application.command_owners import COMMAND_OWNER_REGISTRY

    parser_commands = _leaf_parser_commands(_load_mission_state_module()._build_parser())

    assert set(COMMAND_OWNER_REGISTRY) == parser_commands
    assert all(isinstance(owner, str) and owner for owner in COMMAND_OWNER_REGISTRY.values())


def test_c2_stage_a_and_direct_write_allowlist_are_closed_and_disjoint():
    from mission_application.command_owners import (
        C2_DIRECT_WRITE_ALLOWLIST,
        C2_REPOSITORY_COMMANDS,
        COMMAND_OWNER_REGISTRY,
    )

    assert C2_REPOSITORY_COMMANDS == frozenset(
        {"planning reselect", "supersede-reviews"}
    )
    assert C2_DIRECT_WRITE_ALLOWLIST == frozenset(
        {
            "executor-handoff begin",
            "executor-handoff complete",
            "executor-handoff record-step",
            "executor-handoff verify-step",
            "manual-score-capture",
            "planning adopt-core",
            "planning promote-provider-plan",
            "specialists invoke-command",
            "specialists invoke-prepared",
            "specialists log-invocation",
            "specialists plan-import",
            "specialists prepare-invocation",
            "specialists recommend",
            "specialists reconcile-invocation",
            "specialists verify-approval",
        }
    )
    assert C2_REPOSITORY_COMMANDS.isdisjoint(C2_DIRECT_WRITE_ALLOWLIST)
    assert C2_REPOSITORY_COMMANDS | C2_DIRECT_WRITE_ALLOWLIST <= set(
        COMMAND_OWNER_REGISTRY
    )


def test_c2_repository_commands_have_no_direct_legacy_session_writer_calls():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    target_names = {"cmd_planning_reselect", "cmd_supersede_reviews"}
    forbidden = {"StateLock", "atomic_write_json"}
    violations = []
    for function in (
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in target_names
    ):
        for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
            if isinstance(call.func, ast.Name) and call.func.id in forbidden:
                violations.append((function.name, call.func.id, call.lineno))

    assert violations == []


def test_direct_legacy_call_inventory_has_no_silent_parser_adapter_gap():
    from mission_application.command_owners import (
        C2_DIRECT_WRITE_FUNCTIONS,
        NON_SESSION_DIRECT_CALL_FUNCTIONS,
    )

    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    forbidden = {"StateLock", "atomic_write_json"}
    discovered = set()
    for function in (
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and (node.name.startswith("cmd_") or node.name.startswith("_cmd_"))
    ):
        if any(
            isinstance(call.func, ast.Name) and call.func.id in forbidden
            for call in ast.walk(function)
            if isinstance(call, ast.Call)
        ):
            discovered.add(function.name)

    assert discovered == C2_DIRECT_WRITE_FUNCTIONS | NON_SESSION_DIRECT_CALL_FUNCTIONS


def test_a1_registry_has_one_owner_for_every_lifecycle_command():
    from mission_application.lifecycle import LIFECYCLE_COMMAND_OWNERS

    assert set(LIFECYCLE_COMMAND_OWNERS) == A1_COMMANDS
    assert all(owner == "A1.lifecycle" for owner in LIFECYCLE_COMMAND_OWNERS.values())


def test_advance_command_does_not_read_state_outside_repository():
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"cmd_activity_start", "cmd_activity_end", "cmd_advance"}
    }

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
            if called in FORBIDDEN_LEGACY_CALLS:
                violations.append((function.name, called, call.lineno))
    assert violations == []


def _called_function_names(function: ast.FunctionDef) -> set[str]:
    called = set()
    for call in (node for node in ast.walk(function) if isinstance(node, ast.Call)):
        if isinstance(call.func, ast.Name):
            called.add(call.func.id)
            if (
                call.func.id == "_capture_command_output"
                and call.args
                and isinstance(call.args[0], ast.Name)
            ):
                called.add(call.args[0].id)
    return called


@pytest.mark.parametrize(
    ("command_name", "application_name"),
    COMMAND_APPLICATION_ROUTES.items(),
)
def test_a1_commands_route_through_application_use_cases(
    command_name, application_name
):
    source = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    pending = [command_name]
    visited = set()
    called_names = set()
    while pending:
        current = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        direct_calls = _called_function_names(functions[current])
        called_names.update(direct_calls)
        pending.extend(
            name
            for name in direct_calls
            if name in functions and name not in visited
        )

    assert application_name in called_names


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
