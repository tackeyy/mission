"""Bounded, privacy-safe command outcome telemetry (#386).

The lifecycle state is the authoritative journal for successful commands.  A
separate per-session sidecar records rejected commands without changing state
bytes.  Both locations intentionally share the exact same small record shape.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "mission-command-outcomes/1"
KINDS = ("ok", "expected-gate", "invalid-input", "external", "internal-error")
LIMIT = 128
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class OutcomeStoreError(ValueError):
    """A sidecar cannot be safely read or appended."""


def valid_identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(_ID_RE.fullmatch(value))


def validate_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    required = ("event_id", "root_event_id", "attempt", "command", "outcome_kind")
    if set(value) - {*required, "retry_of"} or any(key not in value for key in required):
        return None
    if not all(valid_identifier(value[key]) for key in ("event_id", "root_event_id")):
        return None
    if not isinstance(value["attempt"], int) or isinstance(value["attempt"], bool) or value["attempt"] < 1:
        return None
    if not isinstance(value["command"], str) or not value["command"] or len(value["command"]) > 80:
        return None
    if value["outcome_kind"] not in KINDS:
        return None
    if "retry_of" in value and not valid_identifier(value["retry_of"]):
        return None
    return dict(value)


def append_state_record(state: dict[str, Any], record: dict[str, Any]) -> None:
    validated = validate_record(record)
    if validated is None:
        raise OutcomeStoreError("command outcome record is invalid")
    prior = state.get("command_outcomes", [])
    if prior is None:
        prior = []
    if not isinstance(prior, list):
        raise OutcomeStoreError("command outcomes state is invalid")
    retained: list[dict[str, Any]] = []
    for item in prior:
        normalized = validate_record(item)
        if normalized is None:
            raise OutcomeStoreError("command outcomes state is invalid")
        retained.append(normalized)
    state["command_outcomes"] = (retained + [validated])[-LIMIT:]


def _safe_sidecar(directory: Path, sid_token: str) -> Path:
    if not valid_identifier(sid_token):
        raise OutcomeStoreError("sidecar session token is invalid")
    root = directory / "telemetry" / "command-outcomes"
    path = root / f"{sid_token}.json"
    try:
        root_parent = directory.resolve(strict=False)
        if root_parent.is_symlink() or (root.exists() and root.is_symlink()):
            raise OutcomeStoreError("command outcome telemetry directory is unsafe")
        candidate = path.resolve(strict=False)
        candidate.relative_to(root.resolve(strict=False))
    except (OSError, RuntimeError, ValueError) as exc:
        raise OutcomeStoreError("command outcome telemetry path is unsafe") from exc
    return path


def _read_regular(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise OutcomeStoreError("command outcome telemetry is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise OutcomeStoreError("command outcome telemetry is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(os.fspath(path), flags)
    except OSError as exc:
        raise OutcomeStoreError("command outcome telemetry is unavailable") from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size > 256 * 1024:
            raise OutcomeStoreError("command outcome telemetry is unsafe")
        content = os.read(fd, opened.st_size + 1)
        if len(content) != opened.st_size or os.fstat(fd).st_ino != opened.st_ino:
            raise OutcomeStoreError("command outcome telemetry changed while being read")
        if path.lstat().st_ino != opened.st_ino:
            raise OutcomeStoreError("command outcome telemetry changed while being read")
        return content
    except OSError as exc:
        raise OutcomeStoreError("command outcome telemetry is unavailable") from exc
    finally:
        os.close(fd)


def _decode_document(payload: bytes) -> list[dict[str, Any]]:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OutcomeStoreError("command outcome telemetry is corrupt") from exc
    if not isinstance(document, dict) or set(document) != {"schema", "records"} or document.get("schema") != SCHEMA:
        raise OutcomeStoreError("command outcome telemetry is corrupt")
    records = document.get("records")
    if not isinstance(records, list) or len(records) > LIMIT:
        raise OutcomeStoreError("command outcome telemetry is corrupt")
    output = [validate_record(item) for item in records]
    if any(item is None for item in output):
        raise OutcomeStoreError("command outcome telemetry is corrupt")
    return output  # type: ignore[return-value]


class _Lock:
    def __init__(self, path: Path): self.path, self.fd = path, None
    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_symlink(): raise OutcomeStoreError("command outcome telemetry lock is unsafe")
        self.fd = open(self.path, "w")
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(self.fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline: raise OutcomeStoreError("command outcome telemetry lock timed out")
                time.sleep(.05)
    def __exit__(self, *_):
        if self.fd is not None:
            fcntl.flock(self.fd.fileno(), fcntl.LOCK_UN)
            self.fd.close()


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush(); os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def append_sidecar(state_directory: Path, sid_token: str, record: dict[str, Any]) -> None:
    """Append rejected command telemetry or fail closed without following links."""
    validated = validate_record(record)
    if validated is None:
        raise OutcomeStoreError("command outcome record is invalid")
    path = _safe_sidecar(state_directory, sid_token)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise OutcomeStoreError("command outcome telemetry directory is unsafe")
    with _Lock(path.with_suffix(".lock")):
        records = _decode_document(_read_regular(path)) if path.exists() else []
        _atomic_json(path, {"schema": SCHEMA, "records": (records + [validated])[-LIMIT:]})


def iter_records(state: dict[str, Any], state_directory: Path, sid_token: str) -> tuple[list[dict[str, Any]], int, int]:
    """Read state+sidecar with a single validation contract.

    Returns records, invalid state records, corrupt sidecar count. Corrupt data
    is deliberately not treated as empty: telemetry carries an explicit count.
    """
    records: list[dict[str, Any]] = []
    invalid = 0
    raw = state.get("command_outcomes", [])
    if raw is not None:
        if not isinstance(raw, list):
            invalid += 1
        else:
            for item in raw:
                normalized = validate_record(item)
                if normalized is None: invalid += 1
                else: records.append(normalized)
    corrupt = 0
    try:
        path = _safe_sidecar(state_directory, sid_token)
        if path.exists(): records.extend(_decode_document(_read_regular(path)))
    except OutcomeStoreError:
        corrupt = 1
    return records, invalid, corrupt


def summarize(records: Iterable[dict[str, Any]], *, invalid_records: int = 0, corrupt_sidecars: int = 0) -> dict[str, int]:
    counts = Counter({kind: 0 for kind in KINDS})
    roots: set[str] = set()
    retries = 0
    for record in records:
        normalized = validate_record(record)
        if normalized is None:
            invalid_records += 1; continue
        counts[normalized["outcome_kind"]] += 1
        roots.add(normalized["root_event_id"])
        if normalized["attempt"] > 1 or "retry_of" in normalized: retries += 1
    return {**dict(counts), "unique_root_events": len(roots), "retry_count": retries,
            "invalid_records": invalid_records, "corrupt_sidecars": corrupt_sidecars}
