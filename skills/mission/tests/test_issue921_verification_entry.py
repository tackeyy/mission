"""Budgeted verification must reserve before even candidate inventory spawns."""
import json
import pytest
from .test_issue878_verification_runner import _prepare_public_runner, _replay_policy
from .test_issue921_provider_entry import _budget, invoke_here
from .test_provider_application_guard import _state_path


@pytest.fixture
def run_cli(legacy_run_cli):
    return legacy_run_cli


def test_verification_reservation_commit_failure_never_spawns(run_cli, tmp_path, invoke_here, monkeypatch):
    from mission_persistence.legacy_v4 import LegacyV4Repository
    from mission_application import verification_execution as execution
    _prepare_public_runner(tmp_path, run_cli)
    _budget(tmp_path)
    before = _state_path(tmp_path).read_bytes()
    original = LegacyV4Repository.save
    def fail(repo, state, **kw):
        if state.get('budget_ledger', {}).get('reservations'):
            raise OSError('reservation commit failed')
        return original(repo, state, **kw)
    monkeypatch.setattr(LegacyV4Repository, 'save', fail)
    monkeypatch.setattr(execution, 'run_contract_verifier', lambda *a, **kw: pytest.fail('unreserved spawn'))
    with pytest.raises(OSError, match='reservation commit failed'):
        invoke_here(['verification', 'run', '--criterion', 'AC1'], {})
    assert _state_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize('replay', [False, True])
def test_budget_verification_publishes_receipt_and_settlement_in_same_commit(
        run_cli, tmp_path, invoke_here, monkeypatch, replay, capsys):
    from mission_persistence.legacy_v4 import LegacyV4Repository
    _prepare_public_runner(tmp_path, run_cli, policy=_replay_policy() if replay else None)
    _budget(tmp_path)
    original = LegacyV4Repository.save
    commits = []
    def observe(repo, document, **kw):
        if document.get('verification_receipts'):
            assert document['budget_ledger']['reservations'] == []
            assert document['budget_ledger']['settlements'][-1]['outcome'] == 'settled'
            commits.append(document['verification_receipts'][-1])
        return original(repo, document, **kw)
    monkeypatch.setattr(LegacyV4Repository, 'save', observe)
    args = ['verification', 'run', '--criterion', 'AC1']
    if replay:
        path = tmp_path / 'input.json'
        path.write_text(json.dumps({'artifact_kind': 'counterexample', 'content': 'proof'}))
        args += ['--repro-input', str(path)]
    invoke_here(args, {})
    assert commits and commits[-1]['exit_code'] == 0 and commits[-1]['status'] == 'passed'
    assert bool(commits[-1]['repro_input_digest']) == replay


def test_budget_verification_deadline_retains_output_exit_and_cleans_group(run_cli, tmp_path, invoke_here, monkeypatch):
    import hashlib
    import os
    import signal
    import time
    import budgeted_exec
    from .test_issue878_verification_runner import _policy
    policy = _policy()
    policy['commands'][0].update(argv=[policy['commands'][0]['argv'][0], '-c',
        'import time; print("prefix",flush=True); time.sleep(60)'], timeout_sec=3)
    _prepare_public_runner(tmp_path, run_cli, policy=policy)
    _budget(tmp_path)
    spawn, children = budgeted_exec.spawn_exec, []
    def guarded(*a, **kw):
        assert len(json.loads(_state_path(tmp_path).read_text())['budget_ledger']['reservations']) == 1
        child = spawn(*a, **kw)
        children.append(child.pid)
        return child
    monkeypatch.setattr(budgeted_exec, 'spawn_exec', guarded)
    started = time.monotonic()
    invoke_here(['verification', 'run', '--criterion', 'AC1'], {})
    assert time.monotonic() - started < 20  # child + reserved post-run and group cleanup
    state = json.loads(_state_path(tmp_path).read_text())
    receipt = state['verification_receipts'][-1]
    assert receipt['timed_out'] and receipt['exit_code'] == -signal.SIGKILL
    assert receipt['block_reason'] == 'timeout'
    assert receipt['output_digest'] == 'sha256:' + hashlib.sha256(b'prefix\n').hexdigest()
    assert receipt['observed_output_bytes'] == 7
    assert not state['budget_ledger']['reservations']
    for pid in children:
        with pytest.raises(ProcessLookupError):
            os.killpg(pid, 0)
    assert not list((tmp_path / '.mission-state/exec-jobs').glob('*.json'))


def test_budget_verification_run_job_uses_deadline_spawn(run_cli, tmp_path, invoke_here, monkeypatch):
    import budgeted_exec
    _prepare_public_runner(tmp_path, run_cli)
    _budget(tmp_path)
    calls = []
    actual = budgeted_exec.spawn_deadline_exec
    def watched(argv, deadline, **kwargs):
        calls.append((argv, deadline))
        return actual(argv, deadline, **kwargs)
    monkeypatch.setattr(budgeted_exec, 'spawn_deadline_exec', watched)
    invoke_here(['verification', 'run', '--criterion', 'AC1'], {})
    assert len(calls) == 1 and 'spawn_trampoline.py' in calls[0][0][2]


def test_budget_reconcile_charges_crash_reservation_then_deletes_dead_owner_job(run_cli, tmp_path):
    from datetime import datetime, timedelta, timezone
    from mission_application.verification_budget import reserve_verification
    from mission_persistence.spawn_jobs import create_job, session_token
    import os
    _prepare_public_runner(tmp_path, run_cli)
    _budget(tmp_path)
    path = _state_path(tmp_path)
    document = json.loads(path.read_text())
    at = (datetime.now(timezone.utc) - timedelta(seconds=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    document['budget_ledger']['clock']['opened_at'] = at
    document['budget_ledger']['clock']['last_observed_at'] = at
    budget, refusal = reserve_verification(document, 'AC1', 3, at, 'sha256:' + 'a' * 64)
    assert refusal is None
    path.write_text(json.dumps(document))
    directory = tmp_path / '.mission-state/exec-jobs'
    job, _ = create_job(directory, b'{}', reservation_id=budget.reservation.reservation_id.replace(':', '_'), session_id=path.stem)
    # Current PID with an obsolete start identity models PID reuse without
    # creating or signalling an unrelated process.
    token = budget.reservation.reservation_id.replace(':', '_')
    stale = directory / f'job-{os.getpid()}-1-s{session_token(path.stem)}-r{token}-'
    stale = directory / (stale.name + 'a' * 32 + '.json')
    job.rename(stale)
    result = run_cli('budget', 'reconcile', cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    stored = json.loads(path.read_text())
    assert not stored['budget_ledger']['reservations']
    settlement = stored['budget_ledger']['settlements'][-1]
    assert settlement['outcome'] == 'charged-full-unknown'
    assert settlement['charged_sec'] >= budget.reservation.reserved_sec
    assert not stale.exists()


@pytest.mark.parametrize('fault', ['expired', 'no-policy', 'capture-stall', 'spawn-error', 'kill-unconfirmed'])
def test_verification_refusal_legacy_and_supervisor_failures(
        run_cli, tmp_path, invoke_here, monkeypatch, fault):
    import budgeted_exec
    import time
    from .test_issue878_verification_runner import _policy
    from mission_application import verification_budget as budget_module
    policy = _policy()
    policy['commands'][0]['timeout_sec'] = 2
    _prepare_public_runner(tmp_path, run_cli, policy=policy)
    if fault != 'no-policy':
        _budget(tmp_path, expired=fault == 'expired')
    if fault in {'expired', 'no-policy'}:
        monkeypatch.setattr(budgeted_exec, 'spawn_exec', lambda *a, **kw: pytest.fail('forbidden supervisor'))
    elif fault == 'capture-stall':
        # Replace git in the supervisor's inherited PATH. Candidate inventory is
        # inside the deadline, including a child holding captured pipe FDs.
        import os
        bin_dir = tmp_path / 'tools'
        bin_dir.mkdir()
        git = bin_dir / 'git'
        git.write_text('#!/bin/sh\nexec /bin/sleep 60\n')
        git.chmod(0o755)
        monkeypatch.setenv('PATH', str(bin_dir) + os.pathsep + os.environ['PATH'])
    elif fault == 'spawn-error':
        def fail(*a, **kw):
            raise OSError('spawn unavailable')
        monkeypatch.setattr(budgeted_exec, 'spawn_exec', fail)
    else:
        cleanup = budgeted_exec.cleanup_group
        def unconfirmed(*a, **kw):
            assert cleanup(*a, **kw)  # Clean our actual child before injecting uncertainty.
            return False
        monkeypatch.setattr(budgeted_exec, 'cleanup_group', unconfirmed)
    started = time.monotonic()
    args = ['verification', 'run', '--criterion', 'AC1']
    if fault == 'expired':
        with pytest.raises(SystemExit) as rejected:
            invoke_here(args, {})
        assert rejected.value.code == 2
    else:
        invoke_here(args, {})
    assert time.monotonic() - started < 20  # capture stall includes the reserved post-run allowance
    state = json.loads(_state_path(tmp_path).read_text())
    if fault == 'no-policy':
        assert 'budget_ledger' not in state and state['verification_receipts'][-1]['status'] == 'passed'
    elif fault == 'expired':
        assert not state['budget_ledger']['reservations'] and not state.get('verification_receipts')
    else:
        receipt = state['verification_receipts'][-1]
        assert receipt['status'] == 'blocked'
        assert receipt['block_reason'] == {'capture-stall': 'kill-unconfirmed',
            'spawn-error': 'budget-deadline-unenforceable', 'kill-unconfirmed': 'kill-unconfirmed'}[fault]
        if fault == 'spawn-error':
            assert state['budget_ledger']['settlements'][-1]['charged_sec'] == 0
        assert bool(state['budget_ledger']['reservations']) == (fault in ('capture-stall', 'kill-unconfirmed'))
        assert not list((tmp_path / '.mission-state/exec-jobs').glob('*.json'))


@pytest.mark.parametrize('fault', ['missing', 'result', 'candidate', 'telemetry', 'completion', 'foreign-entry'])
def test_receipt_cannot_settle_an_unrelated_or_forged_dispatch(run_cli, tmp_path, fault):
    from dataclasses import replace
    from mission_application.verification_budget import reserve_verification, verification_settlement
    from mission_application.verification_execution import _blocked_receipt
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import RecordVerificationReceipt
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import decide
    from datetime import datetime, timezone
    from acceptance_contract import canonical_contract_digest
    _prepare_public_runner(tmp_path, run_cli)
    _budget(tmp_path)
    document = json.loads(_state_path(tmp_path).read_text())
    contract = document['acceptance_contract']
    at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    budget, reason = reserve_verification(document, 'AC1', 5, at, canonical_contract_digest(contract))
    assert reason is None
    command = contract['verifier_policy']['commands']['project-test']
    receipt = _blocked_receipt(contract, contract['verifier_policy'], 'AC1', command, 'process-unavailable')
    settlement = verification_settlement(budget, at, receipt)
    if fault == 'missing':
        settlement = None
    elif fault in {'result', 'candidate'}:
        settlement = replace(settlement, **{fault + '_digest': 'sha256:' + 'f' * 64})
    elif fault == 'telemetry':
        settlement = replace(settlement, output_bytes=999)
    elif fault == 'completion':
        settlement = replace(settlement, completed=True)
    else:
        document['budget_ledger']['reservations'][0]['target'] = 'foreign-target'
    state = decode_mission_state(json.dumps(document).encode())
    decision = decide(state, RecordVerificationReceipt(at, freeze_json_value(receipt), settlement))
    assert not decision.accepted and decision.rejection.code == 'verification-settlement-binding-invalid'


@pytest.mark.parametrize('criterion,repro', [
    ('UNKNOWN', None), ('AC1', {}), ('AC1', {'credential': 'unexpected'}),
    ('AC1', {'artifact_kind': 'counterexample', 'content': True}),
    ('AC1', {'artifact_kind': 'unknown', 'content': 'proof'}),
    ('AC1', {'artifact_kind': 'counterexample', 'content': 'x' * 65}),
    ('AC1', {'artifact_kind': 'counterexample', 'content': 'proof', 'extra': 1}),
])
def test_exec_job_rejects_unregistered_criterion_and_unbounded_replay(criterion, repro):
    from mission_application.spawn_trampoline import decode_job
    from mission_application.verifier_policy import validate
    from .test_issue878_verification_runner import _contract
    contract = _contract('mission-neutral')
    contract['verifier_policy'] = {'digest': 'sha256:' + 'a' * 64, 'commands': validate(_replay_policy())}
    raw = json.dumps(dict(schema='mission-exec-job/1', kind='verification', result_fd=3,
        contract=contract, criterion=criterion, repro_input=repro, deadline=100.)).encode()
    with pytest.raises(ValueError):
        decode_job(raw)


def test_known_receipt_rejection_still_settles_collected_child(run_cli, tmp_path, invoke_here, monkeypatch):
    from mission_application import evidence
    from mission_application.artifact import EvidenceFailure
    _prepare_public_runner(tmp_path, run_cli)
    _budget(tmp_path)
    def reject(*a, **kw):
        raise EvidenceFailure('verification-contract-stale')
    monkeypatch.setattr(evidence, 'run_verification_receipt', reject)
    with pytest.raises(SystemExit) as rejected:
        invoke_here(['verification', 'run', '--criterion', 'AC1'], {})
    assert rejected.value.code == 2
    state = json.loads(_state_path(tmp_path).read_text())
    assert not state['budget_ledger']['reservations']
    assert state['budget_ledger']['settlements'][-1]['outcome'] == 'settled'
    assert not state.get('verification_receipts')


def test_job_write_cannot_consume_deadline_then_launch_verification(tmp_path, monkeypatch):
    import budgeted_exec
    import time
    from mission_application.verifier_policy import validate
    from .test_issue878_verification_runner import _contract, _policy
    contract = _contract('mission-neutral')
    contract['verifier_policy'] = {'digest': 'sha256:' + 'a' * 64, 'commands': validate(_policy())}
    create = budgeted_exec.create_job
    def slow_write(*a, **kw):
        result = create(*a, **kw)
        time.sleep(.1)
        return result
    monkeypatch.setattr(budgeted_exec, 'create_job', slow_write)
    monkeypatch.setattr(budgeted_exec, 'spawn_exec', lambda *a, **kw: pytest.fail('spawn after job-write deadline'))
    deadline = time.monotonic() + .05
    with pytest.raises(TimeoutError):
        budgeted_exec.run_job('verification', dict(contract=contract, criterion='AC1', repro_input=None, deadline=deadline),
            tmp_path / 'jobs', deadline=deadline)
    assert not list((tmp_path / 'jobs').glob('*.json'))


@pytest.mark.parametrize('hold', ['open', 'kill-unconfirmed', 'live-owner'])
def test_reconcile_preserves_live_jobs_and_unconfirmed_kill_holds(run_cli, tmp_path, hold):
    from datetime import datetime, timedelta, timezone
    from mission_application.verification_budget import reserve_verification
    from mission_application.provider_budget import settle_provider
    from mission_persistence.spawn_jobs import create_job, session_token
    import os
    _prepare_public_runner(tmp_path, run_cli)
    _budget(tmp_path)
    path = _state_path(tmp_path)
    document = json.loads(path.read_text())
    old = datetime.now(timezone.utc) - timedelta(seconds=30 if hold != 'open' else 0)
    at = old.strftime('%Y-%m-%dT%H:%M:%SZ')
    document['budget_ledger']['clock'].update(opened_at=at, last_observed_at=at)
    budget, reason = reserve_verification(document, 'AC1', 3, at, 'sha256:' + 'a' * 64)
    assert reason is None
    if hold == 'kill-unconfirmed':
        settle_provider(document, budget, at, 'sha256:' + 'b' * 64, confirmed=False)
    path.write_text(json.dumps(document))
    job, _ = create_job(tmp_path / '.mission-state/exec-jobs', b'{}',
        reservation_id=budget.reservation.reservation_id.replace(':', '_') if hold != 'live-owner' else None, session_id=path.stem)
    if hold != 'live-owner':
        token = budget.reservation.reservation_id.replace(':', '_')
        stale = job.parent / (f'job-{os.getpid()}-1-s{session_token(path.stem)}-r{token}-' + 'b' * 32 + '.json')
        job.rename(stale)
        job = stale
    result = run_cli('budget', 'reconcile', cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert job.exists()
    ledger = json.loads(path.read_text())['budget_ledger']
    assert bool(ledger['reservations']) == (hold != 'live-owner')
    if hold == 'kill-unconfirmed':
        assert ledger['settlements'][-1]['outcome'] == 'kill-unconfirmed'


def test_budget_verification_retry_runs_again_with_a_new_settled_reservation(run_cli, tmp_path):
    from .test_issue878_verification_runner import _policy
    counter = tmp_path / 'process-count.txt'
    policy = _policy()
    policy['commands'][0]['argv'] = [policy['commands'][0]['argv'][0], '-c',
        f'from pathlib import Path; p=Path({str(counter)!r}); n=int(p.read_text() if p.exists() else "0")+1; p.write_text(str(n)); print(n)']
    _prepare_public_runner(tmp_path, run_cli, policy=policy)
    _budget(tmp_path)
    env = {'MISSION_OPERATION_ID': 'receipt-retry'}
    first = run_cli('verification', 'run', '--criterion', 'AC1', cwd=tmp_path, env_extra=env)
    second = run_cli('verification', 'run', '--criterion', 'AC1', cwd=tmp_path, env_extra=env)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    state = json.loads(_state_path(tmp_path).read_text())
    assert counter.read_text() == '2' and len(state['verification_receipts']) == 2
    assert not state['budget_ledger']['reservations'] and len(state['budget_ledger']['settlements']) == 2


def test_identical_receipt_retry_cannot_leave_its_new_reservation_open(run_cli, tmp_path, invoke_here, monkeypatch):
    from mission_application import verification_budget
    _prepare_public_runner(tmp_path, run_cli)
    _budget(tmp_path)
    args = ['verification', 'run', '--criterion', 'AC1']
    env = {'MISSION_OPERATION_ID': 'receipt-retry'}
    invoke_here(args, env)
    receipt = json.loads(_state_path(tmp_path).read_text())['verification_receipts'][-1]
    receipt.pop('recorded_at')
    monkeypatch.setattr(verification_budget, 'execute_verification', lambda *a, **kw: dict(receipt))
    try:
        invoke_here(args, env)
    except SystemExit as error:
        assert error.code == 2
    assert not json.loads(_state_path(tmp_path).read_text())['budget_ledger']['reservations']


def test_terminal_commit_failure_leaves_crash_reservation_for_reconcile(run_cli, tmp_path, invoke_here, monkeypatch):
    from mission_persistence.legacy_v4 import LegacyV4Repository
    from datetime import datetime, timedelta, timezone
    from dataclasses import replace
    _prepare_public_runner(tmp_path, run_cli)
    _budget(tmp_path)
    save = LegacyV4Repository.save
    def fail(repo, document, **kw):
        if document.get('verification_receipts'):
            raise OSError('terminal commit failed')
        return save(repo, document, **kw)
    monkeypatch.setattr(LegacyV4Repository, 'save', fail)
    with pytest.raises(OSError, match='terminal commit failed'):
        invoke_here(['verification', 'run', '--criterion', 'AC1'], {})
    state = json.loads(_state_path(tmp_path).read_text())
    assert len(state['budget_ledger']['reservations']) == 1 and not state.get('verification_receipts')
    assert not list((tmp_path / '.mission-state/exec-jobs').glob('*.json'))
    future = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime('%Y-%m-%dT%H:%M:%SZ')
    services = invoke_here.module._ACCEPTANCE_CONTRACT_CLI_SERVICES
    monkeypatch.setattr(invoke_here.module, '_ACCEPTANCE_CONTRACT_CLI_SERVICES', replace(services, now=lambda: future))
    invoke_here(['budget', 'reconcile'], {})
    ledger = json.loads(_state_path(tmp_path).read_text())['budget_ledger']
    assert not ledger['reservations'] and ledger['settlements'][-1]['outcome'] == 'charged-full-unknown'


def test_verification_reserves_space_for_large_argv_before_spawn(run_cli, tmp_path, invoke_here, monkeypatch):
    from mission_persistence.legacy_v4 import LegacyV4Repository
    from .test_issue878_verification_runner import _policy
    policy = _policy()
    argv = policy['commands'][0]['argv'] + ['x' * 65536] * 3
    policy['commands'][0]['argv'] = argv
    _prepare_public_runner(tmp_path, run_cli, policy=policy)
    _budget(tmp_path)
    save = LegacyV4Repository.save
    def inspect_reserve(repo, document, **kw):
        rows = document.get('budget_ledger', {}).get('reservations', [])
        if rows:
            assert rows[0]['reserved_bytes'] >= len(json.dumps(argv, indent=2).encode()) + 4096
            raise OSError('stop after inspected reservation')
        return save(repo, document, **kw)
    monkeypatch.setattr(LegacyV4Repository, 'save', inspect_reserve)
    with pytest.raises(OSError, match='stop after inspected reservation'):
        invoke_here(['verification', 'run', '--criterion', 'AC1'], {})


def test_large_verification_receipt_keeps_real_output_and_exit(run_cli, tmp_path):
    import hashlib
    from .test_issue878_verification_runner import _policy
    policy = _policy()
    policy['commands'][0]['argv'].append('x' * 70000)
    _prepare_public_runner(tmp_path, run_cli, policy=policy)
    _budget(tmp_path)
    result = run_cli('verification', 'run', '--criterion', 'AC1', cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    state = json.loads(_state_path(tmp_path).read_text())
    receipt = state['verification_receipts'][-1]
    assert receipt['status'] == 'passed' and receipt['exit_code'] == 0
    assert receipt['output_digest'] == 'sha256:' + hashlib.sha256(b'ok\n').hexdigest()
    assert receipt['observed_output_bytes'] == 3 and not state['budget_ledger']['reservations']


@pytest.mark.parametrize('reserved', [True, False])
def test_reconcile_does_not_delete_another_sessions_unread_job(run_cli, tmp_path, monkeypatch, reserved):
    from mission_persistence import spawn_jobs
    from budgeted_exec import parse_cli
    import argparse
    _prepare_public_runner(tmp_path, run_cli)
    _budget(tmp_path)
    directory = tmp_path / '.mission-state/exec-jobs'
    job, digest = spawn_jobs.create_job(directory, b'{"unread":"session-A"}', reservation_id='session_A_reservation' if reserved else None)
    import os
    token = '-rsession_A_reservation' if reserved else ''
    stale = directory / (f'job-{os.getpid()}-1{token}-' + 'c' * 32 + '.json')
    job.rename(stale)
    job = stale
    monkeypatch.setattr(spawn_jobs, 'process_start', lambda pid: 'dead-owner')
    # B has no A reservation. Parent death does not prove A's child read the file.
    result = run_cli('budget', 'reconcile', cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert spawn_jobs.read_job(job, digest) == b'{"unread":"session-A"}'
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr('sys.argv', ['neutral'])
    parse_cli(argparse.ArgumentParser())
    assert job.exists()


def test_deadline_preserves_large_candidate_result_and_kills_grandchildren(run_cli, tmp_path):
    import hashlib
    import signal
    from .test_issue878_verification_runner import _policy
    policy = _policy()
    policy['commands'][0].update(timeout_sec=5, argv=[policy['commands'][0]['argv'][0], '-c',
        'import subprocess,sys,time; print("prefix",flush=True); '
        'subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); time.sleep(60)'])
    _prepare_public_runner(tmp_path, run_cli, policy=policy,
        tracked_files={f'files/{n}.txt': 'x' * 4096 for n in range(3000)})
    _budget(tmp_path)
    result = run_cli('verification', 'run', '--criterion', 'AC1', cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(_state_path(tmp_path).read_text())['verification_receipts'][-1]
    assert receipt['exit_code'] == -signal.SIGKILL and receipt['timed_out']
    assert receipt['block_reason'] == 'timeout'
    assert receipt['output_digest'] == 'sha256:' + hashlib.sha256(b'prefix\n').hexdigest()
    assert receipt['observed_output_bytes'] == 7


@pytest.mark.parametrize('fault,reason', [('job', 'budget-job-write-failed'), ('exec', 'budget-deadline-unenforceable'), ('deadline', 'budget-deadline')])
def test_unstarted_verification_records_refusal_reason_and_zero_charge(run_cli, tmp_path, invoke_here, monkeypatch, fault, reason):
    import budgeted_exec
    from mission_persistence.spawn_jobs import JobWriteError
    _prepare_public_runner(tmp_path, run_cli)
    _budget(tmp_path)
    observed_monotonic = budgeted_exec.time.monotonic
    def fail(*a, **kw):
        monkeypatch.setattr(budgeted_exec.time, 'monotonic', lambda: observed_monotonic()+2)
        raise JobWriteError(None) if fault == 'job' else TimeoutError('budget-child-timeout') if fault == 'deadline' else OSError('exec failed')
    monkeypatch.setattr(budgeted_exec, 'create_job' if fault == 'job' else 'spawn_exec', fail)
    invoke_here(['verification', 'run', '--criterion', 'AC1'], {})
    state = json.loads(_state_path(tmp_path).read_text())
    assert state['verification_receipts'][-1]['block_reason'] == reason
    ledger = state['budget_ledger']
    assert not ledger['reservations'] and ledger['settlements'][-1]['charged_sec'] == 0
    assert ledger['stop_slots']['last_refusal'] == reason


def test_failed_completed_verifier_records_final_run(run_cli, tmp_path):
    from mission_application.provider_budget import _apply
    from mission_kernel.commands import EnterFinalPhase
    from datetime import datetime, timezone
    from .test_issue878_verification_runner import _policy
    policy = _policy()
    policy['commands'][0]['argv'] = [policy['commands'][0]['argv'][0], '-c', 'import sys; sys.exit(1)']
    _prepare_public_runner(tmp_path, run_cli, policy=policy)
    _budget(tmp_path)
    path = _state_path(tmp_path)
    document = json.loads(path.read_text())
    at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    assert _apply(document, EnterFinalPhase(at, 'verification')).accepted
    path.write_text(json.dumps(document))
    result = run_cli('verification', 'run', '--criterion', 'AC1', cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    document = json.loads(path.read_text())
    assert document['verification_receipts'][-1]['status'] == 'failed'
    assert document['verification_receipts'][-1]['exit_code'] == 1
    assert document['budget_ledger']['stop_slots']['final_run'] is not None


def test_real_candidate_change_reopens_no_progress_but_identical_tree_does_not(run_cli, tmp_path):
    from .test_issue878_verification_runner import _policy
    policy = _policy()
    counter = tmp_path / 'verification-count'
    policy['commands'][0]['argv'] = [policy['commands'][0]['argv'][0], '-c',
        f'from pathlib import Path; p=Path({str(counter)!r}); p.write_text(str(int(p.read_text() if p.exists() else "0")+1)); print("same")']
    _prepare_public_runner(tmp_path, run_cli, policy=policy)
    _budget(tmp_path)
    limit = json.loads(_state_path(tmp_path).read_text())['budget_ledger']['policy']['no_progress_limit']
    for _ in range(limit):
        result = run_cli('verification', 'run', '--criterion', 'AC1', cwd=tmp_path)
        assert result.returncode == 0, result.stderr
    before = json.loads(_state_path(tmp_path).read_text())['budget_ledger']['progress'][0]
    for _ in range(2):
        result = run_cli('verification', 'run', '--criterion', 'AC1', cwd=tmp_path)
        assert counter.read_text() == str(limit)
        assert json.loads(_state_path(tmp_path).read_text())['budget_ledger']['progress'][0] == before
    (tmp_path / 'tracked.txt').write_text('changed actual candidate')
    result = run_cli('verification', 'run', '--criterion', 'AC1', cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert counter.read_text() == str(limit + 1)
    state = json.loads(_state_path(tmp_path).read_text())
    signature = state['budget_ledger']['progress'][0]
    assert signature['candidate_digest'] == state['verification_receipts'][-1]['candidate_digest']
    assert signature['candidate_digest'] != before['candidate_digest'] and signature['consecutive_count'] == 1


def test_progress_result_signature_includes_exit_status_count_and_output(run_cli, tmp_path):
    from dataclasses import replace
    from datetime import datetime, timezone
    from mission_application.verification_budget import reserve_verification, verification_settlement
    from mission_application.verification_execution import _blocked_receipt
    _prepare_public_runner(tmp_path, run_cli)
    _budget(tmp_path)
    document = json.loads(_state_path(tmp_path).read_text())
    at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    budget, reason = reserve_verification(document, 'AC1', 5, at, 'sha256:' + 'a' * 64)
    assert reason is None
    contract = document['acceptance_contract']
    receipt = _blocked_receipt(contract, contract['verifier_policy'], 'AC1', contract['verifier_policy']['commands']['project-test'], 'test')
    receipt.update(candidate_digest='sha256:'+'b'*64, status='passed', block_reason=None, exit_code=0, executed_count=1)
    variants = [receipt, {**receipt, 'exit_code': 1}, {**receipt, 'status': 'failed'},
                {**receipt, 'executed_count': 2}, {**receipt, 'output_digest': 'sha256:'+'c'*64}]
    settlements = [verification_settlement(budget, at, value) for value in variants]
    assert len({s.result_digest for s in settlements}) == len(variants)
    assert all(s.candidate_digest == receipt['candidate_digest'] for s in settlements)






def test_cleanup_requires_session_identity_even_when_reservation_ids_overlap(tmp_path, monkeypatch):
    from mission_persistence import spawn_jobs as jobs
    directory = tmp_path / 'jobs'
    a, digest = jobs.create_job(directory, b'{"unread":true}', reservation_id='same_reservation', session_id='A')
    monkeypatch.setattr(jobs, 'process_start', lambda pid: 'absent')
    assert jobs.cleanup_jobs(directory, open_reservations=set(), closed_reservations={'same_reservation'}, session_id='B') == []
    assert jobs.read_job(a, digest) == b'{"unread":true}'
    assert jobs.cleanup_jobs(directory, open_reservations=set(), closed_reservations={'same_reservation'}, session_id='A') == [a]


def test_verification_job_rejects_deadline_that_cannot_be_enforced():
    from mission_application.spawn_trampoline import decode_job
    from .test_issue878_verification_runner import _contract, _policy
    from mission_application.verifier_policy import validate
    contract = _contract('mission-neutral')
    contract['verifier_policy'] = {'digest': 'sha256:'+'a'*64, 'commands': validate(_policy())}
    raw = json.dumps(dict(schema='mission-exec-job/1', kind='verification', result_fd=3,
        contract=contract, criterion='AC1', repro_input=None, deadline=10**400)).encode()
    with pytest.raises(ValueError):
        decode_job(raw)








def test_replay_path_conflict_keeps_candidate_digest_and_stops_identical_retry(run_cli, tmp_path):
    from mission_application.verification_runner import capture_candidate
    policy = _replay_policy()
    policy['commands'][0]['replay']['relative_path'] = 'tracked.txt'
    _prepare_public_runner(tmp_path, run_cli, policy=policy)
    _budget(tmp_path)
    repro = tmp_path / 'replay.json'
    repro.write_text(json.dumps({'artifact_kind': 'counterexample', 'content': 'proof'}))
    candidate = capture_candidate(tmp_path, declared_untracked=[], external_inputs=[]).digest
    limit = json.loads(_state_path(tmp_path).read_text())['budget_ledger']['policy']['no_progress_limit']
    for _ in range(limit):
        result = run_cli('verification', 'run', '--criterion', 'AC1', '--repro-input', str(repro), cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        state = json.loads(_state_path(tmp_path).read_text())
        receipt = state['verification_receipts'][-1]
        assert receipt['candidate_digest'] == candidate
        assert receipt['block_reason'] == 'replay-input-path-conflict'
        assert state['budget_ledger']['progress'][0]['candidate_digest'] == candidate
    result = run_cli('verification', 'run', '--criterion', 'AC1', '--repro-input', str(repro), cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(_state_path(tmp_path).read_text())['verification_receipts'][-1]
    assert receipt['candidate_digest'] == candidate and receipt['block_reason'] == 'budget-no-new-evidence'


@pytest.mark.parametrize('run_sec,reason', [(4, 'budget-deadline'), (5, 'timeout')])
def test_timeout_reason_uses_reserved_run_duration_not_startup_delay(run_cli, tmp_path, monkeypatch, run_sec, reason):
    import time
    import budgeted_exec
    from dataclasses import replace
    from datetime import datetime, timezone
    from mission_application.verification_budget import reserve_verification, execute_verification
    _prepare_public_runner(tmp_path, run_cli)
    _budget(tmp_path)
    document = json.loads(_state_path(tmp_path).read_text())
    at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    budget, refusal = reserve_verification(document, 'AC1', run_sec, at, None)
    assert refusal is None
    budget = replace(budget, deadline=time.monotonic() + .01)
    monkeypatch.setattr(budgeted_exec, 'run_job', lambda *a, **kw:
        dict(status='blocked', timed_out=True, exit_code=-9, block_reason='budget-deadline'))
    receipt = execute_verification(document, tmp_path, 'AC1', None, budget,
        dict(timeout_sec=5), session_id='neutral')
    assert receipt['block_reason'] == reason


@pytest.mark.parametrize('budgeted', [False, True])
@pytest.mark.parametrize('fault,reason', [
    ('unsupported', 'replay-unsupported'), ('invalid', 'replay-input-invalid'),
    ('oversize', 'replay-input-invalid'),
    ('path-conflict', 'replay-input-path-conflict'), ('no-progress', 'budget-no-new-evidence'),
])
def test_blocked_verifier_receipts_record_current_observation_time(monkeypatch, tmp_path, budgeted, fault, reason):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import time
    from mission_application import verification_execution as execution
    from mission_application.verifier_policy import validate
    from .test_issue878_verification_runner import _contract
    contract = _contract('neutral-session')
    commands = validate(_replay_policy())
    contract['verifier_policy'] = dict(digest='sha256:' + 'a'*64, commands=commands)
    repro = dict(artifact_kind='counterexample', content='proof')
    if fault == 'unsupported':
        commands['project-test'].pop('replay')
    elif fault == 'invalid':
        repro['content'] = True
    elif fault == 'oversize':
        repro['content'] = 'x'*65
    candidate = SimpleNamespace(digest='sha256:' + 'b'*64,
        files=[SimpleNamespace(path='repro.json')])
    monkeypatch.setattr(execution, 'capture_candidate', lambda *a, **kw: candidate)
    monkeypatch.setattr(execution, 'execute_candidate', lambda *a, **kw: pytest.fail('blocked replay started target'))
    before = datetime.now(timezone.utc).replace(microsecond=0)
    receipt = execution.run_contract_verifier(dict(acceptance_contract=contract),
        project_root=tmp_path, criterion_id='AC1', repro_input=repro,
        **({'budget_deadline': time.monotonic()+5} if budgeted else {}),
        no_progress_candidate=candidate.digest if fault == 'no-progress' else None)
    after = datetime.now(timezone.utc)
    assert receipt['status'] == 'blocked' and receipt['block_reason'] == reason
    start, finish = (datetime.fromisoformat(receipt[key].replace('Z', '+00:00'))
        for key in ('started_at', 'finished_at'))
    assert before <= start <= finish <= after, receipt
    assert receipt['exit_code'] is None and not receipt['timed_out']
    if fault in ('path-conflict', 'no-progress'):
        assert receipt['candidate_digest'] == candidate.digest
