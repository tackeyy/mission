"""#782: the probe's input validation, run rather than read.

The contract tests next door assert that certain lines appear in the workflow.
That catches a deleted check, but not a check that was kept and weakened: an
independent review pointed out that removing a guard while leaving its message
behind keeps those tests green.

The plan job's validation is an embedded Python snippet, so it can be lifted
out of the YAML and executed.  These cases drive it directly with the inputs a
dispatcher can type, and assert on what it does.

The two that matter most are the ones a reviewer found by running it:

  * `runners: 1` reproduces the confounding the workflow's own header forbids;
  * `main,main` passed the "two or more entries" check and produced a run that
    compared a ref with itself -- both arms reporting, nothing missing, and a
    clean bound printed for a comparison that never happened.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PROBE = (ROOT / ".github/workflows/flaky-probe.yml").read_text(encoding="utf-8")

VALID_TEST_FILE = "skills/mission/tests/test_issue782_plan_validation.py"


def _snippet() -> str:
    """Lift the plan job's validation out of the workflow.

    Fails loudly rather than skipping: a snippet that can no longer be found
    means the workflow moved, and a test that quietly skips in that case is the
    kind of check this file exists to replace.
    """
    marker = "python3 - <<'PY' >> \"$GITHUB_OUTPUT\"\n"
    assert marker in PROBE, "the plan job no longer embeds its validation the same way"
    body = PROBE.split(marker, 1)[1].split("\n          PY\n", 1)[0]
    return textwrap.dedent(body)


def _run(tmp_path: Path, **env):
    script = tmp_path / "plan.py"
    script.write_text(_snippet(), encoding="utf-8")
    settings = {
        "REFS": "main,topic",
        "RUNNERS": "4",
        "REPEATS": "10",
        "TEST_FILE": VALID_TEST_FILE,
    }
    settings.update(env)
    return subprocess.run(
        [sys.executable, str(script)],
        env={**os.environ, **settings},
        capture_output=True,
        text=True,
    )


def test_a_valid_dispatch_produces_the_ref_by_runner_matrix(tmp_path):
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    matrix = json.loads(result.stdout.strip().removeprefix("matrix="))
    include = matrix["include"]
    assert len(include) == 8, "4 runners x 2 refs"
    assert {cell["ref"] for cell in include} == {"main", "topic"}
    assert {cell["runner"] for cell in include} == {1, 2, 3, 4}


@pytest.mark.parametrize(
    "label,env,expected",
    [
        ("one runner per ref", {"RUNNERS": "1", "REPEATS": "40"}, "runners must be 2 or more"),
        ("a ref against itself", {"REFS": "main,main"}, "refs repeats an entry"),
        ("a ref against itself, spaced", {"REFS": "main, main "}, "refs repeats an entry"),
        ("one ref", {"REFS": "main"}, "one ref is not a comparison"),
        ("too few runs", {"RUNNERS": "2", "REPEATS": "2"}, "too few to say anything"),
        ("a pull request ref", {"REFS": "main,refs/pull/9/head"}, "refs/pull"),
        ("a traversing ref", {"REFS": "main,../etc"}, "not a plain ref name"),
        ("a flag as the test file", {"TEST_FILE": "--collect-only"}, "must be a file under"),
        ("a test file outside the suite", {"TEST_FILE": "scripts/probe_cell.py"}, "must be a file under"),
        ("zero runners", {"RUNNERS": "0", "REPEATS": "40"}, "positive integer"),
        ("a non-numeric repeat", {"REPEATS": "ten"}, "positive integer"),
        ("a non-ASCII digit", {"RUNNERS": "\u0664", "REPEATS": "10"}, "positive integer"),
    ],
)
def test_the_dispatch_is_refused(tmp_path, label, env, expected):
    result = _run(tmp_path, **env)
    assert result.returncode == 2, f"{label}: accepted, stdout={result.stdout!r}"
    assert expected in result.stderr, f"{label}: {result.stderr!r}"
    assert "matrix=" not in result.stdout, f"{label}: emitted a matrix anyway"


def test_a_refused_dispatch_names_the_problem_rather_than_crashing(tmp_path):
    """A traceback loses the `::error::` annotation the dispatcher reads."""
    result = _run(tmp_path, RUNNERS="\u0664")
    assert "Traceback" not in result.stderr, result.stderr
    assert result.stderr.startswith("::error::"), result.stderr
