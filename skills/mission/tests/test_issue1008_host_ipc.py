"""Inactive IPC guards exercised with synthetic messages and local descriptors."""
import json
import struct
import time
import os
import socket
import sys
import tempfile

import pytest

from fresh_review_observation import ObservationBlocked
from fresh_review_ipc import OperationChannel, managed_callback_fds


CAPABILITY = b'x' * 32


def message(**updates):
    value = dict(schema='mission-host-ipc/1', boot_id='boot', operation_id='operation',
                request_digest='sha256:' + 'a' * 64, method='observe_parent', nonce='nonce',
                capability=CAPABILITY.hex())
    return dict(value, **updates)


def frame(value):
    raw = json.dumps(value).encode()
    return struct.pack('!I', len(raw)) + raw


def channel():
    return OperationChannel('boot', 'operation', 'sha256:' + 'a' * 64, CAPABILITY,
                            deadline=time.monotonic() + 60)


def test_fragmented_frame_requires_completion_and_replay_never_redispatches():
    operation = channel()
    wire = frame(message())
    assert operation.feed(wire[:3]) == ()
    assert operation.feed(wire[3:-1]) == ()
    assert operation.feed(wire[-1:]) == ('observe_parent',)
    operation.finish()
    with pytest.raises(ObservationBlocked):
        operation.feed(wire)


@pytest.mark.parametrize('updates', [
    {'schema': 'other'}, {'boot_id': 'old'}, {'operation_id': 'other'},
    {'request_digest': 'sha256:' + 'b' * 64}, {'capability': '00' * 32},
    {'capability': None}, {'method': 'exec'}, {'nonce': ''}, {'nonce': []}, {'fresh': True},
])
def test_wire_cannot_supply_foreign_authority_or_extra_commands(updates):
    operation = channel()
    with pytest.raises(ObservationBlocked):
        operation.feed(frame(message(**updates)))
    with pytest.raises(ObservationBlocked):
        operation.feed(frame(message(nonce='new')))


def test_duplicate_json_keys_cannot_replace_a_binding():
    raw = json.dumps(message()).replace('"boot_id": "boot"', '"boot_id":"old","boot_id":"boot"').encode()
    with pytest.raises(ObservationBlocked):
        channel().feed(struct.pack('!I', len(raw)) + raw)


@pytest.mark.parametrize('wire', [
    struct.pack('!I', 2 * 1024 * 1024 + 1),
    struct.pack('!I', 0),
    struct.pack('!I', 1) + b'\xff',
    struct.pack('!I', 1) + b'[',
])
def test_declared_oversize_and_invalid_frames_fail_before_dispatch(wire):
    with pytest.raises(ObservationBlocked):
        channel().feed(wire)


def test_partial_frame_on_eof_is_not_accepted_or_repaired():
    operation = channel()
    operation.feed(frame(message())[:-1])
    with pytest.raises(ObservationBlocked):
        operation.finish()
    with pytest.raises(ObservationBlocked):
        operation.feed(b'}')


def test_frame_and_cumulative_limits_do_not_truncate_or_accept_later_commands():
    operation = channel()
    for number in range(256):
        assert operation.feed(frame(message(nonce=str(number)))) == ('observe_parent',)
    with pytest.raises(ObservationBlocked):
        operation.feed(frame(message(nonce='257')))
    operation = channel()
    # JSON whitespace is valid, and counts toward envelope bytes just as data.
    raw = json.dumps(message()).encode()
    for number in range(8):
        raw = json.dumps(message(nonce=str(number))).encode()
        padded = raw + b' ' * (2 * 1024 * 1024 - len(raw))
        wire = struct.pack('!I', len(padded)) + padded
        if number < 7:
            assert operation.feed(wire) == ('observe_parent',)
        else:
            with pytest.raises(ObservationBlocked):
                operation.feed(wire)


def test_expired_capability_cannot_complete_a_previously_partial_frame(monkeypatch):
    now = [10.0]
    monkeypatch.setattr('fresh_review_ipc.time.monotonic', lambda: now[0])
    operation = OperationChannel('boot', 'operation', 'sha256:' + 'a' * 64, CAPABILITY, deadline=11.0)
    wire = frame(message())
    operation.feed(wire[:-1])
    now[0] = 11.0
    with pytest.raises(ObservationBlocked):
        operation.feed(wire[-1:])


def test_callback_descriptors_are_owned_duplicates_and_never_include_owner_pipe():
    reader, owner = os.pipe()
    local, peer = socket.socketpair()
    try:
        with managed_callback_fds((local.fileno(),), owner) as descriptors:
            duplicate, = descriptors
            assert duplicate != local.fileno()
            assert os.fstat(duplicate).st_ino == os.fstat(local.fileno()).st_ino
            assert not os.get_inheritable(duplicate)
        with pytest.raises(OSError):
            os.fstat(duplicate)
        os.fstat(local.fileno())
        with pytest.raises(ObservationBlocked):
            with managed_callback_fds((owner,), owner):
                pytest.fail('owner pipe admitted')
    finally:
        os.close(reader)
        os.close(owner)
        local.close()
        peer.close()


def test_existing_deadline_exec_preserves_only_managed_channel(tmp_path):
    from budgeted_exec import spawn_deadline_exec, observe_exit, cleanup_group
    reader, owner = os.pipe()
    local, peer = socket.socketpair()
    child = control = None
    with tempfile.TemporaryFile() as unrelated, tempfile.TemporaryFile() as output:
        try:
            os.set_inheritable(owner, True)
            os.set_inheritable(unrelated.fileno(), True)
            forbidden = [(fd, os.fstat(fd).st_dev, os.fstat(fd).st_ino)
                         for fd in (owner, unrelated.fileno())]
            with managed_callback_fds((local.fileno(),), owner) as descriptors:
                script = '''import json,os,sys
def same(fd,dev,ino):
    try:
        value=os.fstat(fd)
        return (value.st_dev,value.st_ino)==(dev,ino)
    except OSError:
        return False
fd=int(sys.argv[1])
print(json.dumps([same(*value) for value in json.loads(sys.argv[2])]))
os.write(fd,b"channel-ready")
'''
                child, control = spawn_deadline_exec(
                    [sys.executable, '-I', '-S', '-c', script, str(descriptors[0]), json.dumps(forbidden)],
                    time.monotonic() + 3, pass_fds=descriptors, stdout=output)
                deadline = time.monotonic() + 3
                while not observe_exit(child.pid) and time.monotonic() < deadline:
                    time.sleep(.01)
                assert cleanup_group(child)
                assert child.returncode == 0
                peer.settimeout(1)
                assert peer.recv(64) == b'channel-ready'
                output.seek(0)
                assert json.loads(output.read()) == [False, False]
        finally:
            if child is not None and child.returncode is None:
                cleanup_group(child, timed_out=True)
            if control is not None:
                os.close(control)
            os.close(reader)
            os.close(owner)
            local.close()
            peer.close()


def test_descriptor_rejection_and_interruption_close_only_owned_duplicates(monkeypatch):
    reader, owner = os.pipe()
    local, peer = socket.socketpair()
    duplicates = []
    duplicate = os.dup
    def capture(fd):
        value = duplicate(fd)
        duplicates.append(value)
        return value
    monkeypatch.setattr('fresh_review_ipc.os.dup', capture)
    try:
        for channels in ((local.fileno(), reader), (local.fileno(), local.fileno())):
            with pytest.raises(ObservationBlocked):
                with managed_callback_fds(channels, owner):
                    pytest.fail('invalid descriptor admitted')
        with pytest.raises(KeyboardInterrupt):
            with managed_callback_fds((local.fileno(),), owner):
                raise KeyboardInterrupt()
        for fd in duplicates:
            with pytest.raises(OSError):
                os.fstat(fd)
        os.fstat(local.fileno())
        os.fstat(owner)
    finally:
        os.close(reader)
        os.close(owner)
        local.close()
        peer.close()


def test_channel_accepts_only_the_existing_five_methods():
    operation = channel()
    methods = ('observe_parent', 'launch', 'collect', 'cancel', 'recover')
    assert operation.feed(b''.join(frame(message(method=method, nonce=method)) for method in methods)) == methods
    operation.finish()


def test_successful_eof_closes_channel_even_for_a_new_valid_nonce():
    operation = channel()
    assert operation.feed(frame(message())) == ('observe_parent',)
    operation.finish()
    with pytest.raises(ObservationBlocked):
        operation.feed(frame(message(nonce='new-valid-nonce')))
