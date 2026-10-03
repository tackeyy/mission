"""Installed runtime adapter trust boundary; no built-in reviewer adapters.

Registration pins the entry-point module, not its dependencies or the host's
honesty. Observation and execution belong to the trusted adapter. This module
does not confer completion authority or publish review results.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import importlib.machinery
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from types import FunctionType
from typing import Protocol

from mission_kernel.fresh_review import FreshReviewError, canonical_digest
from mission_kernel.errors import StrictReadError
from mission_kernel.model import ContentAddressedRef, FrozenJsonObject
from mission_application.runtime_guard import validate_registered_entry_point_distribution
from mission_persistence.strict_reader import read_stable_bytes, read_stable_bytes_beneath

REGISTRY_SCHEMA = 'mission-fresh-review-adapter-registry/1'
ENTRY_POINT_GROUP = 'mission.fresh_review_adapters'
REGISTRY_LIMIT = 64 * 1024
_NAME = re.compile(r'[a-z][a-z0-9-]{0,63}\Z')
_DISTRIBUTION = re.compile(r'[a-z0-9][a-z0-9._-]{0,127}\Z')
_DIGEST = re.compile(r'sha256:[0-9a-f]{64}\Z')
_VERSION = re.compile(r'[A-Za-z0-9][A-Za-z0-9.!+_-]{0,127}\Z')
_MODULE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z')


@dataclass(frozen=True)
class CollectedReview:
    """Keep host observation separate from the child-authored output bytes."""
    observation: FrozenJsonObject
    output_bytes: bytes | None


class FreshReviewRuntimeAdapter(Protocol):
    """Observe host facts; mission retains state and result authority.

    Handles refer exclusively to captured immutable inputs. The launch envelope
    carries dispatch identity separately from the canonical prepared request.
    Recover must observe the exact original child and must never redispatch it.
    """
    def observe_parent(self) -> FrozenJsonObject: ...

    def launch(self, request_bytes: bytes, input_handle: ContentAddressedRef,
               snapshot_handles: tuple[ContentAddressedRef, ...], dispatch: FrozenJsonObject) -> FrozenJsonObject: ...

    def collect(self, launch: FrozenJsonObject) -> CollectedReview: ...

    def cancel(self, dispatch: FrozenJsonObject) -> FrozenJsonObject: ...

    def recover(self, dispatch: FrozenJsonObject) -> CollectedReview: ...


@dataclass(frozen=True)
class AdapterRegistration:
    id: str
    entry_point: str
    distribution: str
    version: str
    source_digest: str

    @property
    def digest(self) -> str:
        return canonical_digest(asdict(self))


def validate_registry(value):
    """Decode the closed trust-root document without loading executable code."""
    invalid = 'fresh-review-adapter-registry-invalid'
    if (not isinstance(value, dict) or set(value) != {'schema', 'adapters'}
            or value['schema'] != REGISTRY_SCHEMA or not isinstance(value['adapters'], list)
            or len(value['adapters']) > 64):
        raise FreshReviewError(invalid)
    registrations = {}
    entry_points = set()
    for item in value['adapters']:
        if (not isinstance(item, dict) or set(item) != set(AdapterRegistration.__dataclass_fields__)
                or any(not isinstance(item[key], str) for key in item)
                or not _NAME.fullmatch(item['id']) or not _NAME.fullmatch(item['entry_point'])
                or not _DISTRIBUTION.fullmatch(item['distribution'])
                or not _VERSION.fullmatch(item['version'])
                or not _DIGEST.fullmatch(item['source_digest'])
                or item['id'] in registrations or item['entry_point'] in entry_points):
            raise FreshReviewError(invalid)
        registrations[item['id']] = AdapterRegistration(**item)
        entry_points.add(item['entry_point'])
    return registrations


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FreshReviewError('fresh-review-adapter-registry-invalid')
        result[key] = value
    return result


def read_registry():
    """Read only the user trust root, pinning file and directory identities."""
    try:
        configured = os.environ.get('XDG_CONFIG_HOME')
        root = Path(configured) if configured else Path.home() / '.config'
        if not root.is_absolute():
            raise FreshReviewError('fresh-review-adapter-config-relative')
        raw = read_stable_bytes_beneath(root, 'mission/fresh-review-adapters.json', limit=REGISTRY_LIMIT).payload
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_pairs)
        return validate_registry(value)
    except FreshReviewError:
        raise
    except (OSError, StrictReadError, UnicodeError, ValueError) as exc:
        raise FreshReviewError('fresh-review-adapter-registry-invalid') from exc


@dataclass(frozen=True)
class AdapterPin:
    registration: AdapterRegistration
    entry_point_value: str
    module: str


def _source_spec(module):
    # util.find_spec on a dotted name imports its parent package. Walk using
    # PathFinder instead so even package initializers remain outside the parent.
    search = None
    name = ''
    spec = None
    for component in module.split('.'):
        name = name + '.' + component if name else component
        spec = importlib.machinery.PathFinder.find_spec(name, search)
        if spec is None:
            raise FreshReviewError('fresh-review-adapter-source-invalid')
        search = spec.submodule_search_locations
        if name != module and search is None:
            raise FreshReviewError('fresh-review-adapter-source-invalid')
    if spec is None or not isinstance(spec.origin, str) or spec.origin in {'built-in', 'frozen'}:
        raise FreshReviewError('fresh-review-adapter-source-invalid')
    return spec


def _source_digest(module):
    try:
        source = read_stable_bytes(_source_spec(module).origin)
    except (OSError, StrictReadError) as exc:
        raise FreshReviewError('fresh-review-adapter-source-invalid') from exc
    return 'sha256:' + hashlib.sha256(source).hexdigest()


def _load_pinned_factory(pin, entry):
    """Bind entry-point bytes to a plain factory function defined by those bytes.

    Imports, other modules and objects reachable from __main__ that the function
    calls are dependency code outside the pin. Other factory forms are rejected
    with fresh-review-adapter-source-invalid.
    """
    invalid = 'fresh-review-adapter-source-invalid'
    if pin.module in sys.modules:
        raise FreshReviewError(invalid)
    origin = _source_spec(pin.module).origin
    source = read_stable_bytes(origin)
    if 'sha256:' + hashlib.sha256(source).hexdigest() != pin.registration.source_digest:
        raise FreshReviewError(invalid)
    code = compile(source, origin, 'exec', dont_inherit=True)
    # Always use a source spec: module_from_spec must not invoke an extension
    # loader, and cached bytecode must not replace the bytes checked above.
    spec = importlib.util.spec_from_file_location(
        pin.module, origin, loader=importlib.machinery.SourceFileLoader(pin.module, origin))
    module = importlib.util.module_from_spec(spec)
    if _source_digest(pin.module) != pin.registration.source_digest:
        raise FreshReviewError(invalid)
    sys.modules[pin.module] = module
    try:
        exec(code, module.__dict__)
        factory = getattr(module, entry.attr, None)
        if (sys.modules.get(pin.module) is not module or module.__file__ != origin
                or module.__spec__ is not spec or spec.origin != origin):
            raise FreshReviewError(invalid)
        if type(factory) is not FunctionType:
            raise FreshReviewError(invalid)
        if (factory.__globals__ is not module.__dict__
                or factory.__code__.co_filename != origin
                or factory.__module__ != pin.module):
            raise FreshReviewError(invalid)
        return factory
    except BaseException:
        sys.modules.pop(pin.module, None)
        raise


def _resolve_adapter(identifier):
    if not isinstance(identifier, str) or not _NAME.fullmatch(identifier):
        raise FreshReviewError('fresh-review-adapter-id-invalid')
    registration = read_registry().get(identifier)
    if registration is None:
        raise FreshReviewError('fresh-review-adapter-not-registered')
    try:
        discovered = importlib.metadata.entry_points()
        entries = (discovered.select(group=ENTRY_POINT_GROUP) if hasattr(discovered, 'select')
                   else discovered.get(ENTRY_POINT_GROUP, ()))
        matches = [entry for entry in entries if entry.name == registration.entry_point]
        if len(matches) != 1:
            raise FreshReviewError('fresh-review-adapter-entry-point-invalid')
        entry = matches[0]
        attached = entry.dist
        distribution = attached if attached is not None else importlib.metadata.distribution(registration.distribution)
        owned = tuple((item.group, item.name, item.value) for item in distribution.entry_points)
        validate_registered_entry_point_distribution(
            entry_point_name=entry.name, entry_point_value=entry.value, has_attached_distribution=attached is not None,
            distribution_name=distribution.metadata['Name'], distribution_version=distribution.version,
            owned_entry_points=owned, configured_distribution=registration.distribution,
            configured_version=registration.version, group=ENTRY_POINT_GROUP,
        )
    except FreshReviewError:
        raise
    except (AttributeError, KeyError, OSError, TypeError, ValueError, importlib.metadata.PackageNotFoundError) as exc:
        raise FreshReviewError('fresh-review-adapter-distribution-invalid') from exc
    try:
        module = entry.module
        if entry.extras:
            raise FreshReviewError('fresh-review-adapter-entry-point-invalid')
    except (AttributeError, AssertionError, TypeError, ValueError) as exc:
        raise FreshReviewError('fresh-review-adapter-entry-point-invalid') from exc
    if not isinstance(module, str) or not _MODULE.fullmatch(module):
        raise FreshReviewError('fresh-review-adapter-entry-point-invalid')
    if _source_digest(module) != registration.source_digest:
        raise FreshReviewError('fresh-review-adapter-source-invalid')
    return AdapterPin(registration, entry.value, module), entry


def resolve_adapter(identifier):
    """Parent lookup: do not import or instantiate registered adapter code."""
    return _resolve_adapter(identifier)[0]


def load_adapter(pin: AdapterPin) -> FreshReviewRuntimeAdapter:
    """Callback-child boundary: rediscover and verify before executing code.

    The caller must run this in its bounded callback child, outside repository
    locks. Loading an adapter alone supplies no independent-review evidence.
    """
    if not isinstance(pin, AdapterPin) or not isinstance(pin.registration, AdapterRegistration):
        raise FreshReviewError('fresh-review-adapter-pin-changed')
    current, entry = _resolve_adapter(pin.registration.id)
    if current != pin:
        raise FreshReviewError('fresh-review-adapter-pin-changed')
    try:
        factory = _load_pinned_factory(pin, entry)
        adapter = factory()
        if any(not callable(getattr(adapter, method, None))
               for method in ('observe_parent', 'launch', 'collect', 'cancel', 'recover')):
            raise FreshReviewError('fresh-review-adapter-protocol-invalid')
        # Loading code may itself replace source/metadata/registry. No result
        # from such a callback is usable under the originally selected pin.
        if resolve_adapter(pin.registration.id) != pin:
            raise FreshReviewError('fresh-review-adapter-pin-changed')
        return adapter
    except FreshReviewError:
        raise
    except Exception as exc:
        raise FreshReviewError('fresh-review-adapter-load-failed') from exc
