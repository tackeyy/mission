"""Shared fail-closed validation for immutable worktree archive generations."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

WORKTREE_ARCHIVE_SCHEMA = "mission-worktree-archive/1"
WORKTREE_ARCHIVE_POINTER_SCHEMA = "mission-worktree-current/1"
REVIEW_INPUT_MAX_BYTES = 4 * 1024 * 1024
_REVIEW_INPUT_REFERENCE_FIELDS = {
    "kind", "path", "digest", "size", "iteration", "perspective",
}


def valid_review_perspective(value: Any) -> bool:
    """Use one identity contract for review producers, references, and archives."""
    return isinstance(value, str) and bool(value) and value == value.strip()


@dataclass(frozen=True)
class WorktreeArchiveValidation:
    status: str
    root: Path
    generation: str | None = None
    reason: str | None = None
    state_paths: tuple[Path, ...] = ()
    state: dict[str, Any] | None = None
    evidence: tuple[dict[str, Any], ...] = ()
    pointer_sha256: str | None = None
    manifest_sha256: str | None = None


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


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _read_generation_file(
    root: Path, relative: Path, *, limit: int = 4 * 1024 * 1024,
    expected_size: int | None = None,
) -> tuple[bytes, os.stat_result]:
    """Read an archive object through one no-follow descriptor chain.

    The returned bytes and metadata identify the same regular, unlinked object.
    A second descriptor-chain lookup catches pathname replacement after the read.
    """
    if (
        not relative.parts or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
        or expected_size is not None
        and (isinstance(expected_size, bool) or expected_size < 0 or expected_size > limit)
    ):
        raise ValueError("archive evidence path is invalid")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    try:
        root_fd = os.open(os.fspath(root), os.O_RDONLY | directory | nofollow)
    except OSError as exc:
        raise ValueError("archive generation is unavailable") from exc

    def open_target() -> int:
        fd = os.dup(root_fd)
        try:
            for index, part in enumerate(relative.parts):
                flags = os.O_RDONLY | nofollow
                if index + 1 < len(relative.parts):
                    flags |= directory
                else:
                    flags |= os.O_NONBLOCK
                next_fd = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            return fd
        except BaseException:
            os.close(fd)
            raise

    try:
        fd = open_target()
        try:
            initial = os.fstat(fd)
            if (
                not stat.S_ISREG(initial.st_mode)
                or initial.st_nlink != 1
                or initial.st_size > limit
                or expected_size is not None and initial.st_size != expected_size
            ):
                raise ValueError("archive evidence must be a bounded regular non-linked file")
            chunks: list[bytes] = []
            remaining = initial.st_size
            while remaining:
                chunk = os.read(fd, min(remaining, 64 * 1024))
                if not chunk:
                    raise ValueError("archive evidence changed while being read")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(fd, 1) or _stat_identity(os.fstat(fd)) != _stat_identity(initial):
                raise ValueError("archive evidence changed while being read")
            current_fd = open_target()
            try:
                if _stat_identity(os.fstat(current_fd)) != _stat_identity(initial):
                    raise ValueError("archive evidence changed while being read")
            finally:
                os.close(current_fd)
            return b"".join(chunks), initial
        finally:
            os.close(fd)
    except OSError as exc:
        raise ValueError("archive evidence is unavailable") from exc
    finally:
        os.close(root_fd)


def _normalized_state_reference(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if ".mission-state" not in path.parts:
        return None
    index = path.parts.index(".mission-state")
    return Path(*path.parts[index:]).as_posix()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("review input contains duplicate JSON keys")
        result[key] = value
    return result


def verify_review_input_evidence(
    reference: object, content: bytes, *, expected_iteration: int | None = None,
) -> dict[str, Any]:
    """Bind one immutable review-input reference to its exact review bytes."""
    if not isinstance(reference, dict) or set(reference) != _REVIEW_INPUT_REFERENCE_FIELDS:
        raise ValueError("review input reference schema is invalid")
    path = _safe_relative_path(reference.get("path"), state_reference=True)
    digest = reference.get("digest")
    size = reference.get("size")
    iteration = reference.get("iteration")
    perspective = reference.get("perspective")
    if (
        reference.get("kind") != "review-input"
        or path is None
        or not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or len(digest) != 71
        or any(character not in "0123456789abcdef" for character in digest[7:])
        or not isinstance(size, int)
        or isinstance(size, bool)
        or not 0 <= size <= REVIEW_INPUT_MAX_BYTES
        or not isinstance(iteration, int)
        or isinstance(iteration, bool)
        or iteration < 1
        or expected_iteration is not None and iteration != expected_iteration
        or not valid_review_perspective(perspective)
    ):
        raise ValueError("review input reference schema is invalid")
    if len(content) != size or "sha256:" + hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("review input evidence integrity mismatch")
    try:
        payload = json.loads(content.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("review input evidence is invalid JSON") from exc
    payload_iteration = payload.get("iteration") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "mission-review/1"
        or not isinstance(payload_iteration, int)
        or isinstance(payload_iteration, bool)
        or payload_iteration < 1
        or payload_iteration != iteration
        or not valid_review_perspective(payload.get("perspective"))
        or payload.get("perspective") != perspective
    ):
        raise ValueError("review input evidence identity mismatch")
    return payload


def read_verified_review_input_evidence(
    root: Path, reference: object, *, expected_iteration: int | None = None,
) -> bytes:
    """Read and verify a state-owned review through one no-follow descriptor chain."""
    if not isinstance(reference, dict):
        raise ValueError("review input reference schema is invalid")
    relative = _safe_relative_path(reference.get("path"), state_reference=True)
    size = reference.get("size")
    if relative is None or not isinstance(size, int) or isinstance(size, bool):
        raise ValueError("review input reference schema is invalid")
    content, _metadata = _read_generation_file(
        root, relative, limit=REVIEW_INPUT_MAX_BYTES, expected_size=size,
    )
    verify_review_input_evidence(reference, content, expected_iteration=expected_iteration)
    return content


def worktree_archive_lineage_references(
    state: dict[str, Any], state_reference: str,
) -> tuple[tuple[str, int, str], ...] | None:
    """Return every state-owned immutable reference an archive must preserve.

    This is deliberately the one schema reader used by both the archive writer
    and generation validator.  Archive paths are transport details; source
    references remain the immutable state contract.
    """
    iteration = state.get("iteration")
    if not isinstance(iteration, int) or isinstance(iteration, bool):
        return None
    expected: list[tuple[str, int, str]] = []

    def add(kind: str, item_iteration: Any, reference: Any) -> bool:
        normalized = _normalized_state_reference(reference)
        if (
            not isinstance(item_iteration, int)
            or isinstance(item_iteration, bool)
            or normalized is None
        ):
            return False
        expected.append((kind, item_iteration, normalized))
        return True

    if not add("state", iteration, state_reference):
        return None
    if state.get("assumptions_path") and not add(
        "assumptions", iteration, state.get("assumptions_path")
    ):
        return None
    imported_reviews = state.get("review_evidence_refs")
    if imported_reviews is not None:
        if not isinstance(imported_reviews, list):
            return None
        for reference in imported_reviews:
            if (
                not isinstance(reference, dict)
                or reference.get("kind") != "review-input"
                or not isinstance(reference.get("iteration"), int)
                or isinstance(reference.get("iteration"), bool)
                or not isinstance(reference.get("digest"), str)
                or not isinstance(reference.get("size"), int)
                or isinstance(reference.get("size"), bool)
                or reference["size"] < 0
                or not valid_review_perspective(reference.get("perspective"))
                or not add("review-input", reference["iteration"], reference.get("path"))
            ):
                return None
    artifact = state.get("artifact") if isinstance(state.get("artifact"), dict) else {}
    if artifact.get("path") and not add("artifact", iteration, artifact.get("path")):
        return None
    for entry in state.get("score_history") or []:
        if not isinstance(entry, dict):
            continue
        item_iteration = entry.get("iteration")
        if entry.get("scoring_evidence_path") and not add(
            "scoring", item_iteration, entry.get("scoring_evidence_path")
        ):
            return None
        if entry.get("findings_evidence_path") and not add(
            "reviews", item_iteration, entry.get("findings_evidence_path")
        ):
            return None
        provenance = entry.get("score_provenance") if isinstance(entry.get("score_provenance"), dict) else {}
        source = provenance.get("score_source")
        if source == "scoring-json":
            reference = provenance.get("review_evidence_ref")
            kind = "review-aggregate"
        elif source == "manual-import":
            reference = provenance.get("manual_evidence_ref")
            kind = "manual-score-source"
        else:
            reference = None
            kind = ""
        if reference is not None:
            if not isinstance(reference, dict) or not add(kind, item_iteration, reference.get("path")):
                return None
        artifact_reference = provenance.get("scoring_evidence_ref")
        if artifact_reference is not None:
            if not isinstance(artifact_reference, dict) or not add(
                "scoring-artifact", item_iteration, artifact_reference.get("path")
            ):
                return None
    approval = state.get("force_approval") if isinstance(state.get("force_approval"), dict) else {}
    receipt = approval.get("receipt_ref") if isinstance(approval.get("receipt_ref"), dict) else {}
    if receipt.get("path") and not add("approval-receipt", iteration, receipt.get("path")):
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
    if progress.get("evidence_path") and not add(
        "progress", iteration, progress.get("evidence_path")
    ):
        return None
    if progress.get("artifact_path") and not add(
        "progress-artifact", iteration, progress.get("artifact_path")
    ):
        return None
    return tuple(expected)


def _expected_lineage(state: dict[str, Any], state_path: Path):
    references = worktree_archive_lineage_references(
        state, f".mission-state/sessions/{state_path.name}",
    )
    return Counter(references) if references is not None else None


def read_validated_archive_evidence(
    validation: WorktreeArchiveValidation, source_reference: object,
    *, limit: int = 4 * 1024 * 1024,
) -> bytes:
    """Read an evidence copy only after resolving it through a valid manifest."""
    normalized = _normalized_state_reference(source_reference)
    if validation.status != "valid" or normalized is None:
        raise ValueError("archive evidence resolver is invalid")
    matches = [item for item in validation.evidence if item.get("source_reference") == normalized]
    if not matches or len({item.get("sha256") for item in matches}) != 1:
        raise ValueError("archive evidence reference is missing or ambiguous")
    item = matches[0]
    path = item.get("path")
    if not isinstance(path, Path):
        raise ValueError("archive evidence resolver is invalid")
    try:
        relative = path.relative_to(validation.root)
        content, _metadata = _read_generation_file(
            validation.root, relative, limit=limit, expected_size=item.get("size"),
        )
    except (OSError, ValueError) as exc:
        raise ValueError("archive evidence is unavailable") from exc
    if hashlib.sha256(content).hexdigest() != item.get("sha256"):
        raise ValueError("archive evidence integrity mismatch")
    return content


def validated_archive_evidence_reader(
    validation: WorktreeArchiveValidation,
) -> Callable[[object], bytes]:
    """Expose a bounded source-reference reader for scoring consumers."""
    return lambda reference: read_validated_archive_evidence(validation, reference)


def validate_worktree_archive_bundle(bundle: Path) -> WorktreeArchiveValidation:
    """Resolve one bundle and verify a generation manifest before exposing state."""
    try:
        bundle_stat = bundle.lstat()
    except FileNotFoundError:
        return _invalid(bundle, bundle, "bundle-not-regular-directory")
    except (OSError, ValueError):
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
        pointer_bytes, _pointer_metadata = _read_generation_file(
            bundle, Path("current.json")
        )
    except (OSError, ValueError):
        return _invalid(bundle, bundle, "pointer-access-error")
    try:
        pointer = json.loads(pointer_bytes.decode("utf-8"))
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
    try:
        manifest_bytes, _manifest_metadata = _read_generation_file(
            generation_root, Path("manifest.json")
        )
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
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
    state_payloads: dict[str, bytes] = {}
    evidence_payloads: dict[str, bytes] = {}
    checked: list[dict[str, Any]] = []
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
        expected_size = item.get("size")
        expected_hash = item.get("sha256")
        if (
            not isinstance(expected_size, int)
            or isinstance(expected_size, bool)
            or expected_size < 0
            or not isinstance(expected_hash, str)
            or len(expected_hash) != 64
        ):
            return _invalid(bundle, generation_root, "manifest-invalid-evidence-file", generation)
        try:
            content, _metadata = _read_generation_file(
                generation_root, archive_path, expected_size=expected_size,
            )
        except (OSError, ValueError):
            return _invalid(bundle, generation_root, "manifest-evidence-access-error", generation)
        if hashlib.sha256(content).hexdigest() != expected_hash:
            return _invalid(bundle, generation_root, "manifest-evidence-integrity-mismatch", generation)
        if item["evidence_kind"] == "state":
            state_paths.append(archived)
            state_payloads[archive_path.as_posix()] = content
        evidence_payloads[archive_path.as_posix()] = content
        checked.append({**item, "path": archived})

    if len(state_paths) != 1:
        return _invalid(bundle, generation_root, "manifest-state-count-invalid", generation)
    try:
        state_archive_path = state_paths[0].relative_to(generation_root).as_posix()
        state = json.loads(state_payloads[state_archive_path].decode("utf-8"))
    except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _invalid(bundle, generation_root, "manifest-state-invalid-json", generation)
    if (
        not isinstance(state, dict)
        or state.get("session_id") != manifest.get("session_id")
        or state.get("mission_id") != manifest.get("mission_id")
        or state.get("iteration") != manifest.get("iteration")
    ):
        return _invalid(bundle, generation_root, "manifest-state-identity-mismatch", generation)
    expected_lineage = _expected_lineage(state, state_paths[0])
    actual_lineage = Counter(
        (item["evidence_kind"], item["iteration"], item["source_reference"])
        for item in evidence
    )
    if expected_lineage is None or actual_lineage != expected_lineage:
        return _invalid(bundle, generation_root, "manifest-state-lineage-mismatch", generation)
    review_references = state.get("review_evidence_refs")
    if review_references is not None and not isinstance(review_references, list):
        return _invalid(bundle, generation_root, "manifest-review-input-reference-invalid", generation)
    for item in checked:
        if item["evidence_kind"] != "review-input":
            continue
        matches = [
            reference for reference in (review_references or [])
            if isinstance(reference, dict)
            and reference.get("path") == item["source_reference"]
            and reference.get("iteration") == item["iteration"]
        ]
        if len(matches) != 1:
            return _invalid(bundle, generation_root, "manifest-review-input-reference-invalid", generation)
        try:
            verify_review_input_evidence(
                matches[0], evidence_payloads[item["archive_path"]],
                expected_iteration=item["iteration"],
            )
        except (KeyError, ValueError):
            return _invalid(bundle, generation_root, "manifest-review-input-integrity-mismatch", generation)
    state_entries = [item for item in checked if item["evidence_kind"] == "state"]
    state_archive_path = state_paths[0].relative_to(generation_root).as_posix()
    if len(state_entries) != 1 or state_entries[0]["archive_path"] != state_archive_path:
        return _invalid(bundle, generation_root, "manifest-state-path-mismatch", generation)
    return WorktreeArchiveValidation(
        "valid",
        generation_root,
        generation=generation,
        state_paths=tuple(state_paths),
        state=state,
        evidence=tuple(checked),
        pointer_sha256=hashlib.sha256(pointer_bytes).hexdigest(),
        manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
    )
