"""Approval admission detects unreserved execution and uncollected child groups."""
import hashlib
import json
import os
import time

import pytest

from .test_issue921_provider_entry import _budget, invoke_here
from .test_provider_application_guard import _prepare_command_provider, _state_path
from .test_issue879_completion_cli import _force_provider


@pytest.fixture
def run_cli(legacy_run_cli):
    return legacy_run_cli


@pytest.fixture(params=['verify-approval', 'force-approval'])
def approval_entry(request, run_cli, tmp_path, prepare_approved_invocation, isolated_provider_python, monkeypatch):
    if request.param == 'verify-approval':
        _, env = _prepare_command_provider(run_cli, tmp_path)
        _, env, prepared = prepare_approved_invocation(cwd=tmp_path, provider='guarded-command-provider',
                                                     iteration=1, phase='planning', env_extra=env)
        path = _state_path(tmp_path)
        state = json.loads(path.read_text())
        state['provider_preflights'][prepared['preflight_id']]['status'] = 'awaiting-approval'
        path.write_text(json.dumps(state))
        args = ['specialists', 'verify-approval', '--preflight-id', prepared['preflight_id'],
                '--evidence-ref', 'sha256:' + 'e' * 64, '--approval-verifier', 'test-verifier']
        source = tmp_path / '.test-provider-preflight/test_approval_provider.py'
    else:
        run_cli('init', 'approval budget', cwd=tmp_path, check=True)
        (tmp_path / '.mission-state/archive').mkdir(exist_ok=True)
        args, env = _force_provider(tmp_path)
        isolated_provider_python(tmp_path / 'provider')
        source = tmp_path / 'provider/completion_provider.py'
    monkeypatch.syspath_prepend(str(source.parent))
    _budget(tmp_path)
    if request.param == 'force-approval':
        path = _state_path(tmp_path)
        state = json.loads(path.read_text())
        state['artifact_applicability'] = 'not-applicable'
        path.write_text(json.dumps(state))
    return request.param, args, env, source


def test_approval_reservation_commit_failure_never_spawns(approval_entry, tmp_path, invoke_here, monkeypatch):
    from mission_persistence.legacy_v4 import LegacyV4Repository
    entry, args, env, _ = approval_entry
    before = _state_path(tmp_path).read_bytes()
    actual = LegacyV4Repository.save
    def fail(repo, state, **kwargs):
        if state.get('budget_ledger', {}).get('reservations'):
            raise OSError('reservation commit failed')
        return actual(repo, state, **kwargs)
    monkeypatch.setattr(LegacyV4Repository, 'save', fail)
    monkeypatch.setattr(invoke_here.module, '_run_approval_verifier',
                        lambda *a, **kw: pytest.fail('unreserved approval spawn'))
    with pytest.raises(OSError, match='reservation commit failed'):
        invoke_here(args, env)
    assert _state_path(tmp_path).read_bytes() == before


def test_approval_child_sees_reservation_and_terminal_settles_after_cleanup(
        approval_entry, tmp_path, invoke_here, monkeypatch):
    from mission_persistence.legacy_v4 import LegacyV4Repository
    import budgeted_exec
    entry, args, env, source = approval_entry
    # The actual isolated callback must observe a durable reservation on disk.
    source.write_text(source.read_text().replace('def verify(request):',
        'def verify(request):\n state=json.load(open(".mission-state/sessions/test.json"))\n'
        ' assert state["budget_ledger"]["reservations"][0]["entry"] == ' + repr(entry)))
    # Provider approval fixture does not otherwise import json.
    source.write_text('import json\n' + source.read_text())
    config = next((tmp_path / env['XDG_CONFIG_HOME']).glob('mission/approval-verifiers.json'))
    registry = json.loads(config.read_text())
    registry['verifiers'][0]['source_digest'] = 'sha256:' + hashlib.sha256(source.read_bytes()).hexdigest()
    config.write_text(json.dumps(registry))
    children, terminals = [], []
    spawn, save = budgeted_exec.spawn_deadline_exec, LegacyV4Repository.save
    def watched(argv, deadline, **kwargs):
        child, control = spawn(argv, deadline, **kwargs)
        children.append(child.pid)
        return child, control
    def observed(repo, state, **kwargs):
        if state.get('budget_ledger', {}).get('settlements'):
            for pid in children:
                with pytest.raises(ProcessLookupError):
                    os.killpg(pid, 0)
            terminals.append(json.loads(json.dumps(state)))
        return save(repo, state, **kwargs)
    monkeypatch.setattr(budgeted_exec, 'spawn_deadline_exec', watched)
    monkeypatch.setattr(LegacyV4Repository, 'save', observed)
    invoke_here(args, env)
    state = json.loads(_state_path(tmp_path).read_text())
    assert len(children) == 1 and not state['budget_ledger']['reservations']
    assert state['budget_ledger']['settlements'][-1]['outcome'] == 'settled'
    assert terminals
    terminal = terminals[-1]
    if entry == 'force-approval':
        assert terminal['passes'] is True and terminal['force_approval']['consumed'] is True
    else:
        assert terminal['provider_preflights'][args[3]]['status'] == 'approved'
    assert not list((tmp_path / '.mission-state/exec-jobs').glob('*.json'))


def _replace_callback(source, env, text):
    from pathlib import Path
    source.write_text(text)
    config = Path(env['XDG_CONFIG_HOME']) / 'mission/approval-verifiers.json'
    registry = json.loads(config.read_text())
    registry['verifiers'][0]['source_digest'] = 'sha256:' + hashlib.sha256(source.read_bytes()).hexdigest()
    config.write_text(json.dumps(registry))


def test_approval_deadline_sweeps_group_before_rejection_settlement(
        approval_entry, tmp_path, invoke_here, monkeypatch):
    import budgeted_exec
    from mission_kernel.budget import decode_policy, ledger_document, new_ledger
    from datetime import datetime, timezone
    entry, args, env, source = approval_entry
    _replace_callback(source, env, 'import time\ndef verify(request):\n time.sleep(60)\n return {}\n')
    path = _state_path(tmp_path)
    state = json.loads(path.read_text())
    policy = state['budget_ledger']['policy']
    policy['adapter_call_sec'] = 1
    state['budget_ledger'] = ledger_document(new_ledger(decode_policy(policy),
        datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')))
    path.write_text(json.dumps(state))
    children = []
    spawn = budgeted_exec.spawn_deadline_exec
    def watched(argv, deadline, **kwargs):
        child, control = spawn(argv, deadline, **kwargs)
        children.append(child.pid)
        return child, control
    monkeypatch.setattr(budgeted_exec, 'spawn_deadline_exec', watched)
    started = time.monotonic()
    with pytest.raises(SystemExit) as rejected:
        invoke_here(args, env)
    assert rejected.value.code == 2
    assert time.monotonic() - started < 3
    assert len(children) == 1
    for pid in children:
        with pytest.raises(ProcessLookupError):
            os.killpg(pid, 0)
    state = json.loads(path.read_text())
    assert not state['budget_ledger']['reservations']
    assert state['budget_ledger']['settlements'][-1]['outcome'] == 'settled'
    assert state['passes'] is not True
    assert not list((tmp_path / '.mission-state/exec-jobs').glob('*.json'))


@pytest.mark.parametrize('fault', ['expired', 'spawn-error', 'kill-unconfirmed', 'invalid-result', 'no-policy'])
def test_approval_refusal_failure_and_inert_paths(approval_entry, tmp_path, invoke_here, monkeypatch, fault):
    import budgeted_exec
    entry, args, env, source = approval_entry
    path = _state_path(tmp_path)
    if fault == 'expired':
        _budget(tmp_path, expired=True)
        monkeypatch.setattr(budgeted_exec, 'spawn_exec', lambda *a, **kw: pytest.fail('refused spawn'))
    elif fault == 'no-policy':
        state = json.loads(path.read_text())
        del state['budget_ledger']
        path.write_text(json.dumps(state))
    elif fault == 'spawn-error':
        def unavailable(*a, **kw):
            raise OSError('exec unavailable')
        monkeypatch.setattr(budgeted_exec, 'spawn_exec', unavailable)
    elif fault == 'kill-unconfirmed':
        cleanup = budgeted_exec.cleanup_group
        def unconfirmed(*a, **kw):
            assert cleanup(*a, **kw)
            return False
        monkeypatch.setattr(budgeted_exec, 'cleanup_group', unconfirmed)
    else:
        _replace_callback(source, env, 'def verify(request):\n return {}\n')
    if fault == 'no-policy':
        invoke_here(args, env)
        assert 'budget_ledger' not in json.loads(path.read_text())
        return
    with pytest.raises((SystemExit, ValueError)):
        invoke_here(args, env)
    state = json.loads(path.read_text())
    ledger = state['budget_ledger']
    assert state['passes'] is not True
    if fault == 'expired':
        assert not ledger['reservations'] and not ledger['settlements']
    else:
        assert bool(ledger['reservations']) == (fault == 'kill-unconfirmed')
        settlement = ledger['settlements'][-1]
        assert settlement['outcome'] == ('kill-unconfirmed' if fault == 'kill-unconfirmed' else 'settled')
        if fault == 'spawn-error':
            assert settlement['charged_sec'] == 0
        if entry == 'verify-approval':
            assert state['provider_preflights'][args[3]]['status'] == 'awaiting-approval'
    assert not list((tmp_path / '.mission-state/exec-jobs').glob('*.json'))


@pytest.mark.parametrize('value', ['invalid', {}, 1, False])
def test_force_settlement_type_confusion_is_rejected(value):
    from mission_kernel.commands import MarkPass
    from mission_kernel.transitions import decide
    from .test_issue919_budget_decisions import _state, _reserve
    state = _state()
    held = decide(state, _reserve('2026-01-01T00:00:01Z', 'force-approval', 'force-pass', 'op:force'))
    assert held.accepted
    decision = decide(held.transition.new_state, MarkPass(force=True, approval_settlement=value))
    assert not decision.accepted and decision.rejection.code == 'approval-settlement-binding-invalid'


def test_approval_crash_reconcile_charges_reservation_and_removes_job(
        approval_entry, run_cli, tmp_path, invoke_here, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from mission_application import approval_budget as budget_module
    from mission_persistence.spawn_jobs import create_job, session_token
    entry = approval_entry[0]
    target = approval_entry[1][3] if entry == 'verify-approval' else 'force-pass'
    path = _state_path(tmp_path)
    document = json.loads(path.read_text())
    at = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    document['budget_ledger']['clock'].update(opened_at=at, last_observed_at=at)
    path.write_text(json.dumps(document))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('MISSION_SESSION_ID', 'test')
    monkeypatch.setenv('MISSION_LEASE_ID', 'test-lease')
    monkeypatch.setattr(budget_module, 'now', lambda: at)
    repository = invoke_here.module._legacy_lifecycle_repository(tmp_path, path, stamp=True, strict_read=True)
    budget = budget_module.admit_approval(repository, entry, target)
    token = budget.permit.reservation.reservation_id.replace(':', '_')
    directory = tmp_path / '.mission-state/exec-jobs'
    job, _ = create_job(directory, b'{}', reservation_id=token, session_id='test')
    stale = directory / f'job-{os.getpid()}-1-s{session_token("test")}-r{token}-{"a" * 32}.json'
    job.rename(stale)
    result = run_cli('budget', 'reconcile', cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    ledger = json.loads(path.read_text())['budget_ledger']
    assert not ledger['reservations']
    assert ledger['settlements'][-1]['outcome'] == 'charged-full-unknown'
    assert ledger['settlements'][-1]['charged_sec'] >= budget.permit.reservation.reserved_sec
    assert not stale.exists()


def test_approval_budget_works_in_fenced_container(approval_entry, tmp_path, invoke_here):
    from .test_issue879_completion_cli import _persist_fixture
    from mission_persistence.fenced_commit import LocalFencedRepository
    _, args, env, _ = approval_entry
    path = _state_path(tmp_path)
    _persist_fixture(tmp_path, json.loads(path.read_text()), 5)
    env = {**env, 'MISSION_OPERATION_ID': 'approval-budget:container'}
    invoke_here(args, env)
    stored = json.loads(LocalFencedRepository(tmp_path / '.mission-state').read('test').state_bytes)
    assert not stored['budget_ledger']['reservations']
    assert stored['budget_ledger']['settlements'][-1]['outcome'] == 'settled'


@pytest.mark.parametrize('approval_entry', ['verify-approval'], indirect=True)
def test_fenced_provider_approval_retry_does_not_reserve_or_spawn_again(
        approval_entry, tmp_path, invoke_here, monkeypatch):
    import budgeted_exec
    from .test_issue879_completion_cli import _persist_fixture
    from mission_persistence.fenced_commit import LocalFencedRepository
    _, args, env, _ = approval_entry
    path = _state_path(tmp_path)
    _persist_fixture(tmp_path, json.loads(path.read_text()), 5)
    env = {**env, 'MISSION_OPERATION_ID': 'approval-budget:retry'}
    invoke_here(args, env)
    before = LocalFencedRepository(tmp_path / '.mission-state').read('test').state_bytes
    monkeypatch.setattr(budgeted_exec, 'spawn_exec', lambda *a, **kw: pytest.fail('replayed approval spawned'))
    invoke_here(args, env)
    assert LocalFencedRepository(tmp_path / '.mission-state').read('test').state_bytes == before


@pytest.mark.parametrize('approval_entry', ['force-approval'], indirect=True)
def test_force_settlement_is_bound_to_pass_envelope_and_dispatch(
        approval_entry, tmp_path, invoke_here, monkeypatch):
    from dataclasses import replace
    from mission_kernel.commands import MarkPass
    from mission_kernel.transitions import decide
    from mission_persistence.legacy_v4 import LegacyV4Repository, _legacy_command_state
    captured = []
    execute = LegacyV4Repository.execute
    def watched(repository, command, **kwargs):
        if isinstance(command, MarkPass):
            captured.append((_legacy_command_state(repository._loaded_document, command), command))
        return execute(repository, command, **kwargs)
    monkeypatch.setattr(LegacyV4Repository, 'execute', watched)
    invoke_here(approval_entry[1], approval_entry[2])
    state, command = captured[0]
    assert decide(state, command).accepted
    settlement = command.approval_settlement
    # One pure input table protects distinct authority/binding dimensions; no
    # repeated isolated subprocess for each forged settlement.
    changes = [
        {'reservation_id': 'reservation:foreign'}, {'result_digest': 'sha256:' + 'f' * 64},
        {'candidate_digest': 'sha256:' + 'f' * 64}, {'at': '2000-01-01T00:00:00Z'},
        {'completed': False}, {'completed': 1}, {'outcome': 'kill-unconfirmed'},
        {'outcome': 'charged-full-unknown'}, {'refusal_reason': 'budget-deadline'},
        {'tool_calls': 0}, {'replays': 0}, {'output_bytes': 0},
    ]
    changes += [{field: value} for field in ('reservation_id', 'candidate_digest', 'result_digest', 'at')
                for value in (None, {}, [], 1, False, '', 'invalid', 'sha256:' + '0' * 63, 'sha256:' + 'z' * 64)]
    failures = 0
    for change in changes:
        result = decide(state, replace(command, approval_settlement=replace(settlement, **change)))
        assert not result.accepted, change
        assert result.rejection.code == 'approval-settlement-binding-invalid', (change, result.rejection)
        failures += 1
    for value in (None, {}, [], 'invalid', 1, False):
        result = decide(state, replace(command, approval_settlement=value))
        expected = 'budget-dispatch-unsettled' if value is None else 'approval-settlement-binding-invalid'
        assert not result.accepted and result.rejection.code == expected
        failures += 1
    foreign = replace(state.budget.reservations[0], entry='verification-run', target='AC1')
    result = decide(replace(state, budget=replace(state.budget, reservations=(foreign,))), command)
    assert not result.accepted and result.rejection.code == 'approval-settlement-binding-invalid'
    assert failures == 54  # 48 field mutations + 6 missing/malformed settlements
    from mission_kernel.json_codec import freeze_json_value
    for value in (['invalid'], 'invalid', 1, False, None):
        payload = command.compatibility.upserts.thaw()
        payload['force_approval']['request'] = value
        altered = replace(command, compatibility=replace(command.compatibility, upserts=freeze_json_value(payload)))
        result = decide(state, altered)
        assert not result.accepted and result.rejection.code == 'approval-settlement-binding-invalid'
