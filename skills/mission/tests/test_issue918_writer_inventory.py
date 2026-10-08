"""Freeze the conservative bin/lib publication inventory before capacity gates.

This is a syntactic inventory, not proof of gate reachability. Ambiguous calls
(e.g. replace methods and unknown open modes) remain in the reviewed manifest
so a new publication candidate requires an explicit inventory update.
"""
import ast
import json
from pathlib import Path

import pytest


def _writer_calls(source):
    from collections import Counter
    found = Counter()
    tree = ast.parse(source)
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update((item.asname or item.name, item.name) for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            aliases.update((item.asname or item.name, f'{node.module}.{item.name}') for item in node.names)
    class Inventory(ast.NodeVisitor):
        def __init__(self):
            self.stack = []
        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()
        visit_AsyncFunctionDef = visit_FunctionDef
        def visit_Assign(self, node):
            if isinstance(node.value, (ast.Attribute, ast.Name)):
                method = ast.unparse(node.value).rsplit('.', 1)[-1]
                if method in ('write', 'write_bytes', 'write_text', 'replace', 'rename', 'link',
                              'open', '_atomic_write', 'atomic_write_bytes', 'atomic_write_text', 'stage_generation'):
                    found[(self.stack[-1] if self.stack else '<module>', method + '-alias')] += 1
            self.generic_visit(node)
        def visit_Call(self, node):
            name = ast.unparse(node.func)
            parts = name.split('.')
            name = '.'.join([aliases.get(parts[0], parts[0]), *parts[1:]])
            tail = name.rsplit('.', 1)[-1]
            sink = None
            if tail in ('_atomic_write', 'stage_generation') or name in ('services.atomic_write', 'atomic_write'):
                sink = tail if tail != 'atomic_write' else name
            elif tail in ('write', 'write_bytes', 'write_text', 'replace', 'rename',
                          'link', 'symlink', 'symlink_to', 'dump', 'copy', 'copy2',
                          'copyfile', 'copytree', 'move', 'atomic_write_bytes', 'atomic_write_text'):
                if name not in ('sys.stderr.write', 'sys.stdout.write', 'dataclasses.replace'):
                    sink = tail
            elif tail == 'open':
                mode = next((kw.value for kw in node.keywords if kw.arg == 'mode'), None)
                if mode is None and node.args:
                    position = 1 if name in ('open', 'io.open', 'builtins.open') else 0
                    mode = node.args[position] if len(node.args) > position else None
                # Unknown modes can be writes. A literal read mode is safe.
                if mode is not None and not (isinstance(mode, ast.Constant)
                        and isinstance(mode.value, str) and not any(c in mode.value for c in 'wax+')):
                    sink = 'open-write'
            if name == 'getattr' and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                method = node.args[1].value
                if method in ('write', 'write_bytes', 'write_text', 'replace', 'rename', 'link', 'open'):
                    sink = method + '-dynamic'
            if sink:
                found[(self.stack[-1] if self.stack else '<module>', sink)] += 1
            self.generic_visit(node)
    Inventory().visit(tree)
    return found


def test_writer_inventory_matches_baseline_manifest():
    mission = Path(__file__).resolve().parents[1]
    inventory = {}
    for path in [*(mission / 'bin').rglob('*.py'), *(mission / 'lib').rglob('*.py')]:
        calls = _writer_calls(path.read_text())
        if calls:
            inventory[str(path.relative_to(mission))] = {
                f'{function}:{sink}': count for (function, sink), count in sorted(calls.items())}
    # Include infrastructure/evidence sinks and ambiguous syntactic candidates.
    # New raw I/O in a new module or an existing function changes this map.
    fixture = Path(__file__).parent / 'fixtures/state-writer-inventory.json'
    assert inventory == json.loads(fixture.read_text())



@pytest.mark.parametrize('save', [
    '_atomic_write(path, data)', 'path.write_bytes(data)', 'path.write_text(data)',
    'os.replace(tmp, path)', 'path.replace(target)', 'os.link(tmp, path)',
    'open(path, "wb")', 'path.open(mode="w")', 'open(path, mode=unknown)',
    'shutil.copyfile(tmp, path)', 'publish(tmp, path)',
])
def test_inventory_detects_new_raw_publication(save):
    source = 'from os import replace as publish\ndef unguarded(path, data):\n    ' + save
    assert _writer_calls(source)


@pytest.mark.parametrize('source', [
    'publish = path.write_bytes; publish(data)',
    'publish = _atomic_write; publish(path, data)',
    'getattr(path, "write_bytes")(data)',
])
def test_inventory_detects_indirect_raw_publication(source):
    assert _writer_calls('def unguarded(path, data):\n    ' + source)
