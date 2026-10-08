"""Conservative, non-executing shell inspection for evaluated Mission runs.

This is detection, not an integrity guarantee. Script files, source files and
alias expansion are not followed; constructed paths inside programs can escape
literal inspection. Unsupported syntax is a detection, never safe.
"""
from __future__ import annotations

import fnmatch
import os
import re
from pathlib import PurePosixPath

from shell_syntax import Node, ShellSyntaxError, parse_shell

OUTPUTS = {
    ('manual-score-capture',): '--out', ('aggregate-reviews',): '--out',
    ('review-finalize',): '--out', ('context-manifest',): '--out',
    ('verification', 'claims'): '--out', ('artifact', 'export'): '--to',
    ('archive-worktree',): '--destination-root',
}
SHELLS = {'bash', 'sh', 'zsh', 'dash', 'ksh', 'mksh', 'ash', 'fish'}
DYNAMIC = re.compile(r'[$`]|__command_substitution__')
ASSIGNMENT = re.compile(r'^[A-Za-z_][A-Za-z_0-9]*=')
STATE_LITERAL = re.compile(r'''(?<![\w.-])\.mission-state(?=/|$|[\s'";()<>])''', re.IGNORECASE)


def _component(value, depth=0):
    if depth > 8:
        return True
    brace = re.search(r'\{([^{}]*,[^{}]*)\}', value)
    if brace:
        return any(_component(value[:brace.start()] + part + value[brace.end():], depth + 1) for part in brace[1].split(','))
    return fnmatch.fnmatchcase('.mission-state', value.casefold())


def _mentions_state(value):
    key, equal, destination = value.partition('=')
    if equal and (key.startswith('-') or ASSIGNMENT.match(value)): value = destination
    return any(_component(part) for part in value.split('/'))


def _payload(value):
    # Interpreter literals are inspected, not evaluated or concatenated.
    return STATE_LITERAL.search(value) is not None


def _state_path(value, cwd):
    if DYNAMIC.search(value) or value.startswith('~'):
        return True
    if not value.startswith('/') and cwd is None:
        return True
    return _mentions_state(os.path.normpath(value if value.startswith('/') else os.path.join(cwd, value)))


def _argv(values):
    values = list(values)
    while values and ASSIGNMENT.match(values[0] if isinstance(values[0], str) else values[0].raw): values.pop(0)
    values = [value if isinstance(value, str) else value.value for value in values]
    prefix = []
    while values and PurePosixPath(values[0]).name in {'env', 'sudo', 'timeout', 'command', 'builtin', 'exec', 'nohup', 'time'}:
        name = PurePosixPath(values.pop(0)).name
        while values and (values[0].startswith('-') or (name == 'env' and ASSIGNMENT.match(values[0]))):
            flag = values.pop(0); prefix.append(flag)
            if flag in {'-u', '-g', '--user', '--group', '--unset', '-k', '--kill-after', '-o'} and values:
                prefix.append(values.pop(0))
        if name == 'timeout' and values: prefix.append(values.pop(0))
    return values, prefix


def _trusted(argv, script, interpreter):
    return 1 if argv and argv[0] == script else 2 if argv[:2] == [interpreter, script] else 0


def _command(argv, cwd, script, interpreter, depth, previous=None):
    argv, prefix = _argv(argv)
    kinds = ['state_path_command'] if any(_mentions_state(v) for v in prefix) else []
    if not argv:
        return kinds
    name = PurePosixPath(argv[0]).name
    if DYNAMIC.search(argv[0]) or (name not in {'[', '[['} and any(c in argv[0] for c in '*?[')):
        return kinds + ['unparsed_script']
    if name in SHELLS:
        for index, value in enumerate(argv[1:], 1):
            if value.startswith('-') and not value.startswith('--') and 'c' in value[1:]:
                return kinds + (_script(argv[index + 1], cwd, script, interpreter, depth + 1, previous) if index + 1 < len(argv) else ['unparsed_script'])
    if name == 'eval':
        return kinds + (_script(' '.join(argv[1:]), cwd, script, interpreter, depth + 1, previous) if len(argv) > 1 else ['unparsed_script'])
    invocation = argv[1:] if name == 'mission-state.py' else None
    if argv[0] == interpreter or re.fullmatch(r'(?:python|pypy)[\d.]*', name):
        index = next((i for i, value in enumerate(argv[1:], 1) if not value.startswith('-')), len(argv))
        if index < len(argv) and PurePosixPath(argv[index]).name == 'mission-state.py': invocation = argv[index + 1:]
    if name == 'reactivate' or (invocation and invocation[0] == 'reactivate'):
        kinds.append('reactivate_command')
    offset = _trusted(argv, script, interpreter)
    if offset:
        args = argv[offset:]
        option = next((opt for command, opt in OUTPUTS.items() if tuple(args[:len(command)]) == command), None)
        if option:
            for index, arg in enumerate(args):
                if arg == '--': break
                key, equal, value = arg.partition('=')
                if len(key) >= 3 and key.startswith('--') and option.startswith(key):
                    if not equal: value = args[index + 1] if index + 1 < len(args) else ''
                    if not value or value.startswith('--'): kinds.append('unparsed_script')
                    elif option == '--destination-root' or _state_path(value, cwd): kinds.append('state_output_option')
        return kinds
    if any(_mentions_state(v) for v in argv[1:]): kinds.append('state_path_command')
    if any(v in {'-c', '-e'} and i + 1 < len(argv) and _payload(argv[i + 1]) for i, v in enumerate(argv)):
        kinds.append('state_path_interpreter')
    if any(v in {'-c', '-e'} and i + 1 < len(argv) and argv[i + 1].startswith(('$', '__command_substitution__')) for i, v in enumerate(argv)):
        kinds.append('unparsed_script')
    # Unknown expansion at a write destination is not evidence of a safe path.
    destinations = argv[-1:] if name in {'cp', 'mv', 'rsync', 'install', 'ln'} else argv[1:] if name in {'tee', 'touch', 'mkdir', 'rm', 'truncate'} else [v[3:] for v in argv if v.startswith('of=')] if name == 'dd' else []
    # Relative copy sources also expose the evaluated state. Arbitrary data
    # arguments (echo text, program source) are not filesystem operands.
    operands = argv[1:] if name in {'cp', 'mv', 'rsync', 'install', 'ln'} else destinations
    if cwd and _mentions_state(cwd) and any(not v.startswith('-') and _state_path(v, cwd) for v in operands):
        kinds.append('state_path_command')
    if any(not v.startswith('-') and _state_path(v, cwd) for v in destinations): kinds.append('state_path_command')
    return kinds


def _cd(argv, location):
    cwd, previous = location
    args = argv[1:]
    while args and args[0] in {'--', '-P', '-L'}:
        flag, args = args[0], args[1:]
        if flag == '--': break
    if len(args) != 1: return None, cwd
    value = args[0]
    if value == '-': return previous, cwd
    for variable in ('${PWD}', '$PWD'):
        if value == variable or value.startswith(variable + '/'):
            value = cwd + value[len(variable):] if cwd else '$UNKNOWN'; break
    for variable in ('~', '$HOME', '${HOME}'):
        if value == variable or value.startswith(variable + '/'):
            value = '/__shell_home__' + value[len(variable):]; break
    if DYNAMIC.search(value) or any(c in value for c in '*?['): return None, cwd
    return (os.path.normpath(value if value.startswith('/') else os.path.join(cwd, value)) if cwd or value.startswith('/') else None), cwd


def _merge(*locations):
    result = list(dict.fromkeys(item for group in locations for item in group))
    return result if len(result) <= 64 else [(None, None)]


class Inspection:
    def __init__(self, script, interpreter, depth):
        self.script, self.interpreter, self.depth, self.kinds = script, interpreter, depth, []

    def source(self, text, locations):
        if isinstance(text, Node):
            if self.depth >= 16:
                self.kinds.append('unparsed_script'); return
            inner = Inspection(self.script, self.interpreter, self.depth + 1)
            inner.visit(text, locations)
            self.kinds.extend(inner.kinds); return
        for cwd, previous in locations:
            self.kinds.extend(_script(text, cwd, self.script, self.interpreter, self.depth + 1, previous))

    def visit(self, node, locations):
        for word in node.words + [word for _, word in node.redirects]:
            for body in word.substitutions: self.source(body, locations)
        argv, _ = _argv(node.words)
        for operator, word in node.redirects:
            if operator in {'<<', '<<-', '<<<'}:
                body = word.value if operator == '<<<' else word.body
                if body is None: self.kinds.append('unparsed_script')
                elif argv and PurePosixPath(argv[0]).name in SHELLS: self.source(body, locations)
                elif _payload(body): self.kinds.append('state_path_interpreter')
            elif operator in {'>&', '<&'} and re.fullmatch(r'(?:\d+-?|-)', word.value):
                pass  # descriptor duplication/move/closure has no file target
            elif not (operator == '<' and _trusted(argv, self.script, self.interpreter)):
                if any(_state_path(word.value, cwd) for cwd, _ in locations): self.kinds.append('state_redirection')
        if node.kind == 'command':
            if argv and PurePosixPath(argv[0]).name == 'cd': return _merge([_cd(argv, loc) for loc in locations])
            for cwd, previous in locations: self.kinds.extend(_command(node.words, cwd, self.script, self.interpreter, self.depth, previous))
        elif node.kind in {'subshell', 'function', 'pipeline'}:
            for child in node.children: self.visit(child, locations)
            # Literal stdin piped into a shell can be inspected; files cannot.
            if node.kind == 'pipeline' and len(node.children) == 2:
                left, right = node.children
                values, _ = _argv([w.value for w in right.words])
                if left.kind == 'command' and left.words and left.words[0].value == 'echo' and values and PurePosixPath(values[0]).name in SHELLS:
                    self.source(' '.join(w.value for w in left.words[1:]), locations)
        elif node.kind == 'case':
            exits, fallthrough, retry = locations, [], []
            for branch in node.children:
                result = self.visit(branch.children[0], _merge(locations, fallthrough, retry))
                exits = _merge(exits, result)
                fallthrough = result if branch.kind == ';&' else []
                if branch.kind == ';;&': retry = _merge(retry, result)
            return exits
        elif node.kind == 'choice':
            return _merge(*(self.visit(child, locations) for child in node.children))
        elif node.kind == 'and':
            return self.visit(node.children[1], self.visit(node.children[0], locations))
        elif node.kind == 'or':
            first = self.visit(node.children[0], locations)
            return _merge(first, self.visit(node.children[1], _merge(locations, first)))
        elif node.kind == 'loop':
            header = self.visit(node.children[0], locations)
            return _merge(locations, header, self.visit(node.children[1], header))
        elif node.kind == 'sequence':
            for child in node.children: locations = self.visit(child, locations)
        return locations


def _script(text, cwd, script, interpreter, depth=0, previous=None):
    if depth > 16: return ['unparsed_script']
    inspection = Inspection(script, interpreter, depth)
    try:
        inspection.visit(parse_shell(text), [(cwd, previous)])
    except (ShellSyntaxError, RecursionError):
        inspection.kinds.append('unparsed_script')
    return inspection.kinds


def scan_exec_events(events, mission_state_path, interpreter_path, cwd):
    """Return event indices and detection kinds; never run a worker command."""
    if not isinstance(events, list) or not all(isinstance(p, str) and p.startswith('/') for p in (mission_state_path, interpreter_path, cwd)):
        raise ValueError('exec_scan_identity_unavailable')
    detections = []
    for index, event in enumerate(events):
        command = event.get('command') if isinstance(event, dict) else None
        event_cwd = event.get('cwd') if isinstance(event, dict) else None
        event_cwd = cwd if event_cwd is None else event_cwd if isinstance(event_cwd, str) and event_cwd.startswith('/') else None
        if isinstance(command, list) and all(isinstance(v, str) for v in command):
            kinds = _command(command, event_cwd, mission_state_path, interpreter_path, 0)
        else:
            kinds = _script(command, event_cwd, mission_state_path, interpreter_path)
        detections.extend({'event_index': index, 'kind': kind} for kind in sorted(set(kinds)))
    return detections
