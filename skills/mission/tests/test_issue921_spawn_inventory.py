"""Conservative AST spawn inventory bound to design 881 section 3.2.

This freezes syntactic candidates, not arbitrary dynamically generated code.
__import__, importlib and vars() are outside this syntactic boundary.
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
    def bind(name, values):
        if not values:
            return False
        old = aliases.setdefault(name, set())
        added = values - old
        old.update(values)
        return bool(added)
    def names(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, {node.id})
        if isinstance(node, ast.Attribute):
            return {name.split('.')[0] + '.' + node.attr for name in names(node.value)}
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
            return {name.split('.')[0] + '.' + node.slice.value for name in names(node.value)}
        if isinstance(node, ast.Call) and any(name.rsplit('.', 1)[-1] == 'getattr' for name in names(node.func)) and len(node.args) >= 2:
            attribute = node.args[1]
            if isinstance(attribute, ast.Constant) and isinstance(attribute.value, str):
                return {name.split('.')[0] + '.' + attribute.value for name in names(node.args[0])}
            return {'dynamic-spawn'}
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
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
    found = Counter()
    class Inventory(ast.NodeVisitor):
        function = '<module>'
        def visit_FunctionDef(self, node):
            previous, self.function = self.function, node.name
            self.generic_visit(node)
            self.function = previous
        visit_AsyncFunctionDef = visit_FunctionDef
        def candidate(self, node, *, invoked=False):
            for name in names(node):
                tail = name.rsplit('.', 1)[-1]
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
