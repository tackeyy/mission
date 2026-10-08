"""I2a: preserved harness evidence rejects altered sessions, even at deadline."""
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / 'benchmarks' / 'mission-vs-goal'
@pytest.fixture(autouse=True)
def module_path(monkeypatch):
    monkeypatch.syspath_prepend(str(BENCH))


def _load_probe():
    spec = importlib.util.spec_from_file_location('probe924', BENCH / 'run_native_goal_probe.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

SCRIPT = '/package/skills/mission/bin/mission-state.py'
PYTHON = '/usr/bin/python3'


def integrity():
    import evaluation_integrity
    return evaluation_integrity


SAFE = [
    f'{SCRIPT} status', f'{PYTHON} {SCRIPT} status',
    f'{SCRIPT} status --input .mission-state/x',
    f'{SCRIPT} verification claims --out /tmp/result',
    f'{SCRIPT} manual-score-capture --out /tmp/result',
    f'{SCRIPT} aggregate-reviews --out /tmp/result',
    f'{SCRIPT} review-finalize --out /tmp/result',
    f'{SCRIPT} context-manifest --out /tmp/result',
    f'{SCRIPT} artifact export --to /tmp/result',
    f'{SCRIPT} status > /tmp/out', f'{SCRIPT} status 2>/tmp/error',
    f'{SCRIPT} status | tee /tmp/out',
    f'{SCRIPT} context-manifest -- --out .mission-state/x',
    f'cd /work/.mission-state; {SCRIPT} context-manifest --out /tmp/out',
    f'(cd /tmp); {SCRIPT} context-manifest --out x',
    'echo normal', 'bash -lc "printf ok"', 'source ./other.sh', '. ./other.sh',
]
BAD = [
    'cp -r .mission-state /tmp/copy', 'rsync -a .mission-state/ /tmp/copy',
    'mv .mission-state/x /tmp/x', 'printf x > .mission-state/x',
    'echo x >> .mission-state/x', 'echo x 2>.mission-state/x',
    'echo x &>.mission-state/x', 'python3 -c "open(\'.mission-state/x\',\'w\')"',
    'python3 - <<EOF\nopen(".mission-state/x")\nEOF',
    'bash <<EOF\ncp .mission-state/x /tmp/x\nEOF',
    'sh <<\'EOF\'\ncp .mission-state/x /tmp/x\nEOF',
    'bash -c "sh -c \'cp .mission-state/x /tmp/x\'"',
    'eval "cp .mission-state/x /tmp/x"', 'eval "$COMMAND"',
    'f(){ cp .mission-state/x /tmp/x; }; f',
    'function f() { cp .mission-state/x /tmp/x; }; f',
    'bash -c "$SCRIPT"', '$COMMAND something', 'echo "unterminated',
    'mission-state.py status .mission-state/x', './mission-state.py status .mission-state/x',
    '/different/mission-state.py status .mission-state/x',
    f'/other/python3 {SCRIPT} status .mission-state/x',
    f'{SCRIPT} reactivate --approved-by-user',
    f'{PYTHON} {SCRIPT} reactivate',
    *[f'{SCRIPT} status {sep} cp .mission-state/x /tmp/x' for sep in (';', '&&', '||', '|', '\n')],
    f'({SCRIPT} status; cp .mission-state/x /tmp/x)',
    f'echo "$({SCRIPT} status; cp .mission-state/x /tmp/x)"',
    *[f'{SCRIPT} {cmd} {opt}' for cmd, opt in (
        ('manual-score-capture', '--out .mission-state/x'),
        ('aggregate-reviews', '--o .mission-state/x'),
        ('review-finalize', '--ou=.mission-state/x'),
        ('context-manifest', '--out=.mission-state/x'),
        ('verification claims', '--out .mission-state/x'),
        ('artifact export', '--t .mission-state/x'),
        ('archive-worktree', '--destination-root /tmp/out'))],
    f'{SCRIPT} status >.mission-state/x',
    f'cd /work/.mission-state; {SCRIPT} context-manifest --out x',
    f'cd "$DIR"; {SCRIPT} context-manifest --out x',
    f'{SCRIPT} context-manifest --out',
]


def record_and_spec():
    m = integrity()
    policy = {'schema': 'mission-budget-policy/1', 'reactivate': 'forbidden', 'external_deadline_at': '2026-01-01T00:00:09Z'}
    conditions = dict(host='codex', arm='mission', model_id='m', effort='high', permissions='p', timeout_seconds=10, max_turns=1, token_budget=None, max_budget_usd=None)
    spec = dict(conditions=conditions, mission_source_commit='verified', package_sha256='pkg', provider_version='v', initial_sha256='starter', budget_policy_template_sha256=m.policy_digest(policy), m_post=1)
    state = dict(session_id='cx-t', mission_id='mid', budget_policy=policy, reactivation_history=[])
    record = dict(arm='mission', manifest=dict(conditions=conditions, mission_source_commit='verified', package={'sha256': 'pkg'}, provider_version='v', task_snapshot={'matches': True}, worker_export={'initial_sha256': 'starter'}), provider_version_after='v', observed_config={'model': 'm', 'reasoningEffort': 'high', 'activePermissionProfile': {'id': 'p'}}, config_matches=True, package_delivery='skill_input', budget_policy=policy, run_started_at='2026-01-01T00:00:00Z', session_init={**copy.deepcopy(state), 'budget_policy_template_sha256': m.policy_digest(policy)}, mission_state=state, thread_id='t', exec_events=[], mission_state_path=SCRIPT, interpreter_path=PYTHON, exec_scan=[], turn_start_sent=1)
    return record, {'mission_verified_complex': spec}


@pytest.mark.parametrize('field,value,reason', [
    ('budget_policy', None, 'evaluated_session_mismatch'),
    ('reactivation_history', [{}], 'evaluated_session_mismatch'),
    ('mission_id', 'replacement', 'evaluated_session_mismatch'),
    ('session_id', 'cx-other', 'evaluated_session_mismatch'),
])
@pytest.mark.parametrize('deadline', [False, True])
def test_post_state_mismatch_is_non_quality(field, value, reason, deadline):
    record, arms = record_and_spec()
    record['deadline_reached'] = deadline
    record['mission_state'][field] = value
    result = integrity().check_record(record, arms)
    assert result['classification'] == 'non_quality'
    assert reason in result['reasons']


@pytest.mark.parametrize('part', ['record_digest', 'record_deadline', 'forbidden', 'state_digest', 'state_deadline', 'init_digest', 'init_record_digest', 'init_identity', 'missing_state', 'missing_stream', 'version', 'config', 'source', 'package', 'return_conflict'])
def test_record_configuration_rejections(part):
    record, arms = record_and_spec()
    record = copy.deepcopy(record)
    if part == 'record_digest': record['budget_policy']['extra'] = True
    elif part == 'record_deadline': record['budget_policy']['external_deadline_at'] = '2026-01-01T00:00:10Z'
    elif part == 'forbidden': del record['budget_policy']['reactivate']
    elif part == 'state_digest': record['mission_state']['budget_policy'] = {'reactivate': 'forbidden'}
    elif part == 'state_deadline': record['mission_state']['budget_policy'] = {**record['budget_policy'], 'external_deadline_at': '2026-01-01T00:00:08Z'}
    elif part == 'init_digest': record['session_init']['budget_policy'] = {}
    elif part == 'init_record_digest': record['session_init']['budget_policy_template_sha256'] = 'other'
    elif part == 'init_identity': record['session_init']['mission_id'] = None
    elif part == 'missing_state': record['mission_state'] = None
    elif part == 'missing_stream': del record['exec_events']
    elif part == 'version': record['provider_version_after'] = 'changed'
    elif part == 'config': del record['observed_config']
    elif part == 'source': record['manifest']['mission_source_commit'] = 'other'
    elif part == 'package': record['manifest']['package']['sha256'] = 'other'
    elif part == 'return_conflict': record['identity_conflicts'] = ['package_delivery']
    assert integrity().check_record(record, arms)['classification'] == 'non_quality'


def test_deadline_only_varies_across_valid_records_and_versions_are_distinct():
    record, arms = record_and_spec()
    baseline = {**arms['mission_verified_complex'], 'mission_source_commit': 'baseline', 'package_sha256': 'old', 'budget_policy_template_sha256': None}
    arms['mission_baseline'] = baseline
    for hour in range(4):
        r = copy.deepcopy(record)
        r['run_started_at'] = f'2026-01-01T0{hour}:00:00Z'
        for key in ('budget_policy',): r[key]['external_deadline_at'] = f'2026-01-01T0{hour}:00:09Z'
        for key in ('session_init', 'mission_state'): r[key]['budget_policy']['external_deadline_at'] = r['budget_policy']['external_deadline_at']
        r['deadline_reached'] = True
        assert integrity().check_record(r, arms)['matches']
    record['manifest']['mission_source_commit'] = 'baseline'
    record['manifest']['package']['sha256'] = 'old'
    record['budget_policy'] = None
    record['mission_state'] = None
    record.pop('session_init')
    result = integrity().check_record(record, arms)
    assert result['matches'] and result['planned_arm'] == 'mission_baseline'


def write_state(root, state):
    path = root / '.mission-state/sessions/cx-t.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'schema_version': 4, 'phase': 'planning', 'loop_active': True, **state}))
    return path


def test_authoritative_reader_retains_history_without_mutation(tmp_path):
    state = dict(session_id='cx-t', mission_id='mid', budget_policy={'reactivate': 'forbidden'}, reactivation_history=[{'reason': 'observed'}])
    path = write_state(tmp_path, state)
    before = path.read_bytes()
    observed = integrity().read_evaluated_state(tmp_path, 't')
    assert observed['reactivation_history'] == state['reactivation_history']
    assert observed['budget_policy'] == state['budget_policy']
    assert path.read_bytes() == before


class FakeRpc:
    def __init__(self, skill, mode, probe):
        self.skill, self.mode, self.probe = skill, mode, probe
        self.events = []
        self.calls = []
        self.closed = False
        self.wait_end_reason = None
    def request(self, method, params):
        self.calls.append(method)
        if method == 'thread/start': return {'thread': {'id': 't'}, 'model': 'm', 'reasoningEffort': 'high', 'activePermissionProfile': {'id': 'p'}}
        if method == 'skills/list': return {'data': [{'skills': [{'path': str(self.skill)}]}]} if self.mode != 'skill_missing' else {}
        if method == 'turn/start':
            self.probe_evidence['exec_events'].append({'command': ['bash', '-lc', 'cp .mission-state/x /tmp/x']})
            if self.mode == 'exception': raise RuntimeError('fake failure')
            if self.mode == 'request_deadline': raise self.probe.AssignmentDeadlineReached()
            return {'turn': {'id': 'turn'}}
        return {}
    def wait_for_event(self, *_args):
        self.wait_end_reason = 'deadline' if self.mode == 'deadline' else 'eof' if self.mode == 'eof' else None
        return self.mode not in ('deadline', 'eof')
    def close(self): self.closed = True


@pytest.mark.parametrize('mode', ['success', 'deadline', 'request_deadline', 'eof', 'exception', 'skill_missing', 'pre_failed', 'post_failed'])
def test_every_exit_retains_identity_and_post_run_disk_read(tmp_path, monkeypatch, mode):
    probe = _load_probe()
    skill = tmp_path / 'package/skills/mission/SKILL.md'
    skill.parent.mkdir(parents=True); skill.write_text('fixture')
    fake = FakeRpc(skill, mode, probe)
    evidence = {}
    fake.probe_evidence = evidence
    monkeypatch.setattr(probe, 'RpcProcess', lambda *_: fake)
    versions = iter(['v', 'v'])
    monkeypatch.setattr(probe, '_codex_version', lambda: next(versions))
    state = dict(session_id='cx-t', mission_id='mid', budget_policy={'reactivate': 'forbidden'}, reactivation_history=[])
    write_state(tmp_path, state)
    def pre(root, thread, observed):
        assert 'turn/start' not in fake.calls
        if mode == 'pre_failed': raise RuntimeError('init failed')
        return state
    def post(root, observed):
        assert fake.closed
        if mode == 'post_failed': raise RuntimeError('post failed')
        return {'mission_state': integrity().read_evaluated_state(root, observed['thread_id'])}
    result = probe.run_codex_assignment(tmp_path, 'o', 'a', 10, None, 1, 'm', 'high', 'p', 'mission', skill.parents[2], evidence=evidence, pre_turn=pre, post_run=post, mission_edition='verified-complex')
    assert all(key in result for key in ('observed_config', 'config_matches', 'package_delivery', 'turn_start_sent', 'exec_events', 'provider_version_after'))
    assert result['provider_version_before'] == result['provider_version_after'] == 'v'
    assert result['turn_start_sent'] == (0 if mode in ('skill_missing', 'pre_failed') else 1)
    if mode == 'pre_failed': assert result['reason'] == 'mission_session_init_failed'
    else: assert result['session_init']['budget_policy_template_sha256'] == integrity().policy_digest(state['budget_policy'])
    if mode in ('deadline', 'request_deadline'): assert result['deadline_reached'] is True and result['reason'] == 'assignment_deadline_reached'
    if mode == 'eof': assert result['deadline_reached'] is False
    if mode == 'post_failed': assert result['mission_state'] is None and result['post_run_error'] == 'RuntimeError'
    else: assert result['mission_state']['mission_id'] == 'mid'
    if result['turn_start_sent']: assert result['exec_scan']


def test_provider_unavailable_prevents_turn(tmp_path, monkeypatch):
    probe = _load_probe()
    monkeypatch.setattr(probe, '_codex_version', lambda: (_ for _ in ()).throw(RuntimeError('unavailable')))
    monkeypatch.setattr(probe, 'RpcProcess', lambda *_: pytest.fail('provider must not start'))
    result = probe.run_codex_assignment(tmp_path, 'o', 'a', 1, None, 1, 'm', 'high', 'p')
    assert result['reason'] == 'provider_version_unavailable' and result['turn_start_sent'] == 0


def test_rpc_eof_and_deadline_are_typed_and_stream_survives(tmp_path, monkeypatch):
    probe = _load_probe()
    rpc = object.__new__(probe.RpcProcess)
    rpc.events, rpc.evidence = [], {'exec_events': []}
    rpc._record_event({'method': 'item/started', 'params': {'item': {'type': 'commandExecution', 'command': 'cp .mission-state/x /tmp/x'}}})
    assert rpc.evidence['exec_events'][0]['command'] == 'cp .mission-state/x /tmp/x'
    rpc.sequence = 0
    import io
    from types import SimpleNamespace
    rpc.process = SimpleNamespace(stdin=io.BytesIO())
    rpc.deadline = float('inf')
    rpc.wait_end_reason = 'eof'
    monkeypatch.setattr(rpc, '_next_message', lambda: None)
    with pytest.raises(probe.RpcEOFError): rpc.request('turn/start', {})
    rpc.deadline = 0
    with pytest.raises(probe.AssignmentDeadlineReached): rpc.request('turn/start', {})
    rpc._buffer = bytearray()
    rpc.selector = SimpleNamespace(select=lambda *_: [True])
    rpc.process.stdout = SimpleNamespace(fileno=lambda: 1)
    monkeypatch.setattr(probe.os, 'read', lambda *_: b'')
    assert probe.RpcProcess._next_message(rpc) is None and rpc.wait_end_reason == 'eof'
    rpc.selector.select = lambda *_: []
    assert probe.RpcProcess._next_message(rpc) is None and rpc.wait_end_reason == 'deadline'


@pytest.mark.parametrize('bad', [None, [], 'text', {'manifest': []}, {'manifest': {'package': 4}}, {'manifest': {'conditions': 'invalid'}}])
def test_malformed_record_is_non_quality(bad):
    _, arms = record_and_spec()
    result = integrity().check_record(bad, arms)
    assert result['classification'] == 'non_quality'


@pytest.mark.parametrize('mode', ['deadline', 'eof', 'exception'])
@pytest.mark.parametrize('damage', ['missing', 'invalid'])
def test_default_post_run_reader_missing_or_invalid_is_non_quality(tmp_path, monkeypatch, mode, damage):
    probe = _load_probe()
    skill = tmp_path / 'package/skills/mission/SKILL.md'
    skill.parent.mkdir(parents=True); skill.write_text('fixture')
    evidence = {}
    fake = FakeRpc(skill, mode, probe); fake.probe_evidence = evidence
    monkeypatch.setattr(probe, 'RpcProcess', lambda *_: fake)
    monkeypatch.setattr(probe, '_codex_version', lambda: 'v')
    if damage == 'invalid': write_state(tmp_path, {'invalid': True})
    result = probe.run_codex_assignment(tmp_path, 'o', 'a', 10, None, 1, 'm', 'high', 'p', 'mission', skill.parents[2], evidence=evidence)
    assert fake.closed and result['mission_state'] is None and result['post_run_error']
    record, arms = record_and_spec(); record.update(result)
    assert integrity().check_record(record, arms)['classification'] == 'non_quality'


@pytest.mark.parametrize('reactivated', [False, True])
def test_real_v5_reader_resolves_head_and_leaves_repository_bytes_unchanged(tmp_path, raw_run_cli, reactivated):
    started = raw_run_cli('init', 'fixture task', cwd=tmp_path, env_extra={'CODEX_THREAD_ID': 't'})
    assert started.returncode == 0, started.stderr
    if reactivated:
        for args in (('mark-halt', '--reason', 'fixture halt', '--category', 'other'), ('reactivate', '--approved-by-user', '--reason', 'fixture approval', '--expected-category', 'other')):
            result = raw_run_cli(*args, cwd=tmp_path, env_extra={'CODEX_THREAD_ID': 't'})
            assert result.returncode == 0, result.stderr
    root = tmp_path / '.mission-state'
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    state = integrity().read_evaluated_state(tmp_path, 't')
    assert state['session_id'] == 'cx-t' and state['mission_id']
    assert bool(state['reactivation_history']) is reactivated
    after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert after == before


@pytest.mark.parametrize('mode', ['deadline', 'exception', 'post_failed'])
def test_main_persists_record_with_versions_candidate_and_failed_post_run(tmp_path, monkeypatch, mode):
    probe = _load_probe()
    def package(_repo, _commit, output): output.write_bytes(b'fixture'); return output
    def unpack(_archive, destination, **_kwargs):
        for prefix in ('', 'plugins/mission/'):
            path = Path(destination) / prefix / 'skills/mission/SKILL.md'
            path.parent.mkdir(parents=True); path.write_text('fixture')
    def worker(_source, _commit, destination, _allow): destination.mkdir(parents=True); return destination
    monkeypatch.setattr(probe, 'create_immutable_package', package)
    monkeypatch.setattr(probe.shutil, 'unpack_archive', unpack)
    monkeypatch.setattr(probe, 'create_worker_export', worker)
    monkeypatch.setattr(probe, 'initialize_worker_export_repository', lambda _: 'export')
    monkeypatch.setattr(probe, 'worker_export_manifest', lambda _: {'sha256': 'candidate'})
    monkeypatch.setattr(probe, '_task_snapshot', lambda _: {'observed': 'a' * 40, 'clean': True})
    monkeypatch.setattr(probe, '_codex_version', lambda: 'v')
    monkeypatch.setattr(probe.sys, 'executable', '/Users/x/venv/bin/python3')
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: Path('/Users/x')))
    fake = FakeRpc(None, mode, probe)
    def create_rpc(*_):
        # package_root exists only inside main's temporary package context.
        fake.skill = Path(captured['package_root']) / 'skills/mission/SKILL.md'
        return fake
    original = probe.probe_codex
    captured = {}
    def run(*args, **kwargs):
        captured['package_root'] = args[10]
        fake.probe_evidence = kwargs['evidence']
        try:
            return original(*args, **kwargs)
        finally:
            observed = kwargs['evidence']
            observed.update(workspace='/Users/x/project', mission_state_path='/Users/x/package/skills/mission/bin/mission-state.py')
            observed['exec_events'] = [{'command': ['/Users/x/venv/bin/python3', observed['mission_state_path'], 'status', '--input', '/Users/x/project/.mission-state/x'], 'cwd': '/Users/x/project'}, {'command': ['bash', '-lc', 'cp /Users/x/project/.mission-state/x /tmp/x'], 'cwd': '/Users/x/project'}]
    monkeypatch.setattr(probe, 'probe_codex', run)
    monkeypatch.setattr(probe, 'RpcProcess', create_rpc)
    def post(root, observed):
        assert fake.closed
        raise OSError('unreadable')
    output = tmp_path / 'record.json'
    args = ['probe', '--host', 'codex', '--arm', 'mission', '--objective', 'fixture', '--task-id', 'task', '--assignment-id', 'assignment', '--acceptance-criterion', 'fixture', '--starting-commit', 'a' * 40, '--mission-source-repo', str(tmp_path), '--mission-source-commit', 'a' * 40, '--model-id', 'm', '--effort', 'high', '--permissions', 'p', '--worktree', str(tmp_path), '--output', str(output)]
    monkeypatch.setattr(sys, 'argv', args)
    assert probe.main(post_run=post) == 0
    record = json.loads(output.read_text())
    assert record['manifest']['provider_version'] == record['provider_version_after'] == 'v'
    assert record['manifest']['worker_export']['candidate_sha256'] == 'candidate'
    assert record['post_run_error'] == 'OSError' and record['mission_state'] is None
    assert record['exec_events'] and record['exec_scan'] and record['turn_start_sent'] == 1
    from native_goal_benchmark import _UNSAFE_TRACE
    assert not any(marker in output.read_text() for marker in _UNSAFE_TRACE)
    assert record['interpreter_path'].startswith('/') and record['mission_state_path'].startswith('/')
    assert record['exec_events'][0]['command'][:2] == [record['interpreter_path'], record['mission_state_path']]
    from exec_event_scan import scan_exec_events
    assert scan_exec_events(record['exec_events'], record['mission_state_path'], record['interpreter_path'], record['workspace']) == record['exec_scan']


def test_goal_deadline_preserves_last_observation_and_identity(tmp_path, monkeypatch):
    probe = _load_probe()
    class GoalRpc:
        def __init__(self, *_): self.events = []; self.objective = None; self.turns = 0
        def request(self, method, params):
            if method == 'thread/start': return {'thread': {'id': 't'}, 'model': 'm', 'reasoningEffort': 'high', 'activePermissionProfile': {'id': 'p'}}
            if method == 'thread/goal/set': self.objective = params['objective']
            if method.startswith('thread/goal/'):
                return {'goal': {'threadId': 't', 'objective': self.objective, 'status': 'active', 'createdAt': 1, 'tokensUsed': 12}}
            if method == 'turn/start':
                self.turns += 1
                if self.turns == 2: raise probe.AssignmentDeadlineReached()
                return {'turn': {'id': 'turn'}}
            return {}
        def wait_for_event(self, *_):
            self.events.extend([{'method': method, 'params': {'threadId': 't', 'turnId': 'turn'}} for method in ('turn/started', 'turn/completed')])
            return True
        def close(self): pass
    monkeypatch.setattr(probe, 'RpcProcess', GoalRpc)
    monkeypatch.setattr(probe, '_codex_version', lambda: 'v')
    result = probe.run_codex_assignment(tmp_path, 'o', 'a', 1, None, 2, 'm', 'high', 'p')
    assert result['reason'] == 'assignment_deadline_reached' and result['goal_status'] == 'active'
    assert result['tokens_used'] == 12 and result['turn_start_sent'] == 2
    assert result['observed_config'] and result['config_matches'] and result['package_delivery'] is None


def test_native_arm_can_share_package_with_verified_arm():
    record, arms = record_and_spec()
    native = copy.deepcopy(arms['mission_verified_complex'])
    native['conditions']['arm'] = 'goal'
    native['budget_policy_template_sha256'] = None
    arms['native_goal'] = native
    record.update(arm='codex_native_goal', package_delivery=None, budget_policy=None)
    record.pop('session_init')
    record['manifest']['conditions'] = native['conditions']
    result = integrity().check_record(record, arms)
    assert result['matches'] and result['planned_arm'] == 'native_goal'


@pytest.mark.parametrize('path', [
    ('manifest', 'conditions', 'permissions'), ('manifest', 'conditions', 'token_budget'),
    ('manifest', 'task_snapshot', 'matches'), ('manifest', 'worker_export', 'initial_sha256'),
    ('package_delivery',), ('config_matches',), ('provider_version_after',),
    ('observed_config', 'model'), ('observed_config', 'reasoningEffort'),
    ('observed_config', 'activePermissionProfile', 'id'),
])
def test_each_required_identity_field_is_checked(path):
    record, arms = record_and_spec()
    node = record
    for key in path[:-1]: node = node[key]
    del node[path[-1]]
    assert integrity().check_record(record, arms)['classification'] == 'non_quality'


def test_unexpected_adapter_exception_keeps_post_run_record(tmp_path, monkeypatch):
    probe = _load_probe()
    monkeypatch.setattr(probe, '_codex_version', lambda: 'v')
    monkeypatch.setattr(probe, 'probe_codex', lambda *_args, **_kwargs: (_ for _ in ()).throw(TypeError('malformed response')))
    result = probe.run_codex_assignment(tmp_path, 'o', 'a', 1, None, 1, 'm', 'high', 'p', 'mission', tmp_path)
    assert result['reason'] == 'adapter_execution_failed'
    assert result['provider_version_after'] == 'v' and result['exec_scan'] is None


@pytest.mark.parametrize('damage', ['script', 'interpreter', 'exception', 'recorded'])
def test_baseline_reports_unavailable_exec_scan(monkeypatch, damage):
    record, arms = record_and_spec()
    arms = {'mission_baseline': arms['mission_verified_complex']}
    record['budget_policy'] = None
    record.pop('session_init')
    if damage == 'exception':
        def fail(*_): raise ValueError('fixture')
        monkeypatch.setattr(integrity(), 'scan_exec_events', fail)
    elif damage == 'recorded': record['exec_scan_error'] = 'OSError'
    else:
        record['mission_state_path' if damage == 'script' else 'interpreter_path'] = None
    result = integrity().check_record(record, arms)
    assert result['matches'] and result['classification'] is None
    assert 'exec_event_stream' in result['unobserved'] and result['exec_scan_error']


@pytest.mark.parametrize('script,tampered', [(s, False) for s in SAFE] + [(s, True) for s in BAD])
def test_path_normalization_preserves_scan_and_sanitises_record(tmp_path, script, tampered):
    from record_paths import write_probe_record
    from exec_event_scan import scan_exec_events
    from native_goal_benchmark import _UNSAFE_TRACE
    package = '/Users/x/package'
    interpreter = '/Users/x/venv/bin/python3'
    mission = package + '/skills/mission/bin/mission-state.py'
    workspace = '/Users/x/project'
    command = script.replace(SCRIPT, mission).replace(PYTHON, interpreter).replace('/work', workspace)
    raw, arms = record_and_spec()
    raw.update(mission_state_path=mission, interpreter_path=interpreter, workspace=workspace,
               exec_events=[{'command': ['bash', '-lc', command], 'cwd': workspace}],
               nested={workspace + '/file': [workspace + '/file']})
    before = scan_exec_events(raw['exec_events'], mission, interpreter, workspace)
    output = tmp_path / 'normalised.json'
    roots = dict(home='/Users/x', workspace=workspace, package=package, interpreter=interpreter)
    write_probe_record(output, raw, roots)
    saved = json.loads(output.read_text())
    after = scan_exec_events(saved['exec_events'], saved['mission_state_path'], saved['interpreter_path'], saved['workspace'])
    assert bool(before) is tampered and before == after == saved['exec_scan']
    assert integrity().check_record(saved, arms)['matches'] is (not tampered)
    assert not saved.get('record_persistence_error')
    assert not any(marker in output.read_text() for marker in _UNSAFE_TRACE)
    assert raw['workspace'] == workspace  # no mutation of live callback/reader evidence


@pytest.mark.parametrize('slot', ['command', 'cwd', 'dict_key', 'nested'])
@pytest.mark.parametrize('unknown', ['/Users/USER/other/file', '/tmp/.codex/memories/file', '/tmp/.claude/projects/file', '/tmp/.codex/memories', '/tmp/.claude/projects'])
def test_unpersistable_trace_keeps_safe_non_quality_record(tmp_path, slot, unknown):
    from record_paths import write_probe_record
    from native_goal_benchmark import _UNSAFE_TRACE
    record, arms = record_and_spec()
    record.update(run_id='run', assignment_id='assignment', task_id='task', workspace='/work', outcome='completed', fidelity='verified')
    if slot == 'command': record['exec_events'] = [{'command': ['cat', unknown]}]
    elif slot == 'cwd': record['exec_events'] = [{'command': [SCRIPT, 'status'], 'cwd': unknown}]
    elif slot == 'dict_key': record['nested'] = {unknown: True}
    else: record['nested'] = [{'path': unknown}]
    output = tmp_path / 'failed.json'
    write_probe_record(output, record, dict(home='/Users/x', workspace='/work', package='/package', interpreter=PYTHON))
    saved = json.loads(output.read_text())
    assert saved['assignment_id'] == 'assignment' and saved['manifest']['package']['sha256'] == 'pkg'
    assert saved['classification'] == 'non_quality' and saved['reason'] == 'evaluated_session_unverifiable'
    assert saved['record_persistence_error'] and saved['exec_events'] is None and saved['exec_scan'] is None
    assert integrity().check_record(saved, arms)['classification'] == 'non_quality'
    assert not any(marker in output.read_text() for marker in _UNSAFE_TRACE)


def test_writer_redaction_cannot_silently_change_saved_scan(tmp_path):
    from record_paths import write_probe_record
    record = dict(arm='mission', workspace='/work', mission_state_path=SCRIPT, interpreter_path=PYTHON,
                  exec_events=[{'command': 'echo Bearer .mission-state/x'}])
    output = tmp_path / 'redacted.json'
    write_probe_record(output, record, dict(workspace='/work', package='/package', interpreter=PYTHON))
    saved = json.loads(output.read_text())
    assert saved['record_persistence_error'] == 'path_normalization_changed_scan'
    assert saved['classification'] == 'non_quality' and saved['exec_events'] is None


@pytest.mark.parametrize('arm', ['mission_baseline', 'native_goal'])
def test_persistence_failure_is_non_quality_even_without_verified_stream_requirement(tmp_path, arm):
    from record_paths import write_probe_record
    record, arms = record_and_spec()
    arms = {arm: arms['mission_verified_complex']}
    record['budget_policy'] = None
    record.pop('session_init')
    if arm == 'native_goal':
        record.update(arm='codex_native_goal', package_delivery=None)
        record['manifest']['conditions']['arm'] = 'goal'
    assert integrity().check_record(record, arms)['matches']
    record['nested'] = '/Users/USER/unknown'
    output = tmp_path / 'failed.json'
    write_probe_record(output, record, dict(home='/Users/x'))
    saved = json.loads(output.read_text())
    assert not integrity().check_record(saved, arms)['matches']
    assert integrity().check_record(saved, arms)['classification'] == 'non_quality'


@pytest.mark.parametrize('arm', ['mission_baseline', 'native_goal'])
@pytest.mark.parametrize('initialized', [None, {'session_id': 'cx-t'}])
def test_non_verified_arm_rejects_harness_session_init(arm, initialized):
    record, arms = record_and_spec()
    arms = {arm: arms['mission_verified_complex']}
    record.update(budget_policy=None, session_init=initialized)
    if arm == 'native_goal':
        record.update(arm='codex_native_goal', package_delivery=None)
        record['manifest']['conditions']['arm'] = 'goal'
    result = integrity().check_record(record, arms)
    assert result['classification'] == 'non_quality'
    assert result['reasons'] == ['execution_config_mismatch']


@pytest.mark.parametrize('field,value', [('max_turns', True), ('max_turns', 1.0), ('timeout_seconds', 10.0)])
def test_conditions_require_matching_types(field, value):
    record, arms = record_and_spec()
    record['manifest']['conditions'] = {**record['manifest']['conditions'], field: value}
    result = integrity().check_record(record, arms)
    assert result['classification'] == 'non_quality'
    assert result['reasons'] == ['execution_config_mismatch']


@pytest.mark.parametrize('hook', [False, True])
def test_post_run_projects_state_without_mutating_reader_document(tmp_path, hook):
    record, _ = record_and_spec()
    full = {**record['mission_state'], 'mission': 'PRIVATE TASK', 'cwd': '/Users/USER/secret', 'halt_reason': 'PRIVATE REASON'}
    path = write_state(tmp_path, full)
    before = path.read_bytes()
    post = (lambda *_: {'mission_state': full, 'session_init': full}) if hook else None
    integrity().collect_post_run(tmp_path, record, post)
    allowed = {'session_id', 'mission_id', 'budget_policy', 'reactivation_history', 'budget_policy_template_sha256'}
    for key in ('mission_state', 'session_init'):
        assert set(record[key]) <= allowed
        assert record[key]['mission_id'] == 'mid'
    assert full['mission'] == 'PRIVATE TASK' and path.read_bytes() == before


@pytest.mark.parametrize('slot', ['mission_state', 'session_init'])
@pytest.mark.parametrize('field', ['budget_policy', 'reactivation_history'])
@pytest.mark.parametrize('private', ['/Users/x/known/file', '/Users/USER/unknown/file'])
def test_projected_state_paths_remain_subject_to_privacy_guard(tmp_path, slot, field, private):
    from record_paths import write_probe_record
    from native_goal_benchmark import _UNSAFE_TRACE
    record, _ = record_and_spec()
    if field == 'budget_policy': record[slot][field]['extra'] = private
    else: record[slot][field] = [{'reason': private}]
    integrity().collect_post_run(tmp_path, record, lambda *_: {})
    output = tmp_path / 'projected.json'
    write_probe_record(output, record, dict(home='/Users/x', package='/package', interpreter=PYTHON))
    saved = json.loads(output.read_text())
    assert not any(marker in output.read_text() for marker in _UNSAFE_TRACE)
    assert bool(saved.get('record_persistence_error')) is ('USER' in private)
    if 'USER' in private: assert saved['classification'] == 'non_quality'


@pytest.mark.parametrize('edition', ['goal', 'baseline', 'verified-complex'])
@pytest.mark.parametrize('mode', ['success', 'deadline', 'eof', 'exception', 'launch_failed', 'version_failed'])
def test_main_records_validate_schema_on_all_exit_paths(tmp_path, monkeypatch, edition, mode):
    from contextlib import nullcontext
    import jsonschema
    probe = _load_probe()
    physical = tmp_path / 'physical'; physical.mkdir()
    alias = tmp_path / 'alias'; alias.symlink_to(physical, target_is_directory=True)
    monkeypatch.setattr(probe.tempfile, 'TemporaryDirectory', lambda **_: nullcontext(str(alias)))
    full = dict(session_id='cx-t', mission_id='mid', budget_policy={'reactivate': 'forbidden'}, reactivation_history=[], mission='PRIVATE TASK', workspace='/Users/USER/private')
    def package(_repo, _commit, output): output.write_bytes(b'fixture'); return output
    def unpack(_archive, destination, **_kwargs):
        for prefix in ('', 'plugins/mission/'):
            path = Path(destination) / prefix / 'skills/mission/SKILL.md'
            path.parent.mkdir(parents=True); path.write_text('fixture')
    def worker(_source, _commit, destination, _allow): destination.mkdir(parents=True); return destination
    monkeypatch.setattr(probe, 'create_immutable_package', package)
    monkeypatch.setattr(probe.shutil, 'unpack_archive', unpack)
    monkeypatch.setattr(probe, 'create_worker_export', worker)
    monkeypatch.setattr(probe, 'initialize_worker_export_repository', lambda _: 'b' * 40)
    monkeypatch.setattr(probe, 'worker_export_manifest', lambda _: {'sha256': 'sha256:' + 'c' * 64})
    monkeypatch.setattr(probe, '_task_snapshot', lambda _: {'observed': 'a' * 40, 'clean': True})
    monkeypatch.setattr(probe, '_fresh_mission_state', lambda *_: {'passes': True})
    def version():
        if mode == 'version_failed': raise RuntimeError('unavailable')
        return 'v'
    monkeypatch.setattr(probe, '_codex_version', version)
    calls, initialized = [], []
    class Rpc:
        def __init__(self, *_):
            if mode == 'launch_failed': raise FileNotFoundError('missing provider')
            assert mode != 'version_failed'
            self.events, self.wait_end_reason = [], None
        def request(self, method, params):
            calls.append((method, params))
            if method == 'thread/start': return {'thread': {'id': 't'}, 'model': 'm', 'reasoningEffort': 'high', 'activePermissionProfile': {'id': 'p'}}
            if method == 'skills/extraRoots/set': self.skill = Path(params['extraRoots'][0]) / 'mission/SKILL.md'
            if method == 'skills/list': return {'data': [{'skills': [{'path': str(self.skill)}]}]}
            if method == 'thread/goal/set': self.objective = params['objective']
            if method == 'thread/goal/clear': return {'cleared': True}
            if method.startswith('thread/goal/'): return {'goal': {'threadId': 't', 'objective': self.objective, 'status': 'complete' if mode == 'success' else 'active', 'createdAt': 1}}
            if method == 'turn/start':
                if edition == 'verified-complex': assert 'mission' not in self.evidence['session_init']
                if mode == 'exception': raise RuntimeError('fake failure')
                return {'turn': {'id': 'turn'}}
            return {}
        def wait_for_event(self, *_):
            self.wait_end_reason = 'deadline' if mode == 'deadline' else 'eof' if mode == 'eof' else None
            if mode == 'deadline': raise probe.AssignmentDeadlineReached()
            self.events.extend({'method': m, 'params': {'threadId': 't', 'turnId': 'turn'}} for m in ('turn/started', 'turn/completed'))
            return mode != 'eof'
        def close(self): pass
    monkeypatch.setattr(probe, 'RpcProcess', Rpc)
    def pre(*_): initialized.append(True); return full
    output = tmp_path / 'result.json'
    monkeypatch.setattr(sys, 'argv', ['probe', '--host', 'codex', '--arm', 'goal' if edition == 'goal' else 'mission', '--objective', 'PRIVATE TASK', '--task-id', 't', '--assignment-id', 'a', '--acceptance-criterion', 'PRIVATE ACCEPTANCE', '--starting-commit', 'a' * 40, '--mission-source-repo', str(tmp_path), '--mission-source-commit', 'a' * 40, '--model-id', 'm', '--effort', 'high', '--permissions', 'p', '--worktree', str(tmp_path), '--output', str(output), '--worker-allow-path', 'task.txt'])
    assert probe.main(mission_edition='verified-complex' if edition == 'verified-complex' else 'baseline', pre_turn=pre, post_run=lambda *_: {'mission_state': full}) == 0
    record = json.loads(output.read_text())
    jsonschema.validate(record, json.loads((BENCH / 'native_goal_result.schema.json').read_text()))
    assert record.get('reason') != 'package_prepare_failed'
    assert record['outcome'] == ('completed' if mode == 'success' else 'failed')
    assert bool(initialized) is (edition == 'verified-complex' and mode not in ('launch_failed', 'version_failed'))
    assert ('session_init' in record) is bool(initialized)
    if mode in ('launch_failed', 'version_failed'):
        assert 'config_matches' not in record and 'observed_config' not in record
    assert 'PRIVATE TASK' not in output.read_text() and 'PRIVATE ACCEPTANCE' not in output.read_text()
    assert '/Users/USER/private' not in output.read_text()
    if edition != 'goal' and mode != 'version_failed':
        assert record['mission_state']['mission_id'] == 'mid'
        assert record['mission_state_path'].startswith('/__mission_paths__/package/')
    assert full['mission'] == 'PRIVATE TASK'


def test_symlink_package_uses_same_recorded_and_delivered_paths(tmp_path, monkeypatch):
    from exec_event_scan import scan_exec_events
    probe = _load_probe()
    root = tmp_path / 'physical/package'
    skill = root / 'skills/mission/SKILL.md'
    skill.parent.mkdir(parents=True); skill.write_text('fixture')
    alias = tmp_path / 'alias'; alias.symlink_to(root.parent, target_is_directory=True)
    evidence = {}
    class Rpc(FakeRpc):
        def request(self, method, params):
            if method == 'skills/extraRoots/set': assert params['extraRoots'] == [str(root / 'skills')]
            if method == 'turn/start':
                assert params['input'][0]['path'] == str(skill)
                evidence['exec_events'].append({'command': [str(root / 'skills/mission/bin/mission-state.py'), 'status', '.mission-state/x']})
                return {'turn': {'id': 'turn'}}
            return super().request(method, params)
    monkeypatch.setattr(probe, 'RpcProcess', lambda *_: Rpc(skill, 'success', probe))
    probe.probe_codex(tmp_path, 'o', 'a', 10, None, 1, 'm', 'high', 'p', 'mission', alias / 'package', evidence=evidence, pre_turn=lambda *_: pytest.fail('baseline must not initialise'))
    assert evidence['mission_state_path'] == str(root / 'skills/mission/bin/mission-state.py')
    assert scan_exec_events(evidence['exec_events'], evidence['mission_state_path'], evidence['interpreter_path'], str(tmp_path)) == []


@pytest.mark.parametrize('document', ['PRIVATE TASK', ['PRIVATE TASK'], None])
def test_invalid_session_documents_do_not_publish_task_prose(tmp_path, document):
    record, _ = record_and_spec()
    integrity().collect_post_run(tmp_path, record, lambda *_: {'mission_state': document, 'session_init': document})
    assert record['mission_state'] is None and record['session_init'] is None
