"""Non-executing shell word lexer and structural parser for event inspection.

Words retain quoting and substitution boundaries; grammar consumes reserved
words only at command positions. Script files and alias expansion are not read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re


class ShellSyntaxError(ValueError):
    pass


@dataclass
class Word:
    value: str
    kind: str = 'word'
    quoted: bool = False
    start: int = 0
    end: int = 0
    substitutions: list = field(default_factory=list)
    body: str | None = None
    raw: str = ''
    expanded: bool = False
    body_expanded: bool = False
    # Offsets in the quote-removed value, with a simple variable name or None.
    expansions: list = field(default_factory=list)


@dataclass
class Node:
    kind: str
    children: list = field(default_factory=list)
    words: list = field(default_factory=list)
    redirects: list = field(default_factory=list)


OPERATORS = (';;&', '<<-', '<<<', '&>>', '&&', '||', ';;', ';&', '|&', '>>', '<<', '>&', '<&', '&>', '>|', ';', '|', '&', '<', '>', '(', ')', '\n')
REDIRECTS = {'<', '>', '>>', '>|', '&>', '&>>', '>&', '<&', '<<', '<<-', '<<<'}


def _balanced(text, start, opening='(', closing=')'):
    index, level, quote = start, 1, None
    while index < len(text):
        char = text[index]
        if quote != "'" and text.startswith('$(', index):
            _, index = _substitution(text, index + 2); continue
        if quote != "'" and text.startswith('${', index):
            _, index = _balanced(text, index + 2, '{', '}'); continue
        if char == '\\' and quote != "'":
            index += 2; continue
        if char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
        elif quote is None:
            level += (char == opening) - (char == closing)
            if not level:
                return text[start:index], index + 1
        index += 1
    raise ShellSyntaxError('unclosed_expansion')


def _backtick(text, start):
    end = start
    while end < len(text) and text[end] != '`':
        end += 2 if text[end] == '\\' else 1
    if end >= len(text): raise ShellSyntaxError('unclosed_backtick')
    return text[start:end], end + 1


def heredoc_substitutions(text):
    """Unquoted delimiters expand substitutions; body quote marks are data."""
    index, bodies = 0, []
    while index < len(text):
        if text[index] == '\\' and index + 1 < len(text) and text[index + 1] in '$`\\\n':
            index += 2; continue
        if text.startswith('$(', index):
            body, index = _substitution(text, index + 2); bodies.append(body)
        elif text.startswith('${', index):
            body, index = _balanced(text, index + 2, '{', '}')
            bodies.extend(heredoc_substitutions(body))
        elif text[index] == '`':
            body, index = _backtick(text, index + 1); bodies.append(body)
        else: index += 1
    return bodies


def _ansi_quote(text, start):
    """Decode shell ANSI-C literal words without evaluating an expansion."""
    index, parts = start, []
    escapes = dict(zip('abefnrtv\\\'"', '\a\b\x1b\f\n\r\t\v\\\'"'))
    while index < len(text) and text[index] != "'":
        if text[index] != '\\': parts.append(text[index]); index += 1; continue
        index += 1
        if index == len(text): break
        char = text[index]; index += 1
        if char in 'xuU01234567':
            base, limit = (16, {'x': 2, 'u': 4, 'U': 8}[char]) if char in 'xuU' else (8, 3)
            digits = '' if base == 16 else char
            while index < len(text) and len(digits) < limit and text[index] in ('0123456789abcdefABCDEF' if base == 16 else '01234567'):
                digits += text[index]; index += 1
            try: parts.append(chr(int(digits, base)))
            except ValueError as exc: raise ShellSyntaxError('invalid_ansi_escape') from exc
        elif char == 'c' and index < len(text):
            parts.append(chr(ord(text[index].upper()) & 31)); index += 1
        else: parts.append(escapes.get(char, '\\' + char))
    if index >= len(text): raise ShellSyntaxError('unclosed_ansi_quote')
    return ''.join(parts), index + 1


def _lex_shell(text, index=0):
    if not isinstance(text, str):
        raise ShellSyntaxError('script_type')
    last, pending = None, []
    while index < len(text):
        if text[index] in ' \t\r':
            index += 1; continue
        if text.startswith('\\\n', index):
            index += 2; continue
        if text[index] == '#':
            end = text.find('\n', index)
            index = len(text) if end < 0 else end
            continue
        operator = next((op for op in OPERATORS if text.startswith(op, index)), None)
        if operator and not text.startswith(('<(', '>('), index):
            last = Word(operator, kind='operator', start=index, end=index + len(operator))
            yield last
            index += len(operator)
            if operator == '\n':
                for delimiter, strip_tabs in pending:
                    body = []
                    while True:
                        end = text.find('\n', index)
                        end = len(text) if end < 0 else end + 1
                        line = text[index:end]
                        if not line:
                            raise ShellSyntaxError('unclosed_heredoc')
                        index = end
                        line = line.lstrip('\t') if strip_tabs else line
                        if line.rstrip('\r\n') == delimiter.value:
                            break
                        body.append(line)
                    delimiter.body = ''.join(body)
                    # Quotes in a here-doc body are data. Only its delimiter
                    # quoting and an odd escaping backslash suppress expansion.
                    delimiter.body_expanded = not delimiter.quoted and re.search(r'(?:^|[^\\])(?:\\\\)*[$`]', delimiter.body) is not None
                pending = []
            continue
        start, quote, quoted, parts, substitutions, expanded = index, None, False, [], [], False
        expansions = []
        def expansion(value, name=None):
            offset = sum(map(len, parts))
            expansions.append((offset, offset + len(value), name)); parts.append(value)
        while index < len(text):
            char = text[index]
            if quote is None and (char in ' \t\r\n' or (char in ';|&<>()' and not text.startswith(('<(', '>('), index))):
                break
            if quote is None and char == '~' and (not parts or re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*=', ''.join(parts))):
                prefix = re.match(r"~[^/\s;'\"|&<>()]*", text[index:])[0]
                expanded = True; expansion(prefix, 'HOME' if prefix == '~' else None)
                index += len(prefix); continue
            if quote is None and text.startswith("$'", index):
                literal, index = _ansi_quote(text, index + 2)
                quoted = True; parts.append(literal); continue
            if quote is None and text.startswith('$"', index):
                # Locale-translated quoting is an expansion, not a literal '$'.
                expanded = True; index += 1; continue
            if char in {"'", '"'} and (quote is None or quote == char):
                quoted = True; quote = None if quote else char
                index += 1; continue
            if char == '\\' and quote != "'":
                if index + 1 == len(text):
                    raise ShellSyntaxError('unclosed_escape')
                following = text[index + 1]
                if following == '\n':
                    index += 2; continue
                if quote == '"' and following not in '$`"\\':
                    parts.append(char); index += 1; continue
                quoted = True; parts.append(following); index += 2; continue
            if quote != "'" and text.startswith(('$(', '<(', '>('), index):
                body, index = _substitution(text, index + 2)
                expanded = True; substitutions.append(body); expansion('__command_substitution__')
                continue
            if quote != "'" and text.startswith('${', index):
                expanded = True
                body, index = _balanced(text, index + 2, '{', '}')
                substitutions.extend(body for token in lex_shell(body) for body in token.substitutions)
                expansion('${' + body + '}', body if re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', body) else None); continue
            if char == '`' and quote != "'":
                body, index = _backtick(text, index + 1)
                expanded = True; substitutions.append(body); expansion('__command_substitution__')
                continue
            if char == '$' and quote != "'" and index + 1 < len(text) and (text[index + 1].isalnum() or text[index + 1] in '_@*#?$!-'):
                expanded = True
                match = re.match(r'\$([A-Za-z_][A-Za-z_0-9]*|[0-9@*#?$!-])', text[index:])
                if match:
                    expansion(match[0], match[1]); index += len(match[0]); continue
            parts.append(char); index += 1
        if quote:
            raise ShellSyntaxError('unclosed_quote')
        word = Word(''.join(parts), quoted=quoted, start=start, end=index, substitutions=substitutions, raw=text[start:index], expanded=expanded, expansions=expansions)
        if last and last.value in {'<<', '<<-'}:
            pending.append((word, last.value == '<<-'))
        last = word
        yield word
    if pending:
        raise ShellSyntaxError('unclosed_heredoc')


def lex_shell(text):
    return list(_lex_shell(text))


class Tokens:
    """Lazy lookahead lets grammar delimit substitutions, including case/EOF."""
    def __init__(self, source):
        self.source, self.buffer = iter(source), []

    def get(self, index):
        while len(self.buffer) <= index:
            token = next(self.source, None)
            if token is None: return None
            self.buffer.append(token)
        return self.buffer[index]

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self.get(i) for i in range(index.start, index.stop)]
        return self.get(index)


class Parser:
    def __init__(self, tokens):
        self.tokens, self.index, self.depth = tokens, 0, 0

    def at(self, values):
        token = self.tokens.get(self.index)
        return token is not None and not token.quoted and token.value in values

    def require(self, value):
        if not self.at({value}):
            raise ShellSyntaxError('expected_' + value)
        self.index += 1

    def sequence(self, stops=()):
        children = []
        while self.tokens.get(self.index) is not None and not self.at(stops):
            if self.at({';', '\n'}):
                self.index += 1; continue
            node = self.pipeline()
            while self.at({'&&', '||'}):
                connector = self.tokens[self.index].value; self.index += 1
                while self.at({'\n'}): self.index += 1
                node = Node('and' if connector == '&&' else 'or', [node, self.pipeline()])
            background = self.at({'&'})
            if background:
                self.index += 1; node = Node('subshell', [node])
            children.append(node)
            if not background and self.tokens.get(self.index) is not None and not self.at(set(stops) | {';', '\n'}):
                raise ShellSyntaxError('missing_separator')
        return Node('sequence', children)

    def pipeline(self):
        children = [self.statement()]
        while self.at({'|', '|&'}):
            self.index += 1
            while self.at({'\n'}): self.index += 1
            children.append(self.statement())
        return children[0] if len(children) == 1 else Node('pipeline', children)

    def statement(self):
        self.depth += 1
        if self.depth > 64:
            raise ShellSyntaxError('nesting_limit')
        try:
            return self._statement()
        finally:
            self.depth -= 1

    def _statement(self):
        if self.tokens.get(self.index) is None:
            raise ShellSyntaxError('missing_command')
        if self.at({'!'}):
            self.index += 1; return self.statement()
        if self.at({'if'}):
            self.index += 1
            condition = self.sequence({'then'}); self.require('then')
            branches = [self.sequence({'elif', 'else', 'fi'})]
            while self.at({'elif'}):
                self.index += 1
                test = self.sequence({'then'}); self.require('then')
                branches.append(Node('sequence', [test, self.sequence({'elif', 'else', 'fi'})]))
            if self.at({'else'}):
                self.index += 1; branches.append(self.sequence({'fi'}))
            else:
                branches.append(Node('sequence'))
            self.require('fi')
            return self.suffix(Node('sequence', [condition, Node('choice', branches)]))
        if self.at({'for', 'select', 'while', 'until'}):
            name = self.tokens[self.index].value; self.index += 1
            if name in {'while', 'until'}:
                header = self.sequence({'do'})
            else:
                words = []
                while self.tokens.get(self.index) is not None and not self.at({';', '\n'}):
                    words.append(self.tokens[self.index]); self.index += 1
                while self.at({';', '\n'}): self.index += 1
                header = Node('expansions', words=words)
            self.require('do'); body = self.sequence({'done'}); self.require('done')
            return self.suffix(Node('loop', [header, body]))
        if self.at({'case'}):
            self.index += 1; words, branches = [], []
            while self.tokens.get(self.index) is not None and not self.at({'in'}):
                words.append(self.tokens[self.index]); self.index += 1
            self.require('in')
            while True:
                while self.at({'\n'}): self.index += 1
                if self.at({'esac'}): break
                pattern = []
                while self.tokens.get(self.index) is not None and not self.at({')'}):
                    pattern.append(self.tokens[self.index]); self.index += 1
                self.require(')')
                body = Node('sequence', [Node('expansions', words=pattern), self.sequence({';;', ';&', ';;&', 'esac'})])
                if self.tokens.get(self.index) is None:
                    raise ShellSyntaxError('unclosed_case')
                ending = self.tokens[self.index].value
                branches.append(Node(ending, [body]))
                if self.at({';;', ';&', ';;&'}): self.index += 1
            self.require('esac')
            return self.suffix(Node('sequence', [Node('expansions', words=words), Node('case', branches)]))
        following = self.tokens.get(self.index + 1)
        if self.at({'function'}) or (self.tokens[self.index].kind == 'word' and following is not None and following.value == '(' and self.tokens.get(self.index + 2) is not None and self.tokens[self.index + 2].value == ')'):
            name = self.tokens[self.index + 1] if self.at({'function'}) else self.tokens[self.index]
            self.index += 2 if self.at({'function'}) else 1
            if self.at({'('}): self.require('('); self.require(')')
            return Node('function', [self.statement()], words=[name])
        if self.at({'(', '{'}):
            opening = self.tokens[self.index].value; self.index += 1
            closing = ')' if opening == '(' else '}'
            body = self.sequence({closing}); self.require(closing)
            return self.suffix(Node('subshell' if opening == '(' else 'sequence', [body]))
        if self.at({'then', 'elif', 'else', 'fi', 'do', 'done', 'esac', ')', '}'}):
            raise ShellSyntaxError('unexpected_reserved_word')
        return self.simple()

    def redirect(self):
        operator = self.tokens[self.index].value; self.index += 1
        if self.tokens.get(self.index) is None or self.tokens[self.index].kind != 'word':
            raise ShellSyntaxError('missing_redirect_value')
        word = self.tokens[self.index]; self.index += 1
        return operator, word

    def suffix(self, node):
        while self.at(REDIRECTS) or self.fd():
            if self.fd(): self.index += 1
            node.redirects.append(self.redirect())
        return node

    def fd(self):
        token = self.tokens.get(self.index)
        if token is None or token.kind != 'word' or token.quoted or not token.value.isdigit():
            return False
        following = self.tokens.get(self.index + 1)
        return following is not None and following.kind == 'operator' and following.value in REDIRECTS and token.end == following.start

    def simple(self):
        node = Node('command')
        while self.tokens.get(self.index) is not None:
            token = self.tokens[self.index]
            if token.kind == 'operator' and token.value not in REDIRECTS: break
            if self.fd():
                self.index += 1; continue
            if token.kind == 'operator': node.redirects.append(self.redirect())
            else: node.words.append(token); self.index += 1
        if not node.words and not node.redirects:
            raise ShellSyntaxError('empty_command')
        return node


def parse_shell(text):
    parser = Parser(Tokens(_lex_shell(text)))
    return parser.sequence()


def _substitution(text, start):
    parser = Parser(Tokens(_lex_shell(text, start)))
    body = parser.sequence({')'})
    closing = parser.tokens.get(parser.index)
    parser.require(')')
    return body, closing.end
