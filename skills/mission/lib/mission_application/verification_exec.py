"""Exec a verifier in its owned group with an independent absolute deadline.

The supervisor owns and pins this group leader until cleanup before reap.
The watchdog shares the group; its SIGKILL also kills itself and descendants.
Use -I -S for this bootstrap so site hooks cannot precede watchdog admission.
"""
from __future__ import annotations

import math
import os
import signal
import subprocess
import sys
import time


def main():
    if len(sys.argv) < 4:
        return 2
    deadline = float(sys.argv[1])
    control = int(sys.argv[2])
    if not math.isfinite(deadline) or deadline <= 0 or control < 3:
        return 2
    if sys.argv[3] == '--watchdog':
        while time.monotonic() < deadline:
            time.sleep(min(.01, max(0, deadline - time.monotonic())))
        try:
            os.write(control, b'T')  # one byte, nonblocking; never a payload channel
        except OSError:
            pass
        os.killpg(os.getpgrp(), signal.SIGKILL)
        return 2
    # Spawn before exec: descendants cannot outlive the absolute deadline even
    # if the reporting supervisor stalls or crashes during candidate cleanup.
    os.set_blocking(control, False)
    try:
        watchdog = subprocess.Popen([sys.executable, '-I', '-S', __file__, sys.argv[1], sys.argv[2], '--watchdog'],
            pass_fds=(control,), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
        os.set_inheritable(control, False)
        if time.monotonic() >= deadline:
            os.killpg(os.getpgrp(), signal.SIGKILL)
        os.execvpe(sys.argv[3], sys.argv[3:], os.environ)
    except OSError:
        try:
            os.write(control, b'E')
        finally:
            os.killpg(os.getpgrp(), signal.SIGKILL)
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
