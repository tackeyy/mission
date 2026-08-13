"""Merge queue sidecar for Issue #424."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mission_common import parse_iso_datetime


SCHEMA = "mission-merge-queue/1"
_TMP_PREFIX = ".tmp-"
_QUEUE_FILE = "merge-queue.json"
_VALID_STATUSES = {"queued", "ready", "merged", "invalidated", "superseded"}
_ISSUE_REF_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_-]")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class MergeQueueError(ValueError):
    """Raised when a merge queue document or directory is invalid."""


class BaseMismatchError(MergeQueueError):
    """Raised when a candidate's accepted base no longer matches the live base."""


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
        raise MergeQueueError("merge queue issue_ref is missing")
    sanitized = _ISSUE_REF_SANITIZE_RE.sub("_", canonical)
    if not sanitized:
        raise MergeQueueError("merge queue issue_ref is invalid")
    return sanitized


def _state_dir(cwd: Path) -> Path:
    return cwd / ".mission-state"


def _ensure_mission_state_dir(cwd: Path) -> Path:
    root = _state_dir(cwd)
    try:
        metadata = root.lstat()
    except FileNotFoundError as exc:
        raise MergeQueueError("mission state directory is missing") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise MergeQueueError("mission state directory is unsafe")
    return root


def _queue_path(cwd: Path) -> Path:
    return _ensure_mission_state_dir(cwd) / _QUEUE_FILE


def _queue_now(now: datetime | None = None) -> datetime:
    if now is not None:
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)
    override = os.environ.get("MISSION_STATE_NOW")
    if override:
        parsed = parse_iso_datetime(override)
        if parsed is None:
            raise MergeQueueError("merge queue time override is invalid")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def _lock_file(directory: Path):
    lock_path = directory / ".merge-queue.lock"
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


def _read_regular_file(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise MergeQueueError("merge queue file is missing") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1:
        raise MergeQueueError("merge queue file is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(os.fspath(path), flags)
    except OSError as exc:
        raise MergeQueueError("merge queue file is unreadable") from exc
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise MergeQueueError("merge queue file is unsafe")
        remaining = opened.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(fd, min(remaining, 64 * 1024))
            if not chunk:
                raise MergeQueueError("merge queue file changed while being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise MergeQueueError("merge queue file changed while being read")
        after = os.fstat(fd)
        if after.st_size != opened.st_size or after.st_ino != opened.st_ino or after.st_dev != opened.st_dev:
            raise MergeQueueError("merge queue file changed while being read")
        return b"".join(chunks)
    except OSError as exc:
        raise MergeQueueError("merge queue file is unreadable") from exc
    finally:
        os.close(fd)


def _load_json_bytes(data: bytes) -> dict[str, Any]:
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MergeQueueError("merge queue document is not valid JSON") from exc
    if not isinstance(document, dict):
        raise MergeQueueError("merge queue document is not a JSON object")
    return document


def _validate_sha(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.fullmatch(value):
        raise MergeQueueError(f"merge queue {field} is invalid")
    return value


def _validate_entry(document: dict[str, Any]) -> dict[str, Any]:
    required = {
        "queue_id",
        "session_id",
        "issue_ref_key",
        "pr_ref",
        "head_sha",
        "accepted_base_sha",
        "depends_on",
        "status",
        "reason",
        "enqueued_at",
        "updated_at",
    }
    if set(document) != required:
        raise MergeQueueError("merge queue entry shape is invalid")
    queue_id = document["queue_id"]
    if not isinstance(queue_id, str) or not re.fullmatch(r"[0-9a-f]{16}", queue_id):
        raise MergeQueueError("merge queue queue_id is invalid")
    session_id = document["session_id"]
    if not isinstance(session_id, str) or not session_id.strip():
        raise MergeQueueError("merge queue session_id is invalid")
    issue_ref_key = _issue_ref_key(document["issue_ref_key"])
    pr_ref = document["pr_ref"]
    if not isinstance(pr_ref, str) or not pr_ref.strip() or len(pr_ref) > 256:
        raise MergeQueueError("merge queue pr_ref is invalid")
    head_sha = _validate_sha(document["head_sha"], field="head_sha")
    accepted_base_sha = _validate_sha(document["accepted_base_sha"], field="accepted_base_sha")
    depends_on = document["depends_on"]
    if not isinstance(depends_on, list):
        raise MergeQueueError("merge queue depends_on is invalid")
    normalized_depends_on: list[str] = []
    for item in depends_on:
        normalized_depends_on.append(_issue_ref_key(item))
    status = document["status"]
    if status not in _VALID_STATUSES:
        raise MergeQueueError("merge queue status is invalid")
    reason = document["reason"]
    if not isinstance(reason, str):
        raise MergeQueueError("merge queue reason is invalid")
    enqueued_at = document["enqueued_at"]
    updated_at = document["updated_at"]
    if not isinstance(enqueued_at, str) or parse_iso_datetime(enqueued_at) is None:
        raise MergeQueueError("merge queue enqueued_at is invalid")
    if not isinstance(updated_at, str) or parse_iso_datetime(updated_at) is None:
        raise MergeQueueError("merge queue updated_at is invalid")
    return {
        "queue_id": queue_id,
        "session_id": session_id,
        "issue_ref_key": issue_ref_key,
        "pr_ref": pr_ref,
        "head_sha": head_sha,
        "accepted_base_sha": accepted_base_sha,
        "depends_on": normalized_depends_on,
        "status": status,
        "reason": reason,
        "enqueued_at": enqueued_at,
        "updated_at": updated_at,
    }


def _validate_queue(document: dict[str, Any]) -> dict[str, Any]:
    if set(document) != {"schema", "entries"}:
        raise MergeQueueError("merge queue document shape is invalid")
    if document["schema"] != SCHEMA:
        raise MergeQueueError("merge queue schema is invalid")
    entries = document["entries"]
    if not isinstance(entries, list):
        raise MergeQueueError("merge queue entries are invalid")
    normalized = [_validate_entry(item) for item in entries]
    return {"schema": SCHEMA, "entries": normalized}


def _load_queue(cwd: Path) -> dict[str, Any]:
    path = _queue_path(cwd)
    if not path.exists():
        return {"schema": SCHEMA, "entries": []}
    return _validate_queue(_load_json_bytes(_read_regular_file(path)))


def _queue_digest(entry: dict[str, Any], now: datetime) -> str:
    canonical = json.dumps(
        {
            "issue_ref_key": entry["issue_ref_key"],
            "pr_ref": entry["pr_ref"],
            "head_sha": entry["head_sha"],
            "accepted_base_sha": entry["accepted_base_sha"],
            "depends_on": entry["depends_on"],
            "session_id": entry["session_id"],
            "enqueued_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def _write_queue_unlocked(cwd: Path, queue: dict[str, Any]) -> Path:
    """Atomic write of the queue document. Caller must hold the queue lock."""
    path = _queue_path(cwd)
    root = path.parent
    temp_path: Path | None = None
    try:
        data = json.dumps(queue, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=root, prefix=_TMP_PREFIX, suffix=".json") as tmp:
            temp_path = Path(tmp.name)
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(temp_path, path)
        _fsync_directory(root)
        return path
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
        raise


def _locked_queue_update(cwd: Path, mutate):
    """Run read → mutate → write as one critical section under the queue lock.

    mutate(queue) は queue を更新して結果 dict を返す。書き込みを省略したい場合は
    (result, False) のタプルを返す。
    """
    root = _ensure_mission_state_dir(cwd)
    lock_fd = _lock_file(root)
    try:
        queue = _load_queue(cwd)
        outcome = mutate(queue)
        if isinstance(outcome, tuple):
            result, should_write = outcome
        else:
            result, should_write = outcome, True
        if should_write:
            _write_queue_unlocked(cwd, queue)
        return result
    finally:
        os.close(lock_fd)


def _current_session_id() -> str:
    return (
        os.environ.get("MISSION_SESSION_ID")
        or os.environ.get("CLAUDE_CODE_SESSION_ID")
        or os.environ.get("CODEX_THREAD_ID")
        or "test"
    )


def _find_entry(queue: dict[str, Any], queue_id: str) -> dict[str, Any] | None:
    for entry in queue["entries"]:
        if entry["queue_id"] == queue_id:
            return entry
    return None


def _merged_issue_refs(queue: dict[str, Any]) -> set[str]:
    return {entry["issue_ref_key"] for entry in queue["entries"] if entry["status"] == "merged"}


def _candidate_entries(queue: dict[str, Any]) -> list[dict[str, Any]]:
    merged = _merged_issue_refs(queue)
    candidates = []
    for entry in queue["entries"]:
        if entry["status"] not in {"queued", "ready"}:
            continue
        if all(dep in merged for dep in entry["depends_on"]):
            candidates.append(entry)
    return candidates


def _blocked_entries(queue: dict[str, Any]) -> list[dict[str, Any]]:
    """依存未解決で next に出られない entry と、その理由を返す (I-1: silent deadlock 防止)."""
    merged = _merged_issue_refs(queue)
    status_by_ref: dict[str, str] = {}
    for entry in queue["entries"]:
        status_by_ref[entry["issue_ref_key"]] = entry["status"]
    blocked = []
    for entry in queue["entries"]:
        if entry["status"] not in {"queued", "ready"}:
            continue
        reasons = []
        for dep in entry["depends_on"]:
            if dep in merged:
                continue
            dep_status = status_by_ref.get(dep, "missing")
            reasons.append(f"{dep} ({dep_status})")
        if reasons:
            blocked.append({
                "queue_id": entry["queue_id"],
                "issue_ref_key": entry["issue_ref_key"],
                "blocked_by": reasons,
            })
    return blocked


def enqueue(
    cwd: Path,
    *,
    issue_ref: Any,
    pr_ref: Any,
    head_sha: Any,
    base_sha: Any,
    depends_on: list[Any] | None = None,
    session_id: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now_utc = _queue_now(now)
    issue_ref_key = _issue_ref_key(issue_ref)
    pr_ref_text = str(pr_ref).strip()
    if not pr_ref_text:
        raise MergeQueueError("merge queue pr_ref is invalid")
    new_entry = {
        "queue_id": "",
        "session_id": (session_id or _current_session_id()).strip(),
        "issue_ref_key": issue_ref_key,
        "pr_ref": pr_ref_text,
        "head_sha": _validate_sha(head_sha, field="head_sha"),
        "accepted_base_sha": _validate_sha(base_sha, field="accepted_base_sha"),
        "depends_on": [_issue_ref_key(item) for item in (depends_on or [])],
        "status": "queued",
        "reason": "",
        "enqueued_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    new_entry["queue_id"] = _queue_digest(new_entry, now_utc)
    if issue_ref_key in new_entry["depends_on"]:
        raise MergeQueueError("merge queue depends_on must not reference the entry itself")

    def _mutate(queue: dict[str, Any]):
        # 依存先が未 enqueue のケースは正当 (兄弟 mission の完走順は不定) なので拒否しない。
        # typo 検出のため unknown_depends_on として可視化し、next の blocked_by でも missing 表示する。
        known_refs = {entry["issue_ref_key"] for entry in queue["entries"]}
        unknown_deps = [dep for dep in new_entry["depends_on"] if dep not in known_refs]
        retained: list[dict[str, Any]] = []
        for entry in queue["entries"]:
            if entry["issue_ref_key"] == issue_ref_key and entry["status"] in {"queued", "ready"}:
                retained.append({**entry, "status": "superseded", "reason": "replaced by newer enqueue", "updated_at": new_entry["updated_at"]})
                continue
            retained.append(entry)
        retained.append(new_entry)
        queue["entries"] = retained
        result = {"status": "ok", "queue_id": new_entry["queue_id"], "entry": new_entry}
        if unknown_deps:
            result["unknown_depends_on"] = unknown_deps
        return result

    return _locked_queue_update(cwd, _mutate)


def status(cwd: Path) -> dict[str, Any]:
    queue = _load_queue(cwd)
    return {"status": "ok", "entries": queue["entries"]}


def next_candidate(cwd: Path) -> dict[str, Any]:
    queue = _load_queue(cwd)
    candidates = _candidate_entries(queue)
    if not candidates:
        result: dict[str, Any] = {"status": "empty"}
        blocked = _blocked_entries(queue)
        if blocked:
            result["blocked"] = blocked
        return result
    return {"status": "ok", "entry": candidates[0]}


def verify(cwd: Path, *, queue_id: str, current_base_sha: Any, now: datetime | None = None) -> dict[str, Any]:
    current_base_sha_text = _validate_sha(current_base_sha, field="current_base_sha")

    def _mutate(queue: dict[str, Any]):
        entry = _find_entry(queue, queue_id)
        if entry is None:
            raise MergeQueueError("merge queue entry is missing")
        if entry["status"] not in {"queued", "ready"}:
            raise MergeQueueError("merge queue entry is not mergeable")
        if entry["accepted_base_sha"] != current_base_sha_text:
            entry["status"] = "invalidated"
            entry["reason"] = "base changed; refreeze required"
            entry["updated_at"] = _queue_now(now).strftime("%Y-%m-%dT%H:%M:%SZ")
            return {"__base_mismatch__": True}
        return ({"status": "ok", "queue_id": entry["queue_id"], "entry": entry}, False)

    result = _locked_queue_update(cwd, _mutate)
    if isinstance(result, dict) and result.get("__base_mismatch__"):
        raise BaseMismatchError(
            "base changed; refreeze required: re-integrate base, refreeze with the new head sha, then request fresh review"
        )
    return result


def mark(cwd: Path, *, queue_id: str, status_value: str, reason: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    def _mutate(queue: dict[str, Any]):
        entry = _find_entry(queue, queue_id)
        if entry is None:
            raise MergeQueueError("merge queue entry is missing")
        if entry["status"] in {"merged", "invalidated", "superseded"}:
            raise MergeQueueError("merge queue entry is terminal")
        if status_value not in {"merged", "invalidated", "superseded"}:
            raise MergeQueueError("merge queue status is invalid")
        if status_value == "merged" and entry["status"] not in {"queued", "ready"}:
            raise MergeQueueError("merge queue entry is not mergeable")
        if status_value == entry["status"]:
            raise MergeQueueError("merge queue entry is already in that status")
        entry["status"] = status_value
        entry["reason"] = (reason or "").strip()
        entry["updated_at"] = _queue_now(now).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"status": "ok", "queue_id": entry["queue_id"], "entry": entry}

    return _locked_queue_update(cwd, _mutate)
