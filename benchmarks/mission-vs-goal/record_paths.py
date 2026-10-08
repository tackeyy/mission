"""Portable path evidence at the probe's persistence boundary.

Live evidence retains filesystem paths for the state reader. Persisted evidence
uses one prefix map for identities, cwd, argv and shell bodies. A changed scan
or remaining private trace makes the assignment non-quality without losing it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from exec_event_scan import scan_exec_events
from native_goal_benchmark import _UNSAFE_TRACE, serialise_record, write_record

ALIAS_ROOT = '/__mission_paths__'
PRIVATE_ROOT = re.compile('(?:' + '|'.join(re.escape(marker.rstrip('/')) for marker in _UNSAFE_TRACE[1:]) + r')(?=$|[^\w./~-])')


def _unsafe(value):
    return any(marker in value for marker in _UNSAFE_TRACE) or PRIVATE_ROOT.search(value) is not None


def _walk(value, transform):
    if isinstance(value, str):
        return transform(value)
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            name = transform(key)
            if name in result:
                raise ValueError('path_key_collision')
            result[name] = _walk(item, transform)
        return result
    if isinstance(value, list):
        return [_walk(item, transform) for item in value]
    return value


def _normalise(record, roots):
    prefixes = {}
    for name in ('interpreter', 'workspace', 'package', 'home'):
        root = roots.get(name)
        if not isinstance(root, str) or not root.startswith('/') or root == '/':
            continue
        # A private memory directory must never become an innocuous alias.
        if any(marker in root + '/' for marker in _UNSAFE_TRACE[1:]):
            continue
        alias = ALIAS_ROOT + '/' + name
        if name == 'interpreter':
            alias += '/' + Path(root).name
        prefixes.setdefault(root.rstrip('/'), alias)
    if not prefixes:
        return _walk(record, lambda value: value)
    pattern = re.compile(r'(?<![\w./~-])(' + '|'.join(re.escape(p) for p in sorted(prefixes, key=len, reverse=True)) + r')(?=/|$|[^\w.~-])')
    def replace(value):
        if ALIAS_ROOT in value:
            raise ValueError('path_alias_collision')
        return pattern.sub(lambda match: prefixes[match[0]], value)
    return _walk(record, replace)


def _scan(record):
    return scan_exec_events(record['exec_events'], record['mission_state_path'], record['interpreter_path'], record.get('workspace', '/'))


def _redact(value):
    if isinstance(value, str):
        return '[redacted-path]' if _unsafe(value) else value
    if isinstance(value, dict):
        # Redacted keys are not evidence; omit them rather than collapse keys.
        return {key: _redact(item) for key, item in value.items() if _redact(key) == key}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def write_probe_record(path, record, roots):
    """Keep the assignment even when its execution trace cannot be published."""
    error = None
    saved = record
    try:
        saved = _normalise(record, roots)
        if _unsafe(json.dumps(saved, ensure_ascii=False)):
            raise ValueError('unsafe_trace')
        published = json.loads(serialise_record(saved))
        if (isinstance(record.get('exec_events'), list)
                and all(isinstance(record.get(key), str) for key in ('mission_state_path', 'interpreter_path'))):
            # Compare the full findings, including kind and event index. Prefix
            # aliases can change cd/.. resolution or shell tokenisation.
            try:
                before, after = _scan(record), _scan(published)
            except (KeyError, ValueError, TypeError):
                error = 'path_scan_unavailable'
            else:
                if before != after:
                    error = 'path_normalization_changed_scan'
                else:
                    saved['exec_scan'] = after
    except ValueError as exc:
        error = str(exc)
    if error:
        saved = _redact(saved)
        saved.update(record_persistence_error=error, classification='non_quality',
                     outcome='failed', fidelity='unverified',
                     reason='evaluated_session_unverifiable' if record.get('arm') == 'mission' else 'execution_config_mismatch',
                     exec_events=None, exec_scan=None)
    write_record(path, saved)
