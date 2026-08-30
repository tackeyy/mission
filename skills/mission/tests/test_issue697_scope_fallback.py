"""Issue #697: the integration gate must not require the target repo's CI helper.

The gate classified test scope by calling ``scripts/ci_changed_scopes.js`` in
the integrated tree.  A repository without that helper could not enter the
merge procedure at all, which pushed callers toward the ``gh pr merge`` detour
that Phase 7 forbids.  Absence now falls back to the full suite; a helper that
answers incorrectly still fails closed.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
CI_SCOPE_HELPER = REPO_ROOT / "scripts" / "ci_changed_scopes.js"


def _gate_module():
    return importlib.import_module("integration_gate")


def _gate(tmp_path, *, node_binary="node", stdout="", stderr="", returncode=0):
    gate = _gate_module()
    instance = gate.SubprocessGateOperations(
        tmp_path,
        runner=lambda *_a, **_k: gate.CommandResult(returncode, stdout=stdout, stderr=stderr),
        node_binary=node_binary,
    )
    return gate, instance


def test_missing_helper_falls_back_to_the_full_suite(tmp_path):
    """A repo without the helper must still be testable, not blocked."""
    gate, instance = _gate(tmp_path)
    tree = tmp_path / "tree"
    tree.mkdir()

    scope, targets = instance._classify_scope(["module.py"], tree)

    assert scope == "full"
    assert targets == ""


def _raise_missing_interpreter(*_args, **_kwargs):
    """Reproduce what subprocess does when the interpreter is absent."""
    raise FileNotFoundError(2, "No such file or directory: 'node'")


def test_missing_node_falls_back_to_the_full_suite(tmp_path):
    """The helper is JavaScript; a repo without node must not be blocked either."""
    gate = _gate_module()
    instance = gate.SubprocessGateOperations(tmp_path, runner=_raise_missing_interpreter)
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    (tree / "scripts" / "ci_changed_scopes.js").write_text("module.exports = {};\n", encoding="utf-8")

    scope, targets = instance._classify_scope(["module.py"], tree)

    assert scope == "full"
    assert targets == ""


def test_node_that_exits_non_zero_with_a_real_error_fails_closed(tmp_path):
    """A helper that ran and failed is an error, not an absent convention."""
    gate, instance = _gate(
        tmp_path, returncode=1, stdout="", stderr="TypeError: classifyChangedFiles is not a function"
    )
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    (tree / "scripts" / "ci_changed_scopes.js").write_text("module.exports = {};\n", encoding="utf-8")

    with pytest.raises(gate.IntegrationGateError) as captured:
        instance._classify_scope(["module.py"], tree)

    assert captured.value.reason == "scope-selection-failed"


def test_present_helper_that_answers_badly_still_fails_closed(tmp_path):
    """Only absence falls back.  A helper that answers wrongly is still an error."""
    gate, instance = _gate(tmp_path, stdout="not json")
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    (tree / "scripts" / "ci_changed_scopes.js").write_text("module.exports = {};\n", encoding="utf-8")

    with pytest.raises(gate.IntegrationGateError) as captured:
        instance._classify_scope(["module.py"], tree)

    assert captured.value.reason == "scope-selection-failed"


def test_present_helper_keeps_its_existing_answer(tmp_path):
    """The helper path is unchanged: its decision is still what selects scope."""
    gate, instance = _gate(
        tmp_path, stdout='{"pythonTargets": "skills/mission", "docsOnly": false}'
    )
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    (tree / "scripts" / "ci_changed_scopes.js").write_text("module.exports = {};\n", encoding="utf-8")

    scope, targets = instance._classify_scope(["module.py"], tree)

    assert (scope, targets) == ("full", "skills/mission")


def test_fallback_reason_is_logged(tmp_path):
    """A silent fallback would hide which suite actually ran."""
    gate, instance = _gate(tmp_path)
    tree = tmp_path / "tree"
    tree.mkdir()
    messages = []

    instance._classify_scope(["module.py"], tree, logger=messages.append)

    joined = "\n".join(messages)
    assert "scope_fallback=" in joined
    assert "helper-missing" in joined
