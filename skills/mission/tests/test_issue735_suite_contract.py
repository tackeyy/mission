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
    What this closes is the case the gate could not see at all: a suite that
    never ran, exiting zero.
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
        (10 ** 400, "beyond-any-real-run"),
        (True, "bool-is-not-a-count"),
    ],
    ids=["infinity", "absurd", "bool"],
)
def test_a_count_that_cannot_be_a_real_run_is_rejected(executed, label):
    """`1e999` parses as `inf` rather than raising, so the count check is where
    it has to be caught.  `True` is an int in Python and would otherwise pass
    as a count of one.
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
