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
    path = archive(tmp_path / 'bundle.tar', files)
    return path, manifest


@pytest.mark.parametrize('defect', ['unknown-key', 'duplicate-task', 'duplicate-check', 'no-checks', 'nested-key'])
def test_bundle_rejects_unknown_keys_duplicate_ids_and_empty_checks(tmp_path, defect):
    module = load_module()
    def mutate(manifest):
        task = manifest['tasks'][0]
        if defect == 'unknown-key': manifest['unexpected'] = True
        if defect == 'nested-key': task['environment']['unexpected'] = True
        if defect == 'duplicate-task': manifest['tasks'].append(task.copy())
        if defect == 'duplicate-check': task['checks'].append(task['checks'][0].copy())
        if defect == 'no-checks': task['checks'] = []
    path, _ = fixture_bundle(module, tmp_path, mutate)
    with pytest.raises(ValueError, match='bundle_schema_invalid'):
        module.load_bundle(path, module.bundle_digest(path))


def prepared(module, tmp_path, monkeypatch):
    import shutil
    import native_goal_benchmark as native
    seen = []
    def export(source, commit, output, allowed_paths):
        assert commit == 'd' * 40
        assert list(allowed_paths) == ['main.py']
        seen.append(1)
        shutil.copytree(source, output)
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
    assert envelope['candidate_digest'] == envelope['starter_digest']
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
    ('count', 'evaluator_cases_invalid', 'failed'),
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
        cases = [{'name': 'repair', 'passed': True}, {'name': 'preserve', 'passed': True}]
        if defect == 'count': cases.pop()
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
