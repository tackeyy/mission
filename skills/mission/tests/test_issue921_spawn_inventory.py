"""Conservative AST spawn inventory bound to design 881 section 3.2.

This freezes syntactic candidates, not arbitrary dynamically generated code.
exec/eval, importlib, __import__, and getattr(*args) are outside the static
boundary of design section 3.2; they can generate arbitrary code or names.
Native FFI calls such as libc.vfork() are also outside this syntactic scope.
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


# Only non-spawning references observed in bin/mission-state.py and lib/.
# Exact names (no prefix exemptions): a future API must be classified explicitly.
SAFE = set("""os.CLD_EXITED os.CLD_KILLED os.CLD_DUMPED
os.O_CLOEXEC os.O_CREAT os.O_DIRECTORY os.O_EXCL os.O_NOFOLLOW os.O_NONBLOCK
os.O_RDONLY os.O_RDWR os.O_WRONLY os.P_PID os.PathLike os.WEXITED os.WNOHANG
os.WNOWAIT os.X_OK os.access os.chmod os.close os.defpath os.dup os.environ
os.environ.get os.fchmod os.fdopen os.fspath os.fstat os.fsync os.getpid os.getppid
os.getuid os.getpgrp os.kill os.killpg os.link os.listdir os.lstat os.mkdir os.open os.path
os.path.abspath os.path.basename os.path.lexists os.path.normpath os.pathsep
os.pathsep.join os.pipe os.read os.readlink os.rename os.replace os.rmdir os.scandir
os.sep os.set_blocking os.set_inheritable os.setpgid os.setsid os.stat os.stat_result os.unlink
os.waitid os.walk os.write subprocess.DEVNULL subprocess.PIPE subprocess.STDOUT
subprocess.TimeoutExpired multiprocessing.connection multiprocessing.connection.wait""".split())
CAPABILITIES = {'subprocess', 'os', 'posix', '_posixsubprocess', 'multiprocessing',
                'concurrent', 'asyncio', 'pty'}
UNKNOWN = 'unclassified-spawn'
SUBPROCESS_APIS = {'run', 'call', 'check_call', 'check_output', 'getoutput', 'getstatusoutput'}
TAILS = set("""Popen Process Pool ProcessPoolExecutor get_context fork forkpty system
popen launch collect cancel recover observe_parent run_job dispatch_prepared_packet
_run_bounded create_worker_export initialize_worker_export_repository run_contract_verifier
subprocess_exec subprocess_shell""".split())


def spawn_calls(source):
    tree, aliases = ast.parse(source), {}
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    def capable(name):
        return name.split('.')[0] in CAPABILITIES
    def known_spawn(name):
        # Every receiver and bare binding uses the same syntactic tail rule.
        # Builtin exec/eval generate arbitrary code and remain out of scope.
        tail = name.rsplit('.', 1)[-1]
        return tail not in {'exec', 'eval'} and (tail in TAILS | SUBPROCESS_APIS or
            tail.startswith(('spawn', 'exec', 'posix_spawn', 'create_subprocess_')))
    def attribute(values, key):
        # Unknown receivers still expose syntactic spawn tails. Collapse other
        # origins so self-referential assignments have a finite fixed point.
        return {value + '.' + key if capable(value) and value + '.' + key in SAFE
                else value.split('.')[0] + '.' + key for value in values} or {'?.' + key}
    def lookup(values, key):
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            return attribute(values, key.value)
        return {UNKNOWN} if any(capable(n) or n == UNKNOWN for n in values) else set()
    def names(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, {node.id})
        if isinstance(node, ast.Attribute):
            return attribute(names(node.value), node.attr)
        if isinstance(node, ast.Subscript):
            return lookup(names(node.value), node.slice)
        if isinstance(node, ast.Call):
            functions = names(node.func)
            if functions & {'getattr', 'builtins.getattr', 'operator.getitem'} and len(node.args) >= 2:
                return lookup(names(node.args[0]), node.args[1]) or {'dynamic-spawn'}
            if functions & {'vars', 'builtins.vars'} and node.args and any(capable(n) for n in names(node.args[0])):
                return {UNKNOWN}
        return set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for item in node.names:
                name = str(node.module) + '.' + item.name if isinstance(node, ast.ImportFrom) else item.name
                aliases.setdefault(item.asname or item.name.split('.')[0], set()).add(name if item.asname or isinstance(node, ast.ImportFrom) else name.split('.')[0])
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                for target in node.targets if isinstance(node, ast.Assign) else [node.target]:
                    if isinstance(target, ast.Name):
                        old = aliases.setdefault(target.id, set()); added = names(node.value) - old
                        old.update(added); changed |= bool(added)
    imported_capability = any(capable(n) for values in aliases.values() for n in values)
    found = Counter()
    class Inventory(ast.NodeVisitor):
        function = '<module>'
        def visit_FunctionDef(self, node):
            previous, self.function = self.function, node.name
            self.generic_visit(node); self.function = previous
        visit_AsyncFunctionDef = visit_FunctionDef
        def candidate(self, node):
            parent = parents.get(node)
            # Namespace receivers are checked at the outer named reference;
            # reflective dictionary access is never a safe namespace.
            receiver = isinstance(parent, ast.Attribute) and parent.value is node
            observer = (isinstance(parent, ast.Call) and parent.args and parent.args[0] is node
                        and bool(names(parent.func)) and names(parent.func) <=
                        {'hasattr', 'builtins.hasattr', 'getattr', 'builtins.getattr', 'operator.getitem'})
            values = names(node)
            if isinstance(node, ast.Name) and known_spawn(node.id) and not any(
                    name.rsplit('.', 1)[-1] == node.id for name in values):
                values = values | {node.id}
            if imported_capability and (values & {'sys.modules'} or
                    any(name.rsplit('.', 1)[-1] in {'__globals__', 'f_globals'} for name in values)):
                # The namespace reference is statically identified; freeze it
                # separately from unresolved capability names, never exempt it.
                found[(self.function, 'namespace-access')] += 1
            for name in values:
                tail = name.rsplit('.', 1)[-1]
                if name in SAFE:
                    continue
                invoked_lookup = (tail == 'dynamic-spawn' and isinstance(parent, ast.Call)
                                  and parent.func is node)
                if invoked_lookup or known_spawn(name):
                    found[(self.function, tail)] += 1
                elif name == UNKNOWN or capable(name) and (tail.startswith('__') or not (receiver and name in CAPABILITIES) and not (observer and name in CAPABILITIES)):
                    found[(self.function, UNKNOWN)] += 1
        def visit_Import(self, node):
            # Root imports declare namespaces; every non-allowlisted nested
            # module can expose capabilities even if it is never referenced.
            for item in node.names:
                if capable(item.name) and item.name not in CAPABILITIES | SAFE:
                    found[(self.function, UNKNOWN)] += 1
        def visit_ImportFrom(self, node):
            for item in node.names:
                name = str(node.module) + '.' + item.name
                if capable(name) and name not in SAFE:
                    tail = item.name
                    found[(self.function, tail if known_spawn(name) else UNKNOWN)] += 1
        def visit_Call(self, node):
            if imported_capability and names(node.func) & {'globals', 'locals', 'vars', 'builtins.globals', 'builtins.locals', 'builtins.vars'}:
                found[(self.function, UNKNOWN)] += 1
            self.candidate(node)
            self.candidate(node.func)
            for child in ast.iter_child_nodes(node.func): self.visit(child)
            for argument in [*node.args, *node.keywords]: self.visit(argument)
        def visit_Attribute(self, node):
            self.candidate(node); self.visit(node.value)
        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load): self.candidate(node)
        def visit_Subscript(self, node):
            self.candidate(node); self.generic_visit(node)
    Inventory().visit(tree)
    return found


def inventory():
    result = {}
    benchmarks = [ROOT / 'benchmarks/mission-vs-goal' / name
                  for name in ('public_benchmark.py', 'bench_selection.py', 'bench_cohort.py')
                  if name != 'bench_cohort.py' or (ROOT / 'benchmarks/mission-vs-goal' / name).is_file()]
    for path in [MISSION / 'bin/mission-state.py', *(MISSION / 'lib').rglob('*.py'), *benchmarks]:
        for (function, call), count in spawn_calls(path.read_text()).items():
            relative = path.relative_to(ROOT) if path in benchmarks else path.relative_to(MISSION)
            result[f'{relative}:{function}:{call}'] = count
    return result


def manifest_counts():
    return {key: dict(count=count, rows=group['rows'], reason=group['reason'])
            for group in json.loads(FIXTURE.read_text()).values()
            for key, count in group['candidates'].items()}


def test_spawn_inventory_matches_design_table_and_has_no_unclassified_call():
    actual = inventory()
    manifest = manifest_counts()
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
    ('multiprocessing.context.SpawnProcess()', 'unclassified-spawn'),
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
    assert spawn_calls('from subprocess import Popen\nconsume(Popen)') == {('<module>', 'Popen'): 2}


def test_unclassified_capability_cannot_be_whitelisted(monkeypatch, tmp_path):
    manifest = manifest_counts()
    key = 'lib/example.py:example:unclassified-spawn'
    manifest[key] = dict(count=1, rows=[3], reason='unresolved capability')
    fixture = tmp_path / 'inventory.json'
    fixture.write_text(json.dumps({'probe': dict(candidates={k: v['count'] for k, v in manifest.items()}, rows=[3], reason='probe')}))
    monkeypatch.setitem(globals(), 'FIXTURE', fixture)
    monkeypatch.setitem(globals(), 'inventory', lambda: {k: v['count'] for k, v in manifest.items()})
    with pytest.raises(AssertionError):
        test_spawn_inventory_matches_design_table_and_has_no_unclassified_call()


@pytest.mark.parametrize('source', [
    *[f'{name}' for name in sorted(SAFE)], 'getattr(os, "fspath")', 'getattr(subprocess, "PIPE")',
    'import os as operating\ngetattr(operating, "O_NOFOLLOW", 0)',
    'operator.getitem(os, "fspath")', 'hasattr(os, name)',
    '__import__("subprocess").__dict__',
    'importlib.import_module("subprocess").__dict__', 'exec(code)', 'eval(code)', 'getattr(*args)', 'globals()', 'locals()',
    'libc.vfork()', 'fn.__globals__', 'frame.f_globals', 'sys.modules["os"]',
])
def test_known_non_spawn_and_out_of_scope_dynamic_import_are_excluded(source):
    assert not spawn_calls(source)


@pytest.mark.parametrize('source', [
    *[f'from {module} import __dict__ as d\nd.get("Popen")([])' for module in
      ('subprocess', 'os', 'concurrent.futures', 'asyncio', 'pty')],
    'factory().Popen([])', 'x[i].Popen', '(a or b).Popen',
    'adapter_for(e).launch(env)', 'self.adapters[name].launch(env)',
    'import subprocess\nglobals()["subprocess"].Popen([])',
    'import subprocess\nglobals().get("subprocess").Popen',
    *[f'from subprocess import Popen as P\n{scope}()["P"]' for scope in ('globals', 'locals')],
    'mp().get_context("spawn").Process(target=f)',
    'from os import *\nsystem("x")', 'from subprocess import *\nrun([])',
    'import subprocess\nsubprocess.future_api',
    'import os as m\nglobals()', 'import subprocess as m\nlocals()',
    'from os import future_api', 'subprocess.future_api.PIPE', 'os.future_api.environ', 'getattr(subprocess.future_api, "PIPE")',
])
def test_allowlist_boundary_rejects_unknown_capabilities_and_receivers(source):
    assert spawn_calls(source)


def test_safe_allowlist_contains_only_observed_runtime_names():
    observed = set()
    benchmarks = [ROOT / 'benchmarks/mission-vs-goal' / name
                  for name in ('public_benchmark.py', 'bench_selection.py', 'bench_cohort.py')
                  if name != 'bench_cohort.py' or (ROOT / 'benchmarks/mission-vs-goal' / name).is_file()]
    for path in [MISSION / 'bin/mission-state.py', *(MISSION / 'lib').rglob('*.py'), *benchmarks]:
        tree, aliases = ast.parse(path.read_text()), {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    aliases[item.asname or item.name.split('.')[0]] = item.name if item.asname else item.name.split('.')[0]
            elif isinstance(node, ast.ImportFrom):
                observed.add(str(node.module))
                for item in node.names:
                    aliases[item.asname or item.name] = str(node.module) + '.' + item.name
        def origin(node):
            if isinstance(node, ast.Name): return aliases.get(node.id, node.id)
            if isinstance(node, ast.Attribute): return origin(node.value) + '.' + node.attr
            return '?'
        for node in ast.walk(tree):
            if isinstance(node, (ast.Name, ast.Attribute)): observed.add(origin(node))
            if isinstance(node, ast.Call) and origin(node.func) == 'getattr' and len(node.args) >= 2:
                key = node.args[1]
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    observed.add(origin(node.args[0]) + '.' + key.value)
    assert SAFE <= observed


@pytest.mark.parametrize('tail', sorted(TAILS | SUBPROCESS_APIS | {
    'posix_spawn', 'posix_spawnp', 'execv', 'spawnv', 'spawn', 'create_subprocess_exec',
    'spawn_future_api', 'exec_future_api', 'posix_spawn_future_api', 'create_subprocess_future_api'}))
@pytest.mark.parametrize('use', ['{tail}([])', 'consume({tail})', '{tail} = harmless\nconsume({tail})', 'keyword:{tail}([])'])
def test_bare_spawn_tail_cannot_be_hidden_by_injection_or_rebinding(tail, use):
    signature = f'*, {tail}=None' if use.startswith('keyword:') else tail
    use = use.removeprefix('keyword:')
    source = f'def invoke({signature}):\n' + '\n'.join('    ' + line for line in use.format(tail=tail).splitlines())
    assert ('invoke', tail) in spawn_calls(source)


@pytest.mark.parametrize('source', [
    'import multiprocessing.popen_fork', 'import os.future_api',
    'import multiprocessing.popen_fork as p', 'import os.future_api as p',
    'loop.subprocess_exec(protocol, "cmd")', 'loop.subprocess_shell(protocol, "cmd")',
    'import subprocess\nfn.__globals__', 'import os\nframe.f_globals',
    'import os\ngetattr(fn, "__globals__")', 'import os\ngetattr(frame, "f_globals")',
    'import subprocess\nimport sys\nsys.modules["subprocess"]',
    'import os\nimport sys as s\ns.modules.get("os")',
    'import os\nfrom sys import modules as m\nconsume(m)',
])
def test_imports_and_namespace_escape_routes_are_inventory_candidates(source):
    assert spawn_calls(source)


@pytest.mark.parametrize('name', sorted(SAFE))
def test_allowlisted_imports_do_not_introduce_spawn_candidates(name):
    module, _, api = name.rpartition('.')
    assert not spawn_calls(f'from {module} import {api} as reference\nconsume(reference)')


@pytest.mark.parametrize('present', [False, True])
def test_inventory_scans_optional_second_pr_cohort(monkeypatch, present):
    target = ROOT / 'benchmarks/mission-vs-goal/bench_cohort.py'
    read, exists = Path.read_text, Path.is_file
    monkeypatch.setattr(Path, 'is_file', lambda path: present if path == target else exists(path))
    def read_source(path, *args, **kwargs):
        if path == target:
            assert present
            return 'import subprocess\ndef new_run():\n    subprocess.run([])\n'
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read_source)
    found = inventory()
    assert any('bench_cohort.py:new_run:run' in key for key in found) is present
