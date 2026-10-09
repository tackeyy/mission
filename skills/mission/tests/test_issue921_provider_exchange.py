"""Bounded provider I/O precursor; public spawn admission remains pending."""
import hashlib
import errno
import os
import subprocess
import signal
import sys
import time

import pytest

import budgeted_exec


@pytest.fixture
def provider():
    children = []
    def start(code):
        child = budgeted_exec.spawn_exec(
            [sys.executable, '-I', '-c', code], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        children.append(child)
        return child
    yield start
    for child in children:
        if child.returncode is None:
            budgeted_exec.cleanup_group(child, kill_wait=1, timed_out=True)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def test_packet_larger_than_pipe_is_written_exactly_with_partial_writes(provider, monkeypatch):
    from mission_application.provider_process import exchange_provider
    packet = bytes(range(256)) * 4096
    child = provider('import hashlib,sys; raw=sys.stdin.buffer.read(); '
                     'print(hashlib.sha256(raw).hexdigest()); print(len(raw),file=sys.stderr)')
    write = os.write
    fd = child.stdin.fileno()
    monkeypatch.setattr(os, 'write', lambda target, raw: write(target, raw[:137]) if target == fd else write(target, raw))
    result = exchange_provider(child, packet, time.monotonic() + 10, kill_wait=1)
    assert result.stdout.strip() == hashlib.sha256(packet).hexdigest().encode()
    assert result.stderr.strip() == str(len(packet)).encode()
    assert result.exit_code == 0 and result.kill_confirmed and not result.timed_out
    assert result.output_complete and not result.output_truncated
    assert all(stream.closed for stream in (child.stdin, child.stdout, child.stderr))


def test_provider_closing_stdin_keeps_output_and_exit_code(provider):
    from mission_application.provider_process import exchange_provider
    child = provider('import os,sys; os.close(0); '
                     'print("accepted-prefix"); print("refused",file=sys.stderr); sys.exit(23)')
    result = exchange_provider(child, b'x' * (1024 * 1024), time.monotonic() + 10, kill_wait=1)
    assert result.stdout == b'accepted-prefix\n' and result.stderr == b'refused\n'
    assert result.exit_code == 23 and result.kill_confirmed and not result.timed_out


def test_nonreading_provider_is_recovered_at_absolute_deadline(provider):
    from mission_application.provider_process import exchange_provider
    started = time.monotonic()
    child = provider('import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)')
    result = exchange_provider(child, b'x' * (1024 * 1024), started + .3,
                               term_grace=.05, kill_wait=1, collect_sec=.05)
    assert result.timed_out and result.kill_confirmed
    assert result.exit_code is not None
    assert time.monotonic() - started < 2
    with pytest.raises(ProcessLookupError):
        os.killpg(child.pid, 0)
    assert all(stream.closed for stream in (child.stdin, child.stdout, child.stderr))


@pytest.mark.parametrize('confirmed', [True, False])
def test_pipe_error_still_cleans_group_and_closes_descriptors(provider, monkeypatch, confirmed):
    from mission_application.provider_process import exchange_provider
    child = provider('import sys,time; print("ready",flush=True); time.sleep(60)')
    fd, read = child.stdout.fileno(), os.read
    def fail_read(target, size):
        if target == fd:
            raise OSError(errno.EIO, 'pipe failure')
        return read(target, size)
    monkeypatch.setattr(os, 'read', fail_read)
    cleanup = budgeted_exec.cleanup_group
    def cleanup_result(*args, **kwargs):
        cleanup(*args, **kwargs)
        return confirmed
    monkeypatch.setattr(budgeted_exec, 'cleanup_group', cleanup_result)
    error, message = (OSError, 'pipe failure') if confirmed else (ValueError, 'kill-unconfirmed')
    with pytest.raises(error, match=message):
        exchange_provider(child, b'', time.monotonic() + 10, kill_wait=1)
    assert child.returncode is not None
    assert all(stream.closed for stream in (child.stdin, child.stdout, child.stderr))
    with pytest.raises(ProcessLookupError):
        os.killpg(child.pid, 0)


def test_large_output_is_drained_but_retained_bytes_are_bounded(provider):
    from mission_application.provider_process import exchange_provider
    child = provider('import sys; sys.stdout.buffer.write(b"a"*200000); '
                     'sys.stderr.buffer.write(b"b"*200000)')
    result = exchange_provider(child, b'', time.monotonic() + 10,
                               output_limit=1024, kill_wait=1)
    assert result.stdout == b'a' * 1024 and result.stderr == b'b' * 1024
    assert result.output_bytes == 400000
    assert result.output_truncated and result.output_complete and result.exit_code == 0


def test_closed_stdin_descriptor_does_not_discard_provider_result(provider, monkeypatch):
    from mission_application.provider_process import exchange_provider
    child = provider('print("no-input")')
    fd, set_blocking = child.stdin.fileno(), os.set_blocking
    def close_before_setup(target, blocking):
        if target == fd:
            os.close(target)
        return set_blocking(target, blocking)
    monkeypatch.setattr(os, 'set_blocking', close_before_setup)
    result = exchange_provider(child, b'packet', time.monotonic() + 10, kill_wait=1)
    assert result.stdout == b'no-input\n' and result.exit_code == 0
    assert result.kill_confirmed and result.output_complete


@pytest.mark.parametrize('options', [
    {'deadline': float('nan')}, {'deadline': True}, {'deadline': 10**400},
    {'term_grace': -1}, {'kill_wait': float('inf')}, {'collect_sec': 86401},
    {'output_limit': True}, {'output_limit': 0}, {'output_limit': 4 * 1024 * 1024 + 1},
])
def test_invalid_exchange_limits_are_rejected_and_owned_child_is_cleaned(provider, options):
    from mission_application.provider_process import exchange_provider
    child = provider('print("finished")')
    with pytest.raises(ValueError, match='provider-exchange-invalid'):
        exchange_provider(child, b'', **{'deadline': time.monotonic() + 10, **options})
    assert child.returncode is not None
    assert all(stream.closed for stream in (child.stdin, child.stdout, child.stderr))


def test_missing_output_fd_is_reported_as_incomplete(provider):
    from mission_application.provider_process import exchange_provider
    child = provider('pass')
    child.stdout.close()
    result = exchange_provider(child, b'', time.monotonic() + 10, kill_wait=1)
    assert result.exit_code == 0 and result.kill_confirmed
    assert not result.output_complete


def test_unconfirmed_group_cleanup_is_not_reported_as_success(provider, monkeypatch):
    from mission_application.provider_process import exchange_provider
    child = provider('print("done")')
    cleanup = budgeted_exec.cleanup_group
    def unconfirmed(*args, **kwargs):
        cleanup(*args, **kwargs)
        return False
    monkeypatch.setattr(budgeted_exec, 'cleanup_group', unconfirmed)
    result = exchange_provider(child, b'', time.monotonic() + 10, kill_wait=1)
    assert result.stdout == b'done\n' and result.exit_code == 0
    assert not result.kill_confirmed


def test_normal_exit_cleans_descendants_before_reaping_leader(provider, monkeypatch):
    from mission_application.provider_process import exchange_provider
    child = provider('import subprocess,sys; subprocess.Popen([sys.executable,"-I","-c",'
                     '"import time; time.sleep(60)"]); print("leader-done")')
    signal_group = budgeted_exec._signal_group
    seen = []
    def cleanup_before_reap(pid, number):
        assert child.returncode is None
        os.kill(pid, 0)
        seen.append(number)
        return signal_group(pid, number)
    monkeypatch.setattr(budgeted_exec, '_signal_group', cleanup_before_reap)
    result = exchange_provider(child, b'', time.monotonic() + 10, kill_wait=1)
    assert result.stdout == b'leader-done\n' and result.exit_code == 0
    assert result.kill_confirmed and result.output_complete and not result.timed_out
    assert seen == [9]
    with pytest.raises(ProcessLookupError):
        os.killpg(child.pid, 0)


@pytest.mark.parametrize('direction', ['read', 'write'])
@pytest.mark.parametrize('number', [errno.EAGAIN, errno.EINTR])
def test_readiness_race_retries_without_losing_packet_or_output(provider, monkeypatch, direction, number):
    from mission_application.provider_process import exchange_provider
    child = provider('import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())')
    fd = child.stdin.fileno() if direction == 'write' else child.stdout.fileno()
    operation = getattr(os, direction)
    raced = []
    def once(target, value):
        if target == fd and not raced:
            raced.append(True)
            raise OSError(number, 'readiness changed')
        return operation(target, value)
    monkeypatch.setattr(os, direction, once)
    result = exchange_provider(child, b'precise-packet', time.monotonic() + 10, kill_wait=1)
    assert raced and result.stdout == b'precise-packet' and result.exit_code == 0


def test_pipe_filling_after_readiness_cannot_block_deadline_recovery(provider, monkeypatch):
    from mission_application.provider_process import exchange_provider
    child = provider('import time; time.sleep(60)')
    fd, write = child.stdin.fileno(), os.write
    filled = []
    def fill_after_readiness(target, raw):
        if target == fd and not filled:
            filled.append(True)
            # Model a writer exhausting the space between readiness and write.
            for _ in range(1024):
                try:
                    write(fd, b'x' * 4096)
                except BlockingIOError:
                    break
        return write(target, raw)
    monkeypatch.setattr(os, 'write', fill_after_readiness)
    def expired(signum, frame):
        raise TimeoutError('blocking provider write exceeded watchdog')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, 3)
    try:
        started = time.monotonic()
        result = exchange_provider(child, b'p' * 131072, started + .3,
                                   term_grace=0, kill_wait=1, collect_sec=.05)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert filled and result.timed_out and result.kill_confirmed
    assert time.monotonic() - started < 2


@pytest.mark.parametrize('input_count', [1, 2])
def test_preflight_json_expansion_has_no_additional_packet_limit(provider, tmp_path, input_count):
    from provider_preflight import MAX_INPUT_BYTES, build_preflight, safe_input_snapshot
    from .test_provider_preflight import _subject
    from mission_application.provider_process import exchange_provider
    snapshots = []
    for index in range(input_count):
        source = tmp_path / f'input-{index}.txt'
        source.write_bytes(b'\0' * MAX_INPUT_BYTES)
        snapshots.append(safe_input_snapshot(source, root=tmp_path))
    packet = build_preflight(_subject(), snapshots)['outbound_packet_bytes']
    assert len(packet) > input_count * 6 * MAX_INPUT_BYTES
    child = provider('import hashlib,sys; raw=sys.stdin.buffer.read(); '
                     'print(hashlib.sha256(raw).hexdigest()); print(len(raw),file=sys.stderr)')
    result = exchange_provider(child, packet, time.monotonic() + 10, kill_wait=1)
    assert result.stdout.strip() == hashlib.sha256(packet).hexdigest().encode()
    assert result.stderr.strip() == str(len(packet)).encode()
    assert result.exit_code == 0 and not result.timed_out and result.kill_confirmed


def test_connection_reset_finishes_writing_and_preserves_result(provider, monkeypatch):
    from mission_application.provider_process import exchange_provider
    child = provider('import sys; sys.stdin.buffer.read(); print("accepted-prefix"); '
                     'print("refused",file=sys.stderr); sys.exit(23)')
    fd, write = child.stdin.fileno(), os.write
    injected = []
    def reset(target, raw):
        if target == fd and not injected:
            injected.append(True)
            raise ConnectionResetError(errno.ECONNRESET, 'peer reset')
        return write(target, raw)
    monkeypatch.setattr(os, 'write', reset)
    result = exchange_provider(child, b'x' * (1024 * 1024), time.monotonic() + 10, kill_wait=1)
    assert injected and result.stdout == b'accepted-prefix\n' and result.stderr == b'refused\n'
    assert result.exit_code == 23 and result.kill_confirmed and not result.timed_out


def test_stdout_before_large_stdin_completes_both_directions(provider):
    from mission_application.provider_process import exchange_provider
    packet = b'p' * (1024 * 1024)
    child = provider('import hashlib,sys; sys.stdout.buffer.write(b"o"*1048576); '
                     'sys.stdout.buffer.flush(); raw=sys.stdin.buffer.read(); '
                     'print(len(raw),hashlib.sha256(raw).hexdigest(),file=sys.stderr)')
    result = exchange_provider(child, packet, time.monotonic() + 3, kill_wait=1)
    assert result.stdout == b'o' * len(packet)
    assert result.stderr.strip() == f'{len(packet)} {hashlib.sha256(packet).hexdigest()}'.encode()
    assert result.exit_code == 0 and not result.timed_out and result.output_complete


def test_caller_exception_does_not_turn_unconfirmed_result_into_exception(provider, monkeypatch):
    from mission_application.provider_process import exchange_provider
    child = provider('print("done")')
    cleanup = budgeted_exec.cleanup_group
    def unconfirmed(*args, **kwargs):
        cleanup(*args, **kwargs)
        return False
    monkeypatch.setattr(budgeted_exec, 'cleanup_group', unconfirmed)
    try:
        raise LookupError('caller exception')
    except LookupError:
        result = exchange_provider(child, b'', time.monotonic() + 10, kill_wait=1)
    assert result.stdout == b'done\n' and result.exit_code == 0 and not result.kill_confirmed


@pytest.mark.parametrize('confirmed', [True, False])
def test_collection_error_uses_cleanup_confirmation(provider, monkeypatch, confirmed):
    from mission_application.provider_process import exchange_provider
    child = provider('print("final-output")')
    until = time.monotonic() + 3
    while not budgeted_exec.observe_exit(child.pid):
        assert time.monotonic() < until
        time.sleep(.01)
    fd, read = child.stdout.fileno(), os.read
    cleaned = []
    cleanup = budgeted_exec.cleanup_group
    def cleanup_result(*args, **kwargs):
        assert cleanup(*args, **kwargs)
        cleaned.append(True)
        return confirmed
    def fail_collection(target, size):
        if target == fd and cleaned:
            raise OSError(errno.EIO, 'collection failure')
        return read(target, size)
    monkeypatch.setattr(budgeted_exec, 'cleanup_group', cleanup_result)
    monkeypatch.setattr(os, 'read', fail_collection)
    error, message = (OSError, 'collection failure') if confirmed else (ValueError, 'kill-unconfirmed')
    with pytest.raises(error, match=message):
        exchange_provider(child, b'', time.monotonic() + 10, kill_wait=1)
    assert cleaned and child.returncode == 0
    assert all(stream.closed for stream in (child.stdin, child.stdout, child.stderr))


def test_deadline_delivers_sigterm_and_allows_handler_to_finish(provider, tmp_path):
    from mission_application.provider_process import exchange_provider
    ready = tmp_path / 'ready'
    child = provider('import pathlib,signal,sys,time\n'
                     'def terminate(signum,frame):\n'
                     ' print("term-received",flush=True)\n'
                     ' time.sleep(.05)\n'
                     ' print("grace-complete",flush=True)\n'
                     ' sys.exit(17)\n'
                     'signal.signal(signal.SIGTERM,terminate)\n'
                     f'pathlib.Path({str(ready)!r}).touch()\n'
                     'time.sleep(60)\n')
    until = time.monotonic() + 3
    while not ready.exists():
        assert time.monotonic() < until
        time.sleep(.01)
    result = exchange_provider(child, b'', time.monotonic() + .1,
                               term_grace=.5, kill_wait=1)
    assert result.timed_out and result.kill_confirmed and result.exit_code == 17
    assert result.stdout == b'term-received\ngrace-complete\n' and result.output_complete
