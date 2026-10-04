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

from fresh_review_runtime import resolve_adapter as resolve, load_adapter, AdapterPin, AdapterRegistration
from mission_kernel.fresh_review import FreshReviewError, canonical_bytes, canonical_digest, request_document
from mission_kernel.json_codec import freeze_json_value
from mission_kernel.model import ContentAddressedRef
from mission_persistence.strict_reader import read_stable_bytes_beneath


def _call(pin, action, payload, *, cwd=None, timeout=10):
    envelope = {'pin': asdict(pin), 'action': action, **payload}
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve())],
            input=canonical_bytes(envelope), capture_output=True, cwd=cwd, timeout=timeout)
        if result.returncode or len(result.stdout) > 512 * 1024:
            return {'unknown': True}
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            return {'unknown': True}
        return value
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {'unknown': True}


def observe(pin):
    result = _call(pin, 'observe', {})
    parent = result.get('parent')
    if not isinstance(parent, dict) or set(parent) != {'parent_identity'}:
        raise FreshReviewError('fresh-review-identity-unobservable')
    from mission_kernel.fresh_review import _identifier
    _identifier(parent['parent_identity'])
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
        for handle, content in contents:
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
    status = result.get('cancel', {}).get('status')
    return status if status in ('cancelled', 'failed', 'unknown') else 'unknown'


def _callback(value):
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
            if resolve(pin.registration.id) != pin:
                raise FreshReviewError('fresh-review-adapter-pin-changed')
        except FreshReviewError:
            return {'blocked': 'registration-mismatch', 'attempted': True}
        return {'launch_receipt': receipt.thaw() if hasattr(receipt, 'thaw') else None}
    if action == 'recover':
        collected = adapter.recover(freeze_json_value(value['dispatch']))
        result = {'observation': collected.observation.thaw(),
                  'output': None if collected.output_bytes is None else base64.b64encode(collected.output_bytes).decode('ascii')}
        if resolve(pin.registration.id) != pin:
            return {'unknown': True}
        return result
    if action == 'cancel':
        return {'cancel': adapter.cancel(freeze_json_value(value['dispatch'])).thaw()}
    raise FreshReviewError('fresh-review-host-action-invalid')


if __name__ == '__main__':
    try:
        reply = _callback(json.loads(sys.stdin.buffer.read(2 * 1024 * 1024)))
    except FreshReviewError:
        reply = {'blocked': 'registration-mismatch', 'attempted': False}
    sys.stdout.buffer.write(canonical_bytes(reply))
