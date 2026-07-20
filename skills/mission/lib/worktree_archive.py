"""Shared fail-closed validation for immutable worktree archive generations."""

from __future__ import annotations

import hashlib
import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WORKTREE_ARCHIVE_SCHEMA = "mission-worktree-archive/1"
WORKTREE_ARCHIVE_POINTER_SCHEMA = "mission-worktree-current/1"


@dataclass(frozen=True)
class WorktreeArchiveValidation:
    status: str
    root: Path
    generation: str | None = None
    reason: str | None = None
    state_paths: tuple[Path, ...] = ()


def _invalid(bundle: Path, root: Path, reason: str, generation: str | None = None):
    return WorktreeArchiveValidation("invalid", root, generation, reason)


def _safe_relative_path(value: Any, *, state_reference: bool = False) -> Path | None:
    if not isinstance(value, str) or not value or "://" in value:
        return None
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        return None
    if state_reference and (not path.parts or path.parts[0] != ".mission-state"):
        return None
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_worktree_archive_bundle(bundle: Path) -> WorktreeArchiveValidation:
    """Resolve one bundle and verify a generation manifest before exposing state."""
    try:
        bundle_stat = bundle.lstat()
    except FileNotFoundError:
        return _invalid(bundle, bundle, "bundle-not-regular-directory")
    except OSError:
        return _invalid(bundle, bundle, "bundle-access-error")
    if stat.S_ISLNK(bundle_stat.st_mode) or not stat.S_ISDIR(bundle_stat.st_mode):
        return _invalid(bundle, bundle, "bundle-not-regular-directory")

    pointer_path = bundle / "current.json"
    try:
        pointer_stat = pointer_path.lstat()
    except FileNotFoundError:
        return WorktreeArchiveValidation("legacy", bundle)
    except OSError:
        return _invalid(bundle, bundle, "pointer-access-error")
    if stat.S_ISLNK(pointer_stat.st_mode) or not stat.S_ISREG(pointer_stat.st_mode):
        return _invalid(bundle, bundle, "pointer-not-regular-file")
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except OSError:
        return _invalid(bundle, bundle, "pointer-access-error")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _invalid(bundle, bundle, "pointer-invalid-json")
    generation = pointer.get("generation") if isinstance(pointer, dict) else None
    if (
        not isinstance(pointer, dict)
        or pointer.get("schema") != WORKTREE_ARCHIVE_POINTER_SCHEMA
        or not isinstance(generation, str)
        or not generation
        or generation in {".", ".."}
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for character in generation
        )
    ):
        return _invalid(bundle, bundle, "pointer-invalid-schema-or-generation")

    generations_root = bundle / "generations"
    try:
        generations_stat = generations_root.lstat()
    except FileNotFoundError:
        return _invalid(bundle, generations_root, "generations-not-regular-directory", generation)
    except OSError:
        return _invalid(bundle, generations_root, "generations-access-error", generation)
    if stat.S_ISLNK(generations_stat.st_mode) or not stat.S_ISDIR(generations_stat.st_mode):
        return _invalid(bundle, generations_root, "generations-not-regular-directory", generation)

    generation_root = generations_root / generation
    try:
        generation_stat = generation_root.lstat()
    except FileNotFoundError:
        return _invalid(bundle, generation_root, "generation-missing-or-not-directory", generation)
    except OSError:
        return _invalid(bundle, generation_root, "generation-access-error", generation)
    if stat.S_ISLNK(generation_stat.st_mode) or not stat.S_ISDIR(generation_stat.st_mode):
        return _invalid(bundle, generation_root, "generation-missing-or-not-directory", generation)

    manifest_path = generation_root / "manifest.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return _invalid(bundle, generation_root, "manifest-not-regular-file", generation)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _invalid(bundle, generation_root, "manifest-invalid-json", generation)
    if not isinstance(manifest, dict) or manifest.get("schema") != WORKTREE_ARCHIVE_SCHEMA:
        return _invalid(bundle, generation_root, "manifest-invalid-schema", generation)
    evidence = manifest.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return _invalid(bundle, generation_root, "manifest-invalid-evidence", generation)
    core = {
        "schema": manifest["schema"],
        "session_id": manifest.get("session_id"),
        "mission_id": manifest.get("mission_id"),
        "iteration": manifest.get("iteration"),
        "evidence": evidence,
    }
    content_digest = hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if manifest.get("content_digest") != content_digest or generation != content_digest:
        return _invalid(bundle, generation_root, "manifest-content-digest-mismatch", generation)

    seen_paths: set[str] = set()
    state_paths: list[Path] = []
    for item in evidence:
        if not isinstance(item, dict):
            return _invalid(bundle, generation_root, "manifest-invalid-evidence", generation)
        if (
            item.get("session_id") != manifest.get("session_id")
            or item.get("mission_id") != manifest.get("mission_id")
            or not isinstance(item.get("iteration"), int)
            or isinstance(item.get("iteration"), bool)
            or not isinstance(item.get("evidence_kind"), str)
        ):
            return _invalid(bundle, generation_root, "manifest-evidence-identity-mismatch", generation)
        source_reference = _safe_relative_path(item.get("source_reference"), state_reference=True)
        archive_path = _safe_relative_path(item.get("archive_path"))
        if source_reference is None or archive_path is None or archive_path.as_posix() in seen_paths:
            return _invalid(bundle, generation_root, "manifest-unsafe-or-duplicate-path", generation)
        seen_paths.add(archive_path.as_posix())
        archived = generation_root / archive_path
        current = generation_root
        for part in archive_path.parts:
            current = current / part
            if current.is_symlink():
                return _invalid(bundle, generation_root, "manifest-evidence-symlink", generation)
        expected_size = item.get("size")
        expected_hash = item.get("sha256")
        if (
            not archived.is_file()
            or not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
            or not isinstance(expected_hash, str)
            or len(expected_hash) != 64
        ):
            return _invalid(bundle, generation_root, "manifest-invalid-evidence-file", generation)
        try:
            if archived.stat().st_size != expected_size or _sha256(archived) != expected_hash:
                return _invalid(bundle, generation_root, "manifest-evidence-integrity-mismatch", generation)
        except OSError:
            return _invalid(bundle, generation_root, "manifest-evidence-access-error", generation)
        if item["evidence_kind"] == "state":
            state_paths.append(archived)

    if len(state_paths) != 1:
        return _invalid(bundle, generation_root, "manifest-state-count-invalid", generation)
    try:
        state = json.loads(state_paths[0].read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _invalid(bundle, generation_root, "manifest-state-invalid-json", generation)
    if (
        not isinstance(state, dict)
        or state.get("session_id") != manifest.get("session_id")
        or state.get("mission_id") != manifest.get("mission_id")
        or state.get("iteration") != manifest.get("iteration")
    ):
        return _invalid(bundle, generation_root, "manifest-state-identity-mismatch", generation)
    return WorktreeArchiveValidation(
        "valid", generation_root, generation=generation, state_paths=tuple(state_paths)
    )
