"""Issue #391: archive generations materialize canonical state without deletion."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
MISSION_STATE = REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py"
MISSION_AUDIT = REPO_ROOT / "scripts" / "mission-audit.py"


def _load_mission_state():
    spec = importlib.util.spec_from_file_location(
        "mission_state_issue391_compaction", MISSION_STATE,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_halted(path: Path, root: Path, *, session_id: str = "session-391") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "mission": "archive compaction fixture",
                "mission_id": "mission-391",
                "session_id": session_id,
                "project_root": str(root),
                "loop_active": False,
                "passes": False,
                "halt_reason": "superseded fixture",
                "halt_category": "stagnation",
                "phase": "halted",
                "iteration": 1,
                "score_history": [],
                "started_at": "2026-08-12T00:00:00Z",
                "updated_at": "2026-08-12T00:01:00Z",
                "schema_version": 4,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _run_state(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith("MISSION_")
        and key not in {"CODEX_THREAD_ID", "CLAUDE_CODE_SESSION_ID"}
    }
    env["MISSION_SESSION_ID"] = "archive-compaction-test"
    return subprocess.run(
        [sys.executable, str(MISSION_STATE), *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
    )


def _audit(root: Path, *, forensic: bool = False) -> dict:
    args = [sys.executable, str(MISSION_AUDIT), "--root", str(root), "--json"]
    if forensic:
        args.append("--forensic")
    result = subprocess.run(args, cwd=root, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_resolve_archive_publishes_canonical_generation_and_audit_views(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / ".mission-state" / "sessions" / "current.json"
    duplicate = tmp_path / ".mission-state" / "archive" / "state-copy.json"
    _write_halted(canonical, tmp_path)
    duplicate.parent.mkdir(parents=True, exist_ok=True)
    duplicate.write_bytes(canonical.read_bytes())

    result = _run_state(
        tmp_path,
        "resolve-archive",
        "--path", ".mission-state/archive/state-copy.json",
        "--status", "superseded",
        "--canonical-path", ".mission-state/sessions/current.json",
        "--retention-generations", "1",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    pointer = json.loads(
        (tmp_path / ".mission-state" / "archive" / "compaction" / "current.json").read_text(
            encoding="utf-8"
        )
    )
    generation = pointer["generation"]
    manifest_path = (
        tmp_path / ".mission-state" / "archive" / "compaction"
        / "generations" / generation / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert output["archive_generation"] == generation
    assert manifest["schema"] == "mission-state-archive-generation/1"
    assert manifest["content_digest"] == generation
    assert manifest["retention_policy"] == {
        "retain_generations": 1,
        "physical_deletion": "forbidden",
    }
    assert manifest["records"] == [
        {
            "canonical_path": ".mission-state/sessions/current.json",
            "canonical_sha256": manifest["records"][0]["canonical_sha256"],
            "mission_id": "mission-391",
            "session_id": "session-391",
            "superseded": [
                {
                    "path": ".mission-state/archive/state-copy.json",
                    "sha256": manifest["records"][0]["superseded"][0]["sha256"],
                }
            ],
        }
    ]
    assert canonical.exists()
    assert duplicate.exists()

    materialized = _audit(tmp_path)
    forensic = _audit(tmp_path, forensic=True)
    assert materialized["archive_compaction"]["view"] == "materialized"
    assert materialized["archive_compaction"]["discovered_record_count"] == 1
    assert forensic["archive_compaction"]["view"] == "forensic"
    assert forensic["archive_compaction"]["discovered_record_count"] == 2


def test_retention_keeps_prior_generation_and_full_lineage(tmp_path: Path) -> None:
    canonical = tmp_path / ".mission-state" / "sessions" / "current.json"
    first = tmp_path / ".mission-state" / "archive" / "state-copy-a.json"
    second = tmp_path / ".mission-state" / "archive" / "state-copy-b.json"
    _write_halted(canonical, tmp_path)
    first.parent.mkdir(parents=True, exist_ok=True)
    first.write_bytes(canonical.read_bytes())
    second.write_bytes(canonical.read_bytes())

    generations: list[str] = []
    for duplicate in (first, second):
        result = _run_state(
            tmp_path,
            "resolve-archive",
            "--path", str(duplicate.relative_to(tmp_path)),
            "--status", "superseded",
            "--canonical-path", ".mission-state/sessions/current.json",
            "--retention-generations", "1",
            "--json",
        )
        assert result.returncode == 0, result.stderr
        generations.append(json.loads(result.stdout)["archive_generation"])

    generations_root = (
        tmp_path / ".mission-state" / "archive" / "compaction" / "generations"
    )
    assert generations[0] != generations[1]
    assert all((generations_root / generation / "manifest.json").is_file() for generation in generations)
    current = json.loads(
        (tmp_path / ".mission-state" / "archive" / "compaction" / "current.json").read_text(
            encoding="utf-8"
        )
    )
    manifest = json.loads(
        (generations_root / current["generation"] / "manifest.json").read_text(encoding="utf-8")
    )
    assert [item["path"] for item in manifest["records"][0]["superseded"]] == [
        ".mission-state/archive/state-copy-a.json",
        ".mission-state/archive/state-copy-b.json",
    ]
    assert first.exists() and second.exists()


def test_canonical_identity_mismatch_rejects_without_rewriting_target(tmp_path: Path) -> None:
    canonical = tmp_path / ".mission-state" / "sessions" / "current.json"
    duplicate = tmp_path / ".mission-state" / "archive" / "state-copy.json"
    _write_halted(canonical, tmp_path, session_id="canonical-session")
    _write_halted(duplicate, tmp_path, session_id="duplicate-session")
    before = duplicate.read_bytes()

    result = _run_state(
        tmp_path,
        "resolve-archive",
        "--path", ".mission-state/archive/state-copy.json",
        "--status", "superseded",
        "--canonical-path", ".mission-state/sessions/current.json",
        "--json",
    )

    assert result.returncode == 2
    assert duplicate.read_bytes() == before
    assert not (
        tmp_path / ".mission-state" / "archive" / "compaction" / "current.json"
    ).exists()


def test_audit_fails_closed_on_tampered_compaction_pointer(tmp_path: Path) -> None:
    canonical = tmp_path / ".mission-state" / "sessions" / "current.json"
    duplicate = tmp_path / ".mission-state" / "archive" / "state-copy.json"
    _write_halted(canonical, tmp_path)
    duplicate.parent.mkdir(parents=True, exist_ok=True)
    duplicate.write_bytes(canonical.read_bytes())
    result = _run_state(
        tmp_path,
        "resolve-archive",
        "--path", ".mission-state/archive/state-copy.json",
        "--status", "superseded",
        "--canonical-path", ".mission-state/sessions/current.json",
        "--json",
    )
    assert result.returncode == 0, result.stderr
    pointer = tmp_path / ".mission-state" / "archive" / "compaction" / "current.json"
    document = json.loads(pointer.read_text(encoding="utf-8"))
    document["manifest_sha256"] = "0" * 64
    pointer.write_text(json.dumps(document), encoding="utf-8")

    audit = _audit(tmp_path)

    assert audit["total_sessions"] == 0
    assert audit["invalid_worktree_archive_count"] == 1
    assert any(
        finding["code"] == "invalid-worktree-archive"
        for finding in audit["findings"]
    )


def test_compaction_rejects_hardlinked_canonical_before_pointer_publish(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / ".mission-state" / "sessions" / "current.json"
    duplicate = tmp_path / ".mission-state" / "archive" / "state-copy.json"
    alias = tmp_path / "canonical-alias.json"
    _write_halted(canonical, tmp_path)
    duplicate.parent.mkdir(parents=True, exist_ok=True)
    duplicate.write_bytes(canonical.read_bytes())
    os.link(canonical, alias)
    before = duplicate.read_bytes()

    result = _run_state(
        tmp_path,
        "resolve-archive",
        "--path", ".mission-state/archive/state-copy.json",
        "--status", "superseded",
        "--canonical-path", ".mission-state/sessions/current.json",
        "--json",
    )

    assert result.returncode == 2
    assert duplicate.read_bytes() == before
    assert not (
        tmp_path / ".mission-state" / "archive" / "compaction" / "current.json"
    ).exists()


def test_compaction_retry_reuses_valid_generation_bytes_after_pointer_gap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_mission_state()
    canonical = tmp_path / ".mission-state" / "sessions" / "current.json"
    duplicate = tmp_path / ".mission-state" / "archive" / "state-copy.json"
    _write_halted(canonical, tmp_path)
    duplicate.parent.mkdir(parents=True, exist_ok=True)
    duplicate.write_bytes(canonical.read_bytes())
    data = json.loads(duplicate.read_text(encoding="utf-8"))

    monkeypatch.setattr(module, "iso_now", lambda: "2026-08-12T00:10:00Z")
    first = module._publish_state_archive_compaction(
        tmp_path, duplicate, canonical, data, 1,
    )
    pointer = (
        tmp_path / ".mission-state" / "archive" / "compaction" / "current.json"
    )
    manifest_path = (
        tmp_path / ".mission-state" / "archive" / "compaction"
        / "generations" / first / "manifest.json"
    )
    manifest_before = manifest_path.read_bytes()
    pointer.unlink()

    monkeypatch.setattr(module, "iso_now", lambda: "2026-08-12T00:11:00Z")
    second = module._publish_state_archive_compaction(
        tmp_path, duplicate, canonical, data, 1,
    )

    assert second == first
    assert manifest_path.read_bytes() == manifest_before
    assert json.loads(pointer.read_text(encoding="utf-8"))["generation"] == first
