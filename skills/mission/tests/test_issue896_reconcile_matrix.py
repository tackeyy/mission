"""Public sender/deadline decision table, independently scheduled by loadfile."""
import json

import pytest

from .completion_cli_fixtures import completion_cli_code, completion_template, run_cli
from .test_issue912_fresh_review_dispatch import completion_session, reviewer, invoke
from .test_issue879_completion_cli import _reject_unchanged


@pytest.mark.parametrize('status', ['running', 'dispatch-unknown'])
@pytest.mark.parametrize('deadline', ['before', 'after'])
@pytest.mark.parametrize('sender', ['matching', 'foreign', 'wrong-type', 'missing'])
@pytest.mark.parametrize('process', ['exited', 'live'])
@pytest.mark.parametrize('body', ['output', 'none'])
def test_reconcile_sender_deadline_table(reviewer, run_cli, status, deadline, sender, process, body):
    """Recovery cannot publish a foreign report or strand an optional-field adapter."""
    from .test_issue912_fresh_review_dispatch import _expire_dispatch
    root, request, env, journal = reviewer
    old = invoke(run_cli, reviewer, **({'FIXTURE_REVIEW_MODE': 'crash'} if status == 'dispatch-unknown' else {}))
    stored = json.loads(journal.read_text())
    stored.update(process_exited=process == 'exited', output='invalid' if body == 'output' else None,
                  exit_code=0, budget_used=dict(wall_time_sec=1, tool_calls=0, replays=0,
                                              output_bytes=7 if body == 'output' else 0))
    if sender == 'foreign':
        stored['observation_updates'] = {'child_identity': 'foreign'}
    elif sender == 'wrong-type':
        stored['observation_updates'] = {'fencing_epoch': True}
    elif sender == 'missing':
        stored['minimal_observation'] = True
    journal.write_text(json.dumps(stored))
    if deadline == 'after':
        _expire_dispatch(reviewer)
    if deadline == 'before' and sender in ('foreign', 'wrong-type'):
        _reject_unchanged(run_cli, root, ['fresh-review', 'reconcile', '--request', request['request_id'],
            '--adapter', 'neutral'], 'fresh-review-output-sender-mismatch',
            env={**env, 'MISSION_OPERATION_ID': 'reconcile-table'})
        assert not journal.with_suffix('.cancel').exists()
        return
    record = invoke(run_cli, reviewer, 'reconcile', MISSION_OPERATION_ID='reconcile-table')
    ignored = deadline == 'after' and sender in ('foreign', 'wrong-type')
    failed = process == 'exited' and not ignored
    cancelled = deadline == 'after' and not failed
    assert record['status'] == ('failed' if failed else 'abandoned-unknown' if cancelled else 'running')
    assert journal.with_suffix('.cancel').exists() == cancelled
    if ignored:
        assert record.get('launch') == old.get('launch')
        assert journal.with_suffix('.cancel').read_text() == status
    if failed:
        assert record['result']['reason'] == 'output-invalid'
        if body == 'none':
            assert 'output_ref' not in record['result']
    if cancelled:
        assert 'output_ref' not in record['result']
    assert json.loads(journal.read_text())['count'] == 1

