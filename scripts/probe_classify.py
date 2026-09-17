#!/usr/bin/env python3
"""Decide what one probe run means, where a test can reach it.

This lived inside the workflow's shell block and was wrong in two ways that
only showed up under review.  The code was passed to `python3 -c` with its
indentation intact, which the pinned 3.12 rejects outright (3.13 dedents, so a
newer local interpreter hid it) -- and the guard it implemented, once fixed,
would have classified every real failure as "nothing ran", because `make test`
stops at the failing pytest and never writes the report.

Both were invisible while the logic sat in YAML: nothing could run it.  It is
a file so that `test_issue782_probe_classify.py` can hand it the cases that
matter -- a failing run, a run that selected nothing, a missing report -- and
so that the interpreter the CI pins is one of the interpreters it runs under.

The order of the checks is the substance here:

    exit status first, report second.

A non-zero `make test` is a failure whether or not a report exists.  Reading
the report first turns "pytest failed, so the report was never written" into
"no test ran", and the summary then tells the reader to drop that run from the
denominator -- a reproducible failure would be reported as `0 failed` out of a
smaller N.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal

Verdict = Literal["failed", "passed", "no-result", "unsupported"]

# Exit status from `timeout(1)` when it killed the command.  A run that never
# finished is a failure of the test, not of the measurement: dropping it would
# hide exactly the kind of non-determinism this probe looks for.
TIMEOUT_EXIT = 124


def classify(status: int, report_path: Path) -> tuple[Verdict, str]:
    """Return the verdict for one run, with a reason the summary can print."""
    if status == TIMEOUT_EXIT:
        return "failed", "the run exceeded its time limit"
    if status != 0:
        # Deliberately before reading the report: `make test` runs
        # write_suite_report only after pytest succeeds, so a failing run has
        # no report to read.
        return "failed", f"make test exited {status}"

    if not report_path.exists():
        # Success without a report is not "nothing ran".  The recipe runs
        # under `set -eu`, so a report writer that failed would have failed
        # the run; a run that succeeded and left no report used a recipe that
        # never calls the writer.  That is a property of the ref, not of this
        # run, so every further repeat would say the same thing -- the probe
        # stops on this verdict rather than spending them.
        #
        # Observed rather than predicted.  Asking `make -n` whether the recipe
        # mentions the writer was tried and failed twice in review: recipe
        # comments and unrelated `echo`s matched, and `grep -q` closing the
        # pipe early made `make` die of SIGPIPE on a ref that *does* report.
        return (
            "unsupported",
            "the run succeeded but wrote no suite report; the ref's test recipe "
            "predates the report writer (#740) and cannot be measured",
        )

    try:
        executed = json.loads(report_path.read_text(encoding="utf-8"))["executed"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        return "no-result", f"the report could not be read: {error}"

    if not isinstance(executed, int) or executed < 1:
        # Exit 0 with nothing executed: every test skipped at runtime, or a
        # report that lost its count.  `Makefile` makes the same point about
        # the suite: an exit code cannot tell a pass from a run that did
        # nothing.
        #
        # An empty *selection* does not reach here.  The recipe runs under
        # `set -eu` and pytest exits 5 for a path that matches nothing, so it
        # is caught by the status check above and counted as a failure -- which
        # is why the workflow refuses to start a cell whose ref lacks the file.
        return "no-result", f"the run executed {executed} test cases"

    return "passed", f"the run executed {executed} test cases"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=int, required=True, help="exit status of make test")
    parser.add_argument("--report", type=Path, required=True, help="suite report path")
    args = parser.parse_args(argv)

    verdict, reason = classify(args.status, args.report)
    # One token for the shell to branch on, then the reason for a human.
    print(verdict)
    print(reason, file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI test
    raise SystemExit(main())
