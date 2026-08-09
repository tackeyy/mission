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
    summarize_artifact_coverage,
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


def test_coverage_treats_lint_observation_for_another_identity_as_invalid():
    current_identity = {
        "path": "reports/result.md",
        "digest": "b" * 64,
        "size": 12,
        "producer_run_id": "portable-run-2",
    }
    state = {
        "phase": "done",
        "passes": True,
        "loop_active": False,
        "terminal_outcome": "completed_pass",
        "artifact_applicability": "producing",
        "artifact": current_identity,
        "artifact_lint_status": "clean",
        "artifact_lint": [],
        "artifact_lint_identity": {
            **current_identity,
            "digest": "a" * 64,
            "producer_run_id": "portable-run-1",
        },
    }

    coverage = summarize_artifact_coverage([state])

    assert coverage["counts"] == {
        "eligible": 1,
        "observed": 0,
        "missing": 0,
        "invalid": 1,
        "clean": 0,
        "findings": 0,
        "skipped": 0,
    }
    assert coverage["counts_conserved"] is True


def test_coverage_never_treats_unresolved_pending_as_a_clean_observation():
    state = {
        "phase": "done",
        "passes": True,
        "loop_active": False,
        "terminal_outcome": "completed_pass",
        "artifact_applicability": "pending",
        "artifact_lint_status": "clean",
        "artifact_lint": [],
    }

    coverage = summarize_artifact_coverage([state])

    assert coverage["counts"] == {
        "eligible": 1,
        "observed": 0,
        "missing": 1,
        "invalid": 0,
        "clean": 0,
        "findings": 0,
        "skipped": 0,
    }
    assert coverage["counts_conserved"] is True


def test_coverage_counts_not_applicable_with_canonical_identity_as_invalid():
    identity = {
        "path": "reports/result.md",
        "digest": "a" * 64,
        "size": 12,
        "producer_run_id": "portable-run",
    }
    state = {
        "phase": "done",
        "passes": True,
        "loop_active": False,
        "terminal_outcome": "completed_pass",
        "artifact_applicability": "not-applicable",
        "artifact": identity,
    }

    coverage = summarize_artifact_coverage([state])

    assert coverage["counts"] == {
        "eligible": 1,
        "observed": 0,
        "missing": 0,
        "invalid": 1,
        "clean": 0,
        "findings": 0,
        "skipped": 0,
    }
    assert coverage["counts_conserved"] is True


@pytest.mark.parametrize(
    "artifact,lint_identity",
    [
        ({"path": ["malformed"]}, None),
        (
            {
                "path": "reports/result.md",
                "digest": "b" * 64,
                "size": 12,
                "producer_run_id": "portable-run-2",
            },
            {
                "path": "reports/result.md",
                "digest": "a" * 64,
                "size": 12,
                "producer_run_id": "portable-run-1",
            },
        ),
    ],
)
def test_coverage_keeps_all_not_applicable_canonical_contradictions_in_denominator(
    artifact, lint_identity
):
    state = {
        "phase": "done",
        "passes": True,
        "loop_active": False,
        "terminal_outcome": "failed",
        "artifact_applicability": "not-applicable",
        "artifact": artifact,
        "artifact_lint_status": "clean",
    }
    if lint_identity is not None:
        state["artifact_lint_identity"] = lint_identity

    coverage = summarize_artifact_coverage([state])

    assert coverage["counts"]["eligible"] == 1
    assert coverage["counts"]["invalid"] == 1
    assert coverage["counts"]["skipped"] == 0
    assert coverage["counts_conserved"] is True
    assert coverage["by_profile"]["unclassified"]["counts"] == coverage["counts"]
    assert coverage["by_terminal_outcome"]["failed"]["counts"] == coverage["counts"]


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"artifact_path": "legacy/result.md"},
    ],
)
def test_coverage_keeps_true_not_applicable_and_legacy_fallback_skipped(extra):
    state = {
        "phase": "done",
        "passes": True,
        "loop_active": False,
        "terminal_outcome": "completed_pass",
        "artifact_applicability": "not-applicable",
        **extra,
    }

    coverage = summarize_artifact_coverage([state])

    assert coverage["counts"] == {
        "eligible": 0,
        "observed": 0,
        "missing": 0,
        "invalid": 0,
        "clean": 0,
        "findings": 0,
        "skipped": 1,
    }
    assert coverage["counts_conserved"] is True


def test_coverage_rejects_producing_clean_claim_without_canonical_identity():
    state = {
        "phase": "done",
        "passes": True,
        "loop_active": False,
        "terminal_outcome": "completed_pass",
        "artifact_applicability": "producing",
        "artifact_lint_status": "clean",
        "artifact_lint": [],
    }

    coverage = summarize_artifact_coverage([state])

    assert coverage["counts"] == {
        "eligible": 1,
        "observed": 0,
        "missing": 0,
        "invalid": 1,
        "clean": 0,
        "findings": 0,
        "skipped": 0,
    }
    assert coverage["counts_conserved"] is True


@pytest.mark.parametrize("lint_status", ["clean", "findings"])
@pytest.mark.parametrize(
    ("artifact_present", "artifact"),
    [
        (False, None),
        (True, None),
        (True, "invalid"),
        (True, []),
        (True, {}),
        (True, {"path": "reports/partial.md"}),
        (
            True,
            {
                "path": "reports/malformed.md",
                "digest": "not-a-digest",
                "size": True,
                "producer_run_id": "portable-run",
            },
        ),
    ],
)
def test_coverage_rejects_unverifiable_producing_lint_claims(
    artifact_present, artifact, lint_status
):
    state = {
        "phase": "done",
        "passes": False,
        "loop_active": False,
        "terminal_outcome": "failed",
        "task_profile": {"primary": "portable-analysis"},
        "artifact_applicability": "producing",
        "artifact_lint_status": lint_status,
        "artifact_lint": [] if lint_status == "clean" else [{"kind": "finding"}],
    }
    if artifact_present:
        state["artifact"] = artifact
        if isinstance(artifact, dict):
            state["artifact_lint_identity"] = artifact

    coverage = summarize_artifact_coverage([state])

    assert coverage["counts"]["eligible"] == 1
    assert coverage["counts"]["observed"] == 0
    assert coverage["counts"]["invalid"] == 1
    assert coverage["counts"]["clean"] == 0
    assert coverage["counts"]["findings"] == 0
    assert coverage["counts_conserved"] is True
    assert coverage["by_profile"]["portable-analysis"]["counts"] == coverage["counts"]
    assert coverage["by_terminal_outcome"]["failed"]["counts"] == coverage["counts"]


@pytest.mark.parametrize(
    "lint_identity",
    [
        None,
        "invalid",
        {"path": "reports/result.md"},
        {
            "path": "reports/result.md",
            "digest": "not-a-digest",
            "size": 12,
            "producer_run_id": "portable-run",
        },
        {
            "path": "reports/result.md",
            "digest": "b" * 64,
            "size": 12,
            "producer_run_id": "stale-run",
        },
    ],
)
def test_coverage_rejects_complete_identity_without_exact_complete_lint_snapshot(
    lint_identity,
):
    identity = {
        "path": "reports/result.md",
        "digest": "a" * 64,
        "size": 12,
        "producer_run_id": "portable-run",
    }
    state = {
        "phase": "done",
        "passes": True,
        "loop_active": False,
        "terminal_outcome": "completed_pass",
        "artifact_applicability": "producing",
        "artifact": identity,
        "artifact_lint_status": "clean",
        "artifact_lint": [],
    }
    if lint_identity is not None:
        state["artifact_lint_identity"] = lint_identity

    coverage = summarize_artifact_coverage([state])

    assert coverage["counts"]["eligible"] == 1
    assert coverage["counts"]["observed"] == 0
    assert coverage["counts"]["invalid"] == 1
    assert coverage["counts_conserved"] is True


@pytest.mark.parametrize("lint_status", ["clean", "findings"])
def test_coverage_accepts_only_exact_complete_identity_bound_observation(lint_status):
    identity = {
        "path": "reports/result.md",
        "digest": "a" * 64,
        "size": 12,
        "producer_run_id": "portable-run",
    }
    state = {
        "phase": "done",
        "passes": True,
        "loop_active": False,
        "terminal_outcome": "completed_pass",
        "artifact_applicability": "producing",
        "artifact": identity,
        "artifact_lint_status": lint_status,
        "artifact_lint_identity": identity,
    }

    coverage = summarize_artifact_coverage([state])

    assert coverage["counts"]["eligible"] == 1
    assert coverage["counts"]["observed"] == 1
    assert coverage["counts"][lint_status] == 1
    assert coverage["counts_conserved"] is True


@pytest.mark.parametrize("extra", [{}, {"artifact_path": "legacy/result.md"}])
def test_coverage_keeps_producing_without_lint_claim_as_missing(extra):
    state = {
        "phase": "done",
        "passes": False,
        "loop_active": False,
        "terminal_outcome": "failed",
        "artifact_applicability": "producing",
        **extra,
    }

    coverage = summarize_artifact_coverage([state])

    assert coverage["counts"]["eligible"] == 1
    assert coverage["counts"]["missing"] == 1
    assert coverage["counts"]["invalid"] == 0
    assert coverage["counts_conserved"] is True
