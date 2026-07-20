"""Explicit, short-lived, fail-closed state snapshot documents."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
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


class SnapshotError(ValueError):
    """Raised when a snapshot cannot be trusted or reused."""


def normalize_roots(roots: list[Path]) -> list[str]:
    return [str(Path(root).expanduser().resolve(strict=False)) for root in roots]


def canonical_digest(document: dict[str, Any]) -> str:
    core = {key: value for key, value in document.items() if key != "content_digest"}
    encoded = json.dumps(
        core, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def discovery_digest(index: list[dict[str, Any]]) -> str:
    encoded = json.dumps(
        index, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def record_index(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index: list[dict[str, Any]] = []
    for item in records:
        state = item.get("state")
        path = item.get("path")
        if not isinstance(state, dict) or not isinstance(path, str):
            raise SnapshotError("record payload is invalid")
        identity = state_identity(state, Path(path).stem, path)
        index.append({"path": path, "identity": list(identity)})
    return index


def build_snapshot_document(
    *,
    roots: list[Path],
    records: list[dict[str, Any]],
    invalid_worktree_archives: list[dict[str, Any]],
    discovery_index: list[dict[str, Any]],
    observed_at: datetime,
    ttl_seconds: int,
    created_at: datetime | None = None,
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
        "record_index": record_index(records),
        "invalid_worktree_archives": invalid_worktree_archives,
        "discovery_index": discovery_index,
        "discovery_digest": discovery_digest(discovery_index),
    }
    document["content_digest"] = canonical_digest(document)
    return document


def write_snapshot(path: Path, document: dict[str, Any]) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
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
        document = json.loads(source.read_text(encoding="utf-8"))
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
    discovery_index_value = document.get("discovery_index")
    invalid_archives = document.get("invalid_worktree_archives")
    if not isinstance(records, list) or not isinstance(stored_index, list):
        raise SnapshotError("snapshot record collection is invalid")
    if not isinstance(discovery_index_value, list) or not isinstance(invalid_archives, list):
        raise SnapshotError("snapshot discovery collection is invalid")
    if document.get("record_count") != len(records) or stored_index != record_index(records):
        raise SnapshotError("snapshot record count/index mismatch")
    if (
        document.get("discovery_count") != len(discovery_index_value)
        or document.get("discovery_digest") != discovery_digest(discovery_index_value)
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
        or observed_at is None
        or isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, int)
        or ttl_seconds <= 0
    ):
        raise SnapshotError("snapshot time/TTL metadata is invalid")
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    age = (base.astimezone(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds()
    if age < 0 or age > ttl_seconds:
        raise SnapshotError("snapshot is expired or from the future")
    return document
