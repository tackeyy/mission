"""Public fresh-review recovery boundaries, independently scheduled by loadfile."""
import json

import pytest

from .completion_cli_fixtures import completion_cli_code, completion_template, run_cli
from .test_issue912_fresh_review_dispatch import completion_session, reviewer, invoke
from .test_issue879_completion_cli import _reject_unchanged
from .test_issue896_publish import exited


@pytest.mark.parametrize('process_exited', [True, False], ids=['exited', 'live'])
@pytest.mark.parametrize('field', ['operation_id', 'fencing_epoch', 'request_id', 'nonce', 'child_identity'])
@pytest.mark.parametrize('status', ['dispatch-unknown', 'running'])
def test_reconcile_rejects_foreign_sender_before_any_launch_commit(reviewer, run_cli, field, status, process_exited):
    root, request, env, journal = reviewer
    invoke(run_cli, reviewer, **({'FIXTURE_REVIEW_MODE': 'crash'} if status == 'dispatch-unknown' else {}))
    exited(journal)
    stored = json.loads(journal.read_text())
    stored['process_exited'] = process_exited
    stored['observation_updates'] = {field: 999 if field == 'fencing_epoch' else 'foreign'}
    journal.write_text(json.dumps(stored))
    _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', request['request_id'],
        '--adapter', 'neutral'], 'fresh-review-output-sender-mismatch',
        env={**env, 'MISSION_OPERATION_ID': 'reconcile-one'})
    assert not journal.with_suffix('.cancel').exists()


@pytest.mark.parametrize('size', [3, 262145], ids=['small', 'over-import-limit'])
@pytest.mark.parametrize('cancel', ['cancelled', 'unknown'])
def test_live_output_does_not_bypass_deadline_or_confirmed_cancellation(reviewer, run_cli, size, cancel):
    from .test_issue912_fresh_review_dispatch import _expire_dispatch
    from .test_issue879_completion_cli import _public_bytes
    root, request, env, journal = reviewer
    running = invoke(run_cli, reviewer)
    stored = json.loads(journal.read_text())
    stored.update(output='x'*size, process_exited=False)
    journal.write_text(json.dumps(stored))
    before = _public_bytes(root)
    assert invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one') == running
    assert _public_bytes(root) == before
    _expire_dispatch(reviewer)
    if cancel == 'unknown':
        _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', request['request_id'],
            '--adapter', 'neutral'], 'fresh-review-kill-unconfirmed',
            env={**env, 'MISSION_OPERATION_ID': 'reconcile-two', 'FIXTURE_CANCEL': cancel})
    else:
        result = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-two', FIXTURE_CANCEL=cancel)
        assert result['status'] == 'abandoned-unknown'
        assert result['result']['reason'] == 'output-unobservable'
        assert 'output_ref' not in result['result']
    assert journal.with_suffix('.cancel').read_text() == 'running'


@pytest.mark.parametrize('status', ['running', 'dispatch-unknown'])
@pytest.mark.parametrize('report', ['foreign-child', 'minimal'])
@pytest.mark.parametrize('output', [None, 'partial'])
@pytest.mark.parametrize('cancel', ['cancelled', 'unknown'])
def test_expired_nonimport_report_can_cancel_saved_dispatch(reviewer, run_cli, status, report, output, cancel):
    from .test_issue912_fresh_review_dispatch import _expire_dispatch
    root, request, env, journal = reviewer
    old = invoke(run_cli, reviewer, **({'FIXTURE_REVIEW_MODE': 'crash'} if status == 'dispatch-unknown' else {}))
    stored = json.loads(journal.read_text())
    stored.update(process_exited=False, output=output)
    if report == 'foreign-child':
        stored['observation_updates'] = {'child_identity': 'foreign'}
    else:
        stored['minimal_observation'] = True
    journal.write_text(json.dumps(stored))
    _expire_dispatch(reviewer)
    if cancel == 'unknown':
        result = run_cli('fresh-review', 'reconcile', '--request', request['request_id'], '--adapter', 'neutral',
            cwd=root, env_extra={**env, 'MISSION_OPERATION_ID': 'reconcile-one', 'FIXTURE_CANCEL': cancel})
        assert result.returncode == 2 and 'fresh-review-kill-unconfirmed' in result.stderr
        record = json.loads(run_cli('get', cwd=root).stdout)['fresh_review']['requests'][0]
        assert record['status'] == (status if report == 'foreign-child' else 'running')
        assert record.get('result') is None
    else:
        record = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one', FIXTURE_CANCEL=cancel)
        assert record['status'] == 'abandoned-unknown'
        assert record['result']['reason'] == ('child-unobservable' if report == 'foreign-child' else 'output-unobservable')
        assert record['result']['dispatch_operation_id'] == old['dispatch']['operation_id']
        assert 'output_ref' not in record['result']
    assert record.get('launch') == (old.get('launch') if report == 'foreign-child' else old.get('launch') or stored['launch'])
    assert journal.with_suffix('.cancel').read_text() == (status if report == 'foreign-child' else 'running')
    assert json.loads(journal.read_text())['count'] == 1


@pytest.mark.parametrize('status', ['running', 'dispatch-unknown'])
def test_live_minimal_recovery_without_output_does_not_require_import_sender(reviewer, run_cli, status):
    root, _, _, journal = reviewer
    old = invoke(run_cli, reviewer, **({'FIXTURE_REVIEW_MODE': 'crash'} if status == 'dispatch-unknown' else {}))
    stored = json.loads(journal.read_text())
    stored.update(process_exited=False, output=None, minimal_observation=True)
    journal.write_text(json.dumps(stored))
    record = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-one')
    assert record['status'] == 'running'
    assert record['launch'] == (old.get('launch') or stored['launch'])
    assert not journal.with_suffix('.cancel').exists()


@pytest.mark.parametrize('status', ['running', 'dispatch-unknown'])
def test_foreign_exit_cannot_bypass_unconfirmed_cancel(reviewer, run_cli, status):
    from .test_issue912_fresh_review_dispatch import _expire_dispatch
    root, request, env, journal = reviewer
    invoke(run_cli, reviewer, **({'FIXTURE_REVIEW_MODE': 'crash'} if status == 'dispatch-unknown' else {}))
    stored = json.loads(journal.read_text())
    stored.update(process_exited=True, output=None, observation_updates={'child_identity': 'foreign'})
    journal.write_text(json.dumps(stored))
    _expire_dispatch(reviewer)
    _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', request['request_id'],
        '--adapter', 'neutral'], 'fresh-review-kill-unconfirmed',
        env={**env, 'MISSION_OPERATION_ID': 'reconcile-one', 'FIXTURE_CANCEL': 'unknown'})
    assert journal.with_suffix('.cancel').read_text() == status


@pytest.mark.parametrize('receipt', ['no-receipt', 'missing-child', 'null-child'])
def test_partial_or_unobservable_sender_cannot_match_missing_child(reviewer, run_cli, receipt):
    root, request, env, journal = reviewer
    invoke(run_cli, reviewer, FIXTURE_REVIEW_MODE='crash')
    stored = json.loads(journal.read_text())
    launch = dict(stored['launch'])
    launch.pop('child_identity')
    updates = {key: stored['launch'][key] for key in
               ('operation_id', 'fencing_epoch', 'request_id', 'nonce')}
    if receipt == 'null-child':
        launch['child_identity'] = updates['child_identity'] = None
    updates['launch_receipt'] = None if receipt == 'no-receipt' else launch
    stored.update(output=None, process_exited=False, minimal_observation=True, observation_updates=updates)
    journal.write_text(json.dumps(stored))
    _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', request['request_id'],
        '--adapter', 'neutral'], 'fresh-review-output-sender-mismatch',
        env={**env, 'MISSION_OPERATION_ID': 'reconcile-one'})
    assert not journal.with_suffix('.cancel').exists()

