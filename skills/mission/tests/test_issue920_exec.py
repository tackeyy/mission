"""Exec boundary regressions: private jobs, process lifetime and deadline."""
import hashlib
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


def test_exec_keeps_leader_unreaped_until_group_cleanup(tmp_path, monkeypatch):
    import time
    from budgeted_exec import spawn_exec, observe_exit, cleanup_group
    child = spawn_exec([sys.executable, '-I', '-c', 'pass'])
    import budgeted_exec
    original = budgeted_exec._signal_group
    def signal_before_reap(pid, number):
        assert os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT).si_pid == pid
        return original(pid, number)
    monkeypatch.setattr(budgeted_exec, '_signal_group', signal_before_reap)
    try:
        until = time.monotonic() + 10
        while not observe_exit(child.pid) and time.monotonic() < until:
            time.sleep(.01)
        assert observe_exit(child.pid)
        assert os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT).si_pid == child.pid
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
    assert jobs.cleanup_jobs(directory) == [path]  # reused PID is a dead owner
    reserved, _ = jobs.create_job(directory, b'{}', reservation_id='reservation_1')
    monkeypatch.setattr(jobs, 'process_start', lambda pid: 'absent')
    assert jobs.cleanup_jobs(directory) == []  # reservation state unknown
    assert jobs.cleanup_jobs(directory, open_reservations={'reservation_1'}) == []
    assert jobs.cleanup_jobs(directory, open_reservations=set()) == [reserved]


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


@pytest.mark.parametrize('startup', ['pth', 'sitecustomize'])
def test_startup_grandchild_stall_stays_in_group_and_is_swept(tmp_path, installed_verifier, startup):
    import signal
    import time
    from budgeted_exec import run_job
    pin, request, _, site = installed_verifier
    marker = tmp_path / 'grandchild.json'
    # Child Python starts a descendant before trampoline and stalls afterwards.
    # The descendant uses exec so pytest's own at-fork hooks never run here.
    code = ("import subprocess, sys, os, json, time; "
            "p=subprocess.Popen([sys.executable,'-S','-c','import time; time.sleep(20)']); "
            f"open({str(marker)!r},'w').write(json.dumps([p.pid,os.getpid(),os.getpgrp()])); time.sleep(20)")
    if startup == 'pth':
        (site / 'stall.pth').write_text(code + '\n')
    else:
        (site / 'prioritize.pth').write_text(f'import sys; sys.path.insert(0, {str(site)!r})\n')
        (site / 'sitecustomize.py').write_text(code + '\n')
    began = time.monotonic()
    try:
        with pytest.raises((ValueError, TimeoutError)):
            run_job('approval-verifier', {'verifier': pin, 'request': request}, tmp_path / 'jobs', timeout=2, kill_wait=1)
        assert time.monotonic() - began < 10
        assert marker.exists()
        import json
        grandchild, leader, group = json.loads(marker.read_text())
        assert group == leader
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


def test_successful_callback_cannot_leave_descendants(tmp_path, installed_verifier):
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


def test_large_job_and_startup_stall_have_no_blocking_parent_write(tmp_path, installed_verifier):
    import time
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
    import json
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
    import time
    from types import SimpleNamespace
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
