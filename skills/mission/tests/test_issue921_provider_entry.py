"""Provider admission contracts at the existing public CLI boundary."""
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from mission_kernel.budget import decode_policy, default_policy_document, ledger_document, new_ledger
from .test_provider_application_guard import _prepare_command_provider, _state_path


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
            time.sleep(1.2)
            with pytest.raises(ProcessLookupError):
                os.kill(entry['child_pid'], 0)
            observed.append(True)
        return save(repo, state, **kwargs)
    monkeypatch.setattr(LegacyV4Repository, 'save', stalled_receipt)
    invoke_here([*args, '--timeout', '1'], env)
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
