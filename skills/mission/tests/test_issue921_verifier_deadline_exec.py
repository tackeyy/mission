"""Private deadline executor; CLI admission stays pending in this precursor."""
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from mission_application import verification_runner as runner
from mission_application.verifier_policy import validate
from .test_issue878_verification_runner import _policy


@pytest.fixture(autouse=True)
def owned_groups(monkeypatch):
    """Even failed assertions reclaim only groups created by this test."""
    import budgeted_exec
    original, cleanup, children = budgeted_exec.spawn_exec, budgeted_exec.cleanup_group, []
    def spawn(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(budgeted_exec, 'spawn_exec', spawn)
    yield
    for child in children:
        if child.returncode is None:
            cleanup(child, term_grace=0, kill_wait=1)


def _wait(predicate, seconds=2):
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


def _program(marker):
    return ('import os,json,subprocess,sys,time; from pathlib import Path; '
        'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
        f'm=Path({str(marker)!r}); '
        'm.with_suffix(".tmp").write_text(json.dumps([os.getpgrp(),os.getpid(),p.pid])); '
        'm.with_suffix(".tmp").replace(m); '
        'print("prefix",flush=True); time.sleep(60)')


@contextlib.contextmanager
def _marked_group(marker):
    try:
        yield
    finally:
        if marker.exists():
            pids = json.loads(marker.read_text())
            if any(not _absent(pid) for pid in pids[1:]):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(pids[0], signal.SIGKILL)


def _faulty_watchdog(marker, fault, deadline, *, target=None, watchdog_marker=None):
    """Inject a real watchdog process, leaving bootstrap/target unmocked."""
    import budgeted_exec
    helper = Path(runner.__file__).with_name('verification_exec.py')
    receiver, sender = os.pipe()
    script = f'''
import runpy,subprocess,sys
from pathlib import Path
namespace=runpy.run_path({str(helper)!r})
original=subprocess.Popen
def injected(argv, **kwargs):
    if '--watchdog' in argv:
        child=original([sys.executable,'-c',{fault!r}] if {fault!r} is not None else argv, **kwargs)
        if {str(watchdog_marker)!r} != 'None':
            Path({str(watchdog_marker)!r}).write_text(str(child.pid))
        return child
    return original(argv, **kwargs)
subprocess.Popen=injected
sys.argv=['bootstrap',{str(deadline)!r},{str(sender)!r},*{(target or [sys.executable, '-c', _program(marker)])!r}]
raise SystemExit(namespace['main']())
'''
    try:
        child = budgeted_exec.spawn_exec([sys.executable, '-I', '-S', '-c', script],
            pass_fds=(sender,))
    finally:
        os.close(sender)
    return child, receiver


@pytest.mark.parametrize('deadline', [float('inf'), float('nan'), 1e10, 1e18, 1e300,
    True, False, -1, 'later', 10**400])
def test_invalid_budget_deadline_is_blocked_before_spawn(monkeypatch, deadline):
    import budgeted_exec
    spawned, original = [], budgeted_exec.spawn_exec
    def spawn(*args, **kwargs):
        spawned.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(budgeted_exec, 'spawn_exec', spawn)
    command = validate(_policy())['project-test']
    result = runner.execute_candidate(runner.CandidateSnapshot((), runner._digest(())),
        command, relative_cwd='.', budget_deadline=deadline)
    assert result['status'] == 'blocked' and result['block_reason'] == 'process-unavailable'
    assert result['exit_code'] is None and not result['timed_out']
    assert result['observed_output_bytes'] == 0 and spawned == []


@pytest.mark.parametrize('deadline', ['inf', 'nan', '1e10', '1e18', '1e300', 'True', 'later', '-1'])
def test_bootstrap_invalid_deadline_reports_admission_failure(monkeypatch, deadline):
    from mission_application import verification_exec as bootstrap
    receiver, sender = os.pipe()
    os.set_blocking(receiver, False)
    monkeypatch.setattr(bootstrap.sys, 'argv', ['bootstrap', deadline, str(sender), 'neutral-verifier'])
    monkeypatch.setattr(bootstrap.subprocess, 'Popen',
        lambda *a, **kw: pytest.fail('invalid deadline admitted a watchdog or target'))
    try:
        assert bootstrap.main() == 2
        assert os.read(receiver, 1) == b'E'
    finally:
        os.close(receiver); os.close(sender)


@pytest.mark.parametrize('mode,backend', [
    ('leader', 'native'), ('group', 'native'), ('group', 'kqueue'), ('legacy', 'native'),
])
def test_stopped_verifier_waits_for_deadline(tmp_path, monkeypatch, mode, backend):
    if backend == 'kqueue':
        if sys.platform != 'darwin':
            pytest.skip('Darwin kqueue fallback')
        monkeypatch.delattr(os, 'waitid', raising=False)
    marker = tmp_path / 'owned-pids'
    stop = (f'os.kill(os.getppid(),{int(signal.SIGSTOP)})' if mode == 'leader' else
            f'os.killpg(os.getpgrp(),{int(signal.SIGSTOP)})')
    program = _program(marker).removesuffix('time.sleep(60)') + stop + '; time.sleep(60)'
    command = validate(_policy())['project-test']
    command.update(argv=[command['argv'][0], '-c', program], timeout_sec=1 if mode == 'legacy' else 5)
    deadline = time.monotonic()+1
    kwargs = {} if mode == 'legacy' else {'budget_deadline': deadline}
    with _marked_group(marker):
        result = runner.execute_candidate(runner.CandidateSnapshot((), runner._digest(())),
            command, relative_cwd='.', **kwargs)
        assert marker.exists(), 'verifier did not reach SIGSTOP'
        assert result['status'] == 'blocked' and result['timed_out']
        assert result['block_reason'] == ('timeout' if mode == 'legacy' else 'budget-deadline')
        assert time.monotonic() >= deadline and result['exit_code'] == -signal.SIGKILL
        _wait(lambda: all(_absent(pid) for pid in json.loads(marker.read_text())[1:]))


@pytest.mark.parametrize('fault,reason,seconds', [
    ('raise SystemExit(1)', b'E', 3),
    ('import os,time; os.write(1,b"X"); time.sleep(60)', b'E', 3),
    ('import time; time.sleep(60)', b'T', .5),
])
def test_watchdog_must_be_ready_before_target_exec(tmp_path, fault, reason, seconds):
    import budgeted_exec
    marker = tmp_path / 'owned-pids'
    with _marked_group(marker):
        child, receiver = _faulty_watchdog(marker, fault, time.monotonic()+seconds)
        try:
            _wait(lambda: budgeted_exec.observe_exit(child.pid) or marker.exists())
            assert not marker.exists(), 'target exec preceded watchdog readiness'
            assert os.read(receiver, 1) == reason
        finally:
            os.close(receiver)


def test_deadline_rechecked_after_watchdog_ready_before_target_exec(monkeypatch):
    from types import SimpleNamespace
    from mission_application import verification_exec as bootstrap
    deadline, now, launches = time.monotonic()+5, [], []
    receiver, sender = os.pipe()
    ready_receiver, ready_sender = os.pipe()
    os.write(ready_sender, b'R'); os.close(ready_sender)
    ready = os.fdopen(ready_receiver, 'rb')
    def spawn(argv, **kwargs):
        launches.append(argv)
        if '--watchdog' not in argv:
            pytest.fail('target started after deadline reached during readiness')
        return SimpleNamespace(stdout=ready, poll=lambda: None)
    read = os.read
    def consume(fd, count):
        data = read(fd, count)
        if fd == ready_receiver:
            now.append(deadline)
        return data
    monkeypatch.setattr(bootstrap.sys, 'argv', ['bootstrap', str(deadline), str(sender), 'neutral-target'])
    monkeypatch.setattr(bootstrap, 'time', SimpleNamespace(monotonic=lambda: now[-1] if now else deadline-1))
    monkeypatch.setattr(bootstrap.subprocess, 'Popen', spawn)
    monkeypatch.setattr(bootstrap.os, 'read', consume)
    def kill(*args):
        raise SystemExit('owned group reclaimed')
    monkeypatch.setattr(bootstrap.os, 'killpg', kill)
    try:
        with pytest.raises(SystemExit, match='owned group reclaimed'):
            bootstrap.main()
        assert len(launches) == 1 and os.read(receiver, 1) == b'T'
    finally:
        ready.close(); os.close(receiver); os.close(sender)


def test_watchdog_exit_kills_verifier_group(tmp_path):
    marker = tmp_path / 'owned-pids'
    with _marked_group(marker):
        child, receiver = _faulty_watchdog(marker,
            'import os,time; os.write(1,b"R"); time.sleep(.5)', time.monotonic()+10)
        try:
            _wait(marker.exists)
            pids = json.loads(marker.read_text())
            _wait(lambda: all(_absent(pid) for pid in pids[1:]))
            assert os.read(receiver, 1) == b'E'
        finally:
            os.close(receiver)


@pytest.mark.parametrize('terminal', ['passed', 'failed', 'unstarted'])
def test_watchdog_exits_after_bootstrap_terminal(tmp_path, terminal):
    import budgeted_exec
    marker, watchdog_marker = tmp_path / 'owned-pids', tmp_path / 'watchdog-pid'
    target = [str(tmp_path / 'missing-target')] if terminal == 'unstarted' else [sys.executable, '-c',
        _program(marker).removesuffix('time.sleep(60)') + f'raise SystemExit({int(terminal == "failed")})']
    with _marked_group(marker):
        child, receiver = _faulty_watchdog(marker, None, time.monotonic()+30,
            target=target, watchdog_marker=watchdog_marker)
        try:
            _wait(watchdog_marker.exists)
            watchdog_pid = int(watchdog_marker.read_text())
            _wait(lambda: budgeted_exec.observe_exit(child.pid) and _absent(watchdog_pid))
            if terminal != 'unstarted':
                assert marker.exists()
                _wait(lambda: all(_absent(pid) for pid in json.loads(marker.read_text())[1:]))
        finally:
            os.close(receiver)
            if watchdog_marker.exists():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(watchdog_marker.read_text()), signal.SIGKILL)


def test_exit_observed_after_budget_deadline_cannot_pass(monkeypatch):
    import budgeted_exec
    from types import SimpleNamespace
    deadline = time.monotonic()+5
    observed = []
    original = budgeted_exec.observe_exit
    def observe(pid):
        result = original(pid)
        if result:
            observed.append(deadline+.01)
        return result
    monkeypatch.setattr(budgeted_exec, 'observe_exit', observe)
    monkeypatch.setattr(runner, 'time', SimpleNamespace(
        monotonic=lambda: observed[-1] if observed else time.monotonic()))
    command = validate(_policy())['project-test']
    command['timeout_sec'] = 10
    result = runner.execute_candidate(runner.CandidateSnapshot((), runner._digest(())),
        command, relative_cwd='.', budget_deadline=deadline)
    assert observed and result['exit_code'] == 0
    assert result['status'] == 'blocked' and result['block_reason'] == 'budget-deadline'


@pytest.mark.parametrize('kill_bootstrap', [False, True])
def test_deadline_watchdog_reclaims_verifier_after_supervisor_sigkill(tmp_path, kill_bootstrap):
    import budgeted_exec
    marker = tmp_path / 'owned-pids'
    deadline = time.monotonic()+3
    command = validate(_policy())['project-test']
    command['argv'] = [command['argv'][0], '-c', _program(marker)]
    script = f'''
import sys
sys.path.insert(0,{str(Path(runner.__file__).parents[1])!r})
from mission_application.verification_runner import execute_candidate,CandidateSnapshot,_digest
execute_candidate(CandidateSnapshot((),_digest(())),{command!r},relative_cwd='.',budget_deadline={deadline!r})
'''
    with _marked_group(marker):
        supervisor = budgeted_exec.spawn_exec([sys.executable, '-I', '-S', '-c', script])
        _wait(marker.exists)
        pids = json.loads(marker.read_text())
        os.kill(supervisor.pid, signal.SIGKILL)
        supervisor.wait(timeout=1)
        if kill_bootstrap:
            # Also lose the inner leader: this arm isolates the watchdog's
            # SIGKILL guarantee from the bootstrap's redundant deadline guard.
            os.kill(pids[0], signal.SIGKILL)
        _wait(lambda: time.monotonic() >= deadline and all(_absent(pid) for pid in pids[1:]), 4)


@pytest.mark.parametrize('supervisor_signal', [signal.SIGKILL, signal.SIGSTOP])
def test_stopped_group_is_reclaimed_without_reporting_supervisor(tmp_path, supervisor_signal):
    import budgeted_exec
    marker, watchdog_marker = tmp_path / 'owned-pids', tmp_path / 'watchdog-pid'
    helper = Path(runner.__file__).with_name('verification_exec.py')
    deadline = time.monotonic()+3
    program = _program(marker).removesuffix('time.sleep(60)') + \
        'os.killpg(os.getpgrp(),signal.SIGSTOP); time.sleep(60)'
    program = 'import signal; ' + program
    bootstrap_script = f'''
import runpy,subprocess,sys
from pathlib import Path
namespace=runpy.run_path({str(helper)!r})
original=subprocess.Popen
def record(argv, **kwargs):
    child=original(argv, **kwargs)
    if '--watchdog' in argv:
        Path({str(watchdog_marker)!r}).write_text(str(child.pid))
    return child
subprocess.Popen=record
raise SystemExit(namespace['main']())
'''
    stopped_marker = tmp_path / 'stopped-leader'
    supervisor_script = f'''
import os,subprocess,sys,time
from pathlib import Path
receiver,sender=os.pipe()
child=subprocess.Popen([sys.executable,'-I','-S','-c',{bootstrap_script!r},
    {str(deadline)!r},str(sender),sys.executable,'-c',{program!r}],
    pass_fds=(sender,),start_new_session=True)
os.close(sender)
_,state=os.waitpid(child.pid,os.WUNTRACED)
assert os.WIFSTOPPED(state)
Path({str(stopped_marker)!r}).write_text('stopped')
child.wait()
'''
    with _marked_group(marker):
        supervisor = budgeted_exec.spawn_exec([sys.executable, '-I', '-S', '-c', supervisor_script])
        try:
            _wait(lambda: marker.exists() and watchdog_marker.exists() and stopped_marker.exists())
            pids = json.loads(marker.read_text())
            watchdog_pid = int(watchdog_marker.read_text())
            os.kill(supervisor.pid, supervisor_signal)
            _wait(lambda: time.monotonic() >= deadline and
                all(_absent(pid) for pid in [*pids[1:], watchdog_pid]), 4)
        finally:
            # Markers identify only our processes, including the detached guard.
            if watchdog_marker.exists():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(watchdog_marker.read_text()), signal.SIGKILL)
            with contextlib.suppress(ProcessLookupError):
                os.kill(supervisor.pid, signal.SIGKILL)
            supervisor.wait(timeout=1)


def test_legacy_timeout_reclaims_grandchild(tmp_path):
    marker = tmp_path / 'owned-pids'
    command = validate(_policy())['project-test']
    command.update(argv=[command['argv'][0], '-c', _program(marker)], timeout_sec=1)
    with _marked_group(marker):
        result = runner.execute_candidate(runner.CandidateSnapshot((), runner._digest(())),
            command, relative_cwd='.')
        assert result['timed_out'] and result['block_reason'] == 'timeout'
        _wait(lambda: all(_absent(pid) for pid in json.loads(marker.read_text())[1:]))


def test_budget_mode_preserves_replay_input_and_frozen_environment():
    import hashlib
    command = validate(_policy())['project-test']
    command.update(env={'NEUTRAL_PROBE': 'frozen'}, argv=[command['argv'][0], '-c',
        'import os; from pathlib import Path; assert os.environ["NEUTRAL_PROBE"]=="frozen"; '
        'print(Path("repro.json").read_text())'])
    result = runner.execute_candidate(runner.CandidateSnapshot((), runner._digest(())),
        command, relative_cwd='.', budget_deadline=time.monotonic()+5,
        repro_input=('counterexample', 'repro.json', b'proof'))
    assert result['status'] == 'passed' and result['exit_code'] == 0
    assert result['output_digest'] == 'sha256:'+hashlib.sha256(b'proof\n').hexdigest()
    assert result['repro_input_digest'] is not None


@pytest.mark.parametrize('number', [signal.SIGINT, signal.SIGPIPE])
def test_budget_mode_preserves_signal_exit_without_bootstrap_output(number):
    import hashlib
    command = validate(_policy())['project-test']
    command.update(argv=['/bin/sh', '-c', f'kill -{int(number)} $$'], toolchain=None)
    result = runner.execute_candidate(runner.CandidateSnapshot((), runner._digest(())),
        command, relative_cwd='.', budget_deadline=time.monotonic()+5)
    assert result['status'] == 'failed' and result['exit_code'] == -number
    assert result['observed_output_bytes'] == 0
    assert result['output_digest'] == 'sha256:'+hashlib.sha256(b'').hexdigest()


def test_budget_mode_still_reports_a_shorter_frozen_timeout(tmp_path):
    import time
    from mission_application.verification_runner import execute_candidate
    from .test_issue878_verification_runner import _policy
    from mission_application.verifier_policy import validate
    policy = _policy()
    policy['commands'][0].update(timeout_sec=1, argv=[policy['commands'][0]['argv'][0], '-c', 'import time; time.sleep(60)'])
    command = validate(policy)['project-test']
    from mission_application.verification_runner import CandidateSnapshot, _digest
    candidate = CandidateSnapshot((), _digest(()))
    result = execute_candidate(candidate, command, relative_cwd='.', budget_deadline=time.monotonic() + 10)
    assert result['timed_out'] and result['block_reason'] == 'timeout'



def test_verifier_grandchild_is_gone_before_post_run_observation(tmp_path, monkeypatch):
    import os
    import time
    from mission_application import verification_runner as runner
    from mission_application.verifier_policy import validate
    from .test_issue878_verification_runner import _policy
    marker = tmp_path / 'grandchild-pid'
    policy = _policy()
    policy['commands'][0].update(timeout_sec=2, argv=[policy['commands'][0]['argv'][0], '-c',
        'import subprocess,sys,time; from pathlib import Path; '
        'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
        f'Path({str(marker)!r}).write_text(str(p.pid)); print("prefix",flush=True); time.sleep(60)'])
    command = validate(policy)['project-test']
    snapshot = runner.CandidateSnapshot((runner.CandidateFile('input', 0o644, b'input'),), '')
    from dataclasses import replace
    snapshot = replace(snapshot, digest=runner._digest(snapshot.files))
    read = runner._read
    checked = []
    def observe(*a, **kw):
        pid = int(marker.read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        checked.append(pid)
        return read(*a, **kw)
    monkeypatch.setattr(runner, '_read', observe)
    import budgeted_exec
    cleanup = budgeted_exec.cleanup_group
    def before_cleanup(child, **kw):
        # SIGKILL at the child deadline must include grandchildren, before
        # normal terminal cleanup or potentially slow candidate observation.
        end = time.monotonic() + .2
        try:
            while time.monotonic() < end:
                try:
                    os.kill(int(marker.read_text()), 0)
                except ProcessLookupError:
                    break
                time.sleep(.01)
            with pytest.raises(ProcessLookupError):
                os.kill(int(marker.read_text()), 0)
        finally:
            reclaimed = cleanup(child, **kw)  # always reclaim only our group
        return reclaimed
    monkeypatch.setattr(budgeted_exec, 'cleanup_group', before_cleanup)
    deadline = time.monotonic() + 2
    result = runner.execute_candidate(snapshot, command, relative_cwd='.', budget_deadline=deadline)
    assert time.monotonic() >= deadline  # never cut the admitted execution short
    assert result['block_reason'] == 'budget-deadline' and checked



@pytest.mark.parametrize('unavailable', [True, False])
def test_verifier_exec_failure_is_distinct_from_registered_exit_126(tmp_path, unavailable):
    import time
    from mission_application.verification_runner import execute_candidate, CandidateSnapshot, _digest
    from .test_issue878_verification_runner import _policy
    from mission_application.verifier_policy import validate
    command = validate(_policy())['project-test']
    command.update(argv=['missing-neutral-verifier'] if unavailable else
        [command['argv'][0], '-c', 'import sys; sys.exit(126)'], toolchain=None)
    result = execute_candidate(CandidateSnapshot((), _digest(())), command,
        relative_cwd='.', budget_deadline=time.monotonic()+5)
    assert result['status'] == ('blocked' if unavailable else 'failed')
    assert result['block_reason'] == ('process-unavailable' if unavailable else None)
    assert result['exit_code'] == (None if unavailable else 126)



def test_deadline_watchdog_start_failure_cannot_report_a_completed_verifier(monkeypatch):
    import os, time
    from mission_application import verification_exec as bootstrap
    receiver, sender = os.pipe()
    monkeypatch.setattr(bootstrap.sys, 'argv', ['bootstrap', str(time.monotonic()+10), str(sender), 'neutral-verifier'])
    def unavailable(*a, **kw):
        raise OSError('watchdog unavailable')
    def stop_owned_group(*a):
        raise SystemExit(2)
    monkeypatch.setattr(bootstrap.subprocess, 'Popen', unavailable)
    monkeypatch.setattr(bootstrap.os, 'killpg', stop_owned_group)
    try:
        with pytest.raises(SystemExit):
            bootstrap.main()
        assert os.read(receiver, 1) == b'E'
    finally:
        os.close(receiver); os.close(sender)



def test_completed_failed_verifier_does_not_wait_for_grandchild_stdout(tmp_path):
    import time, hashlib, os
    from mission_application.verification_runner import execute_candidate, CandidateSnapshot, _digest
    from .test_issue878_verification_runner import _policy
    from mission_application.verifier_policy import validate
    marker = tmp_path / 'grandchild-pid'
    command = validate(_policy())['project-test']
    command['argv'] = [command['argv'][0], '-c',
        'import subprocess,sys; from pathlib import Path; '
        'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
        f'Path({str(marker)!r}).write_text(str(p.pid)); print("prefix",flush=True); sys.exit(1)']
    started = time.monotonic()
    result = execute_candidate(CandidateSnapshot((), _digest(())), command,
        relative_cwd='.', budget_deadline=started+5)
    assert result['status'] == 'failed' and result['exit_code'] == 1 and not result['timed_out']
    assert result['output_digest'] == 'sha256:' + hashlib.sha256(b'prefix\n').hexdigest()
    with pytest.raises(ProcessLookupError):
        os.kill(int(marker.read_text()), 0)
