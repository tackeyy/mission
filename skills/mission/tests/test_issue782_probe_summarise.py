"""#782: the summary must bound what was measured, not what was asked for.

The first version computed the bound in the workflow from the two input
numbers, never reading what the cells reported. A run where half the cells
never started, or where every run executed nothing, produced the same sentence
as a clean 40-run comparison -- and that sentence is what a reader uses to
decide whether a change introduced a flake.

The cases below are the ones where "requested" and "measured" come apart.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/probe_summarise.py"

sys.path.insert(0, str(ROOT / "scripts"))

from probe_summarise import load_cells, render, totals, upper_bound  # noqa: E402


def _cells(tmp_path: Path, *payloads: dict) -> Path:
    for index, payload in enumerate(payloads):
        directory = tmp_path / f"cell-{index}"
        directory.mkdir()
        (directory / "cell.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def test_nothing_reported_produces_no_bound(tmp_path):
    """An empty denominator must not read like a clean result."""
    out = render([], requested_per_ref=40, probe_result="failure")
    assert "No cell reported" in out
    assert "%" not in out, "a bound was printed with nothing to bound"


def test_the_bound_uses_measured_runs_not_requested(tmp_path):
    """Half the cells missing must widen the bound, not keep it.

    40 requested, 10 measured: the honest bound is the one for 10.
    """
    out = render(
        [{"ref": "main", "failed": 0, "no_result": 0, "passed": 10}],
        requested_per_ref=40,
        probe_result="success",
    )
    assert f"{upper_bound(10):.1%}" in out
    assert f"{upper_bound(40):.1%}" not in out
    assert "10 measured runs" in out


def test_no_result_runs_are_excluded_from_the_denominator():
    """They executed no test case, so they say nothing about the rate."""
    out = render(
        [{"ref": "main", "failed": 0, "no_result": 30, "passed": 10}],
        requested_per_ref=40,
        probe_result="success",
    )
    assert "10 measured runs" in out
    assert f"{upper_bound(10):.1%}" in out


def test_a_ref_where_everything_was_a_no_result_gets_no_bound():
    out = render(
        [{"ref": "main", "failed": 0, "no_result": 40, "passed": 0}],
        requested_per_ref=40,
        probe_result="success",
    )
    assert "nothing was measured" in out
    assert "invisible at this count" not in out


def test_failures_are_reported_as_a_rate_not_a_bound():
    out = render(
        [{"ref": "main", "failed": 3, "no_result": 0, "passed": 17}],
        requested_per_ref=20,
        probe_result="success",
    )
    assert "3 of 20 failed" in out
    assert "15.0%" in out


def test_cells_are_summed_per_ref():
    counts = totals(
        [
            {"ref": "main", "failed": 1, "no_result": 0, "passed": 9},
            {"ref": "main", "failed": 0, "no_result": 2, "passed": 8},
            {"ref": "topic", "failed": 0, "no_result": 0, "passed": 10},
        ]
    )
    assert counts["main"] == {"failed": 1, "no_result": 2, "passed": 17}
    assert counts["topic"] == {"failed": 0, "no_result": 0, "passed": 10}


def test_an_unreadable_cell_is_dropped_rather_than_guessed(tmp_path):
    """Its counts are unknown; inventing them would put them in the denominator."""
    good = tmp_path / "cell-a"
    good.mkdir()
    (good / "cell.json").write_text(json.dumps({"ref": "main", "passed": 5}), encoding="utf-8")
    bad = tmp_path / "cell-b"
    bad.mkdir()
    (bad / "cell.json").write_text("{ this is not json", encoding="utf-8")

    cells = load_cells(tmp_path)
    assert len(cells) == 1
    assert cells[0]["ref"] == "main"


def test_a_partial_probe_says_so(tmp_path):
    out = render(
        [{"ref": "main", "failed": 0, "no_result": 0, "passed": 5}],
        requested_per_ref=40,
        probe_result="failure",
    )
    assert "did not succeed" in out


@pytest.mark.parametrize("measured,expected", [(1, 0.95), (20, 0.139), (40, 0.072)])
def test_the_bound_matches_the_rule_of_three(measured, expected):
    assert upper_bound(measured) == pytest.approx(expected, abs=0.001)


def test_a_bound_with_no_runs_is_refused():
    with pytest.raises(ValueError):
        upper_bound(0)


@pytest.mark.skipif(
    subprocess.run(["which", "python3.12"], capture_output=True).returncode != 0,
    reason="python3.12 is not installed here; CI pins it and runs this",
)
def test_it_runs_under_the_interpreter_ci_pins(tmp_path):
    _cells(tmp_path, {"ref": "main", "failed": 0, "no_result": 0, "passed": 20})
    result = subprocess.run(
        ["python3.12", str(SCRIPT), "--cells", str(tmp_path), "--runners", "4", "--repeats", "5"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "20 measured runs" in result.stdout


# --- A ref that reported nothing at all -------------------------------------
#
# The case that made the first dispatch dangerous: `origin/main` does not carry
# these scripts, so every cell on that arm dies before writing `cell.json`.
# Summarising only what reported left the topic branch alone in the table with
# `measured 40 / requested 40` and a 7.2% bound -- a sentence that reads as a
# finished comparison when no comparison happened.


def test_a_requested_ref_that_never_reported_still_appears():
    out = render(
        [{"ref": "topic", "failed": 0, "no_result": 0, "passed": 40}],
        requested_per_ref=40,
        probe_result="success",
        requested_refs=["main", "topic"],
    )
    assert "| `main` | 0 | 0 | 0 | 40 |" in out
    assert "Nothing was measured for `main`" in out


def test_a_missing_arm_suppresses_the_surviving_arms_bound():
    """One arm's bound is not the answer to a comparison that never ran."""
    out = render(
        [{"ref": "topic", "failed": 0, "no_result": 0, "passed": 40}],
        requested_per_ref=40,
        probe_result="success",
        requested_refs=["main", "topic"],
    )
    assert f"{upper_bound(40):.1%}" not in out
    assert "40 measured runs" in out, "the count itself is still worth printing"
    assert "missing an arm" in out


def test_both_refs_reporting_keeps_the_bound():
    """The suppression must not fire when the comparison is whole."""
    out = render(
        [
            {"ref": "main", "failed": 0, "no_result": 0, "passed": 40},
            {"ref": "topic", "failed": 0, "no_result": 0, "passed": 40},
        ],
        requested_per_ref=40,
        probe_result="success",
        requested_refs=["main", "topic"],
    )
    assert f"{upper_bound(40):.1%}" in out
    assert "Nothing was measured for" not in out


def test_failure_counts_survive_a_missing_arm():
    """Suppressing the bound must not suppress an observed failure."""
    out = render(
        [{"ref": "topic", "failed": 3, "no_result": 0, "passed": 17}],
        requested_per_ref=20,
        probe_result="success",
        requested_refs=["main", "topic"],
    )
    assert "3 of 20 failed" in out


def test_the_cli_takes_the_requested_refs(tmp_path):
    _cells(tmp_path, {"ref": "topic", "failed": 0, "no_result": 0, "passed": 20})
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--cells",
            str(tmp_path),
            "--runners",
            "4",
            "--repeats",
            "5",
            "--refs",
            "main, topic",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "Nothing was measured for `main`" in result.stdout
    assert f"{upper_bound(20):.1%}" not in result.stdout


# --- An arm that reported, but measured nothing ------------------------------
#
# Keying the suppression on "no cell reported" left this case out: a ref whose
# every run executed nothing still appears in the table, so it was not silent,
# and the other arm printed a clean bound beside an empty column.


def test_an_all_no_result_arm_also_suppresses_the_other_arms_bound():
    out = render(
        [
            {"ref": "main", "failed": 0, "no_result": 40, "passed": 0},
            {"ref": "topic", "failed": 0, "no_result": 0, "passed": 40},
        ],
        requested_per_ref=40,
        probe_result="success",
        requested_refs=["main", "topic"],
    )
    assert f"{upper_bound(40):.1%}" not in out, "a bound was printed beside an empty arm"
    assert "Nothing was measured for `main`" in out
    assert "40 measured runs" in out


def test_an_all_no_result_arm_without_requested_refs_keeps_the_old_reading():
    """Not being told what was requested must not invent a suppression."""
    out = render(
        [{"ref": "main", "failed": 0, "no_result": 40, "passed": 0}],
        requested_per_ref=40,
        probe_result="success",
    )
    assert "nothing was measured" in out
    assert "%" not in out


def test_the_cli_multiplies_runners_by_repeats(tmp_path):
    _cells(tmp_path, {"ref": "main", "failed": 0, "no_result": 0, "passed": 6})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--cells", str(tmp_path), "--runners", "3", "--repeats", "7"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "| 6 | 21 |" in result.stdout, result.stdout


@pytest.mark.parametrize("bad", ["3x", "$(echo hi)", "", "2 3"])
def test_the_cli_refuses_counts_that_are_not_integers(tmp_path, bad):
    """The summary job runs with `if: always()`, so the plan job's validation
    does not gate these values. argparse has to."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--cells", str(tmp_path), "--runners", bad, "--repeats", "5"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, f"{bad!r} was accepted"
