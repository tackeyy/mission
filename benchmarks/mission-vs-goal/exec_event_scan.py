"""Conservative, non-executing shell inspection for evaluated Mission runs.

This is detection, not an integrity guarantee. Script files, source files and
alias expansion are not followed; constructed paths inside programs can escape
literal inspection. Every non-excluded argv word is a potential path; echo /
grep data arguments and commands inside the state cwd may be false positives.
These detections count against the hypothesis. Unsupported syntax fails closed.
"""
from __future__ import annotations

import fnmatch
import os
import re
from pathlib import PurePosixPath

from shell_syntax import Node, Word, ShellSyntaxError, heredoc_substitutions, lex_shell, parse_shell

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


def _word(value):
    # Direct argv has already passed shell expansion; literal dollar signs stay.
    return value if isinstance(value, Word) else Word(value, raw=value)


def _state_path(word, cwd):
    word = _word(word)
    value = word.value.partition('=')[2] if word.value.startswith('-') and '=' in word.value else word.value
    if word.expanded or (value.startswith('~') and not word.quoted): return True
    if not value.startswith('/') and cwd is None: return True
    return _mentions_state(os.path.normpath(value if value.startswith('/') else os.path.join(cwd, value)))


def _argv(values):
    words = [_word(value) for value in values]
    while words and ASSIGNMENT.match(words[0].raw): words.pop(0)
    return words


def _trusted(words, script, interpreter):
    argv = [w.value for w in words]
    return 1 if argv and argv[0] == script else 2 if argv[:2] == [interpreter, script] else 0


def _shell_script(words, piped=False):
    """Find a shell command string without interpreting wrapper argv shapes."""
    seen, index = False, 1
    while index < len(words):
        word = words[index]; value = word.value
        if value.startswith('--command=') and PurePosixPath(words[0].value).name == 'fish':
            return Word(value.partition('=')[2], expanded=word.expanded)
        if value == '--command' and PurePosixPath(words[0].value).name == 'fish':
            seen = True; index += 1; continue
        if value in {'-o', '-O', '+o', '+O'}:
            if index + 1 >= len(words): raise ShellSyntaxError('missing_shell_option_value')
            index += 2; continue
        if value == '--': index += 1; continue
        if re.fullmatch(r'[-+][A-Za-z]+', value):
            seen = seen or (value.startswith('-') and 'c' in value[1:])
            index += 1; continue
        if value.startswith('--'):
            index += 1; continue
        if seen: return word
        return None  # A script file, outside literal inspection.
    if seen or piped: raise ShellSyntaxError('missing_shell_script')
    return None


def _has_expansion(text):
    try: return any(w.expanded or w.body_expanded for w in lex_shell(text))
    except (ShellSyntaxError, RecursionError): return True


def _command(words, cwd, script, interpreter, depth, previous=None, piped=False):
    words = _argv(words)
    if not words: return []
    argv = [w.value for w in words]; kinds = []
    # Inspect every position, including packed wrapper arguments such as env -S.
    atoms = [atom for value in argv for atom in value.split()]
    if argv[0] == 'reactivate' or any(PurePosixPath(atom).name == 'mission-state.py' and 'reactivate' in atoms[i + 1:] for i, atom in enumerate(atoms)):
        kinds.append('reactivate_command')
    offset = _trusted(words, script, interpreter)
    if offset:
        args = words[offset:]
        option = next((opt for command, opt in OUTPUTS.items() if tuple(w.value for w in args[:len(command)]) == command), None)
        if option:
            for index, arg in enumerate(args):
                if arg.value == '--': break
                key, equal, value = arg.value.partition('=')
                if len(key) >= 3 and key.startswith('--') and option.startswith(key):
                    target = Word(value, quoted=arg.quoted, expanded=arg.expanded) if equal else args[index + 1] if index + 1 < len(args) else Word('')
                    if not target.value or target.value.startswith('--'): kinds.append('unparsed_script')
                    elif option == '--destination-root' or _state_path(target, cwd): kinds.append('state_output_option')
        return kinds
    if (cwd and _mentions_state(cwd)) or any(_state_path(w, cwd) for w in words): kinds.append('state_path_command')
    name = PurePosixPath(argv[0]).name
    if words[0].expanded or (name not in {'[', '[['} and any(c in argv[0] for c in '*?[')):
        kinds.append('unparsed_script')
    for index, word in enumerate(words):
        executable = PurePosixPath(word.value).name
        if executable in SHELLS:
            try: body = _shell_script(words[index:], piped)
            except ShellSyntaxError:
                kinds.append('unparsed_script'); continue
            if body is not None:
                if body.expanded or _has_expansion(body.value):
                    kinds.append('unparsed_script')
                kinds.extend(_script(body.value, cwd, script, interpreter, depth + 1, previous))
        elif executable == 'eval':
            body = words[index + 1:]; text = ' '.join(w.value for w in body)
            if not body or any(w.expanded for w in body) or _has_expansion(text):
                kinds.append('unparsed_script')
            kinds.extend(_script(text, cwd, script, interpreter, depth + 1, previous))
    if name not in SHELLS:
        for i, arg in enumerate(words[:-1]):
            if arg.value in {'-c', '-e'}:
                if _payload(words[i + 1].value): kinds.append('state_path_interpreter')
                if words[i + 1].expanded: kinds.append('unparsed_script')
    return kinds


def _cd(words, location):
    cwd, previous = location
    args = words[1:]
    while args and args[0].value in {'--', '-P', '-L'}:
        flag, args = args[0].value, args[1:]
        if flag == '--': break
    if len(args) != 1: return None, cwd
    word = args[0]; value = word.value
    if value == '-': return previous, cwd
    if word.expanded:
        for variable in ('${PWD}', '$PWD'):
            if value == variable or value.startswith(variable + '/'):
                value = cwd + value[len(variable):] if cwd else '$UNKNOWN'; break
        for variable in ('$HOME', '${HOME}'):
            if value == variable or value.startswith(variable + '/'):
                value = '/__shell_home__' + value[len(variable):]; break
        if DYNAMIC.search(value): return None, cwd
    if not word.quoted and (value == '~' or value.startswith('~/')): value = '/__shell_home__' + value[1:]
    if any(c in value for c in '*?['): return None, cwd
    return (os.path.normpath(value if value.startswith('/') else os.path.join(cwd, value)) if cwd or value.startswith('/') else None), cwd


def _merge(*locations):
    result = list(dict.fromkeys(item for group in locations for item in group))
    return result if len(result) <= 64 else [(None, None)]


class Inspection:
    def __init__(self, script, interpreter, depth):
        self.script, self.interpreter, self.depth, self.kinds = script, interpreter, depth, []
        self.functions, self.active = {}, set()
        self.piped = False

    def source(self, text, locations):
        if isinstance(text, Node):
            if self.depth >= 16:
                self.kinds.append('unparsed_script'); return
            inner = Inspection(self.script, self.interpreter, self.depth + 1)
            inner.functions = dict(self.functions)
            inner.visit(text, locations)
            self.kinds.extend(inner.kinds); return
        for cwd, previous in locations:
            self.kinds.extend(_script(text, cwd, self.script, self.interpreter, self.depth + 1, previous))

    def visit(self, node, locations):
        for word in node.words + [word for _, word in node.redirects]:
            for body in word.substitutions: self.source(body, locations)
        words = _argv(node.words)
        argv = [w.value for w in words]
        for operator, word in node.redirects:
            if operator in {'<<', '<<-', '<<<'}:
                body = word.value if operator == '<<<' else word.body
                if body is None: self.kinds.append('unparsed_script')
                else:
                    if operator in {'<<', '<<-'} and not word.quoted:
                        for substitution in heredoc_substitutions(body): self.source(substitution, locations)
                    if argv and PurePosixPath(argv[0]).name in SHELLS: self.source(body, locations)
                    elif _payload(body): self.kinds.append('state_path_interpreter')
            elif operator in {'>&', '<&'} and re.fullmatch(r'(?:\d+-?|-)', word.value):
                pass  # descriptor duplication/move/closure has no file target
            elif not (operator == '<' and _trusted(words, self.script, self.interpreter)):
                if any(_state_path(word, cwd) for cwd, _ in locations): self.kinds.append('state_redirection')
        if node.kind == 'command':
            for cwd, previous in locations: self.kinds.extend(_command(words, cwd, self.script, self.interpreter, self.depth, previous, self.piped))
            if argv and PurePosixPath(argv[0]).name == 'cd': return _merge([_cd(words, loc) for loc in locations])
            if argv and argv[0] in self.functions:
                if argv[0] in self.active:
                    self.kinds.append('unparsed_script'); return [(None, None)]
                bodies = self.functions[argv[0]]; self.active.add(argv[0])
                try: result = _merge(*(self.visit(body, locations) for body in bodies))
                finally: self.active.remove(argv[0])
                # Calls are opaque to subsequent cwd tracking if any body has cd.
                def has_cd(node):
                    return (node.kind == 'command' and any(w.value == 'cd' for w in node.words)) or any(has_cd(child) for child in node.children)
                return [(None, None)] if any(has_cd(body) for body in bodies) else result
        elif node.kind == 'function':
            # Keep possible definitions across branches; replacing one would
            # incorrectly choose the last syntactically visited alternative.
            self.functions[argv[0]] = self.functions.get(argv[0], []) + [node.children[0]]
            saved = dict(self.functions)
            self.visit(node.children[0], locations)
            self.functions = saved
        elif node.kind in {'subshell', 'pipeline'}:
            for index, child in enumerate(node.children):
                saved, piped = dict(self.functions), self.piped
                self.piped = piped or (node.kind == 'pipeline' and index > 0)
                self.visit(child, locations)
                self.functions, self.piped = saved, piped
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
        try:
            if isinstance(command, list) and all(isinstance(v, str) for v in command):
                kinds = _command(command, event_cwd, mission_state_path, interpreter_path, 0)
            else:
                kinds = _script(command, event_cwd, mission_state_path, interpreter_path)
        except (ShellSyntaxError, RecursionError):
            kinds = ['unparsed_script']
        detections.extend({'event_index': index, 'kind': kind} for kind in sorted(set(kinds)))
    return detections
