"""Launch a verifier in its owned group with an independent absolute deadline.

The supervisor owns and pins this group leader until cleanup before reap.
The watchdog shares the group; its SIGKILL also kills itself and descendants.
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


def _stop(control, reason):
    try:
        os.write(control, reason)  # nonblocking; never a payload channel
    except OSError:
        pass
    os.killpg(os.getpgrp(), signal.SIGKILL)


def main():
    if len(sys.argv) < 4:
        return 2
    deadline = float(sys.argv[1])
    control = int(sys.argv[2])
    if not math.isfinite(deadline) or deadline <= 0 or control < 3:
        return 2
    if sys.argv[3] == '--watchdog':
        os.write(1, b'R')  # private readiness pipe; target has not started
        while time.monotonic() < deadline:
            time.sleep(min(.01, max(0, deadline - time.monotonic())))
        _stop(control, b'T')
        return 2
    os.set_blocking(control, False)
    try:
        watchdog = subprocess.Popen([sys.executable, '-I', '-S', __file__, sys.argv[1], sys.argv[2], '--watchdog'],
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
        # the reporting supervisor has crashed. Both guards own this group.
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
