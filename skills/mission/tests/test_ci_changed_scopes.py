"""Fast-path file-scope detection for CI."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
HELPER = REPO_ROOT / "scripts" / "ci_changed_scopes.js"
NODE = shutil.which("node") or shutil.which("nodejs")
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ci_changed_scopes"
def _fast_path_targets_from_helper() -> list[str]:
    """単一ソース: JS 側の FAST_PATH_TARGETS 定義を正規表現で抽出する (#448 二重定義排除)。"""
    text = HELPER.read_text(encoding="utf-8")
    block = re.search(r"const FAST_PATH_TARGETS = \[(.*?)\]\.join", text, re.DOTALL)
    assert block, "FAST_PATH_TARGETS definition not found in helper"
    return re.findall(r'"([^"]+)"', block.group(1))


FAST_PATH_TARGETS = _fast_path_targets_from_helper()


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


def test_code_change_pr_falls_back_to_full_suite():
    result = _classify(event_name="pull_request", files=_fixture("code_change.json"))

    assert result["runAll"] is False
    assert result["docsOnly"] is False
    assert result["pythonTargets"] == "skills/mission"


def test_workflow_change_forces_full_suite():
    result = _classify(event_name="pull_request", files=_fixture("workflow_change.json"))

    assert result["runAll"] is False
    assert result["docsOnly"] is False
    assert result["pythonTargets"] == "skills/mission"


def test_merge_group_event_stays_on_full_suite_even_for_docs_only_changes():
    result = _classify(event_name="merge_group", files=_fixture("docs_only.json"))

    assert result["runAll"] is True
    assert result["docsOnly"] is False
    assert result["pythonTargets"] == "skills/mission"


def test_empty_or_invalid_file_list_fails_safe_to_full_suite():
    result = _classify(event_name="pull_request", files=None)

    assert result["runAll"] is True
    assert result["docsOnly"] is False
    assert result["pythonTargets"] == "skills/mission"


def test_fast_path_targets_exist_and_cover_repo_wide_guards():
    assert "skills/mission/tests/test_codex_wrapper_sync.py" in FAST_PATH_TARGETS
    for target in FAST_PATH_TARGETS:
        assert (REPO_ROOT / target).exists(), target


def test_code_file_under_docs_is_not_fast_pathed():
    result = _classify(event_name="pull_request", files=["docs/design/helper.py"])
    assert result["docsOnly"] is False
    assert result["pythonTargets"] == "skills/mission"
