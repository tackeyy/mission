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


@pytest.mark.parametrize("returncode,stdout,stderr", [
    # A helper that ran and threw is an error, not an absent convention.
    (1, "", "TypeError: classifyChangedFiles is not a function"),
    # The helper itself hit a missing file.  Matching that text would turn a
    # broken classifier into a silent full run, so only exit 127 counts.
    (1, "", "Error: ENOENT: no such file or directory, open './missing.json'"),
    # Killed before writing anything, but not a missing interpreter.
    (143, "", ""),
])
def test_present_helper_that_fails_never_falls_back(tmp_path, returncode, stdout, stderr):
    """Only the shell's command-not-found signal may be read as absence."""
    gate, instance = _gate(tmp_path, returncode=returncode, stdout=stdout, stderr=stderr)
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    (tree / "scripts" / "ci_changed_scopes.js").write_text("module.exports = {};\n", encoding="utf-8")

    with pytest.raises(gate.IntegrationGateError) as captured:
        instance._classify_scope(["module.py"], tree)

    assert captured.value.reason == "scope-selection-failed"


@pytest.mark.parametrize("stderr", [
    "bash: node: command not found",
    # A Japanese shell says it in Japanese.  The decision must not depend on
    # which locale the operator happens to run under.
    "bash: node: \u30b3\u30de\u30f3\u30c9\u304c\u898b\u3064\u304b\u308a\u307e\u305b\u3093",
    "",
])
def test_command_not_found_exit_falls_back(tmp_path, stderr):
    """Exit 127 with no classifier output means the command never ran."""
    gate, instance = _gate(tmp_path, returncode=127, stderr=stderr)
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    (tree / "scripts" / "ci_changed_scopes.js").write_text("module.exports = {};\n", encoding="utf-8")

    scope, targets = instance._classify_scope(["module.py"], tree)

    assert (scope, targets) == ("full", "")


def test_non_executable_interpreter_fails_closed(tmp_path):
    """A present but non-executable interpreter is a misconfiguration, not absence."""
    gate = _gate_module()

    def _raise_permission(*_args, **_kwargs):
        raise PermissionError(13, "Permission denied: 'node'")

    instance = gate.SubprocessGateOperations(tmp_path, runner=_raise_permission)
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    (tree / "scripts" / "ci_changed_scopes.js").write_text("module.exports = {};\n", encoding="utf-8")

    with pytest.raises(PermissionError):
        instance._classify_scope(["module.py"], tree)


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


def test_exit_127_with_classifier_output_still_fails_closed(tmp_path):
    """A helper that answered and then exited 127 did run, so it is not absent."""
    gate, instance = _gate(
        tmp_path,
        returncode=127,
        stdout='{"pythonTargets": "skills/mission", "docsOnly": false}',
    )
    tree = tmp_path / "tree"
    (tree / "scripts").mkdir(parents=True)
    (tree / "scripts" / "ci_changed_scopes.js").write_text("module.exports = {};\n", encoding="utf-8")

    with pytest.raises(gate.IntegrationGateError) as captured:
        instance._classify_scope(["module.py"], tree)

    assert captured.value.reason == "scope-selection-failed"
