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


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _make_neutral_git_worktree(tmp_path: Path) -> tuple[Path, Path]:
    destination = tmp_path / "neutral-main"
    destination.mkdir()
    _git(destination, "init", "-b", "main")
    _git(destination, "config", "user.name", "Neutral Test")
    _git(destination, "config", "user.email", "neutral@example.invalid")
    _write(destination / "seed.txt", "seed\n")
    _git(destination, "add", "seed.txt")
    _git(destination, "commit", "-m", "seed")
    worktree = tmp_path / "neutral-worktree"
    _git(destination, "worktree", "add", "-b", "archive-source", str(worktree))
    return worktree, destination


def _make_completed_worktree(tmp_path: Path) -> tuple[Path, Path]:
    worktree, destination = _make_neutral_git_worktree(tmp_path)
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
    generation_root = _active_bundle_root(bundle)
    manifest_path = generation_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scoring = next(item for item in manifest["evidence"] if item["evidence_kind"] == "scoring")
    old_path = generation_root / scoring["archive_path"]
    new_path = generation_root / "payload" / "score-proof.bin"
    new_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.replace(new_path)
    scoring["archive_path"] = "payload/score-proof.bin"
    _rewrite_manifest_digest(manifest_path, manifest)
    if generation_root != bundle:
        new_generation_root = generation_root.with_name(manifest["content_digest"])
        generation_root.replace(new_generation_root)
        pointer = bundle / "current.json"
        pointer.write_text(
            json.dumps(
                {"schema": "mission-worktree-current/1", "generation": manifest["content_digest"]}
            )
            + "\n",
            encoding="utf-8",
        )
        manifest_path = new_generation_root / "manifest.json"
    return manifest_path


def _current_generation(bundle: Path) -> tuple[str, Path]:
    pointer = json.loads((bundle / "current.json").read_text(encoding="utf-8"))
    assert pointer["schema"] == "mission-worktree-current/1"
    generation = pointer["generation"]
    assert isinstance(generation, str) and generation
    return generation, bundle / "generations" / generation


def _active_bundle_root(bundle: Path) -> Path:
    if (bundle / "current.json").is_file():
        return _current_generation(bundle)[1]
    return bundle


def _load_audit_module(name: str):
    module_path = REPO_ROOT / "scripts" / "mission-audit.py"
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_archive_worktree_copies_allowlisted_evidence_and_writes_manifest(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)

    result = _archive(run_cli, worktree, destination)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    bundle = Path(payload["bundle_path"])
    generation_root = _active_bundle_root(bundle)
    manifest = json.loads((generation_root / "manifest.json").read_text(encoding="utf-8"))
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
        archived = generation_root / item["archive_path"]
        assert archived.is_file()
        assert item["session_id"] == SESSION_ID
        assert item["mission_id"] == MISSION_ID
        assert item["iteration"] == 2
        assert item["sha256"] == _sha256(archived)
        assert item["size"] == archived.stat().st_size
        assert not item["source_reference"].startswith("/")

    assert (generation_root / "archive" / "iter-2-feedface-scoring.json").is_file()
    assert (generation_root / "archive" / "iter-2-feedface-reviews.json").is_file()


def test_archived_bundle_survives_worktree_removal_without_missing_scoring(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    shutil.rmtree(worktree)

    data = _run_audit(destination)
    assert data["total_sessions"] == 1
    assert data["missing_scoring_evidence_count"] == 0
    assert data["duplicate_group_count"] == 0


def test_audit_resolves_specialist_evidence_from_generation_manifest_after_source_removal(
    tmp_path, run_cli
):
    worktree, destination = _make_completed_worktree(tmp_path)
    state_path = worktree / ".mission-state" / "sessions" / f"{SESSION_ID}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["specialist_invocations"][0]["mode"] = "command-provider"
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    specialist_path = worktree / state["specialist_invocations"][0]["evidence_path"]
    specialist_path.write_text(
        "# Oracle Browser Review Prepared\n\n"
        "To capture the review as command-provider output, rerun the provider.\n",
        encoding="utf-8",
    )
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    shutil.rmtree(worktree)

    data = _run_audit(destination)

    assert data["invalid_worktree_archive_count"] == 0
    assert data["preparation_only_completed_provider_count"] == 1
    item = data["preparation_only_completed_providers"][0]
    assert item["session_id"] == SESSION_ID
    assert item["bad_entries"][0]["skill"] == "neutral-specialist"
    assert "Oracle Browser Review Prepared" in item["bad_entries"][0]["evidence"][0]["markers"]


def test_audit_uses_valid_manifest_for_noncanonical_scoring_path(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    bundle = Path(json.loads(result.stdout)["bundle_path"])
    _move_scoring_to_manifest_only_path(bundle)
    shutil.rmtree(worktree)

    with_manifest = _run_audit(destination)
    (_active_bundle_root(bundle) / "manifest.json").unlink()
    without_manifest = _run_audit(destination)

    assert with_manifest["missing_scoring_evidence_count"] == 0
    assert without_manifest["total_sessions"] == 0
    assert without_manifest["invalid_worktree_archive_count"] == 1


@pytest.mark.parametrize("pointer_failure", ["malformed", "symlink", "missing-generation"])
def test_audit_reports_invalid_current_pointer_instead_of_silently_omitting_archive(
    tmp_path, run_cli, pointer_failure
):
    worktree, destination = _make_completed_worktree(tmp_path)
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    bundle = Path(json.loads(result.stdout)["bundle_path"])
    pointer = bundle / "current.json"
    if pointer_failure == "malformed":
        pointer.write_text("{not-json\n", encoding="utf-8")
    elif pointer_failure == "symlink":
        external = tmp_path / "external-pointer.json"
        external.write_text(pointer.read_text(encoding="utf-8"), encoding="utf-8")
        pointer.unlink()
        pointer.symlink_to(external)
    else:
        pointer.write_text(
            json.dumps({"schema": "mission-worktree-current/1", "generation": "f" * 64}) + "\n",
            encoding="utf-8",
        )

    data = _run_audit(destination)

    assert data["invalid_worktree_archive_count"] == 1
    assert data["invalid_worktree_archives"][0]["bundle_path"] == str(bundle)
    codes = {finding["code"] for finding in data["findings"]}
    assert "invalid-worktree-archive" in codes
    assert "no-critical-findings" not in codes


@pytest.mark.parametrize("state_failure", ["missing", "invalid-json"])
def test_audit_preflights_generation_state_before_loading_records(
    tmp_path, run_cli, state_failure
):
    worktree, destination = _make_completed_worktree(tmp_path)
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    bundle = Path(json.loads(result.stdout)["bundle_path"])
    generation, generation_root = _current_generation(bundle)
    manifest_path = generation_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    state_item = next(item for item in manifest["evidence"] if item["evidence_kind"] == "state")
    archived_state = generation_root / state_item["archive_path"]
    if state_failure == "missing":
        archived_state.unlink()
    else:
        archived_state.write_text("{not-json\n", encoding="utf-8")
        state_item["sha256"] = _sha256(archived_state)
        state_item["size"] = archived_state.stat().st_size
        _rewrite_manifest_digest(manifest_path, manifest)
        replacement = generation_root.with_name(manifest["content_digest"])
        generation_root.replace(replacement)
        (bundle / "current.json").write_text(
            json.dumps(
                {
                    "schema": "mission-worktree-current/1",
                    "generation": manifest["content_digest"],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert manifest["content_digest"] != generation

    data = _run_audit(destination)

    assert data["total_sessions"] == 0
    assert data["invalid_worktree_archive_count"] == 1
    assert data["invalid_worktree_archives"][0]["bundle_path"] == str(bundle)
    codes = {finding["code"] for finding in data["findings"]}
    assert "invalid-worktree-archive" in codes
    assert "no-critical-findings" not in codes


@pytest.mark.parametrize("symlink_ancestor", ["bundle", "generations"])
def test_audit_rejects_symlinked_archive_ancestors_without_reading_outside_root(
    tmp_path, run_cli, symlink_ancestor
):
    worktree, destination = _make_completed_worktree(tmp_path)
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    bundle = Path(json.loads(result.stdout)["bundle_path"])
    if symlink_ancestor == "bundle":
        external = tmp_path / "external-bundle"
        bundle.replace(external)
        bundle.symlink_to(external, target_is_directory=True)
    else:
        generations = bundle / "generations"
        external = tmp_path / "external-generations"
        generations.replace(external)
        generations.symlink_to(external, target_is_directory=True)

    data = _run_audit(destination)

    assert data["total_sessions"] == 0
    assert data["invalid_worktree_archive_count"] == 1
    assert data["invalid_worktree_archives"][0]["bundle_path"] == str(bundle)
    codes = {finding["code"] for finding in data["findings"]}
    assert "invalid-worktree-archive" in codes
    assert "no-critical-findings" not in codes


@pytest.mark.parametrize(
    ("symlink_ancestor", "reason"),
    [
        ("archive", "archive-root-symlink"),
        ("mission-state", "mission-state-root-symlink"),
    ],
)
def test_audit_rejects_symlinked_state_roots_without_reading_external_archive(
    tmp_path, run_cli, symlink_ancestor, reason
):
    worktree, destination = _make_completed_worktree(tmp_path)
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    mission_state = destination / ".mission-state"
    unsafe_path = mission_state / "archive" if symlink_ancestor == "archive" else mission_state
    external = tmp_path / f"external-{symlink_ancestor}"
    unsafe_path.replace(external)
    unsafe_path.symlink_to(external, target_is_directory=True)

    data = _run_audit(destination)

    assert data["total_sessions"] == 0
    assert data["invalid_worktree_archive_count"] == 1
    assert data["invalid_worktree_archives"][0] == {
        "bundle_path": str(unsafe_path),
        "reason": reason,
    }
    codes = {finding["code"] for finding in data["findings"]}
    assert "invalid-worktree-archive" in codes
    assert "no-critical-findings" not in codes


@pytest.mark.parametrize(
    ("access_target", "reason"),
    [
        ("bundle", "pointer-access-error"),
        ("archive", "archive-root-access-error"),
    ],
)
def test_audit_reports_unreadable_archive_path_instead_of_treating_it_as_absent(
    tmp_path, run_cli, access_target, reason
):
    worktree, destination = _make_completed_worktree(tmp_path)
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    bundle = Path(json.loads(result.stdout)["bundle_path"])
    unreadable = bundle if access_target == "bundle" else destination / ".mission-state" / "archive"
    unreadable.chmod(0)
    try:
        data = _run_audit(destination)
    finally:
        unreadable.chmod(0o700)

    assert data["total_sessions"] == 0
    assert data["invalid_worktree_archive_count"] == 1
    assert data["invalid_worktree_archives"][0]["bundle_path"] == str(unreadable)
    assert data["invalid_worktree_archives"][0]["reason"] == reason
    codes = {finding["code"] for finding in data["findings"]}
    assert "invalid-worktree-archive" in codes
    assert "no-critical-findings" not in codes


def test_audit_deduplicates_invalid_bundle_across_overlapping_roots(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    bundle = Path(json.loads(result.stdout)["bundle_path"])
    (bundle / "current.json").write_text("{not-json\n", encoding="utf-8")

    audit = subprocess.run(
        [
            sys.executable,
            str(MISSION_AUDIT_PY),
            "--root",
            str(destination),
            "--root",
            str(destination / ".mission-state"),
            "--since",
            "2026-07-20",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(audit.stdout)

    assert data["invalid_worktree_archive_count"] == 1
    assert data["invalid_worktree_archives"][0]["bundle_path"] == str(bundle)


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
        "non-object",
    ],
)
def test_audit_rejects_tampered_or_malformed_manifest(tmp_path, run_cli, tamper):
    worktree, destination = _make_completed_worktree(tmp_path)
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    bundle = Path(json.loads(result.stdout)["bundle_path"])
    manifest_path = _move_scoring_to_manifest_only_path(bundle)
    generation_root = _active_bundle_root(bundle)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scoring = next(item for item in manifest["evidence"] if item["evidence_kind"] == "scoring")
    if tamper == "malformed":
        manifest_path.write_text("{not-json\n", encoding="utf-8")
    elif tamper == "non-object":
        manifest_path.write_text("[]\n", encoding="utf-8")
    elif tamper == "content":
        (generation_root / scoring["archive_path"]).write_text("changed\n", encoding="utf-8")
    else:
        scope, field = tamper.split("-", 1)
        target = manifest if scope == "top" else scoring
        target[{"session": "session_id", "mission": "mission_id", "iteration": "iteration"}[field]] = (
            999 if field == "iteration" else "tampered"
        )
        _rewrite_manifest_digest(manifest_path, manifest)
    shutil.rmtree(worktree)

    data = _run_audit(destination)

    assert data["total_sessions"] == 0
    assert data["invalid_worktree_archive_count"] == 1


@pytest.mark.parametrize("tamper", ["relabel-reviews", "source-mismatch"])
def test_audit_rejects_manifest_lineage_that_disagrees_with_state(tmp_path, run_cli, tamper):
    worktree, destination = _make_completed_worktree(tmp_path)
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    bundle = Path(json.loads(result.stdout)["bundle_path"])
    generation_root = _active_bundle_root(bundle)
    manifest_path = generation_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    scoring = next(item for item in manifest["evidence"] if item["evidence_kind"] == "scoring")
    reviews = next(item for item in manifest["evidence"] if item["evidence_kind"] == "reviews")
    if tamper == "relabel-reviews":
        (generation_root / scoring["archive_path"]).unlink()
        manifest["evidence"].remove(scoring)
        reviews["evidence_kind"] = "scoring"
    else:
        scoring["source_reference"] = reviews["source_reference"]
    _rewrite_manifest_digest(manifest_path, manifest)
    shutil.rmtree(worktree)

    data = _run_audit(destination)

    assert data["total_sessions"] == 0
    assert data["invalid_worktree_archive_count"] == 1


def test_audit_validates_manifest_hashes_once_per_record(tmp_path, run_cli, monkeypatch):
    worktree, destination = _make_completed_worktree(tmp_path)
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    bundle = Path(json.loads(result.stdout)["bundle_path"])
    manifest = json.loads((_active_bundle_root(bundle) / "manifest.json").read_text(encoding="utf-8"))
    module_path = REPO_ROOT / "scripts" / "mission-audit.py"
    spec = importlib.util.spec_from_file_location("mission_audit_cache_issue212", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    real_hash = module._file_sha256
    hash_count = 0

    def count_hashes(path):
        nonlocal hash_count
        hash_count += 1
        return real_hash(path)

    monkeypatch.setattr(module, "_file_sha256", count_hashes)
    record = next(record for record in module.load_records([destination]) if record.state["session_id"] == SESSION_ID)

    for _ in range(3):
        assert module.scoring_evidence_paths(record, 2)

    assert hash_count == len(manifest["evidence"])


def test_audit_record_keeps_discovery_generation_snapshot_when_pointer_advances(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    first = _archive(run_cli, worktree, destination)
    assert first.returncode == 0, first.stderr
    bundle = Path(json.loads(first.stdout)["bundle_path"])
    first_generation, first_root = _current_generation(bundle)
    scoring = worktree / ".mission-state" / "archive" / "noncanonical-scoring.json"
    scoring.write_text('{"items":{"accuracy":4.7}}\n', encoding="utf-8")
    second = _archive(run_cli, worktree, destination)
    assert second.returncode == 0, second.stderr
    second_generation, _second_root = _current_generation(bundle)
    pointer = bundle / "current.json"
    pointer.write_text(
        json.dumps({"schema": "mission-worktree-current/1", "generation": first_generation}) + "\n",
        encoding="utf-8",
    )
    module = _load_audit_module("mission_audit_snapshot_issue212")
    record = next(record for record in module.load_records([destination]) if record.state["session_id"] == SESSION_ID)

    pointer.write_text(
        json.dumps({"schema": "mission-worktree-current/1", "generation": second_generation}) + "\n",
        encoding="utf-8",
    )

    assert record.archive_generation == first_generation
    assert record.archive_root == first_root
    evidence = module.scoring_evidence_paths(record, 2)
    assert evidence
    assert all(path.is_relative_to(first_root) for path in evidence)
    assert module.missing_scoring_evidence_iterations(record) == []


def test_generation_missing_manifest_is_invalid_and_cannot_use_legacy_root_fallback(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    result = _archive(run_cli, worktree, destination)
    assert result.returncode == 0, result.stderr
    bundle = Path(json.loads(result.stdout)["bundle_path"])
    _generation, generation_root = _current_generation(bundle)
    stale_scoring = bundle / "archive" / "iter-2-feedface-scoring.json"
    stale_scoring.parent.mkdir(parents=True)
    shutil.copy2(generation_root / "archive" / "iter-2-feedface-scoring.json", stale_scoring)
    (generation_root / "manifest.json").unlink()
    shutil.rmtree(worktree)

    data = _run_audit(destination)

    assert data["total_sessions"] == 0
    assert data["invalid_worktree_archive_count"] == 1
    assert data["missing_scoring_evidence_count"] == 0
    assert any(finding["code"] == "invalid-worktree-archive" for finding in data["findings"])


def test_archive_worktree_is_idempotent(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    first = _archive(run_cli, worktree, destination)
    assert first.returncode == 0, first.stderr
    first_payload = json.loads(first.stdout)
    manifest_path = _active_bundle_root(Path(first_payload["bundle_path"])) / "manifest.json"
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
    archived_scoring = (
        _active_bundle_root(Path(second_payload["bundle_path"]))
        / "archive"
        / "iter-2-feedface-scoring.json"
    )
    assert archived_scoring.read_text(encoding="utf-8") == '{"items":{"accuracy":4.7}}\n'


def test_archive_update_keeps_immutable_generations_and_auditable_atomic_pointer(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    first = _archive(run_cli, worktree, destination)
    assert first.returncode == 0, first.stderr
    bundle = Path(json.loads(first.stdout)["bundle_path"])
    first_generation, first_root = _current_generation(bundle)
    first_manifest_bytes = (first_root / "manifest.json").read_bytes()
    scoring = worktree / ".mission-state" / "archive" / "noncanonical-scoring.json"
    scoring.write_text('{"items":{"accuracy":4.7}}\n', encoding="utf-8")

    second = _archive(run_cli, worktree, destination)

    assert second.returncode == 0, second.stderr
    second_generation, second_root = _current_generation(bundle)
    assert second_generation != first_generation
    assert first_root.is_dir() and second_root.is_dir()
    assert (first_root / "manifest.json").read_bytes() == first_manifest_bytes

    pointer_path = bundle / "current.json"
    for generation in (first_generation, second_generation):
        temporary = pointer_path.with_suffix(".reader.tmp")
        temporary.write_text(
            json.dumps({"schema": "mission-worktree-current/1", "generation": generation}) + "\n",
            encoding="utf-8",
        )
        temporary.replace(pointer_path)
        data = _run_audit(destination)
        assert data["missing_scoring_evidence_count"] == 0


def test_generation_publish_failure_never_removes_previous_current(tmp_path, monkeypatch):
    module_path = REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("mission_state_generation_issue212", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bundle = tmp_path / "worktree-neutral"
    old_generation = bundle / "generations" / "old-generation"
    old_generation.mkdir(parents=True)
    old_sentinel = old_generation / "sentinel.txt"
    old_sentinel.write_text("old\n", encoding="utf-8")
    old_manifest = {
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
                "archive_path": "sentinel.txt",
                "sha256": _sha256(old_sentinel),
                "size": old_sentinel.stat().st_size,
            }
        ],
        "created_at": "2026-07-20T00:00:00Z",
        "content_digest": "pending",
    }
    _rewrite_manifest_digest(old_generation / "manifest.json", old_manifest)
    old_generation_name = old_manifest["content_digest"]
    published_old_generation = old_generation.with_name(old_generation_name)
    old_generation.replace(published_old_generation)
    old_generation = published_old_generation
    pointer = bundle / "current.json"
    pointer.write_text(
        json.dumps({"schema": "mission-worktree-current/1", "generation": old_generation_name}) + "\n",
        encoding="utf-8",
    )
    staging = tmp_path / ".new-generation.tmp"
    staging.mkdir()
    (staging / "sentinel.txt").write_text("new\n", encoding="utf-8")

    def fail_pointer_swap(_bundle, _generation):
        raise OSError("injected pointer swap failure")

    monkeypatch.setattr(module, "_atomic_write_archive_pointer", fail_pointer_swap)

    with pytest.raises(OSError, match="injected pointer swap failure"):
        module._publish_archive_generation(staging, bundle, "new-generation")

    assert json.loads(pointer.read_text(encoding="utf-8"))["generation"] == old_generation_name
    assert (old_generation / "sentinel.txt").read_text(encoding="utf-8") == "old\n"


def test_archive_worktree_fails_closed_when_required_scoring_is_missing(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    (worktree / ".mission-state" / "archive" / "noncanonical-scoring.json").unlink()

    result = _archive(run_cli, worktree, destination)

    assert result.returncode == 2
    assert "required evidence" in result.stderr
    archive_root = destination / ".mission-state" / "archive"
    assert not list(archive_root.glob("worktree-*")) if archive_root.exists() else True


@pytest.mark.parametrize("destination_kind", ["missing", "plain-directory", "different-repository"])
def test_archive_worktree_requires_existing_checkout_in_same_git_common_dir(
    tmp_path, run_cli, destination_kind
):
    worktree, valid_destination = _make_completed_worktree(tmp_path)
    if destination_kind == "missing":
        invalid_destination = tmp_path / "missing-destination"
    elif destination_kind == "plain-directory":
        invalid_destination = tmp_path / "plain-destination"
        invalid_destination.mkdir()
    else:
        invalid_destination = tmp_path / "different-repository"
        invalid_destination.mkdir()
        _git(invalid_destination, "init", "-b", "main")

    result = _archive(run_cli, worktree, invalid_destination)

    assert result.returncode == 2
    assert "git common directory" in result.stderr
    assert not (invalid_destination / ".mission-state").exists()
    assert valid_destination.is_dir()


def test_archive_worktree_rejects_duplicate_archive_paths(tmp_path, run_cli):
    worktree, destination = _make_completed_worktree(tmp_path)
    state_file = worktree / ".mission-state" / "sessions" / f"{SESSION_ID}.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["assumptions_path"] = str(state_file.relative_to(worktree))
    state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    result = _archive(run_cli, worktree, destination)

    assert result.returncode == 2
    assert "duplicate archive path" in result.stderr


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
    bundle = Path(json.loads(result.stdout)["bundle_path"])
    manifest = json.loads((_active_bundle_root(bundle) / "manifest.json").read_text())
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
