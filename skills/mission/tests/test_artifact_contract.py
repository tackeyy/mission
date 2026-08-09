import contextlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
MISSION_STATE_PATH = Path(__file__).resolve().parent.parent / "bin" / "mission-state.py"

SPEC = importlib.util.spec_from_file_location("mission_state_artifact_contract", MISSION_STATE_PATH)
MISSION_STATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MISSION_STATE)

from artifact_contract import (  # noqa: E402
    ArtifactContractError,
    capture_artifact_identity,
)


@pytest.mark.parametrize("path_text", ["../outside.md", "reports/\x00result.md"])
def test_canonical_paths_reject_escape_and_nul(tmp_path, path_text):
    with pytest.raises(ArtifactContractError):
        capture_artifact_identity(tmp_path, path_text, "portable-run")


def test_artifact_mutation_during_single_descriptor_read_is_rejected(
    tmp_path, monkeypatch
):
    artifact = tmp_path / "result.md"
    artifact.write_bytes(b"a" * (128 * 1024))
    original_read = MISSION_STATE.os.read
    changed = False

    def mutate_after_first_read(fd, size):
        nonlocal changed
        chunk = original_read(fd, size)
        if chunk and not changed:
            changed = True
            artifact.write_bytes(b"b" * (128 * 1024))
        return chunk

    import artifact_contract

    monkeypatch.setattr(artifact_contract.os, "read", mutate_after_first_read)

    with pytest.raises(ArtifactContractError, match="changed while it was being read"):
        capture_artifact_identity(tmp_path, "result.md", "portable-run")


def test_advance_artifact_handoff_state_is_atomic_on_write_failure(
    state_dir, read_state, monkeypatch
):
    root = state_dir.parent
    state_path = state_dir / "sessions" / "test.json"
    state = read_state(state_dir)
    state["phase"] = "executing"
    state["artifact_applicability"] = "pending"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    before = state_path.read_bytes()
    (root / "result.md").write_text("# Result\nverified\n", encoding="utf-8")
    monkeypatch.chdir(root)
    monkeypatch.setenv("MISSION_SESSION_ID", "test")
    monkeypatch.setattr(MISSION_STATE, "StateLock", lambda _path: contextlib.nullcontext())
    monkeypatch.setattr(MISSION_STATE, "backup_state", lambda _path: None)

    def fail_publish(_path, _data):
        raise OSError("simulated atomic publish failure")

    monkeypatch.setattr(MISSION_STATE, "atomic_write_json", fail_publish)
    args = SimpleNamespace(
        phase="reviewing",
        activity="reviewer-wait:review-response",
        detail=None,
        at="2026-08-09T00:00:00Z",
        artifact_applicability="producing",
        artifact_path="result.md",
        producer_run_id="portable-run",
    )

    with pytest.raises(OSError, match="simulated atomic publish failure"):
        MISSION_STATE.cmd_advance(args)

    assert state_path.read_bytes() == before
