"""Inactive model-free primitives for a trusted, explicitly registered host.

These validators do not discover native sources or certify host observations.
The future adapter must supply exhaustive enumeration from its trusted host
boundary. No caller/model declaration can establish that boundary here.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import PurePosixPath
import re

from mission_kernel.errors import StrictReadError
from mission_persistence.strict_reader import read_stable_bytes_beneath


class ObservationBlocked(ValueError):
    """Required observation is absent or inconsistent; no launch is authorized."""


def _digest(value):
    return type(value) is str and re.fullmatch(r'sha256:[0-9a-f]{64}', value) is not None


def _path(value):
    return (type(value) is str and 0 < len(value) <= 1024 and '\x00' not in value
            and not PurePosixPath(value).is_absolute()
            and PurePosixPath(value).as_posix() == value
            and all(part not in ('.', '..') for part in PurePosixPath(value).parts)
            and bool(PurePosixPath(value).parts))


@dataclass(frozen=True)
class SourcePolicy:
    path: str
    digest: str
    kind: str = 'static-policy'
    inputs: tuple[str, ...] = ()
    output_digest: str | None = None


@dataclass(frozen=True)
class ContextManifest:
    sources: tuple[SourcePolicy, ...]

    def _validate(self):
        if type(self.sources) is not tuple or not 1 <= len(self.sources) <= 64:
            raise ObservationBlocked('context-manifest-invalid')
        paths = set()
        for source in self.sources:
            if (type(source) is not SourcePolicy or not _path(source.path) or not _digest(source.digest)
                    or type(source.kind) is not str or source.kind not in ('static-policy', 'hook')
                    or type(source.inputs) is not tuple or len(source.inputs) > 64
                    or any(not _path(item) for item in source.inputs)
                    or len(set(source.inputs)) != len(source.inputs)
                    or source.path in paths
                    or (source.kind == 'static-policy' and (source.inputs or source.output_digest is not None))
                    or (source.kind == 'hook' and not _digest(source.output_digest))):
                raise ObservationBlocked('context-manifest-invalid')
            paths.add(source.path)
        remaining = {source.path: set(source.inputs) for source in self.sources}
        if any(not inputs <= paths for inputs in remaining.values()):
            raise ObservationBlocked('context-input-unlisted')
        resolved = set()
        while remaining:
            ready = {path for path, inputs in remaining.items() if inputs <= resolved}
            if not ready:
                raise ObservationBlocked('context-input-cycle')
            resolved.update(ready)
            remaining = {path: inputs for path, inputs in remaining.items() if path not in ready}

    def verify(self, root, *, enumerate_sources, hook_outputs):
        """Use only trusted host enumeration, never a request-supplied source list.

        Hook implementations and all their transitive inputs must have been
        reviewed as static before constructing this manifest. Hashes detect
        drift; they cannot prove arbitrary code has no undeclared inputs.
        """
        self._validate()
        expected = {source.path for source in self.sources}
        def inventory():
            try:
                values = enumerate_sources()
                if (type(values) is not tuple or len(values) > 64
                        or any(not _path(item) for item in values)
                        or len(set(values)) != len(values) or set(values) != expected):
                    raise ObservationBlocked('context-source-mismatch')
            except Exception as error:
                raise ObservationBlocked('context-source-unobservable') from error
        inventory()
        for source in self.sources:
            try:
                raw = read_stable_bytes_beneath(root, source.path, limit=1024 * 1024).payload
            except StrictReadError as error:
                raise ObservationBlocked('context-source-unobservable') from error
            if 'sha256:' + hashlib.sha256(raw).hexdigest() != source.digest:
                raise ObservationBlocked('context-source-changed')
        hooks = {source.path: source for source in self.sources if source.kind == 'hook'}
        if type(hook_outputs) is not dict or set(hook_outputs) != set(hooks):
            raise ObservationBlocked('context-hook-unobservable')
        for path, source in hooks.items():
            output = hook_outputs[path]
            if (type(output) is not bytes or len(output) > 1024 * 1024
                    or 'sha256:' + hashlib.sha256(output).hexdigest() != source.output_digest):
                raise ObservationBlocked('context-hook-changed')
        inventory()


class QueryIdentity:
    """Query-closure state; only the trusted native event bridge may feed it.

    Owner is an in-process object, never a serialized query ID or credential.
    Fields are the bridge's closed projection of native events. The bridge
    and native SDK mapping are not implemented or attested by these primitives.
    """

    def __init__(self, owner):
        if type(owner) is not object:
            raise ObservationBlocked('identity-owner-invalid')
        self._owner = owner
        self._start = self._init = None
        self._failed = False

    def _event(self, owner, event, expected, previous):
        if (self._failed or owner is not self._owner or previous is not None
                or type(event) is not dict or set(event) != expected
                or type(event.get('session_id')) is not str
                or re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', event['session_id']) is None):
            self._failed = True
            raise ObservationBlocked('identity-event-invalid')
        return event['session_id']

    def session_start(self, owner, event):
        value = self._event(owner, event, {'source', 'session_id'}, self._start)
        if event['source'] != 'startup':
            self._failed = True
            raise ObservationBlocked('identity-startup-required')
        self._start = value
        self._match()

    def init(self, owner, event):
        self._init = self._event(owner, event, {'session_id'}, self._init)
        self._match()

    def _match(self):
        if self._start is not None and self._init is not None and self._start != self._init:
            self._failed = True
            raise ObservationBlocked('identity-event-mismatch')

    def observe(self):
        if self._failed or self._start is None or self._init is None:
            raise ObservationBlocked('identity-unobservable')
        return self._start
