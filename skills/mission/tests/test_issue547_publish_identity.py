"""Issue #547: aggregate output publication stays isolated and fail-closed."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
from pathlib import Path

import pytest


MISSION_STATE_PY = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"


def _load_mission_state():
    spec = importlib.util.spec_from_file_location("mission_state_issue547", MISSION_STATE_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _review(path: Path) -> Path:
    path.write_text(json.dumps({
        "schema": "mission-review/1",
        "perspective": "quality",
        "iteration": 1,
        "scores": {
            "mission_achievement": 4.5,
            "accuracy": 4.4,
            "completeness": 4.3,
            "usability": 4.2,
        },
        "findings": [],
        "same_score_note": None,
        "notes": "review",
    }))
    return path


def test_default_aggregate_output_is_isolated_by_project(
    state_dir, run_cli, tmp_path,
):
    second_root = tmp_path / "second-project"
    shutil.copytree(state_dir, second_root / ".mission-state")
    second_state = second_root / ".mission-state" / "sessions" / "test.json"
    second_payload = json.loads(second_state.read_text())
    second_payload["project_root"] = str(second_root)
    second_state.write_text(json.dumps(second_payload))

    first = run_cli(
        "aggregate-reviews",
        "--iteration", "1",
        "--input", str(_review(tmp_path / "first-review.json")),
        "--json",
        cwd=state_dir.parent,
    )
    second = run_cli(
        "aggregate-reviews",
        "--iteration", "1",
        "--input", str(_review(second_root / "second-review.json")),
        "--json",
        cwd=second_root,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_out = Path(json.loads(first.stdout)["out"])
    second_out = Path(json.loads(second.stdout)["out"])
    assert first_out != second_out
    assert first_out.parent == state_dir / "tmp"
    assert second_out.parent == second_root / ".mission-state" / "tmp"
    assert first_out.read_bytes() == second_out.read_bytes()


def test_output_publish_rejects_same_size_file_replacement(tmp_path, monkeypatch):
    module = _load_mission_state()
    output = tmp_path / "score.json"
    content = b"same-size-output\n"
    original_link = module.os.link
    replaced = False

    def replace_target_after_link(source, target, **kwargs):
        nonlocal replaced
        result = original_link(source, target, **kwargs)
        if target == output.name and not replaced:
            replaced = True
            directory_fd = kwargs["dst_dir_fd"]
            module.os.unlink(target, dir_fd=directory_fd)
            competitor = tmp_path / "competitor.json"
            competitor.write_bytes(content)
            module.os.replace(
                competitor.name,
                target,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        return result

    monkeypatch.setattr(module.os, "link", replace_target_after_link)

    with pytest.raises(ValueError, match="output publish changed"):
        module._publish_output_transaction(output, content)

    assert replaced is True
    # Publication failure rolls back its own link; the competing replacement remains.
    assert output.read_bytes() == content
    assert os.stat(output).st_size == len(content)
