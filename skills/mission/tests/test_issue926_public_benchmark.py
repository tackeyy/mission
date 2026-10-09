"""External bundle integrity and evaluator isolation, using neutral local data."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[3]
BENCH = ROOT / 'benchmarks/mission-vs-goal'


def load_module():
    sys.path.insert(0, str(BENCH))
    spec = importlib.util.spec_from_file_location('public_benchmark', BENCH / 'public_benchmark.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def archive(path, files):
    with tarfile.open(path, 'w') as tar:
        for name, data in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(data)
            tar.addfile(item, io.BytesIO(data))
    return path


def test_bundle_digest_mismatch_precedes_manifest_parsing(tmp_path):
    module = load_module()
    path = archive(tmp_path / 'bundle.tar', {'manifest.json': b'not json'})
    with pytest.raises(ValueError, match='bundle_digest_mismatch'):
        module.load_bundle(path, 'sha256:' + '0' * 64)


def fixture_bundle(module, tmp_path, mutate=lambda m: None):
    starter = tmp_path / 'starter'
    starter.mkdir(exist_ok=True)
    (starter / 'main.py').write_text('raise RuntimeError("must not import")\n')
    import native_goal_benchmark as native
    manifest = {
        'schema': 'mission-public-bundle/1',
        'benchmarks': [{'name': 'neutral-suite', 'revision': 'v1', 'license': 'MIT'}],
        'tasks': [{
            'task_id': 'task-a', 'unit_id': 'unit-a', 'benchmark': 'neutral-suite',
            'upstream': 'https://example.invalid/project', 'base_commit': 'a' * 40,
            'environment': {'image': 'neutral@sha256:' + 'b' * 64,
                            'command': ['python', '/evaluator/runner.py']},
            'requirement': 'Repair the regression', 'license': 'MIT',
            'checks': [{'name': 'repair', 'kind': 'fail_to_pass'},
                       {'name': 'preserve', 'kind': 'pass_to_pass'}],
            'criteria': {key: {'accepted': True, 'reason': None, 'evidence_digest': 'sha256:' + 'c' * 64}
                         for key in ('Lic', 'Con', 'Cx', 'Det')},
            'starter_digest': native._digest_tree(starter, exclude_git=True),
            'starter_root': 'starters/task-a', 'evaluator_root': 'evaluators/task-a',
        }],
    }
    mutate(manifest)
    files = {'manifest.json': json.dumps(manifest).encode(),
             'starters/task-a/main.py': (starter / 'main.py').read_bytes(),
             'evaluators/task-a/test.patch': b'neutral patch bytes',
             'evaluators/task-a/runner.py': b'raise RuntimeError("must not import")\n'}
    if len(manifest['tasks']) > 1:
        for task in manifest['tasks'][1:]:
            files[task['starter_root'] + '/main.py'] = files['starters/task-a/main.py']
            files[task['evaluator_root'] + '/test.patch'] = b'neutral patch bytes'
    path = archive(tmp_path / 'bundle.tar', files)
    return path, manifest


@pytest.mark.parametrize('defect', ['unknown-key', 'duplicate-task', 'duplicate-root', 'duplicate-benchmark', 'duplicate-check', 'no-checks', 'nested-key'])
def test_bundle_rejects_unknown_keys_duplicate_ids_and_empty_checks(tmp_path, defect):
    module = load_module()
    def mutate(manifest):
        task = manifest['tasks'][0]
        if defect == 'unknown-key': manifest['unexpected'] = True
        if defect == 'nested-key': task['environment']['unexpected'] = True
        if defect == 'duplicate-task':
            other = task | {'starter_root': 'starters/task-b', 'evaluator_root': 'evaluators/task-b'}
            manifest['tasks'].append(other)
        if defect == 'duplicate-root': manifest['tasks'].append(task | {'task_id': 'task-b'})
        if defect == 'duplicate-benchmark': manifest['benchmarks'].append(manifest['benchmarks'][0].copy())
        if defect == 'duplicate-check': task['checks'].append(task['checks'][0].copy())
        if defect == 'no-checks': task['checks'] = []
    path, _ = fixture_bundle(module, tmp_path, mutate)
    with pytest.raises(ValueError, match='bundle_schema_invalid'):
        module.load_bundle(path, module.bundle_digest(path))


def prepared(module, tmp_path, monkeypatch, *, mode_change=False):
    import shutil
    import native_goal_benchmark as native
    seen = []
    def export(source, commit, output, allowed_paths):
        assert commit == 'd' * 40
        assert list(allowed_paths) == ['main.py']
        seen.append(1)
        shutil.copytree(source, output)
        if mode_change: (output / 'main.py').chmod(0o755)
        return output
    monkeypatch.setattr(native, 'initialize_worker_export_repository', lambda root, *, literal_snapshot: 'd' * 40)
    monkeypatch.setattr(native, 'create_worker_export', export)
    path, _ = fixture_bundle(module, tmp_path)
    bundle = module.load_bundle(path, module.bundle_digest(path))
    worker = tmp_path / 'worker'
    assignment = module.prepare_assignment(bundle, 'task-a', worker)
    assert seen and {p.name for p in worker.iterdir()} == {'main.py'}
    return bundle, assignment, worker


def test_assignment_and_envelope_bind_bundle_starter_and_initial_worker_digest(tmp_path, monkeypatch):
    module = load_module()
    bundle, assignment, worker = prepared(module, tmp_path, monkeypatch)
    envelope = module.freeze_candidate(bundle, assignment, worker)
    assert envelope['bundle_digest'] == bundle.digest
    assert envelope['starter_digest'] == assignment['worker_export']['initial_sha256']
    assert envelope['candidate_digest'] == module._candidate_digest(worker)
    assert envelope['candidate_digest'].startswith('sha256-tree-exec-v1:')
    assert module.validate_binding(bundle, assignment, envelope, assignment['worker_export'])['task_id'] == 'task-a'
    envelope['bundle_digest'] = 'sha256:' + '0' * 64
    with pytest.raises(ValueError, match='candidate_envelope_mismatch'):
        module.validate_binding(bundle, assignment, envelope, assignment['worker_export'])
    envelope['bundle_digest'] = bundle.digest
    with pytest.raises(ValueError, match='assignment_worker_mismatch'):
        module.validate_binding(bundle, assignment, envelope, {'initial_sha256': 'sha256:' + '0' * 64})


def test_evaluator_uses_fresh_read_only_copy_and_evaluator_owned_tests(tmp_path, monkeypatch):
    module = load_module()
    bundle, assignment, worker = prepared(module, tmp_path, monkeypatch)
    envelope = module.freeze_candidate(bundle, assignment, worker)
    commands = []
    def bounded(command, *, timeout_seconds):
        commands.append(command)
        if command[1] == 'create':
            assert '--network=none' in command and '--read-only' in command
            mounts = [command[i + 1] for i, value in enumerate(command) if value == '--mount']
            candidate = next(m for m in mounts if 'dst=/candidate' in m)
            source = Path(candidate.split('src=')[1].split(',')[0])
            assert source != worker and source.joinpath('main.py').read_bytes() == worker.joinpath('main.py').read_bytes()
            assert all(m.endswith(',readonly') for m in mounts)
            evaluator = Path(next(m for m in mounts if 'dst=/evaluator' in m).split('src=')[1].split(',')[0])
            assert evaluator.joinpath('test.patch').read_bytes() == b'neutral patch bytes'
            assert 'git apply /evaluator/test.patch' in ' '.join(command)
            return 0, b'e' * 64, False, False, False
        if command[1] == 'start':
            return 0, b'[{"name":"repair","passed":true},{"name":"preserve","passed":true}]', False, False, False
        if command[1] == 'inspect':
            return 0, b'{"status":"exited","error":"","exit_code":0}', False, False, False
        assert command[1:3] == ['rm', '-f']
        return 0, b'', False, False, False
    monkeypatch.setattr(module.fixtures, '_run_bounded', bounded)
    result = module.evaluate_assignment(bundle, assignment, assignment['worker_export'], worker, envelope)
    assert result['status'] == 'passed' and result['reason'] is None
    assert result['case_count'] == 2 and result['candidate_digest'] == envelope['candidate_digest']
    assert [c[1] for c in commands] == ['create', 'start', 'inspect', 'rm']
    assert result['family'] == 'neutral-suite' and result['version'] == 'v1'
    assert worker.joinpath('main.py').read_bytes() == b'raise RuntimeError("must not import")\n'


@pytest.mark.parametrize('defect,reason,status', [
    ('changed-source', 'candidate_changed', 'failed'),
    ('changed-copy', 'candidate_changed', 'failed'),
    ('invalid-source', 'candidate_invalid', 'failed'),
    ('count', 'evaluator_cases_invalid', 'failed'),
    ('extra-duplicate', 'evaluator_cases_invalid', 'failed'),
    ('names', 'evaluator_cases_invalid', 'failed'),
    ('duplicate', 'evaluator_cases_invalid', 'failed'),
    ('zero', 'evaluator_cases_invalid', 'failed'),
    ('bad-json', 'evaluator_output_invalid', 'failed'),
    ('bad-bool', 'evaluator_cases_invalid', 'failed'),
    ('create', 'evaluator_process_unavailable', 'failed'),
    ('os-error', 'evaluator_process_unavailable', 'failed'),
    ('start', 'evaluator_process_unavailable', 'failed'),
    ('start-created', 'evaluator_process_unavailable', 'failed'),
    ('candidate-exit-125', 'evaluator_execution_failed', 'failed'),
    ('timeout', 'evaluator_timeout', 'blocked'),
    ('overflow', 'evaluator_output_too_large', 'failed'),
    ('incomplete', 'evaluator_reader_incomplete', 'blocked'),
    ('test-failed', 'contract_mismatch', 'failed'),
])
def test_evaluator_preserves_non_pass_and_classifies_environment_failures(tmp_path, monkeypatch, defect, reason, status):
    module = load_module()
    bundle, assignment, worker = prepared(module, tmp_path, monkeypatch)
    envelope = module.freeze_candidate(bundle, assignment, worker)
    starts, creates = [], []
    copies = []
    def bounded(command, *, timeout_seconds):
        if command[1] == 'rm': return 0, b'', False, False, False
        if command[1] == 'create':
            creates.append(1)
            if defect == 'os-error': raise FileNotFoundError('engine unavailable')
            if defect == 'create': return 125, b'', False, False, False
            mount = next(command[i + 1] for i, arg in enumerate(command) if arg == '--mount' and 'dst=/candidate' in command[i + 1])
            copies.append(Path(mount.split('src=')[1].split(',')[0]))
            return 0, b'e' * 64, False, False, False
        if command[1] == 'inspect':
            state = {'status': 'created' if defect in ('start', 'start-created') else 'exited',
                     'error': 'runtime unavailable' if defect in ('start', 'start-created') else '',
                     'exit_code': 125 if defect == 'candidate-exit-125' else 0}
            return 0, json.dumps(state).encode(), False, False, False
        assert command[1] == 'start'
        starts.append(1)
        if defect == 'changed-source': worker.joinpath('main.py').write_text('changed')
        if defect == 'changed-copy': copies[-1].joinpath('main.py').write_text('changed')
        if defect == 'invalid-source':
            worker.joinpath('main.py').unlink(); worker.joinpath('main.py').symlink_to('missing')
        cases = [{'name': 'repair', 'passed': True}, {'name': 'preserve', 'passed': True}]
        if defect == 'count': cases.pop()
        if defect == 'extra-duplicate': cases.append(cases[0].copy())
        if defect == 'names': cases[0]['name'] = 'foreign'
        if defect == 'duplicate': cases[1]['name'] = 'repair'
        if defect == 'zero': cases = []
        if defect == 'bad-bool': cases[0]['passed'] = 'true'
        if defect == 'test-failed': cases[0]['passed'] = False
        output = b'{' if defect == 'bad-json' else json.dumps(cases).encode()
        return (125 if defect in ('start', 'candidate-exit-125') else 1 if defect == 'start-created' else 0, output, defect == 'timeout', defect == 'overflow', defect == 'incomplete')
    monkeypatch.setattr(module.fixtures, '_run_bounded', bounded)
    result = module.evaluate_assignment(bundle, assignment, assignment['worker_export'], worker, envelope)
    assert (result['status'], result['reason']) == (status, reason)
    if defect in ('changed-source', 'changed-copy', 'invalid-source'):
        assert result['evaluations'][0]['status'] == 'passed' and result['evaluations'][0]['reason'] is None
        assert len(result['evaluations'][0]['cases']) == len(result['cases']) == 2
    if defect == 'count':
        assert result['case_count'] == 1 and result['cases'][0]['name'] == 'repair'
    if defect in ('create', 'os-error', 'start', 'start-created'):
        assert len(creates) == 2
        assert len(result['evaluations']) == 2
    else:
        assert len(creates) == 1


def test_snapshot_export_retains_ignored_source_and_executable_modes(tmp_path, monkeypatch):
    module = load_module()
    import native_goal_benchmark as native
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return type('Result', (), {'returncode': 0, 'stdout': 'd' * 40})()
    monkeypatch.setattr(native.subprocess, 'run', run)
    assert native.initialize_worker_export_repository(tmp_path, literal_snapshot=True) == 'd' * 40
    assert ['git', 'add', '--force', '--all'] in calls
    attributes = (tmp_path / '.git/info/attributes').read_text()
    assert attributes == '* -text -filter -ident -working-tree-encoding -export-ignore -export-subst\n'
    candidate = tmp_path / 'candidate'
    candidate.mkdir()
    executable = candidate / 'check.sh'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o755)
    module._copy_candidate(candidate, tmp_path / 'fresh')
    assert (tmp_path / 'fresh/check.sh').stat().st_mode & 0o111 == 0o111


def test_benchmark_code_runs_in_child_and_never_in_harness(tmp_path, monkeypatch):
    import os
    module = load_module()
    bundle, assignment, worker = prepared(module, tmp_path, monkeypatch)
    envelope = module.freeze_candidate(bundle, assignment, worker)
    marker = tmp_path / 'process-id'
    files = {name: value[0] for name, value in bundle.files.items()}
    files['evaluators/task-a/runner.py'] = (
        'import os\nfrom pathlib import Path\n'
        f'Path({str(marker)!r}).write_text(str(os.getpid()))\n'
        'print(\'[{"name":"repair","passed":true},{"name":"preserve","passed":true}]\')\n'
    ).encode()
    path = archive(tmp_path / 'isolated.tar', files)
    bundle = module.load_bundle(path, module.bundle_digest(path))
    assignment['bundle_digest'] = envelope['bundle_digest'] = bundle.digest
    original = module.fixtures._run_bounded
    evaluator_paths = []
    def local_engine(command, *, timeout_seconds):
        if command[1] == 'create':
            mount = next(command[i+1] for i, arg in enumerate(command) if arg == '--mount' and 'dst=/evaluator' in command[i+1])
            evaluator_paths.append(Path(mount.split('src=')[1].split(',')[0]))
            assert not marker.exists()
            return 0, b'e' * 64, False, False, False
        if command[1] == 'inspect':
            return 0, b'{"status":"exited","error":"","exit_code":0}', False, False, False
        if command[1] == 'start':
            return original([sys.executable, '-I', str(evaluator_paths[-1] / 'runner.py')], timeout_seconds=timeout_seconds)
        return 0, b'', False, False, False
    monkeypatch.setattr(module.fixtures, '_run_bounded', local_engine)
    result = module.evaluate_assignment(bundle, assignment, assignment['worker_export'], worker, envelope)
    assert result['status'] == 'passed'
    assert int(marker.read_text()) != os.getpid()


@pytest.mark.parametrize('defect', ['starter', 'assignment', 'envelope', 'initial', 'candidate'])
def test_provenance_rejection_never_starts_evaluation(tmp_path, monkeypatch, defect):
    module = load_module()
    bundle, assignment, worker = prepared(module, tmp_path, monkeypatch)
    envelope = module.freeze_candidate(bundle, assignment, worker)
    def forbidden(*args, **kwargs): raise AssertionError('unbound evaluation started')
    monkeypatch.setattr(module.fixtures, '_run_bounded', forbidden)
    if defect == 'starter': assignment['starter_digest'] = 'sha256:' + '0' * 64
    if defect == 'assignment': assignment['task_id'] = 'foreign'
    if defect == 'envelope': envelope['unexpected'] = True
    initial = assignment['worker_export'].copy()
    if defect == 'initial': initial['initial_sha256'] = 'sha256:' + '0' * 64
    if defect == 'candidate': worker.joinpath('main.py').write_text('changed')
    result = module.evaluate_assignment(bundle, assignment, initial, worker, envelope)
    assert result['status'] == 'failed' and result['reason'] is not None


@pytest.mark.parametrize('kind', ['link', 'hardlink', 'traversal', 'duplicate-json', 'duplicate-member'])
def test_bundle_rejects_unsafe_archive_and_ambiguous_json(tmp_path, kind):
    module = load_module()
    path, _ = fixture_bundle(module, tmp_path)
    files = {name: item[0] for name, item in module._archive_files(path).items()}
    if kind == 'duplicate-json':
        files['manifest.json'] = files['manifest.json'].replace(b'"schema":', b'"schema":"ignored","schema":', 1)
        archive(path, files)
        with pytest.raises(ValueError, match='bundle_schema_invalid'):
            module.load_bundle(path, module.bundle_digest(path))
        return
    with tarfile.open(path, 'a') as tar:
        item = tarfile.TarInfo('../escape' if kind == 'traversal' else 'manifest.json' if kind == 'duplicate-member' else 'external')
        if kind in ('link', 'hardlink'):
            item.type = tarfile.SYMTYPE if kind == 'link' else tarfile.LNKTYPE
            item.linkname = '/external'
        tar.addfile(item, io.BytesIO(b''))
    with pytest.raises(ValueError, match='bundle_(path|member)_invalid'):
        module.load_bundle(path, 'sha256:' + '0' * 64)


def test_bundle_digest_normalizes_tar_order_owners_and_times(tmp_path):
    module = load_module()
    a = archive(tmp_path / 'a.tar', {'a': b'1', 'b': b'2'})
    b = tmp_path / 'b.tar'
    with tarfile.open(b, 'w') as tar:
        for name, data in [('b', b'2'), ('a', b'1')]:
            item = tarfile.TarInfo(name)
            item.size, item.mtime, item.uid, item.gid = 1, 100, 200, 300
            item.uname = item.gname = 'neutral'
            tar.addfile(item, io.BytesIO(data))
    assert module.bundle_digest(a) == module.bundle_digest(b)


@pytest.mark.parametrize('timeout', [True, -1, float('nan'), float('inf'), 10**1000], ids=['boolean', 'negative', 'nan', 'infinite', 'oversized-integer'])
def test_invalid_timeout_is_rejected_without_environment_start(tmp_path, monkeypatch, timeout):
    module = load_module()
    bundle, assignment, worker = prepared(module, tmp_path, monkeypatch)
    envelope = module.freeze_candidate(bundle, assignment, worker)
    def forbidden(*args, **kwargs): raise AssertionError('invalid timeout launched evaluation')
    monkeypatch.setattr(module.fixtures, '_run_bounded', forbidden)
    result = module.evaluate_assignment(bundle, assignment, assignment['worker_export'], worker, envelope, timeout)
    assert result['status'] == 'failed' and result['reason'] == 'evaluation_entry_invalid'


@pytest.mark.parametrize('phase', ['freeze', 'copy', 'after'])
def test_executable_bit_changes_are_bound_to_candidate(tmp_path, monkeypatch, phase):
    module = load_module()
    bundle, assignment, worker = prepared(module, tmp_path, monkeypatch)
    path = worker / 'main.py'
    monkeypatch.setattr(module.fixtures, '_run_bounded', lambda *a, **k: pytest.fail('must not launch'))
    old_h_digest = module.native._digest_tree(worker, exclude_git=True)
    envelope = module.freeze_candidate(bundle, assignment, worker)
    if phase == 'freeze':
        path.chmod(0o755)
        result = module.evaluate_assignment(bundle, assignment, assignment['worker_export'], worker, envelope)
        assert result['reason'] == 'candidate_digest_mismatch'
    else:
        if phase == 'copy':
            original = module.shutil.copy2
            def copy(source, target):
                result = original(source, target)
                target.chmod(0o755)
                return result
            monkeypatch.setattr(module.shutil, 'copy2', copy)
        else:
            def evaluate(task, fresh, evaluator, timeout):
                (fresh / 'main.py').chmod(0o755)
                return 'passed', None, [], False
            monkeypatch.setattr(module, '_container_evaluate', evaluate)
        result = module.evaluate_assignment(bundle, assignment, assignment['worker_export'], worker, envelope)
        assert result['reason'] in ('candidate_changed', 'candidate_invalid')
    assert module.native._digest_tree(worker, exclude_git=True) == old_h_digest


@pytest.mark.parametrize('names', [('Run.py', 'run.py'), ('é.py', 'e\u0301.py'), ('Dir/a', 'dir/b')])
def test_bundle_rejects_portable_path_collisions(tmp_path, names):
    module = load_module()
    path = archive(tmp_path / 'collision.tar', {name: b'neutral' for name in names})
    with pytest.raises(ValueError, match='bundle_member_invalid'):
        module.bundle_digest(path)


@pytest.mark.parametrize('prefix', ['starters/task-a', 'evaluators/task-a'])
@pytest.mark.parametrize('defect', ['bytes', 'mode'])
def test_materialized_tree_is_verified_against_bundle(tmp_path, monkeypatch, prefix, defect):
    module = load_module()
    path, _ = fixture_bundle(module, tmp_path)
    bundle = module.load_bundle(path, module.bundle_digest(path))
    original = Path.write_bytes
    def corrupt(path, data):
        return original(path, data + b'changed')
    if defect == 'bytes': monkeypatch.setattr(Path, 'write_bytes', corrupt)
    else:
        chmod = Path.chmod
        monkeypatch.setattr(Path, 'chmod', lambda path, mode: chmod(path, 0o755))
    with pytest.raises(ValueError, match='bundle_materialization_mismatch'):
        module._write_tree(bundle, prefix, tmp_path / 'materialized')


@pytest.mark.parametrize('defect', ['patch', 'starter', 'rejected-prepare', 'rejected-binding'])
def test_bundle_patch_starter_and_acceptance_boundaries(tmp_path, monkeypatch, defect):
    module = load_module()
    def mutate(manifest):
        task = manifest['tasks'][0]
        if defect == 'starter': task['starter_digest'] = 'sha256:' + '0' * 64
        if defect.startswith('rejected'):
            task['criteria']['Lic'].update(accepted=False, reason='license unavailable')
    path, _ = fixture_bundle(module, tmp_path, mutate)
    if defect == 'patch':
        files = module._archive_files(path)
        del files['evaluators/task-a/test.patch']
        archive(path, {key: value[0] for key, value in files.items()})
        with pytest.raises(ValueError, match='bundle_schema_invalid'):
            module.load_bundle(path, module.bundle_digest(path))
        return
    bundle = module.load_bundle(path, module.bundle_digest(path))
    if defect == 'rejected-binding':
        identity = module._identity(bundle, bundle.task('task-a'))
        assignment = identity | {'source_commit': 'd' * 40, 'worker_export': {'initial_sha256': identity['starter_digest']}}
        envelope = identity | {'candidate_digest': module._candidate_digest(tmp_path / 'starter')}
        with pytest.raises(ValueError, match='bundle_task_rejected'):
            module.validate_binding(bundle, assignment, envelope, assignment['worker_export'])
    else:
        monkeypatch.setattr(module.native, 'initialize_worker_export_repository', lambda *a, **k: pytest.fail('must reject before git'))
        with pytest.raises(ValueError, match='bundle_starter_mismatch' if defect == 'starter' else 'bundle_task_rejected'):
            module.prepare_assignment(bundle, 'task-a', tmp_path / 'worker')


@pytest.mark.parametrize('defect', ['corrupt', 'long'])
def test_archive_errors_have_bundle_reason_codes(tmp_path, defect):
    module = load_module()
    path = tmp_path / 'invalid.tar'
    if defect == 'corrupt': path.write_bytes(b'not a tar')
    else: archive(path, {'x' * 256: b'neutral'})
    for entry in (lambda: module.bundle_digest(path), lambda: module.load_bundle(path, 'sha256:' + '0' * 64)):
        with pytest.raises(ValueError, match='bundle_archive_invalid'):
            entry()


@pytest.mark.parametrize('stage', ['create', 'inspect'])
@pytest.mark.parametrize('flag', [2, 3, 4])
def test_environment_control_failures_retry_once(tmp_path, monkeypatch, stage, flag):
    module = load_module()
    bundle, assignment, worker = prepared(module, tmp_path, monkeypatch)
    envelope = module.freeze_candidate(bundle, assignment, worker)
    creates = []
    def bounded(command, **kwargs):
        if command[1] == 'create': creates.append(1)
        result = [0, b'[{"name":"repair","passed":true},{"name":"preserve","passed":true}]', False, False, False]
        if command[1] == stage: result[flag] = True
        return tuple(result)
    monkeypatch.setattr(module.fixtures, '_run_bounded', bounded)
    result = module.evaluate_assignment(bundle, assignment, assignment['worker_export'], worker, envelope)
    assert result['reason'] == 'evaluator_process_unavailable' and len(creates) == 2


@pytest.mark.parametrize('character', [',', '"', '\n'])
def test_mount_paths_reject_option_injection(tmp_path, monkeypatch, character):
    module = load_module()
    monkeypatch.setattr(module.fixtures, '_run_bounded', lambda *a, **k: pytest.fail('must reject before spawn'))
    with pytest.raises(ValueError, match='evaluator_mount_invalid'):
        module._container_evaluate({'environment': {'image': 'neutral', 'command': ['true']}},
                                   tmp_path / ('candidate' + character), tmp_path / 'evaluator', 1)


def test_literal_snapshot_uses_real_local_git_without_user_config_or_hooks(tmp_path, monkeypatch):
    module = load_module()
    root = tmp_path / 'source'
    root.mkdir()
    (root / '.gitignore').write_text('ignored.txt\n')
    (root / '.gitattributes').write_text('* text eol=lf export-ignore filter=neutral\n')
    (root / 'ignored.txt').write_bytes(b'neutral\r\n')
    executable = root / 'verify.sh'
    executable.write_text('#!/bin/sh\nexit 0\n')
    executable.chmod(0o755)
    hooks = tmp_path / 'hooks'
    hooks.mkdir()
    hook = hooks / 'pre-commit'
    hook.write_text('#!/bin/sh\nexit 99\n')
    hook.chmod(0o755)
    config = tmp_path / 'global-config'
    config.write_text(f'[core]\n hooksPath = {hooks}\n[filter "neutral"]\n clean = false\n required = true\n')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(config))
    original_run = module.native.subprocess.run
    def isolated_git(command, **kwargs):
        env = kwargs['env']
        assert env['GIT_CONFIG_GLOBAL'] == '/dev/null' and env['GIT_CONFIG_NOSYSTEM'] == '1'
        assert env['GIT_CONFIG_COUNT'] == '1' and env['GIT_CONFIG_KEY_0'] == 'core.hooksPath'
        assert env['GIT_CONFIG_VALUE_0'] == '/dev/null'
        return original_run(command, **kwargs)
    monkeypatch.setattr(module.native.subprocess, 'run', isolated_git)
    before = module._candidate_digest(root)
    commit = module.native.initialize_worker_export_repository(root, literal_snapshot=True)
    exported = module.native.create_worker_export(root, commit, tmp_path / 'export', [p.name for p in root.iterdir() if p.name != '.git'])
    assert module._candidate_digest(exported) == before



def test_mode_change_during_snapshot_is_rejected(tmp_path, monkeypatch):
    module = load_module()
    path = tmp_path / 'verify.sh'
    path.write_text('neutral')
    original = module.native.os.open
    def open_and_change(target, *args, **kwargs):
        descriptor = original(target, *args, **kwargs)
        if Path(target) == path: path.chmod(0o755)
        return descriptor
    monkeypatch.setattr(module.native.os, 'open', open_and_change)
    with pytest.raises(ValueError, match='changed during snapshot'):
        module._candidate_digest(tmp_path)


@pytest.mark.parametrize('component', ['.GIT', '.Git'])
def test_bundle_rejects_git_directory_case_aliases(tmp_path, component):
    module = load_module()
    path = archive(tmp_path / 'alias.tar', {component + '/config': b'neutral'})
    with pytest.raises(ValueError, match='bundle_path_invalid'):
        module.bundle_digest(path)


@pytest.mark.parametrize('outcome', ['contract', 'passed', 'invalid', 'infra'])
@pytest.mark.parametrize('cleanup', ['timeout', 'overflow', 'incomplete', 'exit', 'os-error'])
def test_cleanup_failure_preserves_observation_and_only_retries_infrastructure(tmp_path, monkeypatch, outcome, cleanup):
    module = load_module()
    bundle, assignment, worker = prepared(module, tmp_path, monkeypatch)
    envelope = module.freeze_candidate(bundle, assignment, worker)
    creates = []
    cases = [{'name': 'repair', 'passed': outcome != 'contract'}, {'name': 'preserve', 'passed': True}]
    def bounded(command, **kwargs):
        operation = command[1]
        if operation == 'create': creates.append(1)
        if operation == 'inspect':
            if outcome == 'infra' and len(creates) == 1: return 125, b'', False, False, False
            return 0, b'{"status":"exited","error":"","exit_code":0}', False, False, False
        if operation == 'start':
            output = b'{' if outcome == 'invalid' else json.dumps(cases if len(creates) == 1 else [c | {'passed': True} for c in cases]).encode()
            return 0, output, False, False, False
        if operation == 'rm' and len(creates) == 1:
            if cleanup == 'os-error': raise OSError('neutral cleanup failure')
            return (1 if cleanup == 'exit' else 0, b'', cleanup == 'timeout', cleanup == 'overflow', cleanup == 'incomplete')
        return 0, b'', False, False, False
    monkeypatch.setattr(module.fixtures, '_run_bounded', bounded)
    result = module.evaluate_assignment(bundle, assignment, assignment['worker_export'], worker, envelope)
    original = result['evaluations'][0]
    assert original['cleanup_failed'] is True
    if outcome == 'infra':
        assert len(creates) == 2 and len(result['evaluations']) == 2 and result['status'] == 'passed'
        assert original['reason'] == 'evaluator_process_unavailable' and original['cases'] == []
    else:
        status, reason = ('passed', None) if outcome == 'passed' else ('failed', 'evaluator_output_invalid' if outcome == 'invalid' else 'contract_mismatch')
        assert len(creates) == 1 and result['cleanup_failed'] is True
        assert (result['status'], result['reason'], result['cases']) == (status, reason, [] if outcome == 'invalid' else cases)
        assert (original['status'], original['reason'], original['cases']) == (result['status'], result['reason'], result['cases'])


def test_export_executable_mode_mismatch_has_fixed_reason(tmp_path, monkeypatch):
    module = load_module()
    with pytest.raises(ValueError, match='^assignment_worker_mismatch$'):
        prepared(module, tmp_path, monkeypatch, mode_change=True)


@pytest.mark.parametrize('digest', ['sha256:' + 'a' * 64, 'a' * 64, 'sha256-tree-exec-v1:' + 'A' * 64,
                                  'sha256-tree-exec-v1:' + 'a' * 63, 'sha256-tree-exec-v1:' + 'a' * 65])
def test_candidate_digest_format_has_fixed_reason(tmp_path, monkeypatch, digest):
    module = load_module()
    bundle, assignment, worker = prepared(module, tmp_path, monkeypatch)
    envelope = module.freeze_candidate(bundle, assignment, worker) | {'candidate_digest': digest}
    with pytest.raises(ValueError, match='^candidate_envelope_mismatch$'):
        module.validate_binding(bundle, assignment, envelope, assignment['worker_export'])
    monkeypatch.setattr(module.fixtures, '_run_bounded', lambda *a, **k: pytest.fail('must reject before spawn'))
    assert module.evaluate_assignment(bundle, assignment, assignment['worker_export'], worker, envelope)['reason'] == 'candidate_envelope_mismatch'


@pytest.mark.parametrize('passed', [False, True])
def test_temporary_cleanup_failure_preserves_verified_result(tmp_path, monkeypatch, passed):
    module = load_module()
    bundle, assignment, worker = prepared(module, tmp_path, monkeypatch)
    envelope = module.freeze_candidate(bundle, assignment, worker)
    original = module.tempfile.TemporaryDirectory
    class Directory:
        def __init__(self, **kwargs): self.inner = original(**kwargs)
        def __enter__(self): return self.inner.__enter__()
        def __exit__(self, *args):
            self.inner.__exit__(*args)
            raise OSError('neutral temporary cleanup failure')
    monkeypatch.setattr(module.tempfile, 'TemporaryDirectory', Directory)
    cases = [{'name': 'repair', 'passed': passed}, {'name': 'preserve', 'passed': True}]
    status, reason = ('passed', None) if passed else ('failed', 'contract_mismatch')
    monkeypatch.setattr(module, '_container_evaluate', lambda *_: (status, reason, cases, False))
    result = module.evaluate_assignment(bundle, assignment, assignment['worker_export'], worker, envelope)
    assert (result['status'], result['reason'], result['cases']) == (status, reason, cases)
    assert result['cleanup_failed'] is True and result['evaluations'][0]['reason'] == reason
