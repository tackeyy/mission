"""Shared fail-closed validation for immutable worktree archive generations."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

WORKTREE_ARCHIVE_SCHEMA = "mission-worktree-archive/1"
WORKTREE_ARCHIVE_POINTER_SCHEMA = "mission-worktree-current/1"
STATE_ARCHIVE_GENERATION_SCHEMA = "mission-state-archive-generation/1"
STATE_ARCHIVE_POINTER_SCHEMA = "mission-state-archive-current/1"
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
    checkout_root: Path | None = None


@dataclass(frozen=True)
class StateArchiveCompaction:
    generation: str
    manifest_sha256: str
    previous_generation: str | None
    retention_generations: int
    records: tuple[dict[str, Any], ...]

    @property
    def superseded_paths(self) -> frozenset[str]:
        return frozenset(
            item["path"]
            for record in self.records
            for item in record["superseded"]
        )


@dataclass(frozen=True)
class TypedScoreEvidenceBinding:
    evidence_kind: str
    iteration: int
    source_reference: str
    reference: dict[str, Any]
    expected_kind: str


def resolve_repo_artifact_reference(cwd: object, reference: str) -> str:
    """Return the exact repository-relative path later passed to the Git reader."""
    candidate = Path(reference).expanduser()
    if not candidate.is_absolute():
        return reference
    try:
        relative = candidate.resolve(strict=False).relative_to(
            Path(cwd).resolve(strict=False)
        )
    except ValueError as exc:
        raise ValueError(
            f"required evidence is outside .mission-state: artifact: {reference}"
        ) from exc
    return relative.as_posix()


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


def _git_object_id(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{40}([0-9a-f]{24})?", value))


def _git_bytes(cwd: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise ValueError("git evidence is unavailable")
    return result.stdout


def _git_text(cwd: Path, *args: str) -> str:
    return _git_bytes(cwd, *args).decode("utf-8").strip()


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
        metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns,
    )


def _git_repo_artifact_bytes(
    checkout_root: Path, *, path: str, head_sha: str,
) -> bytes:
    if not _git_object_id(head_sha):
        raise ValueError("archive repo artifact head sha is invalid")
    if not _safe_relative_path(path):
        raise ValueError("archive repo artifact path is invalid")
    return _git_bytes(checkout_root, "show", f"{head_sha}:{path}")


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


def read_state_archive_file_bytes(
    project_root: Path, reference: str, *, limit: int = 4 * 1024 * 1024,
) -> bytes:
    """Read one state-owned archive input with the consumer's strict contract."""
    relative = _safe_relative_path(reference, state_reference=True)
    if relative is None:
        raise ValueError("state archive reference is invalid")
    content, _metadata = _read_generation_file(project_root, relative, limit=limit)
    return content


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


def _state_archive_core(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": document.get("schema"),
        "previous_generation": document.get("previous_generation"),
        "retention_policy": document.get("retention_policy"),
        "records": document.get("records"),
    }


def state_archive_content_digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            _state_archive_core(document),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _state_archive_path(value: Any) -> str | None:
    path = _safe_relative_path(value, state_reference=True)
    return path.as_posix() if path is not None else None


def read_state_archive_compaction(
    state_root: Path, *, verify_superseded: bool = False,
) -> StateArchiveCompaction | None:
    """Read the current state-archive generation through the shared no-follow reader."""
    compaction = state_root / "archive" / "compaction"
    pointer_path = compaction / "current.json"
    try:
        pointer_path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError("state archive pointer is unavailable") from exc
    try:
        pointer_bytes, _pointer_metadata = _read_generation_file(
            compaction, Path("current.json"), limit=64 * 1024,
        )
        pointer = json.loads(
            pointer_bytes.decode("utf-8"), object_pairs_hook=_strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, OSError, ValueError) as exc:
        raise ValueError("state archive pointer is invalid") from exc
    generation = pointer.get("generation") if isinstance(pointer, dict) else None
    manifest_sha256 = pointer.get("manifest_sha256") if isinstance(pointer, dict) else None
    if (
        not isinstance(pointer, dict)
        or set(pointer) != {"schema", "generation", "manifest_sha256"}
        or pointer.get("schema") != STATE_ARCHIVE_POINTER_SCHEMA
        or not isinstance(generation, str)
        or not re.fullmatch(r"[0-9a-f]{64}", generation)
        or not isinstance(manifest_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256)
    ):
        raise ValueError("state archive pointer schema is invalid")
    relative_manifest = Path("generations") / generation / "manifest.json"
    try:
        manifest_bytes, _manifest_metadata = _read_generation_file(
            compaction, relative_manifest, limit=4 * 1024 * 1024,
        )
        manifest = json.loads(
            manifest_bytes.decode("utf-8"), object_pairs_hook=_strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, OSError, ValueError) as exc:
        raise ValueError("state archive generation manifest is invalid") from exc
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
        raise ValueError("state archive manifest digest mismatch")
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {
            "schema", "created_at", "previous_generation", "retention_policy",
            "records", "content_digest",
        }
        or manifest.get("schema") != STATE_ARCHIVE_GENERATION_SCHEMA
        or manifest.get("content_digest") != generation
        or state_archive_content_digest(manifest) != generation
    ):
        raise ValueError("state archive generation schema is invalid")
    previous = manifest.get("previous_generation")
    if previous is not None and (
        not isinstance(previous, str) or not re.fullmatch(r"[0-9a-f]{64}", previous)
    ):
        raise ValueError("state archive previous generation is invalid")
    retention = manifest.get("retention_policy")
    if (
        not isinstance(retention, dict)
        or set(retention) != {"retain_generations", "physical_deletion"}
        or not isinstance(retention.get("retain_generations"), int)
        or isinstance(retention["retain_generations"], bool)
        or retention["retain_generations"] < 1
        or retention.get("physical_deletion") != "forbidden"
    ):
        raise ValueError("state archive retention policy is invalid")
    records = manifest.get("records")
    if not isinstance(records, list):
        raise ValueError("state archive records are invalid")
    canonical_seen: set[str] = set()
    superseded_seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "canonical_path", "canonical_sha256", "mission_id", "session_id", "superseded",
        }:
            raise ValueError("state archive record schema is invalid")
        canonical = _state_archive_path(record.get("canonical_path"))
        canonical_sha256 = record.get("canonical_sha256")
        superseded = record.get("superseded")
        if (
            canonical is None
            or canonical in canonical_seen
            or not isinstance(canonical_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", canonical_sha256)
            or not isinstance(record.get("mission_id"), str)
            or not record["mission_id"]
            or not isinstance(record.get("session_id"), str)
            or not record["session_id"]
            or not isinstance(superseded, list)
            or not superseded
        ):
            raise ValueError("state archive canonical record is invalid")
        canonical_seen.add(canonical)
        try:
            canonical_bytes, _canonical_metadata = _read_generation_file(
                state_root.parent, Path(canonical), limit=4 * 1024 * 1024,
            )
        except ValueError as exc:
            raise ValueError("state archive canonical record is unavailable") from exc
        if hashlib.sha256(canonical_bytes).hexdigest() != canonical_sha256:
            raise ValueError("state archive canonical record digest mismatch")
        validated_superseded: list[dict[str, str]] = []
        for item in superseded:
            path = _state_archive_path(item.get("path")) if isinstance(item, dict) else None
            digest = item.get("sha256") if isinstance(item, dict) else None
            if (
                not isinstance(item, dict)
                or set(item) != {"path", "sha256"}
                or path is None
                or path == canonical
                or path in superseded_seen
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise ValueError("state archive superseded record is invalid")
            superseded_seen.add(path)
            if verify_superseded:
                try:
                    superseded_bytes, _superseded_metadata = _read_generation_file(
                        state_root.parent, Path(path), limit=4 * 1024 * 1024,
                    )
                except ValueError as exc:
                    raise ValueError("state archive superseded record is unavailable") from exc
                if hashlib.sha256(superseded_bytes).hexdigest() != digest:
                    raise ValueError("state archive superseded record digest mismatch")
            validated_superseded.append({"path": path, "sha256": digest})
        validated.append({**record, "canonical_path": canonical, "superseded": validated_superseded})
    return StateArchiveCompaction(
        generation=generation,
        manifest_sha256=manifest_sha256,
        previous_generation=previous,
        retention_generations=retention["retain_generations"],
        records=tuple(validated),
    )


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


def read_verified_content_addressed_evidence(
    root: Path, reference: object, *, expected_kind: str
) -> bytes:
    """Read one typed score reference and bind its digest and declared size."""

    if not isinstance(reference, dict) or reference.get("kind") != expected_kind:
        raise ValueError("content-addressed evidence reference schema is invalid")
    relative = _safe_relative_path(reference.get("path"), state_reference=True)
    digest = reference.get("digest")
    size = reference.get("size")
    if (
        relative is None
        or not isinstance(digest, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
    ):
        raise ValueError("content-addressed evidence reference schema is invalid")
    content, _metadata = _read_generation_file(
        root, relative, limit=REVIEW_INPUT_MAX_BYTES, expected_size=size,
    )
    if "sha256:" + hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("content-addressed evidence integrity mismatch")
    return content


def worktree_archive_typed_score_bindings(
    state: dict[str, Any],
) -> tuple[TypedScoreEvidenceBinding, ...]:
    """Project complete typed score refs for writer and validator rebinding."""

    bindings: list[TypedScoreEvidenceBinding] = []
    reference_specs = {
        "scoring-json": (
            ("review_evidence_ref", "review-aggregate", "review-aggregate"),
        ),
        "manual-import": (
            ("manual_evidence_ref", "manual-score-source", "manual-score"),
        ),
    }
    for entry in state.get("score_history") or []:
        if not isinstance(entry, dict):
            continue
        iteration = entry.get("iteration")
        provenance = (
            entry.get("score_provenance")
            if isinstance(entry.get("score_provenance"), dict)
            else {}
        )
        specs = list(reference_specs.get(provenance.get("score_source"), ()))
        specs.append(("scoring_evidence_ref", "scoring-artifact", "scoring-artifact"))
        for key, evidence_kind, expected_kind in specs:
            reference = provenance.get(key)
            if not isinstance(reference, dict):
                continue
            complete = all(
                field in reference for field in ("kind", "path", "digest", "size")
            )
            if not complete:
                # Historical v4 references are path-only or omit size.
                continue
            source_reference = _normalized_state_reference(reference.get("path"))
            if (
                not isinstance(iteration, int)
                or isinstance(iteration, bool)
                or iteration < 0
                or source_reference is None
                or reference.get("kind") != expected_kind
                or not isinstance(reference.get("digest"), str)
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", reference["digest"])
                or not isinstance(reference.get("size"), int)
                or isinstance(reference.get("size"), bool)
                or reference["size"] < 0
                or (
                    reference.get("iteration") is not None
                    and reference.get("iteration") != iteration
                )
            ):
                raise ValueError("typed score evidence binding is invalid")
            bindings.append(TypedScoreEvidenceBinding(
                evidence_kind=evidence_kind,
                iteration=iteration,
                source_reference=source_reference,
                reference=reference,
                expected_kind=expected_kind,
            ))
    return tuple(bindings)


def worktree_archive_lineage_references(
    state: dict[str, Any], state_reference: str, *, repo_root: Path | None = None,
    verify_repo_artifact_tracking: bool = True,
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
        ):
            return False
        if normalized is not None:
            expected.append((kind, item_iteration, normalized))
            return True
        if repo_root is not None and kind == "artifact":
            repo_relative = _safe_relative_path(reference)
            if repo_relative is None:
                return False
            expected.append((kind, item_iteration, repo_relative.as_posix()))
            return True
        return False

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
    artifact_path = artifact.get("path")
    if artifact_path:
        normalized_artifact = _normalized_state_reference(artifact_path)
        if normalized_artifact is not None:
            if not add("artifact", iteration, artifact_path):
                return None
        else:
            if repo_root is None:
                return None
            artifact_candidate = Path(artifact_path).expanduser()
            if artifact_candidate.is_absolute():
                repo_candidate = artifact_candidate.resolve(strict=False)
                repo_root_resolved = repo_root.resolve(strict=False)
                try:
                    repo_relative = repo_candidate.relative_to(repo_root_resolved)
                except ValueError:
                    return None
            else:
                repo_relative = _safe_relative_path(artifact_path)
                if repo_relative is None:
                    return None
            if verify_repo_artifact_tracking:
                tracked = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(repo_root),
                        "ls-files",
                        "--error-unmatch",
                        "--",
                        repo_relative.as_posix(),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if tracked.returncode != 0:
                    return None
            if not add("artifact", iteration, repo_relative.as_posix()):
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


def _expected_lineage(state: dict[str, Any], state_path: Path, *, repo_root: Path | None = None):
    references = worktree_archive_lineage_references(
        state, f".mission-state/sessions/{state_path.name}",
        repo_root=repo_root,
        verify_repo_artifact_tracking=False,
    )
    return Counter(references) if references is not None else None


def read_validated_archive_evidence(
    validation: WorktreeArchiveValidation, source_reference: object,
    *, limit: int = 4 * 1024 * 1024,
) -> bytes:
    """Read an evidence copy only after resolving it through a valid manifest."""
    if validation.status != "valid" or not isinstance(source_reference, str) or not source_reference:
        raise ValueError("archive evidence resolver is invalid")
    normalized = _normalized_state_reference(source_reference)
    matches = [item for item in validation.evidence if item.get("source_reference") == source_reference]
    if not matches and normalized is not None:
        matches = [item for item in validation.evidence if item.get("source_reference") == normalized]
    if not matches or len({item.get("sha256") for item in matches}) != 1:
        raise ValueError("archive evidence reference is missing or ambiguous")
    item = matches[0]
    kind = item.get("kind") if isinstance(item.get("kind"), str) else item.get("evidence_kind")
    if kind == "repo-artifact":
        checkout_root = validation.checkout_root
        path = item.get("path")
        head_sha = item.get("head_sha")
        digest = item.get("digest")
        if (
            checkout_root is None
            or _safe_relative_path(path) is None
            or not _git_object_id(head_sha)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError("archive evidence resolver is invalid")
        try:
            content = _git_repo_artifact_bytes(
                checkout_root, path=path, head_sha=head_sha,
            )
        except ValueError as exc:
            raise ValueError("archive evidence is unavailable") from exc
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("archive evidence integrity mismatch")
        return content
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


def read_validated_archive_authoritative_snapshot(
    validation: WorktreeArchiveValidation,
):
    """Read the single manifest-bound state through the sealed-bytes reader."""

    if (
        validation.status != "valid"
        or validation.state is None
        or len(validation.state_paths) != 1
    ):
        raise ValueError("validated archive state is unavailable")
    expected_session_id = validation.state.get("session_id")
    if not isinstance(expected_session_id, str) or not expected_session_id:
        raise ValueError("validated archive session identity is invalid")
    state_path = validation.state_paths[0]
    state_items = [
        item for item in validation.evidence
        if item.get("evidence_kind") == "state" and item.get("path") == state_path
    ]
    if len(state_items) != 1:
        raise ValueError("validated archive state binding is invalid")
    from mission_persistence.authoritative_reader import (
        authoritative_snapshot_from_validated_archive_bytes,
    )
    from mission_persistence.strict_reader import STATE_LIMIT, read_stable_bytes

    source = read_stable_bytes(state_path, limit=STATE_LIMIT)
    item = state_items[0]
    if (
        len(source) != item.get("size")
        or hashlib.sha256(source).hexdigest() != item.get("sha256")
    ):
        raise ValueError("validated archive state bytes drifted")
    snapshot = authoritative_snapshot_from_validated_archive_bytes(
        source, expected_session_id=expected_session_id
    )
    if snapshot.document_copy() != validation.state:
        raise ValueError("validated archive state projection drifted")
    return snapshot


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
        if source_reference is None:
            source_reference = _safe_relative_path(item.get("path"))
        if source_reference is None:
            return _invalid(bundle, generation_root, "manifest-unsafe-or-duplicate-path", generation)
        if item.get("kind") == "repo-artifact":
            digest = item.get("digest")
            head_sha = item.get("head_sha")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or not _git_object_id(head_sha)
                or item.get("archive_path") is not None
                or item.get("sha256") is not None
                or item.get("size") is not None
            ):
                return _invalid(bundle, generation_root, "manifest-invalid-evidence-file", generation)
            try:
                content = _git_repo_artifact_bytes(
                    bundle.parent.parent.parent,
                    path=source_reference.as_posix(),
                    head_sha=head_sha,
                )
            except ValueError:
                return _invalid(bundle, generation_root, "manifest-evidence-access-error", generation)
            if hashlib.sha256(content).hexdigest() != digest:
                return _invalid(bundle, generation_root, "manifest-evidence-integrity-mismatch", generation)
            checked.append({
                **item,
                "source_reference": source_reference.as_posix(),
                "path": source_reference.as_posix(),
            })
            continue
        archive_path = _safe_relative_path(item.get("archive_path"))
        if archive_path is None or archive_path.as_posix() in seen_paths:
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
        from mission_persistence.authoritative_reader import (
            authoritative_snapshot_from_validated_archive_bytes,
        )

        state_archive_path = state_paths[0].relative_to(generation_root).as_posix()
        state = authoritative_snapshot_from_validated_archive_bytes(
            state_payloads[state_archive_path],
            expected_session_id=manifest.get("session_id"),
        ).document_copy()
    except (KeyError, OSError, ValueError):
        return _invalid(bundle, generation_root, "manifest-state-invalid-json", generation)
    if (
        not isinstance(state, dict)
        or state.get("session_id") != manifest.get("session_id")
        or state.get("mission_id") != manifest.get("mission_id")
        or state.get("iteration") != manifest.get("iteration")
    ):
        return _invalid(bundle, generation_root, "manifest-state-identity-mismatch", generation)
    expected_lineage = _expected_lineage(
        state, state_paths[0], repo_root=bundle.parent.parent.parent,
    )
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
        if item["evidence_kind"] == "repo-artifact":
            continue
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
    try:
        typed_score_bindings = worktree_archive_typed_score_bindings(state)
    except ValueError:
        return _invalid(
            bundle, generation_root,
            "manifest-score-reference-invalid", generation,
        )
    for binding in typed_score_bindings:
        matches = [
            item for item in checked
            if item.get("evidence_kind") == binding.evidence_kind
            and item.get("iteration") == binding.iteration
            and item.get("source_reference") == binding.source_reference
        ]
        if len(matches) != 1 or matches[0].get("kind") == "repo-artifact":
            return _invalid(
                bundle, generation_root,
                "manifest-score-reference-invalid", generation,
            )
        archive_path = matches[0].get("archive_path")
        content = evidence_payloads.get(archive_path)
        if (
            content is None
            or len(content) != binding.reference["size"]
            or "sha256:" + hashlib.sha256(content).hexdigest()
            != binding.reference["digest"]
        ):
            return _invalid(
                bundle, generation_root,
                "manifest-score-reference-integrity-mismatch", generation,
            )
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
        checkout_root=bundle.parent.parent.parent,
    )
