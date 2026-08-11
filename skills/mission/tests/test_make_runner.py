"""Issue #391: local and CI tests share one deterministic Make entrypoint."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def _last_json_line(output: str) -> dict:
    return json.loads([line for line in output.splitlines() if line.startswith("{")][-1])


def test_make_smoke_runs_without_install_and_reports_tree_and_manifest() -> None:
    result = subprocess.run(
        ["make", "test-smoke"], cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    report = _last_json_line(result.stdout)
    assert report["schema"] == "mission-test-report/1"
    assert report["tier"] == "smoke"
    assert len(report["tree_sha"]) == 40
    assert report["test_manifest"] == [
        "skills/mission/bin/mission-state.py",
        "scripts/mission-audit.py",
        "scripts/mission-stop-guard.sh",
    ]
    assert not (REPO_ROOT / ".venv-ci").exists()


def test_ci_invokes_the_same_make_test_entrypoint() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "run: make test" in workflow
    assert "python -m pytest -q skills/mission" not in workflow

    dry_run = subprocess.run(
        ["make", "-n", "test"], cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert "pytest -q skills/mission" in dry_run.stdout
    assert '"tier":"full"' in dry_run.stdout
