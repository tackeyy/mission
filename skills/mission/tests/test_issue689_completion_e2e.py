"""D3: public subprocess producers, never direct acceptance-state fixtures.

The only persistence arrangement is the existing v5 container genesis helper:
it wraps the publicly initialized v4 payload before any acceptance evidence.
Registered children read the immutable packet and return output through the
host adapter; only fresh-review import publishes coverage and finding bytes.
"""
import copy
import hashlib
import json
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from mission_kernel.fresh_review import canonical_digest
from .conftest import canonical_review, write_canonical_review_aggregate
from .test_issue878_candidate_snapshot import _commit_candidate
from .test_issue878_verification_runner import _contract, _replay_policy
from .test_issue879_completion_cli import _persist_fixture, _public_bytes, _reject_unchanged


def test_registered_fixture_launch_skips_withdrawn_tombstone(tmp_path, monkeypatch):
    """A withdrawn reservation must not break the next registered child launch."""
    from mission_kernel.fresh_review import WithdrawnFreshReviewRecord, request_document, projection_document
    from mission_kernel.json_codec import freeze_json_value
    from .test_issue895_fresh_review import _pure_projection, ADAPTER

    spec = importlib.util.spec_from_file_location('neutral_adapter',
        Path(__file__).parent / 'fixtures/fresh_review_adapter.py')
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    projection = _pure_projection()
    request = request_document(projection.requests[0].request)
    tombstone = WithdrawnFreshReviewRecord('old-request', 'old-nonce', 'prepare-old', ADAPTER,
        ('AC1',), 'withdraw-old', 1)
    envelope = dict(operation_id='dispatch-next', fencing_epoch=1)
    record = projection_document(projection)['requests'][0]
    record.update(status='dispatch-unknown', dispatch=envelope)
    withdrawn = projection_document(type(projection)((tombstone,)))['requests'][0]
    monkeypatch.setattr(adapter, '_state', lambda: dict(fresh_review=dict(requests=[withdrawn, record])))
    monkeypatch.setenv('FIXTURE_REVIEW_STATE', str(tmp_path / '.mission-state'))
    monkeypatch.setenv('FIXTURE_REVIEW_JOURNAL', str(tmp_path / 'journal.json'))
    monkeypatch.setenv('FIXTURE_REVIEW_MODE', '')
    packet = tmp_path / 'input.json'
    packet.write_text('{}')
    launch = adapter.Adapter().launch(json.dumps(request).encode(),
        SimpleNamespace(relative_path=str(packet)), (), freeze_json_value(envelope)).thaw()
    assert launch['request_id'] == request['request_id']
    assert launch['context_mode'] == 'fresh'
    assert launch['received_input_digest'] == canonical_digest({})


@pytest.fixture(params=[4, 5], ids=['v4-flat', 'v5-container'])
def session(request, tmp_path, raw_run_cli):
    root = tmp_path
    _commit_candidate(root, {'app.txt': 'candidate', 'candidate.txt': 'initial'})
    raw_run_cli('init', 'Run the registered verifier.', '--force-mission',
                '--artifact-applicability', 'not-applicable', cwd=root, check=True)
    state = json.loads(raw_run_cli('get', cwd=root, check=True).stdout)
    if request.param == 5:
        _persist_fixture(root, state, 5)
    policy = _replay_policy()
    # The normal verifier observes candidate bytes; a changed candidate gives
    # a genuine latest failed receipt rather than an injected history entry.
    policy['commands'][0]['argv'][2] = "from pathlib import Path; assert Path('app.txt').read_text() == 'candidate'"
    encoded = json.dumps(policy, sort_keys=True, separators=(',', ':')).encode()
    directory = root / '.mission'
    directory.mkdir()
    (directory / 'verifiers.json').write_bytes(encoded)
    contract = _contract(state['mission_id'])
    second = copy.deepcopy(contract['criteria'][0])
    second.update(id='AC2', expected='Second required criterion was checked')
    contract['criteria'].append(second)
    contract['verifier_policy_digest'] = 'sha256:' + hashlib.sha256(encoded).hexdigest()
    source = root / 'contract.json'
    source.write_text(json.dumps(contract))
    raw_run_cli('acceptance-contract', 'import', '--input', str(source), cwd=root, check=True)
    installed = root / 'adapter-install'
    installed.mkdir()
    module = installed / 'neutral_adapter.py'
    module.write_bytes((Path(__file__).parent / 'fixtures/fresh_review_adapter.py').read_bytes())
    dist = installed / 'neutral_adapter-1.0.dist-info'
    dist.mkdir()
    (dist / 'METADATA').write_text('Metadata-Version: 2.1\nName: neutral-adapter\nVersion: 1.0\n')
    (dist / 'entry_points.txt').write_text('[mission.fresh_review_adapters]\nneutral = neutral_adapter:factory\n')
    registration = dict(id='neutral', entry_point='neutral', distribution='neutral-adapter', version='1.0',
                        source_digest='sha256:' + hashlib.sha256(module.read_bytes()).hexdigest())
    config = installed / 'config/mission'
    config.mkdir(parents=True)
    (config / 'fresh-review-adapters.json').write_text(json.dumps(dict(
        schema='mission-fresh-review-adapter-registry/1', adapters=[registration])))
    env = dict(PYTHONPATH=str(installed), XDG_CONFIG_HOME=str(config.parent),
               FIXTURE_REVIEW_JOURNAL=str(installed / 'journal.json'),
               FIXTURE_REVIEW_STATE=str(root / '.mission-state'))
    return root, env, canonical_digest(registration)


def verify(run, session, *criteria):
    for criterion in criteria or ('AC1', 'AC2'):
        run('verification', 'run', '--criterion', criterion, cwd=session[0], check=True)


def prepare(run, session, attempt='one', criteria=()):
    root, env, digest = session
    args = [argument for criterion in criteria for argument in ('--criterion', criterion)]
    result = run('fresh-review', 'prepare', '--perspective', 'counterexamples',
                 '--adapter-registration-digest', digest, *args, cwd=root, check=True,
                 env_extra={**env, 'MISSION_OPERATION_ID': 'prepare-' + attempt})
    return json.loads(result.stdout)['request']['request_id']


def review(run, session, mode='completion-clean', attempt='one', criteria=(), *, import_result=True):
    root, env, _ = session
    request_id = prepare(run, session, attempt, criteria)
    result = run('fresh-review', 'run', '--request', request_id, '--adapter', 'neutral', cwd=root,
                 env_extra={**env, 'MISSION_OPERATION_ID': 'run-' + attempt, 'FIXTURE_REVIEW_MODE': mode}, check=True)
    record = json.loads(result.stdout)['record']
    assert record['status'] == 'running'
    if import_result:
        result = run('fresh-review', 'import', '--request', request_id, '--adapter', 'neutral', cwd=root,
                     env_extra={**env, 'MISSION_OPERATION_ID': 'import-' + attempt}, check=True)
        record = json.loads(result.stdout)['record']
    return record


def score(run, root):
    # Existing content-bound review aggregate/scoring fixture; push-score still
    # goes through the public executable, with its ordinary provenance checks.
    _, ref, claim = write_canonical_review_aggregate(root, [canonical_review({})])
    head = (root / '.git/HEAD').read_text().strip()
    sha = (root / '.git' / head.removeprefix('ref: ')).read_text().strip()
    ref['revision_scope'] = dict(kind='git', base_sha=sha, head_sha=sha)
    scoring = root / 'score.json'
    scoring.write_text(json.dumps(dict(items=claim['items'], open_high=claim['open_high'],
        review_agreement=claim['review_agreement'], agreement_detail=claim['agreement_detail'],
        findings_evidence_path=ref['path'], score_provenance=dict(score_source='scoring-json',
            review_evidence_ref=ref, revision_scope=ref['revision_scope']))))
    result = run('push-score', '--iteration', '1', '--scoring-json', str(scoring), cwd=root)
    assert result.returncode == 0, result.stdout + result.stderr


def test_public_review_import_aggregate_score_completion_preserves_fresh_receipt(session, raw_run_cli):
    """Ordinary score publication must retain D's completed receipt until completion."""
    root, _, _ = session
    verify(raw_run_cli, session)
    completed = review(raw_run_cli, session)
    source = root / 'ordinary-review.json'
    source.write_text(json.dumps(canonical_review({}, perspective='quality')))
    imported = raw_run_cli('review-import', '--iteration', '1', '--input', str(source),
                           cwd=root, check=True)
    reference = json.loads(imported.stdout)['review_evidence_ref']['path']
    state = json.loads(raw_run_cli('get', cwd=root, check=True).stdout)
    assert state['fresh_review']['requests'] == [completed]
    head = (root / '.git/HEAD').read_text().strip()
    reviewed_sha = (root / '.git' / head.removeprefix('ref: ')).read_text().strip()
    scoring = root / 'public-score.json'
    raw_run_cli('aggregate-reviews', '--iteration', '1', '--input-ref', reference,
                '--min-reviewers', '1', '--base-sha', reviewed_sha, '--head-sha', reviewed_sha,
                '--out', str(scoring), cwd=root, check=True)
    raw_run_cli('push-score', '--iteration', '1', '--scoring-json', str(scoring), cwd=root, check=True)
    raw_run_cli('mark-passes', cwd=root, check=True)
    state = json.loads(raw_run_cli('get', cwd=root, check=True).stdout)
    assert state['fresh_review']['requests'] == [completed]
    assert (state['passes'], state['loop_active'], state['phase'], state['terminal_outcome']) == (
        True, False, 'done', 'completed_pass')


@pytest.mark.parametrize('command', ['mark-passes', 'closeout'])
def test_public_completion_requires_generated_receipts_and_keeps_score_gate(session, raw_run_cli, command):
    root, _, _ = session
    verify(raw_run_cli, session)
    record = review(raw_run_cli, session)
    assert record['status'] == 'completed' and record['independent'] is True
    assert record['request']['criterion_ids'] == ['AC1', 'AC2']
    state = json.loads(raw_run_cli('get', cwd=root, check=True).stdout)
    assert state['acceptance_contract']['coverage']['status'] == 'pending'
    assert len(state['verification_receipts']) == 2
    assert state['passes'] is False
    assert record['result']['coverage_receipt']['status'] == 'valid'
    _reject_unchanged(raw_run_cli, root, ['mark-passes'], '採点未実施')
    score(raw_run_cli, root)
    result = raw_run_cli(command, cwd=root)
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(raw_run_cli('get', cwd=root, check=True).stdout)
    assert (state['passes'], state['loop_active'], state['phase'], state['terminal_outcome']) == (
        True, False, 'done', 'completed_pass')


def test_public_already_passed_contract_closeout_has_no_success_shortcut(session, raw_run_cli):
    """Generated clean receipts retain C's refusal of an already-pass shortcut."""
    root, _, _ = session
    verify(raw_run_cli, session)
    review(raw_run_cli, session)
    score(raw_run_cli, root)
    raw_run_cli('mark-passes', cwd=root, check=True)
    _reject_unchanged(raw_run_cli, root, ['closeout'],
                      'acceptance contract requires completion revalidation')


@pytest.mark.parametrize('case,reason', [
    ('missing-second-verifier', 'acceptance-receipt-missing'),
    ('latest-failed-verifier', 'acceptance-receipt-not-passed'),
    ('missing-review', 'acceptance-fresh-review-missing'),
    ('partial-review', 'acceptance-fresh-review-missing'),
    ('inline', 'acceptance-fresh-review-non-independent'),
    ('finding', 'acceptance-unresolved-finding'),
    ('running', 'acceptance-fresh-review-pending'),
    ('stale-candidate', 'acceptance-receipt-stale'),
    ('stale-review', 'acceptance-fresh-review-stale'),
    ('open-coverage', 'acceptance-coverage-open'),
    ('new-pending', 'acceptance-fresh-review-pending'),
    ('new-failed', 'acceptance-coverage-open'),
    ('new-partial-pending', 'acceptance-fresh-review-pending'),
    ('new-partial-open', 'acceptance-coverage-open'),
    ('old-finding', 'acceptance-unresolved-finding'),
])
def test_public_completion_rejects_missing_or_superseded_evidence_atomically(session, raw_run_cli, case, reason):
    root, _, _ = session
    verify(raw_run_cli, session, *(('AC1',) if case == 'missing-second-verifier' else ()))
    if case != 'missing-review':
        mode = {'inline': 'completion-inline', 'finding': 'counterexample',
                'open-coverage': 'completion-open', 'old-finding': 'counterexample'}.get(case, 'completion-clean')
        # A later clean whole review must not erase an earlier open finding.
        if case == 'old-finding':
            review(raw_run_cli, session, mode)
            review(raw_run_cli, session, attempt='whole')
        else:
            review(raw_run_cli, session, mode, criteria=('AC1',) if case == 'partial-review' else (),
                   import_result=case != 'running')
    if case in ('stale-candidate', 'latest-failed-verifier'):
        (root / 'app.txt').write_text('changed candidate')
        if case == 'latest-failed-verifier':
            verify(raw_run_cli, session, 'AC1')
            (root / 'app.txt').write_text('candidate')
    if case == 'stale-review':
        (root / 'candidate.txt').write_text('changed candidate')
        verify(raw_run_cli, session)
    if case in ('new-pending', 'new-partial-pending'):
        prepare(raw_run_cli, session, 'two', ('AC2',) if case == 'new-partial-pending' else ())
    if case == 'new-failed':
        failed = review(raw_run_cli, session, 'completion-failed', 'two')
        assert failed['status'] == 'failed'
    if case == 'new-partial-open':
        partial = review(raw_run_cli, session, 'completion-open', 'two', criteria=('AC1',))
        assert partial['status'] == 'completed'
        assert partial['request']['criterion_ids'] == ['AC1']
        assert partial['result']['coverage_receipt']['status'] == 'open'
    score(raw_run_cli, root)
    _reject_unchanged(raw_run_cli, root, ['mark-passes'], reason)
    _reject_unchanged(raw_run_cli, root, ['closeout'], reason)


def test_public_completion_rejects_partial_open_before_clean_whole_review_atomically(session, raw_run_cli):
    """A later clean whole review must not erase an earlier partial open obligation."""
    root, _, _ = session
    verify(raw_run_cli, session)
    partial = review(raw_run_cli, session, 'completion-open', 'one', criteria=('AC1',))
    assert partial['status'] == 'completed'
    assert partial['request']['criterion_ids'] == ['AC1']
    assert partial['result']['coverage_receipt']['status'] == 'open'
    whole = review(raw_run_cli, session, 'completion-clean', 'two')
    assert whole['status'] == 'completed'
    assert whole['request']['criterion_ids'] == ['AC1', 'AC2']
    assert whole['result']['coverage_receipt']['status'] == 'valid'
    score(raw_run_cli, root)
    _reject_unchanged(raw_run_cli, root, ['mark-passes'], 'acceptance-coverage-open')


@pytest.mark.parametrize('schema', [4, 5], ids=['v4-flat', 'v5-container'])
def test_public_contract_key_absent_legacy_still_completes(tmp_path, raw_run_cli, schema):
    _commit_candidate(tmp_path, {'app.txt': 'candidate'})
    raw_run_cli('init', 'Legacy completion.', '--force-mission',
                '--artifact-applicability', 'not-applicable', cwd=tmp_path, check=True)
    state = json.loads(raw_run_cli('get', cwd=tmp_path, check=True).stdout)
    assert 'acceptance_contract' not in state
    if schema == 5:
        _persist_fixture(tmp_path, state, 5)
    score(raw_run_cli, tmp_path)
    raw_run_cli('mark-passes', cwd=tmp_path, check=True)
    assert json.loads(raw_run_cli('get', cwd=tmp_path, check=True).stdout)['passes'] is True
    before = _public_bytes(tmp_path)
    raw_run_cli('closeout', cwd=tmp_path, check=True)
    assert _public_bytes(tmp_path) == before
