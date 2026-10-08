"""Exact Mission exclusions with conservative, non-executing word/cwd checks.

Tables cover bypasses and safe literals/fd operations at the public scanner;
expanded words and state-like data arguments deliberately permit false positives.
All cases use strings/argv and run without worker commands or providers.
"""
from pathlib import Path
import sys

import pytest

BENCH = Path(__file__).resolve().parents[3] / 'benchmarks/mission-vs-goal'
MS = '/package/skills/mission/bin/mission-state.py'
PY = '/usr/bin/python3'
SCRIPT = MS
PYTHON = PY

ORDINARY = [
    f'{MS} status 2>&1', 'pytest -q 2>&1 | tail', 'echo hi >&2',
    'echo hi 1>&2', 'cat <&-', 'echo hi >&-',
    f'for s in a b; do {MS} status --session $s; done',
    f'if true; then {MS} status; elif false; then {MS} get; else {MS} next; fi',
    f'while false; do {MS} status; done', f'until true; do {MS} status; done',
    f'case a in a|b) {MS} status;; *) {MS} next;; esac',
    f'{{ {MS} status; }}', f'( {MS} status )',
    f'x=$({MS} status); echo $x', f'X=1 {MS} status',
    'cat <<EOF\n$HOME\nEOF', 'bash <<EOF\necho $HOME\nEOF',
    "node -e 'console.log(`x`)'", 'node -e "console.log(`x`)"',
    "echo '$(cp x .mission-state/a)'", "echo '`cp x .mission-state/a`'",
    'find . -name "*.py" -exec wc -l {} +', 'ls {a,b}', 'echo ${HOME}',
    f'{MS} status \\\n  --session x', f'cd /tmp; cd -; {MS} context-manifest --out x',
    f'cd .mission-state; (cd ..; {MS} context-manifest --out x)',
    f'(cd .mission-state); {MS} context-manifest --out x',
    f'cd /tmp; echo hi || {MS} context-manifest --out x',
    f'cd /tmp; echo hi | {MS} context-manifest --out x',
    f'cd /tmp; echo hi & {MS} context-manifest --out x',
    'bash s.sh', 'python3 evil.py',
    "python3 -c \"open('.mission-'+'state/a','w')\"",
    f'/bin/echo {MS} reactivate',
    'if [ -f a ]; then cat a; fi',
    'cd .mission-state && cd .. && cp x a',
    'apply_patch <<EOF\n*** Begin Patch\nEOF',
    f'echo "${{X:-$({MS} status)}}"',
    f'({MS} status) 2>&1', f'{{ {MS} status; }} 2>&1',
    f'for x in do a; do {MS} status; done',
    f'cd /tmp && echo hi | {MS} context-manifest --out x',
    f'X="a b" {MS} status --input .mission-state/a',
    f'cd /tmp; cd /work; x=$(cd -; {MS} context-manifest --out x)',
    '(cd .mission-state; echo ok); cp x a',
    f'{{ cd .mission-state; echo ok; }}; {MS} status',
    f'x=$(case a in a) {MS} status;; esac); echo $x',
    'x=$(cat <<EOF\n)\nEOF\n); echo $x',
    'echo "$(date)"', 'echo "$(pwd)/x"',
    f'echo "$(case a in a) {MS} status;; esac)"',
    f'echo "$(if true; then {MS} status; fi)"',
    f'echo "$({{ {MS} status; }})"',
    'case a in a) cd .mission-state;; b) cp x a;; esac',
]
# General word/cwd rules intentionally detect these former data/control cases.
CONSERVATIVE = [
    f'x=$({MS} status); echo $x', 'bash <<EOF\necho $HOME\nEOF',
    'node -e "console.log(`x`)"', 'echo ${HOME}',
    f'cd .mission-state; (cd ..; {MS} context-manifest --out x)',
    f'(cd .mission-state); {MS} context-manifest --out x',
    f'/bin/echo {MS} reactivate', 'cd .mission-state && cd .. && cp x a',
    f'echo "${{X:-$({MS} status)}}"', '(cd .mission-state; echo ok); cp x a',
    f'{{ cd .mission-state; echo ok; }}; {MS} status',
    f'x=$(case a in a) {MS} status;; esac); echo $x',
    'x=$(cat <<EOF\n)\nEOF\n); echo $x', 'echo "$(date)"', 'echo "$(pwd)/x"',
    f'echo "$(case a in a) {MS} status;; esac)"',
    f'echo "$(if true; then {MS} status; fi)"', f'echo "$({{ {MS} status; }})"',
    'case a in a) cd .mission-state;; b) cp x a;; esac',
]

BAD = [
    'echo x | xargs -I{} cp {} .mission-state/a',
    'find . -exec cp {} .mission-state/ \\;',
    'cp x ${HOME}/.mission-state/a', 'cp {a,b} .mission-state/a',
    'cp x {.mission-state,other}/a', 'cp x .mission-state{,.bak}/a',
    *['cp x \\\n' + space + '.mission-state/a' for space in ('', ' ', '   ')],
    'echo ok \\\n && cp x .mission-state/a', 'x=$(cp y .mission-state/a)',
    f'x=$({MS} reactivate); echo $x', f'X=1 {MS} reactivate',
    f'if true; then {MS} reactivate; fi',
    f'for s in a; do {MS} context-manifest --out .mission-state/x; done',
    'while true; do cp x .mission-state/a; break; done',
    'until false; do cp x .mission-state/a; done',
    'case x in a) echo ok;; x) cp x .mission-state/a;; esac',
    '{ cd .mission-state; cp x a; }', '(cd .mission-state; cp x a)',
    'cd .mission-state || exit 1; cp x a',
    'cd .mission-state; echo hi | cp x a',
    'cd .mission-state; echo hi & cp x a',
    'cd .mission-state; (cp x a)',
    'cd .mission-state; { cp x a; }',
    'cd -- .mission-state; cp x a', 'cd -P .mission-state; cp x a',
    'cd -L .mission-state; cp x a', 'cd ~/.mission-state; cp x a',
    'cd "$PWD/.mission-state"; cp x a', 'cd ${PWD}/.mission-state; cp x a',
    'cd .mission-state; cd /tmp; cd -; cp x a',
    *['cp x ' + path + '/a' for path in ('.Mission-State', '.MISSION-STATE', '.mission-stat?', '.mission-*', '.m[ia]ssion-state', '.m*')],
    'D=.mission-state; cp x $D/a', 'cp x $D/a', 'echo hi > $D/a',
    f'cd $UNKNOWN; {MS} context-manifest --out x',
    'echo hi >&.mission-state/a', 'echo hi &>.mission-state/a',
    'echo hi >|.mission-state/a', 'echo `cp x .mission-state/a`',
    'echo "`cp x .mission-state/a`"',
    'bash <<EOF\ncp $X .mission-state/a\nEOF',
    'python3 - <<EOF\nopen(".mission-state/a", "w")\nEOF',
    "perl -e 'open(F,\">.mission-state/a\")'",
    'echo "${X:-$(cp x .mission-state/a)}"',
    f'({MS} status) 2>.mission-state/a',
    f'cd .mission-state && echo hi | {MS} context-manifest --out x',
    '/other/mission-state.py context-manifest --out=.mission-state/a',
    f'"X=1" {MS} status --input .mission-state/a',
    f'cd .mission-state; cd /tmp; x=$(cd -; {MS} context-manifest --out x)',
    f'x=$(case a in a) {MS} reactivate;; esac)',
    'case a in a) cd .mission-state ;& b) cp x a;; esac',
    'case a in a) cd .mission-state ;;& *) cp x a;; esac',
    'case a in a) echo x',
]


@pytest.fixture(autouse=True)
def module_path(monkeypatch):
    monkeypatch.syspath_prepend(str(BENCH))


@pytest.mark.parametrize('script,tampered', [(s, s in CONSERVATIVE) for s in ORDINARY] + [(s, True) for s in BAD])
def test_shell_structure_preserves_detection_boundary(script, tampered):
    from exec_event_scan import scan_exec_events
    assert bool(scan_exec_events([{'command': script}], MS, PY, '/work')) is tampered


@pytest.mark.parametrize('shell', ['bash', 'sh', 'zsh', 'dash', 'ksh', 'mksh', 'ash', 'fish'])
@pytest.mark.parametrize('script,tampered', [(f'{MS} status 2>&1', False),
    (f'{MS} reactivate', True), (f'{MS} context-manifest --out .mission-state/x', True),
    (f'{MS} status > .mission-state/x', True)])
def test_nested_shell_argv_is_scanned(shell, script, tampered):
    from exec_event_scan import scan_exec_events
    assert bool(scan_exec_events([{'command': ['/bin/' + shell, '-lc', script]}], MS, PY, '/work')) is tampered


@pytest.mark.parametrize('command', [[], ['node', '-e', 'console.log(`x`)']])
def test_argv_has_no_shell_expansion(command):
    from exec_event_scan import scan_exec_events
    assert scan_exec_events([{'command': command}], MS, PY, '/work') == []


EXEC_SAFE = [
    f'{SCRIPT} status', f'{PYTHON} {SCRIPT} status',
    f'{SCRIPT} status --input .mission-state/x',
    f'{SCRIPT} verification claims --out /tmp/result',
    f'{SCRIPT} manual-score-capture --out /tmp/result',
    f'{SCRIPT} aggregate-reviews --out /tmp/result',
    f'{SCRIPT} review-finalize --out /tmp/result',
    f'{SCRIPT} context-manifest --out /tmp/result',
    f'{SCRIPT} artifact export --to /tmp/result',
    f'{SCRIPT} status > /tmp/out', f'{SCRIPT} status 2>/tmp/error',
    f'{SCRIPT} status | tee /tmp/out',
    f'{SCRIPT} context-manifest -- --out .mission-state/x',

    f'(cd /tmp); {SCRIPT} context-manifest --out x',
    'echo normal', 'bash -lc "printf ok"', 'source ./other.sh', '. ./other.sh',
]

EXEC_BAD = [
    f'cd /work/.mission-state; {SCRIPT} context-manifest --out /tmp/out',
    'cp -r .mission-state /tmp/copy', 'rsync -a .mission-state/ /tmp/copy',
    'mv .mission-state/x /tmp/x', 'printf x > .mission-state/x',
    'echo x >> .mission-state/x', 'echo x 2>.mission-state/x',
    'echo x &>.mission-state/x', 'python3 -c "open(\'.mission-state/x\',\'w\')"',
    'python3 - <<EOF\nopen(".mission-state/x")\nEOF',
    'bash <<EOF\ncp .mission-state/x /tmp/x\nEOF',
    'sh <<\'EOF\'\ncp .mission-state/x /tmp/x\nEOF',
    'bash -c "sh -c \'cp .mission-state/x /tmp/x\'"',
    'eval "cp .mission-state/x /tmp/x"', 'eval "$COMMAND"',
    'f(){ cp .mission-state/x /tmp/x; }; f',
    'function f() { cp .mission-state/x /tmp/x; }; f',
    'bash -c "$SCRIPT"', '$COMMAND something', 'echo "unterminated',
    'mission-state.py status .mission-state/x', './mission-state.py status .mission-state/x',
    '/different/mission-state.py status .mission-state/x',
    f'/other/python3 {SCRIPT} status .mission-state/x',
    f'{SCRIPT} reactivate --approved-by-user',
    f'{PYTHON} {SCRIPT} reactivate',
    *[f'{SCRIPT} status {sep} cp .mission-state/x /tmp/x' for sep in (';', '&&', '||', '|', '\n')],
    f'({SCRIPT} status; cp .mission-state/x /tmp/x)',
    f'echo "$({SCRIPT} status; cp .mission-state/x /tmp/x)"',
    *[f'{SCRIPT} {cmd} {opt}' for cmd, opt in (
        ('manual-score-capture', '--out .mission-state/x'),
        ('aggregate-reviews', '--o .mission-state/x'),
        ('review-finalize', '--ou=.mission-state/x'),
        ('context-manifest', '--out=.mission-state/x'),
        ('verification claims', '--out .mission-state/x'),
        ('artifact export', '--t .mission-state/x'),
        ('archive-worktree', '--destination-root /tmp/out'))],
    f'{SCRIPT} status >.mission-state/x',
    f'cd /work/.mission-state; {SCRIPT} context-manifest --out x',
    f'cd "$DIR"; {SCRIPT} context-manifest --out x',
    f'{SCRIPT} context-manifest --out',
]


@pytest.mark.parametrize('script,tampered', [(s, False) for s in EXEC_SAFE] + [(s, True) for s in EXEC_BAD])
def test_exec_scan_contract(script, tampered):
    from exec_event_scan import scan_exec_events
    events = [{'command': ['bash', '-lc', script]}]
    assert bool(scan_exec_events(events, SCRIPT, PYTHON, '/work')) is tampered


@pytest.mark.parametrize('script,tampered', [
    (f'cd "$DIR"; cd /work; {SCRIPT} context-manifest --out /tmp/x', True),
    (f'cd /work/.mission-state; cp x /tmp/x', True),
    (f'cd /tmp || {SCRIPT} context-manifest --out x', True),
    ('source .mission-state/script.sh', True),
    (f'f() {{ {SCRIPT} status; }}; f', True),
])
def test_shell_scope_and_literal_paths(script, tampered):
    from exec_event_scan import scan_exec_events
    assert bool(scan_exec_events([{'command': ['bash', '-lc', script]}], SCRIPT, PYTHON, '/work/.mission-state')) is tampered


def test_event_cwd_controls_relative_output_resolution():
    from exec_event_scan import scan_exec_events
    event = {'command': [SCRIPT, 'context-manifest', '--out', 'x'], 'cwd': '/work/.mission-state'}
    assert scan_exec_events([event], SCRIPT, PYTHON, '/work')


@pytest.mark.parametrize('script,tampered', [
    (f'{SCRIPT} status < .mission-state/input', False),
    ('cat < .mission-state/input', True),
    ('python3 -c "$PROGRAM"', True),
    ('python3 - <<EOF\nx = []\nEOF', False),
])
def test_interpreter_payload_and_read_only_redirect(script, tampered):
    from exec_event_scan import scan_exec_events
    assert bool(scan_exec_events([{'command': ['bash', '-lc', script]}], SCRIPT, PYTHON, '/work')) is tampered


@pytest.mark.parametrize('script,kind', [('echo reactivate', None), (f'{SCRIPT} get reactivate', 'reactivate_command'), (f'{SCRIPT} status --input reactivate', 'reactivate_command')])
def test_reactivate_after_any_mission_word_is_detected(script, kind):
    from exec_event_scan import scan_exec_events
    found = scan_exec_events([{'command': ['bash', '-lc', script]}], SCRIPT, PYTHON, '/work')
    assert ({'event_index': 0, 'kind': kind} in found) if kind else found == []


def test_command_substitution_is_recursively_scanned():
    from exec_event_scan import scan_exec_events
    first = scan_exec_events([{'command': ['bash', '-lc', f'echo "$({SCRIPT} status --input .mission-state/x)"']}], SCRIPT, PYTHON, '/work')
    assert {'event_index': 0, 'kind': 'unparsed_script'} in first
    found = scan_exec_events([{'command': ['bash', '-lc', 'echo "$(cp .mission-state/x /tmp/x)"']}], SCRIPT, PYTHON, '/work')
    assert any(item['kind'] == 'state_path_command' for item in found)


@pytest.mark.parametrize('script', ['if true; then echo ok; fi', 'for name in x; do echo "$name"; done'])
def test_control_grammar_with_expansion_fails_closed(script):
    from exec_event_scan import scan_exec_events
    found = scan_exec_events([{'command': ['bash', '-lc', script]}], SCRIPT, PYTHON, '/work')
    assert {'event_index': 0, 'kind': 'unparsed_script'} in found if '$' in script else found == []


# Frozen exclusions are exact argv shapes; conservative data-path detections
# deliberately include echo/grep and quoted here-doc literals.
RULE_CASES = [
    *[(f'{wrapper} {MS} status --input .mission-state/x', 'state_path_command')
      for wrapper in ('env', 'command', 'timeout 5', 'nice -n 5', 'exec', 'xargs', 'uv run')],
    (f'X=1 {MS} status --input .mission-state/x', None),
    (f'{MS} "$READ_COMMAND" --input .mission-state/x', None),
    (f'{PY} {MS} status --input .mission-state/x', None),
    *[(f'{wrapper} {MS} reactivate', 'reactivate_command') for wrapper in
      ('nice -n 5', 'xargs', 'exec -a foo', 'timeout -s KILL 5', f'{PY} -X dev', f'{PY} -W ignore', 'uv run')],
    (f"env -S '{MS} reactivate'", 'reactivate_command'),
    (f'{MS} --session s reactivate', 'reactivate_command'),
    (f'{MS} reactivate', 'reactivate_command'),
    ('echo reactivate', None),
    ('echo .mission-state/x', 'state_path_command'),
    ('grep .mission-state README.md', 'state_path_command'),
    ("cat <<'EOF'\n.mission-state/x\nEOF", 'state_path_interpreter'),
    ('D=.mission-state; sed -i 1d $D/x.json', 'state_path_command'),
    (r"cat $'\x2emission-state/x'", 'state_path_command'),
    ('cat $".mission-state/x"', 'state_path_command'),
    ('cat --input=.Mission-State/x', 'state_path_command'),
    ('cat .mission-stat?/x', 'state_path_command'),
    *[(f'cd .mission-state; {command}', 'state_path_command') for command in
      ('sed -i 1d a', 'perl -i -pe 1 a', 'chmod 600 a', 'unlink a', 'tar cf a b', 'git checkout -- a', 'cat a', 'ed a', 'pwd')],
    (f'cd .mission-state; {MS} status 2>&1', 'state_path_command'),
    *[(f'cat <<EOF\n{body}\nEOF', 'reactivate_command') for body in
      (f'$({MS} reactivate)', f'`{MS} reactivate`', f"'$( {MS} reactivate )'")],
    (f"cat <<'EOF'\n$({MS} reactivate)\nEOF", None),
    (f'cat <<EOF\n\\$({MS} reactivate)\nEOF', None),
    ('cat <<EOF\n$HOME\nEOF', None),
    ('eval "echo $COMMAND"', 'unparsed_script'),
    ('bash -c "echo $COMMAND"', 'unparsed_script'),
    ("eval 'echo $COMMAND'", 'unparsed_script'),
    ("bash -c 'echo $COMMAND'", 'unparsed_script'),
    ("eval \"echo '\\$OUT'\"", None),
    ("bash -c \"echo '\\$OUT'\"", None),
    *[(f"bash {flags} '{MS} reactivate'", 'reactivate_command') for flags in
      ('-c --', '-c -x', '-c -e', '-c -o pipefail', '-c -O extglob', '-c +o pipefail', '-c +O extglob', '-c +e', '-lc --')],
    (f"fish --command '{MS} reactivate'", 'reactivate_command'),
    (f"fish --command='{MS} reactivate'", 'reactivate_command'),
    ('bash -c -o', 'unparsed_script'),
    ("printf '%s' harmless | sh", 'unparsed_script'),
    ('echo x | sh', 'unparsed_script'),
    ('printf hi | (sh)', 'unparsed_script'),
    ('printf hi | { sh; }', 'unparsed_script'),
    ('printf hi | env sh', 'unparsed_script'),
    ('printf hi | sh -x', 'unparsed_script'),
    ('printf hi | sh --', 'unparsed_script'),
    ('cat script.sh | sh', 'unparsed_script'),
    ('printf hi | tail', None),
    (f'f() {{ {MS} context-manifest --out x; }}; cd .mission-state; f', 'state_output_option'),
    (f'f() {{ cd .mission-state; }}; f; {MS} context-manifest --out x', 'state_output_option'),
    (f'f() {{ {MS} status --input .mission-state/a; }}; f', None),
    (f'if true; then f() {{ {MS} context-manifest --out x; }}; else f() {{ {MS} status; }}; fi; cd .mission-state; f', 'state_output_option'),
    (f'f() {{ cd /tmp; }}; f; {MS} context-manifest --out x', 'state_output_option'),
    (f'f() {{ {MS} context-manifest --out /tmp/x; }}; cd .mission-state; f', 'state_path_command'),
    (f"{MS} context-manifest --out '$OUT'", None),
    (f'{MS} context-manifest --out \\$OUT', None),
    ("echo hi > '$OUT'", None),
    ('echo hi > \\$OUT', None),
    (f'{MS} context-manifest --out "$OUT"', 'state_output_option'),
    ('echo hi > "$OUT"', 'state_redirection'),
    (f'{MS} status 2>&1', None),
    ('pytest -q 2>&1 | tail', None),
]


@pytest.mark.parametrize('script,kind', RULE_CASES)
def test_position_independent_rules_and_detection_kinds(script, kind):
    from exec_event_scan import scan_exec_events
    found = scan_exec_events([{'command': script}], MS, PY, '/work')
    if kind is None:
        assert found == []
    else:
        assert {'event_index': 0, 'kind': kind} in found


@pytest.mark.parametrize('value,expanded', [("'$OUT'", False), (r'\$OUT', False),
    ('"$OUT"', True), ('$OUT', True), ("'$OUT'$X", True), ('$"literal"', True)])
def test_lexer_retains_real_expansion_boundaries(value, expanded):
    from shell_syntax import lex_shell
    assert lex_shell(value)[0].expanded is expanded


@pytest.mark.parametrize('command,kind', [
    (['cat', 'a'], 'state_path_command'), (['pwd'], 'state_path_command'),
    ([MS, 'status', '--input', '.mission-state/a'], None),
    ([MS, 'context-manifest', '--out', '$OUT'], 'state_output_option'),
    (['bash', '-c', '--', MS + ' reactivate'], 'reactivate_command'),
    (['fish', '--command=' + MS + ' reactivate'], 'reactivate_command')])
def test_direct_argv_and_event_cwd_follow_same_rules(command, kind):
    from exec_event_scan import scan_exec_events
    found = scan_exec_events([{'command': command, 'cwd': '/work/.mission-state'}], MS, PY, '/work')
    if kind is None: assert found == []
    else: assert {'event_index': 0, 'kind': kind} in found


@pytest.mark.parametrize('command', [
    ['bash', '-c', MS + " reactivate '"],
    ['eval', MS + " reactivate '"],
    ['bash', '-c', '-o', MS + ' reactivate'],
])
def test_malformed_nested_script_keeps_known_reactivate_kind(command):
    from exec_event_scan import scan_exec_events
    found = scan_exec_events([{'command': command}], MS, PY, '/work')
    assert {'event_index': 0, 'kind': 'reactivate_command'} in found
    assert {'event_index': 0, 'kind': 'unparsed_script'} in found


@pytest.mark.parametrize('body,kind', [('cat <<EOF\n$HOME\nEOF', 'unparsed_script'),
    ("cat <<'EOF'\n$HOME\nEOF", None)])
def test_shell_command_string_includes_heredoc_expansion(body, kind):
    from exec_event_scan import scan_exec_events
    found = scan_exec_events([{'command': ['bash', '-c', body]}], MS, PY, '/work')
    assert ({'event_index': 0, 'kind': kind} in found) if kind else found == []
