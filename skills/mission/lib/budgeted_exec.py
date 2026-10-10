"""Exec-only process-group boundary; cleanup precedes reap and caller settlement."""
from __future__ import annotations

import json
import errno
import contextlib
import math
import os
from pathlib import Path
import selectors
import select
import signal
import struct
import subprocess
import sys
import time

from mission_persistence.spawn_jobs import JOB_LIMIT, create_job, read_job

FRAME_LIMIT = 64 * 1024
_UNREAPED_CHILDREN = []  # retain ownership if the OS cannot confirm leader exit


def _has_waitid():
    return all(hasattr(os, name) for name in ('waitid', 'WEXITED', 'WNOWAIT', 'WNOHANG', 'P_PID',
                                             'CLD_EXITED', 'CLD_KILLED', 'CLD_DUMPED'))


def _has_kqueue():
    return sys.platform == 'darwin' and all(hasattr(select, name) for name in
        ('kqueue', 'kevent', 'KQ_FILTER_PROC', 'KQ_NOTE_EXIT', 'KQ_EV_ADD', 'KQ_EV_ERROR'))


def spawn_exec(argv, *, pass_fds=(), stdin=subprocess.DEVNULL,
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=None, env=None):
    """No Python pre-exec callbacks or caller-supplied session options."""
    if not (_has_waitid() or _has_kqueue()):
        raise ValueError('budget-deadline-unenforceable')
    return subprocess.Popen(argv, start_new_session=True, close_fds=True,
                            pass_fds=pass_fds, stdin=stdin, stdout=stdout, stderr=stderr, cwd=cwd, env=env)


def spawn_deadline_exec(argv, deadline, *, pass_fds=(), stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=None, env=None):
    """Start an exec boundary with a detached watchdog and return its control reader.

    The bootstrap is shared with verifier execution.  The caller owns and must
    close the returned read descriptor after it has interpreted ``E`` or ``T``.
    """
    receiver, sender = os.pipe()
    try:
        os.set_blocking(receiver, False)
        bootstrap = Path(__file__).parent / 'mission_application' / 'verification_exec.py'
        child = spawn_exec([sys.executable, '-I', '-S', str(bootstrap), str(deadline), str(sender), *argv],
                           pass_fds=(*pass_fds, sender), stdin=stdin, stdout=stdout, stderr=stderr,
                           cwd=cwd, env=env)
    except BaseException:
        os.close(receiver)
        raise
    finally:
        os.close(sender)
    return child, receiver


def read_deadline_control(receiver):
    """Return bootstrap/watchdog reason bytes without blocking the deadline path."""
    try:
        return os.read(receiver, 2)
    except BlockingIOError:
        return b''


def observe_exit(pid):
    if _has_waitid():
        info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
        # Darwin can return non-exit siginfo for a stopped child despite WEXITED.
        return (info is not None and info.si_pid == pid
                and info.si_code in (os.CLD_EXITED, os.CLD_KILLED, os.CLD_DUMPED))
    if not _has_kqueue():
        raise ValueError('budget-deadline-unenforceable')
    # Owned, unreaped children cannot reuse their PID. Darwin returns ESRCH
    # when exit wins registration; otherwise NOTE_EXIT observes without reap.
    # Never poll/wait Popen here; the leader must pin its group until cleanup.
    with contextlib.closing(select.kqueue()) as queue:
        change = select.kevent(pid, filter=select.KQ_FILTER_PROC,
                               flags=select.KQ_EV_ADD, fflags=select.KQ_NOTE_EXIT)
        events = queue.control([change], 1, 0)
        for event in events:
            if event.flags & select.KQ_EV_ERROR:
                if event.data == errno.ESRCH:
                    return True
                raise OSError(event.data, 'process exit observation failed')
        return any(event.ident == pid and event.filter == select.KQ_FILTER_PROC
                   and event.fflags & select.KQ_NOTE_EXIT for event in events)


def _signal_group(pid, number):
    try:
        os.killpg(pid, number)
        return True
    except ProcessLookupError:
        return True
    except OSError:
        return False


def _group_absent(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        pass
    return False


def cleanup_group(child, *, term_grace=.2, kill_wait=.2, timed_out=False, exit_probe=observe_exit, reap=None):
    """Leader remains owned until SIGKILL. Failure to confirm returns False."""
    def exited():
        try:
            return exit_probe(child.pid)
        except OSError:
            return False  # observation failure must not prevent group SIGKILL
    if timed_out:
        _signal_group(child.pid, signal.SIGTERM)
        end = time.monotonic() + term_grace
        while time.monotonic() < end and not exited():
            time.sleep(min(.01, max(0, end - time.monotonic())))
    _signal_group(child.pid, signal.SIGKILL)
    end = time.monotonic() + kill_wait
    while not exited():
        if time.monotonic() >= end:
            _UNREAPED_CHILDREN.append(child)
            return False
        time.sleep(min(.01, max(0, end - time.monotonic())))
    try:
        # NOTE_EXIT can arrive just before waitpid becomes ready on Darwin.
        (reap if reap is not None else lambda: child.wait(timeout=max(0, end - time.monotonic())))()
    except subprocess.TimeoutExpired:
        _UNREAPED_CHILDREN.append(child)
        return False
    while not _group_absent(child.pid):
        if time.monotonic() >= end:
            return False
        time.sleep(min(.01, max(0, end - time.monotonic())))
    return True  # ESRCH after reap proves absence even if a zombie refused SIGKILL


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('non-finite JSON')
    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('non-finite JSON')
        return number
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                      parse_constant=constant, parse_float=finite)


def read_frame(child, fd, deadline, *, exit_probe=observe_exit, frame_limit=FRAME_LIMIT):
    """Nonblocking single frame; EOF is not required from surviving descendants."""
    os.set_blocking(fd, False)
    data = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        while True:
            exited = exit_probe(child.pid)
            for _, _ in selector.select(0 if exited else min(.01, max(0, deadline - time.monotonic()))):
                chunk = os.read(fd, frame_limit + 5 - len(data))
                if chunk:
                    data.extend(chunk)
                else:
                    selector.unregister(fd)
            if len(data) >= 4:
                size = struct.unpack('!I', data[:4])[0]
                if size == 0 or size > frame_limit or len(data) > size + 4:
                    raise ValueError('invalid result frame')
            if exited:
                # Drain any buffered bytes without waiting for an inherited fd.
                while len(data) <= frame_limit + 4:
                    try:
                        chunk = os.read(fd, frame_limit + 5 - len(data))
                    except BlockingIOError:
                        break
                    if not chunk:
                        break
                    data.extend(chunk)
                break
            if time.monotonic() >= deadline:
                raise TimeoutError('approval verifier timed out')
    if len(data) < 4 or len(data) != 4 + struct.unpack('!I', data[:4])[0] or len(data) > frame_limit + 4:
        raise ValueError('invalid result frame')
    result = strict_json(data[4:])
    if not isinstance(result, dict):
        raise ValueError('invalid result frame')
    return result


def run_job(kind, payload, directory, *, timeout=5, term_grace=.2, kill_wait=.2, cwd=None, deadline=None, reservation_id=None, collect_deadline=None, session_id=None):
    absolute_deadline = deadline is not None
    deadline = time.monotonic() + timeout if deadline is None else deadline
    if collect_deadline is not None and (kind != 'verification' or not math.isfinite(collect_deadline) or collect_deadline < deadline):
        raise ValueError('invalid verification collection deadline')
    if time.monotonic() >= deadline:
        error = TimeoutError('budget-child-timeout')
        error.exec_unstarted = True
        raise error
    receiver, sender = os.pipe()
    child, control_receiver, path, timed_out = None, None, None, False
    try:
        job = {'schema': 'mission-exec-job/1', 'kind': kind, 'result_fd': sender, **payload,
               'expires_at': time.time() + max(0, deadline - time.monotonic())}
        raw = json.dumps(job, allow_nan=False, separators=(',', ':')).encode()
        # Decode before writing as well as in the child (no arbitrary fields).
        from mission_application.spawn_trampoline import decode_job
        decode_job(raw)
        path, digest = create_job(Path(directory), raw, reservation_id=reservation_id, session_id=session_id)
        try:
            if absolute_deadline and time.monotonic() >= deadline:
                raise TimeoutError('budget-child-timeout')
            argv = [sys.executable, '-I', str(Path(__file__).parent / 'mission_application' / 'spawn_trampoline.py'),
                    str(path), digest]
            if absolute_deadline:
                # Verification may collect a bounded frame after target deadline.
                # Keep the independent guard through that collection window.
                watchdog_deadline = min((collect_deadline if collect_deadline is not None else deadline) + 1,
                                        time.monotonic() + 86400)
                child, control_receiver = spawn_deadline_exec(argv, watchdog_deadline,
                    pass_fds=(sender,), cwd=cwd)
            else:
                # Inert approval descriptors have no budget deadline; preserve
                # the legacy direct spawn contract.
                child = spawn_exec(argv, pass_fds=(sender,), cwd=cwd)
            os.close(sender)
            sender = None
            result = read_frame(child, receiver, deadline if collect_deadline is None else collect_deadline,
                                **({'frame_limit': JOB_LIMIT} if kind == 'verification' else {}))
        except TimeoutError:
            timed_out = True
            raise
        finally:
            if child is not None and not cleanup_group(child, term_grace=term_grace,
                                                       kill_wait=kill_wait, timed_out=timed_out):
                raise ValueError('kill-unconfirmed')
            if control_receiver is not None:
                control = read_deadline_control(control_receiver)
                os.close(control_receiver)
                control_receiver = None
                if b'E' in control:
                    error = OSError('deadline exec failed')
                    error.exec_unstarted = True
                    raise error
        if child.returncode != 0 or set(result) != {'ok', 'result'} or result['ok'] is not True or not isinstance(result['result'], dict):
            raise ValueError('approval verifier rejected the evidence')
        return result['result']
    except Exception as exc:
        if child is None:
            exc.exec_unstarted = True
        raise
    finally:
        with contextlib.suppress(OSError):
            os.close(receiver)
        if sender is not None:
            with contextlib.suppress(OSError):
                os.close(sender)
        if control_receiver is not None:
            with contextlib.suppress(OSError):
                os.close(control_receiver)
        if path is not None:
            path.unlink(missing_ok=True)


def write_frame(fd, result, *, frame_limit=FRAME_LIMIT):
    raw = json.dumps(result, allow_nan=False, separators=(',', ':')).encode()
    if len(raw) > frame_limit:
        raise ValueError('result too large')
    frame = memoryview(struct.pack('!I', len(raw)) + raw)
    while frame:
        written = os.write(fd, frame)
        if written <= 0:
            raise ValueError('result write stalled')
        frame = frame[written:]


def parse_cli(parser):
    """Crash jobs are reconciled on every CLI startup; unknown jobs stay put."""
    args = parser.parse_args()
    from mission_persistence.spawn_jobs import cleanup_jobs
    try:
        cleanup_jobs(Path.cwd() / '.mission-state' / 'exec-jobs')
    except (OSError, ValueError):
        pass  # preserve unsafe/unknown residuals; dispatch validates again
    return args
