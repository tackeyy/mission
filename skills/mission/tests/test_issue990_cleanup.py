"""Cleanup must preserve interruption, lifetime evidence and success policy."""
import errno
import os
import time
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize('cleanup_error', [OSError, KeyboardInterrupt])
def test_cleanup_scope_preserves_original_exception_and_confirmation(cleanup_error):
    from exec_cleanup import cleanup_scope
    original = KeyboardInterrupt('original operation interrupted')
    original.exec_cleanup_confirmed = True
    def fail():
        raise cleanup_error('housekeeping failed')
    with pytest.raises(KeyboardInterrupt) as observed:
        with cleanup_scope(fail):
            raise original
    assert observed.value is original
    assert original.exec_cleanup_confirmed is True


def test_cleanup_scope_does_not_treat_enclosing_except_as_its_original_error():
    from exec_cleanup import cleanup_scope
    failure = OSError(errno.EBADF, 'close failed on success')
    def fail():
        raise failure
    try:
        raise KeyboardInterrupt('unrelated outer interruption')
    except KeyboardInterrupt:
        with pytest.raises(OSError) as observed:
            with cleanup_scope(fail):
                pass
    assert observed.value is failure


@pytest.mark.parametrize('boundary', ['control-close', 'frame-selector'])
def test_run_job_housekeeping_preserves_interruption_after_confirmed_cleanup(tmp_path, monkeypatch, boundary):
    import budgeted_exec as execution
    from mission_application.verifier_policy import validate
    from .test_issue878_verification_runner import _contract, _policy
    contract = _contract('neutral-cleanup')
    contract['verifier_policy'] = dict(digest='sha256:' + 'a' * 64, commands=validate(_policy()))
    receiver, sender = os.pipe()
    original = KeyboardInterrupt('original frame interrupted')
    close = os.close
    monkeypatch.setattr(execution, 'spawn_deadline_exec', lambda *a, **kw:
        (SimpleNamespace(pid=12345, returncode=0), receiver))
    monkeypatch.setattr(execution, 'cleanup_group', lambda *a, **kw: True)
    monkeypatch.setattr(execution, 'read_deadline_control', lambda *a: b'')
    if boundary == 'control-close':
        def interrupt(*a, **kw):
            raise original
        def deny_control(fd):
            if fd == receiver:
                raise OSError(errno.EBADF, 'control close failed')
            return close(fd)
        monkeypatch.setattr(execution, 'read_frame', interrupt)
        monkeypatch.setattr(execution.os, 'close', deny_control)
    else:
        selector = execution.selectors.DefaultSelector()
        selector_close = selector.close
        def deny_selector():
            selector_close()
            raise OSError(errno.EBADF, 'selector close failed')
        def interrupt(*a, **kw):
            raise original
        read_frame = execution.read_frame
        monkeypatch.setattr(selector, 'close', deny_selector)
        monkeypatch.setattr(execution.selectors, 'DefaultSelector', lambda: selector)
        monkeypatch.setattr(execution, 'read_frame', lambda *a, **kw:
            read_frame(*a, **kw, exit_probe=interrupt))
    try:
        deadline = time.monotonic() + 5
        with pytest.raises(KeyboardInterrupt) as observed:
            execution.run_job('verification', dict(contract=contract, criterion='AC1',
                repro_input=None, deadline=deadline), tmp_path / 'jobs', deadline=deadline)
        assert observed.value is original
        assert original.exec_cleanup_confirmed is True
        assert original.exec_unstarted is False
    finally:
        for fd in (receiver, sender):
            try:
                close(fd)
            except OSError:
                pass


def test_fresh_review_control_close_preserves_original_interruption(monkeypatch):
    import fresh_review_host as host
    receiver, sender = os.pipe()
    close = os.close
    original = KeyboardInterrupt('original adapter interrupted')
    original.exec_cleanup_confirmed = True
    def interrupt(*a, **kw):
        raise original
    def deny_control(fd):
        if fd == receiver:
            raise OSError(errno.EBADF, 'adapter control close failed')
        return close(fd)
    monkeypatch.setattr(host, 'spawn_deadline_exec', lambda *a, **kw:
        (SimpleNamespace(pid=12345, returncode=0), receiver))
    monkeypatch.setattr(host, 'observe_exit', interrupt)
    monkeypatch.setattr(host, 'cleanup_group', lambda *a, **kw: True)
    monkeypatch.setattr(host.os, 'close', deny_control)
    try:
        with pytest.raises(KeyboardInterrupt) as observed:
            host._call(None, 'observe', {})
        assert observed.value is original
        assert original.exec_cleanup_confirmed is True
    finally:
        close(receiver)
        close(sender)


def test_verifier_registered_control_close_preserves_confirmed_interruption(monkeypatch):
    import budgeted_exec as execution
    from mission_application import verification_runner as runner
    from mission_application.verifier_policy import validate
    from .test_issue878_verification_runner import _policy
    stdout_fd, output_fd = os.pipe()
    receiver, sender = os.pipe()
    close = os.close
    close(output_fd)
    stdout = os.fdopen(stdout_fd, 'rb', buffering=0)
    child = SimpleNamespace(pid=12345, stdout=stdout, returncode=0, wait=lambda **kw: 0)
    confirmed = []
    original = KeyboardInterrupt('original verifier interrupted after collection')
    def cleanup(*a, **kw):
        confirmed.append(True)
        return True
    def interrupt(*a, **kw):
        assert confirmed == [True]
        original.exec_cleanup_confirmed = True
        raise original
    def deny_control(fd):
        if fd == receiver:
            raise OSError(errno.EBADF, 'registered close failed')
        return close(fd)
    monkeypatch.setattr(execution, 'spawn_deadline_exec', lambda *a, **kw: (child, receiver))
    monkeypatch.setattr(execution, 'observe_exit', lambda pid: True)
    monkeypatch.setattr(execution, 'cleanup_group', cleanup)
    monkeypatch.setattr(execution, 'read_deadline_control', interrupt)
    monkeypatch.setattr(runner.os, 'close', deny_control)
    try:
        command = validate(_policy())['project-test']
        with pytest.raises(KeyboardInterrupt) as observed:
            runner.execute_candidate(runner.CandidateSnapshot((), runner._digest(())),
                command, relative_cwd='.', budget_deadline=time.monotonic() + 5)
        assert observed.value is original
        assert original.exec_cleanup_confirmed is True
    finally:
        stdout.close()
        close(receiver)
        close(sender)


def test_successful_private_write_close_failure_still_refuses_job(tmp_path, monkeypatch):
    from mission_persistence import spawn_jobs as jobs
    close = os.close
    failure = OSError(errno.EBADF, 'successful write close failed')
    closed = []
    def deny_close(fd):
        close(fd)
        closed.append(fd)
        # Fail the writer's first close only; later validation reads stay real.
        if len(closed) == 1:
            raise failure
    monkeypatch.setattr(jobs.os, 'close', deny_close)
    with pytest.raises(jobs.JobWriteError) as observed:
        jobs.create_job(tmp_path / 'jobs', b'{}')
    assert observed.value.__cause__ is failure
    assert len(closed) == 1
    assert not list((tmp_path / 'jobs').iterdir())


def test_watchdog_ready_close_preserves_bootstrap_interruption(monkeypatch):
    from mission_application import verification_exec as bootstrap
    receiver, sender = os.pipe()
    original = KeyboardInterrupt('original watchdog readiness interrupted')
    original.exec_cleanup_confirmed = True
    def interrupt(*a, **kw):
        raise original
    def deny_close():
        raise OSError(errno.EBADF, 'watchdog pipe close failed')
    class Ready:
        close = staticmethod(deny_close)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.close()
    ready = Ready()
    monkeypatch.setattr(bootstrap.sys, 'argv', ['bootstrap', str(time.monotonic() + 5), str(sender), 'neutral'])
    monkeypatch.setattr(bootstrap.os, 'getpgrp', lambda: 12345)
    monkeypatch.setattr(bootstrap.os, 'getpid', lambda: 12345)
    monkeypatch.setattr(bootstrap.subprocess, 'Popen', lambda *a, **kw: SimpleNamespace(stdout=ready))
    monkeypatch.setattr(bootstrap.select, 'select', interrupt)
    # A regressed bootstrap must not signal an unowned group during this test.
    monkeypatch.setattr(bootstrap, '_stop', lambda *a, **kw: None)
    try:
        with pytest.raises(KeyboardInterrupt) as observed:
            bootstrap.main()
        assert observed.value is original
        assert original.exec_cleanup_confirmed is True
    finally:
        os.close(receiver)
        os.close(sender)
