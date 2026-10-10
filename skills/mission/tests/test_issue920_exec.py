"""Exec boundary regressions: private jobs, process lifetime and deadline."""
import hashlib
import json
import signal
import subprocess
import time
import contextlib
from types import SimpleNamespace
import os
from pathlib import Path
import sys

import pytest

LIB = Path(__file__).resolve().parents[1] / 'lib'
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

def test_private_job_digest_and_cleanup(tmp_path):
    from mission_persistence.spawn_jobs import create_job, read_job
    path, digest = create_job(tmp_path / 'jobs', b'{"value":1}')
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert read_job(path, digest, limit=100) == b'{"value":1}'
    path.write_bytes(b'{"value":2}')
    with pytest.raises(ValueError):
        read_job(path, digest, limit=100)

def test_unknown_job_rejected_before_target_import():
    from mission_application.spawn_trampoline import decode_job
    with pytest.raises(ValueError):
        decode_job(b'{"schema":"mission-exec-job/1","kind":"shell","command":"echo unsafe"}')

@pytest.fixture(params=['native', 'kqueue'])
def exit_backend(request, monkeypatch):
    if request.param == 'kqueue':
        if sys.platform != 'darwin':
            pytest.skip('Darwin kqueue fallback')
        monkeypatch.delattr(os, 'waitid', raising=False)

def test_exec_keeps_leader_unreaped_until_group_cleanup(tmp_path, monkeypatch, exit_backend):
    from budgeted_exec import spawn_exec, observe_exit, cleanup_group
    child = spawn_exec([sys.executable, '-I', '-c', 'pass'])
    import budgeted_exec
    original = budgeted_exec._signal_group
    def signal_before_reap(pid, number):
        assert observe_exit(pid)
        os.kill(pid, 0)  # an exited leader still exists until group cleanup reaps it
        if hasattr(os, 'waitid'):
            assert os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT).si_pid == pid
        return original(pid, number)
    monkeypatch.setattr(budgeted_exec, '_signal_group', signal_before_reap)
    try:
        time.sleep(.05)  # registering after exit must also observe the owned zombie
        until = time.monotonic() + 10
        while not observe_exit(child.pid) and time.monotonic() < until:
            time.sleep(.01)
        assert observe_exit(child.pid)
        assert child.returncode is None
        assert cleanup_group(child, term_grace=.05, kill_wait=.5)
        with pytest.raises(ChildProcessError):
            os.waitpid(child.pid, os.WNOHANG)
    finally:
        if child.returncode is None:
            os.killpg(child.pid, 9)
            child.wait(timeout=1)

@pytest.mark.parametrize('options', [{'budgeted': True}, {'state': {'extensions': {'budget_ledger': {}}}}])
def test_budget_callable_rejected_without_spawn(tmp_path, monkeypatch, options):
    from .test_score_provenance import _load_state_module
    module = _load_state_module()
    spawned = []
    module.register_approval_verifier('fixture-verifier', lambda request: {})
    monkeypatch.setattr(module, '_run_approval_verifier', lambda *args, **kw: spawned.append(args))
    error = None
    try:
        module.verify_force_approval({}, 'fixture-verifier', cwd=tmp_path, **options)
    except ValueError as exc:
        error = str(exc)
    assert error == 'budget-deadline-unenforceable', 'budgeted callable was not refused before execution'
    assert spawned == [], 'budgeted callable reached the spawn boundary'

@pytest.mark.parametrize('failure', ['write', 'fsync', 'validation'])
def test_job_write_failure_never_spawns_and_removes_partial(tmp_path, monkeypatch, failure):
    from mission_persistence import spawn_jobs as jobs
    if failure == 'validation':
        monkeypatch.setattr(jobs, 'read_job', lambda *a, **kw: (_ for _ in ()).throw(ValueError('changed')))
    else:
        monkeypatch.setattr(jobs.os, failure, lambda *a: (_ for _ in ()).throw(OSError(28, 'full')))
    with pytest.raises(jobs.JobWriteError) as error:
        jobs.create_job(tmp_path / 'jobs', b'{}')
    assert error.value.reason_code == 'budget-job-write-failed'
    assert list((tmp_path / 'jobs').iterdir()) == []

@pytest.mark.parametrize('attack', ['symlink', 'hardlink', 'mode', 'fifo', 'directory-mode', 'oversize'])
def test_private_job_rejects_unsafe_files(tmp_path, attack):
    from mission_persistence.spawn_jobs import create_job, read_job
    path, digest = create_job(tmp_path / 'jobs', b'{}')
    if attack == 'symlink':
        original = path.with_suffix('.original')
        path.rename(original)
        path.symlink_to(original)
    elif attack == 'hardlink':
        os.link(path, path.with_suffix('.link'))
    elif attack == 'mode':
        path.chmod(0o644)
    elif attack == 'fifo':
        path.unlink()
        os.mkfifo(path, 0o600)
    elif attack == 'directory-mode':
        path.parent.chmod(0o755)
    else:
        path.write_bytes(b'x' * 101)
    with pytest.raises((ValueError, OSError)):
        read_job(path, digest, limit=100)

def test_exclusive_write_does_not_remove_existing_file(tmp_path):
    from mission_persistence.spawn_jobs import write_private_file
    path = tmp_path / 'owned'
    path.write_bytes(b'keep')
    with pytest.raises(FileExistsError):
        write_private_file(path, b'replace')
    assert path.read_bytes() == b'keep'

def test_residual_jobs_preserve_live_unknown_and_open_reservations(tmp_path, monkeypatch):
    from mission_persistence import spawn_jobs as jobs
    directory = tmp_path / 'jobs'
    path, _ = jobs.create_job(directory, b'{}')
    start = jobs.process_start(os.getpid())
    assert start is not None and jobs.cleanup_jobs(directory) == []
    monkeypatch.setattr(jobs, 'process_start', lambda pid: None)
    assert jobs.cleanup_jobs(directory) == []
    monkeypatch.setattr(jobs, 'process_start', lambda pid: str(int(start) + 1))
    assert jobs.cleanup_jobs(directory) == []  # dead parent, unknown reader deadline
    import time
    expired, _ = jobs.create_job(directory, json.dumps({'expires_at': time.time() - 1}).encode())
    monkeypatch.setattr(jobs, 'process_start', lambda pid: str(int(start) + 2))
    assert jobs.cleanup_jobs(directory) == [expired]
    path.unlink()
    reserved, _ = jobs.create_job(directory, b'{}', reservation_id='reservation_1', session_id='test-session')
    monkeypatch.setattr(jobs, 'process_start', lambda pid: 'absent')
    assert jobs.cleanup_jobs(directory) == []  # reservation state unknown
    assert jobs.cleanup_jobs(directory, open_reservations={'reservation_1'}) == []
    assert jobs.cleanup_jobs(directory, open_reservations=set()) == []  # another session is unknown
    assert jobs.cleanup_jobs(directory, open_reservations=set(), closed_reservations={'reservation_1'}, session_id='test-session') == [reserved]

def test_callable_setsid_failure_cannot_invoke_callback(tmp_path, monkeypatch):
    from mission_application import approval_verifier as approval
    marker = tmp_path / 'executed'
    monkeypatch.setattr(approval.os, 'setsid', lambda: (_ for _ in ()).throw(OSError('denied')))
    with pytest.raises(ValueError):
        approval.run_callable(lambda _: marker.write_text('bad'), {}, timeout=.5)
    assert not marker.exists()

@pytest.fixture
def installed_verifier(tmp_path, isolated_provider_python):
    from datetime import datetime, timezone
    from scoring_provenance import build_request
    package = tmp_path / 'provider'
    package.mkdir()
    source = package / 'neutral_verifier.py'
    source.write_text('def verify(request):\n return {"verified": True}\n')
    metadata = package / 'neutral_verifier-1.0.dist-info'
    metadata.mkdir()
    (metadata / 'METADATA').write_text('Name: neutral-verifier\nVersion: 1.0\n')
    (metadata / 'entry_points.txt').write_text('[mission.approval_verifiers]\nneutral = neutral_verifier:verify\n')
    executable, site = isolated_provider_python(package)
    pin = dict(entry_point='neutral', distribution='neutral-verifier', version='1.0',
               module='neutral_verifier', entry_point_value='neutral_verifier:verify',
               source_digest='sha256:' + hashlib.sha256(source.read_bytes()).hexdigest())
    request = build_request(session_id='test', mission_id='abc12345',
        revision_scope={'kind':'not-applicable', 'reason_code':'non-git'},
        terminal_object_digest='sha256:'+'b'*64, approval_evidence_ref='sha256:'+'a'*64,
        approved_actor='role:owner', approved_at=datetime.now(timezone.utc).isoformat(),
        reason_code='user-override', event_nonce='c'*64)
    return pin, request, source, site

def test_registry_exec_does_not_run_parent_atfork_hooks(tmp_path, installed_verifier):
    from budgeted_exec import run_job
    pin, request, _, _ = installed_verifier
    marker = tmp_path / 'fork-hook'
    os.register_at_fork(after_in_child=lambda: marker.write_text('bad'))
    result = run_job('approval-verifier', {'verifier': pin, 'request': request}, tmp_path / 'jobs')
    assert result == {'verified': True}
    assert not marker.exists()
    assert list((tmp_path / 'jobs').iterdir()) == []


def test_unbudgeted_approval_descriptor_run_job_keeps_direct_spawn(tmp_path, installed_verifier, monkeypatch):
    import budgeted_exec as execution
    pin, request, _, _ = installed_verifier
    calls = []
    actual = execution.spawn_exec
    def watched(argv, **kwargs):
        calls.append(argv)
        return actual(argv, **kwargs)
    monkeypatch.setattr(execution, 'spawn_exec', watched)
    assert execution.run_job('approval-verifier', {'verifier': pin, 'request': request}, tmp_path / 'jobs') == {'verified': True}
    assert len(calls) == 1 and 'spawn_trampoline.py' in calls[0][2]


@pytest.mark.parametrize('supervisor_signal', [signal.SIGSTOP, signal.SIGKILL])
def test_approval_descriptor_watchdog_reclaims_stopped_callback_after_supervisor_stops(
        tmp_path, installed_verifier, supervisor_signal):
    """The descriptor job keeps its deadline watchdog after its caller is lost."""
    pin, request, source, _ = installed_verifier
    marker = tmp_path / 'approval-stop.json'
    source.write_text(
        'import json, os, signal, subprocess, sys, time\n'
        'from pathlib import Path\n'
        'def verify(request):\n'
        ' child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
        f' Path({str(marker)!r}).write_text(json.dumps([os.getpgrp(),os.getpid(),child.pid]))\n'
        ' os.killpg(os.getpgrp(), signal.SIGSTOP)\n'
        ' time.sleep(60)\n'
    )
    pin['source_digest'] = 'sha256:' + hashlib.sha256(source.read_bytes()).hexdigest()
    driver = (
        'import sys; from pathlib import Path; '
        f'sys.path.insert(0, {str(LIB)!r}); '
        'from mission_application.approval_verifier import run_approval,verify_approval_request; '
        'from functools import partial; '
        f'verify_approval_request({request!r}, "neutral", verifiers={{}}, '
        f'resolve=lambda cwd,name: {pin!r}, execute=partial(run_approval, timeout=12, '
        f'directory=Path({str(tmp_path / "jobs")!r})), '
        f'cwd=Path({str(tmp_path)!r}))'
    )
    supervisor = subprocess.Popen([sys.executable, '-I', '-c', driver], cwd=tmp_path,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                  start_new_session=True)
    def marker_pids():
        try:
            value = json.loads(marker.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, list) and len(value) == 3 else None
    def absent(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False
    def group_absent(pgid):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return True  # Darwin can retain an unowned zombie group leader.
        # Linux keeps a killed leader as a zombie while its stopped parent cannot
        # reap it; a group holding only zombies has no live member.
        rows = subprocess.run(['ps', '-A', '-o', 'pgid=,stat='], capture_output=True, text=True).stdout
        return not any(parts[0] == str(pgid) and not parts[1].startswith('Z')
                       for parts in (row.split() for row in rows.splitlines()) if len(parts) >= 2)
    try:
        end = time.monotonic() + 10
        while marker_pids() is None:
            assert supervisor.poll() is None, supervisor.stderr.read().decode()
            assert time.monotonic() < end
            time.sleep(.01)
        pgid, target, grandchild = marker_pids()
        os.kill(supervisor.pid, supervisor_signal)
        if supervisor_signal == signal.SIGKILL:
            supervisor.wait(timeout=1)
        end = time.monotonic() + 18
        while not (absent(target) and absent(grandchild) and group_absent(pgid)):
            assert time.monotonic() < end
            time.sleep(.01)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(supervisor.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            supervisor.wait(timeout=1)
        supervisor.stderr.close()
        if marker_pids() is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(marker_pids()[0], signal.SIGKILL)

@pytest.mark.parametrize('startup', ['pth', 'sitecustomize'])
def test_startup_grandchild_stall_stays_in_group_and_is_swept(tmp_path, installed_verifier, startup, exit_backend):
    from budgeted_exec import run_job
    pin, request, _, site = installed_verifier
    marker = tmp_path / 'grandchild.json'
    # Child Python starts a descendant before trampoline and stalls afterwards.
    # The descendant uses exec so pytest's own at-fork hooks never run here.
    code = ("import subprocess, sys, os, json, time; "
            "p=subprocess.Popen([sys.executable,'-S','-c','import time; time.sleep(20)']); "
            f"open({str(marker)!r},'w').write(json.dumps([p.pid,os.getpid(),os.getpgrp(),os.getppid()])); time.sleep(20)")
    if startup == 'pth':
        (site / 'stall.pth').write_text(code + '\n')
    else:
        (site / 'prioritize.pth').write_text(f'import sys; sys.path.insert(0, {str(site)!r})\n')
        (site / 'sitecustomize.py').write_text(code + '\n')
    began = time.monotonic()
    try:
        with pytest.raises((ValueError, TimeoutError)):
            run_job('approval-verifier', {'verifier': pin, 'request': request}, tmp_path / 'jobs',
                    deadline=time.monotonic() + 2, kill_wait=1)
        assert time.monotonic() - began < 10
        assert marker.exists()
        grandchild, target, group, bootstrap = json.loads(marker.read_text())
        assert group != os.getpgrp() and group == bootstrap and group != target
        with pytest.raises(ProcessLookupError):
            os.killpg(group, 0)
        assert list((tmp_path / 'jobs').iterdir()) == []
    finally:
        if marker.exists():
            for pid in __import__('json').loads(marker.read_text())[:2]:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

def test_successful_callback_cannot_leave_descendants(tmp_path, installed_verifier, exit_backend):
    from budgeted_exec import run_job
    pin, request, source, _ = installed_verifier
    marker = tmp_path / 'normal-grandchild'
    source.write_text('import subprocess, sys, os\ndef verify(request):\n'
                      ' p=subprocess.Popen([sys.executable,"-S","-c","import time; time.sleep(20)"])\n'
                      f' open({str(marker)!r},"w").write(str(p.pid))\n'
                      ' return {"pid":p.pid,"group":os.getpgrp()}\n')
    pin['source_digest'] = 'sha256:' + hashlib.sha256(source.read_bytes()).hexdigest()
    result = None
    try:
        result = run_job('approval-verifier', {'verifier':pin, 'request':request}, tmp_path / 'jobs', kill_wait=1)
        with pytest.raises(ProcessLookupError):
            os.killpg(result['group'], 0)
        assert list((tmp_path / 'jobs').iterdir()) == []
    finally:
        if marker.exists():
            try:
                os.kill(int(marker.read_text()), 9)
            except ProcessLookupError:
                pass

def test_large_job_and_startup_stall_have_no_blocking_parent_write(tmp_path, installed_verifier, exit_backend):
    from budgeted_exec import run_job
    pin, _, _, site = installed_verifier
    request = dict(schema='mission-provider-approval-request/1', preflight_id='preflight',
        session_id='test', mission_id='mission', outbound_context_digest='sha256:'+'a'*64,
        invocation_id='invocation', outbound_packet_digest='sha256:'+'b'*64,
        registry_entry_digest='sha256:'+'c'*64, selection_id='selection', selection_source='automatic',
        iteration=1, phase='execution', risk_scopes=['external-send'] * 80000, evidence_ref='evidence')
    (site / 'stall.pth').write_text('import time; time.sleep(20)\n')
    began = time.monotonic()
    with pytest.raises(TimeoutError):
        run_job('approval-verifier', {'verifier':pin, 'request':request}, tmp_path / 'jobs', timeout=.5)
    assert time.monotonic() - began < 10
    assert list((tmp_path / 'jobs').iterdir()) == []

def test_user_only_provider_is_invisible_to_isolated_child(tmp_path, installed_verifier, monkeypatch):
    from budgeted_exec import run_job
    pin, request, source, site = installed_verifier
    (site / 'fixture-provider.pth').unlink()
    monkeypatch.setenv('PYTHONPATH', str(source.parent))
    with pytest.raises(ValueError):
        run_job('approval-verifier', {'verifier':pin, 'request':request}, tmp_path / 'jobs')
    assert list((tmp_path / 'jobs').iterdir()) == []

@pytest.mark.parametrize('attack', ['source', 'value', 'version', 'distribution', 'unserializable', 'oversize'])
def test_registry_child_rechecks_pin_and_bounded_json_result(tmp_path, installed_verifier, attack):
    from budgeted_exec import run_job
    pin, request, source, _ = installed_verifier
    if attack == 'source':
        source.write_text('def verify(request): return {"changed":True}\n')
    elif attack == 'value':
        pin['entry_point_value'] = 'neutral_verifier:other'
    elif attack in {'version', 'distribution'}:
        pin[attack] = 'other'
    else:
        source.write_text('def verify(request): return {"result":'+("b'bytes'" if attack == 'unserializable' else "'x'*100000")+'}\n')
        pin['source_digest'] = 'sha256:' + hashlib.sha256(source.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        run_job('approval-verifier', {'verifier':pin, 'request':request}, tmp_path / 'jobs')
    assert list((tmp_path / 'jobs').iterdir()) == []

def test_exec_failure_removes_job_before_return(tmp_path, installed_verifier, monkeypatch):
    import budgeted_exec as execution
    pin, request, _, _ = installed_verifier
    monkeypatch.setattr(execution, 'spawn_exec', lambda *a, **kw: (_ for _ in ()).throw(OSError('setsid failed')))
    with pytest.raises(OSError):
        execution.run_job('approval-verifier', {'verifier':pin, 'request':request}, tmp_path / 'jobs')
    assert list((tmp_path / 'jobs').iterdir()) == []


def test_run_job_keeps_bootstrap_exec_refusal_distinct_from_target_nonzero(
        tmp_path, installed_verifier, monkeypatch):
    """An E control message is unstarted; a target exit is verifier rejection."""
    import budgeted_exec as execution
    pin, request, _, _ = installed_verifier
    real_spawn = execution.spawn_exec
    receivers = []
    def target_exit(*args, **kwargs):
        receiver, sender = os.pipe()
        os.close(sender)
        receivers.append(receiver)
        return real_spawn([sys.executable, '-c', 'raise SystemExit(7)']), receiver
    monkeypatch.setattr(execution, 'spawn_deadline_exec', target_exit)
    with pytest.raises(ValueError, match='invalid result frame') as nonzero:
        execution.run_job('approval-verifier', {'verifier': pin, 'request': request}, tmp_path / 'nonzero',
                          deadline=time.monotonic() + 5)
    assert not getattr(nonzero.value, 'exec_unstarted', False)
    def exec_refusal(*args, **kwargs):
        receiver, sender = os.pipe()
        os.write(sender, b'E')
        os.close(sender)
        receivers.append(receiver)
        return real_spawn([sys.executable, '-c', 'raise SystemExit(2)']), receiver
    monkeypatch.setattr(execution, 'spawn_deadline_exec', exec_refusal)
    with pytest.raises(OSError, match='deadline exec failed') as refused:
        execution.run_job('approval-verifier', {'verifier': pin, 'request': request}, tmp_path / 'refusal',
                          deadline=time.monotonic() + 5)
    assert refused.value.exec_unstarted is True

def test_kill_unconfirmed_cannot_return_success(tmp_path, installed_verifier, monkeypatch):
    import budgeted_exec as execution
    pin, request, _, _ = installed_verifier
    monkeypatch.setattr(execution, '_group_absent', lambda pid: False)
    with pytest.raises(ValueError, match='kill-unconfirmed'):
        execution.run_job('approval-verifier', {'verifier':pin, 'request':request}, tmp_path / 'jobs', kill_wait=.01)
    assert list((tmp_path / 'jobs').iterdir()) == []

def test_shared_staging_writer_removes_partial_file(tmp_path, monkeypatch):
    from mission_persistence import local_uow
    path = tmp_path / 'partial'
    monkeypatch.setattr(local_uow, '_fsync', lambda fd: (_ for _ in ()).throw(OSError(28, 'full')))
    with pytest.raises(OSError):
        local_uow._write_private_file(path, b'partial')
    assert not path.exists()

@pytest.mark.parametrize('raw', [b'{"value":1e999}', '{"value":1}'.encode('utf-16')])
def test_result_json_rejects_nonfinite_numbers_and_non_utf8(raw):
    from budgeted_exec import strict_json
    with pytest.raises(ValueError):
        strict_json(raw)

def test_no_follow_rejects_link_even_if_name_is_repaired_after_open(tmp_path, monkeypatch):
    from mission_persistence import spawn_jobs as jobs
    path, digest = jobs.create_job(tmp_path / 'jobs', b'{}')
    original = path.with_suffix('.original')
    path.rename(original)
    path.symlink_to(original)
    real_open = os.open
    def repair_after_open(*args):
        fd = real_open(*args)
        path.unlink()
        original.rename(path)
        return fd
    monkeypatch.setattr(jobs.os, 'open', repair_after_open)
    with pytest.raises(OSError):
        jobs.read_job(path, digest)

def test_post_spawn_fd_close_failure_still_sweeps_child(tmp_path, installed_verifier, monkeypatch):
    import budgeted_exec as execution
    pin, request, _, _ = installed_verifier
    original_spawn, original_close = execution.spawn_exec, os.close
    spawned = []
    def spawn(*args, **kw):
        child = original_spawn(*args, **kw)
        spawned.append((child, kw['pass_fds'][0]))
        return child
    def close(fd):
        if spawned and fd == spawned[0][1]:
            original_close(fd)
            raise OSError('close failed')
        original_close(fd)
    monkeypatch.setattr(execution, 'spawn_exec', spawn)
    monkeypatch.setattr(execution.os, 'close', close)
    with pytest.raises(OSError, match='close failed'):
        execution.run_job('approval-verifier', {'verifier':pin, 'request':request}, tmp_path / 'jobs')
    assert spawned[0][0].returncode is not None
    assert execution._group_absent(spawned[0][0].pid)
    assert list((tmp_path / 'jobs').iterdir()) == []

@pytest.mark.parametrize('result_fd', [0, 1, 2])
def test_job_rejects_reserved_result_descriptors(installed_verifier, result_fd):
    from mission_application.spawn_trampoline import decode_job
    pin, request, _, _ = installed_verifier
    raw = json.dumps(dict(schema='mission-exec-job/1', kind='approval-verifier',
                          result_fd=result_fd, verifier=pin, request=request)).encode()
    rejected = False
    try:
        decode_job(raw)
    except ValueError:
        rejected = True
    assert rejected, f'reserved result fd {result_fd} was accepted'

@pytest.mark.parametrize('state', [[], {'extensions': 5}])
def test_approval_rejects_malformed_state_with_value_error(state):
    from mission_application.approval_verifier import verify_approval_request
    with pytest.raises(ValueError, match='approval state is invalid'):
        verify_approval_request({}, 'fixture-verifier', state=state,
                                verifiers={'fixture-verifier': lambda _: {}}, resolve=None, execute=None)

def test_frame_deadline_fails_finitely_if_timeout_check_is_missing():
    import threading
    from budgeted_exec import read_frame
    receiver, sender = os.pipe()  # open, silent writer; no EOF can terminate the loop
    stop, outcome = threading.Event(), []
    def read():
        try:
            read_frame(SimpleNamespace(pid=0), receiver, time.monotonic() + .05,
                       exit_probe=lambda _: stop.is_set())
        except Exception as exc:
            outcome.append(type(exc))
    worker = threading.Thread(target=read, daemon=True)
    try:
        worker.start()
        worker.join(2)
        assert not worker.is_alive(), 'read_frame did not stop at its deadline within the watchdog'
        assert outcome == [TimeoutError], 'silent live child must end with the deadline refusal'
    finally:
        stop.set()  # release a deadline mutant through the independent leader-exit path
        worker.join(1)
        os.close(receiver)
        os.close(sender)

def test_host_without_nonreaping_observation_refuses_before_spawn(monkeypatch):
    import select
    import budgeted_exec as execution
    spawned = []
    monkeypatch.delattr(os, 'waitid', raising=False)
    monkeypatch.delattr(select, 'kqueue', raising=False)
    monkeypatch.setattr(execution.subprocess, 'Popen', lambda *a, **kw: spawned.append(a))
    with pytest.raises(ValueError, match='budget-deadline-unenforceable'):
        execution.spawn_exec(['true'])
    assert spawned == []

def test_observation_failure_still_kills_group_and_retains_leader(monkeypatch):
    import budgeted_exec as execution
    child = execution.spawn_exec([sys.executable, '-I', '-c', 'import time; time.sleep(20)'])
    original_signal, signals = execution._signal_group, []
    def signal_group(pid, number):
        signals.append(number)
        return original_signal(pid, number)
    def failed_probe(_):
        raise OSError(24, 'descriptor limit')
    monkeypatch.setattr(execution, '_signal_group', signal_group)
    try:
        assert not execution.cleanup_group(child, timed_out=True, term_grace=.01, kill_wait=.01, exit_probe=failed_probe)
        assert signals == [signal.SIGTERM, signal.SIGKILL]
        assert child in execution._UNREAPED_CHILDREN and child.returncode is None
    finally:
        original_signal(child.pid, signal.SIGKILL)
        child.wait(timeout=1)
        if child in execution._UNREAPED_CHILDREN:
            execution._UNREAPED_CHILDREN.remove(child)

def test_exit_notification_reap_is_bounded_and_after_group_kill(monkeypatch):
    import budgeted_exec as execution
    child = execution.spawn_exec([sys.executable, '-I', '-c', 'pass'])
    original_wait, original_signal = child.wait, execution._signal_group
    killed = []
    def signal_group(pid, number):
        killed.append(pid)
        return original_signal(pid, number)
    def wait(*, timeout):
        assert killed == [child.pid], 'reap preceded group kill'
        assert 0 < timeout <= 1, 'NOTE_EXIT reap must wait within cleanup budget'
        return original_wait(timeout=timeout)
    monkeypatch.setattr(execution, '_signal_group', signal_group)
    monkeypatch.setattr(child, 'wait', wait)
    try:
        assert execution.cleanup_group(child, kill_wait=1, exit_probe=lambda _: True)
    finally:
        if child.returncode is None:
            original_signal(child.pid, 9)
            original_wait(timeout=1)

def _configure_registry(tmp_path, monkeypatch, pin):
    config = tmp_path / 'config' / 'mission'
    config.mkdir(parents=True)
    (config / 'approval-verifiers.json').write_text(json.dumps({
        'schema': 'mission-approval-verifier-registry/2',
        'verifiers': [{'id': 'neutral', **{k: pin[k] for k in ('entry_point', 'distribution', 'version', 'source_digest')}}]}))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(config.parent))

@pytest.mark.parametrize('value, valid', [('neutral_verifier : verify', True), ('neutral_verifier:検証', True),
    ('検証器 : 検証 [extra-name]', True), ('.pkg:verify', False), ('pkg..mod:verify', False),
    ('pkg.:verify', False), ('9pkg:verify', False)])
def test_registry_entry_point_uses_metadata_grammar(tmp_path, installed_verifier, monkeypatch, value, valid):
    from importlib.metadata import EntryPoint
    from budgeted_exec import run_job
    from mission_application.spawn_trampoline import decode_job
    from .test_score_provenance import _load_state_module
    pin, request, source, _ = installed_verifier
    match = EntryPoint.pattern.match(value)
    module, attr = match.group('module'), match.group('attr')
    source = source.rename(source.with_name(module + '.py'))
    source.write_text(f'def {attr}(request): return {{"verified": True}}\n')
    (source.parent / 'neutral_verifier-1.0.dist-info' / 'entry_points.txt').write_text(
        '[mission.approval_verifiers]\nneutral = ' + value + '\n')
    pin.update(module=module, entry_point_value=value,
               source_digest='sha256:' + hashlib.sha256(source.read_bytes()).hexdigest())
    _configure_registry(tmp_path, monkeypatch, pin)
    monkeypatch.syspath_prepend(str(source.parent))
    resolve = lambda: _load_state_module()._configured_approval_entry_point(tmp_path, 'neutral')
    if valid:
        assert run_job('approval-verifier', {'verifier': resolve(), 'request': request}, tmp_path / 'jobs') == {'verified': True}
    else:
        with pytest.raises(ValueError):
            resolve()
        with pytest.raises(ValueError):
            decode_job(json.dumps(dict(schema='mission-exec-job/1', kind='approval-verifier',
                                       result_fd=3, verifier=pin, request=request)).encode())

@pytest.mark.parametrize('cleanup_errno', [None, 13])
def test_approval_write_refusal_preserves_job_failure_cause(tmp_path, installed_verifier, monkeypatch, cleanup_errno):
    from mission_persistence import spawn_jobs as jobs
    from mission_application.approval_verifier import run_approval
    pin, request, _, _ = installed_verifier
    monkeypatch.setattr(jobs.os, 'fsync', lambda *a: (_ for _ in ()).throw(OSError(28, 'full')))
    if cleanup_errno is not None:
        monkeypatch.setattr(Path, 'unlink', lambda *a, **kw: (_ for _ in ()).throw(OSError(cleanup_errno, 'denied')))
    with pytest.raises(ValueError, match='^approval verifier rejected the evidence$') as error:
        run_approval(pin, request, tmp_path / 'jobs')
    assert type(error.value) is ValueError
    assert isinstance(error.value.__cause__, jobs.JobWriteError)
    assert error.value.__cause__.reason_code == 'budget-job-write-failed'
    assert error.value.__cause__.cleanup_errno == cleanup_errno

@pytest.mark.parametrize('owned', [True, False])
def test_older_metadata_without_dist_rechecks_distribution_ownership(tmp_path, installed_verifier, monkeypatch, owned):
    import importlib.metadata
    from mission_application.approval_verifier import invoke_registered
    from .test_score_provenance import _load_state_module
    pin, request, source, _ = installed_verifier
    monkeypatch.syspath_prepend(str(source.parent))
    _configure_registry(tmp_path, monkeypatch, pin)
    loaded = []
    def load():
        loaded.append(True)
        return lambda _: {'verified': True}
    entry = SimpleNamespace(name='neutral', module=pin['module'], value=pin['entry_point_value'], load=load)
    monkeypatch.setattr(importlib.metadata, 'entry_points', lambda: {'mission.approval_verifiers': [entry]})
    if not owned:
        (source.parent / 'neutral_verifier-1.0.dist-info' / 'entry_points.txt').write_text(
            '[mission.approval_verifiers]\nother = neutral_verifier:verify\n')
    resolve = lambda: _load_state_module()._configured_approval_entry_point(tmp_path, 'neutral')
    if owned:
        assert resolve()['module'] == pin['module']
        assert loaded == []  # discovery never loads provider code in the parent
        assert invoke_registered(pin, request) == {'verified': True}
    else:
        for action in (resolve, lambda: invoke_registered(pin, request)):
            with pytest.raises(ValueError, match='distribution'):
                action()
        assert loaded == []


@pytest.mark.parametrize('reason, unstarted', [(b'W', False), (b'T', False), (b'E', True)])
def test_run_job_marks_only_pre_start_failure_as_unstarted(tmp_path, installed_verifier, monkeypatch, reason, unstarted):
    import budgeted_exec
    pin, request, _, _ = installed_verifier
    def fake(argv, deadline, **kwargs):
        receiver, sender = os.pipe()
        os.write(sender, reason)
        os.close(sender)
        return budgeted_exec.spawn_exec([sys.executable, '-c', 'raise SystemExit(3)']), receiver
    monkeypatch.setattr(budgeted_exec, 'spawn_deadline_exec', fake)
    with pytest.raises(Exception) as caught:
        budgeted_exec.run_job('approval-verifier', {'verifier': pin, 'request': request}, tmp_path / 'jobs',
                              timeout=2, kill_wait=.2, deadline=time.monotonic() + 2)
    assert getattr(caught.value, 'exec_unstarted', False) is unstarted
