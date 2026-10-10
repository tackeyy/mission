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
        monkeypatch.setattr(budgeted_exec.subprocess, 'Popen', unavailable)
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
    changes += [{field: value} for field in ('reservation_id', 'candidate_digest', 'result_digest', 'progress_digest', 'at')
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
    assert failures == 63  # 57 field mutations + 6 missing/malformed settlements
    from mission_kernel.json_codec import freeze_json_value
    for value in (['invalid'], 'invalid', 1, False, None):
        payload = command.compatibility.upserts.thaw()
        payload['force_approval']['request'] = value
        altered = replace(command, compatibility=replace(command.compatibility, upserts=freeze_json_value(payload)))
        result = decide(state, altered)
        assert not result.accepted and result.rejection.code == 'approval-settlement-binding-invalid'
    from mission_kernel.budget_decisions import approval_result_digest
    for value in (None, [], False, 1, 'invalid'):
        payload = command.compatibility.upserts.thaw()
        payload['force_approval']['response'] = value
        altered = replace(command, compatibility=replace(command.compatibility, upserts=freeze_json_value(payload)),
            approval_settlement=replace(settlement, result_digest=approval_result_digest(value)))
        result = decide(state, altered)
        assert not result.accepted and result.rejection.code == 'approval-settlement-binding-invalid'


@pytest.mark.parametrize('fault', ['verifier-error', 'timeout'])
def test_approval_infrastructure_failures_allow_retry_after_verifier_repair(
        approval_entry, tmp_path, invoke_here, monkeypatch, fault):
    import budgeted_exec
    entry, args, env, source = approval_entry
    original = source.read_text()
    read = budgeted_exec.read_frame
    if fault == 'verifier-error':
        _replace_callback(source, env, 'def verify(request):\n raise RuntimeError("unavailable")\n')
    else:
        def timeout(*a, **kw):
            raise TimeoutError('budget-child-timeout')
        monkeypatch.setattr(budgeted_exec, 'read_frame', timeout)
    for _ in range(2):
        with pytest.raises(SystemExit) as rejected:
            invoke_here(args, env)
        assert rejected.value.code == 2
    ledger = json.loads(_state_path(tmp_path).read_text())['budget_ledger']
    assert not ledger['progress']
    assert len(ledger['settlements']) == 2
    _replace_callback(source, env, original)
    monkeypatch.setattr(budgeted_exec, 'read_frame', read)
    invoke_here(args, env)
    state = json.loads(_state_path(tmp_path).read_text())
    assert len(state['budget_ledger']['settlements']) == 3
    assert state['budget_ledger']['progress'][0]['consecutive_count'] == 1
    assert state['passes'] if entry == 'force-approval' else state['provider_preflights'][args[3]]['status'] == 'approved'


@pytest.mark.parametrize('interruption', [KeyboardInterrupt, SystemExit])
def test_approval_interruption_after_spawn_is_charged_and_propagated_after_cleanup(
        approval_entry, tmp_path, invoke_here, monkeypatch, interruption):
    import budgeted_exec
    from mission_application import approval_budget
    from types import SimpleNamespace
    entry, args, env, _ = approval_entry
    error = interruption(78)
    children = []
    spawn = budgeted_exec.spawn_deadline_exec
    def watched(*a, **kw):
        child, control = spawn(*a, **kw)
        children.append(child.pid)
        return child, control
    def interrupt(*a, **kw):
        # Deterministically observe two seconds of elapsed budget time without a sleep.
        observed = time.monotonic() + 2
        monkeypatch.setattr(approval_budget, 'time', SimpleNamespace(monotonic=lambda: observed))
        raise error
    monkeypatch.setattr(budgeted_exec, 'spawn_deadline_exec', watched)
    monkeypatch.setattr(budgeted_exec, 'read_frame', interrupt)
    with pytest.raises(interruption) as raised:
        invoke_here(args, env)
    assert raised.value is error
    assert len(children) == 1
    for pid in children:
        with pytest.raises(ProcessLookupError):
            os.killpg(pid, 0)
    ledger = json.loads(_state_path(tmp_path).read_text())['budget_ledger']
    assert not ledger['reservations']
    assert ledger['settlements'][-1]['observed']['elapsed_sec'] == 2
    assert ledger['settlements'][-1]['charged_sec'] == 2
    assert ledger['stop_slots']['refusal_count'] == 0
    assert ledger['stop_slots']['last_refusal'] is None
    assert not list((tmp_path / '.mission-state/exec-jobs').glob('*.json'))


@pytest.mark.parametrize('failure', ['untrusted', 'gate', 'interrupt-before-spawn'])
def test_approval_preexecution_rejection_does_not_record_deadline_refusal(
        approval_entry, tmp_path, invoke_here, monkeypatch, failure):
    import budgeted_exec
    from pathlib import Path
    entry, args, env, _ = approval_entry
    path = _state_path(tmp_path)
    if failure == 'untrusted':
        config = Path(env['XDG_CONFIG_HOME']) / 'mission/approval-verifiers.json'
        registry = json.loads(config.read_text())
        registry['verifiers'] = []
        config.write_text(json.dumps(registry))
    elif failure == 'gate':
        state = json.loads(path.read_text())
        if entry == 'verify-approval':
            state['provider_preflights'][args[3]]['outbound_packet_digest'] = 'sha256:' + '0' * 64
        else:
            args[args.index('--approval-evidence-ref') + 1] = 'invalid'
        path.write_text(json.dumps(state))
    else:
        def interrupt(*a, **kw):
            from mission_application import approval_budget
            from types import SimpleNamespace
            observed = time.monotonic() + 2
            monkeypatch.setattr(approval_budget, 'time', SimpleNamespace(monotonic=lambda: observed))
            raise KeyboardInterrupt()
        monkeypatch.setattr(budgeted_exec, 'create_job', interrupt)
    monkeypatch.setattr(budgeted_exec, 'spawn_deadline_exec', lambda *a, **kw: pytest.fail('unexpected approval spawn'))
    with pytest.raises(KeyboardInterrupt if failure == 'interrupt-before-spawn' else SystemExit):
        invoke_here(args, env)
    ledger = json.loads(path.read_text())['budget_ledger']
    assert not ledger['reservations']
    assert len(ledger['settlements']) == 1
    assert ledger['settlements'][0]['charged_sec'] == 0
    assert ledger['stop_slots']['refusal_count'] == 0
    assert ledger['stop_slots']['last_refusal'] is None


@pytest.mark.parametrize('approval_entry', ['verify-approval'], indirect=True)
@pytest.mark.parametrize('policy_present', [True, False])
@pytest.mark.parametrize('fault,reason', [
    ('missing-id', 'preflight-not-awaiting-approval'),
    ('wrong-status', 'preflight-not-awaiting-approval'),
    ('missing-digest', 'approval-evidence-invalid'),
])
def test_verify_approval_entry_errors_keep_gate_shape_with_or_without_policy(
        approval_entry, tmp_path, invoke_here, monkeypatch, capsys, policy_present, fault, reason):
    import budgeted_exec
    _, args, env, _ = approval_entry
    path = _state_path(tmp_path)
    state = json.loads(path.read_text())
    pointer = state['provider_preflights'][args[3]]
    if fault == 'missing-id':
        args[3] = 'missing'
    elif fault == 'wrong-status':
        pointer['status'] = 'approved'
    else:
        del pointer['outbound_packet_digest']
    if not policy_present:
        del state['budget_ledger']
    path.write_text(json.dumps(state))
    monkeypatch.setattr(budgeted_exec, 'spawn_deadline_exec', lambda *a, **kw: pytest.fail('unexpected approval spawn'))
    with pytest.raises(SystemExit) as rejected:
        invoke_here(args, env)
    assert rejected.value.code == 2
    assert f'provider-ineligible: {reason}' in capsys.readouterr().err
    assert not json.loads(path.read_text()).get('budget_ledger', {}).get('reservations')


@pytest.mark.parametrize('adapter_limit', [1, 30])
def test_approval_deadline_starts_at_execution_and_retains_reservation_cap(
        approval_entry, tmp_path, invoke_here, monkeypatch, adapter_limit):
    from mission_application import approval_verifier
    from mission_kernel.budget import decode_policy, ledger_document, new_ledger
    from datetime import datetime, timezone
    from types import SimpleNamespace
    entry, args, env, _ = approval_entry
    path = _state_path(tmp_path)
    state = json.loads(path.read_text())
    state['budget_ledger']['policy']['adapter_call_sec'] = adapter_limit
    state['budget_ledger'] = ledger_document(new_ledger(decode_policy(state['budget_ledger']['policy']),
        datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')))
    path.write_text(json.dumps(state))
    execute = invoke_here.module._run_approval_verifier
    job = approval_verifier.run_job
    expected = []
    def preparation(*a, **kw):
        permit = kw['budget'].permit
        # Preparation consumes time before the common execution boundary.
        execution_at = permit.started + 2
        monkeypatch.setattr(approval_verifier, 'time', SimpleNamespace(monotonic=lambda: execution_at))
        expected.append(min(permit.deadline - .2, execution_at + min(5, adapter_limit)))
        return execute(*a, **kw)
    def checked(*a, **kw):
        assert kw['deadline'] == expected[-1]
        return job(*a, **kw)
    monkeypatch.setattr(invoke_here.module, '_run_approval_verifier', preparation)
    monkeypatch.setattr(approval_verifier, 'run_job', checked)
    invoke_here(args, env)
    assert len(expected) == 1


@pytest.mark.parametrize('fault', ['cleanup-interrupt', 'cleanup-error', 'spawn-handoff-interrupt'])
def test_approval_unknown_recovery_keeps_full_charge_and_reservation(
        approval_entry, tmp_path, invoke_here, monkeypatch, fault):
    import budgeted_exec
    _, args, env, source = approval_entry
    _replace_callback(source, env, 'import time\ndef verify(request):\n time.sleep(60)\n return {}\n')
    cleanup, spawn = budgeted_exec.cleanup_group, budgeted_exec.spawn_exec
    children = []
    def owned(*a, **kw):
        child = spawn(*a, **kw)
        children.append(child)
        if fault == 'spawn-handoff-interrupt':
            raise KeyboardInterrupt()
        return child
    def interrupted_cleanup(*a, **kw):
        if fault == 'cleanup-error':
            raise OSError('cleanup observation failed')
        raise KeyboardInterrupt()
    monkeypatch.setattr(budgeted_exec, 'spawn_exec', owned)
    if fault != 'spawn-handoff-interrupt':
        monkeypatch.setattr(budgeted_exec, 'cleanup_group', interrupted_cleanup)
        monkeypatch.setattr(budgeted_exec, 'read_frame', lambda *a, **kw: (_ for _ in ()).throw(TimeoutError()))
    try:
        with pytest.raises(SystemExit if fault == 'cleanup-error' else KeyboardInterrupt) as rejected:
            invoke_here(args, env)
        if fault != 'cleanup-error':
            assert rejected.value.exec_unstarted is False
            assert rejected.value.exec_cleanup_confirmed is False
        ledger = json.loads(_state_path(tmp_path).read_text())['budget_ledger']
        assert len(children) == 1 and len(ledger['reservations']) == 1
        assert ledger['settlements'][-1]['outcome'] == 'kill-unconfirmed'
        assert ledger['settlements'][-1]['charged_sec'] == ledger['reservations'][0]['reserved_sec']
    finally:
        for child in children:
            assert cleanup(child, term_grace=.2, kill_wait=2)


def test_identical_invalid_approval_results_stop_repetition_despite_fresh_nonce(
        approval_entry, tmp_path, invoke_here, monkeypatch, capsys):
    from mission_application import approval_verifier
    entry, args, env, source = approval_entry
    text = source.read_text()
    if entry == 'verify-approval':
        text = text.replace("'single_use_nonce':nonce", "'single_use_nonce':'bad'")
    else:
        text = text.replace("'decision':'approved'", "'decision':'rejected'")
    _replace_callback(source, env, text)
    job, results = approval_verifier.run_job, []
    def observed(*a, **kw):
        result = job(*a, **kw)
        results.append(result)
        return result
    monkeypatch.setattr(approval_verifier, 'run_job', observed)
    for _ in range(2):
        with pytest.raises(SystemExit) as rejected:
            invoke_here(args, env)
        assert rejected.value.code == 2
    ledger = json.loads(_state_path(tmp_path).read_text())['budget_ledger']
    assert ledger['progress'][0]['consecutive_count'] == 2
    if entry == 'force-approval':
        assert results[0]['request_digest'] != results[1]['request_digest']
    monkeypatch.setattr(approval_verifier, 'run_job', lambda *a, **kw: pytest.fail('stalled approval spawned'))
    with pytest.raises(SystemExit) as rejected:
        invoke_here(args, env)
    assert rejected.value.code == 2
    assert 'budget-no-new-evidence' in capsys.readouterr().err
    assert len(json.loads(_state_path(tmp_path).read_text())['budget_ledger']['settlements']) == 2


def test_approval_pipe_failure_is_unstarted_zero_charge_and_refusal(
        approval_entry, tmp_path, invoke_here, monkeypatch):
    import budgeted_exec
    import errno
    from mission_application import approval_budget
    from types import SimpleNamespace
    _, args, env, _ = approval_entry
    def unavailable():
        observed = time.monotonic() + 2
        monkeypatch.setattr(approval_budget, 'time', SimpleNamespace(monotonic=lambda: observed))
        raise OSError(errno.EMFILE, 'pipe unavailable')
    monkeypatch.setattr(budgeted_exec.os, 'pipe', unavailable)
    with pytest.raises(SystemExit) as rejected:
        invoke_here(args, env)
    assert rejected.value.code == 2
    ledger = json.loads(_state_path(tmp_path).read_text())['budget_ledger']
    assert not ledger['reservations']
    assert ledger['settlements'][-1]['charged_sec'] == 0
    assert ledger['stop_slots']['last_refusal'] == 'budget-deadline-unenforceable'


def test_policy_force_pass_requires_settlement_even_without_open_dispatch():
    from mission_kernel.commands import MarkPass
    from mission_kernel.transitions import decide
    from .test_issue919_budget_decisions import _state
    state = _state()
    result = decide(state, MarkPass(force=True, force_approval_verified=True,
                                   at='2026-01-01T00:00:01Z'))
    assert not result.accepted
    assert result.rejection.code == 'approval-settlement-binding-invalid'


@pytest.mark.parametrize('approval_entry', ['force-approval'], indirect=True)
def test_force_admission_with_an_open_dispatch_refuses_before_spawn(
        approval_entry, tmp_path, invoke_here, monkeypatch, capsys):
    from mission_application.approval_budget import now
    from mission_application.provider_budget import _apply
    from mission_kernel.commands import ReserveDispatchBudget
    import budgeted_exec
    _, args, env, _ = approval_entry
    path = _state_path(tmp_path)
    document = json.loads(path.read_text())
    decision = _apply(document, ReserveDispatchBudget(now(), 'verification-run', 'AC1',
        'op:unsettled', 1, 5, 65536, 'sha256:' + 'c' * 64))
    assert decision.accepted
    path.write_text(json.dumps(document))
    monkeypatch.setattr(budgeted_exec, 'spawn_exec', lambda *a, **kw: pytest.fail('force spawned with unsettled work'))
    with pytest.raises(SystemExit) as rejected:
        invoke_here(args, env)
    assert rejected.value.code == 2
    assert 'budget-dispatch-unsettled' in capsys.readouterr().err
    assert json.loads(path.read_text())['budget_ledger'] == document['budget_ledger']


def test_budget_callable_refusal_is_recorded_without_spawning(
        approval_entry, tmp_path, invoke_here, monkeypatch):
    from mission_application import approval_verifier
    _, args, env, _ = approval_entry
    def callable_verifier(request):
        pytest.fail('budgeted callable executed')
    execute = invoke_here.module._run_approval_verifier
    monkeypatch.setattr(invoke_here.module, '_run_approval_verifier',
        lambda descriptor, request, **kw: execute(callable_verifier, request, **kw))
    monkeypatch.setattr(approval_verifier, 'run_callable', lambda *a, **kw: pytest.fail('budgeted callable spawned'))
    with pytest.raises(SystemExit) as rejected:
        invoke_here(args, env)
    assert rejected.value.code == 2
    ledger = json.loads(_state_path(tmp_path).read_text())['budget_ledger']
    assert not ledger['reservations'] and ledger['settlements'][-1]['charged_sec'] == 0
    assert ledger['stop_slots']['last_refusal'] == 'budget-deadline-unenforceable'


@pytest.mark.parametrize('approval_entry', ['force-approval'], indirect=True)
def test_registered_budget_callable_refusal_is_recorded_before_execution(
        approval_entry, tmp_path, invoke_here, monkeypatch):
    _, args, env, _ = approval_entry
    verifier_id = args[args.index('--approval-verifier') + 1]
    monkeypatch.setitem(invoke_here.module._APPROVAL_VERIFIERS, verifier_id,
        lambda request: pytest.fail('registered budget callable executed'))
    with pytest.raises(SystemExit) as rejected:
        invoke_here(args, env)
    assert rejected.value.code == 2
    ledger = json.loads(_state_path(tmp_path).read_text())['budget_ledger']
    assert not ledger['reservations'] and ledger['settlements'][-1]['charged_sec'] == 0
    assert ledger['stop_slots']['last_refusal'] == 'budget-deadline-unenforceable'


def test_approval_progress_ignores_freshness_but_preserves_result_content():
    from mission_kernel.budget_decisions import approval_progress_digest, approval_result_digest
    base = {'decision': 'rejected', 'verifier_id': 'neutral', 'finding': 'invalid-proof'}
    signature = approval_progress_digest(base)
    for index in range(50):
        response = {**base, **{key: str(index) for key in ('event_nonce', 'single_use_nonce',
            'request_digest', 'receipt_ref', 'verified_at', 'expires_at')}}
        assert approval_progress_digest(response) == signature
        assert approval_result_digest(response) != approval_result_digest(base)
        assert approval_progress_digest({**response, 'finding': str(index)}) != signature
