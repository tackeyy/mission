"""D2c stops at running/blocked/abandoned: output import belongs to D2."""
import json

import pytest

from .test_issue879_completion_cli import completion_session, _reject_unchanged
from .test_issue895_fresh_review import _prepare


def test_unregistered_launch_is_consumed_blocked_not_completed(completion_session, run_cli):
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


def test_crash_reconcile_after_lease_takeover_never_launches_twice(reviewer, run_cli):
    from .test_issue879_completion_cli import _rewrite_fixture_document
    root, _, _, journal = reviewer
    unknown = invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE='crash')
    assert unknown['status'] == 'dispatch-unknown'
    assert json.loads(journal.read_text())['count'] == 1
    _rewrite_fixture_document(root, lambda state: state.update(lease_expires_at='2000-01-01T00:00:00Z'))
    running = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one', MISSION_LEASE_ID='takeover-lease')
    assert running['status'] == 'running'
    assert running['launch']['fencing_epoch'] == unknown['dispatch']['fencing_epoch']
    assert json.loads(journal.read_text())['count'] == 1
    assert running['result'] is None  # D2c does not import collected output.


def test_unknown_without_host_output_is_abandoned_with_split_epochs(reviewer, run_cli):
    from .test_issue879_completion_cli import _rewrite_fixture_document
    root, _, _, journal = reviewer
    unknown = invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE='crash')
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



@pytest.mark.parametrize('mode,status,independent', [
    ('inline', 'running', False), ('unobservable', 'blocked', None), ('provider-invalid', 'blocked', None),
])
def test_host_observation_controls_identity_and_independence(reviewer, run_cli, mode, status, independent):
    record = invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE=mode)
    assert record['status'] == status
    assert record['independent'] is independent
    if status == 'blocked':
        assert record['result']['reason'] == ('launch-invalid' if mode == 'provider-invalid' else 'identity-unobservable')
        assert record['result']['launch_attempted'] is True
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
    backing = state.legacy_passthrough.thaw()
    backing['fresh_review'] = {'schema': 'mission-fresh-review/1', 'requests': [old]}
    state = replace(state, fresh_review=projection, legacy_passthrough=freeze_json_value(backing))
    command = RecordFreshReviewLaunch(request['request_id'], old['dispatch']['operation_id'],
        old['dispatch']['fencing_epoch'], freeze_json_value(json.loads(journal.read_text())['launch']), request['candidate_digest'])
    decision = decide(state, command)
    assert not decision.accepted and decision.rejection.code == 'fresh-review-stale-fence'
    assert decide(state, replace(command, operation_id='new-writer', fencing_epoch=state.lease.fencing_epoch)).accepted


def test_crash_after_intent_before_spawn_abandons_without_launch(reviewer, run_cli):
    record = invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE='intent-crash')
    assert record['status'] == 'dispatch-unknown'
    assert not reviewer[3].exists()
    recovered = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one')
    assert recovered['status'] == 'abandoned-unknown'
    assert not reviewer[3].exists()


def test_reconcile_rechecks_candidate_and_rejects_foreign_child(reviewer, run_cli):
    from .test_issue879_completion_cli import _public_bytes
    root, request, env, journal = reviewer
    unknown = invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE='crash')
    stored = json.loads(journal.read_text())
    stored['launch']['operation_id'] = 'foreign-child'
    journal.write_text(json.dumps(stored))
    _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', request['request_id'], '--adapter', 'neutral'],
                      'launch-binding-mismatch', env={**env, 'MISSION_OPERATION_ID': 'reconcile-one'})
    stored['launch']['operation_id'] = unknown['dispatch']['operation_id']
    journal.write_text(json.dumps(stored))
    (root / 'app.txt').write_text('candidate changed')
    _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', request['request_id'], '--adapter', 'neutral'],
                      'fresh-review-stale', env={**env, 'MISSION_OPERATION_ID': 'reconcile-one'})
    assert json.loads(journal.read_text())['count'] == 1


def test_shared_launch_boundary_rejects_changed_bindings_and_unenforced_capabilities():
    import copy
    from mission_kernel.fresh_review_dispatch import validate_launch
    from mission_kernel.fresh_review import FreshReviewError, canonical_digest, request_document
    from .test_issue895_fresh_review import _pure_projection, ADAPTER
    from .test_issue909_fresh_review_receipts import launch_document
    request = _pure_projection().requests[0].request
    raw = launch_document()
    raw['request_digest'] = canonical_digest(request_document(request))
    dispatch = dict(operation_id=raw['operation_id'], fencing_epoch=raw['fencing_epoch'], parent_identity='parent')
    assert validate_launch(request, dispatch, raw)[1] is True
    # 55 hostile inputs at the shared boundary, rather than CLI table copies.
    for field in ('request_id', 'request_digest', 'nonce', 'operation_id', 'fencing_epoch',
                  'adapter_registration_digest', 'parent_identity', 'child_identity', 'context_identity',
                  'received_input_digest', 'enforced_tools'):
        for bad in (None, {}, [], True, 'foreign'):
            value = copy.deepcopy(raw)
            value[field] = bad
            # Different observable child/context ids are legitimate host evidence.
            if bad == 'foreign' and field in ('child_identity', 'context_identity') or bad == [] and field == 'enforced_tools':
                assert validate_launch(request, dispatch, value)[1]
            else:
                with pytest.raises(FreshReviewError):
                    validate_launch(request, dispatch, value)
    value = copy.deepcopy(raw)
    value['enforced_tools'] = ['read-candidate']  # Request grants no tools.
    with pytest.raises(FreshReviewError, match='capability-unenforceable'):
        validate_launch(request, dispatch, value)
    value = copy.deepcopy(raw)
    value['received_input_digest'] = ADAPTER
    with pytest.raises(FreshReviewError, match='launch-binding-mismatch'):
        validate_launch(request, dispatch, value)


def test_public_dispatch_schema_names_the_incomplete_saga(tmp_path, run_cli):
    for command in ('fresh-review-run', 'fresh-review-reconcile'):
        result = run_cli('schema', '--contract', command, cwd=tmp_path)
        assert result.returncode == 0, result.stdout + result.stderr
        schema = json.loads(result.stdout)
        assert schema['closed'] is True
        assert schema['terminal_outcomes'] == ['blocked', 'abandoned-unknown']


def test_fenced_out_launch_writer_cannot_cancel_the_takeover_child(tmp_path, monkeypatch):
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
        now=lambda: '2026-01-01T00:00:01+00:00', fail=lambda code, _: (_ for _ in ()).throw(FreshReviewError(code)))
    monkeypatch.setattr(app, '_candidate', lambda *a: request.candidate_digest)
    raw = launch_document()
    raw['request_digest'] = canonical_digest(request_document(request))
    cancelled = []
    host = Namespace(resolve=lambda _: Namespace(registration=Namespace(digest=ADAPTER)),
        observe=lambda _: {'parent_identity': 'parent'}, launch=lambda *a: {'launch_receipt': raw},
        cancel=lambda *a: cancelled.append(True) or 'cancelled')

    def execute(_, build):
        command = build(state)
        if isinstance(command, app.BeginFreshReviewDispatch):
            record = replace(projection.requests[0], status='dispatch-unknown', operation_id=command.operation_id,
                intent_digest=command.intent_digest, payload_digest=command.payload_digest, dispatch=command.dispatch)
            state['fresh_review'] = projection_document(replace(projection, requests=(record,)))
            return record
        raise FreshReviewError('fresh-review-stale-fence')
    monkeypatch.setattr(app, '_execute', execute)
    with pytest.raises(FreshReviewError, match='stale-fence'):
        app.run_fresh_review_dispatch_cli(Namespace(request=request.request_id, adapter='neutral', fresh_review_command='run'), services, host)
    assert cancelled == []


def test_host_confirms_launch_impossible_before_child_spawn(reviewer, run_cli):
    record = invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE='unavailable')
    assert record['status'] == 'blocked'
    assert record['result']['reason'] == 'launch-unavailable'
    assert record['result']['launch_attempted'] is False
    assert not reviewer[3].exists()
