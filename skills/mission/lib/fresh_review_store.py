"""Private, bounded operation copies; never a credential or capability store."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from contextlib import ExitStack, contextmanager
from functools import wraps
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time

from fresh_review_observation import ObservationBlocked
from fresh_review_quarantine import JOURNAL_LIMIT, QUARANTINE_LIMIT, remove_owned, resume_quarantines, retirement_pending, valid_identity
from mission_kernel.errors import StrictReadError
from exec_cleanup import cleanup_scope, finish_cleanup
from mission_persistence.spawn_jobs import private_directory, read_job
from mission_persistence.strict_reader import read_stable_bytes_beneath

COPY_LIMIT = 8 * 1024 * 1024
EVIDENCE_LIMIT = 16 * 1024 * 1024
_ID = re.compile(r'[A-Za-z0-9_.:-]{1,128}\Z')
_DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')
MAX_FILES = 256
MANIFEST_LIMIT = 48 * 1024
MAX_GENERATIONS = MAX_FILES + 1
METADATA_RESERVE = MAX_GENERATIONS * MANIFEST_LIMIT + QUARANTINE_LIMIT * JOURNAL_LIMIT
EVIDENCE_PAYLOAD_LIMIT = EVIDENCE_LIMIT - METADATA_RESERVE


def _blocked_io(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ObservationBlocked:
            raise
        except (OSError, ValueError, TypeError) as error:
            raise ObservationBlocked('private operation unavailable') from error
    return guarded


def _serialized(function):
    @wraps(function)
    def guarded(self, *args, **kwargs):
        with ExitStack() as stack:
            try:
                stack.enter_context(self._operation_lock())
            except (OSError, ObservationBlocked) as error:
                if function.__name__ == 'cleanup':
                    return False
                raise ObservationBlocked('operation lock unavailable') from error
            if function.__name__ != 'cleanup':
                parent = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                with cleanup_scope(os.close, parent):
                    if retirement_pending(parent, self.path.name, self._directory_identity):
                        raise ObservationBlocked('operation retirement pending')
            return function(self, *args, **kwargs)
    return guarded


def _clock(value):
    if (type(value) not in (int, float) or not 0 <= value <= 2 ** 53 - 1
            or not math.isfinite(value)):
        raise ObservationBlocked('invalid cleanup clock')
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ObservationBlocked('duplicate manifest field')
        result[key] = value
    return result


@dataclass(frozen=True)
class StoredFile:
    name: str
    digest: str
    size: int
    device: int
    inode: int


class PrivateOperationStore:
    @_blocked_io
    def __init__(self, parent, *, now=time.time):
        self._created = _clock(now())
        self._expires = _clock(self._created + 600)
        private_directory(Path(parent))
        self._path = Path(tempfile.mkdtemp(prefix='operation-', dir=parent))
        info = self.path.lstat()
        self._directory_identity = (info.st_dev, info.st_ino)
        self._files = []
        self._copy_bytes = 0
        self._evidence_bytes = 0
        self._manifest = None
        self._manifests = []
        self._generation = 0
        self._sealed = False
        with self._directory() as directory:
            self._lock_record = self._create(directory, 'operation.lock', b'')
        try:
            with self._operation_lock():
                with self._directory() as directory:
                    self._persist(directory)
        except BaseException as error:
            finish_cleanup(self._discard_initial, original=error,
                           suppress=(OSError, ObservationBlocked))
            raise

    @property
    def path(self):
        return self._path

    def _discard_initial(self):
        if self._manifest is not None:
            self.cleanup(now=self._created, force=True)

    @contextmanager
    def _directory(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as error:
            raise ObservationBlocked('operation directory unavailable') from error
        with cleanup_scope(os.close, fd):
            self._check_directory(fd)
            yield fd

    def _check_directory(self, fd):
        info, named = os.fstat(fd), self.path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700
                or (info.st_dev, info.st_ino) != self._directory_identity
                or (named.st_dev, named.st_ino) != self._directory_identity):
            raise ObservationBlocked('operation directory changed')

    @contextmanager
    def _operation_lock(self):
        with self._directory() as directory:
            fd = os.open('operation.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
            with cleanup_scope(os.close, fd):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                info = os.fstat(fd)
                named = os.stat('operation.lock', dir_fd=directory, follow_symlinks=False)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600
                        or (info.st_dev, info.st_ino) != (self._lock_record.device, self._lock_record.inode)
                        or (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino)):
                    raise ObservationBlocked('operation lock changed')
                self._check_directory(directory)
                self._active_lock = fd
                try:
                    yield
                finally:
                    self._active_lock = None

    @_blocked_io
    @_serialized
    def copy_input(self, root, relative_path, digest):
        try:
            raw = read_stable_bytes_beneath(root, relative_path,
                                           limit=COPY_LIMIT - self._copy_bytes).payload
        except (StrictReadError, OSError) as error:
            raise ObservationBlocked('input copy unavailable') from error
        if 'sha256:' + hashlib.sha256(raw).hexdigest() != digest:
            raise ObservationBlocked('copy digest mismatch')
        stored = self._write('input', raw)
        self._copy_bytes += len(raw)
        return stored

    @_blocked_io
    @_serialized
    def record_evidence(self, record):
        identifiers = {'session_id', 'boot_id', 'operation_id'}
        digests = {'request_digest', 'context_digest'}
        if (type(record) is not dict or set(record) != identifiers | digests | {'custody'}
                or any(type(value) is not str for value in record.values())
                or any(not _ID.fullmatch(record[key]) for key in identifiers)
                or any(not _DIGEST.fullmatch(record[key]) for key in digests)
                or record['custody'] not in {'owned', 'stopped', 'unknown'}):
            raise ObservationBlocked('unsafe evidence projection')
        raw = json.dumps(record, sort_keys=True, separators=(',', ':')).encode('ascii')
        if self._evidence_bytes + len(raw) > EVIDENCE_PAYLOAD_LIMIT:
            raise ObservationBlocked('evidence byte limit')
        stored = self._write('evidence', raw)
        self._evidence_bytes += len(raw)
        return stored

    def _write(self, kind, raw):
        if self._sealed or len(self._files) >= MAX_FILES:
            raise ObservationBlocked('operation file limit')
        name = f'{kind}-{len(self._files)}'
        with self._directory() as directory:
            stored = self._create(directory, name, raw)
            self._files.append(stored)
            try:
                self._read_file(stored)
                self._persist(directory)
            except BaseException:
                self._files.pop()
                self._unlink_owned(directory, name, stored.device, stored.inode)
                raise
            return stored

    def _create(self, directory, name, raw):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        with cleanup_scope(os.close, fd):
            info = os.fstat(fd)
            try:
                os.fchmod(fd, 0o600)
                view = memoryview(raw)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError('private write made no progress')
                    view = view[written:]
                os.fsync(fd)
            except BaseException:
                self._unlink_owned(directory, name, info.st_dev, info.st_ino)
                raise
        return StoredFile(name, 'sha256:' + hashlib.sha256(raw).hexdigest(), len(raw),
                          info.st_dev, info.st_ino)

    def _persist(self, directory):
        if self._generation >= MAX_GENERATIONS:
            raise ObservationBlocked('operation generation limit')
        document = dict(schema='mission-private-operation/1',
                        directory=list(self._directory_identity), created=self._created,
                        expires=self._expires, files=[asdict(item) for item in self._files],
                        lock=asdict(self._lock_record), generation=self._generation)
        raw = json.dumps(document, sort_keys=True, separators=(',', ':')).encode('ascii')
        if len(raw) > MANIFEST_LIMIT:
            raise ObservationBlocked('operation manifest limit')
        name = f'manifest-{self._generation}.json'
        self._generation += 1
        manifest = self._create(directory, name, raw)
        self._read_file(manifest)
        self._manifests.append(manifest)
        self._manifest = manifest
        os.fsync(directory)

    def _unlink_owned(self, directory, name, device, inode):
        return remove_owned(directory, name, (device, inode), expires=self._expires,
                            now=self._created)

    @_serialized
    def cleanup(self, *, now, force=False):
        if type(force) is not bool:
            raise ObservationBlocked('invalid cleanup flag')
        if _clock(now) < self._created or (not force and now < self._expires):
            return False
        self._sealed = True
        try:
            with self._directory() as directory:
                pending = resume_quarantines(directory, now=now, force=force)
                results = [self._unlink_owned(directory, item.name, item.device, item.inode)
                           for item in self._files]
                results += [self._unlink_owned(directory, item.name, item.device, item.inode)
                            for item in self._manifests[:-1]]
                if not all(results) or not pending:
                    return False
                self._check_directory(directory)
                if set(os.listdir(directory)) != {self._manifest.name, 'operation.lock'}:
                    return False
                parent = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                with cleanup_scope(os.close, parent):
                    self._check_directory(directory)
                    final = [dict(name=item.name, expected=[item.device, item.inode])
                             for item in (self._manifest, self._lock_record)]
                    return remove_owned(parent, self.path.name, self._directory_identity,
                                        kind='directory', expires=self._expires, now=now,
                                        final=final, locked_fd=self._active_lock)
        except (OSError, ObservationBlocked):
            return False

    @_blocked_io
    @_serialized
    def read(self, stored):
        if type(stored) is not StoredFile or stored not in self._files:
            raise ObservationBlocked('copy is not owned by operation')
        return self._read_file(stored)

    def _read_file(self, stored):
        try:
            with self._directory() as directory:
                info = (self.path / stored.name).lstat()
                if (info.st_dev, info.st_ino) != (stored.device, stored.inode):
                    raise ObservationBlocked('copy identity changed')
                raw = read_job(self.path / stored.name, stored.digest.removeprefix('sha256:'),
                               limit=max(stored.size, 1))
                self._check_directory(directory)
                after = os.stat(stored.name, dir_fd=directory, follow_symlinks=False)
                if ((after.st_dev, after.st_ino) != (stored.device, stored.inode)
                        or not stat.S_ISREG(after.st_mode) or after.st_nlink != 1
                        or after.st_uid != os.getuid() or stat.S_IMODE(after.st_mode) != 0o600):
                    raise ObservationBlocked('copy identity changed after read')
                return raw
        except (ValueError, OSError) as error:
            raise ObservationBlocked('private copy unavailable') from error

    @classmethod
    @_blocked_io
    def recover_expired(cls, parent, *, now):
        """Owner-private metadata authorizes file cleanup, never process recovery."""
        _clock(now)
        parent = Path(parent)
        private_directory(parent)
        removed = []
        parent_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        with cleanup_scope(os.close, parent_fd):
            resume_quarantines(parent_fd, now=now)
        for path in sorted(parent.glob('operation-*')):
            try:
                store = cls.__new__(cls)
                store._path = path
                info = path.lstat()
                store._directory_identity = (info.st_dev, info.st_ino)
                store._load()
                if store.cleanup(now=now):
                    removed.append(path.name)
            except (ValueError, OSError, StrictReadError, TypeError, KeyError, RecursionError):
                continue
        return tuple(removed)

    def _load(self):
        manifests, generations = [], []
        with self._directory():
            for path in self.path.glob('manifest-*.json'):
                if not re.fullmatch(r'manifest-(?:0|[1-9]\d{0,2})\.json', path.name):
                    continue
                generation = int(path.name[9:-5])
                if generation >= MAX_GENERATIONS:
                    continue
                generations.append(generation)
                try:
                    raw = read_stable_bytes_beneath(self.path, path.name,
                                                   limit=MANIFEST_LIMIT).payload
                    info = path.lstat()
                    manifest = StoredFile(path.name, 'sha256:' + hashlib.sha256(raw).hexdigest(),
                                          len(raw), info.st_dev, info.st_ino)
                    self._read_file(manifest)
                    document = json.loads(raw, object_pairs_hook=_unique)
                    self._restore(document)
                    if document['generation'] != generation:
                        raise ObservationBlocked('generation binding mismatch')
                    manifests.append((generation, manifest, document))
                except (ValueError, OSError, StrictReadError, TypeError, KeyError, RecursionError):
                    continue
        if not manifests:
            raise ObservationBlocked('no complete operation manifest')
        manifests.sort(key=lambda entry: entry[0])
        self._restore(manifests[-1][2])
        self._manifests = [entry[1] for entry in manifests]
        self._manifest = self._manifests[-1]
        self._generation = max(generations) + 1
        self._sealed = False

    def _restore(self, document):
        if (type(document) is not dict
                or set(document) != {'schema', 'directory', 'created', 'expires', 'files', 'lock', 'generation'}
                or document['schema'] != 'mission-private-operation/1'
                or not valid_identity(document['directory'])
                or document['directory'] != list(self._directory_identity)
                or type(document['files']) is not list or len(document['files']) > MAX_FILES):
            raise ObservationBlocked('unsafe operation manifest')
        self._created = _clock(document['created'])
        self._expires = _clock(document['expires'])
        if self._expires != self._created + 600:
            raise ObservationBlocked('unsafe operation expiry')
        lock = document['lock']
        if (type(lock) is not dict or set(lock) != {'name', 'digest', 'size', 'device', 'inode'}
                or lock['name'] != 'operation.lock' or type(lock['size']) is not int or lock['size'] != 0
                or lock['digest'] != 'sha256:' + hashlib.sha256(b'').hexdigest()
                or any(type(lock[key]) is not int or lock[key] < 0 for key in ('device', 'inode'))
                or type(document['generation']) is not int
                or not 0 <= document['generation'] < MAX_GENERATIONS):
            raise ObservationBlocked('unsafe operation lock/generation')
        self._lock_record = StoredFile(**lock)
        self._files = []
        for index, item in enumerate(document['files']):
            if (type(item) is not dict
                    or set(item) != {'name', 'digest', 'size', 'device', 'inode'}
                    or item['name'] not in {f'input-{index}', f'evidence-{index}'}
                    or type(item['digest']) is not str or not _DIGEST.fullmatch(item['digest'])
                    or any(type(item[key]) is not int or item[key] < 0
                           for key in ('size', 'device', 'inode'))):
                raise ObservationBlocked('unsafe file identity')
            self._files.append(StoredFile(**item))
        self._copy_bytes = sum(item.size for item in self._files if item.name.startswith('input-'))
        self._evidence_bytes = sum(item.size for item in self._files if item.name.startswith('evidence-'))
        if self._copy_bytes > COPY_LIMIT or self._evidence_bytes > EVIDENCE_PAYLOAD_LIMIT:
            raise ObservationBlocked('unsafe operation byte totals')
