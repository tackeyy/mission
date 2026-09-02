"""Issue #735: the gate stops accepting a suite it cannot show ran.

The gate checks only `make test`'s exit code (`integration_gate.py:586-589`).
A repository whose `make test` runs nothing still returns 0, so the guarantee
the gate advertises -- "the full suite passed on the integrated tree" -- can be
hollow.  #722 established this and chose design B: the target repository
declares a full-suite contract, and a missing, malformed, or zero-count report
fails closed.

Two properties matter as much as the count itself.

The contract is read from the **base**, not from the integrated tree.  A PR that
could rewrite the contract could point it at `true` and pass itself; reading the
base means a PR is always tested under the contract that was already merged.

The report is read from a **path the gate chooses**, not from stdout.  Scraping
output would accept an echoed line or a stale file left by an earlier run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

import integration_gate as gate  # noqa: E402
from mission_application import integration_gate as application  # noqa: E402


CONTRACT_PATH = ".mission/suite-contract.json"


def _contract(command=("make", "test"), schema="mission-suite-contract/1"):
    return {"schema": schema, "full_suite_command": list(command)}


def _report(tree_sha, executed=12, status="complete",
            schema="mission-suite-report/1"):
    return {
        "schema": schema,
        "tree_sha": tree_sha,
        "executed": executed,
        "status": status,
    }


def test_a_missing_contract_stops_the_gate():
    """Without a declaration the gate cannot know what a full suite is."""
    with pytest.raises(gate.IntegrationGateError) as captured:
        gate.require_suite_contract(None, step=4)

    assert captured.value.reason == "suite-contract-missing"


@pytest.mark.parametrize(
    "document,label",
    [
        ({}, "empty"),
        ({"schema": "wrong/1", "full_suite_command": ["make", "test"]}, "wrong-schema"),
        (_contract(command=()), "empty-command"),
        ({"schema": "mission-suite-contract/1", "full_suite_command": "make test"},
         "command-not-argv"),
        ({"schema": "mission-suite-contract/1", "full_suite_command": ["make", 1]},
         "command-element-not-a-string"),
    ],
)
def test_a_malformed_contract_stops_the_gate(document, label):
    """A command given as one string would be split by whatever runs it.

    argv form is what makes the declaration mean the same thing everywhere.
    """
    with pytest.raises(gate.IntegrationGateError) as captured:
        gate.require_suite_contract(document, step=4)

    assert captured.value.reason == "suite-contract-invalid"


def test_a_valid_contract_returns_its_command():
    assert gate.require_suite_contract(_contract(), step=4) == ("make", "test")


TREE = "a" * 40


@pytest.mark.parametrize(
    "document,label",
    [
        (None, "absent"),
        ({}, "empty"),
        (_report(TREE, schema="wrong/1"), "wrong-schema"),
        (_report(TREE, executed=0), "zero-executed"),
        (_report(TREE, executed=-1), "negative-executed"),
        (_report(TREE, executed="12"), "executed-not-an-int"),
        (_report(TREE, status="partial"), "incomplete-status"),
        (_report("b" * 40), "tree-mismatch"),
        (_report(TREE[:-1]), "tree-not-a-sha"),
    ],
)
def test_a_report_that_cannot_prove_execution_stops_the_gate(document, label):
    """Zero tests is the case this issue exists for; the rest are its neighbours.

    `status` has to be checked too: a run that died halfway can still write a
    positive count.
    """
    with pytest.raises(gate.IntegrationGateError) as captured:
        gate.require_suite_report(document, expected_tree_sha=TREE, step=4)

    assert captured.value.reason == "suite-report-unusable"


def test_a_valid_report_passes_and_reports_its_count():
    assert gate.require_suite_report(
        _report(TREE, executed=4678), expected_tree_sha=TREE, step=4
    ) == 4678


def test_the_report_is_read_from_the_path_the_gate_chose(tmp_path):
    """Not from stdout, and not from wherever the repository felt like writing.

    Reading a path the gate names is what rules out an echoed line and a file
    left behind by an earlier run.
    """
    destination = tmp_path / "report.json"
    destination.write_text(json.dumps(_report(TREE)), encoding="utf-8")

    assert gate.read_suite_report(destination)["tree_sha"] == TREE
    assert gate.read_suite_report(tmp_path / "absent.json") is None


def test_an_unparseable_report_is_not_read_as_absent(tmp_path):
    """"Could not read it" and "there was none" must not collapse together."""
    destination = tmp_path / "report.json"
    destination.write_text("not json", encoding="utf-8")

    with pytest.raises(gate.IntegrationGateError) as captured:
        gate.read_suite_report(destination)

    assert captured.value.reason == "suite-report-unusable"


def test_the_contract_is_read_from_the_base_not_the_integrated_tree():
    """A PR that could rewrite the contract could point it at `true`.

    Reading the base pins **which command runs** to the one already merged.

    It does not pin what that command does.  A PR can change the `Makefile` it
    invokes, or the tests, and report truthfully on a hollowed-out suite --
    the same boundary the gate has always had, since a PR can delete tests.

    What this closes is narrower still: a run that exits zero without producing
    a valid report.  A runner that executes nothing but writes a well-formed
    report claiming one test still passes.
    """
    reads = []

    class Operations:
        def read_base_file(self, base_sha, path):
            reads.append((base_sha, path))
            return json.dumps(_contract())

    document = gate.load_suite_contract(Operations(), base_sha="b" * 40, step=4)

    assert document == _contract()
    assert reads == [("b" * 40, CONTRACT_PATH)]


def test_a_contract_absent_from_the_base_is_reported_with_its_bootstrap_path():
    """Failing closed here locks out a repository until it declares one.

    That is intentional -- an undeclared suite is the hole this closes -- but
    the operator has to be told how to get out of it, or the gate just looks
    broken.
    """
    class Operations:
        def read_base_file(self, base_sha, path):
            return None

    with pytest.raises(gate.IntegrationGateError) as captured:
        gate.load_suite_contract(Operations(), base_sha="b" * 40, step=4)

    assert captured.value.reason == "suite-contract-missing"
    assert CONTRACT_PATH in str(captured.value)


def test_the_report_path_is_passed_to_the_suite_through_the_environment():
    """The suite has to be told where to write, without changing its argv.

    An argv placeholder would need a substitution rule, and that rule would
    have to mean the same thing to every runner a repository might declare.
    An environment variable is the one channel every runner already has.
    """
    assert gate.SUITE_REPORT_ENV == "MISSION_SUITE_REPORT"


def test_the_gate_runs_the_declared_command_and_requires_its_report(tmp_path):
    """End to end over the contract: declared command, chosen path, real count."""
    calls = []
    report_path = tmp_path / "report.json"

    def runner(command, cwd, env=None):
        calls.append((tuple(command), (env or {}).get(gate.SUITE_REPORT_ENV)))
        report_path.write_text(
            json.dumps(_report(TREE, executed=4678)), encoding="utf-8"
        )
        return gate.CommandResult(0, "", "")

    executed = gate.run_declared_suite(
        ("make", "test"),
        runner=runner,
        cwd=tmp_path,
        report_path=report_path,
        expected_tree_sha=TREE,
        step=4,
    )

    assert executed == 4678
    assert calls == [(("make", "test"), str(report_path))]


def test_a_declared_suite_that_exits_zero_without_a_report_stops_the_gate(tmp_path):
    """This is the hole the issue exists for: exit 0 proves nothing ran."""
    report_path = tmp_path / "report.json"

    def runner(command, cwd, env=None):
        return gate.CommandResult(0, "0 tests ran", "")

    with pytest.raises(gate.IntegrationGateError) as captured:
        gate.run_declared_suite(
            ("make", "test"),
            runner=runner,
            cwd=tmp_path,
            report_path=report_path,
            expected_tree_sha=TREE,
            step=4,
        )

    assert captured.value.reason == "suite-report-unusable"


def test_a_failing_suite_still_reports_as_a_failing_suite(tmp_path):
    """The new check must not swallow the existing one."""
    def runner(command, cwd, env=None):
        return gate.CommandResult(1, "", "boom")

    with pytest.raises(gate.IntegrationGateError) as captured:
        gate.run_declared_suite(
            ("make", "test"),
            runner=runner,
            cwd=tmp_path,
            report_path=tmp_path / "report.json",
            expected_tree_sha=TREE,
            step=4,
        )

    assert captured.value.reason == "suite-failed"


def test_a_report_left_by_an_earlier_run_is_not_accepted(tmp_path):
    """A file already there proves nothing about the run about to happen.

    A runner that does nothing and exits 0, next to a leftover report, would
    otherwise satisfy every field check.
    """
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_report(TREE, executed=12)), encoding="utf-8")

    def runner(command, cwd, env=None):
        return gate.CommandResult(0, "", "")

    with pytest.raises(gate.IntegrationGateError) as captured:
        gate.run_declared_suite(
            ("make", "test"),
            runner=runner,
            cwd=tmp_path,
            report_path=report_path,
            expected_tree_sha=TREE,
            step=4,
        )

    assert captured.value.reason == "suite-report-unusable"


def test_a_dangling_symlink_is_unreadable_not_absent(tmp_path):
    """`exists()` follows links, so a broken one reads as "no report".

    That collapses the two cases this is supposed to keep apart.
    """
    report_path = tmp_path / "report.json"
    report_path.symlink_to(tmp_path / "gone.json")

    with pytest.raises(gate.IntegrationGateError) as captured:
        gate.read_suite_report(report_path)

    assert captured.value.reason == "suite-report-unusable"


@pytest.mark.parametrize(
    "executed,label",
    [
        (float("inf"), "infinity"),
        (True, "bool-is-not-a-count"),
    ],
    ids=["infinity", "bool"],
)
def test_a_count_that_is_not_a_count_is_rejected(executed, label):
    """`1e999` parses as `inf` rather than raising, so the count check is where
    it has to be caught.  `True` is an int in Python and would otherwise pass
    as a count of one.

    A large integer is not rejected: the contract sets no ceiling, and adding
    one would turn away a legitimately larger suite without making a forged
    count any harder.
    """
    document = _report(TREE)
    document["executed"] = executed

    with pytest.raises(gate.IntegrationGateError) as captured:
        gate.require_suite_report(document, expected_tree_sha=TREE, step=4)

    assert captured.value.reason == "suite-report-unusable"


def test_a_failing_suite_keeps_its_exit_code_in_the_log(tmp_path):
    """The existing `suite_exit=<actual>` line is how a failure is diagnosed."""
    logged = []

    def runner(command, cwd, env=None):
        return gate.CommandResult(3, "", "boom")

    with pytest.raises(gate.IntegrationGateError):
        gate.run_declared_suite(
            ("make", "test"),
            runner=runner,
            cwd=tmp_path,
            report_path=tmp_path / "report.json",
            expected_tree_sha=TREE,
            step=4,
            logger=logged.append,
        )

    assert "suite_exit=3" in logged


def test_the_real_runner_accepts_the_environment_the_gate_passes():
    """The fake would happily accept a keyword the real one does not.

    Without this, the wiring compiles in tests and raises TypeError in the gate.
    """
    import inspect

    assert "env" in inspect.signature(gate._local_runner).parameters


def test_the_operations_object_can_read_a_file_from_the_base():
    """The base read is a real capability, not something only a fake provides."""
    assert hasattr(gate.SubprocessGateOperations, "read_base_file")


def test_a_fabricated_report_is_accepted_and_that_is_documented():
    """State the limit as a test, so it cannot quietly be forgotten.

    The count is self-declared.  A runner that executes nothing and writes a
    well-formed report passes, and the docstrings say so.  Recording it here
    keeps the claim from drifting back to something stronger than the code.
    """
    # A report no run backs still passes.  That is the limit, not a defect.
    assert gate.require_suite_report(
        _report(TREE, executed=1), expected_tree_sha=TREE, step=4
    ) == 1

    # And the code has to say so, so the claim cannot drift back to a stronger
    # one than the implementation supports.
    assert "self-declaration" in (gate.load_suite_contract.__doc__ or "")


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_this_repository_declares_a_contract_the_gate_would_accept():
    """The gate reads the contract from the base, so it has to land first.

    Wiring the gate before the base carries a contract would fail every PR --
    including the one adding it.  This repository declares its own here so the
    call-site change can follow.
    """
    document = json.loads(
        (REPO_ROOT / CONTRACT_PATH).read_text(encoding="utf-8")
    )

    assert gate.require_suite_contract(document, step=4) == ("make", "test")


def test_the_suite_writes_a_report_the_gate_would_accept(tmp_path):
    """The declared command has to produce what the contract promises."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "write_suite_report", REPO_ROOT / "scripts" / "write_suite_report.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuites><testsuite>'
        '<testcase name="a"/><testcase name="b"><skipped/></testcase>'
        '<testcase name="c"/></testsuite></testsuites>',
        encoding="utf-8",
    )
    out = tmp_path / "report.json"

    module.main(["--junit", str(junit), "--out", str(out)])
    document = json.loads(out.read_text(encoding="utf-8"))

    # Skipped cases are excluded: a suite that skips everything has exercised
    # nothing, and counting skips would let it look like it had.
    assert document["executed"] == 2

    # The expected sha comes from the same command the gate uses, not from the
    # report itself.  Taking it from the report would make this pass whatever
    # the writer recorded -- including a tree the gate would reject.
    expected = subprocess.run(
        ["git", "write-tree"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert document["tree_sha"] == expected
    assert gate.require_suite_report(
        document, expected_tree_sha=expected, step=4
    ) == 2


def test_the_writer_records_the_tree_the_gate_observes():
    """The gate integrates without committing and reads the index.

    `HEAD^{tree}` is the committed tree; they differ exactly when the gate is
    doing its job, so recording the wrong one would fail every honest report.
    """
    import ast

    source = (REPO_ROOT / "scripts" / "write_suite_report.py").read_text(
        encoding="utf-8"
    )
    # Look at the literals the code actually runs, not at prose: the comment
    # explaining the distinction names both, and matching on text would either
    # forbid the explanation or accept the wrong command inside one.
    literals = {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and not _is_docstring_or_comment_text(node.value)
    }

    assert "write-tree" in literals
    assert "HEAD^{tree}" not in literals


def _is_docstring_or_comment_text(value: str) -> bool:
    """Whether a string literal is prose rather than an argument."""
    return "\n" in value or len(value) > 60


def test_the_makefile_passes_the_report_path_through_to_the_writer():
    """Without this the contract is declared but nothing honours it."""
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    assert gate.SUITE_REPORT_ENV in makefile
    assert "write_suite_report.py" in makefile


def test_the_suite_actually_writes_to_the_path_the_gate_named(tmp_path):
    """Run the declared command and look for the file at the named path.

    Reading the Makefile only shows the variable appears somewhere in it.  A
    writer told to put the report anywhere else -- the JUnit path, a fixed
    name -- leaves that check passing and the gate finding nothing.  The only
    way to know is to run it and look where the gate would look.

    A one-test target keeps this to a few seconds; the point is the wiring,
    not the suite.
    """
    report_path = tmp_path / "report.json"
    environment = dict(os.environ)
    environment[gate.SUITE_REPORT_ENV] = str(report_path)

    result = subprocess.run(
        ["make", "test",
         "PYTEST_TARGETS=skills/mission/tests/test_issue735_suite_contract.py"
         "::test_a_valid_contract_returns_its_command"],
        cwd=REPO_ROOT, env=environment, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert report_path.exists(), (
        "the suite ran but wrote no report where the gate would look"
    )
    document = json.loads(report_path.read_text(encoding="utf-8"))
    assert gate.require_suite_report(
        document, expected_tree_sha=document["tree_sha"], step=4
    ) >= 1
