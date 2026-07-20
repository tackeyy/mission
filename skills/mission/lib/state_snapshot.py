"""Explicit, short-lived, fail-closed state snapshot documents."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mission_common import parse_iso_datetime, state_identity


SNAPSHOT_SCHEMA = "mission-state-snapshot/1"
CLI_COMPATIBILITY = "mission-state-snapshot-cli/1"
RECORD_CONTRACT = "mission-state-records-unfiltered/1"
DISCOVERY_CONTRACT = "mission-state-discovery-fingerprint/1"
DEDUPE_CONTRACT = "filter-before-dedupe/1"
DEFAULT_TTL_SECONDS = 300
PRUNE_DIRS = frozenset({
    ".git", ".next", ".pytest_cache", ".venv", "__pycache__", "build",
    "dist", "node_modules", "target", "vendor", "venv",
})


class SnapshotError(ValueError):
    """Raised when a snapshot cannot be trusted or reused."""


def parse_snapshot_bytes(payload: bytes) -> Any:
    """Named seam for deterministic snapshot parse accounting."""
    return json.loads(payload.decode("utf-8"))


def normalize_roots(roots: list[Path]) -> list[str]:
    return [str(Path(root).expanduser().resolve(strict=False)) for root in roots]


def canonical_digest(document: dict[str, Any]) -> str:
    core = {key: value for key, value in document.items() if key != "content_digest"}
    encoded = json.dumps(
        core, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def value_digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def discovery_digest(index: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        index, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _metadata_entry(source_path: Path, identity: list[Any]) -> list[Any]:
    absolute = Path(source_path).expanduser().absolute()
    try:
        path_stat = absolute.lstat()
    except FileNotFoundError:
        return [*identity, "missing"]
    except OSError as error:
        return [*identity, "error", error.errno]
    common = [
        path_stat.st_dev,
        path_stat.st_ino,
        path_stat.st_mode,
        path_stat.st_size,
        path_stat.st_mtime_ns,
        path_stat.st_ctime_ns,
    ]
    if stat.S_ISLNK(path_stat.st_mode):
        try:
            target = os.readlink(absolute)
        except OSError:
            target = ""
        return [*identity, "symlink", *common, target]
    if stat.S_ISDIR(path_stat.st_mode):
        kind = "directory"
    elif stat.S_ISREG(path_stat.st_mode):
        kind = "file"
    else:
        kind = "other"
    return [*identity, kind, *common]


def root_metadata_inventory(roots: list[Path]) -> list[list[Any]]:
    """Return compact metadata-only discovery inventory for ordered roots."""
    inventory: list[list[Any]] = []
    for root_index, root_value in enumerate(roots):
        root = Path(root_value).expanduser().resolve(strict=False)
        if not root.exists():
            inventory.append(_metadata_entry(root, ["root", root_index, "."]))
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            directory = Path(dirpath)
            relative = directory.relative_to(root)
            inside_state = ".mission-state" in relative.parts
            if not inside_state:
                dirnames[:] = sorted(name for name in dirnames if name not in PRUNE_DIRS)
            else:
                dirnames[:] = sorted(dirnames)
            inventory.append(_metadata_entry(
                directory, ["root", root_index, relative.as_posix()]
            ))
            retained_dirs: list[str] = []
            for name in dirnames:
                child = directory / name
                if child.is_symlink():
                    inventory.append(_metadata_entry(
                        child, ["root", root_index, (relative / name).as_posix()]
                    ))
                else:
                    retained_dirs.append(name)
            dirnames[:] = retained_dirs
            if inside_state:
                for name in sorted(filenames):
                    inventory.append(_metadata_entry(
                        directory / name,
                        ["root", root_index, (relative / name).as_posix()],
                    ))
    return inventory


def external_evidence_inventory(paths: list[Path]) -> list[list[Any]]:
    return [
        _metadata_entry(path, ["evidence", str(path.expanduser().absolute())])
        for path in paths
    ]


def record_source_inventory(path: Path, roots: list[Path]) -> list[list[Any]]:
    """Return the root-relative metadata entries that prove record provenance."""
    absolute = Path(path).expanduser().absolute()
    entries: list[list[Any]] = []
    for root_index, root_value in enumerate(roots):
        root = Path(root_value).expanduser().resolve(strict=False)
        try:
            relative = absolute.relative_to(root)
        except ValueError:
            continue
        if ".mission-state" not in relative.parts:
            continue
        entries.append(_metadata_entry(
            absolute, ["root", root_index, relative.as_posix()]
        ))
    return entries


def record_index(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index: list[dict[str, Any]] = []
    for item in records:
        state = item.get("state")
        path = item.get("path")
        source_inventory = item.get("source_inventory")
        if (
            not isinstance(state, dict)
            or not isinstance(path, str)
            or not path
            or not isinstance(source_inventory, list)
        ):
            raise SnapshotError("record payload is invalid")
        identity = state_identity(state, Path(path).stem, path)
        index.append({
            "path": path,
            "identity": list(identity),
            "state_sha256": value_digest(state),
            "source_inventory": source_inventory,
        })
    return index


def _normalized_state_reference(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if ".mission-state" not in path.parts:
        return None
    index = path.parts.index(".mission-state")
    return Path(*path.parts[index:]).as_posix()


def _expected_archive_lineage(
    state: dict[str, Any], state_path: Path
) -> Counter[tuple[str, int, str]] | None:
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

    if not add("state", iteration, f".mission-state/sessions/{state_path.name}"):
        return None
    if state.get("assumptions_path") and not add(
        "assumptions", iteration, state.get("assumptions_path")
    ):
        return None
    artifact = state.get("artifact") if isinstance(state.get("artifact"), dict) else {}
    if artifact.get("path") and not add("artifact", iteration, artifact.get("path")):
        return None
    score_history = state.get("score_history") or []
    if not isinstance(score_history, list):
        return None
    for entry in score_history:
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
    invocations = state.get("specialist_invocations") or []
    if not isinstance(invocations, list):
        return None
    for invocation in invocations:
        if not isinstance(invocation, dict) or not invocation.get("evidence_path"):
            continue
        item_iteration = invocation.get("iteration")
        if (
            not isinstance(item_iteration, int)
            or isinstance(item_iteration, bool)
            or item_iteration < 0
        ):
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
    return expected


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_archive_payload(
    *,
    record: dict[str, Any],
    validation_ref: str,
    payload: Any,
) -> None:
    if not isinstance(payload, dict):
        raise SnapshotError("snapshot archive validation payload is invalid")
    path = Path(record["path"])
    state = record["state"]
    bundle_value = record.get("archive_bundle")
    root_value = record.get("archive_root")
    generation = record.get("archive_generation")
    if (
        not isinstance(bundle_value, str)
        or not isinstance(root_value, str)
        or not _is_sha256(generation)
    ):
        raise SnapshotError("snapshot archive record provenance is invalid")
    bundle = Path(bundle_value)
    root = Path(root_value)
    if (
        not bundle.is_absolute()
        or not root.is_absolute()
        or bundle.name.startswith("worktree-") is False
        or bundle.parent.name != "archive"
        or bundle.parent.parent.name != ".mission-state"
        or root != bundle / "generations" / generation
        or validation_ref != "|".join((str(bundle), generation, str(root)))
    ):
        raise SnapshotError("snapshot archive record relationship is invalid")
    try:
        path.relative_to(root)
    except ValueError as error:
        raise SnapshotError("snapshot archive state is outside its generation") from error
    if (
        payload.get("bundle") != str(bundle)
        or payload.get("root") != str(root)
        or payload.get("generation") != generation
        or payload.get("pointer_path") != str(bundle / "current.json")
        or payload.get("manifest_path") != str(root / "manifest.json")
        or payload.get("state_paths") != [str(path)]
        or payload.get("state_sha256") != value_digest(state)
        or not _is_sha256(payload.get("pointer_sha256"))
        or not _is_sha256(payload.get("manifest_sha256"))
    ):
        raise SnapshotError("snapshot archive validation relationship is invalid")
    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise SnapshotError("snapshot archive validation evidence is invalid")
    required = {
        "session_id", "mission_id", "iteration", "evidence_kind",
        "source_reference", "archive_path", "size", "sha256", "path",
    }
    actual_lineage: Counter[tuple[str, int, str]] = Counter()
    seen_archive_paths: set[str] = set()
    state_items: list[dict[str, Any]] = []
    for item in evidence:
        if not isinstance(item, dict) or not required.issubset(item):
            raise SnapshotError("snapshot archive validation evidence is malformed")
        iteration = item.get("iteration")
        kind = item.get("evidence_kind")
        source_reference = item.get("source_reference")
        archive_path_value = item.get("archive_path")
        size = item.get("size")
        if (
            item.get("session_id") != state.get("session_id")
            or item.get("mission_id") != state.get("mission_id")
            or not isinstance(iteration, int)
            or isinstance(iteration, bool)
            or not isinstance(kind, str)
            or _normalized_state_reference(source_reference) != source_reference
            or not isinstance(archive_path_value, str)
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not _is_sha256(item.get("sha256"))
        ):
            raise SnapshotError("snapshot archive validation evidence identity is invalid")
        archive_path = Path(archive_path_value)
        if (
            archive_path.is_absolute()
            or ".." in archive_path.parts
            or archive_path.as_posix() != archive_path_value
            or archive_path_value in seen_archive_paths
            or item.get("path") != str(root / archive_path)
        ):
            raise SnapshotError("snapshot archive validation evidence path is invalid")
        seen_archive_paths.add(archive_path_value)
        actual_lineage[(kind, iteration, source_reference)] += 1
        if kind == "state":
            state_items.append(item)
    expected_lineage = _expected_archive_lineage(state, path)
    if expected_lineage is None or actual_lineage != expected_lineage:
        raise SnapshotError("snapshot archive validation lineage is invalid")
    try:
        state_archive_path = path.relative_to(root).as_posix()
    except ValueError as error:
        raise SnapshotError("snapshot archive state path is invalid") from error
    if len(state_items) != 1 or state_items[0]["archive_path"] != state_archive_path:
        raise SnapshotError("snapshot archive state evidence is invalid")


def validate_snapshot_semantics(
    document: dict[str, Any], current_inventory: list[list[Any]]
) -> None:
    """Validate record provenance and archived semantic relationships."""
    inventory = {json.dumps(item, separators=(",", ":")) for item in current_inventory}
    validations = document["archive_validations"]
    referenced: set[str] = set()
    for record in document["records"]:
        if not isinstance(record, dict):
            raise SnapshotError("snapshot record payload is invalid")
        path = record.get("path")
        state = record.get("state")
        source_inventory = record.get("source_inventory")
        if (
            not isinstance(path, str)
            or not Path(path).is_absolute()
            or not isinstance(state, dict)
            or not isinstance(source_inventory, list)
            or not source_inventory
            or any(
                not isinstance(item, list)
                or json.dumps(item, separators=(",", ":")) not in inventory
                for item in source_inventory
            )
        ):
            raise SnapshotError("snapshot record provenance is invalid")
        ref = record.get("archive_validation_ref")
        archive_values = (
            record.get("archive_bundle"), record.get("archive_root"),
            record.get("archive_generation"),
        )
        if ref is None:
            if archive_values == (None, None, None):
                continue
            if not (
                isinstance(archive_values[0], str)
                and archive_values[1] == archive_values[0]
                and archive_values[2] is None
            ):
                raise SnapshotError("snapshot legacy archive provenance is invalid")
            continue
        if not isinstance(ref, str) or ref not in validations:
            raise SnapshotError("snapshot archive validation reference is invalid")
        _validate_archive_payload(
            record=record, validation_ref=ref, payload=validations[ref]
        )
        referenced.add(ref)
    if referenced != set(validations):
        raise SnapshotError("snapshot archive validation references are inconsistent")


def build_snapshot_document(
    *,
    roots: list[Path],
    records: list[dict[str, Any]],
    invalid_worktree_archives: list[dict[str, Any]],
    discovery_index: list[dict[str, Any]],
    observed_at: datetime,
    ttl_seconds: int,
    created_at: datetime | None = None,
    archive_validations: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
        raise SnapshotError("snapshot TTL must be a positive integer")
    observed = observed_at
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    created = created_at or datetime.now(timezone.utc)
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    document: dict[str, Any] = {
        "schema": SNAPSHOT_SCHEMA,
        "cli_compatibility": CLI_COMPATIBILITY,
        "record_contract": RECORD_CONTRACT,
        "discovery_contract": DISCOVERY_CONTRACT,
        "dedupe_contract": DEDUPE_CONTRACT,
        "created_at": created.astimezone(timezone.utc).isoformat(),
        "observed_at": observed.astimezone(timezone.utc).isoformat(),
        "ttl_seconds": ttl_seconds,
        "roots": normalize_roots(roots),
        "record_count": len(records),
        "discovery_count": len(discovery_index),
        "records": records,
        "archive_validations": archive_validations or {},
        "record_index": record_index(records),
        "invalid_worktree_archives": invalid_worktree_archives,
        "external_evidence_paths": [
            item[1]
            for item in discovery_index
            if isinstance(item, list)
            and len(item) >= 2
            and item[0] == "evidence"
            and isinstance(item[1], str)
        ],
        "discovery_digest": discovery_digest(discovery_index),
    }
    document["content_digest"] = canonical_digest(document)
    return document


def write_snapshot(path: Path, document: dict[str, Any]) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def read_snapshot(
    path: Path,
    *,
    requested_roots: list[Path] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    source = Path(path).expanduser()
    try:
        source_stat = source.lstat()
    except OSError as error:
        raise SnapshotError(f"snapshot is not accessible: {error}") from error
    if stat.S_ISLNK(source_stat.st_mode) or not stat.S_ISREG(source_stat.st_mode):
        raise SnapshotError("snapshot must be a regular non-symlink file")
    if stat.S_IMODE(source_stat.st_mode) & 0o077:
        raise SnapshotError("snapshot permissions must not grant group/world access")
    try:
        document = parse_snapshot_bytes(source.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SnapshotError(f"snapshot JSON is invalid: {error}") from error
    if not isinstance(document, dict):
        raise SnapshotError("snapshot document must be an object")
    expected_contracts = {
        "schema": SNAPSHOT_SCHEMA,
        "cli_compatibility": CLI_COMPATIBILITY,
        "record_contract": RECORD_CONTRACT,
        "discovery_contract": DISCOVERY_CONTRACT,
        "dedupe_contract": DEDUPE_CONTRACT,
    }
    for key, expected in expected_contracts.items():
        if document.get(key) != expected:
            raise SnapshotError(f"snapshot {key} is incompatible")
    if document.get("content_digest") != canonical_digest(document):
        raise SnapshotError("snapshot content digest mismatch")
    records = document.get("records")
    stored_index = document.get("record_index")
    external_evidence_paths = document.get("external_evidence_paths")
    invalid_archives = document.get("invalid_worktree_archives")
    archive_validations = document.get("archive_validations")
    if not isinstance(records, list) or not isinstance(stored_index, list):
        raise SnapshotError("snapshot record collection is invalid")
    if (
        not isinstance(external_evidence_paths, list)
        or not all(isinstance(path, str) for path in external_evidence_paths)
        or not isinstance(invalid_archives, list)
        or not isinstance(archive_validations, dict)
    ):
        raise SnapshotError("snapshot discovery collection is invalid")
    if document.get("record_count") != len(records) or stored_index != record_index(records):
        raise SnapshotError("snapshot record count/index mismatch")
    if (
        not isinstance(document.get("discovery_count"), int)
        or isinstance(document.get("discovery_count"), bool)
        or document.get("discovery_count") < 0
        or not isinstance(document.get("discovery_digest"), str)
        or len(document.get("discovery_digest")) != 64
    ):
        raise SnapshotError("snapshot discovery count/index mismatch")
    roots = document.get("roots")
    if not isinstance(roots, list) or not all(isinstance(root, str) for root in roots):
        raise SnapshotError("snapshot roots are invalid")
    if requested_roots is not None and normalize_roots(requested_roots) != roots:
        raise SnapshotError("snapshot roots do not match the requested ordered multiset")
    created_at = parse_iso_datetime(document.get("created_at"))
    observed_at = parse_iso_datetime(document.get("observed_at"))
    ttl_seconds = document.get("ttl_seconds")
    if (
        created_at is None
        or created_at.tzinfo is None
        or observed_at is None
        or observed_at.tzinfo is None
        or isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or ttl_seconds <= 0
    ):
        raise SnapshotError("snapshot time/TTL metadata is invalid")
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    age = (base.astimezone(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds()
    if age < 0 or age > ttl_seconds:
        raise SnapshotError("snapshot is expired or from the future")
    return document


def consume_snapshot_document(
    path: Path, *, requested_roots: list[Path] | None,
    root_inventory_loader=None,
    evidence_inventory_loader=None,
) -> tuple[dict[str, Any], list[Path], datetime]:
    """Validate one snapshot with one metadata-only rewalk."""
    document = read_snapshot(path, requested_roots=requested_roots)
    roots = [Path(root) for root in document["roots"]]
    evidence_paths = [Path(value) for value in document["external_evidence_paths"]]
    root_loader = root_inventory_loader or root_metadata_inventory
    evidence_loader = evidence_inventory_loader or external_evidence_inventory
    index = root_loader(roots) + evidence_loader(evidence_paths)
    if (
        len(index) != document["discovery_count"]
        or discovery_digest(index) != document["discovery_digest"]
    ):
        raise SnapshotError("snapshot discovery fingerprint is stale")
    validate_snapshot_semantics(document, index)
    observed_at = parse_iso_datetime(document["observed_at"])
    if observed_at is None or observed_at.tzinfo is None:
        raise SnapshotError("snapshot observed_at is invalid")
    return document, roots, observed_at.astimezone(timezone.utc)
