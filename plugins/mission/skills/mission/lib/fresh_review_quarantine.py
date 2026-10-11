"""Identity-bound private removal; public names are captured before deletion.

The trusted OS user may concurrently replace public source names. Quarantine
namespaces are exclusively managed here; arbitrary same-user tampering inside
them is outside this portability guarantee.
"""
from contextlib import ExitStack, contextmanager
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat

from exec_cleanup import cleanup_scope

JOURNAL_LIMIT = 1024
QUARANTINE_LIMIT = 515
_QNAME = re.compile(r'quarantine-[0-9a-f]{16}-[0-9a-f]{32}\Z')
_SOURCE = re.compile(r'(?:input-\d{1,3}|evidence-\d{1,3}|manifest-\d{1,3}\.json|'
                     r'operation-[a-z0-9_]{8}|operation\.lock)\Z')


def identity(info):
    return info.st_dev, info.st_ino


def valid_identity(value):
    return (type(value) is list and len(value) == 2
            and all(type(item) is int and item >= 0 for item in value))


def _private(info, kind):
    return (info.st_uid == os.getuid() and
            (kind == 'file' and stat.S_ISREG(info.st_mode) and info.st_nlink == 1
             and stat.S_IMODE(info.st_mode) == 0o600 or
             kind == 'directory' and stat.S_ISDIR(info.st_mode)
             and stat.S_IMODE(info.st_mode) == 0o700))


def _stat(directory, name):
    try:
        return os.stat(name, dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _matches(info, expected, kind):
    return info is not None and _private(info, kind) and identity(info) == tuple(expected)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate journal field')
        result[key] = value
    return result


@contextmanager
def _journal(directory):
    fd = os.open('journal.json', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                 dir_fd=directory)
    with cleanup_scope(os.close, fd):
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = os.fstat(fd)
        if not _private(before, 'file') or before.st_size > JOURNAL_LIMIT:
            raise ValueError('unsafe cleanup journal')
        raw = os.read(fd, JOURNAL_LIMIT + 1)
        after, named = os.fstat(fd), _stat(directory, 'journal.json')
        if (len(raw) != before.st_size or len(raw) > JOURNAL_LIMIT
                or not _matches(named, identity(before), 'file')
                or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise ValueError('changed cleanup journal')
        yield json.loads(raw, object_pairs_hook=_unique)


def _validate(record, parent):
    keys = {'schema', 'parent', 'name', 'expected', 'kind', 'expires'}
    if type(record) is dict and 'final' in record:
        keys.add('final')
    if (type(record) is not dict or set(record) != keys
            or record['schema'] != 'mission-private-cleanup/1'
            or type(record['name']) is not str or not _SOURCE.fullmatch(record['name'])
            or record['kind'] not in ('file', 'directory')
            or not valid_identity(record['parent'])
            or record['parent'] != list(identity(os.fstat(parent)))
            or not valid_identity(record['expected'])
            or type(record['expires']) not in (int, float)
            or not 0 <= record['expires'] <= 2 ** 53 - 1
            or (record['kind'] == 'directory') != record['name'].startswith('operation-')):
        raise ValueError('unsafe cleanup binding')
    if 'final' in record:
        final = record['final']
        if (record['kind'] != 'directory' or type(final) is not list or len(final) != 2
                or {item['name'] for item in final} != {'operation.lock', final[0]['name']}
                or not re.fullmatch(r'manifest-\d{1,3}\.json', final[0]['name'])):
            raise ValueError('unsafe retirement files')
        for item in final:
            if (type(item) is not dict or set(item) != {'name', 'expected'}
                    or not valid_identity(item['expected'])):
                raise ValueError('unsafe retirement identity')
    hint = hashlib.sha256(json.dumps([record['parent'], record['name'], record['expected']],
                                    separators=(',', ':')).encode()).hexdigest()[:16]
    return hint


def retirement_pending(parent, name, expected):
    binding = [list(identity(os.fstat(parent))), name, list(expected)]
    hint = hashlib.sha256(json.dumps(binding, separators=(',', ':')).encode()).hexdigest()[:16]
    return any(entry.startswith('quarantine-' + hint + '-') for entry in os.listdir(parent))


def _resume(parent, qname, *, now, force, locked_fd=None):
    try:
        fd = os.open(qname, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        with cleanup_scope(os.close, fd):
            if not _private(os.fstat(fd), 'directory'):
                return False
            if not os.listdir(fd):
                return False  # No durable binding survives a missing journal.
            with _journal(fd) as record, ExitStack() as lease:
                hint = _validate(record, parent)
                if not qname.startswith('quarantine-' + hint + '-'):
                    return False
                if not force and now < record['expires']:
                    return False
                source, slot = _stat(parent, record['name']), _stat(fd, 'slot')
                if source is not None and slot is not None:
                    return False
                if slot is None and source is not None:
                    if 'final' in record:
                        operation = os.open(record['name'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                        lease.callback(os.close, operation)
                        lock = locked_fd
                        if lock is None:
                            lock = os.open('operation.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=operation)
                            lease.callback(os.close, lock)
                            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        expected_lock = record['final'][1]['expected']
                        if not _matches(os.fstat(lock), expected_lock, 'file'):
                            return False
                    if not _matches(source, record['expected'], record['kind']):
                        return False
                    os.rename(record['name'], 'slot', src_dir_fd=parent, dst_dir_fd=fd)
                    os.fsync(parent)
                    os.fsync(fd)
                    slot = _stat(fd, 'slot')
                if _stat(parent, record['name']) is not None:
                    return False
                if slot is not None:
                    if not _matches(slot, record['expected'], record['kind']):
                        return False
                    if 'final' in record:
                        operation = os.open('slot', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                        with cleanup_scope(os.close, operation):
                            pending = resume_quarantines(operation, now=now, force=True)
                            outcomes = [remove_owned(operation, item['name'], item['expected'], expires=record['expires'], now=now)
                                        for item in record['final']]
                            if not pending or not all(outcomes) or os.listdir(operation):
                                return False
                    if record['kind'] == 'file':
                        os.unlink('slot', dir_fd=fd)
                    else:
                        os.rmdir('slot', dir_fd=fd)
                    os.fsync(fd)
                if os.listdir(fd) != ['journal.json']:
                    return False
                os.unlink('journal.json', dir_fd=fd)
                os.fsync(fd)
            os.rmdir(qname, dir_fd=parent)
            os.fsync(parent)
            return True
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return False


def resume_quarantines(parent, *, now, force=False):
    """Retry existing journals without allocating another quarantine."""
    results = []
    for name in os.listdir(parent):
        if _QNAME.fullmatch(name):
            results.append(_resume(parent, name, now=now, force=force))
    return all(results)


def remove_owned(parent, name, expected, *, kind='file', expires, now, final=None, locked_fd=None):
    """Never unlink a public name or overwrite a replacement to restore it."""
    try:
        record = dict(schema='mission-private-cleanup/1', parent=list(identity(os.fstat(parent))),
                      name=name, expected=list(expected), kind=kind, expires=expires)
        if final is not None:
            record['final'] = final
        hint = _validate(record, parent)
        prefix = 'quarantine-' + hint + '-'
        pending = [entry for entry in os.listdir(parent) if entry.startswith(prefix)]
        if pending:
            results = [_resume(parent, entry, now=now, force=True, locked_fd=locked_fd) for entry in pending]
            return all(results) and _stat(parent, name) is None
        source = _stat(parent, name)
        if source is None:
            return True
        if not _matches(source, expected, kind):
            return False
        if sum(bool(_QNAME.fullmatch(entry)) for entry in os.listdir(parent)) >= QUARANTINE_LIMIT:
            return False
        raw = json.dumps(record, sort_keys=True, separators=(',', ':')).encode('ascii')
        if len(raw) > JOURNAL_LIMIT:
            return False
        qname = prefix + secrets.token_hex(16)
        os.mkdir(qname, 0o700, dir_fd=parent)
        fd = os.open(qname, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        with cleanup_scope(os.close, fd):
            journal = os.open('journal.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              0o600, dir_fd=fd)
            with cleanup_scope(os.close, journal):
                os.fchmod(journal, 0o600)
                view = memoryview(raw)
                while view:
                    written = os.write(journal, view)
                    if written <= 0:
                        raise OSError('journal write made no progress')
                    view = view[written:]
                os.fsync(journal)
            os.fsync(fd)
        os.fsync(parent)
        return _resume(parent, qname, now=now, force=True, locked_fd=locked_fd)
    except (OSError, ValueError, TypeError, KeyError):
        return False
