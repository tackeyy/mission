"""Launch a verifier in its owned group with an independent absolute deadline.

The supervisor owns and pins this group leader until cleanup before reap.
The watchdog uses a separate group in the same session, pinning the session
leader ID until its last group signal. Parent exit also ends its lifetime.
Use -I -S for this bootstrap so site hooks cannot precede watchdog admission.
"""
from __future__ import annotations

import math
import os
import select
import signal
import subprocess
import sys
import time


MAX_DEADLINE_AHEAD_SEC = 86400  # F's seconds-policy ceiling; also bounds select


def deadline_is_valid(value):
    # Check the range before isfinite so huge integers cannot overflow float.
    return (type(value) in (int, float) and 0 < value <= time.monotonic() + MAX_DEADLINE_AHEAD_SEC
            and math.isfinite(value))


def _stop(control, reason, pgid=None):
    try:
        os.write(control, reason)  # nonblocking; never a payload channel
    except OSError:
        pass
    os.killpg(os.getpgrp() if pgid is None else pgid, signal.SIGKILL)


def main():
    if len(sys.argv) < 4:
        return 2
    control = int(sys.argv[2])
    if control < 3:
        return 2
    os.set_blocking(control, False)
    try:
        deadline = float(sys.argv[1])
    except (ValueError, OverflowError):
        deadline = None
    if not deadline_is_valid(deadline):
        try:
            os.write(control, b'E')
        except OSError:
            pass
        return 2
    if sys.argv[3] == '--watchdog':
        pgid = int(sys.argv[4])
        # Detach before ready; keep the session so its leader ID cannot be reused.
        os.setpgid(0, 0)
        os.write(1, b'R')  # private readiness pipe; target has not started
        while time.monotonic() < deadline and os.getppid() == pgid:
            time.sleep(min(.01, max(0, deadline - time.monotonic())))
        try:
            _stop(control, b'T' if time.monotonic() >= deadline else b'', pgid)
        except ProcessLookupError:
            pass  # reporting supervisor already reclaimed the group
        return 0
    if os.getpgrp() != os.getpid():
        try:
            os.write(control, b'E')
        except OSError:
            pass
        return 2
    try:
        watchdog = subprocess.Popen([sys.executable, '-I', '-S', __file__, sys.argv[1], sys.argv[2], '--watchdog', str(os.getpgrp())],
            pass_fds=(control,), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True)
        with watchdog.stdout as ready:
            readable, _, _ = select.select([ready], [], [], max(0, deadline-time.monotonic()))
            if not readable:
                _stop(control, b'T')
                return 2
            if os.read(ready.fileno(), 1) != b'R' or watchdog.poll() is not None:
                _stop(control, b'E')
                return 2
        os.set_inheritable(control, False)
        if time.monotonic() >= deadline:
            _stop(control, b'T')
            return 2
        target = subprocess.Popen(sys.argv[3:], close_fds=True)
        # Keep the group leader alive: watchdog death must fail closed even if
        # the reporting supervisor has crashed. The detached guard also reclaims it on bootstrap exit.
        while True:
            if watchdog.poll() is not None:
                _stop(control, b'E')
                return 2
            if time.monotonic() >= deadline:
                _stop(control, b'T')
                return 2
            code = target.poll()
            if code is not None:
                if code < 0:
                    if -code not in {signal.SIGKILL, signal.SIGSTOP}:
                        signal.signal(-code, signal.SIG_DFL)
                    os.kill(os.getpid(), -code)
                return code
            time.sleep(.01)
    except OSError:
        _stop(control, b'E')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
