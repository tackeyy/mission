"""Portable artifact identity capture and validation.

Artifact bytes are read once through a bounded, non-blocking, non-symlink file
descriptor.  Both producers and consumers use the same functions so state never
claims a different object than the one that was reviewed.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path, PurePosixPath


ARTIFACT_MAX_BYTES = 4 * 1024 * 1024
ARTIFACT_APPLICABILITIES = frozenset({"producing", "not-applicable", "pending"})
DEFAULT_COVERAGE_THRESHOLD = 0.95
ARTIFACT_IDENTITY_FIELDS = ("path", "digest", "size", "producer_run_id")


class ArtifactContractError(ValueError):
    """The artifact path, bytes, or recorded identity is invalid."""


def canonical_artifact_identity_snapshot(state: dict) -> dict | None:
    """Return the complete canonical identity used to bind an observation."""
    artifact = state.get("artifact")
    if not isinstance(artifact, dict) or not all(
        field in artifact for field in ARTIFACT_IDENTITY_FIELDS
    ):
        return None
    snapshot = {field: artifact[field] for field in ARTIFACT_IDENTITY_FIELDS}
    path = snapshot["path"]
    digest = snapshot["digest"]
    size = snapshot["size"]
    producer_run_id = snapshot["producer_run_id"]
    if not isinstance(path, str) or not path:
        return None
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        return None
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return None
    if not isinstance(producer_run_id, str) or not producer_run_id:
        return None
    return snapshot


def canonical_artifact_identity_present(state: dict) -> bool:
    """Whether state declares any canonical identity field, including malformed input."""
    artifact = state.get("artifact")
    return isinstance(artifact, dict) and any(
        field in artifact for field in ARTIFACT_IDENTITY_FIELDS
    )


def artifact_lint_observation_matches(state: dict) -> bool:
    """Whether the persisted lint observation describes the current identity."""
    current = canonical_artifact_identity_snapshot(state)
    observed = state.get("artifact_lint_identity")
    return current is not None and isinstance(observed, dict) and observed == current


def invalidate_artifact_lint_observation(state: dict) -> None:
    """Atomically remove lint data when a canonical producer changes identity."""
    state.pop("artifact_lint", None)
    state.pop("artifact_lint_status", None)
    state.pop("artifact_lint_identity", None)


def validate_artifact_state_consistency(
    state: dict, *, require_resolved: bool = False
) -> str | None:
    """Reject contradictory or unresolved canonical artifact control state."""
    applicability = state.get("artifact_applicability")
    if applicability is None:
        return None  # Legacy state: observe without physical migration.
    if applicability not in ARTIFACT_APPLICABILITIES:
        raise ArtifactContractError("artifact applicability is invalid")
    canonical_identity = canonical_artifact_identity_present(state)
    if applicability == "pending":
        if canonical_identity:
            raise ArtifactContractError(
                "pending artifact applicability contradicts canonical artifact identity"
            )
        if require_resolved:
            raise ArtifactContractError(
                "artifact applicability is pending; resolve it to producing or not-applicable"
            )
    if applicability == "not-applicable" and canonical_identity:
        raise ArtifactContractError(
            "not-applicable artifact applicability contradicts canonical artifact identity"
        )
    return applicability


def artifact_path_from_state(state: dict) -> tuple[str | None, bool]:
    """Return (path, canonical), preferring nested state.artifact.path."""
    artifact = state.get("artifact")
    if isinstance(artifact, dict) and "path" in artifact:
        path = artifact.get("path")
        return (path if isinstance(path, str) else None), True
    legacy = state.get("artifact_path")
    return (legacy if isinstance(legacy, str) else None), False


def _portable_relative_path(root: Path, path_text: str, *, canonical: bool) -> str:
    if not isinstance(path_text, str) or not path_text.strip():
        raise ArtifactContractError("artifact path is missing")
    if "\x00" in path_text:
        raise ArtifactContractError("artifact path contains NUL")
    raw = Path(path_text)
    root = root.resolve()
    if raw.is_absolute():
        if canonical:
            raise ArtifactContractError("canonical artifact.path must be repository-relative")
        try:
            raw = raw.relative_to(root)
        except ValueError as exc:
            raise ArtifactContractError("artifact path is outside project root") from exc
    pure = PurePosixPath(raw.as_posix())
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ArtifactContractError("artifact path must be a normalized repository-relative path")
    return pure.as_posix()


def _read_relative_regular_file(root: Path, relative_path: str) -> bytes:
    parts = PurePosixPath(relative_path).parts
    directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    file_flags |= getattr(os, "O_NONBLOCK", 0)
    file_flags |= getattr(os, "O_NOFOLLOW", 0)
    opened: list[int] = []
    try:
        current_fd = os.open(root.resolve(), directory_flags)
        opened.append(current_fd)
        for part in parts[:-1]:
            current_fd = os.open(part, directory_flags, dir_fd=current_fd)
            opened.append(current_fd)
        fd = os.open(parts[-1], file_flags, dir_fd=current_fd)
        opened.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ArtifactContractError("artifact path is not a regular file")
        chunks = []
        remaining = ARTIFACT_MAX_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > ARTIFACT_MAX_BYTES:
            raise ArtifactContractError(
                f"artifact exceeds size limit ({ARTIFACT_MAX_BYTES} bytes)"
            )
        after = os.fstat(fd)
        before_identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_identity != after_identity or len(payload) != after.st_size:
            raise ArtifactContractError("artifact changed while it was being read")
        return payload
    except (OSError, UnicodeError, ValueError, TypeError, RuntimeError) as exc:
        if isinstance(exc, ArtifactContractError):
            raise
        raise ArtifactContractError(str(exc)) from exc
    finally:
        for fd in reversed(opened):
            try:
                os.close(fd)
            except OSError:
                pass


def capture_artifact_identity(
    root: Path, path_text: str, producer_run_id: str, *, canonical: bool = True
) -> tuple[dict, bytes]:
    relative_path = _portable_relative_path(root, path_text, canonical=canonical)
    if not isinstance(producer_run_id, str) or not producer_run_id.strip():
        raise ArtifactContractError("producer_run_id is missing")
    payload = _read_relative_regular_file(root, relative_path)
    return (
        {
            "path": relative_path,
            "digest": hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
            "producer_run_id": producer_run_id.strip(),
        },
        payload,
    )


def validate_artifact_identity(state: dict, root: Path) -> tuple[dict, bytes]:
    path_text, canonical = artifact_path_from_state(state)
    if not path_text:
        raise ArtifactContractError("artifact path is missing")
    artifact = state.get("artifact") if isinstance(state.get("artifact"), dict) else {}
    if canonical:
        expected_digest = artifact.get("digest")
        expected_size = artifact.get("size")
        producer_run_id = artifact.get("producer_run_id")
        if not isinstance(expected_digest, str) or not expected_digest:
            raise ArtifactContractError("artifact digest is missing")
        if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0:
            raise ArtifactContractError("artifact size is invalid")
        identity, payload = capture_artifact_identity(
            root, path_text, producer_run_id, canonical=True
        )
        if identity["digest"] != expected_digest or identity["size"] != expected_size:
            raise ArtifactContractError("artifact identity does not match recorded state")
        return identity, payload

    # Legacy fallback is observational only: no physical state rewrite occurs.
    identity, payload = capture_artifact_identity(
        root,
        path_text,
        str(state.get("session_id") or "legacy"),
        canonical=False,
    )
    return identity, payload


def _terminal_outcome(state: dict) -> str | None:
    outcome = state.get("terminal_outcome")
    if isinstance(outcome, str) and outcome:
        return outcome
    if state.get("passes") is True:
        return "completed_pass"
    if state.get("loop_active") is False:
        return "failed"
    return None


def _profile_name(state: dict) -> str:
    profile = state.get("task_profile")
    primary = profile.get("primary") if isinstance(profile, dict) else None
    return primary.strip() if isinstance(primary, str) and primary.strip() else "unclassified"


def _empty_coverage_counts() -> dict:
    return {
        "eligible": 0,
        "observed": 0,
        "missing": 0,
        "invalid": 0,
        "clean": 0,
        "findings": 0,
        "skipped": 0,
    }


def _finalize_coverage_counts(counts: dict, threshold: float) -> dict:
    eligible = counts["eligible"]
    coverage = counts["observed"] / eligible if eligible else None
    return {
        "counts": counts,
        "coverage": coverage,
        "threshold": threshold,
        "gate_active": coverage is not None and coverage >= threshold,
        "counts_conserved": (
            counts["eligible"]
            == counts["observed"] + counts["missing"] + counts["invalid"]
            and counts["observed"] == counts["clean"] + counts["findings"]
        ),
    }


def summarize_artifact_coverage(
    states: list[dict], *, threshold: float = DEFAULT_COVERAGE_THRESHOLD
) -> dict:
    """Summarize terminal artifact observations without treating skips as clean."""
    counts = _empty_coverage_counts()
    profile_counts: dict[str, dict] = {}
    outcome_counts: dict[str, dict] = {}

    for state in states:
        if not isinstance(state, dict):
            continue
        outcome = _terminal_outcome(state)
        if outcome is None:
            continue
        profile = _profile_name(state)
        targets = [
            counts,
            profile_counts.setdefault(profile, _empty_coverage_counts()),
            outcome_counts.setdefault(outcome, _empty_coverage_counts()),
        ]
        applicability = state.get("artifact_applicability", "pending")
        canonical_identity = canonical_artifact_identity_present(state)
        if applicability == "not-applicable" and not canonical_identity:
            for target in targets:
                target["skipped"] += 1
            continue
        for target in targets:
            target["eligible"] += 1
        status = state.get("artifact_lint_status")
        if applicability == "not-applicable":
            bucket = "invalid"
        elif applicability == "pending":
            bucket = "invalid" if canonical_identity else "missing"
        elif applicability != "producing" or status in {"invalid", "skipped"}:
            bucket = "invalid"
        elif (
            status in {"clean", "findings"}
            and canonical_identity
            and not artifact_lint_observation_matches(state)
        ):
            bucket = "invalid"
        elif status in {"clean", "findings"}:
            bucket = status
        else:
            bucket = "missing"
        for target in targets:
            if bucket in {"clean", "findings"}:
                target["observed"] += 1
            target[bucket] += 1

    result = _finalize_coverage_counts(counts, threshold)
    result["by_profile"] = {
        name: _finalize_coverage_counts(value, threshold)
        for name, value in sorted(profile_counts.items())
    }
    result["by_terminal_outcome"] = {
        name: _finalize_coverage_counts(value, threshold)
        for name, value in sorted(outcome_counts.items())
    }
    return result
