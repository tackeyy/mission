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


def test_bootstrap_driver_runs_the_declared_suite_on_a_real_scratch_tree(tmp_path):
    """Detect a runbook driver that stops using the contract/report boundary.

    Existing #735 tests cover malformed contracts and reports. This test instead
    executes the published driver after a real no-commit scratch integration,
    so a stale tree SHA, missing environment report path, or direct subprocess
    shortcut fails at the operator entrypoint.
    """
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
        "import json, os, subprocess\n"
        "tree = subprocess.check_output(['git', 'write-tree'], text=True).strip()\n"
        "with open(os.environ['MISSION_SUITE_REPORT'], 'w', encoding='utf-8') as report:\n"
        "    json.dump({'schema': 'mission-suite-report/1', 'tree_sha': tree, 'executed': 1, 'status': 'complete'}, report)\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".mission/suite-contract.json", "runner.py")
    _git(repository, "commit", "-m", "add suite contract")
    head = _git(repository, "rev-parse", "HEAD")

    scratch = tmp_path / "scratch"
    _git(repository, "worktree", "add", "--detach", str(scratch), base)
    try:
        _git(scratch, "merge", "--no-commit", "--no-ff", head)
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


def test_bootstrap_driver_never_performs_remote_or_state_operations():
    """The published executable is evidence collection, never an approval bypass."""
    source = _driver_source()
    for forbidden in ("gh", "git merge", "mission-state", "approve"):
        assert forbidden not in source


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
