"""User trust-root pins must not execute provider code in the parent."""
import hashlib
from dataclasses import asdict
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys

import pytest


def _entry(**changes):
    item = {'id': 'neutral', 'entry_point': 'neutral', 'distribution': 'neutral-adapter',
            'version': '1.0', 'source_digest': 'sha256:' + 'a' * 64}
    item.update(changes)
    return item


def test_registry_has_closed_portable_fields_and_unique_ids():
    from fresh_review_runtime import validate_registry, REGISTRY_SCHEMA
    from mission_kernel.fresh_review import FreshReviewError

    value = {'schema': REGISTRY_SCHEMA, 'adapters': [_entry()]}
    registrations = validate_registry(value)
    assert registrations['neutral'].entry_point == 'neutral'
    for malformed in (None, [], {}, {**value, 'schema': 'future'}, {**value, 'project': True},
                      {**value, 'adapters': [_entry(), _entry()]}):
        with pytest.raises(FreshReviewError, match='fresh-review-adapter-registry-invalid'):
            validate_registry(malformed)


def test_user_registry_rejects_links_duplicate_keys_and_oversize(tmp_path, monkeypatch):
    from fresh_review_runtime import read_registry, REGISTRY_SCHEMA, REGISTRY_LIMIT
    from mission_kernel.fresh_review import FreshReviewError

    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    folder = tmp_path / 'mission'
    folder.mkdir()
    path = folder / 'fresh-review-adapters.json'
    valid = json.dumps({'schema': REGISTRY_SCHEMA, 'adapters': [_entry()]})
    path.write_text(valid)
    assert read_registry()['neutral'].distribution == 'neutral-adapter'
    for content in ('{"schema":"a","schema":"b","adapters":[]}', ' ' * (REGISTRY_LIMIT + 1),
                    '{"schema":"mission-fresh-review-adapter-registry/1","adapters":NaN}'):
        path.write_text(content)
        with pytest.raises(FreshReviewError, match='fresh-review-adapter-registry-invalid'):
            read_registry()
    path.write_text(valid)
    hardlink = folder / 'linked.json'
    os.link(path, hardlink)
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-registry-invalid'):
        read_registry()
    hardlink.unlink()
    path.rename(hardlink)
    path.symlink_to(hardlink)
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-registry-invalid'):
        read_registry()


@pytest.fixture
def installed_adapter(tmp_path, monkeypatch):
    """Install neutral metadata and user pins without importing fixture code."""
    module = tmp_path / 'neutral_adapter.py'
    module.write_text('raise RuntimeError("provider code executed")\n')
    metadata = tmp_path / 'neutral_adapter-1.0.dist-info'
    metadata.mkdir()
    (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: neutral-adapter\nVersion: 1.0\n')
    (metadata / 'entry_points.txt').write_text('[mission.fresh_review_adapters]\nneutral = neutral_adapter:factory\n')
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    registry = tmp_path / 'config' / 'mission' / 'fresh-review-adapters.json'
    registry.parent.mkdir(parents=True)

    def register():
        entry = _entry(source_digest='sha256:' + hashlib.sha256(module.read_bytes()).hexdigest())
        registry.write_text(json.dumps({'schema': 'mission-fresh-review-adapter-registry/1', 'adapters': [entry]}))

    register()
    return module, metadata, registry, register


def test_installed_entry_point_is_pinned_without_executing_it(installed_adapter):
    from fresh_review_runtime import resolve_adapter
    from mission_kernel.fresh_review import canonical_digest, FreshReviewError

    module, metadata, registry, register = installed_adapter
    pin = resolve_adapter('neutral')
    assert pin.registration.digest == canonical_digest(json.loads(registry.read_bytes())['adapters'][0])
    assert pin.entry_point_value == 'neutral_adapter:factory'
    module.write_text('raise RuntimeError("changed source")\n')
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-source-invalid'):
        resolve_adapter('neutral')
    register()
    (metadata / 'METADATA').write_text('Name: neutral-adapter\nVersion: 2.0\n')
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-distribution-invalid'):
        resolve_adapter('neutral')


def test_callback_child_rechecks_parent_pin_before_loading(installed_adapter):
    from fresh_review_runtime import resolve_adapter

    module, metadata, registry, register = installed_adapter
    module.write_text('''from mission_kernel.json_codec import freeze_json_value
class Adapter:
    def observe_parent(self): return freeze_json_value({'parent_identity': 'neutral-parent'})
    def launch(self, *args): raise RuntimeError('not a host adapter')
    def collect(self, *args): raise RuntimeError('not a host adapter')
    def cancel(self, *args): return freeze_json_value({'status': 'cancelled'})
    def recover(self, *args): raise RuntimeError('not a host adapter')
def factory(): return Adapter()
''')
    register()
    pin = resolve_adapter('neutral')
    script = '''import json, sys
from fresh_review_runtime import AdapterPin, AdapterRegistration, load_adapter
from mission_kernel.fresh_review import FreshReviewError
raw = json.loads(sys.argv[1])
raw['registration'] = AdapterRegistration(**raw['registration'])
try:
    adapter = load_adapter(AdapterPin(**raw))
    print(json.dumps(adapter.observe_parent().thaw()))
except FreshReviewError as error:
    print(error.code)
    sys.exit(2)
'''
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join((str(module.parent), str(Path(__file__).parents[1] / 'lib')))}

    def child():
        return subprocess.run([sys.executable, '-c', script, json.dumps(asdict(pin))],
                              env=env, capture_output=True, text=True, timeout=10)

    result = child()
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {'parent_identity': 'neutral-parent'}
    # A changed trust-root pin must not retarget the pending callback.
    value = json.loads(registry.read_bytes())
    value['adapters'][0]['version'] = '2.0'
    registry.write_text(json.dumps(value))
    (metadata / 'METADATA').write_text('Name: neutral-adapter\nVersion: 2.0\n')
    result = child()
    assert result.returncode == 2
    assert result.stdout.strip() == 'fresh-review-adapter-pin-changed'
    assert result.stderr == ''


@pytest.mark.parametrize('field', ['id', 'entry_point', 'distribution', 'version', 'source_digest'])
@pytest.mark.parametrize('value', [None, True, 1, [], {}, '', '../escape', 'module:factory',
                                 'https://invalid.example', 'name;execute', 'name\nvalue', '\ud800'])
def test_registry_cannot_smuggle_executable_or_unencodable_fields(field, value):
    from fresh_review_runtime import validate_registry, REGISTRY_SCHEMA
    from mission_kernel.fresh_review import FreshReviewError

    with pytest.raises(FreshReviewError, match='fresh-review-adapter-registry-invalid'):
        validate_registry({'schema': REGISTRY_SCHEMA, 'adapters': [_entry(**{field: value})]})


def test_missing_user_registry_never_falls_back_to_project(tmp_path, monkeypatch):
    from fresh_review_runtime import read_registry, REGISTRY_SCHEMA
    from mission_kernel.fresh_review import FreshReviewError

    project = tmp_path / '.mission'
    project.mkdir()
    (project / 'fresh-review-adapters.json').write_text(json.dumps({'schema': REGISTRY_SCHEMA, 'adapters': [_entry()]}))
    monkeypatch.chdir(tmp_path)
    for config in (str(tmp_path / 'missing-user-config'), '.mission'):
        monkeypatch.setenv('XDG_CONFIG_HOME', config)
        with pytest.raises(FreshReviewError, match='fresh-review-adapter-registry-invalid'):
            read_registry()


def test_registry_replacement_during_read_is_not_accepted(installed_adapter, monkeypatch):
    from fresh_review_runtime import read_registry
    from mission_kernel.fresh_review import FreshReviewError

    _, _, registry, _ = installed_adapter
    original_read = os.read
    replaced = False

    def replace_during_read(fd, size):
        nonlocal replaced
        raw = original_read(fd, size)
        if not replaced:
            replacement = registry.with_suffix('.new')
            replacement.write_bytes(registry.read_bytes())
            replacement.replace(registry)
            replaced = True
        return raw

    monkeypatch.setattr(os, 'read', replace_during_read)
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-registry-invalid'):
        read_registry()


def test_ambiguous_metadata_and_missing_protocol_are_rejected(installed_adapter):
    from fresh_review_runtime import resolve_adapter, load_adapter
    from mission_kernel.fresh_review import FreshReviewError

    module, metadata, _, register = installed_adapter
    duplicate = module.parent / 'other_adapter-1.0.dist-info'
    duplicate.mkdir()
    (duplicate / 'METADATA').write_text('Name: other-adapter\nVersion: 1.0\n')
    duplicate_entry = duplicate / 'entry_points.txt'
    duplicate_entry.write_bytes((metadata / 'entry_points.txt').read_bytes())
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-entry-point-invalid'):
        resolve_adapter('neutral')
    duplicate_entry.unlink()
    module.write_text('def factory(): return object()\n')
    register()
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-protocol-invalid'):
        load_adapter(resolve_adapter('neutral'))


def test_dotted_entry_point_parent_package_is_not_imported(installed_adapter):
    from fresh_review_runtime import resolve_adapter

    module, metadata, _, register = installed_adapter
    package = module.parent / 'neutral_package'
    package.mkdir()
    (package / '__init__.py').write_text('raise RuntimeError("parent package imported")\n')
    (package / 'adapter.py').write_bytes(module.read_bytes())
    (metadata / 'entry_points.txt').write_text('[mission.fresh_review_adapters]\nneutral = neutral_package.adapter:factory\n')
    register()
    assert resolve_adapter('neutral').module == 'neutral_package.adapter'


@pytest.mark.parametrize('value', ['bad/adapter:factory', 'neutral_adapter;execute', 'https://invalid.example'])
def test_malformed_installed_entry_point_is_reason_coded(installed_adapter, value):
    from fresh_review_runtime import resolve_adapter
    from mission_kernel.fresh_review import FreshReviewError

    _, metadata, _, _ = installed_adapter
    (metadata / 'entry_points.txt').write_text('[mission.fresh_review_adapters]\nneutral = ' + value + '\n')
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-entry-point-invalid'):
        resolve_adapter('neutral')


def test_installed_bytecode_module_can_be_pinned_without_executing_it(installed_adapter):
    from fresh_review_runtime import resolve_adapter

    module, _, registry, _ = installed_adapter
    bytecode = module.with_suffix('.pyc')
    py_compile.compile(str(module), cfile=str(bytecode), dfile='neutral-adapter', doraise=True)
    module.unlink()
    value = json.loads(registry.read_bytes())
    value['adapters'][0]['source_digest'] = 'sha256:' + hashlib.sha256(bytecode.read_bytes()).hexdigest()
    registry.write_text(json.dumps(value))
    assert resolve_adapter('neutral').registration.source_digest == value['adapters'][0]['source_digest']
