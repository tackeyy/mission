"""Inactive managed-channel validation; no provider registration or spawn seam.

Operation capabilities authenticate IPC only. Parent identity must come from
the query's native observation bridge, never this wire envelope.
"""
from __future__ import annotations

import json
import struct
import hmac
import re
import math
import time
import os
import socket
import stat
import fcntl
from contextlib import contextmanager, ExitStack

from exec_cleanup import cleanup_scope

from fresh_review_observation import ObservationBlocked

METHODS = frozenset(('observe_parent', 'launch', 'collect', 'cancel', 'recover'))
FIELDS = frozenset(('schema', 'boot_id', 'operation_id', 'request_digest', 'method', 'nonce', 'capability'))
FRAME_BYTES = 2 * 1024 * 1024
OPERATION_BYTES = 16 * 1024 * 1024  # Includes each four-byte length prefix.
OPERATION_FRAMES = 256


def _identifier(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value) is not None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ObservationBlocked('ipc-duplicate-key')
        result[key] = value
    return result


class OperationChannel:
    def __init__(self, boot_id, operation_id, request_digest, capability, *, deadline):
        if (not _identifier(boot_id) or not _identifier(operation_id)
                or type(request_digest) is not str
                or re.fullmatch(r'sha256:[0-9a-f]{64}', request_digest) is None
                or type(capability) is not bytes or len(capability) != 32):
            raise ObservationBlocked('ipc-binding-invalid')
        now = time.monotonic()
        if (type(deadline) not in (int, float) or not now < deadline <= now + 300
                or not math.isfinite(deadline)):
            raise ObservationBlocked('ipc-deadline-invalid')
        self._deadline = deadline
        self._binding = (boot_id, operation_id, request_digest)
        self._capability = capability
        self._buffer = bytearray()
        self._nonces = set()
        self._failed = False
        self._bytes = 0

    def _decode(self, raw):
        try:
            value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs)
        except (ValueError, UnicodeError, RecursionError) as error:
            raise ObservationBlocked('ipc-json-invalid') from error
        if (type(value) is not dict or set(value) != FIELDS
                or any(type(item) is not str for item in value.values())
                or value['schema'] != 'mission-host-ipc/1'
                or (value['boot_id'], value['operation_id'], value['request_digest']) != self._binding
                or value['method'] not in METHODS or not _identifier(value['nonce'])
                or re.fullmatch(r'[0-9a-f]{64}', value['capability']) is None
                or not hmac.compare_digest(bytes.fromhex(value['capability']), self._capability)):
            raise ObservationBlocked('ipc-binding-mismatch')
        if value['nonce'] in self._nonces:
            raise ObservationBlocked('ipc-replay')
        self._nonces.add(value['nonce'])
        return value['method']

    def feed(self, chunk):
        try:
            if self._failed or time.monotonic() >= self._deadline:
                raise ObservationBlocked('ipc-closed')
            if type(chunk) is not bytes or len(chunk) > OPERATION_BYTES - self._bytes:
                raise ObservationBlocked('ipc-byte-limit')
            self._bytes += len(chunk)
            self._buffer.extend(chunk)
            result = []
            while len(self._buffer) >= 4:
                size = struct.unpack('!I', self._buffer[:4])[0]
                if not 1 <= size <= FRAME_BYTES or len(self._nonces) >= OPERATION_FRAMES:
                    raise ObservationBlocked('ipc-frame-limit')
                if len(self._buffer) < size + 4:
                    break
                result.append(self._decode(bytes(self._buffer[4:size + 4])))
                del self._buffer[:size + 4]
            methods = tuple(result)
            if time.monotonic() >= self._deadline:
                raise ObservationBlocked('ipc-closed')
            return methods
        except ObservationBlocked:
            self._failed = True
            self._buffer.clear()
            raise

    def finish(self):
        if self._failed or self._buffer or time.monotonic() >= self._deadline:
            self._failed = True
            raise ObservationBlocked('ipc-incomplete')
        self._failed = True  # EOF is terminal, including a complete valid stream.


@contextmanager
def managed_callback_fds(channels, owner_write_fd):
    """Duplicate only connected local sockets for existing exec's pass_fds.

    This does not spawn or change the ordinary callback transport. The future
    managed owner supplies this tuple to spawn_deadline_exec, whose close_fds
    boundary closes everything else. The owner's write pipe stays with owner.
    """
    if (type(channels) is not tuple or not 1 <= len(channels) <= 4
            or type(owner_write_fd) is not int or owner_write_fd < 3
            or any(type(fd) is not int or fd < 3 for fd in channels)
            or len(set(channels)) != len(channels) or owner_write_fd in channels):
        raise ObservationBlocked('ipc-descriptor-invalid')
    with ExitStack() as cleanup:
        try:
            owner = os.fstat(owner_write_fd)
            if (not stat.S_ISFIFO(owner.st_mode)
                    or fcntl.fcntl(owner_write_fd, fcntl.F_GETFL) & os.O_ACCMODE != os.O_WRONLY):
                raise ObservationBlocked('ipc-owner-pipe-invalid')
            result = []
            identities = set()
            for fd in channels:
                if not hasattr(fcntl, 'F_DUPFD_CLOEXEC'):
                    raise ObservationBlocked('ipc-descriptor-unobservable')
                duplicate = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 3)
                cleanup.enter_context(cleanup_scope(lambda fd=duplicate: os.close(fd)))
                metadata = os.fstat(duplicate)
                identity = (metadata.st_dev, metadata.st_ino)
                if not stat.S_ISSOCK(metadata.st_mode) or identity in identities:
                    raise ObservationBlocked('ipc-channel-invalid')
                identities.add(identity)
                # Detach after inspection so ExitStack retains sole FD custody.
                endpoint = socket.socket(fileno=duplicate)
                try:
                    if endpoint.family != socket.AF_UNIX or endpoint.type != socket.SOCK_STREAM:
                        raise ObservationBlocked('ipc-channel-invalid')
                    endpoint.getpeername()
                finally:
                    endpoint.detach()
                os.set_inheritable(duplicate, False)
                result.append(duplicate)
        except OSError as error:
            raise ObservationBlocked('ipc-descriptor-unobservable') from error
        yield tuple(result)
