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
import secrets
import stat
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


def _directory_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _same_identity(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and first.st_mode == second.st_mode
        and first.st_nlink == second.st_nlink
    )


def _verify_directory_identity(directory_fd: int, named_parent: Path) -> None:
    """Prove that a held directory descriptor is still the named sidecar parent."""
    try:
        opened = os.fstat(directory_fd)
        named = named_parent.lstat()
    except OSError as exc:
        raise OutcomeStoreError("command outcome telemetry directory changed") from exc
    if (
        not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(named.st_mode)
        or opened.st_dev != named.st_dev
        or opened.st_ino != named.st_ino
        or opened.st_mode != named.st_mode
    ):
        raise OutcomeStoreError("command outcome telemetry directory changed")


def _open_sidecar_directory(state_directory: Path, *, create: bool = True) -> int | None:
    """Create/open telemetry descendants through a no-follow descriptor chain."""
    try:
        current_fd = os.open(os.fspath(state_directory), _directory_flags())
    except OSError as exc:
        raise OutcomeStoreError("command outcome state directory is unsafe") from exc
    try:
        for name in ("telemetry", "command-outcomes"):
            try:
                next_fd = os.open(name, _directory_flags(), dir_fd=current_fd)
            except FileNotFoundError:
                if not create:
                    os.close(current_fd)
                    return None
                try:
                    os.mkdir(name, 0o700, dir_fd=current_fd)
                    next_fd = os.open(name, _directory_flags(), dir_fd=current_fd)
                except OSError as exc:
                    raise OutcomeStoreError("command outcome telemetry directory is unsafe") from exc
            except OSError as exc:
                raise OutcomeStoreError("command outcome telemetry directory is unsafe") from exc
            opened = os.fstat(next_fd)
            named = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or opened.st_dev != named.st_dev
                or opened.st_ino != named.st_ino
                or opened.st_mode != named.st_mode
            ):
                os.close(next_fd)
                raise OutcomeStoreError("command outcome telemetry directory changed")
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


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
        current = directory
        for candidate in (directory, directory / "telemetry", root):
            if candidate.exists() or candidate.is_symlink():
                metadata = candidate.lstat()
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                    raise OutcomeStoreError("command outcome telemetry directory is unsafe")
            current = candidate
        path.absolute().relative_to(root.absolute())
    except (OSError, RuntimeError, ValueError) as exc:
        raise OutcomeStoreError("command outcome telemetry path is unsafe") from exc
    return path


def _read_regular_at(directory_fd: int, name: str, *, missing_ok: bool = False) -> bytes | None:
    """Read one bounded sidecar through the already-verified parent descriptor."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise OutcomeStoreError("command outcome telemetry is unavailable")
    except OSError as exc:
        raise OutcomeStoreError("command outcome telemetry is unavailable") from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or opened.st_size > 256 * 1024:
            raise OutcomeStoreError("command outcome telemetry is unsafe")
        named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not _same_identity(opened, named):
            raise OutcomeStoreError("command outcome telemetry changed while being read")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 64 * 1024))
            if not chunk:
                raise OutcomeStoreError("command outcome telemetry changed while being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise OutcomeStoreError("command outcome telemetry changed while being read")
        after = os.fstat(fd)
        named_after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not _same_identity(opened, after) or not _same_identity(after, named_after):
            raise OutcomeStoreError("command outcome telemetry changed while being read")
        return b"".join(chunks)
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
    def __init__(self, path: Path, *, parent_fd: int | None = None):
        self.path = path
        self.fd: int | None = None
        self.parent_fd: int | None = os.dup(parent_fd) if parent_fd is not None else None

    @staticmethod
    def _same_identity(first: os.stat_result, second: os.stat_result) -> bool:
        return _same_identity(first, second)

    def __enter__(self):
        if self.parent_fd is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        try:
            if self.parent_fd is None:
                self.parent_fd = os.open(os.fspath(self.path.parent), _directory_flags())
            self.fd = os.open(self.path.name, lock_flags, 0o600, dir_fd=self.parent_fd)
            opened = os.fstat(self.fd)
            named = os.stat(self.path.name, dir_fd=self.parent_fd, follow_symlinks=False)
        except OSError as exc:
            self._close()
            raise OutcomeStoreError("command outcome telemetry lock is unsafe") from exc
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not self._same_identity(opened, named)
        ):
            self._close()
            raise OutcomeStoreError("command outcome telemetry lock is unsafe")
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                after = os.fstat(self.fd)
                named_after = os.stat(
                    self.path.name, dir_fd=self.parent_fd, follow_symlinks=False
                )
                if (
                    after.st_nlink != 1
                    or not self._same_identity(opened, after)
                    or not self._same_identity(after, named_after)
                ):
                    self._close()
                    raise OutcomeStoreError("command outcome telemetry lock changed")
                return self
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    self._close()
                    raise OutcomeStoreError("command outcome telemetry lock timed out")
                time.sleep(.05)

    def _close(self) -> None:
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(self.fd)
            self.fd = None
        if self.parent_fd is not None:
            os.close(self.parent_fd)
            self.parent_fd = None

    def __exit__(self, *_):
        self._close()


def _atomic_json_at(
    directory_fd: int, name: str, document: dict[str, Any], named_parent: Path,
) -> None:
    """Publish JSON atomically without resolving the sidecar parent pathname."""
    payload = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    temporary = ""
    fd: int | None = None
    try:
        for _attempt in range(32):
            temporary = f".{name}.{secrets.token_hex(8)}.tmp"
            try:
                fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_fd,
                )
                break
            except FileExistsError:
                continue
        if fd is None:
            raise OutcomeStoreError("command outcome telemetry temporary file is unavailable")
        initial = os.fstat(fd)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_nlink != 1
            or stat.S_IMODE(initial.st_mode) != 0o600
        ):
            raise OutcomeStoreError("command outcome telemetry temporary file is unsafe")
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise OutcomeStoreError("command outcome telemetry write failed")
            offset += written
        os.fsync(fd)
        current = os.fstat(fd)
        named = os.stat(temporary, dir_fd=directory_fd, follow_symlinks=False)
        if current.st_size != len(payload) or not _same_identity(initial, current) or not _same_identity(current, named):
            raise OutcomeStoreError("command outcome telemetry temporary file changed")
        _verify_directory_identity(directory_fd, named_parent)
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary = ""
        published = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not _same_identity(current, published):
            raise OutcomeStoreError("command outcome telemetry publish changed")
        _verify_directory_identity(directory_fd, named_parent)
        os.fsync(directory_fd)
    except OSError as exc:
        raise OutcomeStoreError("command outcome telemetry publish failed") from exc
    finally:
        if fd is not None:
            os.close(fd)
        if temporary:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass


def append_sidecar(state_directory: Path, sid_token: str, record: dict[str, Any]) -> None:
    """Append rejected command telemetry or fail closed without following links."""
    validated = validate_record(record)
    if validated is None:
        raise OutcomeStoreError("command outcome record is invalid")
    path = _safe_sidecar(state_directory, sid_token)
    directory_fd = _open_sidecar_directory(state_directory)
    if directory_fd is None:  # create=True always yields a descriptor or raises.
        raise OutcomeStoreError("command outcome telemetry directory is unavailable")
    try:
        _verify_directory_identity(directory_fd, path.parent)
        with _Lock(path.with_suffix(".lock"), parent_fd=directory_fd):
            _verify_directory_identity(directory_fd, path.parent)
            payload = _read_regular_at(directory_fd, path.name, missing_ok=True)
            records = _decode_document(payload) if payload is not None else []
            _atomic_json_at(
                directory_fd, path.name,
                {"schema": SCHEMA, "records": (records + [validated])[-LIMIT:]},
                path.parent,
            )
    finally:
        os.close(directory_fd)


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
    directory_fd: int | None = None
    try:
        path = _safe_sidecar(state_directory, sid_token)
        directory_fd = _open_sidecar_directory(state_directory, create=False)
        if directory_fd is not None:
            _verify_directory_identity(directory_fd, path.parent)
            payload = _read_regular_at(directory_fd, path.name, missing_ok=True)
            if payload is not None:
                records.extend(_decode_document(payload))
    except OutcomeStoreError:
        corrupt = 1
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
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
