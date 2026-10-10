"""Cohort replay and evidence contracts, using selection fixtures one way."""
import json
import hashlib
from types import SimpleNamespace
import pytest
from .test_issue927_bench_selection import m, fixture, inputs, pool, task, observed, second, update_file, Proofs, sign, SIZE
import bench_cohort as cohort


@pytest.fixture(autouse=True)
def bind_selection(m, monkeypatch):
    monkeypatch.setattr(cohort, 'selection_core', m)


def check(m, f, used=1, rerun=None):
    h, materials, beacon, records = f
    if rerun is None:
        rerun = lambda number, task_id, variant: materials[number]['observations'][task_id][variant][0]
    return cohort.verify_cohort(h, materials, used, beacon, records, Proofs(), rerun)


def test_complete_recalculation_produces_checks_one_to_three(m):
    result = check(m, fixture(m))
    assert result['status'] == 'valid' and result['canonical_attempt'] == 1
    assert result['checks'] == {'1': True, '2': True, '3': True}
    assert result['lineage_digest'].startswith('sha256:')


@pytest.mark.parametrize('variant', ['starter', 'reference'])
def test_det_rerun_mismatch_never_promotes_a_later_attempt(m, variant):
    f = fixture(m); second(m, f)
    def replay(number, task_id, current):
        result = json.loads(json.dumps(f[1][number]['observations'][task_id][current][0]))
        if current == variant: result['cases'][1]['passed'] = False; result.update(status='failed', reason='contract_mismatch')
        return result
    result = check(m, f, rerun=replay)
    assert result['status'] == 'invalid_cohort' and result['canonical_attempt'] == 1
    assert result['reason'] == 'det_replay_mismatch'


def test_audit_contains_all_selected_and_59_per_det_stratum_by_key(m):
    data = inputs(m, 150)
    for t in data[0][75:]: data[3][t['task_id']]['starter'] = [observed(True)] * 3
    manifest = json.loads(pool(m, data)); seed = b'seed'
    chosen = [next(t['task_id'] for t in manifest['tasks'] if t['task_id'] not in cohort.audit_tasks(seed, manifest, []))]
    ids = cohort.audit_tasks(seed, manifest, chosen)
    expected = set(chosen)
    for rows in (manifest['tasks'][:75], manifest['tasks'][75:]):
        expected.update(t['task_id'] for t in sorted(rows, key=lambda t: (m.key(seed, 'det-audit', t['task_id']), t['task_id'].encode()))[:59])
    assert set(ids) == expected
    assert len(cohort.audit_tasks(seed, json.loads(pool(m, inputs(m, 16))), [])) == 16


@pytest.mark.parametrize('defect', ['signature', 'randomness', 'package', 'selection', 'D-recalculation',
    'D-merged-time', 'D-proof-time', 'pre-C-worker', 'pre-D-worker', 'duplicate-assignment'])
def test_postseed_failure_invalidates_cohort_without_fallback(m, defect):
    f = fixture(m); h, _, beacon, records = f
    if defect == 'signature': beacon['signature'] = '00' * SIZE
    if defect == 'randomness': beacon['randomness'] = '00' * 32
    if defect == 'package': records[0]['package']['digest'] = 'sha256:' + '0' * 64
    if defect in ('selection', 'D-recalculation', 'duplicate-assignment'):
        stage, index = ('C', 2) if defect == 'selection' else ('D', 3)
        value = json.loads(h['prs'][index]['files'][next(iter(h['prs'][index]['files']))])
        if defect == 'selection': value['pilot'].reverse()
        elif defect == 'duplicate-assignment': value['assignments'][1]['assignment_id'] = value['assignments'][0]['assignment_id']
        else: value['control'].reverse()
        update_file(m, h, stage, value)
    if defect == 'D-merged-time': h['prs'][3]['merged_at'] = 1200
    if defect == 'D-proof-time':
        path = next(iter(h['prs'][3]['files'])); h['prs'][3]['proofs'][path] = {
            'time': 1200, 'signature': sign((m.digest(h['prs'][3]['files'][path]) + ':1200').encode())}
    if defect == 'pre-C-worker':
        pilot = json.loads(h['prs'][2]['files'][next(iter(h['prs'][2]['files']))])['assignments'][0]
        records.append({k: pilot[k] for k in ('task_id', 'unit_id', 'arm')} | {'package': records[0]['package'], 'started_at': 1000})
        records[-1]['package'] = json.loads(h['prs'][0]['files'][next(iter(h['prs'][0]['files']))])['packages'][pilot['arm']]
    if defect == 'pre-D-worker': records[0]['started_at'] = 1099
    second(m, f)
    result = check(m, f)
    assert result['status'] == 'invalid_cohort'
    assert result['canonical_attempt'] == 1
    if defect == 'pre-C-worker': assert result['reason'] == 'worker_before_selection'


def test_det_transport_delegates_to_i2c_with_bound_candidate(m, monkeypatch, tmp_path):
    snapshot, bundle, _, replay, calls = bound_job(m, monkeypatch, tmp_path)
    replay.bind_snapshot(1, [snapshot], m.snapshot_digest([snapshot]))
    assert replay(1, snapshot['task_id'], 'starter') == observed()
    assignment = replay.jobs[1, snapshot['task_id'], 'starter'][1]
    assert calls == [(bundle, assignment, 'worker', tmp_path, {'candidate_digest': snapshot['det_binding']['starter']})]


def test_det_adapter_rejects_a_job_for_a_different_task_before_evaluating(m, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cohort.public, 'freeze_candidate', lambda *args: calls.append('freeze') or {})
    monkeypatch.setattr(cohort.public, 'evaluate_assignment', lambda *args: calls.append('evaluate') or observed())
    jobs = {(1, 'task-a', 'starter'): ('bundle', {'task_id': 'task-b', 'worker_export': 'worker'}, tmp_path)}
    with pytest.raises(ValueError, match='det_job_task_mismatch'):
        cohort.BundleReplay(jobs)(1, 'task-a', 'starter')
    assert calls == []


@pytest.mark.parametrize('stage', ['D'])
def test_timestamp_proof_is_bound_to_exact_file_digest(m, stage):
    f = fixture(m); pr = f[0]['prs'][{'A': 0, 'B': 1, 'D': 3}[stage]]
    proof = pr['proofs'][next(iter(pr['files']))]
    proof['signature'] = sign(('sha256:' + '0' * 64 + ':' + str(proof['time'])).encode())
    assert check(m, f)['status'] == 'invalid_cohort'


def test_all_mode_replays_every_det_task_including_deterministic_rejections(m):
    f = fixture(m, 18); material = f[1][1]
    t = material['snapshot'][-1]
    material['observations'][t['task_id']]['starter'] = [observed(True)] * 3
    regenerate_committed(m, f)
    calls = []
    def replay(number, task_id, variant):
        calls.append((task_id, variant)); return material['observations'][task_id][variant][0]
    result = check(m, f, rerun=replay)
    assert result['status'] == 'valid' and len(calls) == 36
    assert (t['task_id'], 'starter') in calls


def regenerate_committed(m, f):
    h, materials, beacon, records = f
    a = json.loads(h['prs'][0]['files'][next(iter(h['prs'][0]['files']))]); material = materials[1]
    b = json.loads(m.generate_pool(material['snapshot'], a, material['scope'], material['observations'], commit_a='a' * 40))
    c = m.select(bytes.fromhex(beacon['randomness']), b, a['pilot_arms'])
    d = m.confirm(bytes.fromhex(beacon['randomness']), c, 'c' * 40, 4, a['confirmatory_arms'])
    for stage, value in [('B', b), ('C', c), ('D', d)]: update_file(m, h, stage, value)
    material['lineage_digest'] = b['lineage_digest']
    records[:] = [{'task_id': r['task_id'], 'unit_id': r['unit_id'], 'arm': r['arm'],
                  'package': a['packages'][r['arm']], 'started_at': 1200 + i} for i, r in enumerate(d['assignments'])]


def test_audit_mode_replays_selected_and_stratified_tasks_only(m):
    f = fixture(m, 130); h, materials, beacon, _ = f
    a = json.loads(h['prs'][0]['files'][next(iter(h['prs'][0]['files']))]); a['det_mode'] = 'audit'
    update_file(m, h, 'A', a)
    calls = []
    def replay(number, task_id, variant):
        calls.append(task_id); return materials[1]['observations'][task_id][variant][0]
    result = check(m, f, rerun=replay)
    assert result['status'] == 'valid'
    b = json.loads(h['prs'][1]['files'][next(iter(h['prs'][1]['files']))])
    c = json.loads(h['prs'][2]['files'][next(iter(h['prs'][2]['files']))])
    chosen = [c['primary'][u] for u in c['pilot'] + c['ranking'][:4]]
    assert set(calls) == set(cohort.audit_tasks(bytes.fromhex(beacon['randomness']), b, chosen))
    assert len(calls) < 260


def test_collect_det_uses_i2c_provider_three_times_per_variant_after_early_filters(m):
    data = inputs(m, 3); data[0][0]['benchmark_license'] = 'unknown'
    data[1]['snapshot_digest'] = m.snapshot_digest(data[0]); calls = []
    def replay(number, task_id, variant):
        calls.append((number, task_id, variant)); return observed(variant == 'reference')
    actual = cohort.collect_det(data[0], data[1], data[2], 1, replay)
    assert set(actual) == {'task-001', 'task-002'} and len(calls) == 12
    assert calls.count((1, 'task-001', 'starter')) == 3


def bound_job(m, monkeypatch, tmp_path):
    snapshot = task(0)
    snapshot['det_binding'] = {'bundle_digest': 'sha256:' + '1' * 64,
                              'starter': 'sha256-tree-exec-v1:' + '2' * 64,
                              'reference': 'sha256-tree-exec-v1:' + '3' * 64}
    bundle_task = {key: snapshot[key] for key in ('task_id', 'benchmark', 'base_commit', 'environment', 'checks')}
    bundle_task['upstream'] = snapshot['repository']
    bundle = SimpleNamespace(digest=snapshot['det_binding']['bundle_digest'], task=lambda _: bundle_task,
                             manifest_bytes=m.canonical({'benchmarks': [{'name': snapshot['benchmark'], 'revision': snapshot['revision']}]}))
    assignment = {'task_id': snapshot['task_id'], 'worker_export': 'worker'}
    replay = cohort.BundleReplay({(1, snapshot['task_id'], 'starter'): (bundle, assignment, tmp_path)})
    calls = []
    monkeypatch.setattr(cohort.public, 'freeze_candidate', lambda *args: {'candidate_digest': snapshot['det_binding']['starter']})
    monkeypatch.setattr(cohort.public, 'evaluate_assignment', lambda *args: calls.append(args) or observed())
    return snapshot, bundle, bundle_task, replay, calls


def test_det_adapter_binds_bundle_environment_and_variant_to_a_snapshot(m, monkeypatch, tmp_path):
    snapshot, bundle, _, replay, calls = bound_job(m, monkeypatch, tmp_path)
    replay.bind_snapshot(1, [snapshot], m.snapshot_digest([snapshot]))
    assert replay(1, snapshot['task_id'], 'starter') == observed() and len(calls) == 1
    monkeypatch.setattr(cohort.public, 'freeze_candidate', lambda *args: {'candidate_digest': snapshot['det_binding']['reference']})
    with pytest.raises(ValueError, match='det_candidate_mismatch'):
        replay(1, snapshot['task_id'], 'starter')
    assert len(calls) == 1


@pytest.mark.parametrize('defect', ['bundle', 'image', 'command', 'checks', 'base_commit', 'benchmark', 'upstream', 'revision',
                                   'missing-binding', 'binding-format', 'candidate-format'])
def test_det_adapter_rejects_unbound_environment_or_snapshot_evidence(m, monkeypatch, tmp_path, defect):
    snapshot, bundle, actual, replay, calls = bound_job(m, monkeypatch, tmp_path)
    if defect == 'missing-binding': del snapshot['det_binding']
    if defect == 'binding-format': snapshot['det_binding']['bundle_digest'] = 'not-a-digest'
    if defect == 'candidate-format': snapshot['det_binding']['starter'] = 'not-a-digest'
    replay.bind_snapshot(1, [snapshot], m.snapshot_digest([snapshot]))
    if defect == 'bundle': bundle.digest = 'sha256:' + '9' * 64
    if defect in ('image', 'command'): actual['environment'] = actual['environment'] | {defect: 'different'}
    if defect in ('checks', 'base_commit', 'benchmark', 'upstream'): actual[defect] = [] if defect == 'checks' else ('https://example.invalid/other/repo' if defect == 'upstream' else 'different')
    if defect == 'revision': bundle.manifest_bytes = m.canonical({'benchmarks': [{'name': snapshot['benchmark'], 'revision': 'different'}]})
    with pytest.raises((KeyError, ValueError)):
        replay(1, snapshot['task_id'], 'starter')
    assert calls == []


def test_det_adapter_snapshot_digest_and_both_variant_candidates_are_bound(m, monkeypatch, tmp_path):
    snapshot, _, _, replay, calls = bound_job(m, monkeypatch, tmp_path)
    with pytest.raises(ValueError, match='det_snapshot_mismatch'):
        replay.bind_snapshot(1, [snapshot], 'sha256:' + '0' * 64)
    replay.bind_snapshot(1, [snapshot], m.snapshot_digest([snapshot]))
    replay.jobs[1, snapshot['task_id'], 'reference'] = replay.jobs[1, snapshot['task_id'], 'starter']
    monkeypatch.setattr(cohort.public, 'freeze_candidate', lambda *args: {'candidate_digest': snapshot['det_binding']['reference']})
    assert replay(1, snapshot['task_id'], 'reference') == observed()
    monkeypatch.setattr(cohort.public, 'freeze_candidate', lambda *args: {'candidate_digest': snapshot['det_binding']['starter']})
    with pytest.raises(ValueError, match='det_candidate_mismatch'):
        replay(1, snapshot['task_id'], 'reference')
    assert len(calls) == 1


@pytest.mark.parametrize('entry', ['collect', 'verify'])
def test_det_entry_points_bind_the_adapter_to_the_declared_snapshot(m, monkeypatch, tmp_path, entry):
    f = fixture(m); material = f[1][1]
    for snapshot in material['snapshot']:
        snapshot['det_binding'] = {'bundle_digest': 'sha256:' + '1' * 64,
                                  'starter': 'sha256-tree-exec-v1:' + '2' * 64,
                                  'reference': 'sha256-tree-exec-v1:' + '3' * 64}
    a = json.loads(next(iter(f[0]['prs'][0]['files'].values())))
    a['snapshot_digest'] = m.snapshot_digest(material['snapshot'])
    update_file(m, f[0], 'A', a); regenerate_committed(m, f)
    jobs = {}
    for snapshot in material['snapshot']:
        actual = {key: snapshot[key] for key in ('task_id', 'benchmark', 'base_commit', 'environment', 'checks')}
        actual['upstream'] = snapshot['repository']
        bundle = SimpleNamespace(digest=snapshot['det_binding']['bundle_digest'], task=lambda _, row=actual: row,
                                 manifest_bytes=m.canonical({'benchmarks': [{'name': snapshot['benchmark'], 'revision': snapshot['revision']}]}))
        for variant in ('starter', 'reference'):
            jobs[1, snapshot['task_id'], variant] = (bundle, {'task_id': snapshot['task_id'], 'worker_export': 'worker'}, tmp_path / variant)
    replay = cohort.BundleReplay(jobs)
    monkeypatch.setattr(cohort.public, 'freeze_candidate', lambda b, a, c: {'candidate_digest': material['snapshot'][0]['det_binding'][c.name]})
    monkeypatch.setattr(cohort.public, 'evaluate_assignment', lambda b, a, w, c, e: observed(c.name == 'reference'))
    if entry == 'collect':
        assert cohort.collect_det(material['snapshot'], a, material['scope'], 1, replay) == material['observations']
    else:
        assert check(m, f, rerun=replay)['status'] == 'valid'
        jobs[1, 'task-000', 'starter'][0].digest = 'sha256:' + '9' * 64
        assert check(m, f, rerun=replay)['reason'] == 'det_bundle_mismatch'


def test_det_adapter_uses_real_i2c_freeze_and_rejects_changed_candidate_bytes(m, monkeypatch, tmp_path):
    freeze_candidate = cohort.public.freeze_candidate
    snapshot, bundle, actual, replay, calls = bound_job(m, monkeypatch, tmp_path)
    candidate = tmp_path / 'main.py'; candidate.write_text('print("starter")\n')
    actual.update(unit_id='neutral-unit', starter_digest=cohort.public.native._digest_tree(tmp_path, exclude_git=True),
                  criteria={key: {'accepted': True} for key in ('Lic', 'Con', 'Cx', 'Det')})
    assignment = cohort.public._identity(bundle, actual) | {'source_commit': '1' * 40,
                  'worker_export': {'initial_sha256': actual['starter_digest']}}
    replay.jobs[1, snapshot['task_id'], 'starter'] = (bundle, assignment, tmp_path)
    snapshot['det_binding']['starter'] = cohort.public._candidate_digest(tmp_path)
    replay.bind_snapshot(1, [snapshot], m.snapshot_digest([snapshot]))
    # Only evaluation is replaced: use the genuine I2c freeze/binding/digest path.
    monkeypatch.setattr(cohort.public, 'freeze_candidate', freeze_candidate)
    assert replay(1, snapshot['task_id'], 'starter') == observed()
    candidate.write_text('print("other candidate")\n')
    with pytest.raises(ValueError, match='det_candidate_mismatch'):
        replay(1, snapshot['task_id'], 'starter')
    assert len(calls) == 1


def test_det_snapshot_binding_rejects_duplicate_task_ids_before_collection(m, monkeypatch, tmp_path):
    snapshot, _, _, replay, calls = bound_job(m, monkeypatch, tmp_path)
    duplicated = [snapshot, json.loads(m.canonical(snapshot))]
    with pytest.raises(ValueError, match='det_snapshot_duplicate_task'):
        replay.bind_snapshot(1, duplicated, m.snapshot_digest(duplicated))
    assert 1 not in replay.snapshots and calls == []


@pytest.mark.parametrize('defect', ['proof-error', 'replay-error', 'missing-document', 'document-binding',
                                   'empty-records', 'unknown-unit', 'invalid-det-mode'])
def test_unavailable_or_missing_evidence_fails_closed(m, defect):
    f = fixture(m, 18 if defect == 'unknown-unit' else 16)
    if defect == 'replay-error':
        def replay(*args): raise OSError('unavailable evaluator')
        assert check(m, f, rerun=replay)['status'] == 'invalid_cohort'; return
    if defect == 'proof-error': f[0]['prs'][0]['proofs'][next(iter(f[0]['prs'][0]['files']))]['signature'] = 'not-hex'
    if defect in ('missing-document', 'document-binding'):
        path = next(p for p in f[0]['current_files'] if p.endswith('preregistration.md'))
        if defect == 'missing-document':
            for c in f[0]['prs'] + f[0]['commits']:
                c['files'].pop(path, None)
            f[0]['current_files'].pop(path)
        else:
            for c in f[0]['prs'] + f[0]['commits']:
                if path in c['files']: c['files'][path] = b'different registration'
            f[0]['current_files'][path] = b'different registration'
    if defect == 'empty-records': f[3].clear()
    if defect == 'unknown-unit':
        c = json.loads(f[0]['prs'][2]['files'][next(iter(f[0]['prs'][2]['files']))]); f[3][0].update(unit_id=c['ranking'][4], task_id=c['primary'][c['ranking'][4]])
    if defect == 'invalid-det-mode':
        a = json.loads(f[0]['prs'][0]['files'][next(iter(f[0]['prs'][0]['files']))]); a['det_mode'] = 'none'
        update_file(m, f[0], 'A', a)
    result = check(m, f)
    assert result['status'] == 'invalid_cohort'
    if defect == 'unknown-unit': assert result['reason'] == 'unselected_worker_run'


def test_beacon_signature_is_checked_even_when_randomness_matches_bad_signature(m):
    f = fixture(m)
    f[2]['signature'] = '00' * SIZE
    f[2]['randomness'] = hashlib.sha256(bytes(SIZE)).hexdigest()
    result = check(m, f)
    assert result['reason'] == 'beacon_signature_invalid'


@pytest.mark.parametrize('mode', ['all', 'audit'])
def test_replay_eligibility_does_not_trust_missing_b_observations(m, monkeypatch, mode):
    f = fixture(m, 18); a = json.loads(f[0]['prs'][0]['files'][next(iter(f[0]['prs'][0]['files']))]); a['det_mode'] = mode
    update_file(m, f[0], 'A', a); b = json.loads(f[0]['prs'][1]['files'][next(iter(f[0]['prs'][1]['files']))]); c = json.loads(f[0]['prs'][2]['files'][next(iter(f[0]['prs'][2]['files']))]); next(r for r in b['tasks'] if r['unit_id'] == c['ranking'][4])['det'] = None
    update_file(m, f[0], 'B', b)
    monkeypatch.setattr(m, 'generate_pool', lambda *args, **kwargs: m.canonical(b))  # faulty self-consistent regenerator
    assert check(m, f)['status'] == 'invalid_cohort'




@pytest.mark.parametrize('pilot_baseline,confirmation_baseline', [(False, False), (False, True), (True, False), (True, True)])
def test_primary_arms_are_required_and_baseline_is_frozen_at_a(m, pilot_baseline, confirmation_baseline):
    primary = ['native_goal', 'mission_verified_complex']
    pilot = primary + (['mission_baseline'] if pilot_baseline else [])
    confirmation = primary + (['mission_baseline'] if confirmation_baseline else [])
    f = fixture(m, pilot_arms=pilot, confirmatory_arms=confirmation)
    assert check(m, f)['status'] == 'valid'
    c = json.loads(f[0]['prs'][2]['files'][next(iter(f[0]['prs'][2]['files']))])
    assert len(c['assignments']) == 24 * len(pilot)


@pytest.mark.parametrize('stage', ['C', 'D'])
def test_postseed_arm_declarations_cannot_override_a(m, stage):
    f = fixture(m); h = f[0]; b = json.loads(h['prs'][1]['files'][next(iter(h['prs'][1]['files']))]); seed = bytes.fromhex(f[2]['randomness'])
    c = m.select(seed, b, m.ARMS)
    if stage == 'C':
        c = m.select(seed, b, ['native_goal', 'mission_verified_complex']); update_file(m, h, 'C', c)
    d = m.confirm(seed, c, 'c' * 40, 4, ['native_goal', 'mission_verified_complex'])
    update_file(m, h, 'D', d); f[3][:] = [r for r in f[3] if r['arm'] != 'mission_baseline']
    assert check(m, f)['reason'] == ('selection_mismatch' if stage == 'C' else 'confirmation_mismatch')


def test_lineage_evidence_is_bound_to_preseed_b_not_p_or_caller(m, monkeypatch):
    f = fixture(m); h = f[0]; p = next(v for path, v in h['current_files'].items() if path.endswith('preregistration.md'))
    assert b'lineage_digest' not in p
    f[1][1]['lineage_digest'] = 'untrusted metadata'
    assert check(m, f)['checks']['2'] and check(m, f)['status'] == 'valid'
    b = json.loads(h['prs'][1]['files'][next(iter(h['prs'][1]['files']))]); b['lineage_digest'] = 'sha256:' + '0' * 64
    update_file(m, h, 'B', b)
    monkeypatch.setattr(m, 'generate_pool', lambda *args, **kwargs: m.canonical(b))
    assert check(m, f)['reason'] == 'lineage_digest_mismatch'


@pytest.mark.parametrize('defect', ['C-before-beacon', 'minimum-K', 'L3-used', 'L3-C', 'wrong-task', 'unplanned-arm', 'float-start', 'noncanonical'])
def test_cohort_execution_metadata_cannot_override_plan(m, defect):
    f = fixture(m)
    if defect == 'C-before-beacon': f[0]['prs'][2]['merged_at'] = 999
    if defect == 'minimum-K': f[1][1]['minimum_k'] = 5
    if defect == 'L3-C': f[0]['prs'][2]['merged_at'] = 1001.5
    if defect == 'wrong-task': f[3][0]['task_id'] = 'unselected'
    if defect == 'unplanned-arm': f[3][0]['arm'] = 'other'
    if defect == 'float-start': f[3][0]['started_at'] = 1200.5
    if defect == 'noncanonical': second(m, f)
    result = check(m, f, used=True if defect == 'L3-used' else 2 if defect == 'noncanonical' else 1)
    assert result['status'] == 'invalid_cohort'
    if defect == 'unplanned-arm': assert result['reason'] == 'unplanned_arm_run'


@pytest.mark.parametrize('stage,field', [('C', field) for field in ('pilot', 'ranking', 'primary', 'assignments', 'duplicate-id')]
                         + [('D', field) for field in ('control', 'commit_c', 'assignments', 'duplicate-id', 'replicate', 'fixture_group')])
def test_committed_selection_assignments_and_order_must_recalculate(m, stage, field):
    f = fixture(m)
    index = 2 if stage == 'C' else 3
    value = json.loads(next(iter(f[0]['prs'][index]['files'].values())))
    if field in ('pilot', 'ranking', 'control', 'assignments'): value[field].reverse()
    elif field == 'primary': value[field][next(iter(value[field]))] = 'other-task'
    elif field == 'commit_c': value[field] = '0' * 40
    elif field == 'duplicate-id': value['assignments'][1]['assignment_id'] = value['assignments'][0]['assignment_id']
    else: value['assignments'][0][field] = 99 if field == 'replicate' else 'other'
    update_file(m, f[0], stage, value)
    result = check(m, f)
    assert result['reason'] == ('selection_mismatch' if stage == 'C' else 'confirmation_mismatch')


@pytest.mark.parametrize('clock', ['merged_at', 'time'])
@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_d_both_clocks_are_strictly_before_first_confirmation_run(m, clock, offset):
    f = fixture(m); pr = f[0]['prs'][3]; when = 1200 + offset
    if clock == 'merged_at': pr[clock] = when
    else:
        path, raw = next(iter(pr['files'].items()))
        pr['proofs'][path] = {'time': when, 'signature': sign((m.digest(raw) + ':' + str(when)).encode())}
    # Records need not arrive sorted; inspect the earliest selected-unit run.
    f[3].reverse()
    result = check(m, f)
    assert result['status'] == ('valid' if offset == -1 else 'invalid_cohort')
    if offset >= 0: assert result['reason'] == 'confirmation_timestamp_invalid'


@pytest.mark.parametrize('offset', [-1, 0, 1])
def test_c_must_follow_beacon_and_precede_d(m, offset):
    f = fixture(m); f[0]['prs'][2]['merged_at'] = 1000 + offset
    assert check(m, f)['status'] == ('invalid_cohort' if offset == -1 else 'valid')
    f = fixture(m); f[0]['prs'][2]['merged_at'] = 1100 + offset
    assert check(m, f)['status'] == ('valid' if offset == -1 else 'invalid_cohort')


def test_pilot_can_start_between_c_and_d_but_not_at_c(m):
    f = fixture(m)
    a = json.loads(next(iter(f[0]['prs'][0]['files'].values())))
    assignment = json.loads(next(iter(f[0]['prs'][2]['files'].values())))['assignments'][0]
    record = {k: assignment[k] for k in ('task_id', 'unit_id', 'arm')}
    f[3].append(record | {'started_at': 1002, 'package': a['packages'][record['arm']]})
    assert check(m, f)['status'] == 'valid'
    f[3][-1]['started_at'] = 1001
    assert check(m, f)['reason'] == 'worker_before_selection'


@pytest.mark.parametrize('stage', ['C', 'D'])
def test_missing_postseed_stage_does_not_promote_next_attempt(m, stage):
    f = fixture(m); h = f[0]; sha = ('c' if stage == 'C' else 'd') * 40
    pr = next(p for p in h['prs'] if p['sha'] == sha)
    for path in pr['files']: del h['current_files'][path]
    for collection in ('prs', 'commits'): h[collection][:] = [p for p in h[collection] if p['sha'] != sha]
    h['reachable'].remove(sha); h['total_prs'] -= 1
    second(m, f)
    result = check(m, f)
    assert result['status'] == 'invalid_cohort' and result['canonical_attempt'] == 1


def test_audit_excludes_early_rejections_and_breaks_key_ties_by_utf8(m, monkeypatch):
    data = inputs(m, 130)
    for t in data[0][60:120]: data[3][t['task_id']]['starter'] = [observed(True)] * 3
    for t in data[0][120:]: t['benchmark_license'] = 'unknown'
    data[1]['snapshot_digest'] = m.snapshot_digest(data[0])
    manifest = json.loads(pool(m, data))
    monkeypatch.setattr(m, 'key', lambda *args: b'collision')
    selected = ['task-059', 'task-119']
    assert cohort.audit_tasks(b'seed', manifest, selected) == [f'task-{i:03}' for i in range(120)]


@pytest.mark.parametrize('defect', ['case-count', 'case-name', 'case-bool', 'status', 'reason'])
def test_malformed_replay_results_are_not_equivalent_to_b(m, defect):
    f = fixture(m)
    def replay(number, task_id, variant):
        result = json.loads(json.dumps(f[1][number]['observations'][task_id][variant][0]))
        if defect == 'case-count': result['case_count'] = True
        elif defect == 'case-name': result['cases'][0]['name'] = 'unknown'
        elif defect == 'case-bool': result['cases'][0]['passed'] = 0
        elif defect == 'status': result['status'] = 'passed'
        else: result['reason'] = 'evaluator_unavailable'
        return result
    assert check(m, f, rerun=replay)['reason'] == 'det_cases_invalid'


@pytest.mark.parametrize('stage', ['C', 'D'])
def test_deep_postseed_json_fails_closed_without_promoting_another_attempt(m, monkeypatch, stage):
    f = fixture(m); raw = b'[' * 2000 + b'0' + b']' * 2000
    update_file(m, f[0], stage, raw); second(m, f)
    loads = json.loads
    def decode(value, *args, **kwargs):
        # Exercise decoder recursion failure independently of runtime limits.
        if value == raw: raise RecursionError('JSON nesting limit')
        return loads(value, *args, **kwargs)
    monkeypatch.setattr(json, 'loads', decode)
    result = check(m, f)
    assert result['status'] == 'invalid_cohort' and result['canonical_attempt'] == 1


def test_all_replays_both_det_strata_but_skips_lic_con_cx_rejections(m):
    f = fixture(m, 21); material = f[1][1]
    material['snapshot'][0]['benchmark_license'] = 'unknown'
    material['snapshot'][1]['natural_request'] = False
    material['scope']['mission']['README.md'] = 'contaminated task-002'
    material['observations']['task-020']['starter'] = [observed(True)] * 3
    a = json.loads(next(iter(f[0]['prs'][0]['files'].values())))
    a.update(snapshot_digest=m.snapshot_digest(material['snapshot']), scope_digest=m.digest(m.canonical(material['scope'])))
    update_file(m, f[0], 'A', a); regenerate_committed(m, f)
    calls = []
    def replay(number, task_id, variant):
        calls.append((task_id, variant)); return material['observations'][task_id][variant][0]
    result = check(m, f, rerun=replay)
    assert result['status'] == 'valid'
    assert set(calls) == {(f'task-{i:03}', variant) for i in range(3, 21) for variant in ('starter', 'reference')}
    assert len(calls) == 36


@pytest.mark.parametrize('minimum', [1, 4, 5, 0, True, 1.5, None])
def test_power_plan_lower_bound_is_a_positive_integer_and_enforced(m, minimum):
    f = fixture(m); f[1][1]['minimum_k'] = minimum
    assert check(m, f)['status'] == ('valid' if type(minimum) is int and 0 < minimum <= 4 else 'invalid_cohort')


@pytest.mark.parametrize('k', [0, -1, True, 1.5, None, 5])
def test_confirmation_k_must_be_integer_and_within_the_remaining_ranking(m, k):
    f = fixture(m); d = json.loads(next(iter(f[0]['prs'][3]['files'].values()))); d['K'] = k
    if k == 1.5:
        raw = json.dumps(d, separators=(',', ':')).encode()
    else: raw = m.canonical(d)
    update_file(m, f[0], 'D', raw)
    assert check(m, f)['status'] == 'invalid_cohort'
