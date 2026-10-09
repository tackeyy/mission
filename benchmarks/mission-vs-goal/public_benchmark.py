"""Data-only public bundles; evaluation is outside the harness process.

This adapter is independent of H's fixed catalog and Mission session commands.
Digests use normalized USTAR regular files (sorted paths, uid/gid/mtime zero,
empty owner names, mode 0644 or 0755). Directory entries do not carry content.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
import uuid
import unicodedata
from types import MappingProxyType
from typing import Mapping

# Trusted repository adapters only; no import path is derived from the bundle.
import native_goal_benchmark as native
import complex_fixture_benchmark as fixtures

MAX_BUNDLE_BYTES = 512 * 1024 * 1024
DIGEST = re.compile(r'sha256:[0-9a-f]{64}')
TREE_DIGEST = re.compile(r'sha256-tree-exec-v1:[0-9a-f]{64}')


def _sha(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def _path(value):
    if (type(value) is not str or not value or '\\' in value or ':' in value
            or any(ord(c) < 32 for c in value)
            or PurePosixPath(value).is_absolute()
            or any(unicodedata.normalize('NFD', p).casefold() in ('', '.', '..', '.git') for p in value.split('/'))):
        raise ValueError('bundle_path_invalid')
    return value


def _archive_files(path):
    files, names, portable, total = {}, set(), {}, 0
    with tarfile.open(path, 'r:') as tar:
        for entry in tar:
            name = _path(entry.name.rstrip('/') if entry.isdir() else entry.name)
            if name in names or not (entry.isdir() or entry.isfile()):
                raise ValueError('bundle_member_invalid')
            for i in range(1, len(name.split('/')) + 1):
                prefix = '/'.join(name.split('/')[:i])
                folded = unicodedata.normalize('NFD', prefix).casefold()
                if folded in portable and portable[folded] != prefix:
                    raise ValueError('bundle_member_invalid')
                portable[folded] = prefix
            names.add(name)
            if entry.isfile():
                total += entry.size
                if total > MAX_BUNDLE_BYTES or entry.size < 0:
                    raise ValueError('bundle_too_large')
                files[name] = (tar.extractfile(entry).read(), 0o755 if entry.mode & 0o111 else 0o644)
    for name in files:
        if any(str(parent) in files for parent in PurePosixPath(name).parents):
            raise ValueError('bundle_member_invalid')
    return files


def _normalized_digest(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for name, (data, mode) in sorted(files.items()):
            item = tarfile.TarInfo(name)
            item.mode, item.size = mode, len(data)
            tar.addfile(item, io.BytesIO(data))
    return _sha(buffer.getvalue())


def _read_bundle(path):
    try:
        files = _archive_files(path)
        return files, _normalized_digest(files)
    except (tarfile.TarError, UnicodeError) as exc:
        raise ValueError('bundle_archive_invalid') from exc
    except ValueError as exc:
        if str(exc).startswith('bundle_'): raise
        raise ValueError('bundle_archive_invalid') from exc


def bundle_digest(path):
    """Compute the preregistered digest without parsing or executing content."""
    return _read_bundle(path)[1]


def load_bundle(path, expected_digest):
    files, digest = _read_bundle(path)
    if not isinstance(expected_digest, str) or not DIGEST.fullmatch(expected_digest) or digest != expected_digest:
        raise ValueError('bundle_digest_mismatch')
    try:
        manifest_bytes = files['manifest.json'][0]
        manifest = json.loads(manifest_bytes, object_pairs_hook=_unique_object)
        _validate_manifest(manifest)
        roots = []
        for task in manifest['tasks']:
            for key in ('starter_root', 'evaluator_root'):
                prefix = task[key] + '/'
                if not any(name.startswith(prefix) for name in files):
                    raise ValueError('bundle_schema_invalid')
                roots.append(task[key])
            if task['evaluator_root'] + '/test.patch' not in files:
                raise ValueError('bundle_schema_invalid')
        if any(a == b or a.startswith(b + '/') or b.startswith(a + '/')
               for i, a in enumerate(roots) for b in roots[i + 1:]):
            raise ValueError('bundle_schema_invalid')
        if any(name != 'manifest.json' and not any(name.startswith(root + '/') for root in roots)
               for name in files):
            raise ValueError('bundle_schema_invalid')
    except (KeyError, TypeError, RecursionError, UnicodeError, ValueError) as exc:
        raise ValueError('bundle_schema_invalid') from exc
    return Bundle(digest, manifest_bytes, MappingProxyType(files))


@dataclass(frozen=True)
class Bundle:
    digest: str
    manifest_bytes: bytes
    files: Mapping[str, tuple[bytes, int]]

    def task(self, task_id):
        for task in json.loads(self.manifest_bytes)['tasks']:
            if task['task_id'] == task_id:
                return task
        raise ValueError('bundle_task_unknown')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys.split()):
        raise ValueError('bundle_schema_invalid')


def _text(value):
    if type(value) is not str or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError('bundle_schema_invalid')


def _list(value):
    if type(value) is not list or not value:
        raise ValueError('bundle_schema_invalid')


def _validate_manifest(manifest):
    _object(manifest, 'schema benchmarks tasks')
    if manifest['schema'] != 'mission-public-bundle/1':
        raise ValueError('bundle_schema_invalid')
    _list(manifest['benchmarks']); _list(manifest['tasks'])
    benchmarks, ids = set(), set()
    for benchmark in manifest['benchmarks']:
        _object(benchmark, 'name revision license')
        for value in benchmark.values(): _text(value)
        if benchmark['name'] in benchmarks: raise ValueError('bundle_schema_invalid')
        benchmarks.add(benchmark['name'])
    for task in manifest['tasks']:
        _object(task, 'task_id unit_id benchmark upstream base_commit environment requirement checks license criteria starter_digest starter_root evaluator_root')
        for key in ('task_id', 'unit_id', 'benchmark', 'upstream', 'license'): _text(task[key])
        if type(task['requirement']) is not str or not task['requirement'].strip():
            raise ValueError('bundle_schema_invalid')
        if task['task_id'] in ids or task['benchmark'] not in benchmarks:
            raise ValueError('bundle_schema_invalid')
        ids.add(task['task_id'])
        if not isinstance(task['base_commit'], str) or not re.fullmatch('[0-9a-f]{40}', task['base_commit']):
            raise ValueError('bundle_schema_invalid')
        if not isinstance(task['starter_digest'], str) or not DIGEST.fullmatch(task['starter_digest']):
            raise ValueError('bundle_schema_invalid')
        for key in ('starter_root', 'evaluator_root'): _path(task[key])
        _object(task['environment'], 'image command')
        image, command = task['environment']['image'], task['environment']['command']
        if type(image) is not str or not re.fullmatch(r'[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}', image):
            raise ValueError('bundle_schema_invalid')
        _list(command)
        for arg in command: _text(arg)
        _list(task['checks'])
        checks = set()
        for check in task['checks']:
            _object(check, 'name kind'); _text(check['name'])
            if check['name'] in checks or check['kind'] not in ('fail_to_pass', 'pass_to_pass'):
                raise ValueError('bundle_schema_invalid')
            checks.add(check['name'])
        _object(task['criteria'], 'Lic Con Cx Det')
        for criterion in task['criteria'].values():
            _object(criterion, 'accepted reason evidence_digest')
            if type(criterion['accepted']) is not bool or not isinstance(criterion['evidence_digest'], str) or not DIGEST.fullmatch(criterion['evidence_digest']):
                raise ValueError('bundle_schema_invalid')
            if criterion['reason'] is not None: _text(criterion['reason'])
            if criterion['accepted'] != (criterion['reason'] is None):
                raise ValueError('bundle_schema_invalid')


BINDING_KEYS = ('task_id', 'unit_id', 'benchmark', 'base_commit', 'bundle_digest', 'starter_digest')


def _identity(bundle, task):
    return {key: task[key] for key in BINDING_KEYS if key != 'bundle_digest'} | {'bundle_digest': bundle.digest}


def _write_tree(bundle, prefix, destination):
    destination.mkdir()
    expected = {}
    for name, (data, mode) in bundle.files.items():
        if name.startswith(prefix + '/'):
            target = destination / name[len(prefix) + 1:]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(mode)
            expected[unicodedata.normalize('NFD', name[len(prefix) + 1:])] = (data, mode)
    actual = {}
    for path in destination.rglob('*'):
        if path.is_symlink(): raise ValueError('bundle_materialization_mismatch')
        if path.is_dir(): continue
        if not path.is_file():
            raise ValueError('bundle_materialization_mismatch')
        actual[unicodedata.normalize('NFD', path.relative_to(destination).as_posix())] = (path.read_bytes(), path.stat().st_mode & 0o777)
    if actual != expected:
        raise ValueError('bundle_materialization_mismatch')


def prepare_assignment(bundle, task_id, destination):
    """Materialize only the starter, then use G's positive allowlist export."""
    task = bundle.task(task_id)
    if not all(value['accepted'] for value in task['criteria'].values()):
        raise ValueError('bundle_task_rejected')
    with tempfile.TemporaryDirectory(prefix='mission-public-starter-') as temporary:
        starter = Path(temporary) / 'starter'
        _write_tree(bundle, task['starter_root'], starter)
        if native._digest_tree(starter, exclude_git=True) != task['starter_digest']:
            raise ValueError('bundle_starter_mismatch')
        # Enumerating all top-level paths includes dotfiles and nested trees,
        # with no '.' shortcut. G rejects links and special archive members.
        allowed = sorted(path.name for path in starter.iterdir())
        if not allowed: raise ValueError('bundle_starter_empty')
        mode_digest = _candidate_digest(starter)
        commit = native.initialize_worker_export_repository(starter, literal_snapshot=True)
        native.create_worker_export(starter, commit, destination, allowed)
    initial = native._digest_tree(destination, exclude_git=True)
    if _candidate_digest(destination) != mode_digest or initial != task['starter_digest']: raise ValueError('assignment_worker_mismatch')
    return _identity(bundle, task) | {'source_commit': commit, 'worker_export': {'initial_sha256': initial}}


def validate_binding(bundle, assignment, envelope, worker_export):
    try:
        task = bundle.task(assignment['task_id'])
        identity = _identity(bundle, task)
        if (set(assignment) != set(BINDING_KEYS) | {'source_commit', 'worker_export'}
                or any(assignment[key] != value for key, value in identity.items())
                or not re.fullmatch('[0-9a-f]{40}', assignment['source_commit'])):
            raise ValueError('assignment_manifest_mismatch')
        if (worker_export != {'initial_sha256': task['starter_digest']}
                or assignment['worker_export'] != worker_export):
            raise ValueError('assignment_worker_mismatch')
        if (set(envelope) != set(BINDING_KEYS) | {'candidate_digest'}
                or any(envelope[key] != value for key, value in identity.items())
                or not isinstance(envelope['candidate_digest'], str)
                or not TREE_DIGEST.fullmatch(envelope['candidate_digest'])):
            raise ValueError('candidate_envelope_mismatch')
        if not all(value['accepted'] for value in task['criteria'].values()):
            raise ValueError('bundle_task_rejected')
        return task
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError('assignment_invalid') from exc


def _candidate_digest(path):
    return native._digest_tree(path, exclude_git=True, include_executable=True)


def freeze_candidate(bundle, assignment, candidate):
    identity = _identity(bundle, bundle.task(assignment['task_id']))
    envelope = identity | {'candidate_digest': _candidate_digest(candidate)}
    validate_binding(bundle, assignment, envelope, assignment['worker_export'])
    return envelope



def _copy_candidate(candidate, destination):
    initial = _candidate_digest(candidate)
    destination.mkdir()
    for path in candidate.rglob('*'):
        relative = path.relative_to(candidate)
        if '.git' in relative.parts or path.is_dir(): continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    if _candidate_digest(candidate) != initial or _candidate_digest(destination) != initial:
        raise ValueError('candidate_changed')
    return initial


def _container_evaluate(task, candidate, evaluator, timeout_seconds):
    """No image build or pull: run the locally available immutable environment.

    The command must emit a JSON list of {name, passed} records, using only
    evaluator-owned tests. Patch application happens in the disposable /work;
    candidate and evaluator inputs are read-only, outside that working tree.
    Control failures without an evaluation result permit one infrastructure
    retry. Cleanup failure is recorded separately and never replaces a result.
    """
    name = 'mission-public-' + uuid.uuid4().hex
    def mount(path, target):
        if any(c in str(path) for c in ',\"\\') or any(ord(c) < 32 for c in str(path)):
            raise ValueError('evaluator_mount_invalid')
        return f'type=bind,src={path},dst={target},readonly'
    script = 'cp -a /candidate/. /work/ && cd /work && git apply /evaluator/test.patch && exec "$@"'
    command = ['docker', 'create', '--pull=never', '--name', name, '--network=none',
               '--read-only', '--cap-drop=ALL', '--security-opt=no-new-privileges',
               '--pids-limit=256', '--memory=2g', '--cpus=1',
               '--tmpfs', '/work:rw,exec,nosuid,size=1g',
               '--tmpfs', '/tmp:rw,noexec,nosuid,size=128m',
               '--mount', mount(candidate, '/candidate'),
               '--mount', mount(evaluator, '/evaluator'), '--entrypoint', '/bin/sh',
               task['environment']['image'], '-c', script, 'evaluate',
               *task['environment']['command']]
    result = ('failed', 'evaluator_process_unavailable', [])
    cleanup_failed = False
    try:
        created = fixtures._run_bounded(command, timeout_seconds=timeout_seconds)
        failure = _process_failure(created, launching=True)
        if failure:
            result = (*failure, [])
        else:
            completed = fixtures._run_bounded(['docker', 'start', '-a', name], timeout_seconds=timeout_seconds)
            result = _completion_result(task, completed, name, timeout_seconds)
    except OSError:
        pass
    finally:
        try:
            cleanup = fixtures._run_bounded(['docker', 'rm', '-f', name], timeout_seconds=timeout_seconds)
            cleanup_failed = _process_failure(cleanup, launching=True) is not None
        except OSError:
            cleanup_failed = True
    return (*result, cleanup_failed)


def _process_failure(result, *, launching):
    code, _, timeout, overflow, incomplete = result
    if launching and (code != 0 or timeout or overflow or incomplete):
        return 'failed', 'evaluator_process_unavailable'
    if timeout: return 'blocked', 'evaluator_timeout'
    if overflow: return 'failed', 'evaluator_output_too_large'
    if incomplete: return 'blocked', 'evaluator_reader_incomplete'
    if code != 0:
        return 'failed', ('evaluator_process_unavailable' if launching
                          else 'evaluator_execution_failed')
    return None


def _completion_result(task, completed, name, timeout_seconds):
    # start -a mixes client/engine errors with container exit codes. Inspect
    # terminal state to distinguish startup failure from candidate execution.
    if any(completed[2:]):
        return (*_process_failure(completed, launching=False), [])
    state_format = '{"status":{{json .State.Status}},"error":{{json .State.Error}},"exit_code":{{json .State.ExitCode}}}'
    inspected = fixtures._run_bounded(
        ['docker', 'inspect', '--format', state_format, name], timeout_seconds=timeout_seconds)
    failure = _process_failure(inspected, launching=True)
    if failure: return (*failure, [])
    try:
        state = json.loads(inspected[1], object_pairs_hook=_unique_object)
        if (type(state) is not dict or set(state) != {'status', 'error', 'exit_code'}
                or state['status'] != 'exited' or state['error'] != ''
                or type(state['exit_code']) is not int or not 0 <= state['exit_code'] <= 255
                or completed[0] != state['exit_code']):
            raise ValueError('environment state unavailable')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return 'failed', 'evaluator_process_unavailable', []
    if state['exit_code'] != 0:
        return 'failed', 'evaluator_execution_failed', []
    return _case_result(task, completed[1])


def _case_result(task, output):
    try:
        cases = json.loads(output, object_pairs_hook=_unique_object)
        if not fixtures._json_value(cases): raise ValueError('invalid JSON')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return 'failed', 'evaluator_output_invalid', []
    if (type(cases) is not list or not cases
            or any(type(case) is not dict or set(case) != {'name', 'passed'}
                   or type(case['name']) is not str or type(case['passed']) is not bool for case in cases)):
        return 'failed', 'evaluator_cases_invalid', []
    if (len(cases) != len(task['checks'])
            or {case['name'] for case in cases} != {check['name'] for check in task['checks']}):
        # Preserve well-formed observations even when the inventory mismatches.
        return 'failed', 'evaluator_cases_invalid', cases
    passed = all(case['passed'] for case in cases)
    return ('passed' if passed else 'failed', None if passed else 'contract_mismatch', cases)


def _evaluate_once(bundle, task, candidate, expected_digest, base, timeout_seconds):
    observed, verified = None, False
    try:
        initial = _candidate_digest(candidate)
        base = base | {'candidate_digest': initial}
        if initial != expected_digest: return base | {'reason': 'candidate_digest_mismatch'}
        with tempfile.TemporaryDirectory(prefix='mission-public-evaluator-') as temporary:
            fresh, evaluator = Path(temporary) / 'candidate', Path(temporary) / 'evaluator'
            if _copy_candidate(candidate, fresh) != initial:
                return base | {'reason': 'candidate_changed'}
            _write_tree(bundle, task['evaluator_root'], evaluator)
            status, reason, cases, cleanup_failed = _container_evaluate(task, fresh, evaluator, timeout_seconds)
            observed = base | {'status': status, 'reason': reason, 'cases': cases, 'case_count': len(cases)}
            if cleanup_failed: observed = observed | {'cleanup_failed': True}
            # Check both source and read-only mount even after timeout/failure.
            if (_candidate_digest(candidate) != initial
                    or _candidate_digest(fresh) != initial):
                return observed | {'status': 'failed', 'reason': 'candidate_changed', 'evaluations': [observed]}
            verified = True
        return observed | {'evaluations': [observed]} if cleanup_failed else observed
    except (ValueError, OSError):
        if verified:
            return observed | {'cleanup_failed': True, 'evaluations': [observed]}
        return (observed or base) | {'status': 'failed', 'reason': 'candidate_invalid'} | (
            {'evaluations': [observed]} if observed is not None else {})


def evaluate_assignment(bundle, assignment, worker_export, candidate, candidate_envelope, timeout_seconds=3.0):
    """Return H vocabulary; never use worker success markers as evaluation."""
    base = {'task_id': assignment.get('task_id') if isinstance(assignment, dict) else None,
            'family': None, 'version': None, 'candidate_digest': None,
            'status': 'failed', 'reason': 'assignment_invalid', 'case_count': 0, 'cases': []}
    try:
        valid_timeout = (type(timeout_seconds) in (int, float)
                         and math.isfinite(timeout_seconds) and timeout_seconds > 0)
    except OverflowError:
        valid_timeout = False
    if not valid_timeout:
        return base | {'reason': 'evaluation_entry_invalid'}
    try:
        task = validate_binding(bundle, assignment, candidate_envelope, worker_export)
    except ValueError as exc:
        return base | {'reason': str(exc)}
    base['family'] = task['benchmark']
    benchmarks = json.loads(bundle.manifest_bytes)['benchmarks']
    base['version'] = next(b['revision'] for b in benchmarks if b['name'] == task['benchmark'])
    base['case_count'] = len(task['checks'])
    first = _evaluate_once(bundle, task, candidate, candidate_envelope['candidate_digest'], base, timeout_seconds)
    if first['reason'] != 'evaluator_process_unavailable': return first
    second = _evaluate_once(bundle, task, candidate, candidate_envelope['candidate_digest'], base, timeout_seconds)
    return second | {'evaluations': [first, second]}
