"""Provider pipe exchange; admission and durable settlement belong to the caller.

This precursor owns an already spawned child from budgeted_exec. It does not
activate budget policy or mark any public spawn entry as covered.
"""
from __future__ import annotations

from dataclasses import dataclass
import contextlib
import errno
import math
import os
import selectors
import time

import budgeted_exec


@dataclass(frozen=True)
class ProviderExchange:
    stdout: bytes
    stderr: bytes
    exit_code: int | None
    timed_out: bool
    kill_confirmed: bool
    output_bytes: int
    output_truncated: bool
    output_complete: bool
    exec_failed: bool
    watchdog_failed: bool


def exchange_provider(child, packet, deadline, *, term_grace=.2, kill_wait=.2,
                      collect_sec=2, output_limit=1024 * 1024, control_receiver=None):
    """Consume exclusively owned pipes of an unreaped spawn_exec child.

    deadline is an absolute monotonic value captured before spawn, never a new
    relative timeout. Packet admission belongs to preflight: JSON escaping and
    multiple inputs have no additional encoded-size limit here. Each stream retains
    at most output_limit bytes (1..4 MiB);
    output_bytes counts drained bytes even after truncation. Incomplete/truncated
    output must not be interpreted as complete evidence by the eventual caller.
    Durations are finite seconds in 0..86400, matching the budget policy bound.
    Exceptions still clean the group; failed cleanup overrides their reason with
    kill-unconfirmed so the eventual caller retains the reservation.
    """
    outputs = {'stdout': bytearray(), 'stderr': bytearray()}
    observed, timed_out, truncated, unavailable = 0, False, False, False
    selector = None
    confirmed = None
    cleanup_grace, cleanup_wait = .2, .2

    def close_stream(stream):
        if stream is None:
            return
        if selector is not None:
            for key in list(selector.get_map().values()):
                if key.fileobj is stream:
                    with contextlib.suppress(KeyError, OSError):
                        selector.unregister(key.fd)
        if not stream.closed:
            with contextlib.suppress(OSError):
                stream.close()

    try:
        try:
            def finite(value):
                try:
                    return type(value) in (int, float) and math.isfinite(value)
                except OverflowError:
                    return False
            if (type(packet) is not bytes
                    or not finite(deadline)
                    or any(not finite(value) or not 0 <= value <= 86400
                           for value in (term_grace, kill_wait, collect_sec))
                    or type(output_limit) is not int or not 1 <= output_limit <= 4 * 1024 * 1024):
                raise ValueError('provider-exchange-invalid')
            cleanup_grace, cleanup_wait = term_grace, kill_wait
            pending = memoryview(packet)
            selector = selectors.DefaultSelector()
            for name in ('stdin', 'stdout', 'stderr'):
                stream = getattr(child, name)
                if stream is None or stream.closed:
                    unavailable |= name != 'stdin'
                    continue
                if name == 'stdin' and not pending:
                    close_stream(stream)
                    continue
                try:
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_WRITE if name == 'stdin' else selectors.EVENT_READ, name)
                except OSError as exc:
                    if exc.errno != errno.EBADF:
                        raise
                    unavailable |= name != 'stdin'
                    close_stream(stream)

            def pump(until):
                nonlocal pending, observed, truncated, unavailable
                for key, _ in selector.select(min(.01, max(0, until - time.monotonic()))):
                    try:
                        if key.data == 'stdin':
                            written = os.write(key.fd, pending[:65536])
                            if written <= 0:
                                raise OSError(errno.EIO, 'provider write made no progress')
                            pending = pending[written:]
                            if not pending:
                                close_stream(key.fileobj)
                        else:
                            chunk = os.read(key.fd, 65536)
                            if not chunk:
                                close_stream(key.fileobj)
                            else:
                                observed += len(chunk)
                                remaining = max(0, output_limit - len(outputs[key.data]))
                                outputs[key.data].extend(chunk[:remaining])
                                truncated |= len(chunk) > remaining
                    except (BlockingIOError, InterruptedError):
                        continue
                    except OSError as exc:
                        if exc.errno != errno.EBADF and not (key.data == 'stdin' and exc.errno in (errno.EPIPE, errno.ECONNRESET)):
                            raise
                        unavailable |= key.data != 'stdin'
                        close_stream(key.fileobj)

            while True:
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                if budgeted_exec.observe_exit(child.pid):
                    break
                pump(deadline)
        finally:
            close_stream(child.stdin)
            confirmed = budgeted_exec.cleanup_group(child, term_grace=cleanup_grace,
                                                   kill_wait=cleanup_wait, timed_out=timed_out)
        collect_until = time.monotonic() + collect_sec
        while selector.get_map() and time.monotonic() < collect_until:
            pump(collect_until)
        complete = not selector.get_map() and not unavailable
        control = b'' if control_receiver is None else budgeted_exec.read_deadline_control(control_receiver)
        return ProviderExchange(bytes(outputs['stdout']), bytes(outputs['stderr']),
                                None if b'E' in control else child.returncode, timed_out or b'T' in control,
                                confirmed, observed, truncated, complete, b'E' in control, b'W' in control)
    except BaseException as error:
        # Only exceptions raised by this exchange count, including collection.
        # An enclosing caller's except block is not an exchange failure.
        error.exec_cleanup_confirmed = confirmed is True
        if confirmed is False:
            failure = ValueError('kill-unconfirmed')
            failure.exec_cleanup_confirmed = False
            raise failure from error
        raise
    finally:
        for stream in (child.stdin, child.stdout, child.stderr):
            close_stream(stream)
        if selector is not None:
            # Closing the selector cannot change the confirmed group outcome.
            with contextlib.suppress(OSError):
                selector.close()
        if control_receiver is not None:
            with contextlib.suppress(OSError):
                os.close(control_receiver)
