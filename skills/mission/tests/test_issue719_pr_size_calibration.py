"""Issue #719: the PR-size thresholds are calibrated to this repository.

The shared rule sets 400 / 1,000 lines as pre-calibration defaults and tells
each repository to replace them with its own p65 / p85.  Uncalibrated, 51 of the
last 100 merged PRs exceed the accountability threshold and 22 exceed the
"must split" one -- a rule that fires on a fifth of all work stops being read.

Calibrating requires deciding what counts as reviewed area, and this repository
has one large mechanical contributor: `plugins/mission/` is a byte-identical
copy of `skills/mission/`, enforced by test_plugins_in_sync.py.  A human reviews
that content once, not twice.

These tests bind the documented numbers to the script that computes them, so a
threshold changed in prose without changing the measurement -- or an allowlist
entry added to one and not the other -- fails here.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "pr_size.py"
AGENTS = REPO_ROOT / "AGENTS.md"


def _load_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location("pr_size", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _documented_section() -> str:
    text = AGENTS.read_text(encoding="utf-8")
    parts = text.split("## PR Size Calibration", 1)
    assert len(parts) == 2, "AGENTS.md is missing the calibration section"
    return parts[1].split("\n## ", 1)[0]


def test_the_documented_thresholds_match_the_script():
    """Prose and measurement must not drift apart.

    Checking that each constant appears somewhere in the section is not enough:
    the section is full of numbers, so an unrelated one can satisfy it, and the
    two thresholds can be swapped without anything noticing.  Parse the row the
    number belongs to.
    """
    module = _load_script()
    section = _documented_section()

    rows = dict(
        (label.strip(), int(value.replace(",", "").replace("**", "").strip()))
        for label, value in re.findall(
            r"^\| ([^|]*\(p\d+\)[^|]*) \| ([^|]+) \|", section, re.MULTILINE
        )
    )
    assert len(rows) == 2, rows

    accountability = next(v for k, v in rows.items() if "p65" in k)
    split_required = next(v for k, v in rows.items() if "p85" in k)

    assert accountability == module.ACCOUNTABILITY_THRESHOLD
    assert split_required == module.SPLIT_REQUIRED_THRESHOLD
    # Swapping them would keep both numbers present but invert the rule.
    assert module.ACCOUNTABILITY_THRESHOLD < module.SPLIT_REQUIRED_THRESHOLD


def test_the_calibration_test_runs_on_a_docs_only_change():
    """AGENTS.md alone takes the docs-only fast path in CI.

    The thresholds live there, so without this the drift check would never run
    on the very change most likely to cause drift.
    """
    scopes = (REPO_ROOT / "scripts" / "ci_changed_scopes.js").read_text(encoding="utf-8")
    assert "test_issue719_pr_size_calibration.py" in scopes


def test_the_documented_allowlist_matches_the_script():
    """A path excluded from the measurement must be visible in the rules.

    Widening the allowlist is how a threshold gets evaded, so the two lists
    have to be the same list.
    """
    module = _load_script()
    section = _documented_section()

    for pattern in module.GENERATED_PATTERNS:
        assert pattern in section, f"{pattern} is excluded but not documented"

    block = section.split("### Generated-artifact allowlist", 1)[1].split("###", 1)[0]
    documented = set(re.findall(r"^- `([^`]+)`", block, re.MULTILINE))
    assert documented == set(module.GENERATED_PATTERNS), (
        "the documented allowlist and the measured one differ"
    )


def test_the_mirror_is_excluded_and_the_source_is_not():
    """The mirror is the reason this repository needed its own calibration."""
    module = _load_script()

    assert module.is_generated("plugins/mission/skills/mission/bin/mission-state.py")
    assert not module.is_generated("skills/mission/bin/mission-state.py")


@pytest.mark.parametrize(
    "path,expected",
    [
        ("benchmarks/run-1/artifacts/out.json", True),
        ("uv.lock", True),
        ("package-lock.json", True),
        ("skills/mission/tests/__snapshots__/x.snap", True),
        ("docs/design/665-integration-gate.md", False),
        ("plugins-not-a-mirror/file.py", False),
        # The sync tests skip cache directories, so nothing holds these
        # identical to a source.  Exempting them would hide a force-added file
        # from both the comparison and the measurement.
        ("plugins/mission/skills/__pycache__/x.txt", False),
        ("plugins/mission/scripts/.pytest_cache/x.txt", False),
        # Git allows leading and trailing spaces; those are different files.
        (" plugins/mission/skills/x.py", False),
        ("docs/package-lock.json ", False),
    ],
)
def test_generated_classification(path, expected):
    assert _load_script().is_generated(path) is expected


def test_the_script_reports_both_totals():
    """Reviewers need the excluded amount too, or the number looks arbitrary."""
    module = _load_script()

    result = module.measure([
        {"path": "skills/mission/a.py", "additions": 10, "deletions": 5},
        {"path": "plugins/mission/skills/mission/a.py", "additions": 10, "deletions": 5},
        {"path": "uv.lock", "additions": 100, "deletions": 0},
    ])

    assert result["raw"] == 130
    assert result["reviewed"] == 15
    assert result["generated"] == 115
    assert result["verdict"] == "ok"


def test_the_script_names_the_band_it_lands_in():
    module = _load_script()

    def verdict(total):
        return module.measure(
            [{"path": "skills/mission/a.py", "additions": total, "deletions": 0}]
        )["verdict"]

    assert verdict(module.ACCOUNTABILITY_THRESHOLD - 1) == "ok"
    assert verdict(module.ACCOUNTABILITY_THRESHOLD + 1) == "explain"
    assert verdict(module.SPLIT_REQUIRED_THRESHOLD + 1) == "split-required"


def test_the_cli_runs_on_a_diff():
    """The check is self-reported; it has to be one command to be used at all."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--pr" in result.stdout


def test_the_rules_say_the_check_is_not_enforced_by_ci():
    """The shared rule requires saying so explicitly rather than implying it."""
    section = _documented_section()
    assert "CI" in section
    assert "自己申告" in section
