"""The first-contract runbook must execute the existing suite evidence checks."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNBOOK = REPO_ROOT / "skills" / "mission" / "refs" / "state-management.md"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ("git", *args), cwd=cwd, text=True, capture_output=True, check=True
    ).stdout.strip()


def _driver_source() -> str:
    text = RUNBOOK.read_text(encoding="utf-8")
    match = re.search(
        r"<!-- bootstrap-suite-driver:start -->\s*```python\n(.*?)\n```\s*"
        r"<!-- bootstrap-suite-driver:end -->",
        text,
        flags=re.DOTALL,
    )
    assert match, "runbook must contain the executable bootstrap driver"
    return match.group(1)


def _scratch_tree_with_suite(tmp_path: Path, test_source: str):
    """Create an integrated tree whose runner reports a real unittest result."""
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    _git(repository, "config", "user.name", "Fixture")
    (repository / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "base.txt")
    _git(repository, "commit", "-m", "base")
    base = _git(repository, "rev-parse", "HEAD")

    (repository / ".mission").mkdir()
    (repository / ".mission" / "suite-contract.json").write_text(
        json.dumps({"schema": "mission-suite-contract/1", "full_suite_command": [sys.executable, "runner.py"]}),
        encoding="utf-8",
    )
    (repository / "runner.py").write_text(
        "import json, os, subprocess, unittest\n"
        "suite = unittest.defaultTestLoader.discover('suite_tests')\n"
        "result = unittest.TextTestRunner(verbosity=0).run(suite)\n"
        "tree = subprocess.check_output(['git', 'write-tree'], text=True).strip()\n"
        "with open(os.environ['MISSION_SUITE_REPORT'], 'w', encoding='utf-8') as report:\n"
        "    json.dump({'schema': 'mission-suite-report/1', 'tree_sha': tree, 'executed': result.testsRun, 'status': 'complete' if result.wasSuccessful() else 'failed'}, report)\n"
        "raise SystemExit(0 if result.wasSuccessful() else 1)\n",
        encoding="utf-8",
    )
    (repository / "suite_tests").mkdir()
    (repository / "suite_tests" / "test_contract.py").write_text(
        test_source, encoding="utf-8"
    )
    _git(repository, "add", ".mission/suite-contract.json", "runner.py", "suite_tests")
    _git(repository, "commit", "-m", "add suite contract")
    head = _git(repository, "rev-parse", "HEAD")

    scratch = tmp_path / "scratch"
    _git(repository, "worktree", "add", "--detach", str(scratch), base)
    _git(scratch, "merge", "--no-commit", "--no-ff", head)
    return repository, scratch


def test_bootstrap_driver_runs_a_real_declared_test_on_a_scratch_tree(tmp_path):
    """Detect a driver that accepts a self-reported count without a real suite.

    Existing #735 tests cover malformed contracts and reports. This test runs
    the published driver after a real no-commit integration and makes the
    declared runner execute a unittest, so the operational entrypoint proves
    the report is connected to actual test execution.
    """
    repository, scratch = _scratch_tree_with_suite(
        tmp_path,
        "import unittest\n\nclass ContractTest(unittest.TestCase):\n    def test_passes(self):\n        self.assertEqual(2 + 2, 4)\n",
    )
    try:
        driver = tmp_path / "bootstrap_suite_driver.py"
        driver.write_text(_driver_source(), encoding="utf-8")
        result = subprocess.run(
            (sys.executable, str(driver), str(scratch)),
            text=True,
            capture_output=True,
            check=True,
            env={**os.environ, "MISSION_PLUGIN_ROOT": str(REPO_ROOT)},
        )
    finally:
        _git(repository, "worktree", "remove", "--force", str(scratch))

    evidence = json.loads(result.stdout)
    assert evidence["command"] == [sys.executable, "runner.py"]
    assert evidence["executed"] == 1
    assert len(evidence["tree_sha"]) == 40


def test_bootstrap_driver_rejects_a_real_failing_declared_test(tmp_path):
    """Detect a driver that records success when the declared suite fails."""
    repository, scratch = _scratch_tree_with_suite(
        tmp_path,
        "import unittest\n\nclass ContractTest(unittest.TestCase):\n    def test_fails(self):\n        self.assertEqual(2 + 2, 5)\n",
    )
    try:
        driver = tmp_path / "bootstrap_suite_driver.py"
        driver.write_text(_driver_source(), encoding="utf-8")
        result = subprocess.run(
            (sys.executable, str(driver), str(scratch)),
            text=True,
            capture_output=True,
            check=False,
            env={**os.environ, "MISSION_PLUGIN_ROOT": str(REPO_ROOT)},
        )
    finally:
        _git(repository, "worktree", "remove", "--force", str(scratch))

    assert result.returncode != 0
    assert "suite-failed" in result.stderr


def test_bootstrap_runbook_keeps_the_owner_only_freshness_boundary():
    """Detect a manual path that could outlive the one approved PR revision.

    The driver test covers the evidence boundary.  This separate text contract
    covers the operational boundary that lets the owner, rather than an agent,
    make the one initial merge after fresh evidence is collected.
    """
    text = RUNBOOK.read_text(encoding="utf-8")
    section = text[text.index("## 初回 suite contract 導入 (#818)"):text.index("## Merge queue")]
    for required in (
        "owner",
        "PR 番号",
        "current base SHA",
        "current head SHA",
        "有効期限",
        "流用しない",
        "CI required checks green",
        "独立 review accepted",
        "read-back",
        "--match-head-commit <head>",
    ):
        assert required in section
