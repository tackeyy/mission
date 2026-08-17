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
from worktree_archive import worktree_archive_lineage_references
from command_outcomes import validate_observation as validate_command_outcome_observation
from mission_persistence.authoritative_reader import (
    AuthoritativeSnapshot,
    expected_session_id_for_live_path,
    read_authoritative_snapshot,
)


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
AUDIT_SNAPSHOT_DIRECTORY = "audit-snapshots"
FALLBACK_AUDIT_SNAPSHOT_DIRECTORY = ".mission-audit-snapshots"
PRIVACY_SCHEMA = "mission-state-snapshot-privacy/1"


class SnapshotError(ValueError):
    """Raised when a snapshot cannot be trusted or reused."""


def read_authoritative_record(path: Path) -> AuthoritativeSnapshot:
    """Resolve a discovered live-session path before snapshot sealing."""

    expected_session_id = expected_session_id_for_live_path(path)
    return read_authoritative_snapshot(path, expected_session_id=expected_session_id)


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


def root_identity_digest(root: Path) -> str:
    """Digest canonical root identity without reading mutable state content."""
    try:
        metadata = Path(root).expanduser().resolve(strict=False).lstat()
    except (OSError, ValueError, RuntimeError, TypeError) as error:
        raise SnapshotError("requested snapshot roots are invalid") from error
    return value_digest([metadata.st_dev, metadata.st_ino, metadata.st_mode])


def anonymize_snapshot_document(
    document: dict[str, Any], roots: list[Path], root_content_digests: list[str],
    root_identity_digests: list[str],
) -> dict[str, Any]:
    """Return a digest-bound snapshot whose persisted locators disclose no paths."""
    normalized = normalize_roots(roots)
    if (
        len(normalized) != len(root_content_digests)
        or len(normalized) != len(root_identity_digests)
        or not all(_is_sha256(value) for value in root_content_digests)
        or not all(_is_sha256(value) for value in root_identity_digests)
    ):
        raise SnapshotError("snapshot privacy root content digests are invalid")
    aliases = [(root, f"root-{index + 1}") for index, root in enumerate(normalized)]

    def anonymize(value: Any) -> Any:
        if isinstance(value, dict):
            return {anonymize(key): anonymize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [anonymize(item) for item in value]
        if not isinstance(value, str):
            return value
        for root, alias in aliases:
            if value == root:
                return alias
            if value.startswith(root + "/"):
                return alias + value[len(root):]
        try:
            is_absolute = Path(value).is_absolute()
        except (OSError, ValueError, RuntimeError, TypeError):
            is_absolute = False
        if is_absolute:
            return "external-" + value_digest(value)[:16]
        return value

    anonymized = anonymize({key: value for key, value in document.items() if key != "content_digest"})
    anonymized["privacy"] = {
        "schema": PRIVACY_SCHEMA,
        "roots": [
            {
                "id": alias,
                "root_content_digest": content_digest,
                "root_identity_digest": identity_digest,
            }
            for (_root, alias), content_digest, identity_digest in zip(
                aliases, root_content_digests, root_identity_digests, strict=True
            )
        ],
    }
    anonymized["content_digest"] = canonical_digest(anonymized)
    return anonymized


def _privacy_root_mapping(document: dict[str, Any], requested_roots: list[Path] | None) -> dict[str, str]:
    privacy = document.get("privacy")
    if not isinstance(privacy, dict) or privacy.get("schema") != PRIVACY_SCHEMA:
        raise SnapshotError("snapshot privacy metadata is invalid")
    entries = privacy.get("roots")
    if not isinstance(entries, list) or not entries:
        raise SnapshotError("snapshot privacy roots are invalid")
    if requested_roots is None:
        raise SnapshotError("privacy snapshot requires requested roots")
    requested = normalize_roots(requested_roots)
    if len(requested) != len(entries):
        raise SnapshotError("snapshot roots do not match the requested ordered multiset")
    mapping: dict[str, str] = {}
    for root, entry in zip(requested, entries, strict=True):
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("id"), str)
            or not entry["id"].startswith("root-")
            or not _is_sha256(entry.get("root_content_digest"))
            or not _is_sha256(entry.get("root_identity_digest"))
            or entry["root_identity_digest"] != root_identity_digest(Path(root))
            or entry["id"] in mapping
        ):
            raise SnapshotError("snapshot roots do not match the requested ordered multiset")
        mapping[entry["id"]] = root
    if document.get("roots") != list(mapping):
        raise SnapshotError("snapshot privacy root locators are invalid")
    return mapping


def _restore_privacy_locators(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            _restore_privacy_locators(key, mapping): _restore_privacy_locators(item, mapping)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_restore_privacy_locators(item, mapping) for item in value]
    if not isinstance(value, str):
        return value
    for alias, root in mapping.items():
        if value == alias:
            return root
        if value.startswith(alias + "/"):
            return root + value[len(alias):]
    return value


def discovery_digest(index: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        index, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _metadata_entry(
    source_path: Path, identity: list[Any], *, ignore_content_metadata: bool = False,
) -> list[Any]:
    absolute = Path(source_path).expanduser().absolute()
    try:
        path_stat = absolute.lstat()
    except FileNotFoundError:
        return [*identity, "missing"]
    except OSError as error:
        return [*identity, "error", error.errno]
    common = [path_stat.st_dev, path_stat.st_ino, path_stat.st_mode]
    if ignore_content_metadata:
        common.extend(["ignored", "ignored", "ignored"])
    else:
        common.extend([path_stat.st_size, path_stat.st_mtime_ns, path_stat.st_ctime_ns])
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
            if relative.parts == (FALLBACK_AUDIT_SNAPSHOT_DIRECTORY,):
                # This is used only if .mission-state cannot be entered.  It is
                # immutable audit output, not an input to a later snapshot.
                dirnames[:] = []
                continue
            inside_state = ".mission-state" in relative.parts
            if not inside_state:
                dirnames[:] = sorted(name for name in dirnames if name not in PRUNE_DIRS)
            else:
                dirnames[:] = sorted(dirnames)
                if relative.parts[-2:] == (".mission-state", "telemetry"):
                    dirnames[:] = [name for name in dirnames if name != "command-outcomes"]
                if relative.parts[-2:] == (".mission-state", AUDIT_SNAPSHOT_DIRECTORY):
                    # Immutable audit snapshots are state-local output, never audit input.
                    # Exclude the directory itself as well so first capture does not
                    # stale a pre-existing external snapshot.
                    dirnames[:] = []
                    continue
            inventory.append(_metadata_entry(
                directory,
                ["root", root_index, relative.as_posix()],
                ignore_content_metadata=relative.parts == (".mission-state",),
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
        if not isinstance(item, dict):
            raise SnapshotError("record payload is invalid")
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
        if "command_outcome_observation" in item:
            index[-1]["command_outcome_observation_sha256"] = value_digest(
                item["command_outcome_observation"]
            )
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
    references = worktree_archive_lineage_references(
        state, f".mission-state/sessions/{state_path.name}"
    )
    return Counter(references) if references is not None else None


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_safe_path_text(value: Any, *, absolute: bool = True) -> bool:
    if not isinstance(value, str) or not value or "\x00" in value:
        return False
    try:
        path = Path(value)
        return not absolute or path.is_absolute()
    except (OSError, ValueError, RuntimeError, TypeError):
        return False


def _validate_record_shape(record: Any, *, privacy: bool = False) -> None:
    if not isinstance(record, dict):
        raise SnapshotError("snapshot record payload is invalid")
    state = record.get("state")
    authoritative_document = record.get("authoritative_document", state)
    if (
        not _is_safe_path_text(record.get("path"), absolute=not privacy)
        or not isinstance(state, dict)
        or not isinstance(authoritative_document, dict)
        or not all(
            isinstance(state.get(key), str) and bool(state.get(key))
            for key in ("mission", "mission_id", "session_id")
        )
        or (
            state.get("project_root") not in (None, "")
            and not _is_safe_path_text(state.get("project_root"), absolute=False)
        )
    ):
        raise SnapshotError("snapshot record payload is invalid")
    for key in (
        "score_history", "specialist_invocations", "activity_segments",
    ):
        value = state.get(key)
        if value is not None and (
            not isinstance(value, list)
            or any(not isinstance(item, dict) for item in value)
        ):
            raise SnapshotError(f"snapshot record {key} collection is invalid")
    for key in (
        "artifact", "progress", "task_profile", "specialists_decision",
        "phase_durations_sec", "failure_ledger",
    ):
        value = state.get(key)
        if value is not None and not isinstance(value, dict):
            raise SnapshotError(f"snapshot record {key} collection is invalid")
    source_inventory = record.get("source_inventory")
    if (
        not isinstance(source_inventory, list)
        or not source_inventory
        or any(not isinstance(item, list) for item in source_inventory)
    ):
        raise SnapshotError("snapshot record source inventory is invalid")
    if "command_outcome_observation" in record:
        observation = validate_command_outcome_observation(
            record.get("command_outcome_observation")
        )
        if observation is None or observation != record["command_outcome_observation"]:
            raise SnapshotError("snapshot command outcome observation is invalid")
    for key in ("archive_bundle", "archive_root"):
        value = record.get(key)
        if value is not None and not _is_safe_path_text(value, absolute=not privacy):
            raise SnapshotError("snapshot archive path is invalid")
    generation = record.get("archive_generation")
    validation_ref = record.get("archive_validation_ref")
    if (
        generation is not None
        and (not isinstance(generation, str) or not generation or "\x00" in generation)
    ) or (
        validation_ref is not None
        and (not isinstance(validation_ref, str) or not validation_ref or "\x00" in validation_ref)
    ):
        raise SnapshotError("snapshot archive reference is invalid")


def _validate_snapshot_collections(document: dict[str, Any]) -> None:
    records = document.get("records")
    stored_index = document.get("record_index")
    external_paths = document.get("external_evidence_paths")
    invalid_archives = document.get("invalid_worktree_archives")
    state_read_errors = document.get("state_read_errors", [])
    archive_validations = document.get("archive_validations")
    roots = document.get("roots")
    privacy = document.get("privacy") is not None
    if not isinstance(records, list) or not isinstance(stored_index, list):
        raise SnapshotError("snapshot record collection is invalid")
    for record in records:
        _validate_record_shape(record, privacy=privacy)
    if any(not isinstance(item, dict) for item in stored_index):
        raise SnapshotError("snapshot record index is invalid")
    if (
        not isinstance(external_paths, list)
        or any(not _is_safe_path_text(path, absolute=not privacy) for path in external_paths)
        or len(external_paths) != len(set(external_paths))
    ):
        raise SnapshotError("snapshot external evidence paths are invalid")
    if (
        not isinstance(roots, list)
        or not roots
        or any(not _is_safe_path_text(root, absolute=not privacy) for root in roots)
    ):
        raise SnapshotError("snapshot roots are invalid")
    if not isinstance(invalid_archives, list):
        raise SnapshotError("snapshot invalid archive collection is invalid")
    for item in invalid_archives:
        if (
            not isinstance(item, dict)
            or not _is_safe_path_text(item.get("bundle_path"), absolute=not privacy)
            or not isinstance(item.get("reason"), str)
            or not item.get("reason")
            or "\x00" in item["reason"]
            or (
                "generation" in item
                and (
                    not isinstance(item["generation"], str)
                    or not item["generation"]
                    or "\x00" in item["generation"]
                )
            )
        ):
            raise SnapshotError("snapshot invalid archive item is invalid")
    if not isinstance(state_read_errors, list):
        raise SnapshotError("snapshot state read error collection is invalid")
    for item in state_read_errors:
        if (
            not isinstance(item, dict)
            or set(item) != {"path", "reason"}
            or not _is_safe_path_text(item.get("path"), absolute=not privacy)
            or item.get("reason") != "authoritative-state-unreadable"
        ):
            raise SnapshotError("snapshot state read error item is invalid")
    if (
        not isinstance(archive_validations, dict)
        or any(
            not isinstance(key, str)
            or not key
            or "\x00" in key
            or not isinstance(value, dict)
            for key, value in archive_validations.items()
        )
    ):
        raise SnapshotError("snapshot archive validation collection is invalid")


def _validate_archive_payload(
    *,
    record: dict[str, Any],
    validation_ref: str,
    payload: Any,
    inventory_by_path: dict[str, list[list[Any]]],
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
    for expected_path, expected_kind in (
        (bundle, "directory"),
        (root, "directory"),
        (bundle / "current.json", "file"),
        (root / "manifest.json", "file"),
    ):
        if not any(
            len(entry) > 3 and entry[3] == expected_kind
            for entry in inventory_by_path.get(str(expected_path), [])
        ):
            raise SnapshotError("snapshot archive metadata relationship is invalid")
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
        current_entries = inventory_by_path.get(str(root / archive_path), [])
        if not any(
            len(entry) > 7 and entry[3] == "file" and entry[7] == size
            for entry in current_entries
        ):
            raise SnapshotError("snapshot archive validation evidence metadata is invalid")
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
    inventory_by_path: dict[str, list[list[Any]]] = {}
    roots = [Path(value) for value in document["roots"]]
    for item in current_inventory:
        if (
            not isinstance(item, list)
            or len(item) < 4
            or item[0] != "root"
            or not isinstance(item[1], int)
            or isinstance(item[1], bool)
            or not 0 <= item[1] < len(roots)
            or not isinstance(item[2], str)
        ):
            continue
        absolute = roots[item[1]] / Path(item[2])
        inventory_by_path.setdefault(str(absolute), []).append(item)
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
            or source_inventory != inventory_by_path.get(path, [])
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
            record=record,
            validation_ref=ref,
            payload=validations[ref],
            inventory_by_path=inventory_by_path,
        )
        referenced.add(ref)
    if referenced != set(validations):
        raise SnapshotError("snapshot archive validation references are inconsistent")


def build_snapshot_document(
    *,
    roots: list[Path],
    records: list[dict[str, Any]],
    invalid_worktree_archives: list[dict[str, Any]],
    state_read_errors: list[dict[str, str]] | None = None,
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
        "state_read_errors": state_read_errors or [],
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
    try:
        source = Path(path).expanduser()
        source_stat = source.lstat()
    except (OSError, ValueError, RuntimeError, TypeError) as error:
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
    if (
        not _is_sha256(document.get("content_digest"))
        or document.get("content_digest") != canonical_digest(document)
    ):
        raise SnapshotError("snapshot content digest mismatch")
    _validate_snapshot_collections(document)
    records = document.get("records")
    stored_index = document.get("record_index")
    external_evidence_paths = document.get("external_evidence_paths")
    invalid_archives = document.get("invalid_worktree_archives")
    archive_validations = document.get("archive_validations")
    record_count = document.get("record_count")
    if (
        not isinstance(record_count, int)
        or isinstance(record_count, bool)
        or record_count < 0
        or record_count != len(records)
        or (
            document.get("privacy") is None
            and stored_index != record_index(records)
        )
    ):
        raise SnapshotError("snapshot record count/index mismatch")
    if (
        not isinstance(document.get("discovery_count"), int)
        or isinstance(document.get("discovery_count"), bool)
        or document.get("discovery_count") < 0
        or not _is_sha256(document.get("discovery_digest"))
    ):
        raise SnapshotError("snapshot discovery count/index mismatch")
    roots = document.get("roots")
    privacy_mapping: dict[str, str] | None = None
    if document.get("privacy") is not None:
        privacy_mapping = _privacy_root_mapping(document, requested_roots)
    elif requested_roots is not None:
        try:
            requested = normalize_roots(requested_roots)
        except (OSError, ValueError, RuntimeError, TypeError) as error:
            raise SnapshotError("requested snapshot roots are invalid") from error
        if requested != roots:
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
    if privacy_mapping is not None:
        document = _restore_privacy_locators(document, privacy_mapping)
    return document


def consume_snapshot_document(
    path: Path, *, requested_roots: list[Path] | None,
    root_inventory_loader=None,
    evidence_inventory_loader=None,
) -> tuple[dict[str, Any], list[Path], datetime]:
    """Validate one snapshot with one metadata-only rewalk."""
    document = read_snapshot(path, requested_roots=requested_roots)
    try:
        roots = [Path(root) for root in document["roots"]]
        evidence_paths = [Path(value) for value in document["external_evidence_paths"]]
        root_loader = root_inventory_loader or root_metadata_inventory
        evidence_loader = evidence_inventory_loader or external_evidence_inventory
        index = root_loader(roots) + evidence_loader(evidence_paths)
    except SnapshotError:
        raise
    except (OSError, ValueError, RuntimeError, TypeError) as error:
        raise SnapshotError(f"snapshot discovery metadata is invalid: {error}") from error
    if (
        len(index) != document["discovery_count"]
        or discovery_digest(index) != document["discovery_digest"]
    ):
        raise SnapshotError("snapshot discovery fingerprint is stale")
    try:
        validate_snapshot_semantics(document, index)
    except SnapshotError:
        raise
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
        raise SnapshotError(f"snapshot semantic payload is invalid: {error}") from error
    observed_at = parse_iso_datetime(document["observed_at"])
    if observed_at is None or observed_at.tzinfo is None:
        raise SnapshotError("snapshot observed_at is invalid")
    return document, roots, observed_at.astimezone(timezone.utc)
