"""Freeze the conservative bin/lib publication inventory before capacity gates.

This is a syntactic inventory, not proof of gate reachability.
書込みを伴いうる呼び出し・参照をすべて数え、無関係な変更でも manifest の更新を
求める。偽陽性は manifest の更新で解消する。
"""
import ast
import json
from pathlib import Path

import pytest


def _writer_calls(source):
    from collections import Counter

    sinks = {
        '_atomic_write', 'atomic_write', 'atomic_write_bytes', 'atomic_write_text',
        'stage_generation', 'write', 'write_bytes', 'write_text', 'writelines',
        'replace', 'rename', 'renames', 'link', 'hardlink_to', 'symlink',
        'symlink_to', 'dump', 'copy', 'copy2', 'copyfile', 'copytree',
        'copyfileobj', 'move', 'truncate', 'ftruncate', 'pwrite', 'writev',
        'sendfile', 'unlink', 'remove', 'rmtree', 'open', 'fdopen', 'FileIO',
    }
    found = Counter()
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    aliases = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split('.')[0]] = (
                    item.name if item.asname else item.name.split('.')[0])
        elif isinstance(node, ast.ImportFrom):
            for item in node.names:
                aliases[item.asname or item.name] = '.'.join(
                    part for part in (node.module, item.name) if part)

    def name_of(node):
        if isinstance(node, ast.Name):
            return aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return name_of(node.value) + '.' + node.attr
        return ''

    def may_write(node, name):
        # os.open uses flags, not a text mode. Keep even literal read flags.
        if name == 'os.open':
            return True
        if any(isinstance(arg, ast.Starred) for arg in node.args) or any(
                kw.arg is None for kw in node.keywords):
            return True
        tail = name.rsplit('.', 1)[-1]
        position = 1 if tail in ('fdopen', 'FileIO') or name in (
            'open', 'io.open', 'builtins.open') else 0
        mode = next((kw.value for kw in node.keywords if kw.arg == 'mode'), None)
        if mode is None and len(node.args) > position:
            mode = node.args[position]
        # A missing mode is the known default read mode. Nonliteral modes fail closed.
        return mode is not None and not (
            isinstance(mode, ast.Constant) and isinstance(mode.value, str)
            and not any(c in mode.value for c in 'wax+'))

    class Inventory(ast.NodeVisitor):
        def __init__(self):
            self.stack = []

        def record(self, kind):
            found[(self.stack[-1] if self.stack else '<module>', kind)] += 1

        def visit_FunctionDef(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            name = name_of(node.func)
            tail = name.rsplit('.', 1)[-1]
            if tail in sinks:
                if tail not in ('open', 'fdopen', 'FileIO'):
                    self.record(tail)
                elif may_write(node, name):
                    self.record(tail + '-write')
            elif tail == 'getattr':
                attribute = node.args[1] if len(node.args) > 1 else None
                if isinstance(attribute, ast.Constant) and isinstance(attribute.value, str):
                    if attribute.value in sinks:
                        self.record(attribute.value + '-dynamic')
                else:
                    self.record('dynamic')
            elif tail in ('methodcaller', 'attrgetter'):
                self.record('dynamic')
            self.generic_visit(node)

        def visit_Name(self, node):
            if isinstance(node.ctx, ast.Load):
                parent = parents.get(node)
                if not (isinstance(parent, ast.Call) and parent.func is node):
                    tail = name_of(node).rsplit('.', 1)[-1]
                    if tail in sinks:
                        self.record(tail + '-ref')
                    elif tail in ('methodcaller', 'attrgetter'):
                        self.record('dynamic')
            self.generic_visit(node)

        visit_Attribute = visit_Name

        def visit_Subscript(self, node):
            value = node.value
            if (isinstance(value, ast.Call) and name_of(value.func).rsplit('.', 1)[-1] == 'vars'
                    or isinstance(value, ast.Attribute) and value.attr == '__dict__'):
                self.record('dynamic')
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
    'os.truncate(path, 0)', 'os.ftruncate(fd, 0)', 'path.hardlink_to(target)',
    'os.renames(tmp, path)', 'os.fdopen(fd, "wb")', 'stream.writelines(lines)',
    'os.pwrite(fd, data, 0)', 'os.writev(fd, buffers)', 'io.FileIO(path, "wb")',
    'shutil.copyfileobj(src, dst)', 'os.sendfile(out_fd, in_fd, 0, count)',
    'path.unlink()', 'os.remove(path)', 'shutil.rmtree(path)',
    'open(**kw)', 'open(*args)', 'path.open(**kw)', 'os.fdopen(fd, **kw)',
    'io.FileIO(*args)', 'open(path, unknown)', 'os.fdopen(fd, mode=unknown)',
    'io.FileIO(path, mode=unknown)', 'os.open(path, os.O_RDONLY)',
    'pipe.write(data)', 'dt.replace(year=2026)', 'sys.stdout.write(data)',
    'dataclasses.replace(item, value=1)',
])
def test_inventory_detects_new_raw_publication(save):
    source = 'from os import replace as publish\ndef unguarded(path, data):\n    ' + save
    assert _writer_calls(source)


@pytest.mark.parametrize(('source', 'kind'), [
    ('publish = path.write_bytes; publish(data)', 'write_bytes-ref'),
    ('publish = _atomic_write; publish(path, data)', '_atomic_write-ref'),
    ('getattr(path, "write_bytes")(data)', 'write_bytes-dynamic'),
    ('save_via(path, data, writer=_atomic_write)', '_atomic_write-ref'),
    ('save_via(path, data, atomic_write=_atomic_write)', '_atomic_write-ref'),
    ('run(path.write_bytes, data)', 'write_bytes-ref'),
    ('functools.partial(path.write_bytes, data)()', 'write_bytes-ref'),
    ('map(path.write_bytes, items)', 'write_bytes-ref'),
    ('a, b = path.write_bytes, 1', 'write_bytes-ref'),
    ('w: object = path.write_bytes', 'write_bytes-ref'),
    ('(w := path.write_bytes)(data)', 'write_bytes-ref'),
    ('lambda p=os.replace: p(tmp, path)', 'replace-ref'),
    ('def deferred(writer=_atomic_write): pass', '_atomic_write-ref'),
    ('name = "write_bytes"; getattr(path, name)(data)', 'dynamic'),
    ('getattr(os, name)', 'dynamic'),
    ('getattr(path, "un" + "link")()', 'dynamic'),
    ('operator.methodcaller(name)(path)', 'dynamic'),
    ('operator.methodcaller("write_bytes", data)(path)', 'dynamic'),
    ('operator.attrgetter(name)(path)', 'dynamic'),
    ('operator.attrgetter("write_bytes")(path)(data)', 'dynamic'),
    ('vars(path)[name](data)', 'dynamic'),
    ('path.__dict__[name](data)', 'dynamic'),
    ('vars(path)["write_bytes"]', 'dynamic'),
    ('path.__dict__["write_bytes"]', 'dynamic'),
    ('getattr(path, "truncate")(0)', 'truncate-dynamic'),
    ('getattr(path, "hardlink_to")(target)', 'hardlink_to-dynamic'),
    ('getattr(path, "unlink")()', 'unlink-dynamic'),
    ('run(os.fdopen, fd)', 'fdopen-ref'),
    ('run(io.FileIO, path)', 'FileIO-ref'),
    ('run(shutil.copyfileobj, src, dst)', 'copyfileobj-ref'),
    ('run(os.pwrite, fd, data, 0)', 'pwrite-ref'),
    ('run(path.unlink)', 'unlink-ref'),
    ('operator.methodcaller', 'dynamic'),
    ('operator.attrgetter', 'dynamic'),
    ('from operator import methodcaller as factory; factory(name)', 'dynamic'),
    ('from operator import attrgetter as factory; factory(name)', 'dynamic'),
    ('from builtins import getattr as lookup; lookup(path, name)', 'dynamic'),
    ('from builtins import vars as attributes; attributes(path)[name]', 'dynamic'),
    ('getattr(path, "copyfileobj")(src, dst)', 'copyfileobj-dynamic'),
    ('getattr(os, "fdopen")(fd, "wb")', 'fdopen-dynamic'),
])
def test_inventory_detects_indirect_raw_publication(source, kind):
    calls = _writer_calls('def unguarded(path, data):\n    ' + source)
    assert sum(count for (_, sink), count in calls.items() if sink == kind) == 1


@pytest.mark.parametrize(('source', 'kind'), [
    ('mission_persistence.services.atomic_write(path, data)', 'atomic_write'),
    ('from . import atomic_write\natomic_write(path, data)', 'atomic_write'),
    ('from . import atomic_write as publish\npublish(path, data)', 'atomic_write'),
    ('import mission_persistence.services as svc\nsvc.atomic_write(path, data)', 'atomic_write'),
    ('from mission_persistence.services import atomic_write as publish\npublish(path, data)', 'atomic_write'),
    ('run(mission_persistence.services.atomic_write, path, data)', 'atomic_write-ref'),
    ('from . import atomic_write as publish\nrun(publish, path, data)', 'atomic_write-ref'),
    ('from os import unlink as delete\nrun(delete, path)', 'unlink-ref'),
    ('from os import fdopen as stream\nstream(fd, "wb")', 'fdopen-write'),
    ('from io import FileIO as stream\nstream(path, "wb")', 'FileIO-write'),
    ('from builtins import open as stream\nstream(**kw)', 'open-write'),
])
def test_inventory_resolves_publication_imports(source, kind):
    assert _writer_calls(source) == {('<module>', kind): 1}


@pytest.mark.parametrize('source', [
    'open(path)', 'open(path, "rb")', 'path.open(mode="r")',
    'os.fdopen(fd)', 'os.fdopen(fd, mode="rb")', 'io.FileIO(path, "r")',
    'from io import open as read\nread(path, "rb")',
])
def test_inventory_omits_known_read_modes(source):
    assert not _writer_calls(source)
