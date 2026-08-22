"""Administrative separate-aggregate commit protocol (ADR-006 point 3 / U5-1).

ADR-006 rejects the category "administrative writer without a protocol": every
separate-aggregate write must provide, at minimum, an identity-checked read,
validation, an atomic publish, and a defined failure outcome.  This module
owns that minimal protocol for janitor-style record mutations
(resolve-archive).  The legacy save's aggregate index update is tracked
separately (U5-2) and remains a known exclusion until it migrates.
"""

from __future__ import annotations

import copy
import json
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class AdministrativeCommitError(Exception):
    """Protocol-level failure with a stable machine-readable code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _record_identity(metadata) -> tuple:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


@dataclass(frozen=True)
class CapturedRecord:
    """One identity-checked read of an administrative record."""

    path: Path
    payload: bytes
    identity: tuple
    document: dict


def capture_record(target: Path) -> CapturedRecord:
    """Identity-checked read: reject symlinks, hardlinks, and non-objects."""
    try:
        metadata = target.lstat()
    except OSError as exc:
        raise AdministrativeCommitError("record-unreadable", str(exc)) from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise AdministrativeCommitError(
            "record-identity-invalid",
            "administrative record must be a regular non-hardlinked file",
        )
    try:
        payload = target.read_bytes()
    except OSError as exc:
        raise AdministrativeCommitError("record-unreadable", str(exc)) from exc
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdministrativeCommitError("record-invalid", str(exc)) from exc
    if not isinstance(document, dict):
        raise AdministrativeCommitError(
            "record-invalid", "administrative record must be a JSON object"
        )
    return CapturedRecord(target, payload, _record_identity(metadata), document)


def administrative_commit(
    target: Path,
    *,
    validate: Callable[[dict], None],
    mutate: Callable[[dict], None],
    write_document: Callable[..., None],
) -> tuple[CapturedRecord, dict]:
    """One identity-checked read → validation → atomic publish transaction.

    Defined failure outcomes:

    - ``validate`` / ``mutate`` errors propagate unchanged before any write;
    - an identity change between capture and publish fails closed with
      ``record-changed`` (the caller's lock makes this a crash-only path, and
      the protocol still refuses to overwrite a record it did not read);
    - a failed publish propagates from the injected atomic writer and leaves
      the original record in place (full former-or-latter state).
    """
    captured = capture_record(target)
    validate(copy.deepcopy(captured.document))
    proposed = copy.deepcopy(captured.document)
    mutate(proposed)
    try:
        current = target.lstat()
    except OSError as exc:
        raise AdministrativeCommitError("record-changed", str(exc)) from exc
    if _record_identity(current) != captured.identity:
        raise AdministrativeCommitError(
            "record-changed",
            "administrative record changed between capture and publish",
        )
    write_document(target, proposed, administrative=True)
    return captured, proposed


def restore_record(
    captured: CapturedRecord, write_bytes: Callable[[Path, bytes], None]
) -> None:
    """Defined recovery step: restore the captured payload or fail closed."""
    try:
        write_bytes(captured.path, captured.payload)
    except OSError as exc:
        raise AdministrativeCommitError("rollback-failed", str(exc)) from exc
