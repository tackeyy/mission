"""Local evidence handoff sidecar contract (#422)."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA = "mission-evidence-handoff/1"
_TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ENVELOPE_RE = re.compile(r"^(?P<seq>[1-9][0-9]*)-(?P<digest>[0-9a-f]{8})\.json$")
_TMP_PREFIX = ".tmp-"


class EvidenceHandoffError(ValueError):
    """Raised when a handoff document or directory is invalid."""


class EvidenceHandoffTimeout(EvidenceHandoffError):
    """Raised when await exhausts its timeout without finding a new handoff."""


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _state_dir(cwd: Path) -> Path:
    return cwd / ".mission-state"


def _ensure_mission_state_dir(cwd: Path) -> Path:
    root = _state_dir(cwd)
    try:
        metadata = root.lstat()
    except FileNotFoundError as exc:
        raise EvidenceHandoffError("mission state directory is missing") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise EvidenceHandoffError("mission state directory is unsafe")
    return root


def _validate_topic(topic: Any) -> str:
    if not isinstance(topic, str) or not _TOPIC_RE.fullmatch(topic):
        raise EvidenceHandoffError("handoff topic slug is invalid")
    return topic


def _topic_dir(cwd: Path, topic: str, *, create: bool) -> Path:
    root = _ensure_mission_state_dir(cwd)
    handoff_root = root / "handoff"
    if create:
        handoff_root.mkdir(mode=0o700, exist_ok=True)
    elif handoff_root.exists():
        handoff_root_meta = handoff_root.lstat()
        if not stat.S_ISDIR(handoff_root_meta.st_mode) or stat.S_ISLNK(handoff_root_meta.st_mode):
            raise EvidenceHandoffError("handoff directory is unsafe")
    elif handoff_root.is_symlink():
        raise EvidenceHandoffError("handoff directory is unsafe")
    topic_dir = handoff_root / topic
    if create:
        topic_dir.mkdir(mode=0o700, exist_ok=True)
    elif topic_dir.exists():
        topic_meta = topic_dir.lstat()
        if not stat.S_ISDIR(topic_meta.st_mode) or stat.S_ISLNK(topic_meta.st_mode):
            raise EvidenceHandoffError("handoff topic directory is unsafe")
    elif topic_dir.is_symlink():
        raise EvidenceHandoffError("handoff topic directory is unsafe")
    return topic_dir


def _canonical_payload_bytes(payload: Any) -> bytes:
    try:
        return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvidenceHandoffError("handoff payload is not JSON serializable") from exc


def payload_digest(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_payload_bytes(payload)).hexdigest()


def load_payload(source: str) -> Any:
    if source == "-":
        raw = sys.stdin.buffer.read()
    else:
        raw = Path(source).read_bytes()
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceHandoffError("handoff input is not valid JSON") from exc


def _load_json_bytes(data: bytes) -> dict[str, Any]:
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceHandoffError("handoff envelope is not valid JSON") from exc
    if not isinstance(document, dict):
        raise EvidenceHandoffError("handoff envelope is not a JSON object")
    return document


def _read_regular_file(path: Path) -> bytes:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise EvidenceHandoffError("handoff file is missing") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1:
        raise EvidenceHandoffError("handoff file is unsafe")
    try:
        return path.read_bytes()
    except OSError as exc:
        raise EvidenceHandoffError("handoff file is unreadable") from exc


def _validate_envelope(document: dict[str, Any], *, path: Path) -> dict[str, Any]:
    required = {"schema", "topic", "seq", "created_at", "producer_session", "payload_digest", "payload"}
    if set(document) != required:
        raise EvidenceHandoffError("handoff envelope shape is invalid")
    if document["schema"] != SCHEMA:
        raise EvidenceHandoffError("handoff schema is invalid")
    topic = _validate_topic(document["topic"])
    seq = document["seq"]
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        raise EvidenceHandoffError("handoff sequence is invalid")
    match = _ENVELOPE_RE.fullmatch(path.name)
    if match is None or int(match.group("seq")) != seq:
        raise EvidenceHandoffError("handoff path does not match sequence")
    digest = document["payload_digest"]
    if not isinstance(digest, str) or not digest.startswith("sha256:") or len(digest) != 71:
        raise EvidenceHandoffError("handoff payload digest is invalid")
    created_at = document["created_at"]
    if not isinstance(created_at, str) or not created_at.endswith("Z"):
        raise EvidenceHandoffError("handoff created_at is invalid")
    producer_session = document["producer_session"]
    if not isinstance(producer_session, str):
        raise EvidenceHandoffError("handoff producer_session is invalid")
    return {
        "schema": SCHEMA,
        "topic": topic,
        "seq": seq,
        "created_at": created_at,
        "producer_session": producer_session,
        "payload_digest": digest,
        "payload": document["payload"],
    }


def _topic_files(topic_dir: Path) -> list[Path]:
    if not topic_dir.exists():
        return []
    files: list[tuple[int, Path]] = []
    for entry in topic_dir.iterdir():
        if entry.name.startswith(_TMP_PREFIX):
            continue
        match = _ENVELOPE_RE.fullmatch(entry.name)
        if match is None:
            continue
        files.append((int(match.group("seq")), entry))
    return [path for _seq, path in sorted(files, key=lambda item: (item[0], item[1].name))]


def _fsync_directory(path: Path) -> None:
    fd = os.open(os.fspath(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _lock_file(topic_dir: Path):
    lock_path = topic_dir / ".handoff.lock"
    fd = os.open(os.fspath(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def publish(cwd: Path, topic: str, payload: Any, *, producer_session: str | None = None) -> dict[str, Any]:
    topic = _validate_topic(topic)
    topic_dir = _topic_dir(cwd, topic, create=True)
    lock_fd = _lock_file(topic_dir)
    temp_path: Path | None = None
    try:
        seq = 1
        existing = _topic_files(topic_dir)
        if existing:
            match = _ENVELOPE_RE.fullmatch(existing[-1].name)
            assert match is not None
            seq = int(match.group("seq")) + 1
        digest = payload_digest(payload)
        envelope = {
            "schema": SCHEMA,
            "topic": topic,
            "seq": seq,
            "created_at": _now_utc(),
            "producer_session": producer_session if producer_session is not None else "",
            "payload_digest": digest,
            "payload": payload,
        }
        if envelope["producer_session"] == "":
            envelope["producer_session"] = os.environ.get("MISSION_SESSION_ID", "") or os.environ.get("CODEX_THREAD_ID", "") or os.environ.get("CLAUDE_CODE_SESSION_ID", "")
        final_path = topic_dir / f"{seq}-{digest.removeprefix('sha256:')[:8]}.json"
        envelope = _validate_envelope(envelope, path=final_path)
        data = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=topic_dir, prefix=_TMP_PREFIX, suffix=".json") as tmp:
            temp_path = Path(tmp.name)
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
        try:
            os.link(temp_path, final_path, follow_symlinks=False)
        except FileExistsError as exc:
            raise EvidenceHandoffError("handoff destination appeared before publish") from exc
        temp_path.unlink()
        temp_path = None
        _fsync_directory(topic_dir)
        return {"path": str(final_path), "seq": seq, "payload_digest": digest}
    except Exception as exc:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
        if isinstance(exc, EvidenceHandoffError):
            raise
        if isinstance(exc, OSError):
            raise EvidenceHandoffError("handoff publish failed") from exc
        raise
    finally:
        os.close(lock_fd)


def _match_newer(topic_dir: Path, after_seq: int) -> Path | None:
    for entry in _topic_files(topic_dir):
        match = _ENVELOPE_RE.fullmatch(entry.name)
        if match is None:
            continue
        seq = int(match.group("seq"))
        if seq > after_seq:
            return entry
    return None


def await_handoff(
    cwd: Path, topic: str, *, after_seq: int = 0, timeout_sec: int = 600
) -> dict[str, Any]:
    topic = _validate_topic(topic)
    topic_dir = _topic_dir(cwd, topic, create=False)
    if timeout_sec < 0:
        raise EvidenceHandoffError("handoff timeout is invalid")
    deadline = time.monotonic() + timeout_sec
    while True:
        candidate = _match_newer(topic_dir, after_seq)
        if candidate is not None:
            document = _validate_envelope(_load_json_bytes(_read_regular_file(candidate)), path=candidate)
            digest = payload_digest(document["payload"])
            if digest != document["payload_digest"]:
                raise EvidenceHandoffError("handoff payload digest mismatch")
            document["path"] = str(candidate)
            return document
        if time.monotonic() >= deadline:
            raise EvidenceHandoffTimeout("handoff await timed out")
        time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))


def verify_handoff(path: str | Path, *, expect_digest: str | None = None, cwd: Path | None = None) -> dict[str, Any]:
    candidate = Path(path)
    if not candidate.is_absolute() and cwd is not None:
        candidate = cwd / candidate
    document = _validate_envelope(_load_json_bytes(_read_regular_file(candidate)), path=candidate)
    digest = payload_digest(document["payload"])
    if digest != document["payload_digest"]:
        raise EvidenceHandoffError("handoff payload digest mismatch")
    if expect_digest is not None and expect_digest != digest:
        raise EvidenceHandoffError("handoff expected digest mismatch")
    return {"path": str(candidate), "payload_digest": digest, "seq": document["seq"], "topic": document["topic"]}
