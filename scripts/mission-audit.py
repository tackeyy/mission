#!/usr/bin/env python3
"""Audit local /mission state files and emit a self-improvement prompt.

This script is intentionally read-only. It summarizes `.mission-state` sessions
across one or more project roots, deduplicates worktree archives, and prints a
Markdown report that can be handed back to `/mission` for self-improvement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
MISSION_LIB = REPO_ROOT / "skills" / "mission" / "lib"
if str(MISSION_LIB) not in sys.path:
    sys.path.insert(0, str(MISSION_LIB))

from mission_common import (  # noqa: E402
    HALT_CATEGORIES,
    PREPARATION_ONLY_MARKERS,
    SPECIALIST_SELECTION_CHECKPOINT_REQUIRED_AT,
    classify_state as classify,
    duration_sec,
    parse_iso_datetime,
    state_dedupe_rank,
    state_identity,
)
from specialist_accounting import (  # noqa: E402
    applied_specialist_invocation_skills,
    candidate_accounting_report,
    candidate_specialist_skills,
    explicitly_selected_specialist_skills,
    selected_specialist_skills,
    terminal_invoked_specialist_skills,
)
from activity_segments import summarize_activity_states  # noqa: E402


PRUNE_DIRS = {
    ".git",
    ".next",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
}

CORE_MISSION_INVOCATION_SKILLS = {
    "mission",
    "mission-core",
    "mission-planner",
    "mission-executor",
    "mission-reviewer",
    "mission-scorer",
    "mission-critic",
}

DEFAULT_ACTIVE_PENDING_SECONDS = 30 * 60
DEFAULT_STALE_ACTIVE_SECONDS = 3 * 60 * 60
WORKTREE_ARCHIVE_SCHEMA = "mission-worktree-archive/1"
WORKTREE_ARCHIVE_POINTER_SCHEMA = "mission-worktree-current/1"
_MANIFEST_VALIDATION_CACHE: dict[tuple[str, str, str, str], tuple[str, list[dict[str, Any]]]] = {}


@dataclass(frozen=True)
class StateRecord:
    path: Path
    state: dict[str, Any]
    archive_bundle: Path | None = None
    archive_root: Path | None = None
    archive_generation: str | None = None


@dataclass(frozen=True)
class StateCandidate:
    path: Path
    archive_bundle: Path | None = None
    archive_root: Path | None = None
    archive_generation: str | None = None
    state: dict[str, Any] | None = None


@dataclass(frozen=True)
class ArchiveResolution:
    status: str
    bundle: Path
    root: Path
    generation: str | None = None
    reason: str | None = None


def _worktree_archive_root(record: StateRecord) -> Path | None:
    if record.archive_bundle is not None:
        return record.archive_bundle
    parts = record.path.parts
    if ".mission-state" not in parts:
        return None
    index = parts.index(".mission-state")
    if index + 2 >= len(parts):
        return None
    if parts[index + 1] != "archive" or not parts[index + 2].startswith("worktree-"):
        return None
    return Path(*parts[: index + 3])


def _manifest_relative_path(value: Any, *, state_reference: bool = False) -> Path | None:
    if not isinstance(value, str) or not value or "://" in value:
        return None
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        return None
    if state_reference and (not path.parts or path.parts[0] != ".mission-state"):
        return None
    return path


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_worktree_archive(bundle: Path) -> ArchiveResolution:
    try:
        bundle_stat = bundle.lstat()
    except FileNotFoundError:
        return ArchiveResolution("invalid", bundle, bundle, reason="bundle-not-regular-directory")
    except OSError:
        return ArchiveResolution("invalid", bundle, bundle, reason="bundle-access-error")
    if stat.S_ISLNK(bundle_stat.st_mode) or not stat.S_ISDIR(bundle_stat.st_mode):
        return ArchiveResolution("invalid", bundle, bundle, reason="bundle-not-regular-directory")
    pointer_path = bundle / "current.json"
    try:
        pointer_stat = pointer_path.lstat()
    except FileNotFoundError:
        return ArchiveResolution("legacy", bundle, bundle)
    except OSError:
        return ArchiveResolution("invalid", bundle, bundle, reason="pointer-access-error")
    if stat.S_ISLNK(pointer_stat.st_mode) or not stat.S_ISREG(pointer_stat.st_mode):
        return ArchiveResolution("invalid", bundle, bundle, reason="pointer-not-regular-file")
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except OSError:
        return ArchiveResolution("invalid", bundle, bundle, reason="pointer-access-error")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ArchiveResolution("invalid", bundle, bundle, reason="pointer-invalid-json")
    generation = pointer.get("generation") if isinstance(pointer, dict) else None
    if (
        not isinstance(pointer, dict)
        or pointer.get("schema") != WORKTREE_ARCHIVE_POINTER_SCHEMA
        or not isinstance(generation, str)
        or not generation
        or generation in {".", ".."}
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in generation)
    ):
        return ArchiveResolution("invalid", bundle, bundle, reason="pointer-invalid-schema-or-generation")
    generations_root = bundle / "generations"
    try:
        generations_stat = generations_root.lstat()
    except FileNotFoundError:
        return ArchiveResolution(
            "invalid",
            bundle,
            generations_root,
            generation=generation,
            reason="generations-not-regular-directory",
        )
    except OSError:
        return ArchiveResolution(
            "invalid",
            bundle,
            generations_root,
            generation=generation,
            reason="generations-access-error",
        )
    if stat.S_ISLNK(generations_stat.st_mode) or not stat.S_ISDIR(generations_stat.st_mode):
        return ArchiveResolution(
            "invalid",
            bundle,
            generations_root,
            generation=generation,
            reason="generations-not-regular-directory",
        )
    generation_root = generations_root / generation
    try:
        generation_stat = generation_root.lstat()
    except FileNotFoundError:
        return ArchiveResolution(
            "invalid",
            bundle,
            generation_root,
            generation=generation,
            reason="generation-missing-or-not-directory",
        )
    except OSError:
        return ArchiveResolution(
            "invalid",
            bundle,
            generation_root,
            generation=generation,
            reason="generation-access-error",
        )
    if stat.S_ISLNK(generation_stat.st_mode) or not stat.S_ISDIR(generation_stat.st_mode):
        return ArchiveResolution(
            "invalid",
            bundle,
            generation_root,
            generation=generation,
            reason="generation-missing-or-not-directory",
        )
    return ArchiveResolution("generation", bundle, generation_root, generation=generation)


def _manifest_root_for_record(record: StateRecord, bundle: Path) -> tuple[str, Path]:
    if record.archive_root is not None:
        try:
            record.path.relative_to(record.archive_root)
        except ValueError:
            return "invalid", record.archive_root
        return record.archive_generation or "legacy", record.archive_root
    resolution = _resolve_worktree_archive(bundle)
    if resolution.status == "invalid":
        return "invalid", resolution.root
    try:
        record.path.relative_to(resolution.root)
    except ValueError:
        return "invalid", resolution.root
    return resolution.generation or "legacy", resolution.root


def _normalized_state_reference(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if ".mission-state" not in path.parts:
        return None
    index = path.parts.index(".mission-state")
    return Path(*path.parts[index:]).as_posix()


def _expected_manifest_lineage(record: StateRecord) -> Counter[tuple[str, int, str]] | None:
    state = record.state
    iteration = state.get("iteration")
    if not isinstance(iteration, int) or isinstance(iteration, bool):
        return None
    expected: Counter[tuple[str, int, str]] = Counter()

    def add(kind: str, item_iteration: Any, reference: Any) -> bool:
        normalized = _normalized_state_reference(reference)
        if (
            not isinstance(item_iteration, int)
            or isinstance(item_iteration, bool)
            or normalized is None
        ):
            return False
        expected[(kind, item_iteration, normalized)] += 1
        return True

    state_reference = f".mission-state/sessions/{record.path.name}"
    if not add("state", iteration, state_reference):
        return None
    if state.get("assumptions_path") and not add("assumptions", iteration, state.get("assumptions_path")):
        return None
    artifact = state.get("artifact") if isinstance(state.get("artifact"), dict) else {}
    if artifact.get("path") and not add("artifact", iteration, artifact.get("path")):
        return None
    for entry in state.get("score_history") or []:
        if not isinstance(entry, dict):
            continue
        entry_iteration = entry.get("iteration")
        if entry.get("scoring_evidence_path") and not add(
            "scoring", entry_iteration, entry.get("scoring_evidence_path")
        ):
            return None
        if entry.get("findings_evidence_path") and not add(
            "reviews", entry_iteration, entry.get("findings_evidence_path")
        ):
            return None
    for invocation in state.get("specialist_invocations") or []:
        if not isinstance(invocation, dict) or not invocation.get("evidence_path"):
            continue
        item_iteration = invocation.get("iteration")
        if not isinstance(item_iteration, int) or isinstance(item_iteration, bool) or item_iteration < 0:
            item_iteration = iteration
        if not add("specialist", item_iteration, invocation.get("evidence_path")):
            return None
    progress = state.get("progress") if isinstance(state.get("progress"), dict) else {}
    if progress.get("evidence_path") and not add("progress", iteration, progress.get("evidence_path")):
        return None
    if progress.get("artifact_path") and not add(
        "progress-artifact", iteration, progress.get("artifact_path")
    ):
        return None
    return expected


def _validate_worktree_manifest_uncached(record: StateRecord) -> tuple[str, list[dict[str, Any]]]:
    """Return legacy/valid/invalid and integrity-checked evidence entries for one bundle."""
    bundle = _worktree_archive_root(record)
    if bundle is None:
        return "legacy", []
    archive_style, manifest_root = _manifest_root_for_record(record, bundle)
    if archive_style == "invalid":
        return "invalid", []
    manifest_path = manifest_root / "manifest.json"
    if not manifest_path.exists() and not manifest_path.is_symlink():
        return ("legacy", []) if archive_style == "legacy" else ("invalid", [])
    if manifest_path.is_symlink() or not manifest_path.is_file():
        return "invalid", []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "invalid", []
    if not isinstance(manifest, dict) or manifest.get("schema") != WORKTREE_ARCHIVE_SCHEMA:
        return "invalid", []
    state = record.state
    if (
        manifest.get("session_id") != state.get("session_id")
        or manifest.get("mission_id") != state.get("mission_id")
        or manifest.get("iteration") != state.get("iteration")
    ):
        return "invalid", []
    evidence = manifest.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return "invalid", []
    core = {
        "schema": manifest["schema"],
        "session_id": manifest["session_id"],
        "mission_id": manifest["mission_id"],
        "iteration": manifest["iteration"],
        "evidence": evidence,
    }
    expected_digest = hashlib.sha256(
        json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if manifest.get("content_digest") != expected_digest:
        return "invalid", []
    if archive_style != "legacy" and manifest.get("content_digest") != archive_style:
        return "invalid", []

    allowed_iterations = {state.get("iteration")}
    allowed_iterations.update(
        entry.get("iteration")
        for entry in state.get("score_history") or []
        if isinstance(entry, dict)
    )
    allowed_iterations.update(
        entry.get("iteration")
        for entry in state.get("specialist_invocations") or []
        if isinstance(entry, dict)
    )
    seen_paths: set[str] = set()
    checked: list[dict[str, Any]] = []
    for item in evidence:
        if not isinstance(item, dict):
            return "invalid", []
        if (
            item.get("session_id") != state.get("session_id")
            or item.get("mission_id") != state.get("mission_id")
            or item.get("iteration") not in allowed_iterations
            or not isinstance(item.get("evidence_kind"), str)
        ):
            return "invalid", []
        source_reference = _manifest_relative_path(item.get("source_reference"), state_reference=True)
        archive_path = _manifest_relative_path(item.get("archive_path"))
        if source_reference is None or archive_path is None or item["archive_path"] in seen_paths:
            return "invalid", []
        seen_paths.add(item["archive_path"])
        archived = manifest_root / archive_path
        current = manifest_root
        for part in archive_path.parts:
            current = current / part
            if current.is_symlink():
                return "invalid", []
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
            return "invalid", []
        try:
            if archived.stat().st_size != expected_size or _file_sha256(archived) != expected_hash:
                return "invalid", []
        except OSError:
            return "invalid", []
        checked.append({**item, "path": archived})

    expected_lineage = _expected_manifest_lineage(record)
    actual_lineage = Counter(
        (item["evidence_kind"], item["iteration"], item["source_reference"])
        for item in evidence
    )
    if expected_lineage is None or actual_lineage != expected_lineage:
        return "invalid", []
    state_entries = [item for item in checked if item["evidence_kind"] == "state"]
    try:
        state_archive_path = record.path.relative_to(manifest_root).as_posix()
    except ValueError:
        return "invalid", []
    if len(state_entries) != 1 or state_entries[0]["archive_path"] != state_archive_path:
        return "invalid", []
    return "valid", checked


def _validated_worktree_manifest(record: StateRecord) -> tuple[str, list[dict[str, Any]]]:
    cache_key = (
        str(record.path),
        str(record.archive_root or ""),
        record.archive_generation or "legacy",
        json.dumps(record.state, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
    if cache_key not in _MANIFEST_VALIDATION_CACHE:
        _MANIFEST_VALIDATION_CACHE[cache_key] = _validate_worktree_manifest_uncached(record)
    return _MANIFEST_VALIDATION_CACHE[cache_key]


def parse_dt(value: str | None) -> datetime | None:
    return parse_iso_datetime(value)


def utc_now() -> datetime:
    override = os.environ.get("MISSION_AUDIT_NOW")
    parsed = parse_dt(override)
    if parsed:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def parse_cutoff(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if "T" not in text and len(text) == 10:
        text = f"{text}T00:00:00+00:00"
    parsed = parse_dt(text)
    if parsed and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) if parsed else None


def parse_period_cutoff(value: str | None, *, upper: bool = False) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if "T" not in text and len(text) == 10:
        suffix = "T23:59:59.999999+00:00" if upper else "T00:00:00+00:00"
        text = f"{text}{suffix}"
    parsed = parse_dt(text)
    if parsed and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) if parsed else None


def age_since_update_sec(state: dict[str, Any], *, now: datetime | None = None) -> float | None:
    updated = parse_dt(state.get("heartbeat_at") or state.get("last_progress_at") or state.get("updated_at"))
    if not updated:
        return None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    base = now or utc_now()
    seconds = (base - updated.astimezone(timezone.utc)).total_seconds()
    return seconds if seconds >= 0 else None


def latest_scored_entry(state: dict[str, Any]) -> dict[str, Any] | None:
    for entry in reversed(state.get("score_history") or []):
        composite = entry.get("composite")
        if isinstance(composite, (int, float)) and not isinstance(composite, bool) and not math.isnan(composite):
            return entry
    return None


def project_name(state: dict[str, Any]) -> str:
    root = str(state.get("project_root") or "").rstrip("/")
    return Path(root).name if root else "unknown"


def project_root_for(record: StateRecord) -> str:
    root = str(record.state.get("project_root") or "").rstrip("/")
    if root:
        return root
    parts = record.path.parts
    if ".mission-state" in parts:
        idx = parts.index(".mission-state")
        if idx > 0:
            return str(Path(*parts[:idx]))
    return str(record.path.parent)


def _invalid_archive_item(resolution: ArchiveResolution, reason: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {
        "bundle_path": str(resolution.bundle),
        "reason": reason or resolution.reason or "archive-integrity-invalid",
    }
    if resolution.generation:
        item["generation"] = resolution.generation
    return item


def _append_invalid_archive_root(
    invalid_archives: list[dict[str, Any]] | None,
    path: Path,
    reason: str,
) -> None:
    if invalid_archives is None:
        return
    resolution = ArchiveResolution("invalid", path, path, reason=reason)
    invalid_archives.append(_invalid_archive_item(resolution))


def _preflight_generation_state(
    resolution: ArchiveResolution,
) -> tuple[StateCandidate | None, str | None]:
    manifest_path = resolution.root / "manifest.json"
    try:
        manifest_stat = manifest_path.lstat()
    except FileNotFoundError:
        return None, "manifest-missing-or-not-regular-file"
    except OSError:
        return None, "manifest-access-error"
    if stat.S_ISLNK(manifest_stat.st_mode) or not stat.S_ISREG(manifest_stat.st_mode):
        return None, "manifest-missing-or-not-regular-file"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError:
        return None, "manifest-access-error"
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "manifest-invalid-json"
    evidence = manifest.get("evidence") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema") != WORKTREE_ARCHIVE_SCHEMA
        or not isinstance(evidence, list)
    ):
        return None, "manifest-invalid-schema"
    state_items = [
        item for item in evidence
        if isinstance(item, dict) and item.get("evidence_kind") == "state"
    ]
    if len(state_items) != 1:
        return None, "manifest-state-entry-invalid"
    state_relative = _manifest_relative_path(state_items[0].get("archive_path"))
    if state_relative is None or not state_relative.parts:
        return None, "manifest-state-path-invalid"
    state_path = resolution.root / state_relative
    current = resolution.root
    for part in state_relative.parts:
        current = current / part
        try:
            current_stat = current.lstat()
        except FileNotFoundError:
            return None, "manifest-state-missing"
        except OSError:
            return None, "manifest-state-access-error"
        if stat.S_ISLNK(current_stat.st_mode):
            return None, "manifest-state-path-symlink"
    if not stat.S_ISREG(current_stat.st_mode):
        return None, "manifest-state-missing"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except OSError:
        return None, "manifest-state-access-error"
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "manifest-state-invalid-json"
    if not is_mission_state(state):
        return None, "manifest-state-invalid-schema"
    record = StateRecord(
        path=state_path,
        state=state,
        archive_bundle=resolution.bundle,
        archive_root=resolution.root,
        archive_generation=resolution.generation,
    )
    status, _evidence = _validated_worktree_manifest(record)
    if status != "valid":
        return None, "manifest-or-generation-integrity-invalid"
    return (
        StateCandidate(
            path=state_path,
            archive_bundle=resolution.bundle,
            archive_root=resolution.root,
            archive_generation=resolution.generation,
            state=state,
        ),
        None,
    )


def _iter_state_candidates(
    root: Path, invalid_archives: list[dict[str, Any]] | None = None
):
    root = root.expanduser()
    if not root.exists():
        return

    def record_walk_error(error: OSError) -> None:
        error_path_value = getattr(error, "filename", None)
        if not error_path_value:
            return
        error_path = Path(error_path_value)
        if ".mission-state" not in error_path.parts:
            return
        state_index = error_path.parts.index(".mission-state")
        state_root = Path(*error_path.parts[: state_index + 1])
        _append_invalid_archive_root(
            invalid_archives,
            state_root,
            "mission-state-root-access-error",
        )

    for dirpath, dirnames, filenames in os.walk(
        root,
        followlinks=False,
        onerror=record_walk_error,
    ):
        current_dir = Path(dirpath)
        excluded_state_roots: set[Path] = set()
        for dirname in dirnames:
            if dirname != ".mission-state":
                continue
            state_root = current_dir / dirname
            try:
                state_root_stat = state_root.lstat()
            except OSError:
                reason = "mission-state-root-access-error"
            else:
                if stat.S_ISLNK(state_root_stat.st_mode):
                    reason = "mission-state-root-symlink"
                elif not stat.S_ISDIR(state_root_stat.st_mode):
                    reason = "mission-state-root-not-directory"
                else:
                    try:
                        with os.scandir(state_root):
                            pass
                    except OSError:
                        reason = "mission-state-root-access-error"
                    else:
                        continue
            excluded_state_roots.add(state_root)
            _append_invalid_archive_root(invalid_archives, state_root, reason)
        for filename in filenames:
            if filename != ".mission-state":
                continue
            non_directory_state_root = current_dir / filename
            try:
                state_root_stat = non_directory_state_root.lstat()
            except OSError:
                reason = "mission-state-root-access-error"
            else:
                reason = (
                    "mission-state-root-symlink"
                    if stat.S_ISLNK(state_root_stat.st_mode)
                    else "mission-state-root-not-directory"
                )
            _append_invalid_archive_root(
                invalid_archives,
                non_directory_state_root,
                reason,
            )
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname not in PRUNE_DIRS and current_dir / dirname not in excluded_state_roots
        ]
        if os.path.basename(dirpath) != ".mission-state":
            continue
        dirnames[:] = []
        mission_state = Path(dirpath)
        if mission_state.is_symlink():
            _append_invalid_archive_root(
                invalid_archives,
                mission_state,
                "mission-state-root-symlink",
            )
            continue
        candidates: list[StateCandidate] = []
        sessions = mission_state / "sessions"
        if sessions.is_dir():
            candidates.extend(StateCandidate(path) for path in sorted(sessions.glob("*.json")))
        archive = mission_state / "archive"
        archive_entries: list[os.DirEntry[str]] = []
        archive_root: Path | None = None
        try:
            archive_stat = archive.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            _append_invalid_archive_root(invalid_archives, archive, "archive-root-access-error")
        else:
            if stat.S_ISLNK(archive_stat.st_mode):
                _append_invalid_archive_root(invalid_archives, archive, "archive-root-symlink")
            elif stat.S_ISDIR(archive_stat.st_mode):
                try:
                    archive_root = archive.resolve(strict=True)
                    with os.scandir(archive) as entries:
                        archive_entries = list(entries)
                except (OSError, RuntimeError):
                    archive_root = None
                    _append_invalid_archive_root(
                        invalid_archives,
                        archive,
                        "archive-root-access-error",
                    )
            else:
                _append_invalid_archive_root(
                    invalid_archives,
                    archive,
                    "archive-root-not-directory",
                )
        if archive_root is not None:
            candidates.extend(
                StateCandidate(archive / entry.name)
                for entry in sorted(archive_entries, key=lambda item: item.name)
                if entry.name.startswith("state-") and entry.name.endswith(".json")
            )
            worktree_dirs = [
                archive / entry.name
                for entry in sorted(archive_entries, key=lambda item: item.name)
                if entry.name.startswith("worktree-")
            ]
            for worktree_dir in worktree_dirs:
                resolution = _resolve_worktree_archive(worktree_dir)
                if resolution.status == "invalid":
                    if invalid_archives is not None:
                        invalid_archives.append(_invalid_archive_item(resolution))
                    continue
                try:
                    bundle_root = worktree_dir.resolve(strict=True)
                except (OSError, RuntimeError):
                    bundle_root = None
                if bundle_root is None or not bundle_root.is_relative_to(archive_root):
                    _append_invalid_archive_root(
                        invalid_archives,
                        worktree_dir,
                        "bundle-outside-archive-root",
                    )
                    continue
                if resolution.status == "generation":
                    candidate, reason = _preflight_generation_state(resolution)
                    if candidate is None:
                        if invalid_archives is not None:
                            invalid_resolution = ArchiveResolution(
                                "invalid",
                                resolution.bundle,
                                resolution.root,
                                generation=resolution.generation,
                                reason=reason,
                            )
                            invalid_archives.append(_invalid_archive_item(invalid_resolution))
                        continue
                    candidates.append(candidate)
                    continue
                active_root = resolution.root
                snapshot = {
                    "archive_bundle": worktree_dir,
                    "archive_root": active_root,
                    "archive_generation": resolution.generation,
                }
                candidates.extend(
                    StateCandidate(path, **snapshot) for path in sorted(active_root.glob("*.json"))
                )
                worktree_sessions = active_root / "sessions"
                if worktree_sessions.is_dir():
                    candidates.extend(
                        StateCandidate(path, **snapshot)
                        for path in sorted(worktree_sessions.glob("*.json"))
                    )
        seen: set[Path] = set()
        for candidate in candidates:
            path = candidate.path
            if path in seen or path.name.endswith(".bak"):
                continue
            seen.add(path)
            yield candidate


def iter_state_files(root: Path):
    """Yield state paths while preserving the pre-manifest iterator contract."""
    for candidate in _iter_state_candidates(root) or []:
        yield candidate.path


def load_records(
    roots: list[Path], invalid_archives: list[dict[str, Any]] | None = None
) -> list[StateRecord]:
    records: list[StateRecord] = []
    for root in roots:
        for candidate in _iter_state_candidates(root, invalid_archives) or []:
            path = candidate.path
            state = candidate.state
            if state is None:
                try:
                    state = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
            if not is_mission_state(state):
                continue
            records.append(
                StateRecord(
                    path=path,
                    state=state,
                    archive_bundle=candidate.archive_bundle,
                    archive_root=candidate.archive_root,
                    archive_generation=candidate.archive_generation,
                )
            )
    return records


def collect_invalid_worktree_archives(
    records: list[StateRecord], discovered: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    invalid: list[dict[str, Any]] = []
    seen: set[str] = set()

    def canonical_bundle_key(value: str) -> str:
        try:
            return str(Path(value).resolve(strict=False))
        except (OSError, RuntimeError):
            return str(Path(value).absolute())

    for item in discovered:
        key = canonical_bundle_key(item["bundle_path"])
        if key in seen:
            continue
        invalid.append(item)
        seen.add(key)
    for record in records:
        if record.archive_bundle is None:
            continue
        bundle_path = str(record.archive_bundle)
        bundle_key = canonical_bundle_key(bundle_path)
        if bundle_key in seen:
            continue
        status, _evidence = _validated_worktree_manifest(record)
        if status != "invalid":
            continue
        resolution = ArchiveResolution(
            "invalid",
            record.archive_bundle,
            record.archive_root or record.archive_bundle,
            generation=record.archive_generation,
            reason="manifest-or-generation-integrity-invalid",
        )
        invalid.append(_invalid_archive_item(resolution))
        seen.add(bundle_key)
    return invalid


def is_mission_state(state: Any) -> bool:
    if not isinstance(state, dict):
        return False
    return bool(state.get("mission") and state.get("mission_id") and state.get("session_id"))


def dedupe_records(records: list[StateRecord]) -> tuple[list[StateRecord], list[list[StateRecord]], int]:
    groups: dict[tuple[str, str, str], list[StateRecord]] = {}
    for record in records:
        state = record.state
        key = state_identity(state, record.path.stem, str(record.path))
        groups.setdefault(key, []).append(record)

    deduped: list[StateRecord] = []
    duplicates: list[list[StateRecord]] = []
    resolved_duplicate_count = 0
    for group in groups.values():
        if len(group) > 1 and is_resolved_archive_duplicate(group):
            resolved_duplicate_count += 1
        elif len(group) > 1 and is_resolved_by_precedence(group):
            resolved_duplicate_count += 1
        elif len(group) > 1:
            duplicates.append(group)
        deduped.append(sorted(group, key=dedupe_rank)[0])
    return deduped, duplicates, resolved_duplicate_count


def is_resolved_archive_duplicate(group: list[StateRecord]) -> bool:
    """Return true for the expected session + archived worktree copy pattern."""
    if len(group) < 2:
        return False
    ranks = {record_rank(record)[0] for record in group}
    fingerprints = {json.dumps(record.state, sort_keys=True, ensure_ascii=False) for record in group}
    if len(fingerprints) != 1:
        return False
    if 0 in ranks and 1 in ranks:
        return True
    return all("/archive/" in str(record.path) for record in group)


def record_rank(record: StateRecord) -> tuple[int, str]:
    path_text = str(record.path)
    if "/archive/worktree-" in path_text:
        rank = 1
    elif "/sessions/" in path_text:
        rank = 0
    else:
        rank = 2
    return (rank, path_text)


def dedupe_status_rank(record: StateRecord) -> int:
    if classify(record.state) == "pass" or record.state.get("phase") == "done":
        return 0
    if classify(record.state) == "halt":
        return 1
    if classify(record.state) == "incomplete":
        return 2
    return 3


def updated_timestamp_rank(record: StateRecord) -> float:
    updated = parse_dt(record.state.get("updated_at"))
    if not updated:
        return 0
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return updated.timestamp()


def is_current_record(record: StateRecord, current_since: datetime | None) -> bool:
    if current_since is None:
        return True
    updated = parse_dt(record.state.get("updated_at"))
    if not updated:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return updated.astimezone(timezone.utc) >= current_since


def is_current_item(item: dict[str, Any], current_since: datetime | None) -> bool:
    if current_since is None:
        return True
    updated = parse_dt(str(item.get("updated_at") or ""))
    if not updated:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return updated.astimezone(timezone.utc) >= current_since


def split_current_historical(
    items: list[dict[str, Any]],
    current_since: datetime | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if current_since is None:
        return items, []
    current: list[dict[str, Any]] = []
    historical: list[dict[str, Any]] = []
    for item in items:
        if is_current_item(item, current_since):
            current.append(item)
        else:
            historical.append(item)
    return current, historical


def split_current_historical_records(
    records: list[StateRecord],
    current_since: datetime | None,
) -> tuple[list[StateRecord], list[StateRecord]]:
    if current_since is None:
        return records, []
    current: list[StateRecord] = []
    historical: list[StateRecord] = []
    for record in records:
        if is_current_record(record, current_since):
            current.append(record)
        else:
            historical.append(record)
    return current, historical


def dedupe_rank(record: StateRecord) -> tuple[int, float, int, str]:
    return state_dedupe_rank(record.state, str(record.path))


def is_resolved_by_precedence(group: list[StateRecord]) -> bool:
    """Return true when a terminal success supersedes stale/incomplete copies."""
    if len(group) < 2:
        return False
    ranks = {dedupe_status_rank(record) for record in group}
    return 0 in ranks and len(ranks) > 1


def commit_datetime(repo: Path, commit: str) -> datetime:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", "-s", "--format=%cI", commit],
        capture_output=True,
        text=True,
        check=True,
    )
    value = result.stdout.strip()
    parsed = parse_dt(value)
    if not parsed:
        raise ValueError(f"Could not parse commit date for {commit}: {value}")
    return parsed.astimezone(timezone.utc)


def filter_records(
    records: list[StateRecord],
    since: datetime | None,
    until: datetime | None,
    after: datetime | None,
) -> list[StateRecord]:
    out: list[StateRecord] = []
    for record in records:
        updated_dt = parse_dt(record.state.get("updated_at"))
        if updated_dt and updated_dt.tzinfo is None:
            updated_dt = updated_dt.replace(tzinfo=timezone.utc)
        if updated_dt:
            updated_dt = updated_dt.astimezone(timezone.utc)
        if since and updated_dt and updated_dt < since:
            continue
        if until and updated_dt and updated_dt > until:
            continue
        if after:
            if not updated_dt or updated_dt.astimezone(timezone.utc) <= after:
                continue
        out.append(record)
    return out


def bucket_counts(records: list[StateRecord], bucket_fn) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        bucket = bucket_fn(record)
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def halt_or_incomplete_bucket(record: StateRecord) -> str:
    state = record.state
    # #190: 構造化 halt_category があればそれを正とする (自由文ヒューリスティックより信頼できる)。
    # incomplete (未 halt) には適用しない -- halt_category は halt 発生時のみ記録される。
    if classify(state) == "halt":
        category = state.get("halt_category")
        if isinstance(category, str) and category in HALT_CATEGORIES:
            return f"structured:{category}"
    reason = str(state.get("halt_reason") or "").lower()
    if classify(state) == "incomplete":
        if not state.get("score_history"):
            if is_stale_active_no_score(record):
                return "stale-active-live-pid"
            return "active-no-score-checkpoint"
        return "active-in-progress"
    if any(token in reason for token in ("orphan", "pid", "dead", "stale")):
        return "stale-state-cleanup"
    if "max-iter" in reason or "max iter" in reason:
        return "max-iter"
    if any(token in reason for token in ("user", "confirm", "captcha", "manual", "approval")):
        return "human-blocker"
    if any(token in reason for token in ("api", "auth", "key", "permission", "external")):
        return "external-blocker"
    if not state.get("score_history"):
        return "halted-before-score"
    latest = latest_scored_entry(state)
    if latest and latest.get("composite", 0) < state.get("threshold", 4.0):
        return "below-threshold"
    return "other-halt"


# #185: force_reason 内でユーザー承認への言及を示すキーワード。あくまで自由文ヒューリスティックの
# fallback であり (LLM が偽の言及を書ける以上、確実な検証手段ではない)、正の証跡は
# force_approved_by_user フラグ (state 側で --approved-by-user 必須化) が担う。
_USER_APPROVAL_MENTION_TOKENS = (
    "user", "ユーザー", "approved", "承認", "instructed", "指示", "confirm", "explicit",
)


def is_forced_pass_autonomous_suspect(state: dict[str, Any]) -> bool:
    """#185: force_approved_by_user が記録されておらず (旧形式)、かつ force_reason にも
    ユーザー承認への言及がない forced-pass を「自律 force の疑いあり」として検出する。"""
    if state.get("force_approved_by_user") is True:
        return False
    reason = str(state.get("force_reason") or "").lower()
    if any(token in reason for token in _USER_APPROVAL_MENTION_TOKENS):
        return False
    return True


def is_active_no_score_checkpoint(record: StateRecord) -> bool:
    """Return true for live mission sessions that have not reached first scoring."""
    return classify(record.state) == "incomplete" and not record.state.get("score_history")


def active_pending_threshold_sec() -> int:
    return _env_int("MISSION_AUDIT_ACTIVE_PENDING_SECONDS", DEFAULT_ACTIVE_PENDING_SECONDS)


def stale_active_threshold_sec() -> int:
    return _env_int("MISSION_STALE_ACTIVE_SECONDS", DEFAULT_STALE_ACTIVE_SECONDS)


def is_active_no_score_pending(record: StateRecord, *, now: datetime | None = None) -> bool:
    if not is_active_no_score_checkpoint(record):
        return False
    age = age_since_update_sec(record.state, now=now)
    return age is not None and age < active_pending_threshold_sec()


def is_stale_active_no_score(record: StateRecord, *, now: datetime | None = None) -> bool:
    if not is_active_no_score_checkpoint(record):
        return False
    age = age_since_update_sec(record.state, now=now)
    return age is None or age >= stale_active_threshold_sec()


def slow_session_bucket(record: StateRecord, slow_threshold_sec: int) -> str:
    state = record.state
    cls = classify(state)
    duration = duration_sec(state) or 0
    if cls != "pass":
        return f"{cls}-slow"
    if duration >= slow_threshold_sec * 4:
        return "extreme-long-pass"
    if state.get("iteration", 0) >= 2 or state.get("complexity") in {"Complex", "Critical"}:
        return "expected-complex-or-multi-iter"
    latest = latest_scored_entry(state) or {}
    if latest.get("composite", 0) >= 4.3:
        return "healthy-long-pass"
    return "needs-review"


def valid_phase_durations(state: dict[str, Any]) -> dict[str, float]:
    durations = state.get("phase_durations_sec")
    if not isinstance(durations, dict):
        return {}
    out: dict[str, float] = {}
    for phase, seconds in durations.items():
        if not isinstance(phase, str):
            continue
        if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and not math.isnan(seconds) and seconds >= 0:
            out[phase] = float(seconds)
    return out


def slow_phase_observability_bucket(record: StateRecord) -> str:
    if classify(record.state) != "pass":
        return f"{classify(record.state)}-phase-durations-untracked"
    if coarse_phase_attribution_item(record) is not None:
        return "slow-with-coarse-phase-attribution"
    if valid_phase_durations(record.state):
        return "slow-with-phase-durations"
    return "slow-without-phase-durations"


def bucket_count_keys(keys: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for key in keys:
        counts[key] = counts.get(key, 0) + 1
    return counts


def low_score_pass_bucket(record: StateRecord) -> str:
    state = record.state
    latest = latest_scored_entry(state) or {}
    if latest.get("open_high", 0):
        return "risky-open-high"
    if latest.get("min_item", 0) < 3.5:
        return "risky-min-item-below-gate"
    if latest.get("composite", 0) < state.get("threshold", 4.0):
        return "risky-composite-below-threshold"
    return "valid-threshold-pass"


SPECIALIST_SELECTION_CHECKPOINT_COMPLEXITIES = {"Standard", "Complex", "Critical"}


def specialist_selection_checkpoint_expected(state: dict[str, Any]) -> bool:
    if str(state.get("complexity") or "") not in SPECIALIST_SELECTION_CHECKPOINT_COMPLEXITIES:
        return False
    started = parse_dt(state.get("created_at_session") or state.get("started_at"))
    if not started:
        return False
    return started.astimezone(timezone.utc) >= SPECIALIST_SELECTION_CHECKPOINT_REQUIRED_AT


def has_specialist_selection_checkpoint(state: dict[str, Any]) -> bool:
    task_profile = state.get("task_profile")
    decision = state.get("specialists_decision")
    return (
        isinstance(task_profile, dict)
        and bool(task_profile.get("primary"))
        and isinstance(decision, dict)
        and bool(decision.get("policy"))
    )


def missing_specialist_selection_checkpoint_item(record: StateRecord) -> dict[str, Any] | None:
    state = record.state
    if is_active_no_score_pending(record):
        return None
    if not specialist_selection_checkpoint_expected(state):
        return None
    if has_specialist_selection_checkpoint(state):
        return None
    return {
        "project": project_name(state),
        "project_root": project_root_for(record),
        "session_id": state.get("session_id") or record.path.stem,
        "mission_id": state.get("mission_id") or "",
        "path": str(record.path),
        "started_at": state.get("created_at_session") or state.get("started_at") or "",
        "updated_at": state.get("updated_at") or "",
    }


def specialist_invocation_gap_skills(record: StateRecord) -> list[str]:
    if is_active_no_score_pending(record):
        return []
    selected = explicitly_selected_specialist_skills(record.state)
    if not selected:
        return []
    invoked = terminal_invoked_specialist_skills(record.state)
    return sorted(selected - invoked)


def unselected_specialist_invocation_skills(record: StateRecord) -> list[str]:
    selected = selected_specialist_skills(record.state)
    applied = applied_specialist_invocation_skills(record.state) - CORE_MISSION_INVOCATION_SKILLS
    unresolved_confirm = set(unresolved_confirm_specialist_selection_skills(record))
    applied -= unresolved_confirm
    return sorted(applied - selected)


def unselected_specialist_invocation_item(record: StateRecord) -> dict[str, Any] | None:
    skills = unselected_specialist_invocation_skills(record)
    if not skills:
        return None
    return {
        "project": project_name(record.state),
        "project_root": project_root_for(record),
        "session_id": record.state.get("session_id") or record.path.stem,
        "mission_id": record.state.get("mission_id") or "",
        "path": str(record.path),
        "skills": skills,
        "updated_at": record.state.get("updated_at") or "",
    }


def unresolved_confirm_specialist_selection_skills(record: StateRecord) -> list[str]:
    state = record.state
    decision = state.get("specialists_decision")
    if not isinstance(decision, dict):
        return []
    if decision.get("action") != "ask-user" or decision.get("prompted_user") is not True:
        return []
    selected = selected_specialist_skills(state)
    applied = applied_specialist_invocation_skills(state) - CORE_MISSION_INVOCATION_SKILLS
    return sorted(applied - selected)


def unresolved_confirm_specialist_selection_item(record: StateRecord) -> dict[str, Any] | None:
    skills = unresolved_confirm_specialist_selection_skills(record)
    if not skills:
        return None
    return {
        "project": project_name(record.state),
        "project_root": project_root_for(record),
        "session_id": record.state.get("session_id") or record.path.stem,
        "mission_id": record.state.get("mission_id") or "",
        "path": str(record.path),
        "skills": skills,
        "updated_at": record.state.get("updated_at") or "",
    }


def is_active_ask_user_specialist_wait(state: dict[str, Any]) -> bool:
    decision = state.get("specialists_decision")
    return (
        state.get("loop_active") is True
        and not state.get("passes")
        and isinstance(decision, dict)
        and decision.get("action") == "ask-user"
        and decision.get("prompted_user") is True
    )


def candidate_only_specialist_item(record: StateRecord) -> dict[str, Any] | None:
    state = record.state
    if is_active_no_score_pending(record):
        return None
    if is_active_ask_user_specialist_wait(state):
        return None
    report = candidate_accounting_report(state)
    unaccounted = report["unaccounted_candidates"]
    if not unaccounted:
        return None
    complexity = str(state.get("complexity") or "Unknown")
    latest = latest_scored_entry(state) or {}
    return {
        "project": project_name(state),
        "project_root": project_root_for(record),
        "session_id": state.get("session_id") or record.path.stem,
        "mission_id": state.get("mission_id") or "",
        "path": str(record.path),
        "complexity": complexity,
        "score": latest.get("composite"),
        "candidate_count": len(unaccounted),
        "skills": [item["skill"] for item in unaccounted],
        "priority": report["priority"] or "P2",
        "updated_at": state.get("updated_at") or "",
    }


def coarse_phase_attribution_item(record: StateRecord, planning_ratio_threshold: float = 0.8) -> dict[str, Any] | None:
    state = record.state
    if classify(state) != "pass":
        return None
    durations = valid_phase_durations(state)
    if not durations:
        return None
    planning = durations.get("planning", 0.0)
    duration = duration_sec(state)
    duration_base = duration if duration and duration > 0 else sum(durations.values())
    if duration_base <= 0:
        return None
    non_planning_phases = [phase for phase, seconds in durations.items() if phase != "planning" and seconds > 0]
    if planning < duration_base * planning_ratio_threshold or len(non_planning_phases) >= 2:
        return None
    return {
        "project": project_name(state),
        "project_root": project_root_for(record),
        "session_id": state.get("session_id") or record.path.stem,
        "mission_id": state.get("mission_id") or "",
        "path": str(record.path),
        "planning_sec": planning,
        "duration_sec": duration_base,
        "planning_ratio": planning / duration_base,
        "phases": sorted(durations),
    }


def scoring_evidence_paths(record: StateRecord, iteration: int) -> list[Path]:
    manifest_status, manifest_evidence = _validated_worktree_manifest(record)
    if manifest_status == "invalid":
        return []
    if manifest_status == "valid":
        return [
            item["path"]
            for item in manifest_evidence
            if item.get("evidence_kind") == "scoring" and item.get("iteration") == iteration
        ]

    mission_id = str(record.state.get("mission_id") or "")
    mission_prefix = mission_id[:8] or "unknown"
    filenames = [
        f"iter-{iteration}-{mission_prefix}-scoring.md",
        f"iter-{iteration}-{mission_prefix}-scoring.json",
    ]
    project_mission_state = Path(project_root_for(record)) / ".mission-state"
    paths: list[Path] = []

    parts = record.path.parts
    mission_state_root: Path | None = None
    worktree_archive_root: Path | None = None
    if ".mission-state" in parts:
        idx = parts.index(".mission-state")
        mission_state_root = Path(*parts[: idx + 1])
        if idx + 2 < len(parts) and parts[idx + 1] == "archive" and parts[idx + 2].startswith("worktree-"):
            worktree_archive_root = Path(*parts[: idx + 3])

    for entry in record.state.get("score_history") or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("iteration") != iteration:
            continue
        evidence_path = str(entry.get("scoring_evidence_path") or "").strip()
        if not evidence_path:
            continue
        raw_path = Path(evidence_path)
        if raw_path.is_absolute():
            paths.append(raw_path)
        else:
            paths.append(Path(project_root_for(record)) / evidence_path)
            if mission_state_root:
                paths.append(mission_state_root.parent / evidence_path)
            if worktree_archive_root:
                paths.append(worktree_archive_root / evidence_path)

    for filename in filenames:
        paths.append(project_mission_state / "archive" / filename)
        paths.append(project_mission_state / "iteration-archive" / filename)
        if mission_state_root:
            paths.append(mission_state_root / "archive" / filename)
            paths.append(mission_state_root / "iteration-archive" / filename)
        if worktree_archive_root:
            paths.append(worktree_archive_root / "archive" / filename)
            paths.append(worktree_archive_root / "iteration-archive" / filename)
            paths.append(worktree_archive_root / "mission-archive" / filename)

    if worktree_archive_root:
        worktree_iteration_dir = worktree_archive_root / f"iter-{iteration}-{mission_prefix}"
        paths.append(worktree_iteration_dir / "scoring.json")
        paths.append(worktree_iteration_dir / "scoring.md")

    deduped: list[Path] = []
    for path in paths:
        if path not in deduped:
            deduped.append(path)
    return deduped


def specialist_evidence_paths(record: StateRecord, invocation: dict[str, Any]) -> list[Path]:
    manifest_status, manifest_evidence = _validated_worktree_manifest(record)
    if manifest_status == "invalid":
        return []
    if manifest_status == "valid":
        source_reference = _normalized_state_reference(invocation.get("evidence_path"))
        iteration = invocation.get("iteration")
        if not isinstance(iteration, int) or isinstance(iteration, bool) or iteration < 0:
            iteration = record.state.get("iteration")
        if source_reference is None or not isinstance(iteration, int) or isinstance(iteration, bool):
            return []
        return [
            item["path"]
            for item in manifest_evidence
            if item.get("evidence_kind") == "specialist"
            and item.get("iteration") == iteration
            and item.get("source_reference") == source_reference
        ]

    paths: list[Path] = []
    evidence_path = str(invocation.get("evidence_path") or "").strip()
    if evidence_path:
        raw_path = Path(evidence_path)
        if raw_path.is_absolute():
            paths.append(raw_path)
        else:
            paths.append(Path(project_root_for(record)) / raw_path)
            parts = record.path.parts
            if ".mission-state" in parts:
                idx = parts.index(".mission-state")
                mission_state_root = Path(*parts[: idx + 1])
                if evidence_path.startswith(".mission-state/"):
                    paths.append(mission_state_root.parent / evidence_path)

    iteration = invocation.get("iteration")
    skill = str(invocation.get("skill") or invocation.get("role") or "").strip()
    if isinstance(iteration, int) and not isinstance(iteration, bool) and iteration >= 0 and skill:
        mission_id = str(record.state.get("mission_id") or "")
        mission_prefix = mission_id[:8] or "unknown"
        filename = f"iter-{iteration}-{mission_prefix}-specialist-{skill}.md"
        paths.append(Path(project_root_for(record)) / ".mission-state" / "archive" / filename)
        parts = record.path.parts
        if ".mission-state" in parts:
            idx = parts.index(".mission-state")
            mission_state_root = Path(*parts[: idx + 1])
            paths.append(mission_state_root / "archive" / filename)
            if idx + 2 < len(parts) and parts[idx + 1] == "archive" and parts[idx + 2].startswith("worktree-"):
                paths.append(Path(*parts[: idx + 3]) / "archive" / filename)

    deduped: list[Path] = []
    for path in paths:
        if path not in deduped:
            deduped.append(path)
    return deduped


def preparation_only_completed_provider_item(record: StateRecord) -> dict[str, Any] | None:
    bad_entries: list[dict[str, Any]] = []
    for index, invocation in enumerate(record.state.get("specialist_invocations") or [], start=1):
        if not isinstance(invocation, dict):
            continue
        if invocation.get("mode") != "command-provider" or invocation.get("status") != "completed":
            continue
        evidence_hits: list[dict[str, Any]] = []
        for path in specialist_evidence_paths(record, invocation):
            if not path.exists() or not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            markers = [marker for marker in PREPARATION_ONLY_MARKERS if marker in text]
            if markers:
                evidence_hits.append({
                    "path": str(path),
                    "markers": markers[:3],
                })
        if evidence_hits:
            bad_entries.append({
                "index": index,
                "role": invocation.get("role") or "",
                "skill": invocation.get("skill") or "",
                "phase": invocation.get("phase") or "",
                "iteration": invocation.get("iteration"),
                "evidence": evidence_hits,
            })
    if not bad_entries:
        return None
    return {
        "project": project_name(record.state),
        "project_root": project_root_for(record),
        "session_id": record.state.get("session_id") or record.path.stem,
        "mission_id": record.state.get("mission_id") or "",
        "path": str(record.path),
        "bad_entries": bad_entries,
        "bad_count": len(bad_entries),
        "updated_at": record.state.get("updated_at") or "",
    }


def missing_scoring_evidence_iterations(record: StateRecord) -> list[int]:
    missing: list[int] = []
    for index, entry in enumerate(record.state.get("score_history") or [], start=1):
        if not isinstance(entry, dict):
            continue
        iteration = entry.get("iteration", index)
        if not isinstance(iteration, int) or isinstance(iteration, bool):
            continue
        if iteration < 1:
            continue
        if not any(path.exists() for path in scoring_evidence_paths(record, iteration)):
            missing.append(iteration)
    return missing


def missing_scoring_evidence_item(record: StateRecord) -> dict[str, Any] | None:
    missing_iterations = missing_scoring_evidence_iterations(record)
    if not missing_iterations:
        return None
    return {
        "project": project_name(record.state),
        "project_root": project_root_for(record),
        "session_id": record.state.get("session_id") or record.path.stem,
        "mission_id": record.state.get("mission_id") or "",
        "path": str(record.path),
        "missing_iterations": missing_iterations,
        "updated_at": record.state.get("updated_at") or "",
    }


def invalid_score_iteration_item(record: StateRecord) -> dict[str, Any] | None:
    invalid_iterations: list[Any] = []
    for index, entry in enumerate(record.state.get("score_history") or [], start=1):
        if not isinstance(entry, dict):
            continue
        iteration = entry.get("iteration", index)
        if (
            not isinstance(iteration, int)
            or isinstance(iteration, bool)
            or iteration < 1
        ):
            invalid_iterations.append(iteration)
    if not invalid_iterations:
        return None
    return {
        "project": project_name(record.state),
        "project_root": project_root_for(record),
        "session_id": record.state.get("session_id") or record.path.stem,
        "mission_id": record.state.get("mission_id") or "",
        "path": str(record.path),
        "invalid_iterations": invalid_iterations,
        "updated_at": record.state.get("updated_at") or "",
    }


def blank_specialist_invocation_item(record: StateRecord) -> dict[str, Any] | None:
    blank_entries: list[dict[str, Any]] = []
    for index, invocation in enumerate(record.state.get("specialist_invocations") or [], start=1):
        if not isinstance(invocation, dict):
            continue
        role = str(invocation.get("role") or "").strip()
        skill = str(invocation.get("skill") or "").strip()
        if not role or not skill:
            blank_entries.append({
                "index": index,
                "role": role,
                "skill": skill,
                "status": invocation.get("status") or "",
                "phase": invocation.get("phase") or "",
            })
    if not blank_entries:
        return None
    return {
        "project": project_name(record.state),
        "project_root": project_root_for(record),
        "session_id": record.state.get("session_id") or record.path.stem,
        "mission_id": record.state.get("mission_id") or "",
        "path": str(record.path),
        "blank_entries": blank_entries,
        "blank_count": len(blank_entries),
        "updated_at": record.state.get("updated_at") or "",
    }


def aggregate(
    records: list[StateRecord],
    duplicates: list[list[StateRecord]],
    resolved_duplicate_count: int,
    slow_threshold_sec: int,
    current_since: datetime | None = None,
    invalid_worktree_archives: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    invalid_worktree_archives = invalid_worktree_archives or []
    classes = [classify(r.state) for r in records]
    active_no_score_checkpoint = [r for r in records if is_active_no_score_checkpoint(r)]
    active_no_score_pending = [r for r in active_no_score_checkpoint if is_active_no_score_pending(r)]
    stale_active_no_score = [r for r in active_no_score_checkpoint if is_stale_active_no_score(r)]
    pass_rate_records = [r for r in records if not is_active_no_score_checkpoint(r)]
    durations = [d for r in records if (d := duration_sec(r.state)) is not None]
    composites = [
        entry["composite"]
        for r in records
        if (entry := latest_scored_entry(r.state)) is not None
    ]
    pass_count = classes.count("pass")
    pass_records = [r for r, cls in zip(records, classes) if cls == "pass"]
    forced = [r for r in records if r.state.get("passes") and r.state.get("passes_forced")]
    forced_autonomous_suspect = [r for r in forced if is_forced_pass_autonomous_suspect(r.state)]
    ungated = [
        r for r in records
        if r.state.get("passes")
        and latest_scored_entry(r.state) is None
        and not r.state.get("passes_forced")
        and not r.state.get("force_reason")
    ]
    slow = [
        r for r in records
        if (duration_sec(r.state) or 0) >= slow_threshold_sec
    ]
    low_score_pass = [
        r for r in pass_records
        if ((latest_scored_entry(r.state) or {}).get("composite") or 0) < 4.3
    ]
    specialist_invocation_gaps = [
        r for r in records
        if specialist_invocation_gap_skills(r)
    ]
    unselected_specialist_invocations = [
        item for r in records
        if (item := unselected_specialist_invocation_item(r)) is not None
    ]
    unresolved_confirm_specialist_selections = [
        item for r in records
        if (item := unresolved_confirm_specialist_selection_item(r)) is not None
    ]
    missing_scoring_evidence = [
        item for r in records
        if (item := missing_scoring_evidence_item(r)) is not None
    ]
    invalid_score_iterations = [
        item for r in records
        if (item := invalid_score_iteration_item(r)) is not None
    ]
    blank_specialist_invocations = [
        item for r in records
        if (item := blank_specialist_invocation_item(r)) is not None
    ]
    preparation_only_completed_providers = [
        item for r in records
        if (item := preparation_only_completed_provider_item(r)) is not None
    ]
    missing_specialist_selection_checkpoints = [
        item for r in records
        if (item := missing_specialist_selection_checkpoint_item(r)) is not None
    ]
    candidate_only_specialists = [
        item for r in records
        if (item := candidate_only_specialist_item(r)) is not None
    ]
    current_missing_scoring_evidence, historical_missing_scoring_evidence = split_current_historical(
        missing_scoring_evidence, current_since
    )
    current_invalid_score_iterations, historical_invalid_score_iterations = split_current_historical(
        invalid_score_iterations, current_since
    )
    current_blank_specialist_invocations, historical_blank_specialist_invocations = split_current_historical(
        blank_specialist_invocations, current_since
    )
    current_preparation_only_completed_providers, historical_preparation_only_completed_providers = split_current_historical(
        preparation_only_completed_providers, current_since
    )
    current_unresolved_confirm_specialist_selections, historical_unresolved_confirm_specialist_selections = split_current_historical(
        unresolved_confirm_specialist_selections, current_since
    )
    current_candidate_only_specialists, historical_candidate_only_specialists = split_current_historical(
        candidate_only_specialists, current_since
    )
    halt_or_incomplete = [
        r for r, cls in zip(records, classes)
        if cls in {"halt", "incomplete"}
    ]
    halt_sessions = [r for r, cls in zip(records, classes) if cls == "halt"]
    current_halt_sessions, historical_halt_sessions = split_current_historical_records(
        halt_sessions, current_since
    )
    current_slow_sessions, historical_slow_sessions = split_current_historical_records(
        slow, current_since
    )
    current_low_score_pass_sessions, historical_low_score_pass_sessions = split_current_historical_records(
        low_score_pass, current_since
    )
    slow_session_breakdown = bucket_counts(slow, lambda record: slow_session_bucket(record, slow_threshold_sec))
    slow_phase_duration_breakdown = bucket_counts(slow, slow_phase_observability_bucket)
    coarse_phase_attributions = [
        item for r in slow
        if (item := coarse_phase_attribution_item(r)) is not None
    ]

    by_project: dict[str, dict[str, int]] = {}
    by_agent: dict[str, dict[str, int]] = {}
    for record, cls in zip(records, classes):
        for bucket, key in ((by_project, project_name(record.state)), (by_agent, record.state.get("agent") or "unknown")):
            row = bucket.setdefault(key, {"total": 0, "pass": 0, "halt": 0, "incomplete": 0, "abandoned": 0})
            row["total"] += 1
            row[cls] += 1

    activity_timing = summarize_activity_states([record.state for record in records])

    return {
        "total_sessions": len(records),
        "invalid_worktree_archives": invalid_worktree_archives,
        "invalid_worktree_archive_count": len(invalid_worktree_archives),
        "pass_count": pass_count,
        "halt_count": classes.count("halt"),
        "incomplete_count": classes.count("incomplete"),
        "abandoned_count": classes.count("abandoned"),
        "active_no_score_checkpoint_count": len(active_no_score_checkpoint),
        "active_no_score_pending_sessions": active_no_score_pending,
        "active_no_score_pending_count": len(active_no_score_pending),
        "stale_active_no_score_sessions": stale_active_no_score,
        "stale_active_no_score_count": len(stale_active_no_score),
        "pass_rate_denominator": len(pass_rate_records),
        "pass_rate": pass_count / len(pass_rate_records) if pass_rate_records else None,
        "forced_pass_count": len(forced),
        "forced_pass_autonomous_suspect_count": len(forced_autonomous_suspect),  # #185
        "ungated_pass_count": len(ungated),
        "duplicate_group_count": len(duplicates),
        "resolved_duplicate_group_count": resolved_duplicate_count,
        "avg_final_composite": sum(composites) / len(composites) if composites else None,
        "median_session_duration_sec": statistics.median(durations) if durations else None,
        "avg_session_duration_sec": sum(durations) / len(durations) if durations else None,
        "slow_sessions": slow,
        "halt_sessions": halt_sessions,
        "low_score_pass_sessions": low_score_pass,
        "specialist_invocation_gap_sessions": specialist_invocation_gaps,
        "forced_pass_sessions": forced,
        "forced_pass_autonomous_suspect_sessions": forced_autonomous_suspect,  # #185
        "ungated_pass_sessions": ungated,
        "duplicates": duplicates,
        "missing_scoring_evidence": missing_scoring_evidence,
        "missing_scoring_evidence_count": len(missing_scoring_evidence),
        "missing_scoring_evidence_breakdown": bucket_count_keys(
            [str(item.get("project") or "unknown") for item in missing_scoring_evidence]
        ),
        "invalid_score_iterations": invalid_score_iterations,
        "invalid_score_iteration_count": len(invalid_score_iterations),
        "invalid_score_iteration_breakdown": bucket_count_keys(
            [str(item.get("project") or "unknown") for item in invalid_score_iterations]
        ),
        "blank_specialist_invocations": blank_specialist_invocations,
        "blank_specialist_invocation_count": len(blank_specialist_invocations),
        "blank_specialist_invocation_breakdown": bucket_count_keys(
            [str(item.get("project") or "unknown") for item in blank_specialist_invocations]
        ),
        "preparation_only_completed_providers": preparation_only_completed_providers,
        "preparation_only_completed_provider_count": len(preparation_only_completed_providers),
        "preparation_only_completed_provider_breakdown": bucket_count_keys(
            [str(item.get("project") or "unknown") for item in preparation_only_completed_providers]
        ),
        "missing_specialist_selection_checkpoints": missing_specialist_selection_checkpoints,
        "missing_specialist_selection_checkpoint_count": len(missing_specialist_selection_checkpoints),
        "missing_specialist_selection_checkpoint_breakdown": bucket_count_keys(
            [str(item.get("project") or "unknown") for item in missing_specialist_selection_checkpoints]
        ),
        "unselected_specialist_invocations": unselected_specialist_invocations,
        "unselected_specialist_invocation_count": len(unselected_specialist_invocations),
        "unselected_specialist_invocation_breakdown": bucket_count_keys(
            [
                str(skill)
                for item in unselected_specialist_invocations
                for skill in item.get("skills", [])
            ]
        ),
        "unresolved_confirm_specialist_selections": unresolved_confirm_specialist_selections,
        "unresolved_confirm_specialist_selection_count": len(unresolved_confirm_specialist_selections),
        "unresolved_confirm_specialist_selection_breakdown": bucket_count_keys(
            [
                str(skill)
                for item in unresolved_confirm_specialist_selections
                for skill in item.get("skills", [])
            ]
        ),
        "candidate_only_specialists": candidate_only_specialists,
        "candidate_only_specialist_count": len(candidate_only_specialists),
        "candidate_only_specialist_breakdown": bucket_count_keys(
            [str(item.get("project") or "unknown") for item in candidate_only_specialists]
        ),
        "candidate_only_specialist_skill_breakdown": bucket_count_keys(
            [
                str(skill)
                for item in candidate_only_specialists
                for skill in item.get("skills", [])
            ]
        ),
        "halt_incomplete_breakdown": bucket_counts(halt_or_incomplete, halt_or_incomplete_bucket),
        "slow_session_breakdown": slow_session_breakdown,
        "slow_phase_duration_breakdown": slow_phase_duration_breakdown,
        "coarse_phase_attributions": coarse_phase_attributions,
        "coarse_phase_attribution_count": len(coarse_phase_attributions),
        "low_score_pass_breakdown": bucket_counts(low_score_pass, low_score_pass_bucket),
        "specialist_invocation_gap_count": len(specialist_invocation_gaps),
        "specialist_invocation_gap_breakdown": bucket_counts(
            [
                StateRecord(record.path, {**record.state, "_gap_skill": skill})
                for record in specialist_invocation_gaps
                for skill in specialist_invocation_gap_skills(record)
            ],
            lambda record: str(record.state.get("_gap_skill") or "unknown"),
        ),
        "by_project": by_project,
        "by_agent": by_agent,
        "activity_timing": activity_timing,
        "current_since": current_since.isoformat() if current_since else None,
        "current_halt_sessions": current_halt_sessions,
        "current_halt_count": len(current_halt_sessions),
        "historical_halt_sessions": historical_halt_sessions,
        "historical_halt_count": len(historical_halt_sessions),
        "current_slow_sessions": current_slow_sessions,
        "current_slow_session_count": len(current_slow_sessions),
        "historical_slow_sessions": historical_slow_sessions,
        "historical_slow_session_count": len(historical_slow_sessions),
        "current_low_score_pass_sessions": current_low_score_pass_sessions,
        "current_low_score_pass_count": len(current_low_score_pass_sessions),
        "historical_low_score_pass_sessions": historical_low_score_pass_sessions,
        "historical_low_score_pass_count": len(historical_low_score_pass_sessions),
        "current_missing_scoring_evidence": current_missing_scoring_evidence,
        "current_missing_scoring_evidence_count": len(current_missing_scoring_evidence),
        "historical_missing_scoring_evidence": historical_missing_scoring_evidence,
        "historical_missing_scoring_evidence_count": len(historical_missing_scoring_evidence),
        "current_invalid_score_iterations": current_invalid_score_iterations,
        "current_invalid_score_iteration_count": len(current_invalid_score_iterations),
        "historical_invalid_score_iterations": historical_invalid_score_iterations,
        "historical_invalid_score_iteration_count": len(historical_invalid_score_iterations),
        "current_blank_specialist_invocations": current_blank_specialist_invocations,
        "current_blank_specialist_invocation_count": len(current_blank_specialist_invocations),
        "historical_blank_specialist_invocations": historical_blank_specialist_invocations,
        "historical_blank_specialist_invocation_count": len(historical_blank_specialist_invocations),
        "current_preparation_only_completed_providers": current_preparation_only_completed_providers,
        "current_preparation_only_completed_provider_count": len(current_preparation_only_completed_providers),
        "historical_preparation_only_completed_providers": historical_preparation_only_completed_providers,
        "historical_preparation_only_completed_provider_count": len(historical_preparation_only_completed_providers),
        "current_unresolved_confirm_specialist_selections": current_unresolved_confirm_specialist_selections,
        "current_unresolved_confirm_specialist_selection_count": len(current_unresolved_confirm_specialist_selections),
        "historical_unresolved_confirm_specialist_selections": historical_unresolved_confirm_specialist_selections,
        "historical_unresolved_confirm_specialist_selection_count": len(historical_unresolved_confirm_specialist_selections),
        "current_candidate_only_specialists": current_candidate_only_specialists,
        "current_candidate_only_specialist_count": len(current_candidate_only_specialists),
        "historical_candidate_only_specialists": historical_candidate_only_specialists,
        "historical_candidate_only_specialist_count": len(historical_candidate_only_specialists),
    }


def fmt_float(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def fmt_minutes(seconds: float | None) -> str:
    return "-" if seconds is None else f"{seconds / 60:.1f} min"


def session_line(record: StateRecord) -> str:
    state = record.state
    entry = latest_scored_entry(state) or {}
    duration = duration_sec(state)
    mission = " ".join(str(state.get("mission") or "").split())
    progress = state.get("progress") if isinstance(state.get("progress"), dict) else {}
    progress_text = ""
    if progress:
        progress_text = (
            f", progress {progress.get('completed', '-')}/{progress.get('total', '-')}"
            f" remaining {progress.get('remaining', '-')}"
        )
    if len(mission) > 90:
        mission = mission[:87] + "..."
    return (
        f"- `{state.get('session_id') or record.path.stem}` "
        f"({project_name(state)}, {state.get('agent') or 'unknown'}, {classify(state)}, "
        f"{fmt_minutes(duration)}, score {fmt_float(entry.get('composite'))}{progress_text}): {mission}"
    )


def finding_rows(stats: dict[str, Any], min_pass_rate: float) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    current_scope = bool(stats.get("current_since"))
    missing_scoring_count = (
        stats["current_missing_scoring_evidence_count"]
        if current_scope else stats["missing_scoring_evidence_count"]
    )
    invalid_score_count = (
        stats["current_invalid_score_iteration_count"]
        if current_scope else stats["invalid_score_iteration_count"]
    )
    blank_invocation_count = (
        stats["current_blank_specialist_invocation_count"]
        if current_scope else stats["blank_specialist_invocation_count"]
    )
    preparation_only_completed_count = (
        stats["current_preparation_only_completed_provider_count"]
        if current_scope else stats["preparation_only_completed_provider_count"]
    )
    halt_count = stats["current_halt_count"] if current_scope else stats["halt_count"]
    slow_count = stats["current_slow_session_count"] if current_scope else len(stats["slow_sessions"])
    low_score_pass_count = (
        stats["current_low_score_pass_count"]
        if current_scope else len(stats["low_score_pass_sessions"])
    )
    unresolved_confirm_count = (
        stats["current_unresolved_confirm_specialist_selection_count"]
        if current_scope else stats["unresolved_confirm_specialist_selection_count"]
    )
    candidate_only_count = (
        stats["current_candidate_only_specialist_count"]
        if current_scope else stats["candidate_only_specialist_count"]
    )
    if stats["invalid_worktree_archive_count"]:
        rows.append((
            "P1",
            "invalid-worktree-archive",
            f"{stats['invalid_worktree_archive_count']} worktree archive bundles have an invalid pointer, generation, or manifest",
        ))
    if stats["ungated_pass_count"]:
        rows.append(("P0", "ungated-pass", f"{stats['ungated_pass_count']} pass sessions bypassed scoring gate"))
    if stats["forced_pass_count"]:
        rows.append(("P1", "forced-pass", f"{stats['forced_pass_count']} sessions used force pass"))
    if stats["forced_pass_autonomous_suspect_count"]:  # #185
        rows.append((
            "P0",
            "forced-pass-autonomous-suspect",
            f"{stats['forced_pass_autonomous_suspect_count']} forced-pass sessions lack "
            f"force_approved_by_user and any user-approval mention in force_reason "
            f"-- possible autonomous --force (must be user-initiated only)",
        ))
    if stats["duplicate_group_count"]:
        rows.append(("P1", "duplicate-state", f"{stats['duplicate_group_count']} duplicate state groups found; stats may double-count"))
    if missing_scoring_count:
        label = "current sessions" if current_scope else "sessions"
        rows.append(("P2", "missing-scoring-evidence", f"{missing_scoring_count} {label} have score_history without archived scoring evidence"))
    if invalid_score_count:
        label = "current sessions" if current_scope else "sessions"
        rows.append(("P1", "invalid-score-iteration", f"{invalid_score_count} {label} have score_history entries outside iteration >= 1"))
    if blank_invocation_count:
        label = "current sessions" if current_scope else "sessions"
        rows.append(("P2", "blank-specialist-invocation", f"{blank_invocation_count} {label} have blank specialist role or skill fields"))
    if preparation_only_completed_count:
        label = "current sessions" if current_scope else "sessions"
        rows.append(("P1", "preparation-only-completed-provider", f"{preparation_only_completed_count} {label} marked command-provider preparation-only evidence as completed"))
    if stats["missing_specialist_selection_checkpoint_count"]:
        rows.append(("P2", "missing-specialist-selection-checkpoint", f"{stats['missing_specialist_selection_checkpoint_count']} sessions started after checkpoint rollout without selection metadata"))
    pass_rate = stats["pass_rate"]
    if pass_rate is not None and pass_rate < min_pass_rate:
        rows.append(("P1", "low-pass-rate", f"pass rate {pass_rate * 100:.1f}% is below {min_pass_rate * 100:.0f}%"))
    if halt_count:
        label = "current halted sessions" if current_scope else "halted sessions"
        rows.append(("P1", "halted-runs", f"{halt_count} {label} need root-cause review"))
    if stats["stale_active_no_score_count"]:
        rows.append(("P1", "stale-active-no-score", f"{stats['stale_active_no_score_count']} active no-score sessions exceeded stale threshold"))
    if slow_count:
        label = "current sessions" if current_scope else "sessions"
        rows.append(("P2", "slow-runs", f"{slow_count} {label} exceeded slow threshold"))
    if stats["coarse_phase_attribution_count"]:
        rows.append(("P2", "coarse-phase-attribution", f"{stats['coarse_phase_attribution_count']} slow sessions attribute most elapsed time to planning"))
    if low_score_pass_count:
        label = "current pass sessions" if current_scope else "pass sessions"
        rows.append(("P2", "low-score-pass", f"{low_score_pass_count} {label} scored below 4.3"))
    if stats["specialist_invocation_gap_sessions"]:
        rows.append(("P2", "specialist-invocation-gap", f"{len(stats['specialist_invocation_gap_sessions'])} sessions selected specialists without terminal invocation logs"))
    if unresolved_confirm_count:
        label = "current sessions" if current_scope else "sessions"
        rows.append(("P2", "unresolved-confirm-specialist-selection", f"{unresolved_confirm_count} {label} applied specialists after ask-user without confirmed selection metadata"))
    if stats["unselected_specialist_invocation_count"]:
        rows.append(("P2", "unselected-specialist-invocation", f"{stats['unselected_specialist_invocation_count']} sessions invoked specialists without matching selection metadata"))
    if candidate_only_count:
        source = stats["current_candidate_only_specialists"] if current_scope else stats["candidate_only_specialists"]
        priority = "P1" if any(item.get("priority") == "P1" for item in source) else "P2"
        label = "current sessions" if current_scope else "sessions"
        rows.append((priority, "candidate-only-specialists", f"{candidate_only_count} {label} had specialist candidates but no selected, invoked, or skipped decision trail"))
    if current_scope:
        historical_count = (
            stats["historical_halt_count"]
            + stats["historical_slow_session_count"]
            + stats["historical_low_score_pass_count"]
            + stats["historical_missing_scoring_evidence_count"]
            + stats["historical_invalid_score_iteration_count"]
            + stats["historical_blank_specialist_invocation_count"]
            + stats["historical_preparation_only_completed_provider_count"]
            + stats["historical_unresolved_confirm_specialist_selection_count"]
            + stats["historical_candidate_only_specialist_count"]
        )
        if historical_count:
            rows.append(("INFO", "historical-fixed-debt", f"{historical_count} pre-cutoff audit items remain visible as historical debt"))
    if not rows:
        rows.append(("OK", "no-critical-findings", "No forced/ungated pass, halt, duplicate, scoring-evidence, slow-session, or specialist finding"))
    return rows


def self_improvement_prompt(
    rows: list[tuple[str, str, str]],
    roots: list[Path],
    since: str | None,
    until: str | None,
    current_since: str | None = None,
) -> str:
    root_args = " ".join(f"--root {root}" for root in roots)
    period = f"--since {since}" if since else ""
    if until:
        period += f" --until {until}"
    if current_since:
        period += f" --current-since {current_since}"
    finding_text = "\n".join(f"- {p} `{code}`: {summary}" for p, code, summary in rows if p != "OK")
    if not finding_text:
        finding_text = "- 現時点で P0/P1 はなし。P2 以下を ROI 順に確認する。"
    return f"""/mission scripts/mission-audit.py の監査結果をもとに mission 自身を自己改善してください。

監査コマンド:
`python3 scripts/mission-audit.py {root_args} {period} --self-improvement-prompt`

検出事項:
{finding_text}

制約:
- 不可逆操作、外部送信、本番反映は行わない
- まず P0/P1 を最大3件に絞る
- GitHub Issue を新規作成する前に open/closed issue を検索し、重複確認の検索語・関連 issue 番号・非重複判断を issue 本文に記録する
- GitHub Issue を新規作成する前に development/tech-lead review を行い、正確性・実装方針・テスト計画・OSS portability の確認結果を issue 本文に記録する
- 修正する場合はテストを追加し、既存テストを通す
- `skills/mission/` と `plugins/mission/` の同期が必要な変更は同期テストも通す
- 完了前に `python3 scripts/mission-audit.py {root_args} {period}` を再実行して forced/ungated/duplicate/halt の改善を確認する
"""


def render_markdown(stats: dict[str, Any], rows: list[tuple[str, str, str]], roots: list[Path], since: str | None, until: str | None) -> str:
    lines = [
        "# /mission Audit Report",
        "",
        "## Scope",
        "",
        f"- roots: {', '.join(str(r) for r in roots)}",
        f"- period: {since or '(all)'} ~ {until or '(now)'}",
        "",
        "## Summary",
        "",
        f"- total sessions: {stats['total_sessions']}",
        f"- current finding cutoff: {stats['current_since'] or '(not set; all findings are treated as current)'}",
        f"- pass / halt / incomplete / abandoned: {stats['pass_count']} / {stats['halt_count']} / {stats['incomplete_count']} / {stats['abandoned_count']}",
        f"- pass rate: {fmt_float(stats['pass_rate'] * 100 if stats['pass_rate'] is not None else None, 1)}% (denominator: {stats['pass_rate_denominator']})",
        f"- active no-score checkpoints excluded from pass rate: {stats['active_no_score_checkpoint_count']}",
        f"- active no-score pending: {stats['active_no_score_pending_count']}",
        f"- stale active no-score: {stats['stale_active_no_score_count']}",
        f"- forced pass: {stats['forced_pass_count']}",
        f"- forced pass (autonomous suspect, #185): {stats['forced_pass_autonomous_suspect_count']}",
        f"- ungated pass: {stats['ungated_pass_count']}",
        f"- duplicate state groups: {stats['duplicate_group_count']}",
        f"- resolved archive duplicates: {stats['resolved_duplicate_group_count']}",
        f"- missing scoring evidence: {stats['missing_scoring_evidence_count']}",
        f"- invalid score iterations: {stats['invalid_score_iteration_count']}",
        f"- blank specialist invocations: {stats['blank_specialist_invocation_count']}",
        f"- preparation-only completed command providers: {stats['preparation_only_completed_provider_count']}",
        f"- missing specialist selection checkpoints: {stats['missing_specialist_selection_checkpoint_count']}",
        f"- unresolved confirm specialist selections: {stats['unresolved_confirm_specialist_selection_count']}",
        f"- unselected specialist invocations: {stats['unselected_specialist_invocation_count']}",
        f"- candidate-only specialists: {stats['candidate_only_specialist_count']}",
        f"- current audit debt: halt {stats['current_halt_count']}, slow {stats['current_slow_session_count']}, low-score {stats['current_low_score_pass_count']}, scoring {stats['current_missing_scoring_evidence_count']}, invalid score {stats['current_invalid_score_iteration_count']}, blank invocation {stats['current_blank_specialist_invocation_count']}, preparation-only completed {stats['current_preparation_only_completed_provider_count']}, unresolved confirm {stats['current_unresolved_confirm_specialist_selection_count']}, candidate-only {stats['current_candidate_only_specialist_count']}",
        f"- historical audit debt: halt {stats['historical_halt_count']}, slow {stats['historical_slow_session_count']}, low-score {stats['historical_low_score_pass_count']}, scoring {stats['historical_missing_scoring_evidence_count']}, invalid score {stats['historical_invalid_score_iteration_count']}, blank invocation {stats['historical_blank_specialist_invocation_count']}, preparation-only completed {stats['historical_preparation_only_completed_provider_count']}, unresolved confirm {stats['historical_unresolved_confirm_specialist_selection_count']}, candidate-only {stats['historical_candidate_only_specialist_count']}",
        f"- coarse phase attribution: {stats['coarse_phase_attribution_count']}",
        f"- avg final composite: {fmt_float(stats['avg_final_composite'])}",
        f"- median duration: {fmt_minutes(stats['median_session_duration_sec'])}",
        f"- avg duration: {fmt_minutes(stats['avg_session_duration_sec'])}",
    ]
    activity = stats.get("activity_timing") or {}
    coverage = activity.get("coverage_ratio")
    coverage_text = f"{coverage * 100:.1f}%" if coverage is not None else "-"
    lines.extend([
        "",
        "## Activity Timing",
        "",
        f"- observed / unclassified: {fmt_minutes(activity.get('observed_total_sec'))} / "
        f"{fmt_minutes(activity.get('unclassified_sec'))}",
        f"- coverage: {coverage_text}",
        f"- unobserved gap: {fmt_minutes(activity.get('unobserved_gap_sec'))}",
        f"- totals consistent: {str(activity.get('totals_consistent', False)).lower()}",
        f"- segments closed / open / invalid: {activity.get('closed_segment_count', 0)} / "
        f"{activity.get('open_segment_count', 0)} / {activity.get('invalid_segment_count', 0)}",
        f"- percentile method: `{activity.get('percentile_method', 'unknown')}`",
    ])
    for kind, seconds in sorted((activity.get("activity_duration_totals_sec") or {}).items()):
        lines.append(f"- `{kind}`: {fmt_minutes(seconds)}")
    for kind, reasons in sorted((activity.get("wait_reason_totals_sec") or {}).items()):
        for reason, seconds in sorted(reasons.items()):
            lines.append(f"- wait `{kind}/{reason}`: {fmt_minutes(seconds)}")
    for task, values in sorted((activity.get("task_duration_percentiles_sec") or {}).items()):
        lines.append(
            f"- task `{task}`: p50 {fmt_minutes(values.get('p50'))}, "
            f"p90 {fmt_minutes(values.get('p90'))} (n={values.get('count', 0)})"
        )
    for phase, values in sorted((activity.get("phase_duration_percentiles_sec") or {}).items()):
        lines.append(
            f"- phase `{phase}`: p50 {fmt_minutes(values.get('p50'))}, "
            f"p90 {fmt_minutes(values.get('p90'))} (n={values.get('count', 0)})"
        )
    lines.extend(["", "## Findings", ""])
    for priority, code, summary in rows:
        lines.append(f"- **{priority} `{code}`**: {summary}")

    lines.extend(["", "## Halt Sessions", ""])
    if stats["halt_sessions"]:
        lines.extend(session_line(record) for record in stats["halt_sessions"])
    else:
        lines.append("- none")

    lines.extend(["", "## Halt / Incomplete Root-Cause Buckets", ""])
    if stats["halt_incomplete_breakdown"]:
        for key, count in sorted(stats["halt_incomplete_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Stale Active No-Score Sessions", ""])
    if stats["stale_active_no_score_sessions"]:
        lines.extend(session_line(record) for record in stats["stale_active_no_score_sessions"])
    else:
        lines.append("- none")

    lines.extend(["", "## Pending Active No-Score Sessions", ""])
    if stats["active_no_score_pending_sessions"]:
        lines.extend(session_line(record) for record in stats["active_no_score_pending_sessions"])
    else:
        lines.append("- none")

    lines.extend(["", "## Slow Sessions", ""])
    if stats["slow_sessions"]:
        lines.extend(session_line(record) for record in stats["slow_sessions"])
    else:
        lines.append("- none")

    lines.extend(["", "## Slow Session Buckets", ""])
    if stats["slow_session_breakdown"]:
        for key, count in sorted(stats["slow_session_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Slow Phase Duration Buckets", ""])
    if stats["slow_phase_duration_breakdown"]:
        for key, count in sorted(stats["slow_phase_duration_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Coarse Phase Attribution", ""])
    if stats["coarse_phase_attributions"]:
        for item in stats["coarse_phase_attributions"]:
            lines.append(
                f"- `{item['session_id']}` ({item['project']}): planning "
                f"{fmt_minutes(item['planning_sec'])} / {fmt_minutes(item['duration_sec'])} "
                f"({item['planning_ratio'] * 100:.1f}%) at `{item['path']}`"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Low-Score Pass Buckets", ""])
    if stats["low_score_pass_breakdown"]:
        for key, count in sorted(stats["low_score_pass_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Missing Scoring Evidence", ""])
    if stats["missing_scoring_evidence"]:
        for item in stats["missing_scoring_evidence"]:
            iterations = ", ".join(str(i) for i in item["missing_iterations"])
            lines.append(
                f"- `{item['session_id']}` ({item['project']}): missing iter {iterations} evidence at `{item['path']}`"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Missing Scoring Evidence Buckets", ""])
    if stats["missing_scoring_evidence_breakdown"]:
        for key, count in sorted(stats["missing_scoring_evidence_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Invalid Score Iterations", ""])
    if stats["invalid_score_iterations"]:
        for item in stats["invalid_score_iterations"]:
            iterations = ", ".join(str(i) for i in item["invalid_iterations"])
            lines.append(
                f"- `{item['session_id']}` ({item['project']}): invalid iter {iterations} at `{item['path']}`"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Invalid Score Iteration Buckets", ""])
    if stats["invalid_score_iteration_breakdown"]:
        for key, count in sorted(stats["invalid_score_iteration_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Missing Specialist Selection Checkpoints", ""])
    if stats["missing_specialist_selection_checkpoints"]:
        for item in stats["missing_specialist_selection_checkpoints"]:
            lines.append(
                f"- `{item['session_id']}` ({item['project']}): missing selection checkpoint at `{item['path']}`"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Missing Specialist Selection Checkpoint Buckets", ""])
    if stats["missing_specialist_selection_checkpoint_breakdown"]:
        for key, count in sorted(stats["missing_specialist_selection_checkpoint_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Blank Specialist Invocations", ""])
    if stats["blank_specialist_invocations"]:
        for item in stats["blank_specialist_invocations"]:
            entries = ", ".join(f"#{entry['index']}" for entry in item["blank_entries"])
            lines.append(
                f"- `{item['session_id']}` ({item['project']}): blank role/skill at {entries} in `{item['path']}`"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Blank Specialist Invocation Buckets", ""])
    if stats["blank_specialist_invocation_breakdown"]:
        for key, count in sorted(stats["blank_specialist_invocation_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Preparation-Only Completed Command Providers", ""])
    if stats["preparation_only_completed_providers"]:
        for item in stats["preparation_only_completed_providers"]:
            entries: list[str] = []
            for entry in item["bad_entries"]:
                evidence = entry["evidence"][0]
                markers = ", ".join(f"`{marker}`" for marker in evidence["markers"])
                entries.append(
                    f"#{entry['index']} `{entry['skill'] or entry['role']}` iter {entry['iteration']} "
                    f"at `{evidence['path']}` markers {markers}"
                )
            lines.append(
                f"- `{item['session_id']}` ({item['project']}): "
                f"{'; '.join(entries)}; state `{item['path']}`"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Preparation-Only Completed Command Provider Buckets", ""])
    if stats["preparation_only_completed_provider_breakdown"]:
        for key, count in sorted(stats["preparation_only_completed_provider_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Specialist Invocation Gaps", ""])
    if stats["specialist_invocation_gap_sessions"]:
        lines.extend(session_line(record) for record in stats["specialist_invocation_gap_sessions"])
    else:
        lines.append("- none")

    lines.extend(["", "## Specialist Invocation Gap Buckets", ""])
    if stats["specialist_invocation_gap_breakdown"]:
        for key, count in sorted(stats["specialist_invocation_gap_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Unresolved Confirm Specialist Selections", ""])
    if stats["unresolved_confirm_specialist_selections"]:
        for item in stats["unresolved_confirm_specialist_selections"]:
            skills = ", ".join(f"`{skill}`" for skill in item["skills"])
            lines.append(
                f"- `{item['session_id']}` ({item['project']}): applied {skills} after ask-user without confirmed selection metadata at `{item['path']}`"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Unresolved Confirm Specialist Selection Buckets", ""])
    if stats["unresolved_confirm_specialist_selection_breakdown"]:
        for key, count in sorted(stats["unresolved_confirm_specialist_selection_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Unselected Specialist Invocations", ""])
    if stats["unselected_specialist_invocations"]:
        for item in stats["unselected_specialist_invocations"]:
            skills = ", ".join(f"`{skill}`" for skill in item["skills"])
            lines.append(
                f"- `{item['session_id']}` ({item['project']}): invoked {skills} without selection metadata at `{item['path']}`"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Unselected Specialist Invocation Buckets", ""])
    if stats["unselected_specialist_invocation_breakdown"]:
        for key, count in sorted(stats["unselected_specialist_invocation_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Candidate-Only Specialist Recommendations", ""])
    if stats["candidate_only_specialists"]:
        for item in stats["candidate_only_specialists"]:
            skills = ", ".join(f"`{skill}`" for skill in item["skills"])
            lines.append(
                f"- `{item['session_id']}` ({item['project']}, {item['complexity']}, {item['priority']}): "
                f"{item['candidate_count']} candidates ({skills}) but no selected/invoked/skipped trail at `{item['path']}`"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Candidate-Only Specialist Buckets", ""])
    if stats["candidate_only_specialist_breakdown"]:
        for key, count in sorted(stats["candidate_only_specialist_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Candidate-Only Specialist Skill Buckets", ""])
    if stats["candidate_only_specialist_skill_breakdown"]:
        for key, count in sorted(stats["candidate_only_specialist_skill_breakdown"].items()):
            lines.append(f"- `{key}`: {count}")
    else:
        lines.append("- none")

    if stats["current_since"]:
        lines.extend(["", "## Historical Audit Debt", ""])
        historical_rows = [
            ("halted runs", stats["historical_halt_count"]),
            ("slow runs", stats["historical_slow_session_count"]),
            ("low-score passes", stats["historical_low_score_pass_count"]),
            ("missing scoring evidence", stats["historical_missing_scoring_evidence_count"]),
            ("invalid score iterations", stats["historical_invalid_score_iteration_count"]),
            ("blank specialist invocations", stats["historical_blank_specialist_invocation_count"]),
            ("preparation-only completed command providers", stats["historical_preparation_only_completed_provider_count"]),
            ("unresolved confirm specialist selections", stats["historical_unresolved_confirm_specialist_selection_count"]),
            ("candidate-only specialists", stats["historical_candidate_only_specialist_count"]),
        ]
        if any(count for _, count in historical_rows):
            for label, count in historical_rows:
                lines.append(f"- {label}: {count}")
        else:
            lines.append("- none")

    lines.extend(["", "## Duplicate State Groups", ""])
    if stats["duplicates"]:
        for group in stats["duplicates"]:
            lines.append("- " + " | ".join(str(record.path) for record in sorted(group, key=record_rank)))
    else:
        lines.append("- none")

    lines.extend(["", "## By Project", ""])
    for key, row in sorted(stats["by_project"].items()):
        lines.append(f"- `{key}`: total {row['total']}, pass {row['pass']}, halt {row['halt']}, incomplete {row['incomplete']}")

    lines.extend(["", "## Self-Improvement Prompt", "", "```text"])
    lines.append(self_improvement_prompt(rows, roots, since, until, stats["current_since"]).strip())
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit local /mission state logs")
    parser.add_argument("--root", action="append", default=None, help="Root to scan. Can be repeated. Defaults to cwd.")
    parser.add_argument("--since", default=None, help="Updated-at lower bound, YYYY-MM-DD or ISO timestamp")
    parser.add_argument("--until", default=None, help="Updated-at upper bound, YYYY-MM-DD or ISO timestamp")
    parser.add_argument("--after-commit", default=None, help="Only include sessions updated after this commit date")
    parser.add_argument("--repo", default=".", help="Repository used for --after-commit, default cwd")
    parser.add_argument(
        "--current-since",
        default=None,
        help="Treat findings updated before this YYYY-MM-DD or ISO timestamp as historical debt, while keeping them in totals.",
    )
    parser.add_argument("--slow-threshold-sec", type=int, default=1800, help="Slow session threshold")
    parser.add_argument("--min-pass-rate", type=float, default=0.9, help="Finding threshold for pass rate")
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary")
    parser.add_argument("--out", default=None, help="Write report to path")
    parser.add_argument("--self-improvement-prompt", action="store_true", help="Print only the prompt for /mission")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _MANIFEST_VALIDATION_CACHE.clear()
    args = parse_args(argv or sys.argv[1:])
    roots = [Path(p).expanduser() for p in (args.root or ["."])]
    after = commit_datetime(Path(args.repo).expanduser(), args.after_commit) if args.after_commit else None
    since = parse_period_cutoff(args.since)
    if args.since and since is None:
        raise SystemExit(f"Invalid --since value: {args.since}")
    until = parse_period_cutoff(args.until, upper=True)
    if args.until and until is None:
        raise SystemExit(f"Invalid --until value: {args.until}")
    current_since = parse_cutoff(args.current_since)
    if args.current_since and current_since is None:
        raise SystemExit(f"Invalid --current-since value: {args.current_since}")
    discovered_invalid_archives: list[dict[str, Any]] = []
    records = load_records(roots, discovered_invalid_archives)
    invalid_worktree_archives = collect_invalid_worktree_archives(
        records,
        discovered_invalid_archives,
    )
    filtered = filter_records(records, since, until, after)
    deduped, duplicates, resolved_duplicate_count = dedupe_records(filtered)
    stats = aggregate(
        deduped,
        duplicates,
        resolved_duplicate_count,
        args.slow_threshold_sec,
        current_since,
        invalid_worktree_archives,
    )
    rows = finding_rows(stats, args.min_pass_rate)

    if args.self_improvement_prompt:
        output = self_improvement_prompt(rows, roots, args.since, args.until, args.current_since)
    elif args.json:
        json_stats = {
            k: v for k, v in stats.items()
            if k not in {
                "duplicates",
                "forced_pass_sessions",
                "forced_pass_autonomous_suspect_sessions",
                "halt_sessions",
                "current_halt_sessions",
                "historical_halt_sessions",
                "low_score_pass_sessions",
                "current_low_score_pass_sessions",
                "historical_low_score_pass_sessions",
                "specialist_invocation_gap_sessions",
                "slow_sessions",
                "current_slow_sessions",
                "historical_slow_sessions",
                "active_no_score_pending_sessions",
                "stale_active_no_score_sessions",
                "ungated_pass_sessions",
            }
        }
        json_stats["findings"] = [{"priority": p, "code": c, "summary": s} for p, c, s in rows]
        output = json.dumps(json_stats, indent=2, ensure_ascii=False)
    else:
        output = render_markdown(stats, rows, roots, args.since, args.until)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
