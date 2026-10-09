"""Conservative AST spawn inventory bound to design 881 section 3.2.

This freezes syntactic candidates, not arbitrary dynamically generated code.
__import__ and importlib dynamic imports are outside this syntactic boundary.
Unresolved reflective access to capability modules is fail-closed, including
references never invoked. Alias unions preserve capability across rebinding.
Runtime entry tests establish reservation-before-spawn ordering separately.
"""
import ast
from collections import Counter
import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[3]
MISSION = ROOT / 'skills/mission'
FIXTURE = Path(__file__).parent / 'fixtures/budget-spawn-inventory.json'


def spawn_calls(source):
    tree = ast.parse(source)
    aliases = {}
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    def bind(name, values):
        if not values:
            return False
        old = aliases.setdefault(name, set())
        added = values - old
        old.update(values)
        return bool(added)
    # A finite capability origin set and an absorbing unknown keep alias
    # propagation bounded, including self-referential attribute assignments.
    capability_modules = {'subprocess', 'os', 'multiprocessing', 'concurrent', 'pty', 'asyncio'}
    module_origins = capability_modules | {'concurrent.futures', 'asyncio.subprocess'}
    unknown = 'unclassified-spawn'
    def capable(values):
        return any(value == unknown or value.split('.')[0] in capability_modules for value in values)
    def attribute(values, key):
        result = {unknown if value == unknown else value.split('.')[0] + '.' + key for value in values}
        if key in {'__dict__', '__getattribute__', '__getattr__'} and capable(values):
            result.add(unknown)
        return result
    def lookup(values, key):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return attribute(values, key.value)
        return {unknown} if capable(values) else set()
    def names(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, {node.id})
        if isinstance(node, ast.Attribute):
            return attribute(names(node.value), node.attr)
        if isinstance(node, ast.Subscript):
            return lookup(names(node.value), node.slice)
        if isinstance(node, ast.Call):
            functions = names(node.func)
            if any(name.rsplit('.', 1)[-1] == 'getattr' for name in functions) and len(node.args) >= 2:
                values = names(node.args[0])
                return lookup(values, node.args[1]) or {'dynamic-spawn'}
            if any(name.rsplit('.', 1)[-1] == 'vars' for name in functions) and node.args:
                return {unknown} if capable(names(node.args[0])) else set()
            if 'operator.getitem' in functions and len(node.args) >= 2:
                return lookup(names(node.args[0]), node.args[1])
            # Unknown capability remains unknown through downstream calls.
            if unknown in functions:
                return {unknown}
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                if capable({item.name}):
                    module_origins.add(item.name)
                bind(item.asname or item.name.split('.')[0], {item.name if item.asname else item.name.split('.')[0]})
        elif isinstance(node, ast.ImportFrom):
            for item in node.names:
                bind(item.asname or item.name, {str(node.module) + '.' + item.name})
    # Union aliases, including before-definition/rebound references. A rebinding
    # must not erase a potential spawn capability from the inventory.
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        changed |= bind(target.id, names(node.value))
    def opaque_module_value(node, values):
        if not values & module_origins:
            return False
        parent = parents.get(node)
        # A static namespace receiver exposes a named API, not the entire module.
        if isinstance(parent, ast.Attribute) and parent.value is node:
            return False
        if isinstance(parent, ast.Call) and parent.args and parent.args[0] is node:
            functions = names(parent.func)
            observer = functions and functions <= {'hasattr', 'builtins.hasattr'}
            lookup_call = ('operator.getitem' in functions or
                           any(name.rsplit('.', 1)[-1] == 'getattr' for name in functions))
            static_key = (len(parent.args) >= 2 and isinstance(parent.args[1], ast.Constant)
                          and isinstance(parent.args[1].value, str))
            if observer or lookup_call and static_key:
                return False
        return True
    found = Counter()
    class Inventory(ast.NodeVisitor):
        function = '<module>'
        def visit_FunctionDef(self, node):
            previous, self.function = self.function, node.name
            self.generic_visit(node)
            self.function = previous
        visit_AsyncFunctionDef = visit_FunctionDef
        def candidate(self, node, *, invoked=False):
            values = names(node)
            if opaque_module_value(node, values):
                found[(self.function, unknown)] += 1
            for name in values:
                tail = name.rsplit('.', 1)[-1]
                if name == unknown:
                    found[(self.function, unknown)] += 1
                    continue
                if tail in {'dynamic-spawn', 'observe_parent', 'launch', 'collect', 'cancel', 'recover'} and not invoked:
                    continue
                if (name.startswith('subprocess.') and tail in {'Popen', 'run', 'call', 'check_call',
                        'check_output', 'getoutput', 'getstatusoutput'}
                    or name.startswith('os.') and (tail.startswith(('exec', 'spawn')) or tail in
                        {'fork', 'forkpty', 'system', 'popen', 'posix_spawn', 'posix_spawnp'})
                    or name.startswith('multiprocessing.')
                    or name in {'pty.fork', 'pty.spawn'}
                    or name.startswith('asyncio.create_subprocess_')
                    or tail in {'Popen', 'Process', 'ProcessPoolExecutor', 'get_context', 'spawn_exec', 'run_job',
                                'observe_parent', 'launch', 'collect', 'cancel', 'recover',
                                'dispatch_prepared_packet', 'dynamic-spawn'}):
                    found[(self.function, tail)] += 1
        def visit_Call(self, node):
            self.candidate(node)
            self.candidate(node.func, invoked=True)
            # Count the callable once, while still visiting nested expressions
            # (partial arguments and adapter.observe_parent().thaw(), for example).
            for child in ast.iter_child_nodes(node.func):
                self.visit(child)
            for argument in [*node.args, *node.keywords]:
                self.visit(argument)
        def visit_Attribute(self, node):
            self.candidate(node)
            self.visit(node.value)
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                self.candidate(node)
        def visit_Subscript(self, node):
            self.candidate(node)
            self.generic_visit(node)
    Inventory().visit(tree)
    return found


def inventory():
    result = {}
    for path in [MISSION / 'bin/mission-state.py', *(MISSION / 'lib').rglob('*.py')]:
        for (function, call), count in spawn_calls(path.read_text()).items():
            result[f'{path.relative_to(MISSION)}:{function}:{call}'] = count
    return result


def test_spawn_inventory_matches_design_table_and_has_no_unclassified_call():
    actual = inventory()
    manifest = json.loads(FIXTURE.read_text())
    assert not any(key.endswith(':unclassified-spawn') for key in actual)
    assert actual == {key: value['count'] for key, value in manifest.items()}
    text = (ROOT / 'docs/design/881-budget-reservation.md').read_text()
    table = text.split('### 3.2 ', 1)[1].split('### 3.3 ', 1)[0]
    rows = {int(n) for n in re.findall(r'^\| (\d+) \|', table, re.M)}
    assert rows == set(range(1, 17))
    for value in manifest.values():
        assert value['rows'] and set(value['rows']) <= rows and value['reason']


def test_covered_provider_entries_leave_other_dispatch_entries_pending():
    from mission_kernel.budget import BUDGET_SPAWN_ENTRIES
    assert {key for key, status in BUDGET_SPAWN_ENTRIES.items() if status == 'covered'} == {'invoke-command', 'invoke-prepared'}
    assert set(BUDGET_SPAWN_ENTRIES.values()) == {'covered', 'pending'}
    source = (MISSION / 'lib/mission_application/command_provider.py').read_text()
    calls = {node.func.id for node in ast.walk(ast.parse(source)) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert {'reserve_provider', 'settle_provider', 'spawn_exec', 'exchange_provider'} <= calls
    helper = ast.parse((MISSION / 'lib/budgeted_exec.py').read_text())
    for node in ast.walk(helper):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'Popen':
            assert 'preexec_fn' not in {keyword.arg for keyword in node.keywords}
            assert {'start_new_session', 'close_fds'} <= {keyword.arg for keyword in node.keywords}
    forks = {key for key in inventory() if key.endswith(':Process') or key.endswith(':fork') or key.endswith(':get_context')}
    assert forks == {'lib/mission_application/approval_verifier.py:run_callable:Process',
                     'lib/mission_application/approval_verifier.py:run_callable:get_context'}


@pytest.mark.parametrize('source,tail', [
    ('import subprocess as p\np.Popen([])', 'Popen'),
    ('from subprocess import run as spawn\nspawn([])', 'run'),
    ('import subprocess\nlaunch = subprocess.Popen\nlaunch([])', 'Popen'),
    ('import subprocess\nlaunch = subprocess.Popen\nlaunch = harmless\nlaunch([])', 'Popen'),
    ('execution.Popen([])', 'Popen'), ('ctx.Process(target=f)', 'Process'),
    ('os.fork()', 'fork'), ('adapter.launch(envelope)', 'launch'),
])
def test_new_or_aliased_spawn_is_a_candidate(source, tail):
    assert any(call == tail for _, call in spawn_calls(source))


@pytest.mark.parametrize('sink', ['Popen', 'run', 'call', 'check_call', 'check_output', 'getoutput', 'getstatusoutput'])
def test_dynamically_fetched_subprocess_function_is_not_hidden(sink):
    assert spawn_calls(f'import subprocess\ngetattr(subprocess, "{sink}")([])') == {('<module>', sink): 1}


def test_unknown_fetched_callable_is_an_inventory_candidate():
    assert spawn_calls('getattr(adapter, method)(envelope)') == {('<module>', 'dynamic-spawn'): 1}


def test_self_referential_attribute_alias_reaches_a_finite_inventory():
    assert spawn_calls('import subprocess\nx = x.foo\nsubprocess.Popen([])') == {('<module>', 'Popen'): 1}


@pytest.mark.parametrize('source,tail', [
    ('subprocess.__dict__["Popen"]([])', 'Popen'),
    ('os.__dict__["execv"]("cmd", [])', 'execv'),
    ('getattr(os, "execv")("cmd", [])', 'execv'),
    *[(f'os.{api}("cmd", [])', api) for api in
      ('execv', 'execvp', 'execve', 'execl', 'execle', 'execlp', 'execlpe',
       'execvpe', 'spawnv', 'spawnve', 'spawnvp', 'spawnvpe', 'spawnl',
       'spawnle', 'spawnlp', 'spawnlpe', 'popen', 'forkpty')],
    ('multiprocessing.Pool()', 'Pool'),
    ('multiprocessing.context.SpawnProcess()', 'SpawnProcess'),
    ('from concurrent.futures import ProcessPoolExecutor as pool\npool()', 'ProcessPoolExecutor'),
    ('concurrent.futures.ProcessPoolExecutor()', 'ProcessPoolExecutor'),
    ('functools.partial(subprocess.Popen, [])()', 'Popen'),
    ('consume(subprocess.Popen)', 'Popen'),
    ('consume(getattr(os, "execv"))', 'execv'),
    ('consume(subprocess.__dict__["Popen"])', 'Popen'),
    ('adapter.observe_parent()', 'observe_parent'),
])
def test_spawn_capability_calls_and_references_cannot_hide(source, tail):
    assert any(call == tail for _, call in spawn_calls(source))


@pytest.mark.parametrize('source', [
    'subprocess.__dict__.get("Popen")([])',
    'key = "Popen"\nsubprocess.__dict__[key]([])',
    'getattr(os, name)', 'vars(subprocess)["Popen"]',
    'getattr(multiprocessing.context, name)', 'vars(multiprocessing.context)',
    'operator.getitem(subprocess.__dict__, "Popen")',
    'import subprocess as p\np.__dict__',
    'from builtins import vars as fields\nfields(subprocess)',
    'import operator as op\nop.getitem(subprocess, name)',
    'import multiprocessing.context as ctx\ngetattr(ctx, name)',
    'import concurrent.futures as futures\nfutures.__dict__',
    'import pty as terminal\nvars(terminal)',
    'import asyncio as loop\nloop[key]',
    'mapping = subprocess.__dict__\nconsume(mapping.get(key))',
    'lookup = getattr(os, name)\nconsume(lookup)',
    'operator.attrgetter(name)(subprocess)',
    'subprocess.__getattribute__(name)',
    'consume(subprocess)',
    'consume(module=subprocess)',
    'lambda: subprocess',
])
def test_unresolved_module_capability_is_unclassified_even_without_call(source):
    assert any(call == 'unclassified-spawn' for _, call in spawn_calls(source))


def test_imported_spawn_name_reference_is_a_candidate():
    assert spawn_calls('from subprocess import Popen\nconsume(Popen)') == {('<module>', 'Popen'): 1}


def test_unclassified_capability_cannot_be_whitelisted(monkeypatch, tmp_path):
    manifest = json.loads(FIXTURE.read_text())
    key = 'lib/example.py:example:unclassified-spawn'
    manifest[key] = dict(count=1, rows=[3], reason='unresolved capability')
    fixture = tmp_path / 'inventory.json'
    fixture.write_text(json.dumps(manifest))
    monkeypatch.setitem(globals(), 'FIXTURE', fixture)
    monkeypatch.setitem(globals(), 'inventory', lambda: {k: v['count'] for k, v in manifest.items()})
    with pytest.raises(AssertionError):
        test_spawn_inventory_matches_design_table_and_has_no_unclassified_call()


@pytest.mark.parametrize('source', [
    'getattr(os, "getcwd")', 'getattr(subprocess, "PIPE")',
    'import os as operating\ngetattr(operating, "O_NOFOLLOW", 0)',
    'operator.getitem(os, "getcwd")', 'hasattr(os, name)',
    '__import__("subprocess").__dict__',
    'importlib.import_module("subprocess").__dict__',
])
def test_known_non_spawn_and_out_of_scope_dynamic_import_are_excluded(source):
    assert not spawn_calls(source)
