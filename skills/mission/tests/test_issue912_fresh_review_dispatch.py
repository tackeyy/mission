"""D2c stops at running/blocked/abandoned: output import belongs to D2."""
import json

import pytest

from .test_issue879_completion_cli import completion_session as _completion_session, _reject_unchanged


@pytest.fixture(params=[5], ids=["v5-container"])
def completion_session(request, state_dir, run_cli):
    return _completion_session.__wrapped__(request, state_dir, run_cli)
from .test_issue895_fresh_review import _prepare


@pytest.mark.parametrize("completion_session", [4, 5], indirect=True, ids=["v4-flat", "v5-container"])
def test_unregistered_launch_is_consumed_blocked_not_completed(completion_session, run_cli):
    if completion_session[2] == 4:
        root, request = _prepare(completion_session, run_cli)
        _reject_unchanged(run_cli, root, ['fresh-review', 'run', '--request', request['request_id'],
                          '--adapter', 'neutral'], 'state-capacity-exhausted',
                          env={'MISSION_OPERATION_ID': 'dispatch-one'})
        return
    root, request = _prepare(completion_session, run_cli)
    result = run_cli('fresh-review', 'run', '--request', request['request_id'], '--adapter', 'neutral',
                     cwd=root, env_extra={'MISSION_OPERATION_ID': 'dispatch-one'})
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads(result.stdout)['record']
    assert record['status'] == 'blocked'
    assert record['result']['outcome'] == 'blocked'
    assert record['result']['launch_attempted'] is False
    assert record['dispatch']['operation_id'] == record['result']['dispatch_operation_id']
    assert record['result']['commit_operation_id'] == record['dispatch']['operation_id']
    _reject_unchanged(run_cli, root, ['mark-passes'], 'acceptance-coverage-pending')
    _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', request['request_id'], '--adapter', 'neutral'],
                      'operation-conflict', env={'MISSION_OPERATION_ID': 'dispatch-one'})


@pytest.fixture
def reviewer(completion_session, run_cli, tmp_path):
    import hashlib
    from pathlib import Path
    from mission_kernel.fresh_review import canonical_digest
    from .test_issue879_completion_cli import _persist_fixture
    root, state, schema = completion_session
    installed = root / 'adapter-install'
    installed.mkdir()
    module = installed / 'neutral_adapter.py'
    module.write_bytes((Path(__file__).parent / 'fixtures' / 'fresh_review_adapter.py').read_bytes())
    dist = installed / 'neutral_adapter-1.0.dist-info'
    dist.mkdir()
    (dist / 'METADATA').write_text('Metadata-Version: 2.1\nName: neutral-adapter\nVersion: 1.0\n')
    (dist / 'entry_points.txt').write_text('[mission.fresh_review_adapters]\nneutral = neutral_adapter:factory\n')
    registration = dict(id='neutral', entry_point='neutral', distribution='neutral-adapter', version='1.0',
                        source_digest='sha256:' + hashlib.sha256(module.read_bytes()).hexdigest())
    config = installed / 'config' / 'mission'
    config.mkdir(parents=True)
    (config / 'fresh-review-adapters.json').write_text(json.dumps({
        'schema': 'mission-fresh-review-adapter-registry/1', 'adapters': [registration]}))
    _persist_fixture(root, state, schema)
    env = {'PYTHONPATH': str(installed), 'XDG_CONFIG_HOME': str(config.parent),
           'FIXTURE_REVIEW_JOURNAL': str(installed / 'journal.json'), 'FIXTURE_REVIEW_STATE': str(root / '.mission-state'), 'MISSION_OPERATION_ID': 'dispatch-one'}
    result = run_cli('fresh-review', 'prepare', '--perspective', 'counterexamples',
                     '--adapter-registration-digest', canonical_digest(registration),
                     cwd=root, env_extra={**env, 'MISSION_OPERATION_ID': 'prepare-one'})
    assert result.returncode == 0, result.stdout + result.stderr
    request = json.loads(result.stdout)['request']
    return root, request, env, installed / 'journal.json'


def invoke(run_cli, reviewer, command='run', **env):
    root, request, environment, _ = reviewer
    result = run_cli('fresh-review', command, '--request', request['request_id'], '--adapter', 'neutral',
                     cwd=root, env_extra={**environment, **env})
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)['record']


def test_registered_child_reaches_running_once_and_does_not_complete(reviewer, run_cli):
    root, request, _, journal = reviewer
    record = invoke(run_cli, reviewer)
    assert record['status'] == 'running'
    assert record['independent'] is True
    assert record['launch']['received_input_digest'] == request['input_digest']
    assert record['launch']['operation_id'] == record['dispatch']['operation_id']
    assert record['launch']['fencing_epoch'] == record['dispatch']['fencing_epoch']
    status = json.loads(run_cli('fresh-review', 'status', cwd=root).stdout)['requests'][0]
    assert status['launch'] == record['launch'] and status['independent'] is True
    assert invoke(run_cli, reviewer) == record
    assert json.loads(journal.read_text())['count'] == 1
    assert record['result'] is None  # Completed requires #896's output import.
    _reject_unchanged(run_cli, root, ['mark-passes'], 'acceptance-coverage-pending')


@pytest.mark.parametrize('unconfirmed', [False, True])
def test_crash_reconcile_after_lease_takeover_never_launches_twice(reviewer, run_cli, unconfirmed):
    from .test_issue879_completion_cli import _rewrite_fixture_document
    root, _, _, journal = reviewer
    unknown = invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE='crash')
    assert unknown['status'] == 'dispatch-unknown'
    assert json.loads(journal.read_text())['count'] == 1
    _rewrite_fixture_document(root, lambda state: state.update(lease_expires_at='2000-01-01T00:00:00Z'))
    stored = json.loads(journal.read_text())
    stored['output'] = None
    journal.write_text(json.dumps(stored))
    if unconfirmed:
        _expire_dispatch(reviewer)
        result = run_cli('fresh-review', 'reconcile', '--request', reviewer[1]['request_id'], '--adapter', 'neutral', cwd=root,
                         env_extra={**reviewer[2], 'MISSION_OPERATION_ID': 'reconcile-one', 'MISSION_LEASE_ID': 'takeover-lease', 'FIXTURE_CANCEL': 'unknown'})
        assert result.returncode == 2 and 'fresh-review-kill-unconfirmed' in result.stderr
    running = (json.loads(run_cli('get', cwd=root).stdout)['fresh_review']['requests'][0] if unconfirmed else
               invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one', MISSION_LEASE_ID='takeover-lease'))
    assert running['status'] == 'running'
    assert running['launch']['fencing_epoch'] == unknown['dispatch']['fencing_epoch']
    assert running['launch']['operation_id'] == unknown['dispatch']['operation_id']
    assert running['launch_operation_id'] == 'reconcile-one'
    assert json.loads(run_cli('get', cwd=root).stdout)['fencing_epoch'] > unknown['dispatch']['fencing_epoch']
    assert json.loads(journal.read_text())['count'] == 1
    assert running['result'] is None  # D2c does not import collected output.
    if unconfirmed:
        _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', reviewer[1]['request_id'], '--adapter', 'neutral'],
                          'fresh-review-kill-unconfirmed', env={**reviewer[2], 'MISSION_OPERATION_ID': 'reconcile-one',
                          'MISSION_LEASE_ID': 'takeover-lease', 'FIXTURE_CANCEL': 'unknown'})
    else:
        assert invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one', MISSION_LEASE_ID='takeover-lease') == running
        _expire_dispatch(reviewer)
    assert invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one', MISSION_LEASE_ID='takeover-lease')['status'] == 'abandoned-unknown'


def test_unknown_without_host_output_is_abandoned_with_split_epochs(reviewer, run_cli):
    from .test_issue879_completion_cli import _rewrite_fixture_document
    root, _, _, journal = reviewer
    unknown = invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE='crash')
    _expire_dispatch(reviewer)
    journal.unlink()
    _rewrite_fixture_document(root, lambda state: state.update(lease_expires_at='2000-01-01T00:00:00Z'))
    abandoned = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one', MISSION_LEASE_ID='takeover-lease')
    assert abandoned['status'] == 'abandoned-unknown'
    receipt = abandoned['result']
    assert receipt['dispatch_operation_id'] == unknown['dispatch']['operation_id']
    assert receipt['dispatch_fencing_epoch'] == unknown['dispatch']['fencing_epoch']
    assert receipt['commit_operation_id'] != receipt['dispatch_operation_id']
    assert receipt['commit_fencing_epoch'] > receipt['dispatch_fencing_epoch']
    assert not journal.exists()  # Reconcile cannot manufacture another child.
    assert invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one', MISSION_LEASE_ID='takeover-lease') == abandoned
    _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', reviewer[1]['request_id'],
                      '--adapter', 'other'], 'adapter-pin-changed',
                      env={**reviewer[2], 'MISSION_OPERATION_ID': 'reconcile-one', 'MISSION_LEASE_ID': 'takeover-lease'})
    _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', reviewer[1]['request_id'],
                      '--adapter', 'neutral', '--completed'], 'unrecognized arguments', env=reviewer[2])
    env = {**reviewer[2], 'MISSION_LEASE_ID': 'takeover-lease'}
    prepared = run_cli('fresh-review', 'prepare', '--perspective', 'counterexamples',
        '--adapter-registration-digest', reviewer[1]['adapter_registration_digest'], cwd=root,
        env_extra={**env, 'MISSION_OPERATION_ID': 'prepare-two'})
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    next_id = json.loads(prepared.stdout)['request']['request_id']
    _reject_unchanged(run_cli, root, ['fresh-review', 'run', '--request', next_id, '--adapter', 'neutral'],
                      'operation-conflict', env={**env, 'MISSION_OPERATION_ID': 'reconcile-one'})



@pytest.mark.parametrize('mode,status,independent,cancel', [('inline', 'running', False, 'cancelled')] + [
    (mode, 'blocked', None, cancel) for mode in ('unobservable', 'provider-invalid', 'binding-mismatch')
    for cancel in ('cancelled', 'failed', 'unknown')])
def test_host_observation_controls_identity_and_independence(reviewer, run_cli, mode, status, independent, cancel):
    if status == 'blocked' and cancel != 'cancelled':
        root, request, env, _ = reviewer
        result = run_cli('fresh-review', 'run', '--request', request['request_id'], '--adapter', 'neutral',
                         cwd=root, env_extra={**env, 'FIXTURE_REVIEW_MODE': mode, 'FIXTURE_CANCEL': cancel})
        assert result.returncode == 2 and 'fresh-review-kill-unconfirmed' in result.stderr
        assert json.loads(run_cli('fresh-review', 'status', cwd=root).stdout)['requests'][0]['status'] == 'dispatch-unknown'
        _expire_dispatch(reviewer)
        recovered = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='retry-cancel',
                           FIXTURE_REVIEW_MODE='' if mode == 'binding-mismatch' else 'malformed-observation')
        assert recovered['status'] == ('blocked' if mode == 'binding-mismatch' else 'abandoned-unknown')
        return
    record = invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE=mode)
    assert record['status'] == status
    assert record.get('independent') is independent
    if status == 'blocked':
        assert record['result']['reason'] == ('binding-mismatch' if mode == 'binding-mismatch' else 'launch-invalid' if mode == 'provider-invalid' else 'identity-unobservable')
        assert record['result']['launch_attempted'] is True
        assert record['result']['budget_used']['wall_time_sec'] >= 1
        assert record['result']['cancel_result'] == 'cancelled'
    assert record['status'] != 'completed'


def test_old_writer_is_rejected_by_kernel_fence_after_takeover(reviewer, run_cli):
    from dataclasses import replace
    from mission_kernel import decode_mission_state
    from mission_kernel.commands import RecordFreshReviewLaunch
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import decide
    from .test_issue879_completion_cli import _rewrite_fixture_document
    root, request, _, journal = reviewer
    old = invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE='crash')
    _rewrite_fixture_document(root, lambda state: state.update(lease_expires_at='2000-01-01T00:00:00Z'))
    invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one', MISSION_LEASE_ID='takeover-lease')
    state = decode_mission_state(run_cli('get', cwd=root).stdout.encode())
    # Restore the crash checkpoint as a typed fixture under the new lease, so
    # only the fence distinguishes this old writer from the legitimate resume.
    from mission_kernel.fresh_review import decode_projection
    record = decode_projection({'fresh_review': {'schema': 'mission-fresh-review/1', 'requests': [old]}}).requests[0]
    projection = replace(state.fresh_review, requests=(record,))
    key = "legacy_passthrough" if state.legacy_passthrough is not None else "extensions"
    backing = getattr(state, key).thaw()
    backing['fresh_review'] = {'schema': 'mission-fresh-review/1', 'requests': [old]}
    state = replace(state, fresh_review=projection, **{key: freeze_json_value(backing)})
    command = RecordFreshReviewLaunch(request['request_id'], old['dispatch']['operation_id'],
        old['dispatch']['fencing_epoch'], freeze_json_value(json.loads(journal.read_text())['launch']), request['candidate_digest'])
    decision = decide(state, command)
    assert not decision.accepted and decision.rejection.code == 'fresh-review-stale-fence'
    assert decide(state, replace(command, fencing_epoch=state.lease.fencing_epoch)).accepted


def test_crash_after_intent_before_spawn_abandons_without_launch(reviewer, run_cli):
    record = invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE='intent-crash')
    assert record['status'] == 'dispatch-unknown'
    assert not reviewer[3].exists()
    _expire_dispatch(reviewer)
    recovered = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one')
    assert recovered['status'] == 'abandoned-unknown'
    assert not reviewer[3].exists()


@pytest.mark.parametrize('status', ['dispatch-unknown', 'running'])
@pytest.mark.parametrize('mutation', ['identity-missing', 'malformed-observation'])
def test_unobservable_recovery_receipt_converges_to_abandoned(reviewer, run_cli, status, mutation):
    _, _, _, journal = reviewer
    old = invoke(run_cli, reviewer, **({'FIXTURE_REVIEW_MODE': 'crash'} if status == 'dispatch-unknown' else {}))
    stored = json.loads(journal.read_text())
    if mutation == 'identity-missing':
        stored['launch'].pop('child_identity')
    journal.write_text(json.dumps(stored))
    mode = 'malformed-observation' if mutation == 'malformed-observation' else ''
    assert invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one', FIXTURE_REVIEW_MODE=mode) == old
    _expire_dispatch(reviewer)
    abandoned = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one', FIXTURE_REVIEW_MODE=mode)
    assert abandoned['status'] == 'abandoned-unknown'
    assert abandoned['result']['reason'] == 'child-unobservable'
    assert abandoned['result']['dispatch_operation_id'] == old['dispatch']['operation_id']
    assert json.loads(journal.read_text())['count'] == 1



@pytest.mark.parametrize('status', ['dispatch-unknown', 'running'])
@pytest.mark.parametrize('field', ['operation_id', 'fencing_epoch', 'request_id', 'nonce'])
def test_recovery_sender_binding_mismatch_rejects_then_cancels(reviewer, run_cli, status, field):
    root, request, environment, journal = reviewer
    old = invoke(run_cli, reviewer, **({'FIXTURE_REVIEW_MODE': 'crash'} if status == 'dispatch-unknown' else {}))
    stored = json.loads(journal.read_text())
    stored['launch'][field] = stored['launch'][field] + 1 if field == 'fencing_epoch' else 'foreign-child'
    stored.update(output=None, process_exited=True)
    journal.write_text(json.dumps(stored))
    _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', request['request_id'],
                      '--adapter', 'neutral'], 'fresh-review-output-sender-mismatch',
                      env={**environment, 'MISSION_OPERATION_ID': 'reconcile-one'})
    assert not journal.with_suffix('.cancel').exists()
    _expire_dispatch(reviewer)
    record = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one')
    assert record['status'] == 'abandoned-unknown' and record.get('launch') == old.get('launch')
    assert journal.with_suffix('.cancel').read_text() == status
    assert json.loads(journal.read_text())['count'] == 1


def test_running_child_identity_mismatch_is_rejected_without_consuming(reviewer, run_cli):
    root, request, environment, journal = reviewer
    invoke(run_cli, reviewer)
    stored = json.loads(journal.read_text())
    stored['launch']['child_identity'] = 'foreign-child'
    journal.write_text(json.dumps(stored))
    _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', request['request_id'],
                      '--adapter', 'neutral'], 'fresh-review-output-sender-mismatch',
                      env={**environment, 'MISSION_OPERATION_ID': 'reconcile-one'})


@pytest.mark.parametrize('status', ['dispatch-unknown', 'running'])
def test_reconcile_rechecks_candidate_before_abandoning(reviewer, run_cli, status):
    root, request, environment, journal = reviewer
    invoke(run_cli, reviewer, **({'FIXTURE_REVIEW_MODE': 'crash'} if status == 'dispatch-unknown' else {}))
    _expire_dispatch(reviewer)
    journal.unlink()
    (root / 'app.txt').write_text('candidate changed')
    _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', request['request_id'],
                      '--adapter', 'neutral'], 'fresh-review-stale',
                      env={**environment, 'MISSION_OPERATION_ID': 'reconcile-one'})
    assert not journal.exists()


def test_run_rechecks_candidate_before_blocking(reviewer, run_cli):
    root, request, environment, journal = reviewer
    result = run_cli('fresh-review', 'run', '--request', request['request_id'], '--adapter', 'neutral',
                     cwd=root, env_extra={**environment, 'FIXTURE_REVIEW_MODE': 'stale-unavailable'})
    assert result.returncode != 0 and 'fresh-review-stale' in result.stderr + result.stdout
    state = json.loads(run_cli('get', cwd=root).stdout)
    record = state['fresh_review']['requests'][0]
    assert record['status'] == 'dispatch-unknown' and record['result'] is None
    assert not journal.exists()


def test_reconcile_rechecks_candidate_before_publishing_running(reviewer, run_cli):
    root, request, env, journal = reviewer
    invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE='crash')
    (root / 'app.txt').write_text('candidate changed')
    _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', request['request_id'], '--adapter', 'neutral'],
                      'fresh-review-stale', env={**env, 'MISSION_OPERATION_ID': 'reconcile-one'})
    assert json.loads(journal.read_text())['count'] == 1


def test_public_dispatch_schema_names_the_incomplete_saga(tmp_path, run_cli):
    for command in ('fresh-review-run', 'fresh-review-reconcile'):
        result = run_cli('schema', '--contract', command, cwd=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        schema = json.loads(result.stdout)
        assert schema['closed'] is True
        assert schema['terminal_outcomes'] == (['blocked', 'abandoned-unknown', 'failed']
                                               if command == 'fresh-review-reconcile' else
                                               ['blocked', 'abandoned-unknown'])


@pytest.mark.parametrize('mode', ['launch-fence', 'blocked-fence', 'candidate-error'])
def test_launch_writer_retains_dispatch_fence_and_candidate_rejections(tmp_path, monkeypatch, mode):
    from dataclasses import replace
    from types import SimpleNamespace as Namespace
    from contextlib import nullcontext
    import mission_application.fresh_review_dispatch as app
    from mission_kernel.fresh_review import FreshReviewError, projection_document, canonical_digest, request_document
    from mission_kernel.json_codec import freeze_json_value
    from .test_issue895_fresh_review import _pure_projection, ADAPTER
    from .test_issue909_fresh_review_receipts import launch_document
    projection = _pure_projection()
    request = projection.requests[0].request
    state = {'fresh_review': projection_document(projection), 'fencing_epoch': 2}
    sf = tmp_path / 'state.json'
    sf.touch()
    reader = Namespace(transaction=lambda: nullcontext(), load=lambda: state)
    services = Namespace(resolve_state_file=lambda _: sf, repository=lambda *a, **k: reader,
        compatibility_arguments=lambda *a, **k: ('dispatch', {}), canonical_operation=lambda *a, **k: ('dispatch', {}),
        capacity_status=lambda _: {'code': None}, commit_errors=(), now=lambda: '2026-01-01T00:00:01+00:00', fail=lambda code, _: (_ for _ in ()).throw(FreshReviewError(code)))
    from mission_application.verification_runner import VerificationRunnerError
    def candidate(*args):
        if mode == 'candidate-error':
            raise VerificationRunnerError('declared-output-missing')
        return request.candidate_digest
    monkeypatch.setattr(app, '_candidate', candidate)
    raw = launch_document()
    raw['request_digest'] = canonical_digest(request_document(request))
    cancelled = []
    host = Namespace(resolve=lambda _: Namespace(registration=Namespace(digest=ADAPTER)),
        observe=lambda _: {'parent_identity': 'parent'}, launch=lambda *a: ({'blocked': 'launch-invalid', 'attempted': True} if mode == 'blocked-fence' else {'launch_receipt': raw}),
        cancel=lambda pin, dispatch: cancelled.append((dispatch['operation_id'], dispatch['fencing_epoch'])) or 'cancelled')

    def execute(_, build):
        command = build(state)
        if isinstance(command, app.BeginFreshReviewDispatch):
            record = replace(projection.requests[0], status='dispatch-unknown', operation_id=command.operation_id,
                intent_digest=command.intent_digest, payload_digest=command.payload_digest, dispatch=command.dispatch)
            state['fresh_review'] = projection_document(replace(projection, requests=(record,)))
            state['fencing_epoch'] += 1
            return record
        assert command.fencing_epoch == 2  # Original dispatch writer, even for blocked.
        raise FreshReviewError('fresh-review-stale-fence')
    monkeypatch.setattr(app, '_execute', execute)
    with pytest.raises(FreshReviewError, match='declared-output-missing' if mode == 'candidate-error' else 'stale-fence'):
        app.run_fresh_review_dispatch_cli(Namespace(request=request.request_id, adapter='neutral', fresh_review_command='run'), services, host)
    assert cancelled == ([(state['fresh_review']['requests'][0]['operation_id'], 2)] if mode == 'blocked-fence' else [])


def test_host_confirms_launch_impossible_before_child_spawn(reviewer, run_cli):
    record = invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE='unavailable')
    assert record['status'] == 'blocked'
    assert record['result']['reason'] == 'launch-unavailable'
    assert record['result']['launch_attempted'] is False
    assert not reviewer[3].exists()


def test_deadline_and_budget_identity_are_persisted_before_callback(reviewer, run_cli):
    from datetime import datetime
    record = invoke(run_cli, reviewer)
    dispatch = record['dispatch']
    assert datetime.fromisoformat(dispatch['deadline_at'].replace('Z', '+00:00')).utcoffset() is not None
    assert dispatch['reservation_id'].startswith('reservation:')
    from mission_kernel.fresh_review_dispatch import reservation_id_for_operation, budget_class_for_fresh_review_dispatch
    assert dispatch['reservation_id'] == reservation_id_for_operation(dispatch['operation_id'])
    assert dispatch['budget_class'] == budget_class_for_fresh_review_dispatch()
    assert dispatch['reservation_id'] != dispatch['operation_id']


def test_adapter_callbacks_share_exec_child_and_parent_never_loads_code(reviewer, monkeypatch):
    import os
    import fresh_review_host as host
    import fresh_review_runtime as runtime
    from mission_kernel.fresh_review import decode_request
    root, request, environment, _ = reviewer
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.syspath_prepend(environment['PYTHONPATH'])
    calls = []
    actual_spawn = host.spawn_exec
    def spawn(*args, **kwargs):
        calls.append(args[0])
        return actual_spawn(*args, **kwargs)
    monkeypatch.setattr(host, 'spawn_exec', spawn)
    monkeypatch.setattr(runtime, 'load_adapter', lambda _: pytest.fail('adapter loaded in parent'))
    monkeypatch.setattr(runtime, '_source_digest', lambda _: pytest.fail('adapter source read in parent'))
    pin = host.resolve('neutral')
    assert host.observe(pin) == {'parent_identity': 'fixture-parent'}
    assert host.recover(pin, {})['observation'] == {}
    assert host.cancel(pin, {}) == 'cancelled'
    # Malformed callback replies are unknown, never a launch receipt.
    assert host._call(pin, 'invalid-action', {}) == {'unknown': True}
    assert len(calls) == 5
    assert all(str(host.Path(host.__file__).resolve()) in command for command in calls)


@pytest.mark.parametrize('operation', [None, 'w' * 128], ids=['generated-id', 'maximum-id'])
def test_withdraw_pending_over_capacity_shrinks_and_replays_tombstone(reviewer, run_cli, operation):
    from .test_issue879_completion_cli import _rewrite_fixture_document, _public_bytes
    from mission_kernel.json_codec import STATE_LIMIT
    root, request, environment, journal = reviewer
    def excess(state):
        state['capacity_fixture_padding'] = 'x' * (STATE_LIMIT - 200000)
    _rewrite_fixture_document(root, excess)
    _reject_unchanged(run_cli, root, ['fresh-review', 'run', '--request', request['request_id'],
                      '--adapter', 'neutral'], 'state-capacity-exhausted', env=environment)
    assert not journal.exists()
    before = json.loads(run_cli('get', cwd=root).stdout)
    withdraw_env = {key: value for key, value in environment.items() if key != 'MISSION_OPERATION_ID'}
    if operation is not None:
        withdraw_env['MISSION_OPERATION_ID'] = operation
    result = run_cli('fresh-review', 'withdraw', '--request', request['request_id'], cwd=root,
                     env_extra=withdraw_env)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads(result.stdout)['record']
    assert record['status'] == 'withdrawn' and record['nonce'] == request['nonce']
    after = json.loads(run_cli('get', cwd=root).stdout)
    assert len(json.dumps(after)) < len(json.dumps(before))
    public = _public_bytes(root)
    again = run_cli('fresh-review', 'withdraw', '--request', request['request_id'], cwd=root,
                    env_extra={**withdraw_env, 'MISSION_OPERATION_ID': record['withdraw_operation_id']})
    assert again.returncode == 0 and json.loads(again.stdout)['record'] == record
    assert _public_bytes(root) == public
    _reject_unchanged(run_cli, root, ['fresh-review', 'run', '--request', request['request_id'],
                      '--adapter', 'neutral'], 'fresh-review-request-withdrawn', env=environment)
    assert not journal.exists()


def test_exec_timeout_kills_callback_descendants_without_success(reviewer, monkeypatch):
    import time
    import fresh_review_host as host
    _, _, environment, journal = reviewer
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv('FIXTURE_REVIEW_MODE', 'callback-timeout')
    monkeypatch.syspath_prepend(environment['PYTHONPATH'])
    pin = host.resolve('neutral')
    # The fixture blocks once its descendant is running; the timeout bounds start-up.
    assert host._call(pin, 'observe', {}, timeout=5) == {'unknown': True}
    heartbeat = journal.with_suffix('.heartbeat')
    assert heartbeat.exists()  # A real descendant was running, not a stub.
    before = heartbeat.read_bytes()
    time.sleep(.08)
    assert heartbeat.read_bytes() == before
    assert not journal.exists()


@pytest.mark.parametrize('explicit', [False, True], ids=['no-operation-id', 'maximum-operation-id'])
def test_public_dispatch_operation_identity_boundaries(reviewer, run_cli, explicit):
    root, request, environment, journal = reviewer
    environment = {key: value for key, value in environment.items() if key != 'MISSION_OPERATION_ID'}
    if explicit:
        environment['MISSION_OPERATION_ID'] = 'd' * 128
    result = run_cli('fresh-review', 'run', '--request', request['request_id'], '--adapter', 'neutral',
                     cwd=root, env_extra={**environment, 'FIXTURE_REVIEW_MODE': 'crash'})
    assert result.returncode == 0, result.stdout + result.stderr
    old = json.loads(result.stdout)['record']
    assert old['status'] == 'dispatch-unknown'
    if explicit:
        assert old['dispatch']['operation_id'] == 'd' * 128
        environment['MISSION_OPERATION_ID'] = 'r' * 128
    _expire_dispatch(reviewer)
    journal.unlink()
    result = run_cli('fresh-review', 'reconcile', '--request', request['request_id'], '--adapter', 'neutral',
                     cwd=root, env_extra=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['record']['status'] == 'abandoned-unknown'


def test_future_launch_clock_cannot_strand_a_running_request(reviewer, run_cli):
    from .test_issue879_completion_cli import _rewrite_fixture_document
    root, _, _, journal = reviewer
    record = invoke(run_cli, reviewer)
    _expire_dispatch(reviewer)
    stored = json.loads(journal.read_text())
    stored['launch']['started_at'] = '9999-12-31T23:59:59.999999Z'
    stored['output'] = None
    journal.write_text(json.dumps(stored))
    def future(state):
        state['fresh_review']['requests'][0]['launch'] = stored['launch']
    _rewrite_fixture_document(root, future)
    abandoned = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one')
    assert abandoned['status'] == 'abandoned-unknown'
    assert abandoned['result']['launch_receipt'] == stored['launch']
    assert abandoned['result']['ended_at'] >= stored['launch']['started_at']
    assert json.loads(journal.read_text())['count'] == 1


@pytest.mark.parametrize('value', [None, [], 'cancelled', {'status': None}])
def test_invalid_cancel_observation_is_unknown_not_an_exception(monkeypatch, value):
    import fresh_review_host as host
    monkeypatch.setattr(host, '_call', lambda *args, **kwargs: {'cancel': value})
    assert host.cancel(None, {}) == 'unknown'


def test_withdraw_refuses_funded_pending_and_already_running(reviewer, run_cli):
    root, request, environment, _ = reviewer
    command = ['fresh-review', 'withdraw', '--request', request['request_id']]
    env = {**environment, 'MISSION_OPERATION_ID': 'withdraw-one'}
    _reject_unchanged(run_cli, root, command, 'state-capacity-withdraw-not-needed', env=env)
    invoke(run_cli, reviewer)
    _reject_unchanged(run_cli, root, command, 'fresh-review-request-not-pending', env=env)


@pytest.mark.parametrize('mutation', ['missing', 'foreign-id', 'extra', 'oversized', 'invalid-version'])
def test_resolved_child_pin_is_closed_and_bound_to_requested_adapter(monkeypatch, mutation):
    import fresh_review_host as host
    from mission_kernel.fresh_review import FreshReviewError
    pin = dict(registration=dict(id='neutral', entry_point='neutral', distribution='neutral-adapter',
                                version='1.0', source_digest='sha256:' + 'a' * 64),
               entry_point_value='neutral_adapter:factory', module='neutral_adapter')
    if mutation == 'foreign-id': pin['registration']['id'] = 'foreign'
    if mutation == 'extra': pin['extra'] = True
    if mutation == 'oversized': pin['module'] = 'a' * 1025
    if mutation == 'invalid-version': pin['registration']['version'] = []
    monkeypatch.setattr(host, '_call', lambda *a, **k: {'pin': None if mutation == 'missing' else pin})
    with pytest.raises(FreshReviewError):
        host.resolve('neutral')


def _expire_dispatch(reviewer):
    from .test_issue879_completion_cli import _rewrite_fixture_document
    _rewrite_fixture_document(reviewer[0], lambda state: state['fresh_review']['requests'][0]['dispatch'].update(
        deadline_at='2000-01-01T00:00:00.000000Z'))


@pytest.mark.parametrize('cancel', ['cancelled', 'failed', 'unknown', 'unavailable'])
def test_live_running_reconcile_before_deadline_is_read_only_then_cancels_before_abandon(reviewer, run_cli, cancel):
    from .test_issue879_completion_cli import _public_bytes
    root, _, _, journal = reviewer
    running = invoke(run_cli, reviewer)
    stored = json.loads(journal.read_text())
    stored.update(output=None, process_exited=False)
    journal.write_text(json.dumps(stored))
    before = _public_bytes(root)
    assert invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one') == running
    assert _public_bytes(root) == before
    assert not journal.with_suffix('.cancel').exists()
    _expire_dispatch(reviewer)
    registry = root / 'adapter-install/config/mission/fresh-review-adapters.json'
    registration = registry.read_bytes()
    if cancel == 'unavailable':
        registry.unlink()
    if cancel != 'cancelled':
        _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', reviewer[1]['request_id'], '--adapter', 'neutral'],
                          'fresh-review-kill-unconfirmed', env={**reviewer[2], 'MISSION_OPERATION_ID': 'reconcile-two', 'FIXTURE_CANCEL': cancel})
    registry.write_bytes(registration)
    abandoned = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-two')
    assert abandoned['status'] == 'abandoned-unknown'
    assert abandoned['result']['reason'] == 'output-unobservable'
    assert journal.with_suffix('.cancel').read_text() == 'running'
    assert json.loads(journal.read_text())['count'] == 1


@pytest.mark.parametrize('takeover', [False, True], ids=['same-lease', 'takeover'])
@pytest.mark.parametrize('expired', [False, True], ids=['before-deadline', 'after-deadline'])
def test_reconcile_inflight_launch_obeys_deadline_and_writer_fence(reviewer, run_cli, takeover, expired):
    import time
    from concurrent.futures import ThreadPoolExecutor
    from .test_issue879_completion_cli import _public_bytes, _rewrite_fixture_document
    root, request, env, journal = reviewer
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_cli, 'fresh-review', 'run', '--request', request['request_id'], '--adapter', 'neutral',
                             cwd=root, env_extra={**env, 'FIXTURE_REVIEW_MODE': 'in-flight'})
        try:
            deadline = time.monotonic() + 30
            while not journal.with_suffix('.launching').exists():
                assert not future.done() and time.monotonic() < deadline
                time.sleep(.01)
            if takeover:
                _rewrite_fixture_document(root, lambda state: state.update(lease_expires_at='2000-01-01T00:00:00Z'))
                env = {**env, 'MISSION_LEASE_ID': 'takeover-lease'}
                prepared = run_cli('fresh-review', 'prepare', '--perspective', 'counterexamples',
                    '--adapter-registration-digest', request['adapter_registration_digest'], cwd=root,
                    env_extra={**env, 'MISSION_OPERATION_ID': 'prepare-takeover'})
                assert prepared.returncode == 0, prepared.stdout + prepared.stderr
            if expired:
                _expire_dispatch(reviewer)
            before = _public_bytes(root)
            result = run_cli('fresh-review', 'reconcile', '--request', request['request_id'], '--adapter', 'neutral',
                             cwd=root, env_extra={**env, 'MISSION_OPERATION_ID': 'reconcile-in-flight'})
            assert result.returncode == 0, result.stdout + result.stderr
            assert json.loads(result.stdout)['record']['status'] == ('abandoned-unknown' if expired else 'dispatch-unknown')
            if expired:
                assert journal.with_suffix('.cancel').read_text() == 'dispatch-unknown'
            else:
                assert _public_bytes(root) == before and not journal.with_suffix('.cancel').exists()
        finally:
            journal.with_suffix('.release').touch()
        launched = future.result(timeout=30)
    if expired:
        assert launched.returncode == 2 and not journal.exists()
        return
    if takeover:
        assert launched.returncode == 2 and ('fresh-review-stale-fence' in launched.stderr or 'lease held' in launched.stderr)
        assert invoke(run_cli, (root, request, env, journal), 'reconcile', MISSION_OPERATION_ID='reconcile-after')['status'] == 'running'
    else:
        assert launched.returncode == 0 and json.loads(launched.stdout)['record']['status'] == 'running'
    assert json.loads(journal.read_text())['count'] == 1


@pytest.mark.parametrize('parent', ['', 'bad parent', 123])
def test_invalid_parent_observation_has_identity_reason(monkeypatch, parent):
    import fresh_review_host as host
    from mission_kernel.fresh_review import FreshReviewError
    monkeypatch.setattr(host, '_call', lambda *a, **k: {'parent': {'parent_identity': parent}})
    with pytest.raises(FreshReviewError, match='^fresh-review-identity-unobservable$'):
        host.observe(None)


@pytest.mark.parametrize('exited', [False, 'true', 1])
def test_unconfirmed_child_exit_does_not_abandon_before_deadline(reviewer, run_cli, exited):
    from .test_issue879_completion_cli import _public_bytes
    root, _, _, journal = reviewer
    running = invoke(run_cli, reviewer)
    stored = json.loads(journal.read_text())
    stored.update(output=None, process_exited=exited)
    journal.write_text(json.dumps(stored))
    before = _public_bytes(root)
    record = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one', FIXTURE_CANCEL='unknown')
    assert record == running and _public_bytes(root) == before
    assert not journal.with_suffix('.cancel').exists()


def test_reconcile_capacity_refuses_before_adapter_recover(reviewer, run_cli):
    from .test_issue879_completion_cli import _rewrite_fixture_document
    from mission_kernel.json_codec import STATE_LIMIT
    root, request, env, journal = reviewer
    invoke(run_cli, reviewer)
    _rewrite_fixture_document(root, lambda state: state.update(capacity_fixture_padding='x' * (STATE_LIMIT - 200000)))
    _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', request['request_id'], '--adapter', 'neutral'],
                      'state-capacity-exhausted', env={**env, 'MISSION_OPERATION_ID': 'reconcile-one'})
    assert not journal.with_suffix('.recover').exists()


@pytest.mark.parametrize('status', ['running', 'dispatch-unknown'])
def test_reconcile_rejects_another_requests_prepare_and_launch_operations(reviewer, run_cli, status):
    root, request, env, journal = reviewer
    invoke(run_cli, reviewer, **({'FIXTURE_REVIEW_MODE': 'crash'} if status == 'dispatch-unknown' else {}))
    original_child = journal.read_text()
    prepared = run_cli('fresh-review', 'prepare', '--perspective', 'counterexamples',
        '--adapter-registration-digest', request['adapter_registration_digest'], cwd=root,
        env_extra={**env, 'MISSION_OPERATION_ID': 'prepare-other'})
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    other = (root, json.loads(prepared.stdout)['request'], env, journal)
    invoke(run_cli, other, MISSION_OPERATION_ID='dispatch-other', FIXTURE_REVIEW_MODE='crash')
    other_running = invoke(run_cli, other, 'reconcile', MISSION_OPERATION_ID='launch-other')
    assert other_running['launch_operation_id'] == 'launch-other'
    journal.write_text(original_child)
    journal.with_suffix('.recover').unlink()
    for operation in ('prepare-one', 'prepare-other', 'launch-other'):
        _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', request['request_id'],
                          '--adapter', 'neutral'], 'fresh-review-operation-conflict',
                          env={**env, 'MISSION_OPERATION_ID': operation})
        assert not journal.with_suffix('.recover').exists()


@pytest.mark.parametrize('command', ['run', 'reconcile'])
def test_candidate_capture_error_is_cli_rejection(reviewer, run_cli, command):
    root, request, env, _ = reviewer
    if command == 'reconcile':
        invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE='crash')
    (root / 'app.txt').unlink()
    (root / 'app.txt').symlink_to('missing-input')
    _reject_unchanged(run_cli, root, ['fresh-review', command, '--request', request['request_id'], '--adapter', 'neutral'],
                      'candidate-special-file', env={**env, 'MISSION_OPERATION_ID': command + '-invalid'})
