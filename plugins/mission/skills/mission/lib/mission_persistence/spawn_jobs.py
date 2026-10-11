"""Private exec jobs and conservative crash-file recovery (no external commands)."""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time
from exec_cleanup import cleanup_scope, finish_cleanup

JOB_LIMIT = 4 * 1024 * 1024
_NAME = re.compile(r'job-([1-9][0-9]*)-([0-9]+)(?:-s([0-9a-f]{64}))?(?:-r([a-zA-Z0-9_]+))?-([0-9a-f]{32})\.json')


class JobWriteError(ValueError):
    """No child started. A failed unlink is exposed for refusal telemetry."""
    reason_code = 'budget-job-write-failed'

    def __init__(self, path, cleanup_errno=None):
        super().__init__(self.reason_code)
        self.path, self.cleanup_errno = path, cleanup_errno


def private_directory(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('unsafe private directory')


def read_job(path: Path, digest: str, *, limit=JOB_LIMIT) -> bytes:
    private_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with cleanup_scope(os.close, fd):
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > limit):
            raise ValueError('unsafe private file')
        chunks, remaining = [], limit + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b''.join(chunks)
        after, named = os.fstat(fd), path.lstat()
        if (len(raw) != info.st_size or len(raw) > limit
                or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                or (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino)
                or hashlib.sha256(raw).hexdigest() != digest):
            raise ValueError('private file changed')
        return raw


def write_private_file(path: Path, content: bytes, *, fsync=None) -> None:
    """Exclusive creation; delete only a file this call actually created."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with cleanup_scope(os.close, fd):
            os.fchmod(fd, 0o600)
            view = memoryview(content)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError(errno.EIO, 'write made no progress')
                view = view[written:]
            (fsync or os.fsync)(fd)
        # Caller ensures the private directory before creation.
        read_job(path, hashlib.sha256(content).hexdigest(), limit=max(len(content), 1))
    except BaseException as error:
        finish_cleanup(path.unlink, missing_ok=True, original=error, suppress=(OSError,))
        raise


def process_start(pid: int) -> str | None:
    """Start identity, None if unknown; ProcessLookupError only for proven absence."""
    if sys.platform.startswith('linux'):
        try:
            raw = Path(f'/proc/{pid}/stat').read_text()
            return raw[raw.rindex(')') + 2:].split()[19]
        except FileNotFoundError as exc:
            raise ProcessLookupError(pid) from exc
        except (OSError, ValueError, IndexError):
            return None
    if sys.platform == 'darwin':
        # kinfo_proc begins with extern_proc's union containing p_starttime.
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 4)(1, 14, 1, pid)  # CTL_KERN/KERN_PROC/KERN_PROC_PID
        buffer = ctypes.create_string_buffer(4096)
        size = ctypes.c_size_t(len(buffer))
        if libc.sysctl(mib, 4, buffer, ctypes.byref(size), None, 0) != 0:
            return None
        if size.value == 0:
            raise ProcessLookupError(pid)
        if size.value < 16:
            return None
        stamp = (ctypes.c_long * 2).from_buffer_copy(buffer.raw[:16])
        return str(stamp[0] * 1000000 + stamp[1]) if stamp[0] > 0 else None
    return None


def session_token(session_id):
    if not isinstance(session_id, str) or not session_id:
        raise ValueError('session identity invalid')
    return hashlib.sha256(session_id.encode()).hexdigest()


def create_job(directory: Path, raw: bytes, *, reservation_id: str | None = None, session_id=None):
    path = None
    try:
        if not raw or len(raw) > JOB_LIMIT:
            raise ValueError('job size invalid')
        private_directory(directory)
        start = process_start(os.getpid())
        if start is None:
            raise ValueError('owner identity unavailable')
        reservation = ''
        if reservation_id is not None:
            if not re.fullmatch(r'[a-zA-Z0-9_]+', reservation_id):
                raise ValueError('reservation identity invalid')
            reservation = '-r' + reservation_id
        session = '' if session_id is None else '-s' + session_token(session_id)
        path = directory / f'job-{os.getpid()}-{start}{session}{reservation}-{secrets.token_hex(16)}.json'
        write_private_file(path, raw)
        return path.absolute(), hashlib.sha256(raw).hexdigest()
    except Exception as exc:
        cleanup_errno = None
        # O_EXCL collision does not confer ownership of an existing file.
        if path is not None and not isinstance(exc, FileExistsError):
            cleanup = finish_cleanup(path.unlink, missing_ok=True, original=exc)
            if isinstance(cleanup, OSError):
                cleanup_errno = cleanup.errno
        raise JobWriteError(path, cleanup_errno) from exc


def _expired_job(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with cleanup_scope(os.close, fd):
        raw = os.read(fd, JOB_LIMIT + 1)
    # Reuse the bounded inode/mode/content check before trusting an expiry.
    value = json.loads(read_job(path, hashlib.sha256(raw).hexdigest()))
    expires = value.get('expires_at') if isinstance(value, dict) else None
    return type(expires) in (int, float) and 0 < expires <= time.time()


def cleanup_jobs(directory: Path, *, open_reservations=None, closed_reservations=(), session_id=None) -> list[Path]:
    """Unknown reservation/owner identity is preserved, including legacy jobs."""
    if not directory.exists():
        return []
    private_directory(directory)
    removed = []
    for path in directory.iterdir():
        match = _NAME.fullmatch(path.name)
        if match is None:
            continue
        pid, start, session, reservation, _ = match.groups()
        # Absence from one session's ledger says nothing about another session.
        # Only a positively identified terminal reservation authorizes removal.
        if reservation and (session is None or session_id is None or session != session_token(session_id)
                            or reservation not in closed_reservations
                            or open_reservations is None or reservation in open_reservations):
            continue
        try:
            observed = process_start(int(pid))
        except ProcessLookupError:
            observed = 'absent'
        if observed is None or observed == start:
            continue
        try:
            if not reservation and not _expired_job(path):
                continue  # dead parent alone does not prove its child read the job
            info = path.lstat()
            if (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600):
                finish_cleanup(path.unlink)
                removed.append(path)
        except (OSError, ValueError):
            pass
    return removed
