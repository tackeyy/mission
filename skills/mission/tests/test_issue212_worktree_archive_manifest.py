"""Issue #212: worktree state/evidence archive manifest regression tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
MISSION_AUDIT_PY = REPO_ROOT / "scripts" / "mission-audit.py"
SESSION_ID = "session-212"
MISSION_ID = "feedfacecafebabe"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_completed_worktree(tmp_path: Path) -> tuple[Path, Path]:
    worktree = tmp_path / "neutral-worktree"
    destination = tmp_path / "neutral-main"
    mission_state = worktree / ".mission-state"

    assumptions = _write(
        mission_state / "sessions" / f"{SESSION_ID}-assumptions.md",
        "# assumptions\n",
    )
    artifact = _write(
        mission_state / "artifacts" / SESSION_ID / "mission-artifact.md",
        "# artifact\n",
    )
    scoring = _write(
        mission_state / "archive" / "noncanonical-scoring.json",
        '{"items":{"accuracy":4.6}}\n',
    )
    reviews = _write(
        mission_state / "archive" / "noncanonical-reviews.json",
        '{"reviews":[]}\n',
    )
    specialist = _write(
        mission_state / "archive" / "specialist-note.md",
        "# specialist evidence\n",
    )
    progress = _write(
        mission_state / "archive" / "progress.md",
        "# progress evidence\n",
    )
    progress_artifact = _write(
        mission_state / "archive" / "progress-data.json",
        '{"completed":1}\n',
    )

    state = {
        "mission": "neutral archive manifest test",
        "mission_id": MISSION_ID,
        "session_id": SESSION_ID,
        "iteration": 2,
        "project_root": str(worktree),
        "passes": True,
        "loop_active": False,
        "halt_reason": "",
        "phase": "done",
        "started_at": "2026-07-20T00:00:00Z",
        "updated_at": "2026-07-20T00:10:00Z",
        "assumptions_path": str(assumptions),
        "artifact": {
            "path": str(artifact.relative_to(worktree)),
            "required_for_pass": True,
            "status": "rendered",
            "last_rendered_at": "2026-07-20T00:09:00Z",
        },
        "score_history": [
            {
                "iteration": 2,
                "composite": 4.6,
                "min_item": 4.5,
                "items": {"accuracy": 4.6},
                "timestamp": "2026-07-20T00:08:00Z",
                "score_source": "scoring-json",
                "scoring_evidence_path": str(scoring),
                "findings_evidence_path": str(reviews.relative_to(worktree)),
                "open_high": 0,
            }
        ],
        "specialist_invocations": [
            {
                "iteration": 2,
                "phase": "execution",
                "skill": "neutral-specialist",
                "status": "completed",
                "evidence_path": str(specialist.relative_to(worktree)),
            }
        ],
        "progress": {
            "evidence_path": str(progress.relative_to(worktree)),
            "artifact_path": str(progress_artifact.relative_to(worktree)),
        },
    }
    state_file = mission_state / "sessions" / f"{SESSION_ID}.json"
    _write(state_file, json.dumps(state, indent=2) + "\n")
    return worktree, destination


def _archive(run_cli, worktree: Path, destination: Path):
    return run_cli(
        "archive-worktree",
        "--destination-root",
        str(destination),
        "--json",
        cwd=worktree,
        env_extra={"MISSION_SESSION_ID": SESSION_ID},
    )


def _run_audit(root: Path) -> dict:
    result = subprocess.run(
        [sys.executable, str(MISSION_AUDIT_PY), "--root", str(root), "--since", "2026-07-20", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _rewrite_manifest_digest(manifest_path: Path, manifest: dict) -> None:
    core = {
        key: manifest[key]
        for key in ("schema", "session_id", "mission_id", "iteration", "evidence")
    }
    manifest["content_digest"] = hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def _move_scoring_to_manifest_only_path(bundle: Path) -> Path:
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scoring = next(item for item in manifest["evidence"] if item["evidence_kind"] == "scoring")
    old_path = bundle / scoring["archive_path"]
    new_path = bundle / "payload" / "score-proof.bin"
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.replace(new_path)
    scoring["archive_path"] = "payload/score-proof.bin"
    _rewrite_manifest_digest(manifest_path, manifest)
    return manifest_path


def test_archive_worktree_copies_allowlisted_evidence_and_writes_manifest(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)

    result = _archive(run_cli, worktree, destination)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    bundle = Path(payload["bundle_path"])
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "mission-worktree-archive/1"
    assert manifest["session_id"] == SESSION_ID
    assert manifest["mission_id"] == MISSION_ID
    assert manifest["iteration"] == 2
    assert {item["evidence_kind"] for item in manifest["evidence"]} == {
        "state",
        "assumptions",
        "artifact",
        "scoring",
        "reviews",
        "specialist",
        "progress",
        "progress-artifact",
    }
    for item in manifest["evidence"]:
        archived = bundle / item["archive_path"]
        assert archived.is_file()
        assert item["session_id"] == SESSION_ID
        assert item["mission_id"] == MISSION_ID
        assert item["iteration"] == 2
        assert item["sha256"] == _sha256(archived)
        assert item["size"] == archived.stat().st_size
        assert not item["source_reference"].startswith("/")

    assert (bundle / "archive" / "iter-2-feedface-scoring.json").is_file()
    assert (bundle / "archive" / "iter-2-feedface-reviews.json").is_file()


def test_archived_bundle_survives_worktree_removal_without_missing_scoring(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    shutil.rmtree(worktree)

    data = _run_audit(destination)
    assert data["total_sessions"] == 1
    assert data["missing_scoring_evidence_count"] == 0
    assert data["duplicate_group_count"] == 0


def test_audit_uses_valid_manifest_for_noncanonical_scoring_path(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    bundle = Path(json.loads(result.stdout)["bundle_path"])
    _move_scoring_to_manifest_only_path(bundle)
    shutil.rmtree(worktree)

    with_manifest = _run_audit(destination)
    (bundle / "manifest.json").unlink()
    without_manifest = _run_audit(destination)

    assert with_manifest["missing_scoring_evidence_count"] == 0
    assert without_manifest["missing_scoring_evidence_count"] == 1


@pytest.mark.parametrize(
    "tamper",
    [
        "top-session",
        "top-mission",
        "top-iteration",
        "item-session",
        "item-mission",
        "item-iteration",
        "content",
        "malformed",
    ],
)
def test_audit_rejects_tampered_or_malformed_manifest(tmp_path, run_cli, tamper):
    worktree, destination = _make_completed_worktree(tmp_path)
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    bundle = Path(json.loads(result.stdout)["bundle_path"])
    manifest_path = _move_scoring_to_manifest_only_path(bundle)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scoring = next(item for item in manifest["evidence"] if item["evidence_kind"] == "scoring")
    if tamper == "malformed":
        manifest_path.write_text("{not-json\n", encoding="utf-8")
    elif tamper == "content":
        (bundle / scoring["archive_path"]).write_text("changed\n", encoding="utf-8")
    else:
        scope, field = tamper.split("-", 1)
        target = manifest if scope == "top" else scoring
        target[{"session": "session_id", "mission": "mission_id", "iteration": "iteration"}[field]] = (
            999 if field == "iteration" else "tampered"
        )
        _rewrite_manifest_digest(manifest_path, manifest)
    shutil.rmtree(worktree)

    data = _run_audit(destination)

    assert data["missing_scoring_evidence_count"] == 1


def test_archive_worktree_is_idempotent(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    first = _archive(run_cli, worktree, destination)
    assert first.returncode == 0, first.stderr
    first_payload = json.loads(first.stdout)
    manifest_path = Path(first_payload["bundle_path"]) / "manifest.json"
    first_bytes = manifest_path.read_bytes()

    second = _archive(run_cli, worktree, destination)

    assert second.returncode == 0, second.stderr
    second_payload = json.loads(second.stdout)
    assert second_payload["action"] == "unchanged"
    assert second_payload["bundle_path"] == first_payload["bundle_path"]
    assert manifest_path.read_bytes() == first_bytes
    assert len(list((destination / ".mission-state" / "archive").glob("worktree-*"))) == 1


def test_archive_worktree_updates_bundle_when_evidence_changes(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    first = _archive(run_cli, worktree, destination)
    assert first.returncode == 0, first.stderr
    first_payload = json.loads(first.stdout)
    first_manifest = first_payload["manifest"]
    scoring = worktree / ".mission-state" / "archive" / "noncanonical-scoring.json"
    scoring.write_text('{"items":{"accuracy":4.7}}\n', encoding="utf-8")

    second = _archive(run_cli, worktree, destination)

    assert second.returncode == 0, second.stderr
    second_payload = json.loads(second.stdout)
    assert second_payload["action"] == "updated"
    assert second_payload["manifest"]["content_digest"] != first_manifest["content_digest"]
    archived_scoring = Path(second_payload["bundle_path"]) / "archive" / "iter-2-feedface-scoring.json"
    assert archived_scoring.read_text(encoding="utf-8") == '{"items":{"accuracy":4.7}}\n'


def test_bundle_replace_restores_previous_bundle_after_mid_replace_failure(tmp_path, monkeypatch):
    module_path = REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state_issue212", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = tmp_path / "worktree-neutral"
    staging = tmp_path / ".worktree-neutral.tmp"
    for root, digest, marker in ((bundle, "old", "old"), (staging, "new", "new")):
        root.mkdir()
        marker_path = root / "marker.txt"
        marker_path.write_text(marker, encoding="utf-8")
        manifest = {
            "schema": "mission-worktree-archive/1",
            "session_id": SESSION_ID,
            "mission_id": MISSION_ID,
            "iteration": 2,
            "evidence": [
                {
                    "session_id": SESSION_ID,
                    "mission_id": MISSION_ID,
                    "iteration": 2,
                    "evidence_kind": "state",
                    "source_reference": ".mission-state/sessions/session-212.json",
                    "archive_path": "marker.txt",
                    "sha256": _sha256(marker_path),
                    "size": marker_path.stat().st_size,
                }
            ],
            "created_at": "2026-07-20T00:00:00Z",
            "content_digest": digest,
        }
        _rewrite_manifest_digest(root / "manifest.json", manifest)
    real_replace = module.os.replace
    call_count = 0

    def fail_second_replace(source, destination):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("injected replacement failure")
        return real_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_second_replace)

    with pytest.raises(OSError, match="injected replacement failure"):
        module._replace_archive_bundle(staging, bundle)

    assert (bundle / "marker.txt").read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".*.backup-*"))


def test_archive_worktree_fails_closed_when_required_scoring_is_missing(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    (worktree / ".mission-state" / "archive" / "noncanonical-scoring.json").unlink()

    result = _archive(run_cli, worktree, destination)

    assert result.returncode == 2
    assert "required evidence" in result.stderr
    archive_root = destination / ".mission-state" / "archive"
    assert not list(archive_root.glob("worktree-*")) if archive_root.exists() else True


@pytest.mark.parametrize(
    "reference",
    ["assumptions", "artifact", "reviews", "specialist", "progress", "progress-artifact"],
)
def test_archive_worktree_fails_closed_when_referenced_evidence_is_missing(tmp_path, run_cli, reference):
    worktree, destination = _make_completed_worktree(tmp_path)
    paths = {
        "assumptions": worktree / ".mission-state" / "sessions" / f"{SESSION_ID}-assumptions.md",
        "artifact": worktree / ".mission-state" / "artifacts" / SESSION_ID / "mission-artifact.md",
        "reviews": worktree / ".mission-state" / "archive" / "noncanonical-reviews.json",
        "specialist": worktree / ".mission-state" / "archive" / "specialist-note.md",
        "progress": worktree / ".mission-state" / "archive" / "progress.md",
        "progress-artifact": worktree / ".mission-state" / "archive" / "progress-data.json",
    }
    paths[reference].unlink()

    result = _archive(run_cli, worktree, destination)

    assert result.returncode == 2
    assert "required evidence file is missing" in result.stderr


def test_manifest_references_are_private_relative_paths(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)

    result = _archive(run_cli, worktree, destination)

    assert result.returncode == 0, result.stderr
    manifest = json.loads(Path(json.loads(result.stdout)["bundle_path"]).joinpath("manifest.json").read_text())
    manifest_text = json.dumps(manifest, ensure_ascii=False)
    assert str(worktree) not in manifest_text
    assert str(Path.home()) not in manifest_text
    for item in manifest["evidence"]:
        source_reference = item["source_reference"]
        archive_path = item["archive_path"]
        assert source_reference.startswith(".mission-state/")
        assert not Path(source_reference).is_absolute()
        assert ".." not in Path(source_reference).parts
        assert "://" not in source_reference
        assert not Path(archive_path).is_absolute()
        assert ".." not in Path(archive_path).parts


def test_archive_worktree_rejects_path_escape(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    outside = _write(tmp_path / "outside.md", "secret\n")
    state_file = worktree / ".mission-state" / "sessions" / f"{SESSION_ID}.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["artifact"]["path"] = str(outside)
    state_file.write_text(json.dumps(state), encoding="utf-8")

    result = _archive(run_cli, worktree, destination)

    assert result.returncode == 2
    assert "outside .mission-state" in result.stderr


def test_archive_worktree_rejects_symlink_evidence(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    target = worktree / ".mission-state" / "archive" / "real-scoring.json"
    _write(target, "{}\n")
    link = worktree / ".mission-state" / "archive" / "noncanonical-scoring.json"
    link.unlink()
    link.symlink_to(target)

    result = _archive(run_cli, worktree, destination)

    assert result.returncode == 2
    assert "symlink" in result.stderr
