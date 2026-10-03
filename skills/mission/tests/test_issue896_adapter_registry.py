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


ADAPTER_SOURCE = '''from mission_kernel.json_codec import freeze_json_value
class Adapter:
    def observe_parent(self): return freeze_json_value({'parent_identity': 'neutral-parent'})
    def launch(self, *args): raise RuntimeError('not a host adapter')
    def collect(self, *args): raise RuntimeError('not a host adapter')
    def cancel(self, *args): return freeze_json_value({'status': 'cancelled'})
    def recover(self, *args): raise RuntimeError('not a host adapter')
def factory(): return Adapter()
'''


def test_registry_has_closed_portable_document_fields():
    from fresh_review_runtime import validate_registry, REGISTRY_SCHEMA
    from mission_kernel.fresh_review import FreshReviewError

    value = {'schema': REGISTRY_SCHEMA, 'adapters': [_entry()]}
    registrations = validate_registry(value)
    assert registrations['neutral'].entry_point == 'neutral'
    for malformed in (None, [], {}, {**value, 'schema': 'future'}, {**value, 'project': True}):
        with pytest.raises(FreshReviewError, match='fresh-review-adapter-registry-invalid'):
            validate_registry(malformed)


def test_duplicate_id_with_distinct_entry_points_is_rejected():
    from fresh_review_runtime import validate_registry, REGISTRY_SCHEMA
    from mission_kernel.fresh_review import FreshReviewError

    with pytest.raises(FreshReviewError, match='fresh-review-adapter-registry-invalid'):
        validate_registry({'schema': REGISTRY_SCHEMA, 'adapters': [_entry(), _entry(entry_point='other')]})


def test_duplicate_entry_point_with_distinct_ids_is_rejected():
    from fresh_review_runtime import validate_registry, REGISTRY_SCHEMA
    from mission_kernel.fresh_review import FreshReviewError

    with pytest.raises(FreshReviewError, match='fresh-review-adapter-registry-invalid'):
        validate_registry({'schema': REGISTRY_SCHEMA, 'adapters': [_entry(), _entry(id='other')]})


def test_user_registry_rejects_links_invalid_json_and_oversize(tmp_path, monkeypatch):
    from fresh_review_runtime import read_registry, REGISTRY_SCHEMA, REGISTRY_LIMIT
    from mission_kernel.fresh_review import FreshReviewError

    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    folder = tmp_path / 'mission'
    folder.mkdir()
    path = folder / 'fresh-review-adapters.json'
    valid = json.dumps({'schema': REGISTRY_SCHEMA, 'adapters': [_entry()]})
    path.write_text(valid)
    assert read_registry()['neutral'].distribution == 'neutral-adapter'
    for content in (' ' * (REGISTRY_LIMIT + 1),
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


def test_duplicate_json_key_with_valid_final_value_is_rejected(tmp_path, monkeypatch):
    from fresh_review_runtime import read_registry, REGISTRY_SCHEMA
    from mission_kernel.fresh_review import FreshReviewError

    path = tmp_path / 'mission' / 'fresh-review-adapters.json'
    path.parent.mkdir()
    valid = json.dumps({'schema': REGISTRY_SCHEMA, 'adapters': [_entry()]})
    path.write_text('{"schema":"future",' + valid[1:])
    # Ordinary JSON decoding would otherwise produce an entirely valid registry.
    assert json.loads(path.read_text()) == json.loads(valid)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
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
    monkeypatch.delitem(sys.modules, 'neutral_adapter', raising=False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    registry = tmp_path / 'config' / 'mission' / 'fresh-review-adapters.json'
    registry.parent.mkdir(parents=True)

    def register():
        entry = _entry(source_digest='sha256:' + hashlib.sha256(module.read_bytes()).hexdigest())
        registry.write_text(json.dumps({'schema': 'mission-fresh-review-adapter-registry/1', 'adapters': [entry]}))

    register()
    yield module, metadata, registry, register
    sys.modules.pop('neutral_adapter', None)


def test_cached_module_cannot_supply_the_pinned_factory(installed_adapter, monkeypatch):
    from types import ModuleType
    from fresh_review_runtime import resolve_adapter, load_adapter
    from mission_kernel.fresh_review import FreshReviewError

    module, _, _, register = installed_adapter
    marker = module.parent / 'executed'
    module.write_text('def factory(): return object()\n')
    register()
    cached = ModuleType('neutral_adapter')
    cached.__file__ = str(module)
    exec('def factory():\n    marker.write_text("executed")\n    return object()\n',
         {'marker': marker}, cached.__dict__)
    # Even an apparently matching __file__ must not authorize cached code.
    monkeypatch.setitem(sys.modules, 'neutral_adapter', cached)
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-source-invalid'):
        load_adapter(resolve_adapter('neutral'))
    assert not marker.exists()


def test_main_entry_point_never_uses_the_callback_namespace(installed_adapter, monkeypatch):
    from fresh_review_runtime import resolve_adapter, load_adapter
    from mission_kernel.fresh_review import FreshReviewError

    module, metadata, _, register = installed_adapter
    module.write_text(ADAPTER_SOURCE)
    module.with_name('__main__.py').write_bytes(module.read_bytes())
    (metadata / 'entry_points.txt').write_text('[mission.fresh_review_adapters]\nneutral = __main__:factory\n')
    register()
    marker = module.with_name('executed')

    def foreign_factory():
        marker.write_text('executed')
        return object()

    monkeypatch.setattr(sys.modules['__main__'], 'factory', foreign_factory, raising=False)
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-source-invalid'):
        load_adapter(resolve_adapter('neutral'))
    assert not marker.exists()


def test_factory_reexported_from_main_is_not_pinned_code(installed_adapter, monkeypatch):
    from fresh_review_runtime import resolve_adapter, load_adapter
    from mission_kernel.fresh_review import FreshReviewError

    module, _, _, register = installed_adapter
    marker = module.parent / 'executed'
    module.write_text('from __main__ import factory\n')
    register()

    def foreign_factory():
        marker.write_text('executed')
        return object()

    monkeypatch.setattr(sys.modules['__main__'], 'factory', foreign_factory, raising=False)
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-source-invalid'):
        load_adapter(resolve_adapter('neutral'))
    assert not marker.exists()


@pytest.mark.parametrize('route', ['constructor', 'classmethod', 'constructor-object', 'metaclass'])
def test_foreign_class_factory_cannot_forge_its_module_name(installed_adapter, monkeypatch, route):
    from fresh_review_runtime import resolve_adapter, load_adapter
    from mission_kernel.fresh_review import FreshReviewError

    module, _, _, register = installed_adapter
    marker = module.parent / 'executed'

    class ForeignAdapter:
        def __init__(self): marker.write_text('executed')
        def observe_parent(self): pass
        launch = collect = cancel = recover = observe_parent

    if route == 'classmethod':
        ForeignAdapter.__init__ = classmethod(ForeignAdapter.__init__)
    if route == 'constructor-object':
        class ForeignConstructor:
            def __call__(self): marker.write_text('executed')

        ForeignAdapter.__init__ = ForeignConstructor()
    if route == 'metaclass':
        class ForeignMeta(type):
            def __call__(cls):
                marker.write_text('executed')
                return object()

        ForeignAdapter = ForeignMeta('ForeignAdapter', (), {})

    ForeignAdapter.__module__ = 'neutral_adapter'
    monkeypatch.setattr(sys.modules['__main__'], 'factory', ForeignAdapter, raising=False)
    module.write_text('from __main__ import factory\n')
    register()
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-source-invalid'):
        load_adapter(resolve_adapter('neutral'))
    assert not marker.exists()


def test_source_defined_class_factory_remains_usable(installed_adapter):
    from fresh_review_runtime import resolve_adapter, load_adapter

    module, _, _, register = installed_adapter
    module.write_text(ADAPTER_SOURCE.replace('def factory(): return Adapter()', 'factory = Adapter'))
    register()
    assert load_adapter(resolve_adapter('neutral')).observe_parent().thaw() == {'parent_identity': 'neutral-parent'}


def test_source_defined_classmethod_factory_remains_usable(installed_adapter):
    from fresh_review_runtime import resolve_adapter, load_adapter

    module, metadata, _, register = installed_adapter
    module.write_text(ADAPTER_SOURCE + '''
class Factory:
    @classmethod
    def create(cls): return Adapter()
''')
    (metadata / 'entry_points.txt').write_text('[mission.fresh_review_adapters]\nneutral = neutral_adapter:Factory.create\n')
    register()
    assert load_adapter(resolve_adapter('neutral')).observe_parent().thaw() == {'parent_identity': 'neutral-parent'}


def test_source_defined_callable_factory_remains_usable(installed_adapter):
    from fresh_review_runtime import resolve_adapter, load_adapter

    module, _, _, register = installed_adapter
    module.write_text(ADAPTER_SOURCE + '''
class Factory:
    def __call__(self): return Adapter()
factory = Factory()
''')
    register()
    assert load_adapter(resolve_adapter('neutral')).observe_parent().thaw() == {'parent_identity': 'neutral-parent'}


@pytest.mark.parametrize('changed', ['file', 'origin', 'module-cache', 'code', 'globals'])
def test_loaded_module_identity_must_still_match_its_source(installed_adapter, changed):
    from fresh_review_runtime import resolve_adapter, load_adapter
    from mission_kernel.fresh_review import FreshReviewError

    module, _, _, register = installed_adapter
    actions = {
        'file': '__file__ = "foreign.py"',
        'origin': '__spec__.origin = "foreign.py"',
        'module-cache': 'sys.modules[__name__] = ModuleType(__name__)',
        'code': 'factory.__code__ = factory.__code__.replace(co_filename="foreign.py")',
        'globals': 'factory = FunctionType(factory.__code__, dict(globals()))',
    }
    source = ADAPTER_SOURCE.replace('def factory(): return Adapter()',
                                    'def factory():\n    marker.write_text("executed")\n    return Adapter()')
    module.write_text(source + '''
import sys
from pathlib import Path
from types import FunctionType, ModuleType
marker = Path(__file__).with_name('executed')
''' + actions[changed] + '\n')
    register()
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-source-invalid'):
        load_adapter(resolve_adapter('neutral'))
    assert not module.with_name('executed').exists()


def test_source_replaced_after_pin_read_is_not_executed(installed_adapter, monkeypatch):
    import fresh_review_runtime as runtime
    from mission_kernel.fresh_review import FreshReviewError

    module, _, _, register = installed_adapter
    module.write_text(ADAPTER_SOURCE)
    register()
    pin = runtime.resolve_adapter('neutral')
    original_read = runtime.read_stable_bytes
    changed = False

    def replace_after_read(path):
        nonlocal changed
        raw = original_read(path)
        if Path(path) == module and not changed:
            changed = True
            module.write_text(ADAPTER_SOURCE + '\nfrom pathlib import Path\n'
                              'Path(__file__).with_name("executed").write_text("changed")\n')
        return raw

    monkeypatch.setattr(runtime, 'read_stable_bytes', replace_after_read)
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-source-invalid'):
        runtime.load_adapter(pin)
    assert changed
    assert not module.with_name('executed').exists()


def test_source_change_during_compile_is_rejected_before_execution(installed_adapter, monkeypatch):
    import builtins
    from fresh_review_runtime import resolve_adapter, load_adapter
    from mission_kernel.fresh_review import FreshReviewError

    module, _, _, register = installed_adapter
    module.write_text(ADAPTER_SOURCE + '\nfrom pathlib import Path\n'
                      'Path(__file__).with_name("executed").write_text("approved")\n')
    register()
    pin = resolve_adapter('neutral')
    original_compile = builtins.compile
    changed = False

    def change_during_compile(source, filename, *args, **kwargs):
        nonlocal changed
        code = original_compile(source, filename, *args, **kwargs)
        if filename == str(module) and not changed:
            changed = True
            module.write_text('raise RuntimeError("changed source")\n')
        return code

    monkeypatch.setattr(builtins, 'compile', change_during_compile)
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-source-invalid'):
        load_adapter(pin)
    assert changed
    assert not module.with_name('executed').exists()


def test_cached_bytecode_cannot_replace_the_pinned_source(installed_adapter):
    import importlib.util
    from fresh_review_runtime import resolve_adapter, load_adapter

    module, _, _, register = installed_adapter
    module.write_text(ADAPTER_SOURCE)
    register()
    pin = resolve_adapter('neutral')
    foreign = module.with_name('foreign.py')
    foreign.write_text(ADAPTER_SOURCE + '\nfrom pathlib import Path\n'
                       'Path(__file__).with_name("executed").write_text("foreign bytecode")\n')
    cache = Path(importlib.util.cache_from_source(str(module)))
    cache.parent.mkdir(exist_ok=True)
    py_compile.compile(str(foreign), cfile=str(cache), dfile=str(module), doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
    # A plausible cache header must never authorize a different compiled body.
    raw = cache.read_bytes()
    cache.write_bytes(raw[:8] + importlib.util.source_hash(module.read_bytes()) + raw[16:])
    assert load_adapter(pin).observe_parent().thaw() == {'parent_identity': 'neutral-parent'}
    assert not module.with_name('executed').exists()


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


def test_callback_child_changed_pin_never_executes_adapter_code(installed_adapter):
    from fresh_review_runtime import resolve_adapter

    module, metadata, registry, register = installed_adapter
    module.write_text(ADAPTER_SOURCE + '\nfrom pathlib import Path\n'
                      'Path(__file__).with_name("executed").write_text("executed")\n')
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
    marker = module.with_name('executed')
    assert marker.read_text() == 'executed'
    marker.unlink()
    # A changed trust-root pin must not retarget the pending callback.
    value = json.loads(registry.read_bytes())
    value['adapters'][0]['version'] = '2.0'
    registry.write_text(json.dumps(value))
    (metadata / 'METADATA').write_text('Name: neutral-adapter\nVersion: 2.0\n')
    result = child()
    assert result.returncode == 2
    assert result.stdout.strip() == 'fresh-review-adapter-pin-changed'
    assert result.stderr == ''
    assert not marker.exists()


@pytest.mark.parametrize('changed,reason', [
    ('registry', 'fresh-review-adapter-pin-changed'),
    ('source', 'fresh-review-adapter-source-invalid'),
    ('metadata', 'fresh-review-adapter-distribution-invalid'),
])
def test_mutation_during_module_load_cannot_return_an_adapter(installed_adapter, changed, reason):
    from fresh_review_runtime import resolve_adapter, load_adapter
    from mission_kernel.fresh_review import FreshReviewError

    module, _, _, register = installed_adapter
    actions = {
        'registry': '''value = json.loads(registry.read_text())
value['adapters'][0]['version'] = '2.0'
registry.write_text(json.dumps(value))
metadata.write_text('Name: neutral-adapter\\nVersion: 2.0\\n')
''',
        'source': 'source.write_text(source.read_text() + "\\n# changed\\n")\n',
        'metadata': "metadata.write_text('Name: neutral-adapter\\nVersion: 2.0\\n')\n",
    }
    module.write_text(ADAPTER_SOURCE + '''
import json, os
from pathlib import Path
source = Path(__file__)
registry = Path(os.environ['XDG_CONFIG_HOME']) / 'mission' / 'fresh-review-adapters.json'
metadata = source.parent / 'neutral_adapter-1.0.dist-info' / 'METADATA'
source.with_name('executed').write_text('executed')
''' + actions[changed])
    register()
    pin = resolve_adapter('neutral')
    with pytest.raises(FreshReviewError) as error:
        load_adapter(pin)
    assert error.value.code == reason
    assert module.with_name('executed').read_text() == 'executed'


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
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'missing-user-config'))
    with pytest.raises(FreshReviewError, match='fresh-review-adapter-registry-invalid'):
        read_registry()


def test_relative_config_is_rejected_even_when_its_registry_is_valid(tmp_path, monkeypatch):
    from fresh_review_runtime import read_registry, REGISTRY_SCHEMA
    from mission_kernel.fresh_review import FreshReviewError

    path = tmp_path / 'relative-config' / 'mission' / 'fresh-review-adapters.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'schema': REGISTRY_SCHEMA, 'adapters': [_entry()]}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('XDG_CONFIG_HOME', 'relative-config')
    with pytest.raises(FreshReviewError) as error:
        read_registry()
    assert error.value.code == 'fresh-review-adapter-config-relative'


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


@pytest.mark.parametrize('value', ['bad/adapter:factory', 'neutral_adapter;execute', 'https://invalid.example',
                                 'neutral_adapter:factory [extra]'])
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
