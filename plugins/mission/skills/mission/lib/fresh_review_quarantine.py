"""Caller-bound fixed cleanup anchors; root anchors are never publicly retired.

Public names may change concurrently. Dedicated anchor internals are managed
exclusively here; arbitrary same-user tampering inside them is not covered.
"""
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
import re
import secrets
import stat

from fresh_review_observation import ObservationBlocked

JOURNAL_LIMIT = 1024
QUARANTINE_LIMIT = 515
ANCHOR_METADATA_LIMIT = 1024
_ANCHOR = 'cleanup-anchor'
_MARKER = 'anchor.json'
_QNAME = re.compile(r'quarantine-[0-9a-f]{16}-[0-9a-f]{32}\Z')
_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


def identity(info):
    return info.st_dev, info.st_ino


def valid_identity(value):
    return (type(value) in (list, tuple) and len(value) == 2
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
            raise ValueError('duplicate private field')
        result[key] = value
    return result


def _encode(document, limit):
    raw = json.dumps(document, sort_keys=True, separators=(',', ':')).encode('ascii')
    if len(raw) > limit:
        raise ValueError('private metadata limit')
    return raw


def _write(fd, raw):
    view = memoryview(raw)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError('private write made no progress')
        view = view[written:]
    os.fsync(fd)


def _read(fd, directory, name, expected, limit):
    before = os.fstat(fd)
    if not _matches(before, expected, 'file') or before.st_size > limit:
        raise ValueError('unsafe private metadata')
    reader = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        if not _matches(os.fstat(reader), expected, 'file'):
            raise ValueError('metadata reader identity changed')
        raw = os.read(reader, limit + 1)
    finally:
        os.close(reader)
    after = os.fstat(fd)
    if (len(raw) != before.st_size or len(raw) > limit
            or not _matches(_stat(directory, name), expected, 'file')
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise ValueError('changed private metadata')
    return json.loads(raw, object_pairs_hook=_unique)


@dataclass(frozen=True)
class AnchorBinding:
    parent: tuple
    anchor_name: str
    anchor: tuple
    marker: tuple

    def __post_init__(self):
        if (self.anchor_name != _ANCHOR
                or any(type(item) is not tuple or not valid_identity(item)
                       for item in (self.parent, self.anchor, self.marker))):
            raise ValueError('unsafe anchor binding')

    def to_dict(self):
        return dict(schema='mission-private-anchor-binding/1', parent=list(self.parent),
                    anchor=dict(name=self.anchor_name, identity=list(self.anchor)),
                    marker=dict(name=_MARKER, identity=list(self.marker)))

    @classmethod
    def from_dict(cls, value):
        if (type(value) is not dict or set(value) != {'schema', 'parent', 'anchor', 'marker'}
                or value['schema'] != 'mission-private-anchor-binding/1'
                or type(value['parent']) is not list or not valid_identity(value['parent'])):
            raise ValueError('unsafe anchor binding')
        for key, name in (('anchor', _ANCHOR), ('marker', _MARKER)):
            item = value[key]
            if (type(item) is not dict or set(item) != {'name', 'identity'}
                    or item['name'] != name or type(item['identity']) is not list
                    or not valid_identity(item['identity'])):
                raise ValueError('unsafe anchor binding')
        return cls(tuple(value['parent']), _ANCHOR, tuple(value['anchor']['identity']),
                   tuple(value['marker']['identity']))


def _directory(stack, name, *, parent=None):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    stack.callback(os.close, fd)
    if not _private(os.fstat(fd), 'directory'):
        raise ValueError('unsafe private directory')
    return fd


def initialize_anchor(parent):
    """Explicit bootstrap only; caller must persist the returned binding."""
    try:
        with ExitStack() as stack:
            root = _directory(stack, parent)
            os.mkdir(_ANCHOR, 0o700, dir_fd=root)  # Existing/unknown anchors are not adopted.
            anchor = _directory(stack, _ANCHOR, parent=root)
            marker = os.open(_MARKER, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=anchor)
            stack.callback(os.close, marker)
            os.fchmod(marker, 0o600)
            binding = AnchorBinding(identity(os.fstat(root)), _ANCHOR,
                                    identity(os.fstat(anchor)), identity(os.fstat(marker)))
            _write(marker, _encode(dict(schema='mission-private-anchor/1',
                                       parent=list(binding.parent), anchor=list(binding.anchor),
                                       self=list(binding.marker)), ANCHOR_METADATA_LIMIT))
            os.fsync(anchor)
            os.fsync(root)
            return binding
    except (OSError, ValueError, TypeError) as error:
        raise ObservationBlocked('anchor initialization unavailable') from error


@dataclass
class CleanupAnchor:
    parent_fd: int
    anchor_fd: int
    marker_fd: int
    binding: AnchorBinding

    def remove_owned(self, name, expected, *, expires, now, kind='file', force=False, inner=None):
        record = _request(self, name, expected, kind, expires, inner)
        _options(now, force)
        if not force and now < expires:
            return False
        try:
            _check_handle(self)
            hint = _hint(record)
            pending = [entry for entry in os.listdir(self.anchor_fd)
                       if entry.startswith('quarantine-' + hint + '-')]
            if pending:
                results = [_resume(self, entry, now=now, force=force) for entry in pending]
                return all(results) and _stat(self.parent_fd, name) is None
            entries = os.listdir(self.anchor_fd)
            if (any(entry != _MARKER and not _QNAME.fullmatch(entry) for entry in entries)
                    or len(entries) - 1 >= QUARANTINE_LIMIT):
                return False
            source = _stat(self.parent_fd, name)
            if source is None:
                return True
            if not _matches(source, expected, kind):
                return False
            qname = 'quarantine-' + hint + '-' + secrets.token_hex(16)
            os.mkdir(qname, 0o700, dir_fd=self.anchor_fd)
            with ExitStack() as stack:
                qfd = _directory(stack, qname, parent=self.anchor_fd)
                journal = os.open('journal.json', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  0o600, dir_fd=qfd)
                stack.callback(os.close, journal)
                os.fchmod(journal, 0o600)
                record.update(quarantine=list(identity(os.fstat(qfd))),
                              self=list(identity(os.fstat(journal))))
                _write(journal, _encode(record, JOURNAL_LIMIT))
                os.fsync(qfd)
            os.fsync(self.anchor_fd)
            return _resume(self, qname, now=now, force=force)
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return False

    def recover(self, *, now, force=False):
        return self._recover(now=now, force=force, allow_inner=True)

    def _recover(self, *, now, force, allow_inner):
        _options(now, force)
        try:
            _check_handle(self)
            results = []
            for name in os.listdir(self.anchor_fd):
                if name != _MARKER:
                    results.append(bool(_QNAME.fullmatch(name))
                                   and _resume(self, name, now=now, force=force, allow_inner=allow_inner))
            return all(results)
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            return False


def _bound(stack, root, binding):
    if type(binding) is not AnchorBinding or identity(os.fstat(root)) != binding.parent:
        raise ValueError('caller binding or parent changed')
    anchor = _directory(stack, binding.anchor_name, parent=root)
    if not _matches(os.fstat(anchor), binding.anchor, 'directory'):
        raise ValueError('anchor identity changed')
    marker = os.open(_MARKER, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=anchor)
    stack.callback(os.close, marker)
    fcntl.flock(marker, fcntl.LOCK_EX | fcntl.LOCK_NB)
    handle = CleanupAnchor(root, anchor, marker, binding)
    _check_handle(handle)
    if not _matches(_stat(root, binding.anchor_name), binding.anchor, 'directory'):
        raise ValueError('anchor name changed')
    return handle


@contextmanager
def open_anchor(parent, binding):
    """Open/recover never creates or adopts an anchor from current observations."""
    try:
        with ExitStack() as stack:
            yield _bound(stack, _directory(stack, parent), binding)
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        raise ObservationBlocked('anchor unavailable') from error


def _options(now, force):
    if (type(now) not in (int, float) or not 0 <= now <= 2 ** 53 - 1
            or type(force) is not bool):
        raise ObservationBlocked('invalid cleanup clock or flag')


def _request(handle, name, expected, kind, expires, inner=None):
    _options(expires, False)
    if (type(name) is not str or not _NAME.fullmatch(name) or name == _ANCHOR
            or not valid_identity(expected) or kind not in ('file', 'directory')):
        raise ObservationBlocked('invalid cleanup request')
    record = dict(schema='mission-private-cleanup/2', parent=list(handle.binding.parent),
                  anchor=list(handle.binding.anchor), name=name, expected=list(expected),
                  kind=kind, expires=expires)
    if inner is not None:
        if type(inner) is not AnchorBinding or kind != 'directory' or inner.parent != tuple(expected):
            raise ObservationBlocked('inner anchor requires captured parent binding')
        record['inner'] = inner.to_dict()
    return record


def _hint(record):
    return hashlib.sha256(_encode([record['parent'], record['name'], record['expected']],
                                  JOURNAL_LIMIT)).hexdigest()[:16]


def _check_handle(handle):
    if (not _matches(os.fstat(handle.parent_fd), handle.binding.parent, 'directory')
            or not _matches(os.fstat(handle.anchor_fd), handle.binding.anchor, 'directory')):
        raise ValueError('anchor descriptor changed')
    record = _read(handle.marker_fd, handle.anchor_fd, _MARKER, handle.binding.marker,
                   ANCHOR_METADATA_LIMIT)
    expected = dict(schema='mission-private-anchor/1', parent=list(handle.binding.parent),
                    anchor=list(handle.binding.anchor), self=list(handle.binding.marker))
    if _encode(record, ANCHOR_METADATA_LIMIT) != _encode(expected, ANCHOR_METADATA_LIMIT):
        raise ValueError('anchor registration changed')


def _resume(handle, qname, *, now, force, allow_inner=True):
    if not _QNAME.fullmatch(qname):
        return False
    try:
        with ExitStack() as stack:
            qfd = _directory(stack, qname, parent=handle.anchor_fd)
            journal = os.open('journal.json', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=qfd)
            stack.callback(os.close, journal)
            fcntl.flock(journal, fcntl.LOCK_EX | fcntl.LOCK_NB)
            info = os.fstat(journal)
            record = _read(journal, qfd, 'journal.json', identity(info), JOURNAL_LIMIT)
            keys = {'schema', 'parent', 'anchor', 'name', 'expected', 'kind', 'expires', 'quarantine', 'self'}
            if type(record) is dict and 'inner' in record:
                if not allow_inner:
                    return False
                keys.add('inner')
            if type(record) is not dict or set(record) != keys:
                return False
            inner = AnchorBinding.from_dict(record['inner']) if 'inner' in record else None
            request = _request(handle, record['name'], record['expected'], record['kind'], record['expires'], inner)
            if (record['schema'] != request['schema']
                    or any(type(record[key]) is not list or not valid_identity(record[key])
                           for key in ('parent', 'anchor', 'expected', 'quarantine', 'self'))
                    or any(record[key] != request[key] for key in request)
                    or record['quarantine'] != list(identity(os.fstat(qfd)))
                    or record['self'] != list(identity(info))
                    or not qname.startswith('quarantine-' + _hint(record) + '-')):
                return False
            if not force and now < record['expires']:
                return False
            source, slot = _stat(handle.parent_fd, record['name']), _stat(qfd, 'slot')
            if source is not None and slot is not None:
                return False
            if slot is None and source is not None:
                if not _matches(source, record['expected'], record['kind']):
                    return False
                os.rename(record['name'], 'slot', src_dir_fd=handle.parent_fd, dst_dir_fd=qfd)
                os.fsync(handle.parent_fd)
                os.fsync(qfd)
                slot = _stat(qfd, 'slot')
            if _stat(handle.parent_fd, record['name']) is not None:
                return False
            if slot is not None:
                if not _matches(slot, record['expected'], record['kind']):
                    return False
                if record['kind'] == 'file':
                    os.unlink('slot', dir_fd=qfd)
                else:
                    if inner is not None:
                        captured = _directory(stack, 'slot', parent=qfd)
                        if identity(os.fstat(captured)) != tuple(record['expected']):
                            return False
                        if not _retire_inner(captured, inner, now=now, force=force):
                            return False
                    os.rmdir('slot', dir_fd=qfd)
                os.fsync(qfd)
            if os.listdir(qfd) != ['journal.json']:
                return False
            os.unlink('journal.json', dir_fd=qfd)
            os.fsync(qfd)
        # This name is inside the caller-bound dedicated anchor, never public parent.
        os.rmdir(qname, dir_fd=handle.anchor_fd)
        os.fsync(handle.anchor_fd)
        return True
    except (OSError, ValueError, TypeError, KeyError, RecursionError, ObservationBlocked):
        return False


def _retire_inner(captured, binding, *, now, force):
    """Only called after outer journal/slot identity validation, never public parent."""
    if identity(os.fstat(captured)) != binding.parent:
        return False
    named = _stat(captured, binding.anchor_name)
    if named is None:
        return True
    if not _matches(named, binding.anchor, 'directory'):
        return False
    with ExitStack() as stack:
        anchor = _directory(stack, binding.anchor_name, parent=captured)
        if identity(os.fstat(anchor)) != binding.anchor:
            return False
        if _stat(anchor, _MARKER) is not None:
            inner = _bound(stack, captured, binding)
            if not inner._recover(now=now, force=force, allow_inner=False) or os.listdir(anchor) != [_MARKER]:
                return False
            os.unlink(_MARKER, dir_fd=anchor)
            os.fsync(anchor)
        if os.listdir(anchor):
            return False
    os.rmdir(binding.anchor_name, dir_fd=captured)
    os.fsync(captured)
    return True
