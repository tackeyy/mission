"""Pre-gate evaluation cache sidecar contract (#421)."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA = "mission-pregate-evaluation/1"
_VALID_VERDICTS = {"accepted", "split-required", "rejected"}
_TMP_PREFIX = ".tmp-"
_MAX_JSON_BYTES = 4 * 1024 * 1024
_ISSUE_REF_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_-]")


class PregateCacheError(ValueError):
    """Raised when a pregate evaluation document or directory is invalid."""


def _canonical_issue_ref(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    m = re.search(r"/issues/(\d+)", raw)
    if m:
        return m.group(1)
    m = re.search(r"#(\d+)\s*$", raw)
    if m:
        return m.group(1)
    m = re.fullmatch(r"#?(\d+)", raw)
    if m:
        return m.group(1)
    return raw.lower()


def _issue_ref_key(value: Any) -> str:
    canonical = _canonical_issue_ref(value)
    if canonical is None:
        raise PregateCacheError("pregate issue_ref is missing")
    sanitized = _ISSUE_REF_SANITIZE_RE.sub("_", canonical)
    if not sanitized:
        raise PregateCacheError("pregate issue_ref is invalid")
    return sanitized


def _state_dir(cwd: Path) -> Path:
    return cwd / ".mission-state"


def _ensure_mission_state_dir(cwd: Path) -> Path:
    root = _state_dir(cwd)
    try:
        metadata = root.lstat()
    except FileNotFoundError as exc:
        raise PregateCacheError("mission state directory is missing") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise PregateCacheError("mission state directory is unsafe")
    return root


def _pregate_root(cwd: Path, *, create: bool) -> Path:
    root = _ensure_mission_state_dir(cwd)
    pregate_root = root / "pregate"
    if pregate_root.exists() or pregate_root.is_symlink():
        metadata = pregate_root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise PregateCacheError("pregate directory is unsafe")
    elif create:
        pregate_root.mkdir(mode=0o700)
        metadata = pregate_root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise PregateCacheError("pregate directory is unsafe")
    return pregate_root


def _lock_file(pregate_root: Path):
    lock_path = pregate_root / ".pregate.lock"
    fd = os.open(os.fspath(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
    except Exception:
        os.close(fd)
        raise
    return fd


def _fsync_directory(path: Path) -> None:
    fd = os.open(os.fspath(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _canonical_bytes(payload: Any) -> bytes:
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PregateCacheError("pregate payload is not JSON serializable") from exc


def subject_digest(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _parse_iso_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise PregateCacheError("pregate evaluated_at is invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_json_bytes(data: bytes) -> dict[str, Any]:
    if len(data) > _MAX_JSON_BYTES:
        raise PregateCacheError("pregate evaluation is too large")
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PregateCacheError("pregate evaluation is not valid JSON") from exc
    if not isinstance(document, dict):
        raise PregateCacheError("pregate evaluation is not a JSON object")
    return document


def _record_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _capture_regular_file(
    path: Path, *, missing_ok: bool = False
) -> tuple[bytes | None, tuple[int, ...] | None]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        if missing_ok:
            return None, None
        raise PregateCacheError("pregate file is missing") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1:
        raise PregateCacheError("pregate file is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(os.fspath(path), flags)
    except OSError as exc:
        raise PregateCacheError("pregate file is unreadable") from exc
    try:
        initial = os.fstat(fd)
        if (
            not stat.S_ISREG(initial.st_mode)
            or initial.st_nlink != 1
            or _record_identity(initial) != _record_identity(metadata)
        ):
            raise PregateCacheError("pregate file is unsafe")
        remaining = initial.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(fd, min(remaining, 64 * 1024))
            if not chunk:
                raise PregateCacheError("pregate file changed while being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise PregateCacheError("pregate file changed while being read")
        after = os.fstat(fd)
        named_after = path.lstat()
        if (
            _record_identity(after) != _record_identity(initial)
            or _record_identity(named_after) != _record_identity(initial)
        ):
            raise PregateCacheError("pregate file changed while being read")
        return b"".join(chunks), _record_identity(initial)
    except OSError as exc:
        raise PregateCacheError("pregate file is unreadable") from exc
    finally:
        os.close(fd)


def _read_regular_file(path: Path) -> bytes:
    payload, _identity = _capture_regular_file(path)
    assert payload is not None
    return payload


def _validate_envelope(document: dict[str, Any], *, path: Path) -> dict[str, Any]:
    required = {
        "schema",
        "issue_ref",
        "subject_digest",
        "evaluated_at",
        "ttl_hours",
        "verdict",
        "gate_id",
        "evidence_refs",
        "producer_session",
        "payload",
    }
    if set(document) != required:
        raise PregateCacheError("pregate envelope shape is invalid")
    if document["schema"] != SCHEMA:
        raise PregateCacheError("pregate schema is invalid")
    issue_ref = _canonical_issue_ref(document["issue_ref"])
    if issue_ref is None:
        raise PregateCacheError("pregate issue_ref is invalid")
    if _issue_ref_key(issue_ref) != path.stem:
        raise PregateCacheError("pregate path does not match issue_ref")
    subject_digest = document["subject_digest"]
    if not isinstance(subject_digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", subject_digest):
        raise PregateCacheError("pregate subject_digest is invalid")
    evaluated_at = _parse_iso_utc(document["evaluated_at"])
    ttl_hours = document["ttl_hours"]
    if not isinstance(ttl_hours, int) or isinstance(ttl_hours, bool) or ttl_hours < 0:
        raise PregateCacheError("pregate ttl_hours is invalid")
    verdict = document["verdict"]
    if verdict not in _VALID_VERDICTS:
        raise PregateCacheError("pregate verdict is invalid")
    gate_id = document["gate_id"]
    if not isinstance(gate_id, str) or not gate_id.strip():
        raise PregateCacheError("pregate gate_id is invalid")
    evidence_refs = document["evidence_refs"]
    if not isinstance(evidence_refs, list):
        raise PregateCacheError("pregate evidence_refs is invalid")
    for item in evidence_refs:
        if not isinstance(item, dict) or set(item) != {"kind", "value"}:
            raise PregateCacheError("pregate evidence_ref is invalid")
        if item["kind"] not in {"url", "path"} or not isinstance(item["value"], str) or not item["value"]:
            raise PregateCacheError("pregate evidence_ref is invalid")
    producer_session = document["producer_session"]
    if not isinstance(producer_session, str):
        raise PregateCacheError("pregate producer_session is invalid")
    payload = document["payload"]
    return {
        "schema": SCHEMA,
        "issue_ref": issue_ref,
        "subject_digest": subject_digest,
        "evaluated_at": evaluated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ttl_hours": ttl_hours,
        "verdict": verdict,
        "gate_id": gate_id,
        "evidence_refs": evidence_refs,
        "producer_session": producer_session,
        "payload": payload,
    }


def _read_record(cwd: Path, issue_ref: Any) -> tuple[dict[str, Any], Path] | None:
    try:
        root = _pregate_root(cwd, create=False)
    except PregateCacheError:
        return None
    try:
        path = root / f"{_issue_ref_key(issue_ref)}.json"
    except PregateCacheError:
        return None
    try:
        document = _validate_envelope(_load_json_bytes(_read_regular_file(path)), path=path)
    except PregateCacheError:
        return None
    return document, path


def _is_expired(record: dict[str, Any], now: datetime) -> bool:
    evaluated_at = _parse_iso_utc(record["evaluated_at"])
    ttl = int(record["ttl_hours"])
    return now > evaluated_at + timedelta(hours=ttl)


def _maybe_load_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def record(cwd: Path, evaluation: Any, *, issue_ref: Any) -> dict[str, Any]:
    root = _pregate_root(cwd, create=True)
    if not isinstance(evaluation, dict):
        raise PregateCacheError("pregate evaluation is not a JSON object")
    expected_key = _issue_ref_key(issue_ref)
    record = _validate_envelope(evaluation, path=root / f"{expected_key}.json")
    if _issue_ref_key(record["issue_ref"]) != expected_key:
        raise PregateCacheError("pregate issue_ref does not match")
    final_path = root / f"{expected_key}.json"
    lock_fd = _lock_file(root)
    temp_path: Path | None = None
    try:
        current_payload, expected_identity = _capture_regular_file(
            final_path, missing_ok=True
        )
        if current_payload is not None:
            _validate_envelope(_load_json_bytes(current_payload), path=final_path)
        data = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=root, prefix=_TMP_PREFIX, suffix=".json") as tmp:
            temp_path = Path(tmp.name)
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        if expected_identity is None:
            try:
                os.link(temp_path, final_path, follow_symlinks=False)
            except FileExistsError as exc:
                raise PregateCacheError("pregate record appeared before publish") from exc
            temp_path.unlink()
            temp_path = None
        else:
            try:
                current_identity = _record_identity(final_path.lstat())
            except OSError as exc:
                raise PregateCacheError("pregate record changed before publish") from exc
            if current_identity != expected_identity:
                raise PregateCacheError("pregate record changed before publish")
            os.replace(temp_path, final_path)
            temp_path = None
        _fsync_directory(root)
        return {"path": str(final_path), "subject_digest": record["subject_digest"]}
    except Exception as exc:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
        if isinstance(exc, PregateCacheError):
            raise
        if isinstance(exc, OSError):
            raise PregateCacheError("pregate publish failed") from exc
        raise
    finally:
        os.close(lock_fd)


def inspect(cwd: Path, issue_ref: Any, *, now: datetime | None = None) -> dict[str, Any] | None:
    now_utc = _maybe_load_now(now)
    loaded = _read_record(cwd, issue_ref)
    if loaded is None:
        return None
    record, path = loaded
    if _is_expired(record, now_utc):
        return None
    return {
        "path": str(path),
        "subject_digest": record["subject_digest"],
        "verdict": record["verdict"],
        "gate_id": record["gate_id"],
        "evaluated_at": record["evaluated_at"],
        "issue_ref": record["issue_ref"],
        "ttl_hours": record["ttl_hours"],
        "evidence_refs": record["evidence_refs"],
        "producer_session": record["producer_session"],
        "payload": record["payload"],
    }


def lookup(
    cwd: Path,
    issue_ref: Any,
    subject_digest: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now_utc = _maybe_load_now(now)
    loaded = _read_record(cwd, issue_ref)
    if loaded is None:
        return {"status": "miss"}
    record, path = loaded
    if subject_digest != record["subject_digest"]:
        return {"status": "stale", "record": {"path": str(path), **record}}
    if _is_expired(record, now_utc):
        return {"status": "stale", "record": {"path": str(path), **record}}
    return {"status": "hit", "record": {"path": str(path), **record}}
