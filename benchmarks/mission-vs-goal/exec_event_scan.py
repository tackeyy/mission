"""Conservative, non-executing shell inspection for evaluated Mission runs.

This is detection, not an integrity guarantee. Script files, source files and
alias expansion are not followed; constructed paths inside programs can escape
literal inspection. Every non-excluded argv word is a potential path; echo /
grep data arguments and commands inside the state cwd may be false positives.
These detections count against the hypothesis. Unknown argument expansion is
inspected by its literal pieces; unknown write destinations fail closed.
Only definite simple bindings are substituted.
Literal/glob loop paths are inspected; uncertain control-flow bindings are not inferred. Expanded command names,
expansion-dependent eval and scripts that cannot be parsed fail closed.
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


def _resolve(word, variables):
    """Substitute known variables; retain only literal pieces of unknown words."""
    word = _word(word)
    if not word.expansions: return word
    parts, end, unknown = [], 0, False
    for start, stop, name in word.expansions:
        parts.append(word.value[end:start])
        value = variables.get(name) if name is not None else None
        unknown |= value is None
        parts.append(value if value is not None else '')
        end = stop
    parts.append(word.value[end:])
    return Word(''.join(parts), quoted=word.quoted, raw=word.raw, expanded=unknown)


def _state_path(word, cwd, *, writing=False):
    word = _word(word)
    value = word.value.partition('=')[2] if '=' in word.value else word.value
    # Unknown write destinations fail closed; other words retain literal-only
    # inspection. A missing expansion could resolve to an absolute path.
    if word.expanded: return writing or _mentions_state(value)
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


def _command(words, cwd, script, interpreter, depth, previous=None, piped=False, variables=None, environment=None):
    words = _argv(words)
    if not words: return []
    command_expanded = words[0].expanded
    words = [_resolve(w, variables or {}) for w in words]
    environment = variables if environment is None else environment
    argv = [w.value for w in words]; kinds = []
    # Inspect every position, including packed wrapper arguments such as env -S.
    atoms = [atom for value in argv for atom in value.split()]
    if argv[0] == 'reactivate' or any(PurePosixPath(atom).name == 'mission-state.py' and 'reactivate' in atoms[i + 1:] for i, atom in enumerate(atoms)):
        kinds.append('reactivate_command')
    if command_expanded: kinds.append('unparsed_script')
    offset = 0 if command_expanded else _trusted(words, script, interpreter)
    if offset:
        args = words[offset:]
        option = next((opt for command, opt in OUTPUTS.items() if tuple(w.value for w in args[:len(command)]) == command), None)
        if option:
            for index, arg in enumerate(args):
                if arg.value == '--': break
                key, equal, value = arg.value.partition('=')
                if len(key) >= 3 and key.startswith('--') and option.startswith(key):
                    target = Word(value, quoted=arg.quoted, expanded=arg.expanded) if equal else args[index + 1] if index + 1 < len(args) else Word('')
                    # Option '=' values are interpreted as paths too; an
                    # unquoted home prefix remains unknown unless HOME is bound.
                    if equal and arg.raw.partition('=')[2].startswith('~'):
                        target = _resolve(lex_shell(arg.raw.partition('=')[2])[0], variables or {})
                    if not target.expanded and (not target.value or target.value.startswith('--')): kinds.append('unparsed_script')
                    elif option == '--destination-root' or _state_path(target, cwd, writing=True): kinds.append('state_output_option')
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
                if body.expanded:
                    kinds.append('unparsed_script')
                kinds.extend(_script(body.value, cwd, script, interpreter, depth + 1, previous, environment))
        elif executable == 'eval':
            body = words[index + 1:]; text = ' '.join(w.value for w in body)
            if not body or any(w.expanded for w in body) or _has_expansion(text):
                kinds.append('unparsed_script')
            kinds.extend(_script(text, cwd, script, interpreter, depth + 1, previous, environment))
    if name not in SHELLS:
        for i, arg in enumerate(words[:-1]):
            if arg.value in {'-c', '-e'}:
                if _payload(words[i + 1].value): kinds.append('state_path_interpreter')
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
    if word.expanded: return None, cwd
    if any(c in value for c in '*?['): return None, cwd
    return (os.path.normpath(value if value.startswith('/') else os.path.join(cwd, value)) if cwd or value.startswith('/') else None), cwd


def _merge(*locations):
    result = list(dict.fromkeys(item for group in locations for item in group))
    return result if len(result) <= 64 else [(None, None)]


def _common(bindings):
    # Only definite simple bindings survive a control-flow join.
    return {k: v for k, v in bindings[0].items() if all(b.get(k) == v for b in bindings[1:])} if bindings else {}


class Inspection:
    def __init__(self, script, interpreter, depth):
        self.script, self.interpreter, self.depth, self.kinds = script, interpreter, depth, []
        self.functions, self.active = {}, set()
        self.piped = False
        self.variables = {}

    def source(self, text, locations):
        if isinstance(text, Node):
            if self.depth >= 16:
                self.kinds.append('unparsed_script'); return
            inner = Inspection(self.script, self.interpreter, self.depth + 1)
            inner.functions = dict(self.functions)
            inner.variables = dict(self.variables)
            inner.visit(text, locations)
            self.kinds.extend(inner.kinds); return
        for cwd, previous in locations:
            self.kinds.extend(_script(text, cwd, self.script, self.interpreter, self.depth + 1, previous, self.variables))

    def visit(self, node, locations):
        saved = dict(self.variables)
        if node.kind == 'command':
            prefix = []
            for word in node.words:
                if not ASSIGNMENT.match(word.raw): break
                prefix.append(word)
            words = node.words[len(prefix):]
            exports = words and words[0].value == 'export'
            environment = dict(saved)
            for word in prefix + (words[1:] if exports else []):
                if not ASSIGNMENT.match(word.raw): continue
                name = word.value.partition('=')[0]
                value = _resolve(word, environment)
                if value.expanded: environment.pop(name, None)
                else: environment[name] = value.value.partition('=')[2]
            # argv and redirects expand before temporary command assignments.
            if not words or exports: self.variables = environment
            result = self.inspect(node, locations, environment)
            if prefix and words and not exports:
                for word in prefix:
                    name = word.value.partition('=')[0]
                    if name in saved: self.variables[name] = saved[name]
                    else: self.variables.pop(name, None)
            return result
        result = self.inspect(node, locations)
        if node.kind in {'function', 'subshell', 'pipeline'}: self.variables = saved
        return result

    def alternatives(self, children, locations):
        saved, bindings, results = dict(self.variables), [], []
        for child in children:
            self.variables = dict(saved)
            results.append(self.visit(child, locations)); bindings.append(dict(self.variables))
        self.variables = _common(bindings)
        return _merge(*results)

    def inspect(self, node, locations, environment=None):
        for word in node.words + [word for _, word in node.redirects]:
            for body in word.substitutions: self.source(body, locations)
        words = [_resolve(w, self.variables) for w in _argv(node.words)]
        argv = [w.value for w in words]
        for operator, word in node.redirects:
            target = _resolve(word, self.variables)
            if operator in {'<<', '<<-', '<<<'}:
                body = word.value if operator == '<<<' else word.body
                if body is None: self.kinds.append('unparsed_script')
                else:
                    if operator in {'<<', '<<-'} and not word.quoted:
                        for substitution in heredoc_substitutions(body): self.source(substitution, locations)
                    if argv and PurePosixPath(argv[0]).name in SHELLS: self.source(body, locations)
                    elif _payload(body): self.kinds.append('state_path_interpreter')
            elif operator in {'>&', '<&'} and not target.expanded and re.fullmatch(r'(?:\d+-?|-)', target.value):
                pass  # descriptor duplication/move/closure has no file target
            elif not (operator == '<' and _trusted(words, self.script, self.interpreter)):
                if any(_state_path(target, cwd, writing=operator in {'>', '>>', '&>', '&>>', '>|', '>&'}) for cwd, _ in locations): self.kinds.append('state_redirection')
        if node.kind == 'command':
            if argv and argv[0] == 'export': return locations
            if argv and argv[0] == 'unset':
                for name in argv[1:]: self.variables.pop(name, None)
                return locations
            if argv and not _argv(node.words)[0].expanded and PurePosixPath(argv[0]).name == 'cd':
                return _merge([_cd([_resolve(w, {'HOME': '/__shell_home__', **self.variables, 'PWD': cwd}) for w in _argv(node.words)], (cwd, previous)) for cwd, previous in locations])
            for cwd, previous in locations: self.kinds.extend(_command(node.words, cwd, self.script, self.interpreter, self.depth, previous, self.piped, self.variables, environment))
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
                saved, piped, bindings = dict(self.functions), self.piped, dict(self.variables)
                self.piped = piped or (node.kind == 'pipeline' and index > 0)
                self.visit(child, locations)
                self.functions, self.piped, self.variables = saved, piped, bindings
        elif node.kind == 'case':
            saved = dict(self.variables); bindings = [saved]
            exits, fallthrough, retry = locations, [], []
            for branch in node.children:
                self.variables = _common(bindings) if fallthrough or retry else dict(saved)
                result = self.visit(branch.children[0], _merge(locations, fallthrough, retry))
                bindings.append(dict(self.variables))
                exits = _merge(exits, result)
                fallthrough = result if branch.kind == ';&' else []
                if branch.kind == ';;&': retry = _merge(retry, result)
            self.variables = _common(bindings)
            return exits
        elif node.kind == 'choice':
            return self.alternatives(node.children, locations)
        elif node.kind == 'and':
            return self.visit(node.children[1], self.visit(node.children[0], locations))
        elif node.kind == 'or':
            saved = dict(self.variables)
            first = self.visit(node.children[0], locations); left = dict(self.variables)
            self.variables = _common([saved, left])
            result = _merge(first, self.visit(node.children[1], _merge(locations, first)))
            self.variables = _common([left, self.variables])
            return result
        elif node.kind == 'loop':
            saved = dict(self.variables)
            header = self.visit(node.children[0], locations)
            words = node.children[0].words
            if node.children[0].kind == 'expansions' and words:
                name = words[0].value
                values = [_resolve(w, self.variables) for w in words[2:]]
                self.variables.pop(name, None); saved.pop(name, None)
                entry = dict(self.variables); results, bindings = [], [saved]
                # A bounded set of literal iteration values can be inspected;
                # unknown/glob values shadow prior bindings; state-matching globs
                # retain a candidate so downstream path inspection can detect it.
                for value in values[:64] or [Word('', expanded=True)]:
                    self.variables = dict(entry)
                    if not value.expanded and (value.quoted or not any(c in value.value for c in '*?[') or _mentions_state(value.value)):
                        self.variables[name] = value.value
                    results.append(self.visit(node.children[1], header)); bindings.append(dict(self.variables))
                if len(values) > 64: self.kinds.append('unparsed_script')
                self.variables = _common(bindings)
                return _merge(locations, header, *results)
            result = _merge(locations, header, self.visit(node.children[1], header))
            self.variables = _common([saved, self.variables])
            return result
        elif node.kind == 'sequence':
            for child in node.children: locations = self.visit(child, locations)
        return locations


def _script(text, cwd, script, interpreter, depth=0, previous=None, variables=None):
    if depth > 16: return ['unparsed_script']
    inspection = Inspection(script, interpreter, depth)
    inspection.variables = dict(variables or {})
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
