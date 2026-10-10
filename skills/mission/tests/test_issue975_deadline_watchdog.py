import contextlib
import json
import os
import signal
import sys
import time

import pytest


def _wait(predicate, seconds=10):
    end = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < end, 'owned process did not reach expected state'
        time.sleep(.01)


def _absent(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _marker_pids(marker):
    try:
        value = json.loads(marker.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, list) and len(value) == 3 else None


def _stopped_program(marker):
    return (
        'import json,os,signal,subprocess,sys,time; from pathlib import Path; '
        'grandchild=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
        f'Path({str(marker)!r}).write_text(json.dumps([os.getpgrp(),os.getpid(),grandchild.pid])); '
        'os.killpg(os.getpgrp(),signal.SIGSTOP); time.sleep(60)'
    )


@pytest.mark.parametrize('supervisor_signal', [signal.SIGSTOP, signal.SIGKILL])
def test_deadline_spawn_reclaims_stopped_target_after_supervisor_stops(tmp_path, supervisor_signal):
    """The detached watchdog owns deadline recovery, not the reporting parent."""
    import budgeted_exec

    marker = tmp_path / 'owned-pids'
    deadline = time.monotonic() + 6
    script = f'''
import sys,time
sys.path.insert(0,{str(__import__('pathlib').Path(__file__).resolve().parents[1] / 'lib')!r})
from budgeted_exec import spawn_deadline_exec
child,control=spawn_deadline_exec([sys.executable,'-c',{_stopped_program(marker)!r}],{deadline!r})
time.sleep(60)
'''
    supervisor = budgeted_exec.spawn_exec([sys.executable, '-I', '-S', '-c', script])
    try:
        _wait(lambda: _marker_pids(marker) is not None)
        pgid, target, grandchild = _marker_pids(marker)
        os.kill(supervisor.pid, supervisor_signal)
        if supervisor_signal == signal.SIGKILL:
            supervisor.wait(timeout=1)
        _wait(lambda: time.monotonic() >= deadline and _absent(target) and _absent(grandchild))
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(supervisor.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            supervisor.wait(timeout=1)
        if marker.exists():
            pgid = json.loads(marker.read_text())[0]
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pgid, signal.SIGKILL)
