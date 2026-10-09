"""Offline preregistration contracts; no model, network, or container execution."""


import copy


import hashlib


import importlib.util


import json


from pathlib import Path


import sys


import pytest


BENCH = Path(__file__).resolve().parents[3] / 'benchmarks/mission-vs-goal'


sys.path.insert(0, str(BENCH))


@pytest.fixture
def m():
    spec = importlib.util.spec_from_file_location('bench_selection', BENCH / 'bench_selection.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


P, Q, E = 2**521 - 1, 2**607 - 1, 65537


N = P * Q


D = pow(E, -1, (P - 1) * (Q - 1))


SIZE = (N.bit_length() + 7) // 8


def encoded(message):
    tail = bytes.fromhex('3031300d060960864801650304020105000420') + hashlib.sha256(message).digest()
    return b'\x00\x01' + b'\xff' * (SIZE - len(tail) - 3) + b'\x00' + tail


def sign(message):
    return pow(int.from_bytes(encoded(message), 'big'), D, N).to_bytes(SIZE, 'big').hex()


class Proofs:
    """Fake transport with cryptographic proof checks, not trusted booleans."""
    def chain_schedule(self, chain_hash):
        if chain_hash != 'neutral-chain': raise ValueError('chain_unknown')
        return {'genesis': 10, 'period': 10}

    def verify_beacon(self, chain, round_number, beacon):
        assert chain['public_key'] == str(N)
        message = chain['hash'].encode() + round_number.to_bytes(8, 'big')
        signature = bytes.fromhex(beacon['signature'])
        if (len(signature) != SIZE or int.from_bytes(signature, 'big') >= N
                or pow(int.from_bytes(signature, 'big'), E, N).to_bytes(SIZE, 'big') != encoded(message)):
            raise ValueError('beacon_signature_invalid')
        return hashlib.sha256(signature).digest()

    def verify_timestamp(self, digest, proof):
        message = (digest + ':' + str(proof['time'])).encode()
        signature = bytes.fromhex(proof['signature'])
        if (len(signature) != SIZE or int.from_bytes(signature, 'big') >= N
                or pow(int.from_bytes(signature, 'big'), E, N).to_bytes(SIZE, 'big') != encoded(message)):
            raise ValueError('timestamp_invalid')
        return proof['time']


def task(index):
    return {'task_id': f'task-{index:03}', 'benchmark': 'neutral-suite', 'revision': 'v1',
            'repository': f'https://example.invalid/owner/project-{index}',
            'base_commit': f'{index + 1:040x}', 'roots': [f'{index + 1000:040x}'],
            'base_files': {'main.py': '\n'.join(f'project {index} line {n}' for n in range(4))},
            'benchmark_license': 'MIT', 'upstream_license': 'MIT', 'natural_request': True,
            'environment': {'image': 'neutral@sha256:' + 'b' * 64, 'command': ['python', '/evaluator/run.py']},
            'changes': [{'path': p, 'added': [f'long source statement for project {index} line {n}' for n in range(5)], 'deleted': []}
                        for p in ('src/a.py', 'src/b.py')],
            'checks': [{'name': 'repair', 'kind': 'fail_to_pass'},
                       {'name': 'keep-a', 'kind': 'pass_to_pass'}, {'name': 'keep-b', 'kind': 'pass_to_pass'}]}


def observed(passed=False):
    cases = [{'name': 'repair', 'passed': passed}, {'name': 'keep-a', 'passed': True}, {'name': 'keep-b', 'passed': True}]
    return {'status': 'passed' if passed else 'failed', 'reason': None if passed else 'contract_mismatch',
            'case_count': 3, 'cases': cases}


def inputs(m, count=16):
    snapshot = [task(i) for i in range(count)]
    scope = {'mission': {'README.md': 'neutral source'},
             'packages': {arm: {'SKILL.md': 'neutral package'} for arm in m.ARMS}}
    config = {'licenses': {'MIT': ['copy', 'execute', 'store']}, 'store_contents': True,
              'cx_files': 2, 'cx_checks': 3, 'cx_lines': 10, 'con_length': 30,
              'con_lines': 3, 'g3_percent': 20, 'det_mode': 'all',
              'snapshot_digest': m.snapshot_digest(snapshot), 'scope_digest': m.digest(m.canonical(scope)),
              'generator_sha': 'f' * 40}
    observations = {t['task_id']: {'starter': [observed()] * 3, 'reference': [observed(True)] * 3} for t in snapshot}
    return snapshot, config, scope, observations


def pool(m, data):
    return m.generate_pool(*data, commit_a='a' * 40)


def fixture(m, count=16, *, pilot_arms=None, confirmatory_arms=None):
    data = inputs(m, count)
    snapshot, config, scope, observations = data
    a = config | {'number': 1, 'round': 100, 'margin': 100,
                  'chain': {'hash': 'neutral-chain', 'public_key': str(N), 'genesis': 10, 'period': 10},
                  'packages': {arm: {'sha': str(i) * 40, 'digest': m.digest(arm.encode())} for i, arm in enumerate(m.ARMS, 1)},
                  'prior_attempts_digest': m.digest(m.canonical([])),
                  'pilot_arms': list(m.ARMS if pilot_arms is None else pilot_arms),
                  'confirmatory_arms': list(m.ARMS if confirmatory_arms is None else confirmatory_arms)}
    history = {'commits': [], 'prs': [], 'current_files': {}, 'reachable': [], 'total_prs': 0,
               'protection': {'force_push': False, 'deletion': False}}
    put(m, history, 1, 'A', 'a' * 40, m.canonical(a), 100)
    b = pool(m, data)  # collect the pool only after A; its lineage cannot be known in P
    put(m, history, 1, 'B', 'b' * 40, b, 200)
    signature = sign(a['chain']['hash'].encode() + a['round'].to_bytes(8, 'big'))
    beacon = {'signature': signature, 'randomness': hashlib.sha256(bytes.fromhex(signature)).hexdigest()}
    seed = bytes.fromhex(beacon['randomness'])
    c = m.canonical(m.select(seed, json.loads(b), a['pilot_arms']))
    d = m.canonical(m.confirm(seed, json.loads(c), 'c' * 40, 4, a['confirmatory_arms']))
    put(m, history, 1, 'C', 'c' * 40, c, 1001)
    put(m, history, 1, 'D', 'd' * 40, d, 1100)
    records = [{'task_id': r['task_id'], 'unit_id': r['unit_id'], 'arm': r['arm'],
                'package': a['packages'][r['arm']], 'started_at': 1200 + i}
               for i, r in enumerate(json.loads(d)['assignments'])]
    material = {1: {'snapshot': snapshot, 'scope': scope, 'observations': observations,
                    'generator_sha': config['generator_sha'], 'minimum_k': 1,
                    'lineage_digest': json.loads(b)['lineage_digest']}}
    return history, material, beacon, records


def put(m, history, number, stage, sha, content, when):
    name = {'A': 'attempt.json', 'B': 'pool-manifest.json', 'C': 'selection.json',
            'D': 'confirmation.json', 'W': 'withdrawal.json'}[stage]
    path = f'{m.ATTEMPT_PATH}/{number:04}/{name}'
    commit = {'sha': sha, 'files': {path: content}}
    if stage == 'A':
        document = f'{m.ATTEMPT_PATH}/{number:04}/preregistration.md'
        commit['files'][document] = b'# Frozen registration\nattempt_digest: ' + m.digest(content).encode() + b'\n'
    history['commits'].append(commit)
    proofs = {p: {'time': when + 1, 'signature': sign((m.digest(raw) + ':' + str(when + 1)).encode())}
              for p, raw in commit['files'].items()}
    history['prs'].append(commit | {'merged_at': when, 'proofs': proofs, 'base': 'main'})
    history['reachable'].append(sha)
    history['current_files'].update(commit['files'])
    history['total_prs'] += 1


def update_file(m, history, stage, value, number=1):
    name = {'A': 'attempt.json', 'B': 'pool-manifest.json', 'C': 'selection.json', 'D': 'confirmation.json', 'P': 'preregistration.md'}[stage]
    path = f'{m.ATTEMPT_PATH}/{number:04}/{name}'
    raw = value if type(value) is bytes else m.canonical(value)
    for c in history['commits'] + history['prs']:
        if path in c['files']: c['files'][path] = raw
    history['current_files'][path] = raw
    for pr in history['prs']:
        if path in pr['proofs']:
            time = pr['proofs'][path]['time']
            pr['proofs'][path]['signature'] = sign((m.digest(raw) + ':' + str(time)).encode())
    if stage == 'A':
        document = f'{m.ATTEMPT_PATH}/{number:04}/preregistration.md'
        document_raw = b'# Frozen registration\nattempt_digest: ' + m.digest(raw).encode() + b'\n'
        for c in history['commits'] + history['prs']:
            if document in c['files']: c['files'][document] = document_raw
        history['current_files'][document] = document_raw
        for pr in history['prs']:
            if document in pr['proofs']:
                time = pr['proofs'][document]['time']
                pr['proofs'][document]['signature'] = sign((m.digest(document_raw) + ':' + str(time)).encode())


@pytest.mark.parametrize('stage,field,value', [(s, f, v) for s in ('A', 'B')
    for f, v in [('proof', None), ('proof_time', 900), ('merged_at', 900), ('proof_time', 901), ('merged_at', 901)]])
def test_missing_or_late_either_timestamp_invalidates_attempt(m, stage, field, value):
    f = fixture(m)
    pr = f[0]['prs'][0 if stage == 'A' else 1]
    path = next(iter(pr['files']))
    if field == 'proof': pr['proofs'][path] = value
    elif field == 'proof_time':
        pr['proofs'][path] = {'time': value, 'signature': sign((m.digest(pr['files'][path]) + ':' + str(value)).encode())}
    else: pr[field] = value
    with pytest.raises(ValueError): canonical_trial(m, f)


@pytest.mark.parametrize('defect', ['unreachable', 'changed-file', 'direct-commit', 'missing-commit',
    'wrong-base', 'outside-path', 'missing-page', 'duplicate-pr', 'unprotected', 'wrong-B-reference'])
def test_history_requires_complete_append_only_main_pr_bijection(m, defect):
    f = fixture(m); h = f[0]
    if defect == 'unreachable': h['reachable'].remove('b' * 40)
    if defect == 'changed-file': h['current_files'][next(iter(h['current_files']))] = b'changed'
    if defect == 'direct-commit': h['commits'].append(h['commits'][0] | {'sha': '9' * 40})
    if defect == 'missing-commit': h['commits'].pop(0)
    if defect == 'wrong-base': h['prs'][0]['base'] = 'other'
    if defect == 'outside-path':
        pr = h['prs'][0]; path, raw = next(iter(pr['files'].items()))
        for c in h['commits'] + h['prs']:
            if path in c['files']: c['files']['other/attempt.json'] = c['files'].pop(path)
        h['current_files']['other/attempt.json'] = h['current_files'].pop(path)
    if defect == 'missing-page': h['total_prs'] += 1
    if defect == 'duplicate-pr': h['prs'].append(h['prs'][0]); h['total_prs'] += 1
    if defect == 'unprotected': h['protection']['force_push'] = True
    if defect == 'wrong-B-reference':
        b = json.loads(h['prs'][1]['files'][next(iter(h['prs'][1]['files']))]); b['commit_a'] = 'wrong'
        update_file(m, h, 'B', b)
    reason = 'history_incomplete' if defect == 'direct-commit' else 'history_path_invalid' if defect == 'outside-path' else None
    with pytest.raises(ValueError, match=reason): canonical_trial(m, f)


def second(m, f, *, withdrawal=None, a_time=1300, round_number=200, new_pool=False):
    h = f[0]
    if withdrawal is not None:
        put(m, h, 1, 'W', 'e' * 40, m.canonical({'reason': 'package_change'}), withdrawal)
    previous = m.enumerate_attempts(h, Proofs())
    a = json.loads(h['prs'][0]['files'][next(iter(h['prs'][0]['files']))])
    a.update(number=2, round=round_number, prior_attempts_digest=m.prior_digest(previous, before=a_time))
    f[1][2] = copy.deepcopy(f[1][1])
    material = f[1][2]
    if withdrawal is not None or new_pool:
        old_id = material['snapshot'][-1]['task_id']
        material['snapshot'][-1] = task(999)
        material['observations']['task-999'] = material['observations'].pop(old_id)
        a['snapshot_digest'] = m.snapshot_digest(material['snapshot'])
    put(m, h, 2, 'A', '1' * 40, m.canonical(a), a_time)
    b = m.generate_pool(material['snapshot'], a, material['scope'], material['observations'], commit_a='1' * 40)
    material['lineage_digest'] = json.loads(b)['lineage_digest']
    put(m, h, 2, 'B', '2' * 40, b, a_time + 30)
    return m.enumerate_attempts(h, Proofs())


def test_earliest_valid_attempt_wins_even_when_two_rounds_are_valid(m):
    f = fixture(m); attempts = second(m, f)
    assert m.canonical_attempt(attempts, f[1])['number'] == 1


@pytest.mark.parametrize('defect', ['round-reuse', 'round-decrease', 'overlap', 'prior-digest', 'withdrawal-order'])
def test_attempt_chain_rounds_and_nonoverlap_are_seed_independent(m, defect):
    f = fixture(m)
    attempts = second(m, f, withdrawal=600, a_time=500 if defect == 'withdrawal-order' else 700,
                      round_number=100 if defect == 'round-reuse' else 99 if defect == 'round-decrease' else 200)
    if defect == 'overlap':
        f = fixture(m); attempts = second(m, f, a_time=800)
    if defect == 'round-reuse':
        f = fixture(m); attempts = second(m, f, round_number=100)
    if defect == 'prior-digest': attempts[1]['a']['prior_attempts_digest'] = 'sha256:' + '0' * 64
    with pytest.raises(ValueError, match='no_canonical_attempt'):
        m.canonical_attempt(attempts, f[1])


@pytest.mark.parametrize('when,merged', [(899, True), (900, True), (950, True), (600, False)])
def test_withdrawal_requires_both_times_before_cutoff_and_a_merged_pr(m, when, merged):
    f = fixture(m)
    put(m, f[0], 1, 'W', 'e' * 40, m.canonical({'reason': 'package_change'}), when)
    if not merged:
        f[0]['prs'].pop(); f[0]['total_prs'] -= 1
        f[0]['commits'].pop(); f[0]['reachable'].pop()
        f[0]['current_files'].pop(next(p for p in f[0]['current_files'] if p.endswith('withdrawal.json')))
    # 899 merged + 900 attested is already too late (strict inequality).
    assert canonical_trial(m, f)['number'] == 1


def test_valid_withdrawal_allows_next_attempt_only_after_both_times(m):
    f = fixture(m); attempts = second(m, f, withdrawal=600, a_time=700)
    assert m.canonical_attempt(attempts, f[1])['number'] == 2


@pytest.mark.parametrize('defect', ['omitted', 'unjustified-reject', 'extra', 'reason', 'whitespace', 'observations'])
def test_manifest_regeneration_detects_every_form_of_pool_edit(m, defect):
    f = fixture(m); h = f[0]
    raw = h['prs'][1]['files'][next(iter(h['prs'][1]['files']))]; b = json.loads(raw)
    if defect == 'omitted': b['tasks'].pop()
    if defect == 'unjustified-reject': b['tasks'][0]['accepted'] = False
    if defect == 'extra': b['tasks'].append(b['tasks'][0] | {'task_id': 'invented'})
    if defect == 'reason': b['tasks'][0]['reason'] = 'Lic'
    if defect == 'observations': b['tasks'][0]['det']['reference'][0]['cases'][0]['passed'] = False
    if defect == 'whitespace':
        path = next(iter(h['prs'][1]['files'])); raw += b'\n'
        h['prs'][1]['files'][path] = h['commits'][1]['files'][path] = h['current_files'][path] = raw
    else: update_file(m, h, 'B', b)
    with pytest.raises(ValueError, match='no_canonical_attempt|det_observations_missing'):
        m.canonical_attempt(m.enumerate_attempts(h, Proofs()), f[1])
    with pytest.raises(ValueError): canonical_trial(m, f)


def test_key_length_prefix_and_ties_use_utf8_identifiers(m, monkeypatch):
    assert m.key(b'seed', 'a', 'bc') != m.key(b'seed', 'ab', 'c')
    assert m.assignment_id('pilot', 'u', 't', 'native_goal', 'worker', 0) == hashlib.sha256(
        b''.join(len(v.encode()).to_bytes(4, 'big') + v.encode() for v in
                 ('mission-884-assignment', 'pilot', 'u', 't', 'native_goal', 'worker', '0'))).hexdigest()
    manifest = json.loads(pool(m, inputs(m, 16)))
    monkeypatch.setattr(m, 'key', lambda *args: b'collision')
    selection = m.select(b'seed', manifest, m.ARMS)
    assert selection['pilot'] == sorted(selection['primary'], key=lambda s: s.encode())[:12]
    assert selection['ranking'] == sorted(selection['primary'], key=lambda s: s.encode())[12:]
    assert [a['assignment_id'] for a in selection['assignments']] == sorted(a['assignment_id'] for a in selection['assignments'])
    monkeypatch.setattr(m, 'assignment_id', lambda *args: 'collision')
    with pytest.raises(ValueError, match='assignment_id_duplicate'): m.select(b'seed', manifest, m.ARMS)


@pytest.mark.parametrize('kind', ['G1', 'G2', 'G3'])
def test_shared_lineage_across_benchmarks_collapses_units(m, kind):
    data = inputs(m); one, two = data[0][:2]; two['benchmark'] = 'other-suite'
    if kind == 'G1': two['repository'] = one['repository'].upper() + '.git'
    if kind == 'G2': two['roots'] = one['roots']
    if kind == 'G3': two['base_files'] = one['base_files']
    data[1]['snapshot_digest'] = m.snapshot_digest(data[0])
    manifest = json.loads(pool(m, data))
    assert manifest['tasks'][0]['unit_id'] == manifest['tasks'][1]['unit_id']
    assert any(pair[kind] for pair in manifest['lineage'])


@pytest.mark.parametrize('criterion,defect', [('Lic', 'benchmark-license'), ('Lic', 'upstream-license'),
    ('Con', 'task-id'), ('Con', 'repo-name'), ('Con', 'source-lines'), ('Cx', 'files'),
    ('Cx', 'checks'), ('Cx', 'lines'), ('Cx', 'natural'), ('Det', 'starter'), ('Det', 'reference'),
    ('Det', 'unstable'), ('Det', 'pass-to-pass'), ('Det', 'missing-case'), ('Det', 'missing-image')])
def test_first_failing_criterion_preserves_values_and_evidence(m, criterion, defect):
    data = inputs(m); t = data[0][0]
    if defect == 'benchmark-license': t['benchmark_license'] = 'unknown'
    if defect == 'upstream-license': t['upstream_license'] = 'unknown'
    if defect == 'task-id': data[2]['mission']['README.md'] = t['task_id']
    if defect == 'repo-name': data[2]['packages'][m.ARMS[0]]['SKILL.md'] = 'owner/project-0'
    if defect == 'source-lines': data[2]['mission']['README.md'] = '\n'.join(t['changes'][0]['added'][:3])
    if defect == 'files':
        t['changes'][1]['path'] = 'tests/a.py'
        t['changes'][0]['added'] += ['additional long source statement for the same module'] * 5
    if defect == 'checks': t['checks'].pop()
    if defect == 'lines': t['changes'][0]['added'] = []
    if defect == 'natural': t['natural_request'] = False
    if defect == 'starter': data[3][t['task_id']]['starter'] = [observed(True)] * 3
    if defect == 'reference': data[3][t['task_id']]['reference'] = [observed()] * 3
    if defect == 'pass-to-pass': data[3][t['task_id']]['starter'][0]['cases'][1]['passed'] = False
    if defect == 'unstable': data[3][t['task_id']]['starter'] = [observed(), observed(True), observed()]
    if defect == 'missing-case': data[3][t['task_id']]['reference'] = [{'cases': []}] * 3
    if defect == 'missing-image': t['environment']['image'] = 'mutable:latest'
    data[1]['snapshot_digest'] = m.snapshot_digest(data[0]); data[1]['scope_digest'] = m.digest(m.canonical(data[2]))
    row = json.loads(pool(m, data))['tasks'][0]
    assert row['accepted'] is False and row['reason'] == criterion
    assert row['criteria'][criterion]['accepted'] is False
    assert row['criteria'][criterion]['evidence_digest'].startswith('sha256:')
    assert row['criteria'][criterion]['values'] is not None


def test_pool_is_canonical_complete_and_snapshot_scope_bound(m):
    data = inputs(m)
    raw = pool(m, data)
    assert raw == m.canonical(json.loads(raw)) == pool(m, copy.deepcopy(data))
    assert len(json.loads(raw)['tasks']) == 16
    data[0].append(copy.deepcopy(data[0][0])); data[1]['snapshot_digest'] = m.snapshot_digest(data[0])
    with pytest.raises(ValueError, match='task_id_duplicate'): pool(m, data)
    data = inputs(m); data[2]['mission']['README.md'] = 'different'
    with pytest.raises(ValueError, match='scope_digest_mismatch'): pool(m, data)


def test_valid_withdrawal_without_next_attempt_invalidates_cohort(m):
    f = fixture(m); put(m, f[0], 1, 'W', 'e' * 40, m.canonical({'reason': 'package_change'}), 600)
    with pytest.raises(ValueError): canonical_trial(m, f)


def test_con_source_matches_require_three_contiguous_lines_in_one_source_file(m):
    data = inputs(m); t = data[0][0]
    # Long matching lines separated by a short changed line cannot be joined.
    t['changes'][1]['added'] = [v + ' distinct module' for v in t['changes'][1]['added']]
    t['changes'][0]['added'].insert(1, 'x')
    data[2]['mission']['README.md'] = '\n'.join(v for v in t['changes'][0]['added'][:4] if v != 'x')
    data[1]['snapshot_digest'] = m.snapshot_digest(data[0]); data[1]['scope_digest'] = m.digest(m.canonical(data[2]))
    assert json.loads(pool(m, data))['tasks'][0]['accepted'] is True
    # Two lines from one source and one from another are also not contiguous.
    t['changes'][0]['added'] = [v for v in t['changes'][0]['added'] if v != 'x'][:2]
    t['changes'][1]['added'] += ['another long statement used to keep the threshold'] * 4
    data[2]['mission']['README.md'] = '\n'.join(t['changes'][0]['added'] + t['changes'][1]['added'][:1])
    data[1]['snapshot_digest'] = m.snapshot_digest(data[0]); data[1]['scope_digest'] = m.digest(m.canonical(data[2]))
    assert json.loads(pool(m, data))['tasks'][0]['reason'] != 'Con'


def test_g3_threshold_and_transitive_roots_collapse_without_inflating_units(m):
    data = inputs(m); a, b, c = data[0][:3]
    a['base_files'] = {str(i): '\n'.join(f'source {i} value {n}' for n in range(4)) for i in range(5)}
    b['base_files'] = {'one': a['base_files']['0']} | {str(i): '\n'.join(f'other {i} value {n}' for n in range(4)) for i in range(4)}
    b['roots'] += c['roots']
    data[1]['snapshot_digest'] = m.snapshot_digest(data[0])
    manifest = json.loads(pool(m, data))
    assert len({r['unit_id'] for r in manifest['tasks'][:3]}) == 1
    data[1]['g3_percent'] = 21
    manifest = json.loads(pool(m, data))
    assert manifest['tasks'][0]['unit_id'] != manifest['tasks'][1]['unit_id']


@pytest.mark.parametrize('path', ['tests/a.py', 'test/a.py', 'spec/a.py', 'src/test_a.py',
    'src/a_test.py', 'src/a.test.js', 'src/a.spec.ts'])
def test_cx_test_files_do_not_count_as_changed_modules(m, path):
    data = inputs(m); data[0][0]['changes'][1]['path'] = path
    data[1]['snapshot_digest'] = m.snapshot_digest(data[0])
    assert json.loads(pool(m, data))['tasks'][0]['reason'] == 'Cx'


def test_primary_tie_uses_task_id_and_pilot_controls_are_disjoint(m, monkeypatch):
    data = inputs(m, 18); data[0][1]['repository'] = data[0][0]['repository']
    data[1]['snapshot_digest'] = m.snapshot_digest(data[0])
    manifest = json.loads(pool(m, data))
    monkeypatch.setattr(m, 'key', lambda *args: b'collision')
    c = m.select(b'seed', manifest, m.ARMS)
    assert c['primary'][manifest['tasks'][0]['unit_id']] == 'task-000'
    d = m.confirm(b'seed', c, 'c' * 40, 5, m.ARMS)
    assert len(d['control']) == 3 and not set(c['pilot']) & set(c['ranking'][:5])
    assert len(d['assignments']) == 8 * len(m.ARMS)
    assert all(a['replicate'] == 0 for a in d['assignments'])


@pytest.mark.parametrize('value', [1.5, 2**53, '\ud800', {1: 'non-string'}])
def test_canonical_json_rejects_ambiguous_cross_runtime_values(m, value):
    with pytest.raises((ValueError, UnicodeError)): m.canonical(value)


def test_canonical_json_uses_utf16_keys_and_normalized_snapshot_tar(m):
    assert m.canonical({'\ue000': 1, '\U0001f600': 2}) == '{"😀":2,"\ue000":1}'.encode()
    assert m.snapshot_digest([{'a': 1, 'b': 2}]) == m.snapshot_digest([{'b': 2, 'a': 1}])


@pytest.mark.parametrize('value', [0, -1, True, '3'])
def test_invalid_frozen_thresholds_cannot_accept_tasks(m, value):
    data = inputs(m); data[1]['cx_lines'] = value
    with pytest.raises(ValueError, match='threshold_invalid'): pool(m, data)


def test_snapshot_digest_and_missing_root_evidence_cannot_be_accepted(m):
    data = inputs(m); data[0][0]['base_commit'] = 'different'
    with pytest.raises(ValueError, match='snapshot_digest_mismatch'): pool(m, data)
    data = inputs(m); data[0][0]['roots'] = []; data[1]['snapshot_digest'] = m.snapshot_digest(data[0])
    with pytest.raises(ValueError, match='lineage_roots_missing'): pool(m, data)


@pytest.mark.parametrize('defect', [None, 'repeat-cursor', 'total-changed', 'missing-page', 'head-moved', 'invalid-total'])
def test_provider_adapter_drains_pages_and_freezes_one_main_snapshot(m, defect):
    h = fixture(m)[0]; calls = []
    class Provider:
        def main_head(self): return 'head-2' if defect == 'head-moved' and calls else 'head-1'
        def main_history(self, path):
            assert path == m.ATTEMPT_PATH
            return {k: v for k, v in h.items() if k not in ('prs', 'total_prs')}
        def merged_pr_page(self, path, cursor):
            assert path == m.ATTEMPT_PATH; calls.append(cursor)
            index = 0 if cursor is None else int(cursor)
            return {'items': h['prs'][index:index + 2],
                    'total': 4.0 if defect == 'invalid-total' else 5 if defect == 'total-changed' and not index else 4,
                    'next': cursor if defect == 'repeat-cursor' and index else
                            None if index or defect == 'missing-page' else '2'}
    if defect:
        with pytest.raises(ValueError, match='pagination_total_invalid' if defect == 'invalid-total' else 'main_moved' if defect == 'head-moved' else 'pagination_total_changed' if defect == 'total-changed' else 'pagination_'): m.acquire_history(Provider())
    else:
        assert m.acquire_history(Provider()) == h and calls == [None, '2']


@pytest.mark.parametrize('finding', ['H1', 'M1', 'M2', 'L1', 'L2', 'L3-case-count', 'L3-row', 'L5', 'empty-source', 'deleted-source'])
def test_exploration_pool_regressions(m, finding):
    data = inputs(m); t = data[0][0]
    if finding == 'H1':
        data[3].pop(t['task_id'])
        with pytest.raises(ValueError, match='det_observations_missing'): pool(m, data)
        return
    if finding == 'L3-row':
        data[3][t['task_id']]['starter'][0]['case_count'] = 3.5
        assert json.loads(pool(m, data))['tasks'][0]['reason'] == 'Det'
        return
    if finding == 'L5':
        value = None
        for _ in range(2000): value = [value]
        with pytest.raises(ValueError, match='canonical_schema_invalid'): m.canonical(value)
        return
    if finding == 'deleted-source': t['changes'][1]['deleted'] = t['changes'][1]['added']; t['changes'][1]['added'] = []
    if finding == 'empty-source': t['changes'][0]['added'] *= 2; t['changes'][1]['added'] = []
    if finding == 'M1': data[2]['mission']['README.md'] = 'Owner/Project-0'
    if finding == 'M2': t['base_files'] = {'tiny': 'one line'}
    if finding == 'L1': data[2]['mission']['README.md'] = '\n'.join(t['changes'][0]['added'][:3])
    if finding == 'L2': t['changes'] += [t['changes'][0] | {'path': './src/a.py'}, t['changes'][0] | {'path': 'src//a.py'}]
    if finding == 'L3-case-count':
        t['checks'] = t['checks'][:1]
        result = {'status': 'passed', 'reason': None, 'case_count': True, 'cases': [{'name': 'repair', 'passed': True}]}
        with pytest.raises(ValueError, match='det_cases_invalid'): m.case_values(result, t['checks'])
        return
    data[1]['snapshot_digest'] = m.snapshot_digest(data[0]); data[1]['scope_digest'] = m.digest(m.canonical(data[2]))
    b = json.loads(pool(m, data)); row = b['tasks'][0]
    if finding == 'empty-source': assert row['reason'] == 'Cx' and row['criteria']['Cx']['values']['source_files'] == 1
    if finding == 'M1': assert row['reason'] == 'Con'
    if finding == 'M2': assert len({r['unit_id'] for r in b['tasks']}) == 16 and not b['lineage'][0]['G3']
    if finding == 'L1': assert row['criteria']['Con']['values'] == {'identifier_absent': True, 'source_match': True}
    if finding in ('L2', 'deleted-source'): assert row['criteria']['Cx']['values']['source_files'] == 2


@pytest.mark.parametrize('defect', ['B-only', 'W-only', 'broken-A', 'duplicate-A', 'wrong-number', 'missing-chain-key'])
def test_later_v1_invalid_attempt_does_not_poison_canonical_attempt(m, defect):
    f = fixture(m)
    stage = 'B' if defect == 'B-only' else 'W' if defect == 'W-only' else 'A'
    raw = b'{' if defect == 'broken-A' else b'{"number":2,"number":2}' if defect == 'duplicate-A' else m.canonical({'number': 9})
    if defect == 'missing-chain-key':
        a = json.loads(f[0]['prs'][0]['files'][next(iter(f[0]['prs'][0]['files']))]); del a['chain']['hash']; raw = m.canonical(a | {'number': 2, 'round': 200})
    if defect == 'wrong-number': raw = m.canonical(json.loads(f[0]['prs'][0]['files'][next(iter(f[0]['prs'][0]['files']))]) | {'number': 9, 'round': 200})
    put(m, f[0], 2, stage, '2' * 40, raw, 2000)
    if defect in ('wrong-number', 'missing-chain-key'): put(m, f[0], 2, 'B', '3' * 40, f[0]['prs'][1]['files'][next(iter(f[0]['prs'][1]['files']))], 2030)
    attempts = m.enumerate_attempts(f[0], Proofs())
    assert attempts[1]['invalid_reason'] and canonical_trial(m, f)['number'] == 1


@pytest.mark.parametrize('defect', ['separate-P', 'B-before-A', 'merge-order', 'missing-A-key'])
def test_review_attempt_route_and_missing_keys(m, defect):
    f = fixture(m)
    if defect == 'missing-A-key':
        a = json.loads(f[0]['prs'][0]['files'][next(iter(f[0]['prs'][0]['files']))]); del a['snapshot_digest']; update_file(m, f[0], 'A', a)
        attempts = second(m, f, new_pool=True)
        assert attempts[0]['invalid_reason'] == 'V1_invalid_A' and m.canonical_attempt(attempts, f[1])['number'] == 2
    elif defect == 'merge-order':
        put(m, f[0], 2, 'B', '2' * 40, b'{}', 50)
        with pytest.raises(ValueError, match='attempt_order_invalid'): m.enumerate_attempts(f[0], Proofs())
    else:
        attempts = m.enumerate_attempts(f[0], Proofs()); stages = attempts[0]['stages']
        if defect == 'separate-P': stages['P']['sha'] = '9' * 40
        else: stages['A']['merged_at'] = stages['B']['merged_at'] + 1
        with pytest.raises(ValueError, match='no_canonical_attempt'): m.canonical_attempt(attempts, f[1])


def canonical_trial(m, f):
    return m.canonical_attempt(m.enumerate_attempts(f[0], Proofs()), f[1])


@pytest.mark.parametrize('defect', ['late-A-proof', 'round-reuse', 'pool-reuse'])
def test_frozen_inputs_and_preregistration_evidence_cannot_be_replaced(m, defect):
    f = fixture(m)
    if defect in ('late-A-proof', 'round-reuse', 'pool-reuse'):
        attempts = second(m, f, withdrawal=600, a_time=700)
        if defect == 'late-A-proof': attempts[1]['stages']['A']['time'] = 600
        if defect == 'round-reuse': attempts[1]['a']['round'] = 100
        if defect == 'pool-reuse':
            # Same withdrawn pool despite a fresh round is not a fresh trial.
            attempts[1]['a']['snapshot_digest'] = attempts[0]['a']['snapshot_digest']
            f[1][2] = copy.deepcopy(f[1][1])
            a = attempts[1]['a']
            attempts[1]['stages']['A']['raw'] = m.canonical(a)
            attempts[1]['stages']['P']['raw'] = b'# Frozen registration\nattempt_digest: ' + m.digest(m.canonical(a)).encode()
            attempts[1]['stages']['B']['raw'] = m.generate_pool(f[1][1]['snapshot'], a, f[1][1]['scope'],
                f[1][1]['observations'], commit_a=attempts[1]['stages']['A']['sha'])
        with pytest.raises(ValueError, match='no_canonical_attempt'): m.canonical_attempt(attempts, f[1])


@pytest.mark.parametrize('defect', ['pool-reuse-new-snapshot', 'status-case-mismatch'])
def test_declared_metadata_cannot_replace_execution_or_pool_identity(m, defect):
    f = fixture(m)
    if defect == 'pool-reuse-new-snapshot':
        attempts = second(m, f, withdrawal=600, a_time=700)
        material = copy.deepcopy(f[1][1]); material['snapshot'][0]['natural_request'] = True
        # Change only reference text: snapshot digest changes but admitted task identities are the same.
        material['snapshot'][0]['changes'][0]['added'][0] += ' harmless whitespace'
        a = attempts[1]['a']; a['snapshot_digest'] = m.snapshot_digest(material['snapshot'])
        a['prior_attempts_digest'] = m.prior_digest(attempts[:1], before=700)
        attempts[1]['stages']['A']['raw'] = m.canonical(a)
        attempts[1]['stages']['P']['raw'] = b'# Frozen registration\nattempt_digest: ' + m.digest(m.canonical(a)).encode()
        attempts[1]['stages']['B']['raw'] = m.generate_pool(material['snapshot'], a, material['scope'], material['observations'], commit_a='1' * 40)
        f[1][2] = material
        with pytest.raises(ValueError, match='no_canonical_attempt'): m.canonical_attempt(attempts, f[1])
        return
    if defect == 'status-case-mismatch':
        result = observed(True); result['status'] = 'failed'; result['reason'] = 'contract_mismatch'
        with pytest.raises(ValueError, match='det_cases_invalid'): m.case_values(result, task(0)['checks'])
        return


@pytest.mark.parametrize('finding', ['M4', 'L3-protection', 'L3-total', 'L4', 'L6a', 'H1-cohort'])
def test_exploration_verification_regressions(m, finding):
    f = fixture(m)
    if finding == 'M4':
        a = json.loads(f[0]['prs'][0]['files'][next(iter(f[0]['prs'][0]['files']))]); a['chain']['genesis'] += 1000
        update_file(m, f[0], 'A', a)
        f[0]['prs'][2]['merged_at'] = 2001; f[0]['prs'][3]['merged_at'] = 2100
        for r in f[3]: r['started_at'] += 1000
    if finding == 'L3-protection': f[0]['protection']['force_push'] = 0
    if finding == 'L3-total': f[0]['prs'] = []; f[0]['commits'] = []; f[0]['total_prs'] = False; f[0]['current_files'] = {}
    if finding == 'L3-total':
        with pytest.raises(ValueError): m.enumerate_attempts(f[0], Proofs())
        return
    if finding == 'L4':
        path = next(p for p in f[0]['current_files'] if p.endswith('preregistration.md'))
        raw = b'# incidental digest\n' + m.digest(f[0]['prs'][0]['files'][next(iter(f[0]['prs'][0]['files']))]).encode()
        for c in f[0]['commits'] + f[0]['prs']:
            if path in c['files']: c['files'][path] = raw
        f[0]['current_files'][path] = raw
        f[0]['prs'][0]['proofs'][path]['signature'] = sign((m.digest(raw) + ':101').encode())
    if finding in ('L6a', 'H1-cohort'):
        attempts = second(m, f, new_pool=True); a = attempts[1]['a']
        if finding == 'L6a': f[1].pop(1)
        else:
            b = json.loads(attempts[0]['stages']['B']['raw']); b['tasks'][0]['det'] = None; attempts[0]['stages']['B']['raw'] = m.canonical(b)
            a['prior_attempts_digest'] = m.prior_digest(attempts[:1], before=attempts[1]['stages']['A']['merged_at']); attempts[1]['stages']['A']['raw'] = m.canonical(a)
            attempts[1]['stages']['P']['raw'] = b'attempt_digest: ' + m.digest(m.canonical(a)).encode()
        if finding == 'H1-cohort': assert m.canonical_attempt(attempts, f[1])['number'] == 2
        else:
            with pytest.raises(ValueError, match='attempt_materials_unknown'): m.canonical_attempt(attempts, f[1])
        return
    with pytest.raises(ValueError): canonical_trial(m, f)


@pytest.mark.parametrize('missing', ['number', 'margin', 'chain', 'snapshot_digest', 'scope_digest', 'generator_sha',
    'prior_attempts_digest', 'licenses', 'store_contents', 'cx_files', 'cx_checks', 'cx_lines', 'con_length',
    'con_lines', 'g3_percent', 'det_mode', 'packages', 'pilot_arms', 'confirmatory_arms',
    'chain.hash', 'chain.public_key', 'chain.genesis', 'chain.period'])
@pytest.mark.parametrize('next_round', [99, 100, 200])
def test_v1_invalid_attempt_still_reserves_readable_round(m, missing, next_round):
    f = fixture(m); second(m, f, new_pool=True, round_number=next_round, a_time=700)
    a = json.loads(f[0]['prs'][0]['files'][next(iter(f[0]['prs'][0]['files']))])
    if missing.startswith('chain.'): del a['chain'][missing.split('.')[1]]
    else: del a[missing]
    update_file(m, f[0], 'A', a)
    attempts = m.enumerate_attempts(f[0], Proofs())
    attempts[1]['a']['prior_attempts_digest'] = m.prior_digest(attempts[:1], before=700)
    raw = m.canonical(attempts[1]['a']); attempts[1]['stages']['A']['raw'] = raw
    attempts[1]['stages']['P']['raw'] = b'attempt_digest: ' + m.digest(raw).encode()
    assert attempts[0]['invalid_reason'] == 'V1_invalid_A'
    if next_round <= 100:
        with pytest.raises(ValueError, match='attempt_chain_invalid'): m.canonical_attempt(attempts, f[1])
    else: assert m.canonical_attempt(attempts, f[1])['number'] == 2


@pytest.mark.parametrize('round_value', [None, True, 0, '100', 100.5, 'broken-json', 'duplicate-json', 'B-only'])
def test_unreadable_earlier_round_blocks_promotion_as_unknown(m, round_value):
    f = fixture(m); second(m, f, new_pool=True)
    a = json.loads(f[0]['prs'][0]['files'][next(iter(f[0]['prs'][0]['files']))])
    if round_value is None: del a['round']
    else: a['round'] = round_value
    raw = (b'{' if round_value == 'broken-json' else b'{"round":100,"round":200}' if round_value == 'duplicate-json'
           else json.dumps(a).encode())
    update_file(m, f[0], 'A', raw)
    if round_value == 'B-only':
        for c in f[0]['commits'] + f[0]['prs']:
            c['files'] = {p: v for p, v in c['files'].items() if not p.endswith(('attempt.json', 'preregistration.md')) or '/0002/' in p}
        f[0]['commits'] = [c for c in f[0]['commits'] if c['files']]
        f[0]['prs'] = [c for c in f[0]['prs'] if c['files']]
        f[0]['total_prs'] = len(f[0]['prs']); f[0]['reachable'] = [c['sha'] for c in f[0]['commits']]
        f[0]['current_files'] = {p: v for c in f[0]['commits'] for p, v in c['files'].items()}
    attempts = m.enumerate_attempts(f[0], Proofs()); a2 = attempts[1]['a']
    a2['prior_attempts_digest'] = m.prior_digest(attempts[:1], before=1300); update_file(m, f[0], 'A', a2, number=2)
    with pytest.raises(m.UnknownAttempt, match='attempt_round_unknown'): canonical_trial(m, f)


@pytest.mark.parametrize('declaration', ['pilot_arms', 'confirmatory_arms'])
@pytest.mark.parametrize('value', [[], ['unknown'], ['native_goal', 'native_goal'], 'native_goal', [True], {'native_goal': True}])
def test_a_requires_nonempty_known_unique_arm_declarations(m, declaration, value):
    f = fixture(m); a = json.loads(f[0]['prs'][0]['files'][next(iter(f[0]['prs'][0]['files']))]); a[declaration] = value
    update_file(m, f[0], 'A', a)
    assert m.enumerate_attempts(f[0], Proofs())[0]['invalid_reason'] == 'V1_invalid_A'
    with pytest.raises(ValueError, match='no_canonical_attempt'): canonical_trial(m, f)


def test_p_binding_requires_declaration_not_just_available_digest(m):
    f = fixture(m); update_file(m, f[0], 'P', b'# Frozen registration\n')
    with pytest.raises(ValueError, match='no_canonical_attempt'): canonical_trial(m, f)


def test_snapshot_reuse_is_rejected_when_thresholds_change_pool_identity(m):
    f = fixture(m); attempts = second(m, f, withdrawal=600, a_time=700)
    f[1][2] = copy.deepcopy(f[1][1]); a = attempts[1]['a']
    a.update(snapshot_digest=attempts[0]['a']['snapshot_digest'], cx_lines=100)
    raw = m.canonical(a); attempts[1]['stages']['A']['raw'] = raw
    attempts[1]['stages']['P']['raw'] = b'attempt_digest: ' + m.digest(raw).encode()
    material = f[1][2]; attempts[1]['stages']['B']['raw'] = m.generate_pool(material['snapshot'], a, material['scope'], material['observations'], commit_a='1' * 40)
    assert m.pool_identity(json.loads(attempts[0]['stages']['B']['raw'])) != m.pool_identity(json.loads(attempts[1]['stages']['B']['raw']))
    with pytest.raises(ValueError, match='no_canonical_attempt'): m.canonical_attempt(attempts, f[1])


@pytest.mark.parametrize('stage', ['A', 'B'])
def test_timestamp_proof_binds_selection_stage_bytes(m, stage):
    f = fixture(m); pr = f[0]['prs'][0 if stage == 'A' else 1]; proof = pr['proofs'][next(iter(pr['files']))]
    proof['signature'] = sign(('sha256:' + '0' * 64 + ':' + str(proof['time'])).encode())
    with pytest.raises(ValueError, match='no_canonical_attempt'): canonical_trial(m, f)


def test_selection_imports_and_verifies_without_cohort(m, monkeypatch):
    import builtins
    original = builtins.__import__
    def isolated(name, *args, **kwargs):
        if 'bench_cohort' in name: raise ImportError('second PR is absent')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', isolated)
    spec = importlib.util.spec_from_file_location('isolated_selection', BENCH / 'bench_selection.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    assert canonical_trial(module, fixture(module))['number'] == 1


def test_frozen_generator_revision_must_match_loaded_materials(m):
    f = fixture(m); f[1][1]['generator_sha'] = '0' * 40
    with pytest.raises(ValueError, match='no_canonical_attempt'): canonical_trial(m, f)


@pytest.mark.parametrize('arms', [['native_goal'], ['mission_baseline'], ['mission_verified_complex'],
    ['native_goal', 'mission_baseline'], ['native_goal', 'mission_verified_complex'],
    ['mission_baseline', 'mission_verified_complex'], list(('native_goal', 'mission_baseline', 'mission_verified_complex'))])
def test_selection_generators_allow_each_nonempty_registered_arm_subset(m, arms):
    manifest = json.loads(pool(m, inputs(m))); selection = m.select(b'seed', manifest, arms)
    confirmation = m.confirm(b'seed', selection, 'c' * 40, 4, arms)
    assert len(selection['assignments']) == 24 * len(arms)
    assert len(confirmation['assignments']) == 6 * len(arms)
    assert {r['arm'] for r in selection['assignments']} == {r['arm'] for r in confirmation['assignments']} == set(arms)
