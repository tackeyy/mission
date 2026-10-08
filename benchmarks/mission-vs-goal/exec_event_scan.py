"""Conservative, non-executing shell inspection for evaluated Mission runs.

This is detection, not an integrity guarantee. Source files and alias expansion
are deliberately not followed. Unsupported syntax is a detection, never safe.
"""
from __future__ import annotations

import os
import re
import shlex
from pathlib import PurePosixPath

OUTPUTS = {
    ('manual-score-capture',): '--out', ('aggregate-reviews',): '--out',
    ('review-finalize',): '--out', ('context-manifest',): '--out',
    ('verification', 'claims'): '--out', ('artifact', 'export'): '--to',
    ('archive-worktree',): '--destination-root',
}
STATE_LITERAL = re.compile(r'(?<![\w.-])(?:[^\s\'";()]*\/)?\.mission-state(?:/|(?=$|[\s\'";()]))')
DYNAMIC = re.compile(r'[$`*?\[~]')
SHELLS = {'bash', 'sh'}


def _state_path(value, cwd):
    if DYNAMIC.search(value) or '__command_substitution__' in value:
        return True  # the resolved destination cannot be established
    if not value.startswith('/') and cwd is None:
        return True
    path = os.path.normpath(value if value.startswith('/') else os.path.join(cwd, value))
    return '.mission-state' in PurePosixPath(path).parts


def _command(argv, cwd, script_path, interpreter_path, depth):
    kinds = []
    if not argv:
        return kinds, cwd
    name = PurePosixPath(argv[0]).name
    if name in {'if', 'then', 'else', 'elif', 'fi', 'for', 'while', 'until', 'do', 'done', 'case', 'esac', 'select', '!'}:
        return ['unparsed_script'], None
    if name in {'source', '.', 'alias'}:
        return (['state_path_command'] if any(STATE_LITERAL.search(v) for v in argv[1:]) else []), cwd  # do not load source or expand aliases
    if DYNAMIC.search(argv[0]) or '__command_substitution__' in argv[0]:
        return ['unparsed_script'], None
    if name == 'cd':
        if len(argv) != 2 or DYNAMIC.search(argv[1]) or '__command_substitution__' in argv[1]:
            return kinds, None
        return kinds, os.path.normpath(argv[1]) if argv[1].startswith('/') else os.path.normpath(os.path.join(cwd, argv[1])) if cwd else None
    if name == 'eval':
        body = ' '.join(argv[1:])
        return (_script(body, cwd, script_path, interpreter_path, depth + 1) if body and not DYNAMIC.search(body) and '__command_substitution__' not in body else ['unparsed_script']), cwd
    if name in SHELLS:
        for index, value in enumerate(argv[1:], 1):
            if value.startswith('-') and 'c' in value[1:]:
                if index + 1 >= len(argv):
                    return ['unparsed_script'], cwd
                return _script(argv[index + 1], cwd, script_path, interpreter_path, depth + 1), cwd
    if any(arg in {'-c', '-e'} and i + 1 < len(argv) and re.search(r'[$`]', argv[i + 1]) for i, arg in enumerate(argv)):
        kinds.append('unparsed_script')
    if name == 'reactivate' or any(PurePosixPath(value).name == 'mission-state.py' and i + 1 < len(argv) and argv[i + 1] == 'reactivate' for i, value in enumerate(argv)):
        kinds.append('reactivate_command')
    offset = 1 if argv[0] == script_path else 2 if len(argv) > 1 and argv[:2] == [interpreter_path, script_path] else 0
    if not offset:
        if any(STATE_LITERAL.search(value) or (cwd is not None and '.mission-state' in PurePosixPath(cwd).parts and not value.startswith('-') and _state_path(value, cwd)) for value in argv[1:]):
            kinds.append('state_path_command')
        return kinds, cwd
    args = argv[offset:]
    # Only the frozen command/option pairs write an explicit output path.
    option = next((opt for command, opt in OUTPUTS.items() if tuple(args[:len(command)]) == command), None)
    if option:
        for index, arg in enumerate(args):
            if arg == '--':
                break
            key, equal, value = arg.partition('=')
            if len(key) >= 3 and key.startswith('--') and option.startswith(key):
                if not equal:
                    value = args[index + 1] if index + 1 < len(args) else ''
                if not value or value.startswith('--'):
                    kinds.append('unparsed_script')
                elif option == '--destination-root' or _state_path(value, cwd):
                    kinds.append('state_output_option')
    return kinds, cwd


def _script(script, cwd, script_path, interpreter_path, depth=0):
    if depth > 16 or not isinstance(script, str):
        return ['unparsed_script']
    kinds = []
    # Command substitutions execute independently, so their cwd never propagates.
    # Keep an unknown marker for the output: using it as a command or output path
    # cannot be resolved without executing the worker.
    parts, cursor, quote = [], 0, None
    while cursor < len(script):
        char = script[cursor]
        if char == '\\' and cursor + 1 < len(script):
            parts.append(script[cursor:cursor + 2]); cursor += 2; continue
        if char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
        if script.startswith('$(', cursor) and quote != "'":
            end, nesting, inner_quote = cursor + 2, 1, None
            while end < len(script) and nesting:
                current = script[end]
                if current == '\\':
                    end += 2; continue
                if current in {"'", '"'}:
                    inner_quote = None if inner_quote == current else current if inner_quote is None else inner_quote
                elif inner_quote is None:
                    nesting += (1 if current == '(' else -1 if current == ')' else 0)
                end += 1
            if nesting:
                return kinds + ['unparsed_script']
            kinds.extend(_script(script[cursor + 2:end - 1], None, script_path, interpreter_path, depth + 1))
            parts.append('__command_substitution__'); cursor = end; continue
        parts.append(char); cursor += 1
    script = ''.join(parts)
    # Extract here-doc bodies before tokenising: non-shell languages are never
    # interpreted as shell; all literal state paths in their payload are scanned.
    lines = script.splitlines(keepends=True)
    cleaned = []
    index = 0
    while index < len(lines):
        line = lines[index]
        docs = list(re.finditer(r'<<(-?)\s*(?:\'([^\']+)\'|"([^"]+)"|([\w]+))', line))
        cleaned.append(re.sub(r'<<-?\s*(?:\'[^\']+\'|"[^"]+"|[\w]+)', '', line))
        index += 1
        for doc in docs:
            delimiter = next(x for x in doc.groups()[1:] if x is not None)
            body = []
            while index < len(lines) and (lines[index].lstrip('\t') if doc.group(1) else lines[index]).rstrip('\r\n') != delimiter:
                body.append(lines[index]); index += 1
            if index == len(lines):
                return kinds + ['unparsed_script']
            index += 1
            try:
                header = shlex.split(line[:doc.start()])
            except ValueError:
                return kinds + ['unparsed_script']
            if header and PurePosixPath(header[0]).name in SHELLS:
                kinds.extend(_script(''.join(body), cwd, script_path, interpreter_path, depth + 1))
            elif STATE_LITERAL.search(''.join(body)):
                kinds.append('state_path_interpreter')
            elif re.search(r'[$`]', ''.join(body)):
                kinds.append('unparsed_script')
    try:
        lexer = shlex.shlex(''.join(cleaned), posix=True, punctuation_chars='();|&<>{}\n')
        lexer.whitespace = ' \t\r'
        lexer.whitespace_split = True
        tokens = []
        for token in lexer:
            if token and all(c in '();|&<>{}\n' for c in token):
                tokens.extend(re.findall(r'<<<|&&|\|\||>>|<<|&>|>&|.', token, re.DOTALL))
            else:
                tokens.append(token)
    except ValueError:
        return kinds + ['unparsed_script']
    argv, stack, redirects = [], [], []
    def flush():
        nonlocal argv, cwd, redirects
        trusted = bool(argv and (argv[0] == script_path or argv[:2] == [interpreter_path, script_path]))
        for operator, destination in redirects:
            if operator in {'<<', '<<<', '>&'}:
                kinds.append('unparsed_script')
            elif not (operator == '<' and trusted) and _state_path(destination, cwd):
                kinds.append('state_redirection')
        found, cwd = _command(argv, cwd, script_path, interpreter_path, depth)
        kinds.extend(found); argv = []; redirects = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in {';', '&&', '||', '|', '&'} or token.strip('\n') == '':
            flush()
            if token in {'||', '|', '&'}: cwd = None
        elif token in {'(', '{'}:
            flush(); stack.append(')' if token == '(' else '}'); cwd = None
        elif token in {')', '}'}:
            flush()
            if not stack or stack.pop() != token:
                kinds.append('unparsed_script')
            cwd = None
        elif token in {'>', '>>', '&>', '<', '<<', '<<<', '>&'}:
            # shlex may group operators. Unhandled redirections fail closed.
            if i + 1 >= len(tokens):
                kinds.append('unparsed_script'); break
            destination = tokens[i + 1]
            redirects.append((token, destination))
            i += 1
        elif token and all(char in '();|&<>{}' for char in token):
            kinds.append('unparsed_script')
        else:
            argv.append(token)
        i += 1
    flush()
    if stack:
        kinds.append('unparsed_script')
    return kinds


def scan_exec_events(events, mission_state_path, interpreter_path, cwd):
    """Return event indices and detection kinds; never run a worker command."""
    if not isinstance(events, list) or not all(isinstance(p, str) and p.startswith('/') for p in (mission_state_path, interpreter_path, cwd)):
        raise ValueError('exec_scan_identity_unavailable')
    detections = []
    for index, event in enumerate(events):
        command = event.get('command') if isinstance(event, dict) else None
        event_cwd = (event.get('cwd') if event.get('cwd') is not None else cwd) if isinstance(event, dict) else cwd
        event_cwd = event_cwd if isinstance(event_cwd, str) and event_cwd.startswith('/') else None
        if isinstance(command, list) and command and all(isinstance(v, str) for v in command):
            kinds, _ = _command(command, event_cwd, mission_state_path, interpreter_path, 0)
        else:
            kinds = _script(command, event_cwd, mission_state_path, interpreter_path)
        detections.extend({'event_index': index, 'kind': kind} for kind in sorted(set(kinds)))
    return detections
