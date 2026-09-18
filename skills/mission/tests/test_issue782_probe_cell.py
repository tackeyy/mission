"""#782: the cell report is written after every run, not once at the end.

A cell used to emit its counts only after the whole loop finished.  The job is
capped at 90 minutes and each run at 15, so the documented 10 repeats can be
cut off -- and a cell cut off wrote nothing at all.  `if-no-files-found:
ignore` then dropped it without a word, and the surviving cells printed a
bound: the failures that cell had already seen left both the numerator and the
denominator.

So the file is rewritten each time round, and it is moved into place rather
than written in place -- a kill during the write must not leave a partial line
that `probe_summarise.load_cells` would silently drop.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/probe_cell.py"

sys.path.insert(0, str(ROOT / "scripts"))

from probe_cell import build, write  # noqa: E402


def test_the_payload_is_what_the_summary_reads():
    assert build("main", 2, 1, 3, 6) == {
        "ref": "main",
        "runner": 2,
        "failed": 1,
        "no_result": 3,
        "passed": 6,
    }


def test_the_summary_can_read_what_this_writes(tmp_path):
    """The two ends of the contract, checked against each other."""
    sys.path.insert(0, str(ROOT / "scripts"))
    from probe_summarise import load_cells, totals

    cell = tmp_path / "cell-main-1"
    cell.mkdir()
    write(cell / "cell.json", build("main", 1, 2, 0, 8))
    assert totals(load_cells(tmp_path))["main"] == {"failed": 2, "no_result": 0, "passed": 8}


def test_a_rewrite_replaces_the_previous_counts(tmp_path):
    """Each run rewrites the file; the reader must see the latest, not a merge."""
    path = tmp_path / "cell.json"
    write(path, build("main", 1, 0, 0, 1))
    write(path, build("main", 1, 0, 0, 2))
    assert json.loads(path.read_text(encoding="utf-8"))["passed"] == 2


def test_no_temporary_file_is_left_behind(tmp_path):
    """`load_cells` globs for `cell.json`, but a stray tmp file is still litter."""
    path = tmp_path / "cell.json"
    write(path, build("main", 1, 0, 0, 1))
    assert [p.name for p in sorted(tmp_path.iterdir())] == ["cell.json"]


@pytest.mark.parametrize("counts", [(-1, 0, 0), (0, -2, 0), (0, 0, -3)])
def test_negative_counts_are_refused(counts):
    """A negative count would subtract from a denominator someone reads."""
    with pytest.raises(ValueError):
        build("main", 1, *counts)


def test_the_cli_writes_the_file_the_workflow_uploads(tmp_path):
    out = tmp_path / "cell.json"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT), "--out", str(out),
            "--ref", "feat/x", "--runner", "3",
            "--failed", "1", "--no-result", "2", "--passed", "7",
        ],
        capture_output=True, text=True, check=True,
    )
    assert result.returncode == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {
        "ref": "feat/x", "runner": 3, "failed": 1, "no_result": 2, "passed": 7,
    }


@pytest.mark.skipif(
    subprocess.run(["which", "python3.12"], capture_output=True).returncode != 0,
    reason="python3.12 is not installed here; CI pins it and runs this",
)
def test_it_runs_under_the_interpreter_ci_pins(tmp_path):
    out = tmp_path / "cell.json"
    result = subprocess.run(
        [
            "python3.12", str(SCRIPT), "--out", str(out),
            "--ref", "main", "--runner", "1",
            "--failed", "0", "--no-result", "0", "--passed", "4",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(out.read_text(encoding="utf-8"))["passed"] == 4
