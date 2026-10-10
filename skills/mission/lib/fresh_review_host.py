"""Bounded callback-child transport; registered adapter code never loads in CLI."""
from __future__ import annotations
from dataclasses import asdict
import base64
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import os
import time
from budgeted_exec import spawn_deadline_exec, observe_exit, cleanup_group, strict_json

from fresh_review_runtime import AdapterPin, AdapterRegistration, validate_registry, REGISTRY_SCHEMA
from mission_kernel.fresh_review import FreshReviewError, canonical_bytes, canonical_digest, request_document
from mission_kernel.json_codec import freeze_json_value
from mission_kernel.model import ContentAddressedRef
from mission_persistence.strict_reader import read_stable_bytes_beneath


def _call(pin, action, payload, *, cwd=None, timeout=10):
    """The only adapter exec seam; no callback or pre-exec Python in the parent.

    File-backed transport cannot deadlock on a descendant's inherited pipe.
    Cleanup precedes reap on every exit, including callback failure and timeout.
    F can replace admission around this seam without changing adapter calls.
    """
    envelope = {'pin': asdict(pin) if pin is not None else None, 'action': action, **payload}
    child, control_receiver, timed_out, confirmed = None, None, False, True
    try:
        with tempfile.TemporaryFile() as incoming, tempfile.TemporaryFile() as outgoing:
            incoming.write(canonical_bytes(envelope))
            incoming.seek(0)
            deadline = time.monotonic() + timeout
            try:
                child, control_receiver = spawn_deadline_exec([sys.executable, str(Path(__file__).resolve())], deadline,
                    stdin=incoming, stdout=outgoing, stderr=subprocess.DEVNULL, cwd=cwd)
                while not observe_exit(child.pid):
                    if time.monotonic() >= deadline or os.fstat(outgoing.fileno()).st_size > 512 * 1024:
                        timed_out = True
                        break
                    time.sleep(min(.01, max(0, deadline - time.monotonic())))
            finally:
                if child is not None:
                    confirmed = cleanup_group(child, timed_out=timed_out)
                if control_receiver is not None:
                    os.close(control_receiver)
                    control_receiver = None
            if not confirmed or timed_out or child.returncode != 0:
                return {'unknown': True}
            outgoing.seek(0)
            raw = outgoing.read(512 * 1024 + 1)
            if len(raw) > 512 * 1024:
                return {'unknown': True}
            value = strict_json(raw)
            return value if isinstance(value, dict) else {'unknown': True}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'unknown': True}



def resolve(identifier):
    value = _call(None, 'resolve', {'identifier': identifier}).get('pin')
    invalid = 'fresh-review-adapter-pin-changed'
    if not isinstance(value, dict) or set(value) != {'registration', 'entry_point_value', 'module'}:
        raise FreshReviewError(invalid)
    registrations = validate_registry({'schema': REGISTRY_SCHEMA, 'adapters': [value['registration']]})
    if identifier not in registrations or any(not isinstance(value[key], str) or not 0 < len(value[key]) <= 1024
                                              for key in ('entry_point_value', 'module')):
        raise FreshReviewError(invalid)
    if (any(not part.isidentifier() for part in value['module'].split('.'))
            or not value['entry_point_value'].startswith(value['module'] + ':')):
        raise FreshReviewError(invalid)
    return AdapterPin(registrations[identifier], value['entry_point_value'], value['module'])


def observe(pin):
    result = _call(pin, 'observe', {})
    parent = result.get('parent')
    if not isinstance(parent, dict) or set(parent) != {'parent_identity'}:
        raise FreshReviewError('fresh-review-identity-unobservable')
    from mission_kernel.fresh_review import _identifier
    try:
        _identifier(parent['parent_identity'])
    except FreshReviewError as exc:
        raise FreshReviewError('fresh-review-identity-unobservable') from exc
    return parent


@contextmanager
def materialize(root, request):
    # Stable reads and digest checks precede spawn. Handles point only into this
    # immutable copy, so mutable workspace files never become reviewer input.
    ref = request.input_ref
    raw = read_stable_bytes_beneath(root, ref.relative_path, limit=request.max_packet_bytes).payload
    if len(raw) != ref.size or 'sha256:' + hashlib.sha256(raw).hexdigest() != request.input_digest:
        raise FreshReviewError('fresh-review-input-unobservable')
    packet = json.loads(raw)
    with tempfile.TemporaryDirectory(prefix='mission-fresh-') as directory:
        folder = Path(directory)
        contents = [(ref, raw)]
        for command, snapshot in packet['snapshots'].items():
            content = canonical_bytes(snapshot)
            digest = canonical_digest(snapshot)
            contents.append((ContentAddressedRef('fresh-review-snapshot',
                'snapshots/' + digest[7:] + '.json', digest, len(content)), content))
        for handle, content in {handle.relative_path: (handle, content) for handle, content in contents}.values():
            target = folder / handle.relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(0o400)
        yield folder, tuple(handle for handle, _ in contents[1:])


def launch(pin, root, request, dispatch):
    with materialize(root, request) as (folder, snapshots):
        return _call(pin, 'launch', {'request': request_document(request), 'input': asdict(request.input_ref),
            'snapshots': [asdict(item) for item in snapshots], 'dispatch': dispatch},
            cwd=folder, timeout=request.wall_time_sec)


def recover(pin, dispatch):
    return _call(pin, 'recover', {'dispatch': dispatch})


def cancel(pin, dispatch):
    result = _call(pin, 'cancel', {'dispatch': dispatch})
    observation = result.get('cancel')
    status = observation.get('status') if isinstance(observation, dict) else None
    return status if status in ('cancelled', 'failed', 'unknown') else 'unknown'


def _callback(value):
    from fresh_review_runtime import load_adapter, resolve_adapter
    if value['action'] == 'resolve':
        return {'pin': asdict(resolve_adapter(value['identifier']))}
    pin = AdapterPin(AdapterRegistration(**value['pin']['registration']),
                     value['pin']['entry_point_value'], value['pin']['module'])
    adapter = load_adapter(pin)
    action = value['action']
    if action == 'observe':
        return {'parent': adapter.observe_parent().thaw()}
    if action == 'launch':
        # Observe parent again in the launch callback, not from caller flags.
        if adapter.observe_parent().thaw() != {'parent_identity': value['dispatch']['parent_identity']}:
            return {'blocked': 'identity-unobservable', 'attempted': False}
        try:
            receipt = adapter.launch(canonical_bytes(value['request']), ContentAddressedRef(**value['input']),
                tuple(ContentAddressedRef(**item) for item in value['snapshots']), freeze_json_value(value['dispatch']))
        except FreshReviewError as exc:
            if exc.code == 'fresh-review-launch-unavailable':
                return {'blocked': 'launch-unavailable', 'attempted': False}
            return {'blocked': 'launch-invalid', 'attempted': True}
        try:
            if resolve_adapter(pin.registration.id) != pin:
                raise FreshReviewError('fresh-review-adapter-pin-changed')
        except FreshReviewError:
            return {'blocked': 'registration-mismatch', 'attempted': True}
        return {'launch_receipt': receipt.thaw() if hasattr(receipt, 'thaw') else None}
    if action == 'recover':
        collected = adapter.recover(freeze_json_value(value['dispatch']))
        result = {'observation': collected.observation.thaw(),
                  'output': None if collected.output_bytes is None else base64.b64encode(collected.output_bytes).decode('ascii')}
        if resolve_adapter(pin.registration.id) != pin:
            return {'unknown': True}
        return result
    if action == 'cancel':
        return {'cancel': adapter.cancel(freeze_json_value(value['dispatch'])).thaw()}
    raise FreshReviewError('fresh-review-host-action-invalid')


if __name__ == '__main__':
    try:
        reply = _callback(json.loads(sys.stdin.buffer.read(2 * 1024 * 1024)))
    except FreshReviewError as exc:
        reply = ({'unknown': True} if exc.code == 'fresh-review-host-action-invalid'
                 else {'blocked': 'registration-mismatch', 'attempted': False})
    sys.stdout.buffer.write(canonical_bytes(reply))
