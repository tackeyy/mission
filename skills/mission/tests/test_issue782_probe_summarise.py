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
        ["python3.12", str(SCRIPT), "--cells", str(tmp_path), "--requested-per-ref", "20"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "20 measured runs" in result.stdout
