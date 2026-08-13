"""Fast-path file-scope detection for CI."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "scripts" / "ci_changed_scopes.js"
NODE = shutil.which("node") or shutil.which("nodejs")
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ci_changed_scopes"
FAST_PATH_TARGETS = [
    "skills/mission/tests/test_artifact_hygiene.py",
    "skills/mission/tests/test_vendor_fingerprint.py",
    "skills/mission/tests/test_plugins_in_sync.py",
    "skills/mission/tests/test_actions_cost_guard.py",
    "skills/mission/tests/test_doc_consistency.py",
]


def _classify(*, event_name: str, files) -> dict:
    if NODE is None:
        pytest.skip("node is required to exercise the CI helper")

    payload = json.dumps({"eventName": event_name, "files": files})
    script = (
        "const helper = require(process.argv[1]);"
        "const input = JSON.parse(process.argv[2]);"
        "const result = helper.classifyChangedFiles(input);"
        "process.stdout.write(JSON.stringify(result));"
    )
    result = subprocess.run(
        [NODE, "-e", script, str(HELPER), payload],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _fixture(name: str) -> list[str]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_docs_only_pr_uses_guard_fast_path():
    result = _classify(event_name="pull_request", files=_fixture("docs_only.json"))

    assert result["runAll"] is False
    assert result["docsOnly"] is True
    assert result["python"] is True
    assert result["pythonTargets"] == " ".join(FAST_PATH_TARGETS)
    assert result["shell"] is False


def test_code_change_pr_falls_back_to_full_suite():
    result = _classify(event_name="pull_request", files=_fixture("code_change.json"))

    assert result["runAll"] is False
    assert result["docsOnly"] is False
    assert result["pythonTargets"] == "skills/mission"
    assert result["shell"] is False


def test_workflow_change_forces_full_suite_and_shellcheck():
    result = _classify(event_name="pull_request", files=_fixture("workflow_change.json"))

    assert result["runAll"] is False
    assert result["docsOnly"] is False
    assert result["pythonTargets"] == "skills/mission"
    assert result["shell"] is True


def test_merge_group_event_stays_on_full_suite_even_for_docs_only_changes():
    result = _classify(event_name="merge_group", files=_fixture("docs_only.json"))

    assert result["runAll"] is True
    assert result["docsOnly"] is False
    assert result["pythonTargets"] == "skills/mission"
    assert result["shell"] is True


def test_empty_or_invalid_file_list_fails_safe_to_full_suite():
    result = _classify(event_name="pull_request", files=None)

    assert result["runAll"] is True
    assert result["docsOnly"] is False
    assert result["pythonTargets"] == "skills/mission"
    assert result["shell"] is True
