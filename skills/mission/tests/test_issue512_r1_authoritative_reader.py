"""R1 Red contracts for the version-aware authoritative session reader."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIB_DIR = REPO_ROOT / "skills" / "mission" / "lib"
TEST_DIR = REPO_ROOT / "skills" / "mission" / "tests"
MISSION_STATE_PY = REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py"
HOOK = REPO_ROOT / "scripts" / "mission-stop-guard.sh"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

from mission_state_fixture_corpus import (
    canonical_json_bytes,
    current_v5_open_state,
    generate_cli_state_bytes,
)
from test_issue511_p1_repository_binding import _request, _seed_repository
from test_issue212_worktree_archive_manifest import (
    _current_generation,
    _make_neutral_git_worktree,
    _republish_generation,
)
from mission_kernel.commands import MarkHalt
from mission_kernel.model import HaltCategory
from mission_kernel.transitions import decide
from mission_persistence.fenced_commit import AdmittedSnapshot, LocalFencedRepository
from worktree_archive import (
    read_validated_archive_authoritative_snapshot,
    validate_worktree_archive_bundle,
)


def _reader():
    """Import lazily so every Red contract reports its own missing API."""
    return importlib.import_module("mission_persistence.authoritative_reader")


def _read(session_path: Path, session_id: str = "test"):
    return _reader().read_authoritative_snapshot(
        session_path, expected_session_id=session_id
    )


def _run_stop_verdict(state_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(MISSION_STATE_PY), "stop-verdict", "--state-file", str(state_file), "--json"],
        cwd=state_file.parents[2],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_hook(cwd: Path, *, session_id: str) -> subprocess.CompletedProcess[str]:
    environment = {"PATH": os.environ["PATH"], "MISSION_SESSION_ID": session_id}
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"stop_hook_active": False, "cwd": str(cwd)}),
        cwd=cwd,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def _run_hook_without_jq(cwd: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    (command_dir / "bash").symlink_to(shutil.which("bash"))
    (command_dir / "cat").symlink_to(shutil.which("cat"))
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"stop_hook_active": False, "cwd": str(cwd)}),
        cwd=cwd,
        capture_output=True,
        text=True,
        env={"PATH": str(command_dir), "MISSION_SESSION_ID": "test"},
        check=False,
    )


def _run_audit(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("MISSION_")
    }
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "mission-audit.py"), *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )


def _v4_state(tmp_path: Path, **changes: object) -> Path:
    session_path, _state_bytes = generate_cli_state_bytes(tmp_path / "legacy")
    state = json.loads(session_path.read_text(encoding="utf-8"))
    state.update(changes)
    session_path.write_text(json.dumps(state), encoding="utf-8")
    return session_path


def _v5_session(tmp_path: Path) -> tuple[Path, Path]:
    _repository, repository_root, _lease_id = _seed_repository(tmp_path / "v5")
    return repository_root / "sessions" / "test.json", repository_root


def _canonical_v5_session(tmp_path: Path) -> tuple[Path, Path, LocalFencedRepository]:
    repository_root = tmp_path / "repository" / ".mission-state"
    admitted_at = datetime(2026, 8, 14, 0, 0, tzinfo=timezone.utc)
    repository = LocalFencedRepository(repository_root, clock=lambda: admitted_at)
    request = _request("canonical-seed", "lease-500")
    admitted = repository.begin(request)
    assert isinstance(admitted, AdmittedSnapshot)
    payload = current_v5_open_state()
    payload["identity"]["session_id"] = "test"
    # The base reader fixture is intentionally reference-free. Dedicated
    # archive fixtures below bind real review and score evidence bytes.
    payload["reviews"] = []
    target_lease = admitted.pending_lease.target
    payload["lease"] = {
        "kind": "fenced",
        "owner_session_id": target_lease.owner_session_id,
        "lease_id": target_lease.lease_id,
        "fencing_epoch": target_lease.fencing_epoch,
        "lease_expires_at": target_lease.lease_expires_at,
        "lease_history": [],
    }
    state_bytes = canonical_json_bytes(payload)
    prepared = repository._stage_persistence(
        admitted, state_bytes=state_bytes, effects=()
    )
    repository.commit(prepared, prepared.precondition)
    return repository_root / "sessions" / "test.json", repository_root, repository


def _canonical_v5_terminal_with_evidence(
    tmp_path: Path,
    *,
    review_damage: Optional[str] = None,
    score_damage: Optional[str] = None,
    score_source: str = "scoring-json",
) -> tuple[Path, Path, LocalFencedRepository, dict[str, bytes]]:
    repository_root = tmp_path / "repository" / ".mission-state"
    admitted_at = datetime(2026, 8, 14, 0, 0, tzinfo=timezone.utc)
    repository = LocalFencedRepository(repository_root, clock=lambda: admitted_at)
    admitted = repository.begin(_request("canonical-evidence-seed", "lease-500"))
    assert isinstance(admitted, AdmittedSnapshot)

    review = canonical_json_bytes({
        "schema": "mission-review/1",
        "perspective": "quality",
        "iteration": 2,
        "scores": {
            "mission_achievement": 4.5,
            "accuracy": 4.5,
            "completeness": 4.5,
            "usability": 4.5,
        },
        "findings": [],
        "same_score_note": "axis-specific canonical fixture",
    })
    aggregate = canonical_json_bytes({
        "schema": "mission-review-aggregate/1",
        "iteration": 2,
        "inputs": [],
        "score_claim": {
            "iteration": 2,
            "items": {
                "mission_achievement": 4.5,
                "accuracy": 4.5,
                "completeness": 4.5,
                "usability": 4.5,
            },
            "composite": 4.5,
            "min_item": 4.5,
            "review_agreement": 5.0,
            "open_high": 0,
        },
    })
    scoring = canonical_json_bytes({"schema": "mission-scoring/1", "iteration": 2})
    manual = canonical_json_bytes({"schema": "mission-manual-score/1", "iteration": 2})
    evidence = {
        ".mission-state/archive/review-input.json": review,
        ".mission-state/archive/review-aggregate.json": aggregate,
        ".mission-state/archive/scoring.json": scoring,
        ".mission-state/archive/manual-score.json": manual,
    }
    for reference, content in evidence.items():
        if review_damage == "missing" and reference.endswith("review-input.json"):
            continue
        if score_damage == "aggregate-missing" and reference.endswith("review-aggregate.json"):
            continue
        if score_damage == "scoring-missing" and reference.endswith("scoring.json"):
            continue
        target = repository_root.parent / Path(reference)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    scope = {"kind": "not-applicable", "reason_code": "non-git"}
    aggregate_ref = {
        "kind": "review-aggregate",
        "relative_path": ".mission-state/archive/review-aggregate.json",
        "digest": (
            "sha256:" + "0" * 64
            if score_damage == "aggregate-digest"
            else "sha256:" + hashlib.sha256(aggregate).hexdigest()
        ),
        "size": len(aggregate) + (1 if score_damage == "aggregate-size" else 0),
        "iteration": 2,
        "generation": "aggregate-2",
        "revision_scope": scope,
    }
    payload = current_v5_open_state()
    payload["identity"]["session_id"] = "test"
    payload["control"].update({
        "phase": "halted",
        "terminal_outcome": "failed",
        "loop_active": False,
        "halt_reason": "canonical terminal evidence",
        "halt_category": "other",
    })
    target_lease = admitted.pending_lease.target
    payload["lease"] = {
        "kind": "fenced",
        "owner_session_id": target_lease.owner_session_id,
        "lease_id": target_lease.lease_id,
        "fencing_epoch": target_lease.fencing_epoch,
        "lease_expires_at": target_lease.lease_expires_at,
        "lease_history": [],
    }
    payload["reviews"] = [{
        "kind": "review-input",
        "relative_path": ".mission-state/archive/review-input.json",
        "digest": (
            "sha256:" + "0" * 64
            if review_damage == "digest"
            else "sha256:" + hashlib.sha256(review).hexdigest()
        ),
        "size": len(review) + (1 if review_damage == "size" else 0),
        "iteration": 2,
        "perspective": "quality",
    }]
    score = {
        "source": "scoring-json",
        "items": {
            "mission_achievement": 4.5,
            "accuracy": 4.5,
            "completeness": 4.5,
            "usability": 4.5,
        },
        "composite": 4.5,
        "min_item": 4.5,
        "agreement": 5.0,
        "open_high": 0,
        "review_evidence_ref": aggregate_ref,
        "scoring_evidence_ref": {
            "kind": "scoring-artifact",
            "relative_path": ".mission-state/archive/scoring.json",
            "digest": (
                "sha256:" + "0" * 64
                if score_damage == "scoring-digest"
                else "sha256:" + hashlib.sha256(scoring).hexdigest()
            ),
            "size": len(scoring) + (1 if score_damage == "scoring-size" else 0),
        },
        "revision_scope": scope,
    }
    if score_source == "manual-import":
        score["source"] = "manual-import"
        score.pop("review_evidence_ref")
        score["manual_evidence_ref"] = {
            "kind": "manual-score",
            "relative_path": ".mission-state/archive/manual-score.json",
            "digest": "sha256:" + hashlib.sha256(manual).hexdigest(),
            "size": len(manual),
            "generation": "manual-2",
            "revision_scope": scope,
        }
    payload["scores"] = [score]
    state_bytes = canonical_json_bytes(payload)
    prepared = repository._stage_persistence(
        admitted, state_bytes=state_bytes, effects=()
    )
    repository.commit(prepared, prepared.precondition)
    return repository_root / "sessions" / "test.json", repository_root, repository, evidence


def test_canonical_v5_generation_projects_authoritative_fields_from_k1_state(tmp_path):
    session_path, _repository_root, _repository = _canonical_v5_session(tmp_path)

    snapshot = _read(session_path)

    assert snapshot.schema_origin.value == "v5"
    assert snapshot.session_id == "test"
    assert snapshot.loop_active is True
    assert snapshot.phase == "planning"
    assert snapshot.iteration == 2
    assert snapshot.lease is not None
    assert snapshot.lease.owner_session_id == "test"


def test_canonical_v5_lane_report_reads_verified_generation(tmp_path, run_cli):
    _session_path, repository_root, _repository = _canonical_v5_session(tmp_path)

    result = run_cli("lane-report", "--json", cwd=repository_root.parent)

    assert result.returncode == 0, result.stderr
    assert [item["session_id"] for item in json.loads(result.stdout)["sessions"]] == [
        "test"
    ]


def _halt_canonical_v5_repository(repository: LocalFencedRepository) -> None:
    command = MarkHalt(HaltCategory.OTHER, "canonical halt")
    request = _request(
        "canonical-halt",
        "lease-500",
        typed_command=command,
        event_types=("mission-halted",),
    )
    admitted = repository.begin(request)
    assert isinstance(admitted, AdmittedSnapshot) and admitted.base is not None
    admitted_state = replace(
        admitted.base.state,
        lease=admitted.pending_lease.target,
        snapshot_provenance=None,
    )
    decision = decide(admitted_state, command)
    assert decision.accepted and decision.transition is not None

    prepared = repository.stage(admitted, decision.transition, request.blobs)
    repository.commit(prepared, prepared.precondition)


def test_canonical_v5_public_stage_commit_remains_readable(tmp_path):
    session_path, _repository_root, repository = _canonical_v5_session(tmp_path)
    _halt_canonical_v5_repository(repository)
    snapshot = _read(session_path)

    assert snapshot.loop_active is False
    assert snapshot.halt_reason == "canonical halt"
    assert snapshot.generation == 2


def test_canonical_v5_generation_is_supported_by_every_read_only_route(
    tmp_path, run_cli
):
    session_path, repository_root, _repository = _canonical_v5_session(
        tmp_path / "live"
    )
    root = repository_root.parent
    snapshot_path = tmp_path / "canonical.snapshot.json"

    stop = _run_hook(root, session_id="test")
    audit = _run_audit(root, "--root", str(root), "--json")
    snapshot = _run_audit(
        root,
        "--root",
        str(root),
        "--snapshot-out",
        str(snapshot_path),
        "--json",
    )
    listed = run_cli(
        "list", cwd=root, env_extra={"MISSION_SEARCH_ROOTS": str(root)}
    )
    stats = run_cli("stats", "--root", str(root), "--json", cwd=root)
    freshness = run_cli(
        "freshness", "--state-file", str(session_path), cwd=root
    )
    next_action = run_cli("next", cwd=root)
    lane_report = run_cli("lane-report", "--json", cwd=root)

    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    worktree, destination = _make_neutral_git_worktree(archive_root)
    shutil.copytree(repository_root, worktree / ".mission-state")
    archived = run_cli(
        "archive-worktree",
        "--destination-root",
        str(destination),
        "--dry-run",
        "--json",
        cwd=worktree,
        env_extra={"MISSION_SESSION_ID": "test"},
    )

    assert json.loads(stop.stdout)["decision"] == "block"
    assert audit.returncode == snapshot.returncode == 0
    assert json.loads(audit.stdout)["total_sessions"] == 1
    assert snapshot_path.is_file()
    assert json.loads(listed.stdout)[0]["session_id"] == "test"
    assert json.loads(stats.stdout)["total_sessions"] == 1
    assert freshness.returncode == next_action.returncode == 0
    assert json.loads(next_action.stdout)["session_id"] == "test"
    assert json.loads(lane_report.stdout)["sessions"][0]["session_id"] == "test"
    assert archived.returncode == 2
    assert "active session cannot be archived" in archived.stderr


def test_terminal_canonical_v5_archive_preserves_generation_bytes(
    tmp_path, run_cli
):
    _session_path, repository_root, repository = _canonical_v5_session(
        tmp_path / "seed"
    )
    _halt_canonical_v5_repository(repository)
    resolved_state_bytes = repository.read("test").state_bytes
    archive_root = tmp_path / "archive-terminal"
    archive_root.mkdir()
    worktree, destination = _make_neutral_git_worktree(archive_root)
    shutil.copytree(repository_root, worktree / ".mission-state")
    (worktree / ".mission-state" / "sessions" / "test-assumptions.md").write_text(
        "# assumptions\n", encoding="utf-8"
    )

    archived = run_cli(
        "archive-worktree",
        "--destination-root",
        str(destination),
        "--json",
        cwd=worktree,
        env_extra={"MISSION_SESSION_ID": "test"},
    )

    assert archived.returncode == 0, archived.stderr
    validation = validate_worktree_archive_bundle(
        Path(json.loads(archived.stdout)["bundle_path"])
    )
    assert validation.status == "valid", validation.reason
    assert validation.state_paths[0].read_bytes() == resolved_state_bytes


def _archive_canonical_evidence_repository(
    tmp_path: Path,
    run_cli,
    *,
    review_damage: Optional[str] = None,
    score_damage: Optional[str] = None,
    score_source: str = "scoring-json",
):
    _session_path, repository_root, _repository, _evidence = (
        _canonical_v5_terminal_with_evidence(
            tmp_path / "seed",
            review_damage=review_damage,
            score_damage=score_damage,
            score_source=score_source,
        )
    )
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    worktree, destination = _make_neutral_git_worktree(archive_root)
    shutil.copytree(repository_root, worktree / ".mission-state")
    result = run_cli(
        "archive-worktree",
        "--destination-root",
        str(destination),
        "--json",
        cwd=worktree,
        env_extra={"MISSION_SESSION_ID": "test"},
    )
    return result


def test_canonical_terminal_v5_archive_preserves_typed_review_and_score_lineage(
    tmp_path, run_cli
):
    result = _archive_canonical_evidence_repository(tmp_path, run_cli)

    assert result.returncode == 0, result.stderr
    validation = validate_worktree_archive_bundle(
        Path(json.loads(result.stdout)["bundle_path"])
    )
    assert validation.status == "valid", validation.reason
    assert {
        "review-input",
        "review-aggregate",
        "scoring-artifact",
    }.issubset({item["evidence_kind"] for item in validation.evidence})


@pytest.mark.parametrize("damage", ["missing", "digest", "size"])
def test_canonical_terminal_v5_archive_rejects_invalid_typed_review_reference(
    tmp_path, run_cli, damage
):
    result = _archive_canonical_evidence_repository(
        tmp_path, run_cli, review_damage=damage
    )

    assert result.returncode != 0
    assert "review input" in result.stderr.lower() or "required evidence" in result.stderr.lower()


@pytest.mark.parametrize(
    "damage",
    [
        "aggregate-missing",
        "aggregate-digest",
        "aggregate-size",
        "scoring-missing",
        "scoring-digest",
        "scoring-size",
    ],
)
def test_canonical_terminal_v5_archive_rejects_invalid_typed_score_reference(
    tmp_path, run_cli, damage
):
    result = _archive_canonical_evidence_repository(
        tmp_path, run_cli, score_damage=damage
    )

    assert result.returncode != 0
    assert "evidence" in result.stderr.lower()


def _replace_archived_state_and_republish(
    bundle: Path, generation_root: Path, replacement: bytes
) -> Path:
    manifest_path = generation_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    state_item = next(
        item for item in manifest["evidence"] if item["evidence_kind"] == "state"
    )
    state_path = generation_root / state_item["archive_path"]
    state_path.write_bytes(replacement)
    state_item["sha256"] = hashlib.sha256(replacement).hexdigest()
    state_item["size"] = len(replacement)
    return _republish_generation(bundle, generation_root, manifest)


@pytest.mark.parametrize("damage", ["duplicate-key", "non-finite"])
def test_archive_validation_strictly_decodes_self_consistent_state_bytes(
    tmp_path, run_cli, damage
):
    result = _archive_canonical_evidence_repository(tmp_path, run_cli)
    assert result.returncode == 0, result.stderr
    bundle = Path(json.loads(result.stdout)["bundle_path"])
    _generation, generation_root = _current_generation(bundle)
    manifest = json.loads((generation_root / "manifest.json").read_text(encoding="utf-8"))
    state_item = next(
        item for item in manifest["evidence"] if item["evidence_kind"] == "state"
    )
    state_bytes = (generation_root / state_item["archive_path"]).read_bytes()
    if damage == "duplicate-key":
        replacement = state_bytes.replace(
            b'"schema_version":5',
            b'"schema_version":5,"schema_version":5',
            1,
        )
    else:
        replacement = state_bytes.replace(b'"threshold":4.0', b'"threshold":NaN', 1)
    assert replacement != state_bytes
    _replace_archived_state_and_republish(bundle, generation_root, replacement)

    validation = validate_worktree_archive_bundle(bundle)

    assert validation.status == "invalid"
    assert validation.reason == "manifest-state-invalid-json"


@pytest.mark.parametrize(
    "evidence_kind, score_source",
    [
        ("review-aggregate", "scoring-json"),
        ("scoring-artifact", "scoring-json"),
        ("manual-score-source", "manual-import"),
    ],
)
def test_archive_validation_rebinds_typed_score_reference_after_self_consistent_tamper(
    tmp_path, run_cli, evidence_kind, score_source
):
    result = _archive_canonical_evidence_repository(
        tmp_path, run_cli, score_source=score_source
    )
    assert result.returncode == 0, result.stderr
    bundle = Path(json.loads(result.stdout)["bundle_path"])
    _generation, generation_root = _current_generation(bundle)
    manifest_path = generation_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    item = next(
        entry for entry in manifest["evidence"]
        if entry["evidence_kind"] == evidence_kind
    )
    evidence_path = generation_root / item["archive_path"]
    replacement = evidence_path.read_bytes() + b"tampered\n"
    evidence_path.write_bytes(replacement)
    item["sha256"] = hashlib.sha256(replacement).hexdigest()
    item["size"] = len(replacement)
    _republish_generation(bundle, generation_root, manifest)

    validation = validate_worktree_archive_bundle(bundle)

    assert validation.status == "invalid"
    assert validation.reason == "manifest-score-reference-integrity-mismatch"


def _mixed_v4_v5_root(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "mixed-root"
    v4_path = _v4_state(root / "legacy-v4", session_id="test")
    v5_path, _v5_repository_root = _v5_session(root / "v5")
    shutil.rmtree(v5_path.parents[3] / "source")
    return root, v4_path, v5_path


def _archive_dry_run_for_active_session(tmp_path: Path, kind: str, run_cli):
    archive_fixture_root = tmp_path / (kind + "-archive")
    archive_fixture_root.mkdir()
    worktree, destination = _make_neutral_git_worktree(archive_fixture_root)
    if kind == "legacy-v4":
        generate_cli_state_bytes(worktree)
    else:
        _session_path, repository_root = _v5_session(tmp_path / (kind + "-seed"))
        shutil.copytree(repository_root, worktree / ".mission-state")
    return run_cli(
        "archive-worktree",
        "--destination-root",
        str(destination),
        "--dry-run",
        "--json",
        cwd=worktree,
    )


def _head_document(session_path: Path) -> dict:
    return json.loads(session_path.read_text(encoding="utf-8"))


def _write_head(session_path: Path, head: dict) -> None:
    session_path.write_text(json.dumps(head), encoding="utf-8")


def test_t1_active_v5_session_blocks_stop_hook_the_same_as_v4(tmp_path):
    """T1: a verified active v5 head must never be mistaken for an inactive file."""
    v4_path = _v4_state(tmp_path / "v4", session_id="test", loop_active=True)
    v5_path, v5_root = _v5_session(tmp_path)

    v4 = _run_hook(v4_path.parents[2], session_id="test")
    v5 = _run_hook(v5_root.parent, session_id="test")

    assert json.loads(v4.stdout)["decision"] == json.loads(v5.stdout)["decision"] == "block"


@pytest.mark.parametrize(
    "name, changes, expected_reason, expected_kind",
    [
        (
            "pass",
            {"passes": True, "loop_active": False},
            "passes-true",
            "completed-pass",
        ),
        (
            "halt",
            {"halt_reason": "operator stop", "loop_active": False},
            "halt-reason",
            "halted",
        ),
        (
            "evidence-complete",
            {
                "halt_category": "evidence-submitted",
                "halt_reason": "evidence complete",
                "loop_active": False,
            },
            "evidence-submitted",
            "completed-evidence",
        ),
    ],
)
def test_t2_terminal_states_keep_stop_verdict_and_hook_nonblocking(
    name, changes, expected_reason, expected_kind, tmp_path
):
    """T2: pass, halt, and evidence completion remain terminal without new enums."""
    state_path = _v4_state(tmp_path / name, session_id="test", **changes)

    verdict = _run_stop_verdict(state_path)
    hook = _run_hook(state_path.parents[2], session_id="test")

    assert verdict.returncode == 0, verdict.stderr
    payload = json.loads(verdict.stdout)
    assert payload["schema"] == "mission-stop-verdict/1"
    assert payload["decision"] == "skip"
    assert payload["reason"] == expected_reason
    assert payload["outcome_kind"] == expected_kind
    assert "block" not in hook.stdout


def test_t3_mixed_root_consumers_are_equivalent_for_v4_and_v5(tmp_path, run_cli):
    """T3: every query surface resolves both formats through the same reader."""
    root, v4_path, v5_path = _mixed_v4_v5_root(tmp_path)

    v4_next = run_cli("next", cwd=v4_path.parents[2])
    v5_next = run_cli("next", cwd=v5_path.parents[2])
    v4_freshness = run_cli("freshness", "--state-file", str(v4_path), cwd=root)
    v5_freshness = run_cli("freshness", "--state-file", str(v5_path), cwd=root)
    listed = run_cli(
        "list", cwd=root, env_extra={"MISSION_SEARCH_ROOTS": str(root)}
    )
    stats = run_cli("stats", "--root", str(root), "--json", cwd=root)
    audit = _run_audit(root, "--root", str(root), "--json")
    snapshot_path = tmp_path / "mixed.snapshot.json"
    snapshot = _run_audit(
        root,
        "--root",
        str(root),
        "--snapshot-out",
        str(snapshot_path),
        "--snapshot-ttl-sec",
        "3600",
        "--json",
    )
    legacy_archive = _archive_dry_run_for_active_session(
        tmp_path, "legacy-v4", run_cli
    )
    v5_archive = _archive_dry_run_for_active_session(tmp_path, "v5", run_cli)

    assert v4_next.returncode == v5_next.returncode == 0
    assert json.loads(v4_next.stdout)["session_id"] == json.loads(v5_next.stdout)["session_id"] == "test"
    assert v4_freshness.returncode == v5_freshness.returncode == 0
    assert json.loads(v4_freshness.stdout)["verdict"] == json.loads(v5_freshness.stdout)["verdict"]
    listed_payload = json.loads(listed.stdout)
    matching = [item for item in listed_payload if item.get("session_id") == "test"]
    assert len(matching) == 2 and all(item.get("loop_active") is True for item in matching)
    assert stats.returncode == audit.returncode == snapshot.returncode == 0
    assert json.loads(stats.stdout)["total_sessions"] == json.loads(audit.stdout)["total_sessions"] == 2
    assert json.loads(snapshot.stdout)["total_sessions"] == 2
    assert len(json.loads(snapshot_path.read_text(encoding="utf-8"))["records"]) == 2
    assert legacy_archive.returncode == v5_archive.returncode == 2
    assert "active session cannot be archived" in legacy_archive.stderr
    assert legacy_archive.stderr == v5_archive.stderr


def test_t4_malformed_v5_head_fails_closed_without_filename_fallback(tmp_path):
    """T4: the filename is never an alternate source of authoritative state."""
    session_path, _repository_root = _v5_session(tmp_path)
    session_path.write_text('{"schema":"mission-head/1"}', encoding="utf-8")
    reader = _reader()

    with pytest.raises(Exception):
        reader.read_authoritative_snapshot(session_path, expected_session_id="test")


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": "mission-head/2"},
        {
            "schema": None,
            "mission": "schema null",
            "phase": "planning",
            "loop_active": True,
        },
        {"commit": {}, "generation": 1, "session_id": "test", "state_generation": {}},
        {},
        {
            "schema": "mission-state/5",
            "mission": "future state",
            "session_id": "test",
            "phase": "planning",
            "loop_active": True,
        },
    ],
    ids=[
        "future-head",
        "null-schema",
        "schema-less-head",
        "empty",
        "schema-tagged-state",
    ],
)
def test_t4_t9_unknown_or_head_shaped_documents_fail_closed_for_reader_and_stop(
    tmp_path, payload
):
    """T4/T9/T10: unknown formats never downgrade into legacy defaults."""
    root = tmp_path / "project"
    session_path = root / ".mission-state" / "sessions" / "test.json"
    session_path.parent.mkdir(parents=True)
    session_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Exception):
        _read(session_path)

    result = _run_hook(root, session_id="test")
    assert result.returncode == 0
    assert json.loads(result.stdout)["decision"] == "block"


@pytest.mark.parametrize(
    "payload",
    [
        {"mission": "legacy but control missing", "session_id": "test"},
        {"phase": "planning", "loop_active": True, "session_id": "test"},
    ],
    ids=["identity-only", "control-only"],
)
def test_schema_less_legacy_requires_identity_and_control(tmp_path, payload):
    state_path = tmp_path / "sessions" / "test.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(Exception):
        _read(state_path)


def test_schema_less_legacy_with_identity_and_control_remains_compatible(tmp_path):
    state_path = tmp_path / "sessions" / "test.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps({
            "mission": "legacy compatible",
            "phase": "planning",
            "loop_active": True,
        }),
        encoding="utf-8",
    )

    snapshot = _read(state_path)

    assert snapshot.schema_origin.value == "missing"
    assert snapshot.session_id is None
    assert snapshot.loop_active is True


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"mission": "sealed identity-only", "session_id": "sealed"},
        {"phase": "planning", "loop_active": True},
    ],
    ids=["empty", "identity-only", "control-only"],
)
def test_already_sealed_sparse_legacy_document_keeps_compatibility(document):
    """The sealed-document seam is not a live repository format selector."""
    snapshot = _reader().authoritative_snapshot_from_document(document)

    assert snapshot.schema_origin.value == "missing"


@pytest.mark.parametrize(
    "document",
    [
        {"schema": "mission-head/2"},
        {"commit": {}, "state_generation": {}},
        {"loop_active": "true"},
    ],
    ids=["unknown-schema", "head-shaped", "authoritative-type"],
)
def test_already_sealed_document_still_rejects_unknown_format_and_bad_types(document):
    with pytest.raises(Exception):
        _reader().authoritative_snapshot_from_document(document)


@pytest.mark.parametrize("missing", ["commit", "generation"])
def test_t5_missing_v5_lineage_record_fails_closed(missing, tmp_path):
    """T5: a head is insufficient unless every immutable lineage record exists."""
    session_path, repository_root = _v5_session(tmp_path / missing)
    head = _head_document(session_path)
    reference = head["commit"] if missing == "commit" else head["state_generation"]
    (repository_root / reference["path"]).unlink()
    reader = _reader()

    with pytest.raises(Exception):
        reader.read_authoritative_snapshot(session_path, expected_session_id="test")


@pytest.mark.parametrize("drift", ["digest", "size", "generation"])
def test_t6_v5_lineage_reference_drift_fails_closed(drift, tmp_path):
    """T6: head references must bind bytes, sizes, and target generation exactly."""
    session_path, _repository_root = _v5_session(tmp_path / drift)
    head = _head_document(session_path)
    if drift == "digest":
        head["commit"]["digest"] = "sha256:" + "0" * 64
    elif drift == "size":
        head["state_generation"]["size"] += 1
    else:
        head["generation"] += 1
    _write_head(session_path, head)
    reader = _reader()

    with pytest.raises(Exception):
        reader.read_authoritative_snapshot(session_path, expected_session_id="test")


@pytest.mark.parametrize(
    "payload",
    [
        b'{"loop_active":true,"loop_active":false}',
        b'{"loop_active":NaN}',
    ],
    ids=["duplicate-key", "non-finite-number"],
)
def test_t7_invalid_json_encoding_fails_closed(payload, tmp_path):
    """T7: codec rejection applies before either format is interpreted."""
    state_path = tmp_path / "sessions" / "test.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_bytes(payload)
    reader = _reader()

    with pytest.raises(Exception):
        reader.read_authoritative_snapshot(state_path, expected_session_id="test")


@pytest.mark.skipif(os.name == "nt", reason="POSIX file kinds are required")
@pytest.mark.parametrize("file_kind", ["symlink", "fifo", "hard-link"])
def test_t8_unsafe_session_file_kinds_fail_closed(file_kind, tmp_path):
    """T8: only one-link regular files are eligible authoritative inputs."""
    state_path = tmp_path / "sessions" / "test.json"
    state_path.parent.mkdir(parents=True)
    payload = b'{"loop_active":true}'
    if file_kind == "symlink":
        target = tmp_path / "target.json"
        target.write_bytes(payload)
        state_path.symlink_to(target)
    elif file_kind == "fifo":
        os.mkfifo(state_path)
    else:
        target = tmp_path / "target.json"
        target.write_bytes(payload)
        os.link(target, state_path)
    reader = _reader()

    with pytest.raises(Exception):
        reader.read_authoritative_snapshot(state_path, expected_session_id="test")


def test_t9_future_schema_fails_closed(tmp_path):
    """T9: #483's forward-version rejection survives the shared reader."""
    state_path = tmp_path / "sessions" / "test.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text('{"schema_version":999,"loop_active":true}', encoding="utf-8")
    reader = _reader()

    with pytest.raises(Exception):
        reader.read_authoritative_snapshot(state_path, expected_session_id="test")


def test_t10_unreadable_v5_session_blocks_stop_instead_of_becoming_inactive(tmp_path):
    """T10: a broken v5 lineage is an uncertain active state, never a skip."""
    session_path, repository_root = _v5_session(tmp_path)
    session_path.write_text('{"schema":"mission-head/1"}', encoding="utf-8")

    result = _run_hook(repository_root.parent, session_id="test")

    assert json.loads(result.stdout)["decision"] == "block"


_AUTHORITATIVE_FIELDS = {
    "awaiting_user",
    "loop_active",
    "logical_group_id",
    "mission",
    "phase",
    "pid",
    "passes",
    "halt_reason",
    "issue_ref",
    "iteration",
    "owner_session_id",
    "lease_id",
    "fencing_epoch",
    "lease_expires_at",
    "project_root",
    "score_history",
    "session_id",
    "threshold",
    "updated_at",
}

_CONSUMER_AUTHORITATIVE_FIELDS = {
    "fencing_epoch",
    "halt_reason",
    "heartbeat_at",
    "last_activity_at",
    "last_progress_at",
    "lease_expires_at",
    "lease_id",
    "loop_active",
    "owner_session_id",
    "passes",
    "updated_at",
}

_R1_QUERY_ROUTE_FUNCTIONS = {
    "stop-verdict": {"cmd_stop_verdict", "_stop_verdict_pending"},
    "freshness": {"cmd_freshness"},
    "next": {"cmd_next", "_derive_next_action"},
    "list": {"cmd_list"},
    "stats": {"cmd_stats", "_collect_states", "_dedupe_states", "_aggregate"},
    "lane-report": {
        "cmd_lane_report",
        "_lane_report_session_entry",
        "_lane_report_wall_clock_sec",
        "_lane_report_session_role",
    },
    "archive": {"cmd_archive_worktree", "_collect_worktree_archive_specs"},
    "audit": {"load_records", "aggregate", "dedupe_rank"},
    "snapshot": {"load_immutable_state_snapshot", "_record_from_payload"},
}
_EXPECTED_R1_QUERY_ROUTES = {
    "stop-verdict",
    "freshness",
    "next",
    "list",
    "stats",
    "lane-report",
    "archive",
    "audit",
    "snapshot",
}


def _missing_r1_query_routes(routes: dict[str, set[str]]) -> set[str]:
    return _EXPECTED_R1_QUERY_ROUTES - set(routes)


def test_t12_route_inventory_mutation_detects_every_missing_query_route():
    assert _missing_r1_query_routes(_R1_QUERY_ROUTE_FUNCTIONS) == set()
    for route in sorted(_EXPECTED_R1_QUERY_ROUTES):
        mutated = dict(_R1_QUERY_ROUTE_FUNCTIONS)
        mutated.pop(route)
        assert _missing_r1_query_routes(mutated) == {route}


def _shell_commands(source: str) -> list[str]:
    commands: list[str] = []
    current: list[str] = []
    quote = None
    escaped = False
    for character in source:
        if escaped:
            escaped = False
            if character != "\n":
                current.append(character)
            continue
        if character == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            current.append(character)
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
            current.append(character)
            continue
        if character in {"\n", ";"}:
            command = "".join(current).strip()
            if command:
                commands.append(command)
            current = []
            continue
        current.append(character)
    if current:
        commands.append("".join(current).strip())
    return commands


def _jq_authoritative_file_reads(source: str) -> list[str]:
    offenders: list[str] = []
    literal_assignments: dict[str, str] = {}
    for command in _shell_commands(source):
        try:
            tokens = shlex.split(command, comments=True, posix=True)
        except ValueError:
            continue
        if len(tokens) == 1 and re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]
        ):
            name, value = tokens[0].split("=", 1)
            literal_assignments[name] = value
            continue
        for jq_index, token in enumerate(tokens):
            if Path(token).name != "jq":
                continue
            args = tokens[jq_index + 1:]
            position = 0
            while position < len(args):
                option = args[position]
                if option in {"--arg", "--argjson", "--slurpfile", "--rawfile"}:
                    position += 3
                    continue
                if option.startswith("-"):
                    position += 1
                    continue
                break
            if position >= len(args):
                continue
            jq_filter = args[position]
            variable_match = re.fullmatch(
                r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))",
                jq_filter,
            )
            if variable_match:
                variable_name = variable_match.group(1) or variable_match.group(2)
                jq_filter = literal_assignments.get(variable_name, jq_filter)
            inputs = [
                item for item in args[position + 1:]
                if item not in {"|", "||", "&&"} and not item.startswith(">")
            ]
            has_input = bool(inputs) or any(
                Path(token).name == "cat" for token in tokens[:jq_index]
            )
            if has_input and any(
                re.search(r"\." + re.escape(field) + r"\b", jq_filter)
                for field in _AUTHORITATIVE_FIELDS
            ):
                offenders.append(command)
    return offenders


def test_t11_static_guard_rejects_shell_jq_authoritative_reads():
    """T11: stop hook may decode only Python's verdict, never session fields."""
    offenders = []
    for script in (REPO_ROOT / "scripts").glob("*.sh"):
        offenders.extend(
            f"{script.name}:{command}"
            for command in _jq_authoritative_file_reads(
                script.read_text(encoding="utf-8")
            )
        )

    assert _jq_authoritative_file_reads(
        "jq -r '\n.loop_active\n' \"$renamed_session_path\""
    )
    assert _jq_authoritative_file_reads(
        "cat \"$renamed_session_path\" | jq '.loop_active'"
    )
    assert _jq_authoritative_file_reads(
        "FILTER='.loop_active'; jq \"$FILTER\" \"$renamed_session_path\""
    )

    assert offenders == []


class _AuthoritativeGetVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.offenders: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in _CONSUMER_AUTHORITATIVE_FIELDS
        ):
            self.offenders.append(node.lineno)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if (
            isinstance(node.slice, ast.Constant)
            and node.slice.value in _CONSUMER_AUTHORITATIVE_FIELDS
        ):
            self.offenders.append(node.lineno)
        self.generic_visit(node)


class _RawAuthoritativeHelperVisitor(ast.NodeVisitor):
    FORBIDDEN = {
        "classify",
        "_classify",
        "classify_pass_rate_health",
        "derive_terminal_outcome",
        "state_dedupe_rank",
        "summarize_pass_rate_population",
    }

    def __init__(self) -> None:
        self.offenders: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in self.FORBIDDEN:
            self.offenders.append(node.lineno)
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "summarize_artifact_coverage"
            and not any(
                keyword.arg == "terminal_outcomes" for keyword in node.keywords
            )
        ):
            self.offenders.append(node.lineno)
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "summarize_activity_states"
            and not {"phases", "session_roles"}.issubset(
                {keyword.arg for keyword in node.keywords}
            )
        ):
            self.offenders.append(node.lineno)
        self.generic_visit(node)


class _VerifiedStateRecordVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.offenders: list[int] = []

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "StateRecord"
            and not any(
                keyword.arg == "authoritative_snapshot" for keyword in node.keywords
            )
        ):
            self.offenders.append(node.lineno)
        self.generic_visit(node)


def test_t12_static_guard_rejects_consumer_local_authoritative_gets():
    """T12: only session-state consumers, not arbitrary documents, are guarded."""
    offenders = []
    mission_state_tree = ast.parse(MISSION_STATE_PY.read_text(encoding="utf-8"))
    mission_route_functions = set().union(*(
        functions
        for route, functions in _R1_QUERY_ROUTE_FUNCTIONS.items()
        if route not in {"audit", "snapshot"}
    ))
    guarded_functions = mission_route_functions | {
        "_build_agent_summary",
        "_matches_period",
        "_score_provenance_counts",
    }
    found_mission_functions = set()
    for node in ast.walk(mission_state_tree):
        if isinstance(node, ast.FunctionDef) and node.name in guarded_functions:
            found_mission_functions.add(node.name)
            visitor = _AuthoritativeGetVisitor()
            visitor.visit(node)
            offenders.extend(
                f"mission-state.py:{node.name}:{line}" for line in visitor.offenders
            )
            helper_visitor = _RawAuthoritativeHelperVisitor()
            helper_visitor.visit(node)
            offenders.extend(
                f"mission-state.py:{node.name}:raw-helper:{line}"
                for line in helper_visitor.offenders
            )
    offenders.extend(
        "mission-state.py:missing-route-function:%s" % name
        for name in sorted(mission_route_functions - found_mission_functions)
    )

    snapshot_tree = ast.parse(
        (REPO_ROOT / "skills" / "mission" / "lib" / "state_snapshot.py").read_text(
            encoding="utf-8"
        )
    )
    if not any(
        isinstance(node, ast.ImportFrom)
        and node.module == "mission_persistence.authoritative_reader"
        for node in ast.walk(snapshot_tree)
    ):
        offenders.append("state_snapshot.py:missing-authoritative-reader-import")

    audit_tree = ast.parse(
        (REPO_ROOT / "scripts" / "mission-audit.py").read_text(encoding="utf-8")
    )
    audit_functions = (
        _R1_QUERY_ROUTE_FUNCTIONS["audit"]
        | _R1_QUERY_ROUTE_FUNCTIONS["snapshot"]
    )
    found_audit_functions = set()
    for node in ast.walk(audit_tree):
        if isinstance(node, ast.FunctionDef) and node.name in audit_functions:
            found_audit_functions.add(node.name)
            visitor = _AuthoritativeGetVisitor()
            visitor.visit(node)
            offenders.extend(
                f"mission-audit.py:{node.name}:{line}"
                for line in visitor.offenders
            )
            helper_visitor = _RawAuthoritativeHelperVisitor()
            helper_visitor.visit(node)
            offenders.extend(
                f"mission-audit.py:{node.name}:raw-helper:{line}"
                for line in helper_visitor.offenders
            )
            if node.name == "load_records":
                record_visitor = _VerifiedStateRecordVisitor()
                record_visitor.visit(node)
                offenders.extend(
                    f"mission-audit.py:load_records:unverified-record:{line}"
                    for line in record_visitor.offenders
                )
    offenders.extend(
        "mission-audit.py:missing-route-function:%s" % name
        for name in sorted(audit_functions - found_audit_functions)
    )
    state_records = [
        node
        for node in audit_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "StateRecord"
    ]
    if len(state_records) != 1 or not any(
        isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "authoritative_snapshot"
        for node in state_records[0].body
    ):
        offenders.append("mission-audit.py:StateRecord:missing-authoritative-snapshot")

    assert offenders == []


def test_t12_static_guard_detects_literal_authoritative_subscript():
    visitor = _AuthoritativeGetVisitor()
    visitor.visit(
        ast.parse("def cmd_lane_report(document):\n    return document['loop_active']\n")
    )

    assert visitor.offenders == [2]


def test_t12_production_record_guard_detects_missing_verified_snapshot():
    visitor = _VerifiedStateRecordVisitor()
    visitor.visit(ast.parse("def load_records(path, state):\n    return StateRecord(path, state)\n"))

    assert visitor.offenders == [2]


def test_t13_legacy_v4_reader_rejects_v5_head_while_new_reader_resolves_it(tmp_path):
    """T13: old-reader compatibility remains an explicit safe boundary, not a downgrade."""
    from mission_persistence.repository_binding import RepositorySelectionError, require_legacy_session

    session_path, _repository_root = _v5_session(tmp_path)

    with pytest.raises(RepositorySelectionError):
        require_legacy_session("test", session_path).select()
    assert _read(session_path).session_id == "test"


def test_t14_new_reader_accepts_the_existing_v4_writer_bytes(
    tmp_path, state_dir, read_state
):
    """T14: writer compatibility is retained for the untouched v4 producer."""
    state_path, state_bytes = generate_cli_state_bytes(tmp_path / "legacy-writer")

    snapshot = _read(state_path)

    assert snapshot.session_id == json.loads(state_bytes)["session_id"]
    assert _read(state_dir / "sessions" / "test.json").session_id == read_state(state_dir)["session_id"]


@pytest.mark.parametrize("repository_kind", ["legacy-v4", "v5"])
def test_terminal_archive_stores_resolved_state_and_validates(
    tmp_path, run_cli, repository_kind
):
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    worktree, destination = _make_neutral_git_worktree(archive_root)
    if repository_kind == "legacy-v4":
        session_path, _source = generate_cli_state_bytes(worktree)
        state = json.loads(session_path.read_text(encoding="utf-8"))
        state.update({
            "loop_active": False,
            "halt_category": "other",
            "halt_reason": "archive terminal",
            "phase": "halted",
        })
        session_path.write_text(json.dumps(state), encoding="utf-8")
        resolved_state_bytes = session_path.read_bytes()
    else:
        repository, repository_root, lease_id = _seed_repository(tmp_path / "seed")
        command = MarkHalt(HaltCategory.OTHER, "archive terminal")
        request = _request(
            "archive-terminal", lease_id, typed_command=command,
            event_types=("mission-halted",),
        )
        admitted = repository.begin(request)
        assert isinstance(admitted, AdmittedSnapshot) and admitted.base is not None
        admitted_state = replace(
            admitted.base.state,
            lease=admitted.pending_lease.target,
            snapshot_provenance=None,
        )
        decision = decide(admitted_state, command)
        assert decision.accepted and decision.transition is not None
        prepared = repository.stage(admitted, decision.transition, request.blobs)
        repository.commit(prepared, prepared.precondition)
        resolved_state_bytes = repository.read("test").state_bytes
        shutil.copytree(repository_root, worktree / ".mission-state")
        (worktree / ".mission-state" / "sessions" / "test-assumptions.md").write_text(
            "# assumptions\n", encoding="utf-8"
        )
    archived = run_cli(
        "archive-worktree",
        "--destination-root",
        str(destination),
        "--json",
        cwd=worktree,
        env_extra={"MISSION_SESSION_ID": "test"},
    )

    assert archived.returncode == 0, archived.stderr
    validation = validate_worktree_archive_bundle(
        Path(json.loads(archived.stdout)["bundle_path"])
    )
    assert validation.status == "valid", validation.reason
    assert len(validation.state_paths) == 1
    assert validation.state_paths[0].read_bytes() == resolved_state_bytes


@pytest.mark.parametrize("repository_kind", ["legacy-v4", "canonical-v5"])
def test_terminal_archive_is_visible_to_audit_stats_and_snapshot_destination(
    tmp_path, run_cli, repository_kind
):
    archive_root = tmp_path / "destination-visibility"
    archive_root.mkdir()
    worktree, destination = _make_neutral_git_worktree(archive_root)
    if repository_kind == "legacy-v4":
        session_path, _source = generate_cli_state_bytes(worktree)
        state = json.loads(session_path.read_text(encoding="utf-8"))
        state.update({
            "loop_active": False,
            "halt_category": "other",
            "halt_reason": "terminal archive visibility",
            "phase": "halted",
        })
        session_path.write_text(json.dumps(state), encoding="utf-8")
    else:
        _session_path, repository_root, _repository, _evidence = (
            _canonical_v5_terminal_with_evidence(tmp_path / "canonical-source")
        )
        shutil.copytree(repository_root, worktree / ".mission-state")
    archived = run_cli(
        "archive-worktree",
        "--destination-root",
        str(destination),
        "--json",
        cwd=worktree,
        env_extra={"MISSION_SESSION_ID": "test"},
    )
    assert archived.returncode == 0, archived.stderr
    bundle = Path(json.loads(archived.stdout)["bundle_path"])
    validation = validate_worktree_archive_bundle(bundle)
    assert validation.status == "valid", validation.reason
    snapshot_path = tmp_path / (repository_kind + "-destination.snapshot.json")

    audit = _run_audit(destination, "--root", str(destination), "--json")
    stats = run_cli("stats", "--root", str(destination), "--json", cwd=destination)
    snapshot = _run_audit(
        destination,
        "--root",
        str(destination),
        "--snapshot-out",
        str(snapshot_path),
        "--json",
    )

    assert audit.returncode == stats.returncode == snapshot.returncode == 0
    assert json.loads(audit.stdout)["total_sessions"] == 1
    assert json.loads(stats.stdout)["total_sessions"] == 1
    assert json.loads(snapshot.stdout)["total_sessions"] == 1
    assert len(json.loads(snapshot_path.read_text(encoding="utf-8"))["records"]) == 1


def test_validated_archive_state_decode_drift_is_not_silently_skipped(
    tmp_path, run_cli
):
    result = _archive_canonical_evidence_repository(tmp_path, run_cli)
    assert result.returncode == 0, result.stderr
    validation = validate_worktree_archive_bundle(
        Path(json.loads(result.stdout)["bundle_path"])
    )
    assert validation.status == "valid"
    validation.state_paths[0].write_bytes(b"{broken")

    with pytest.raises(ValueError, match="drifted"):
        read_validated_archive_authoritative_snapshot(validation)


@pytest.mark.parametrize("damage", ["malformed-head", "missing-commit", "digest-drift"])
def test_broken_live_v5_is_not_zero_sessions_for_audit_stats_or_snapshot(
    tmp_path, run_cli, damage
):
    session_path, repository_root = _v5_session(tmp_path / damage)
    if damage == "malformed-head":
        session_path.write_text('{"schema":"mission-head/1"}', encoding="utf-8")
    else:
        head = _head_document(session_path)
        if damage == "missing-commit":
            (repository_root / head["commit"]["path"]).unlink()
        else:
            head["commit"]["digest"] = "sha256:" + "0" * 64
            _write_head(session_path, head)
    root = repository_root.parent
    snapshot_path = tmp_path / (damage + ".snapshot.json")

    audit = _run_audit(root, "--root", str(root), "--json")
    stats = run_cli("stats", "--root", str(root), "--json", cwd=root)
    lane_report = run_cli("lane-report", "--json", cwd=root)
    snapshot = _run_audit(
        root, "--root", str(root), "--snapshot-out", str(snapshot_path), "--json"
    )

    assert audit.returncode != 0
    assert stats.returncode != 0
    assert lane_report.returncode != 0
    assert snapshot.returncode != 0
    assert not snapshot_path.exists()


def test_live_v5_alias_head_is_rejected_by_every_discovery_consumer(tmp_path, run_cli):
    session_path, repository_root = _v5_session(tmp_path)
    alias = session_path.with_name("alias.json")
    alias.write_bytes(session_path.read_bytes())
    root = repository_root.parent

    with pytest.raises(Exception):
        _read(alias, session_id="alias")
    assert _run_audit(root, "--root", str(root), "--json").returncode != 0
    assert run_cli("stats", "--root", str(root), "--json", cwd=root).returncode != 0


def test_live_v4_alias_is_rejected_by_path_and_document_reader(tmp_path):
    session_path = _v4_state(tmp_path, session_id="real")
    alias = session_path.with_name("alias.json")
    alias.write_bytes(session_path.read_bytes())
    document = json.loads(alias.read_text(encoding="utf-8"))

    with pytest.raises(ValueError, match="session identity"):
        _reader().read_authoritative_snapshot(
            alias, expected_session_id=alias.stem
        )
    with pytest.raises(ValueError, match="session identity"):
        _reader().authoritative_snapshot_from_document(
            document, expected_session_id=alias.stem
        )


def test_legacy_v4_missing_embedded_identity_keeps_repository_compatibility(tmp_path):
    session_path = _v4_state(tmp_path, session_id="real")
    document = json.loads(session_path.read_text(encoding="utf-8"))
    document.pop("session_id")
    session_path.write_text(json.dumps(document), encoding="utf-8")

    path_snapshot = _read(session_path, session_id=session_path.stem)
    document_snapshot = _reader().authoritative_snapshot_from_document(
        document, expected_session_id=session_path.stem
    )

    assert path_snapshot.session_id is None
    assert document_snapshot.session_id is None


def test_live_v4_alias_is_rejected_by_audit_and_stats(tmp_path, run_cli):
    session_path = _v4_state(tmp_path, session_id="real")
    alias = session_path.with_name("alias.json")
    alias.write_bytes(session_path.read_bytes())
    session_path.unlink()
    root = alias.parents[2]

    assert _run_audit(root, "--root", str(root), "--json").returncode != 0
    assert run_cli("stats", "--root", str(root), "--json", cwd=root).returncode != 0


def test_nested_live_path_uses_immediate_mission_state_parent(tmp_path):
    nested = (
        tmp_path
        / ".mission-state"
        / "copied-project"
        / ".mission-state"
        / "sessions"
        / "alias.json"
    )

    assert _reader().is_live_session_path(nested) is True
    assert _reader().expected_session_id_for_live_path(nested) == "alias"


@pytest.mark.parametrize("payload_kind", ["alias", "malformed"])
def test_nested_live_alias_or_malformed_state_fails_closed_for_all_consumers(
    tmp_path, run_cli, payload_kind
):
    project = tmp_path / ".mission-state" / "copied-project"
    state_path = _v4_state(project, session_id="real")
    alias = state_path.with_name("alias.json")
    if payload_kind == "alias":
        alias.write_bytes(state_path.read_bytes())
    else:
        alias.write_text('{"session_id":"alias"', encoding="utf-8")
    state_path.unlink()
    root = alias.parents[2]

    with pytest.raises(Exception):
        _reader().read_authoritative_snapshot(
            alias, expected_session_id=alias.stem
        )
    assert _run_audit(root, "--root", str(root), "--json").returncode != 0
    assert run_cli("stats", "--root", str(root), "--json", cwd=root).returncode != 0


def test_stop_hook_without_jq_fails_closed_with_fixed_block_json(tmp_path):
    state_path = _v4_state(tmp_path, session_id="test")

    result = _run_hook_without_jq(state_path.parents[2], tmp_path)

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "decision": "block",
        "reason": "mission Stop guard requires jq; state verdict is unavailable",
        "outcome_kind": "expected-gate",
    }


def test_stop_display_uses_only_the_last_score_history_entry(tmp_path):
    state_path = _v4_state(
        tmp_path,
        session_id="test",
        score_history=[{"composite": 4.9}, {"note": "progress only"}],
    )

    result = _run_hook(state_path.parents[2], session_id="test")

    assert "last_score=n/a" in json.loads(result.stdout)["reason"]


def test_python39_reader_annotations_do_not_use_pep604_path_union():
    source = (
        REPO_ROOT
        / "skills"
        / "mission"
        / "lib"
        / "mission_persistence"
        / "authoritative_reader.py"
    ).read_text(encoding="utf-8")

    assert "Path | str" not in source
