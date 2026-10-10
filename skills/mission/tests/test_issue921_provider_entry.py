"""Provider admission contracts at the existing public CLI boundary."""
import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from mission_kernel.budget import decode_policy, default_policy_document, ledger_document, new_ledger
from .test_provider_application_guard import _prepare_command_provider, _state_path


def _wait(predicate, seconds=5):
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


def _group_absent(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        # Darwin can retain an unowned zombie group leader after both owned
        # members have gone. The PID checks below still prove no owned member.
        return True
    return False


def _pids(marker):
    try:
        value = json.loads(marker.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, list) and len(value) == 3 else None


@pytest.fixture
def run_cli(legacy_run_cli):
    return legacy_run_cli


def _budget(root, *, expired=False):
    path = _state_path(root)
    state = json.loads(path.read_text())
    at = datetime.now(timezone.utc) - timedelta(seconds=1801 if expired else 0)
    state['budget_minutes'] = 30
    state['budget_ledger'] = ledger_document(new_ledger(
        decode_policy(default_policy_document(1800)), at.strftime('%Y-%m-%dT%H:%M:%SZ')))
    path.write_text(json.dumps(state))


def test_expired_budget_refuses_provider_before_spawn(run_cli, tmp_path, prepare_approved_invocation):
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    args, env, _ = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
                                             iteration=1, phase='planning', env_extra=env)
    _budget(tmp_path, expired=True)
    result = run_cli(*args, cwd=tmp_path, env_extra=env)
    assert result.returncode == 2 and 'budget-exhausted' in result.stderr
    assert not marker.exists()
    state = json.loads(_state_path(tmp_path).read_text())
    assert state['budget_ledger']['stop_slots']['last_refusal'] == 'budget-exhausted'
    assert state['specialist_invocations'] == []
    assert state['provider_preflights'][args[args.index('--preflight-id') + 1]]['status'] == 'approved'


def test_provider_sees_durable_reservation_and_timeout_is_settled_after_cleanup(
        run_cli, tmp_path, prepare_approved_invocation):
    import sys
    import time
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    command = tmp_path / 'commands' / 'provider-command'
    command.write_text(f'#!{sys.executable}\n'
        'import json,os,time\n'
        'state=json.load(open(os.environ["STATE_PATH"]))\n'
        'open(os.environ["PROVIDER_MARKER"],"w").write(json.dumps(state["budget_ledger"]["reservations"]))\n'
        'print("started",flush=True)\ntime.sleep(60)\n')
    env['STATE_PATH'] = str(_state_path(tmp_path))
    args, env, _ = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
                                             iteration=1, phase='planning', env_extra=env)
    _budget(tmp_path)
    started = time.monotonic()
    result = run_cli(*args, '--timeout', '3', cwd=tmp_path, env_extra=env)
    assert result.returncode == 0, result.stderr
    assert time.monotonic() - started < 10
    reservations = json.loads(marker.read_text())
    assert len(reservations) == 1 and reservations[0]['reserved_bytes'] >= 64 * 1024
    state = json.loads(_state_path(tmp_path).read_text())
    assert state['budget_ledger']['reservations'] == []
    assert state['budget_ledger']['settlements'][-1]['outcome'] == 'settled'
    entry = state['specialist_invocations'][-1]
    assert entry['reason_code'] == 'budget-child-timeout'
    with pytest.raises(ProcessLookupError):
        os.killpg(entry['child_pid'], 0)


def test_budgeted_provider_preserves_graceful_sigterm_output(run_cli, tmp_path, prepare_approved_invocation):
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    command = tmp_path / 'commands' / 'provider-command'
    command.write_text(f'#!{sys.executable}\n'
        'import signal,time\n'
        'def done(*_): print("graceful", flush=True); raise SystemExit(0)\n'
        'signal.signal(signal.SIGTERM, done)\nwhile True: time.sleep(.1)\n')
    command.chmod(0o700)
    args, env, _ = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
        iteration=1, phase='planning', env_extra=env)
    _budget(tmp_path)
    result = run_cli(*args, '--timeout', '3', cwd=tmp_path, env_extra=env)
    assert result.returncode == 0
    entry = json.loads(_state_path(tmp_path).read_text())['specialist_invocations'][-1]
    assert entry['exit_code'] == 0 and entry['reason'] == 'budget-child-timeout'


@pytest.mark.parametrize('supervisor_signal', [signal.SIGSTOP, signal.SIGKILL])
def test_budgeted_provider_watchdog_reclaims_stopped_command_after_supervisor_stops(
        run_cli, tmp_path, prepare_approved_invocation, supervisor_signal):
    """The command-provider path retains deadline enforcement after CLI loss."""
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    command = tmp_path / 'commands' / 'provider-command'
    command.write_text(f'#!{sys.executable}\n'
        'import json,os,signal,subprocess,sys,time\nfrom pathlib import Path\n'
        'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
        f'Path({str(marker)!r}).write_text(json.dumps([os.getpgrp(),os.getpid(),child.pid]))\n'
        'os.killpg(os.getpgrp(),signal.SIGSTOP)\ntime.sleep(60)\n')
    command.chmod(0o700)
    args, env, _ = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
        iteration=1, phase='planning', env_extra=env)
    _budget(tmp_path)
    process_env = {key: value for key, value in os.environ.items() if not key.startswith('MISSION_')}
    process_env.update(env)
    process_env.update(MISSION_SESSION_ID='test', MISSION_LEASE_ID='test-lease')
    supervisor = subprocess.Popen([sys.executable, str(Path(__file__).parents[1] / 'bin/mission-state.py'),
        *args, '--timeout', '6'], cwd=tmp_path, env=process_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        _wait(lambda: _pids(marker) is not None)
        pgid, target, grandchild = _pids(marker)
        os.kill(supervisor.pid, supervisor_signal)
        if supervisor_signal == signal.SIGKILL:
            supervisor.wait(timeout=1)
        _wait(lambda: _absent(target) and _absent(grandchild) and _group_absent(pgid), seconds=8)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(supervisor.pid, signal.SIGKILL)
        with contextlib.suppress(Exception):
            supervisor.wait(timeout=1)
        pids = _pids(marker)
        if pids is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pids[0], signal.SIGKILL)


def test_deadline_does_not_restore_the_fractional_second_already_consumed(monkeypatch):
    from mission_application import provider_budget
    from .mission_state_fixture_corpus import issue483_corpus
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 1, 0, 1, 0, 400000, tzinfo=timezone.utc)
    monkeypatch.setattr(provider_budget, 'datetime', Clock)
    monkeypatch.setattr(provider_budget.time, 'monotonic', lambda: 100.0)
    document = issue483_corpus()['v4']
    document.update(loop_active=True, phase='planning', budget_minutes=30,
        budget_ledger=ledger_document(new_ledger(decode_policy(default_policy_document(1800)),
                                                '2026-01-01T00:00:00Z')))
    entry = dict(invocation_id='inv_' + 'a' * 32, operation_id='op:test', fencing_epoch=1,
                 outbound_packet_digest='sha256:' + 'b' * 64)
    budget, refusal = provider_budget.reserve_provider(document, entry, 60, '2026-01-01T00:01:00Z', prepared=True)
    assert refusal is None
    assert budget.deadline == pytest.approx(159.6)


@pytest.fixture
def invoke_here(monkeypatch, tmp_path):
    """Fault injection at the real repository save, without a second CLI process."""
    import importlib.util
    import sys
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('provider_entry_cli', Path(__file__).resolve().parents[1] / 'bin/mission-state.py')
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    def invoke(args, env):
        monkeypatch.chdir(tmp_path)
        monkeypatch.syspath_prepend(str(tmp_path / ".test-provider-preflight"))
        monkeypatch.delitem(sys.modules, "test_approval_provider", raising=False)
        for key in list(os.environ):
            if key.startswith('MISSION_') or key in {'CLAUDE_CODE_SESSION_ID', 'CODEX_THREAD_ID'}:
                monkeypatch.delenv(key)
        for key, value in {**env, 'MISSION_SESSION_ID': 'test', 'MISSION_LEASE_ID': 'test-lease'}.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(sys, 'argv', ['mission-state.py', *args])
        parsed = module._build_parser().parse_args(args)
        return parsed.func(parsed)
    invoke.module = module
    return invoke


def test_reservation_commit_failure_prevents_spawn(run_cli, tmp_path, prepare_approved_invocation,
                                                   invoke_here, monkeypatch):
    from mission_persistence.legacy_v4 import LegacyV4Repository
    import budgeted_exec
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    args, env, _ = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
                                             iteration=1, phase='planning', env_extra=env)
    _budget(tmp_path)
    before = _state_path(tmp_path).read_bytes()
    save = LegacyV4Repository.save
    def fail_reserve(repo, state, **kwargs):
        if state.get('budget_ledger', {}).get('reservations'):
            raise OSError('reservation commit failed')
        return save(repo, state, **kwargs)
    monkeypatch.setattr(LegacyV4Repository, 'save', fail_reserve)
    monkeypatch.setattr(budgeted_exec, 'spawn_exec', lambda *a, **kw: pytest.fail('spawn before durable reservation'))
    with pytest.raises(OSError, match='reservation commit failed'):
        invoke_here(args, env)
    assert not marker.exists() and _state_path(tmp_path).read_bytes() == before


def test_receipt_commit_never_delays_child_deadline(run_cli, tmp_path, prepare_approved_invocation,
                                                  invoke_here, monkeypatch):
    from mission_persistence.legacy_v4 import LegacyV4Repository
    import sys
    import time
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    (tmp_path / 'commands/provider-command').write_text(f'#!{sys.executable}\nimport time\ntime.sleep(60)\n')
    args, env, _ = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
                                             iteration=1, phase='planning', env_extra=env)
    _budget(tmp_path)
    save = LegacyV4Repository.save
    observed = []
    def stalled_receipt(repo, state, **kwargs):
        entry = (state.get('specialist_invocations') or [{}])[-1]
        if entry.get('status') == 'running':
            # Stall the receipt commit past the child deadline; the child must be
            # reaped by its own absolute deadline, not after this commit returns.
            # The bound is deadline + kill grace + slack, so a loaded runner whose
            # second-granular clock eats part of the window does not flake.
            bound = time.monotonic() + 3 + 10
            while time.monotonic() < bound:
                try:
                    os.kill(entry['child_pid'], 0)
                except ProcessLookupError:
                    observed.append(True)
                    break
                time.sleep(.05)
        return save(repo, state, **kwargs)
    monkeypatch.setattr(LegacyV4Repository, 'save', stalled_receipt)
    invoke_here([*args, '--timeout', '3'], env)
    assert observed


def test_budgeted_strict_provider_is_refused_and_legacy_is_unchanged():
    from mission_application.provider_budget import reserve_provider
    from .mission_state_fixture_corpus import issue483_corpus
    document = issue483_corpus()['v4']
    entry = dict(invocation_id='inv_' + 'a' * 32, operation_id='op:test', fencing_epoch=1,
                 outbound_packet_digest='sha256:' + 'b' * 64)
    before = json.dumps(document)
    assert reserve_provider(document, entry, 60, '2026-01-01T00:01:00Z',
                            prepared=True, enforceable=False) == (None, None)
    assert json.dumps(document) == before
    document.update(loop_active=True, phase='planning', budget_minutes=30,
        budget_ledger=ledger_document(new_ledger(decode_policy(default_policy_document(1800)),
                                                '2026-01-01T00:00:00Z')))
    before = json.dumps(document)
    assert reserve_provider(document, entry, 60, '2026-01-01T00:01:00Z',
                            prepared=True, enforceable=False) == (None, 'budget-deadline-unenforceable')
    assert json.dumps(document) == before


def test_epipe_keeps_provider_output_and_exit_in_terminal_record(run_cli, tmp_path, prepare_approved_invocation):
    import sys
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    (tmp_path / 'commands/provider-command').write_text(f'#!{sys.executable}\n'
        'import os,sys\nos.close(0)\nprint("accepted-prefix")\nprint("refused",file=sys.stderr)\nsys.exit(23)\n')
    source = tmp_path / 'large-input.txt'
    source.write_text('x' * (1024 * 1024))
    args, env, _ = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
        iteration=1, phase='planning', env_extra=env, input_file=source)
    _budget(tmp_path)
    result = run_cli(*args, cwd=tmp_path, env_extra=env)
    assert result.returncode == 0, result.stderr
    state = json.loads(_state_path(tmp_path).read_text())
    entry = state['specialist_invocations'][-1]
    assert entry['exit_code'] == 23 and entry['status'] == 'failed'
    assert state['budget_ledger']['reservations'] == []
    assert state['budget_ledger']['settlements'][-1]['telemetry']['output_bytes'] == len(b'accepted-prefix\nrefused\n')
    evidence = (tmp_path / entry['evidence_path']).read_text()
    assert 'accepted-prefix' in evidence and 'refused' in evidence and 'exit_code: 23' in evidence


@pytest.mark.parametrize('fault,reason,held', [
    ('unconfirmed', 'kill-unconfirmed', True),
    ('truncated', 'budget-output-incomplete', False),
    ('incomplete', 'budget-output-incomplete', False),
])
def test_unsafe_exchange_cannot_be_successful_evidence_and_preserves_kill_hold(
        run_cli, tmp_path, prepare_approved_invocation, invoke_here, monkeypatch, fault, reason, held):
    from dataclasses import replace
    from mission_application import provider_process
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    args, env, _ = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
                                             iteration=1, phase='planning', env_extra=env)
    _budget(tmp_path)
    exchange = provider_process.exchange_provider
    def observe_fault(*args, **kwargs):
        observed = exchange(*args, **kwargs)  # real owned child is cleaned before fault injection
        return replace(observed, **{'unconfirmed': {'kill_confirmed': False},
                       'truncated': {'output_truncated': True},
                       'incomplete': {'output_complete': False}}[fault])
    monkeypatch.setattr(provider_process, 'exchange_provider', observe_fault)
    invoke_here(args, env)
    state = json.loads(_state_path(tmp_path).read_text())
    assert state['specialist_invocations'][-1]['status'] == 'failed'
    assert state['specialist_invocations'][-1]['reason_code'] == reason
    assert bool(state['budget_ledger']['reservations']) == held
    assert state['budget_ledger']['settlements'][-1]['outcome'] == ('kill-unconfirmed' if held else 'settled')


def test_no_policy_keeps_original_spawn_and_adds_no_budget_writes(run_cli, tmp_path,
        prepare_approved_invocation, invoke_here, monkeypatch):
    import budgeted_exec
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    args, env, _ = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
                                             iteration=1, phase='planning', env_extra=env)
    monkeypatch.setattr(budgeted_exec, 'spawn_exec', lambda *a, **kw: pytest.fail('budgeted legacy spawn'))
    invoke_here(args, env)
    assert marker.exists()
    assert 'budget_ledger' not in json.loads(_state_path(tmp_path).read_text())


def test_strict_entry_never_calls_in_process_backend_with_budget(run_cli, tmp_path,
        prepare_approved_invocation, invoke_here, monkeypatch, capsys):
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    args, env, prepared = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
        iteration=1, phase='planning', env_extra=env)
    _budget(tmp_path)
    path = _state_path(tmp_path)
    state = json.loads(path.read_text())
    state['provider_preflights'][prepared['preflight_id']]['execution_context'] = {'isolation': 'strict'}
    path.write_text(json.dumps(state))
    module = invoke_here.module
    monkeypatch.setattr(module, '_verified_preflight_packet',
        lambda cwd, data, provider, request, **kw: (data['provider_preflights'][request.preflight_id], b'{}'))
    monkeypatch.setattr(module, '_dispatch_provider_execution', lambda *a, **kw: pytest.fail('unenforceable backend ran'))
    with pytest.raises(SystemExit) as stopped:
        invoke_here(args, env)
    assert stopped.value.code == 2 and 'budget-deadline-unenforceable' in capsys.readouterr().err
    assert not marker.exists()
    assert json.loads(path.read_text())['budget_ledger']['reservations'] == []


@pytest.mark.parametrize('fault', ['input', 'context', 'intent', 'eligibility', 'public-state'])
def test_definitive_pre_spawn_rejection_commits_terminal_and_zero_settlement(
        run_cli, tmp_path, prepare_approved_invocation, invoke_here, monkeypatch, fault):
    from mission_application import command_provider
    from mission_persistence.legacy_v4 import LegacyV4Repository
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    args, env, _ = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
        iteration=1, phase='planning', env_extra=env)
    _budget(tmp_path)
    if fault == 'intent':
        def refuse(*a, **kw):
            import time
            time.sleep(1.1)  # A refusal still owes zero executed seconds.
            raise command_provider.PlanningFailure('dispatch-refused')
        monkeypatch.setattr(command_provider, 'record_dispatch_intent', refuse)
    else:
        name = {'input': '_verified_preflight_packet', 'context': '_require_current_provider_application',
                'eligibility': '_require_current_provider_application',
                'public-state': '_validate_specialist_public_state'}[fault]
        original = getattr(invoke_here.module, name)
        def reject(data_or_cwd, *a, **kw):
            data = a[0] if fault == 'input' else data_or_cwd
            import inspect
            application_call = inspect.currentframe().f_back.f_code.co_name == '_invoke_command_provider'
            if data.get('budget_ledger', {}).get('reservations') and application_call:
                if fault == 'context':
                    return {**original(data_or_cwd, *a, **kw), '_application_context_digest': 'drift'}
                invoke_here.module._provider_gate('payload-drift')
            return original(data_or_cwd, *a, **kw)
        monkeypatch.setattr(invoke_here.module, name, reject)
    saves = []
    original_save = LegacyV4Repository.save
    def observe(repo, data, **kw):
        if data.get('specialist_invocations', [{}])[-1].get('lifecycle_state') == 'terminal':
            saves.append(data['budget_ledger']['settlements'][-1]['charged_sec'])
            assert data['budget_ledger']['reservations'] == []
        return original_save(repo, data, **kw)
    monkeypatch.setattr(LegacyV4Repository, 'save', observe)
    with pytest.raises((SystemExit, ValueError)):
        invoke_here(args, env)
    state = json.loads(_state_path(tmp_path).read_text())
    assert not marker.exists() and state['specialist_invocations'][-1]['status'] == 'rejected'
    assert state['budget_ledger']['reservations'] == [] and saves == [0]


@pytest.mark.parametrize('probe', ['unavailable', 'self-signal', 'dispatch-stall', 'success'])
def test_provider_terminal_probes_release_budget_and_deadline_prevents_late_spawn(
        run_cli, tmp_path, prepare_approved_invocation, invoke_here, monkeypatch, probe):
    import sys
    import time
    import signal
    import budgeted_exec
    from mission_persistence.legacy_v4 import LegacyV4Repository
    from mission_application import command_provider
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    if probe == 'success':
        registry = tmp_path / 'provider-registry.json'
        value = json.loads(registry.read_text())
        value['specialists_v2'][0]['result_contract'] = {'min_non_template_chars': 0}
        registry.write_text(json.dumps(value))
        run_cli('specialists', 'recommend', '--no-default-skill-roots', '--task', 'Review the architecture',
                '--registry', str(registry), '--complexity', 'Complex', '--record-state',
                cwd=tmp_path, check=True, env_extra=env)
    if probe == 'self-signal':
        (tmp_path / 'commands/provider-command').write_text(f'#!{sys.executable}\n'
            'import os,signal\nos.kill(os.getpid(),signal.SIGTERM)\n')
    args, env, _ = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
        iteration=1, phase='planning', env_extra=env)
    _budget(tmp_path)
    if probe == 'unavailable':
        from types import SimpleNamespace
        from mission_application import provider_budget
        clock = [100.0]
        monkeypatch.setattr(provider_budget, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
        def unavailable(_):
            clock[0] += 5  # Preflight overhead is not executed child time.
            return False
        monkeypatch.setattr(invoke_here.module, '_command_is_available', unavailable)
    if probe == 'dispatch-stall':
        original = LegacyV4Repository.save
        def stall(repo, data, **kw):
            result = original(repo, data, **kw)
            if data['specialist_invocations'][-1]['status'] == 'dispatch-unknown':
                time.sleep(1.1)
            return result
        monkeypatch.setattr(LegacyV4Repository, 'save', stall)
        monkeypatch.setattr(budgeted_exec, 'spawn_exec', lambda *a, **kw: pytest.fail('spawn after deadline'))
        args = [*args, '--timeout', '1']
    else:
        spawn = budgeted_exec.spawn_exec
        def inherited(*a, **kw):
            assert kw.get('cwd') is None  # Both provider routes inherit the invocation cwd.
            return spawn(*a, **kw)
        monkeypatch.setattr(budgeted_exec, 'spawn_exec', inherited)
    settled = command_provider.settle_provider
    def capture(*a, **kw):
        if probe == 'success':
            assert kw.get('completed') is True
        return settled(*a, **kw)
    monkeypatch.setattr(command_provider, 'settle_provider', capture)
    invoke_here(args, env)
    state = json.loads(_state_path(tmp_path).read_text())
    entry = state['specialist_invocations'][-1]
    assert entry['lifecycle_state'] == 'terminal' and state['budget_ledger']['reservations'] == []
    if probe == 'self-signal':
        assert entry['exit_code'] == -signal.SIGTERM and entry['status'] == 'failed'
    if probe in ('unavailable', 'dispatch-stall'):
        assert entry['status'] == 'failed-before-start' and not marker.exists()
        assert state['budget_ledger']['settlements'][-1]['charged_sec'] == 0


def test_completed_final_provider_sets_final_run():
    from mission_application import provider_budget as pb
    from mission_kernel.commands import EnterFinalPhase
    from .mission_state_fixture_corpus import issue483_corpus
    document = issue483_corpus()['v4']
    document.update(loop_active=True, phase='reviewing', budget_minutes=30,
        budget_ledger=ledger_document(new_ledger(decode_policy(default_policy_document(1800)),
                                                '2026-01-01T00:00:00Z')))
    assert pb._apply(document, EnterFinalPhase('2026-01-01T00:01:00Z', 'explicit')).accepted
    entry = dict(invocation_id='inv_' + 'a' * 32, operation_id='op:final', fencing_epoch=1,
                 outbound_packet_digest='sha256:' + 'b' * 64)
    budget, refusal = pb.reserve_provider(document, entry, 60, '2026-01-01T00:01:01Z', prepared=True)
    assert refusal is None and budget.reservation.budget_class == 'final'
    pb.settle_provider(document, budget, '2026-01-01T00:01:02Z', 'sha256:' + 'c' * 64, completed=True)
    assert document['budget_ledger']['stop_slots']['final_run']['reservation_id'] == budget.reservation.reservation_id


@pytest.mark.parametrize('status,reason', [('awaiting-approval', 'approval-required'), ('consumed', 'receipt-replayed')])
def test_approval_rejection_precedes_budget_admission(
        run_cli, tmp_path, prepare_approved_invocation, invoke_here, monkeypatch, status, reason):
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    args, env, _ = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
        iteration=1, phase='planning', env_extra=env)
    _budget(tmp_path, expired=True)
    original = invoke_here.module._require_current_provider_application
    def change_pointer(data, *a, **kw):
        result = original(data, *a, **kw)
        if kw.get('invocation_id'):
            next(iter(data['provider_preflights'].values()))['status'] = status
        return result
    monkeypatch.setattr(invoke_here.module, '_require_current_provider_application', change_pointer)
    with pytest.raises(SystemExit) as caught:
        invoke_here(args, env)
    assert caught.value.provider_reason_code == reason
    state = json.loads(_state_path(tmp_path).read_text())
    assert not marker.exists() and state['budget_ledger']['stop_slots']['last_refusal'] is None


def test_process_receipt_commit_failure_retains_unknown_reservation_for_reconcile(
        run_cli, tmp_path, prepare_approved_invocation, invoke_here, monkeypatch):
    from mission_persistence.legacy_v4 import LegacyV4Repository
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    args, env, _ = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
        iteration=1, phase='planning', env_extra=env)
    _budget(tmp_path)
    original = LegacyV4Repository.save
    def fail(repo, data, **kw):
        if data['specialist_invocations'][-1]['status'] == 'running':
            raise OSError('process receipt commit failed')
        return original(repo, data, **kw)
    monkeypatch.setattr(LegacyV4Repository, 'save', fail)
    with pytest.raises(OSError, match='process receipt commit failed'):
        invoke_here(args, env)
    state = json.loads(_state_path(tmp_path).read_text())
    assert marker.exists() and state['specialist_invocations'][-1]['status'] == 'dispatch-unknown'
    assert len(state['budget_ledger']['reservations']) == 1 and not state['budget_ledger']['settlements']


def test_budget_provider_exec_failure_is_refused_and_settled_zero(
        run_cli, tmp_path, prepare_approved_invocation, invoke_here, monkeypatch):
    import budgeted_exec
    marker, env = _prepare_command_provider(run_cli, tmp_path)
    args, env, _ = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
                                             iteration=1, phase='planning', env_extra=env)
    _budget(tmp_path)
    actual = budgeted_exec.spawn_deadline_exec
    missing = str(tmp_path / 'missing-provider-executable')
    def fail(_argv, deadline, **kw):
        return actual([missing], deadline, **kw)
    monkeypatch.setattr(budgeted_exec, 'spawn_deadline_exec', fail)
    invoke_here(args, env)
    state = json.loads(_state_path(tmp_path).read_text())
    assert not marker.exists()
    assert state['specialist_invocations'][-1]['reason_code'] == 'budget-deadline-unenforceable'
    assert state['specialist_invocations'][-1]['reason'] == 'executable not found'
    ledger = state['budget_ledger']
    assert not ledger['reservations'] and ledger['settlements'][-1]['charged_sec'] == 0
    assert ledger['stop_slots']['last_refusal'] == 'budget-deadline-unenforceable'
