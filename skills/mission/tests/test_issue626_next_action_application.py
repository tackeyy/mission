"""Issue #626: application extraction preserves legacy next-action behavior."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path
import sys

from .mission_state_fixture_corpus import generate_cli_state_corpus


MISSION_STATE_PATH = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
LEGACY_OUTPUT_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "issue626-next-action-legacy-output.json"
)


def _load_mission_state():
    spec = importlib.util.spec_from_file_location("issue626_mission_state", MISSION_STATE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _state_documents(corpus):
    documents = {}

    def visit(name, value):
        if isinstance(value, dict) and "mission_id" in value:
            documents[name] = value
        elif isinstance(value, dict):
            for child_name, child in value.items():
                visit(f"{name}.{child_name}", child)

    visit("corpus", corpus)
    return documents


def test_application_use_case_matches_legacy_corpus_exactly(tmp_path):
    legacy = _load_mission_state()
    legacy.detect_host = lambda: "unknown"
    from mission_application.next_action import (
        NextActionRequest,
        NextActionServices,
        derive_next_action,
    )

    services = NextActionServices(
        pregate_warning=legacy._pregate_verdict_warning,
        goal_dispatch_fields=legacy._goal_dispatch_route_fields,
        goal_dispatch_guidance=legacy._goal_dispatch_guidance,
        expected_context_mode=legacy._expected_context_mode,
        valid_composite=legacy._is_valid_composite,
    )

    documents = _state_documents(generate_cli_state_corpus(tmp_path.resolve()))
    expected_outputs = json.loads(LEGACY_OUTPUT_PATH.read_text(encoding="utf-8"))
    assert documents.keys() == expected_outputs.keys()

    for name, document in documents.items():
        authoritative = legacy._authoritative_snapshot_for_state(document)
        actual = derive_next_action(
            NextActionRequest(document=document, authoritative=authoritative),
            services,
        )

        assert legacy._derive_next_action(
            document, authoritative=authoritative
        ) == expected_outputs[name]
        assert actual == expected_outputs[name]


def test_legacy_name_is_a_thin_application_facade():
    tree = ast.parse(MISSION_STATE_PATH.read_text(encoding="utf-8"))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_derive_next_action"
    )
    calls = {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert "run_derive_next_action" in calls
    assert not any(
        isinstance(node, (ast.If, ast.IfExp, ast.For, ast.While, ast.Compare))
        for node in ast.walk(function)
    )
    assert sum(isinstance(node, ast.BoolOp) for node in ast.walk(function)) == 1


def test_application_module_does_not_import_adapter_or_persistence():
    source = (
        Path(__file__).resolve().parents[1]
        / "lib"
        / "mission_application"
        / "next_action.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules = {
        alias.name
        for node in tree.body
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }

    assert not any(
        module.startswith(("mission_adapter", "mission_persistence"))
        for module in imported_modules
    )
