"""#782: the probe's verdict for one run, checked where the workflow cannot be.

Two defects reached review while this logic sat inside the workflow's shell
block, and neither was reachable by a test:

  * it was handed to `python3 -c` with its indentation, which the pinned 3.12
    refuses (`IndentationError: unexpected indent`).  A newer local
    interpreter dedents and hid it;
  * it read the suite report before the exit status, so a failing run -- which
    never gets a report, because `make test` stops at the failing pytest --
    would have been recorded as "nothing ran" and then dropped from the
    denominator by the summary.

So the cases below are the ones that decide whether `0 failed` means anything:
a failure, a timeout, a run that selected nothing, and a report that is absent
or unreadable.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/probe_classify.py"

sys.path.insert(0, str(ROOT / "scripts"))

from probe_classify import TIMEOUT_EXIT, classify  # noqa: E402


def _report(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "report.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_failing_run_is_a_failure_even_though_no_report_exists(tmp_path):
    """The case that would otherwise be counted as 'nothing ran'.

    `make test` runs write_suite_report only after pytest succeeds, so the
    report is missing for precisely the runs that matter most.
    """
    verdict, reason = classify(status=2, report_path=tmp_path / "absent.json")
    assert verdict == "failed", reason


def test_a_timed_out_run_is_a_failure():
    """A run that never finished is non-determinism, not a missing datum."""
    verdict, _ = classify(status=TIMEOUT_EXIT, report_path=Path("/nonexistent"))
    assert verdict == "failed"


def test_a_successful_run_that_executed_nothing_is_not_a_pass(tmp_path):
    """Exit 0 with no test case executed: a runtime skip or an empty selection."""
    verdict, reason = classify(status=0, report_path=_report(tmp_path, {"executed": 0}))
    assert verdict == "no-result", reason


def test_a_successful_run_with_no_report_is_not_a_pass(tmp_path):
    verdict, _ = classify(status=0, report_path=tmp_path / "absent.json")
    assert verdict == "no-result"


@pytest.mark.parametrize(
    "payload", [{"executed": "3"}, {"executed": None}, {}, [], "not json at all"]
)
def test_an_unreadable_report_is_not_a_pass(tmp_path, payload):
    path = tmp_path / "report.json"
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    verdict, _ = classify(status=0, report_path=path)
    assert verdict == "no-result"


def test_a_successful_run_that_executed_a_test_is_a_pass(tmp_path):
    verdict, _ = classify(status=0, report_path=_report(tmp_path, {"executed": 12}))
    assert verdict == "passed"


def test_the_cli_prints_one_token_the_shell_can_branch_on(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--status", "0", "--report", str(_report(tmp_path, {"executed": 1}))],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "passed"
    # The reason goes to stderr so the shell can read the verdict without
    # parsing around it.
    assert result.stderr.strip()


@pytest.mark.skipif(
    subprocess.run(["which", "python3.12"], capture_output=True).returncode != 0,
    reason="python3.12 is not installed here; CI pins it and runs this",
)
def test_it_runs_under_the_interpreter_ci_pins(tmp_path):
    """The defect that survived review: valid on 3.13+, a syntax error on 3.12.

    CI sets `python-version: '3.12'`. Running this file there is the check
    that the previous inline version could not have.
    """
    result = subprocess.run(
        ["python3.12", str(SCRIPT), "--status", "2", "--report", str(tmp_path / "absent.json")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "failed"
