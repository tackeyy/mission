"""Offline selection and audit of the frozen evaluation preregistration.

Acquisition/proof providers are trusted adapters: history must include every
path-touching main commit and every merged PR page. Timestamp verification must
bind the supplied digest to a verified block header, returning its integer time;
beacon verification must bind chain/key/round to a cryptographically verified
signature, returning randomness bytes. No boolean 'verified' flags are accepted.
The caller loads this generator from commit A's pinned generator revision.
No transport, subprocess, or worker execution is performed by this module.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
import re
import posixpath
from urllib.parse import urlsplit

import public_benchmark as public

ATTEMPT_PATH = 'docs/preregistration/884/attempts'
ARMS = ('native_goal', 'mission_baseline', 'mission_verified_complex')
FILES = {'attempt.json': 'A', 'pool-manifest.json': 'B', 'selection.json': 'C',
         'confirmation.json': 'D', 'withdrawal.json': 'W', 'preregistration.md': 'P'}
SHA = re.compile(r'[0-9a-f]{40}')


def canonical(value):
    try: return _canonical(value)
    except RecursionError as exc: raise ValueError('canonical_schema_invalid') from exc


def _canonical(value):
    """RFC 8785 for our integer-only data schema; reject floats and surrogates.

    Numbers outside the exact IEEE-754 integer domain are rejected rather than
    serialized with different cross-runtime values. Keys use UTF-16 ordering.
    """
    if value is None or type(value) is bool: return json.dumps(value).encode()
    if type(value) is int and abs(value) <= 2**53 - 1: return str(value).encode()
    if type(value) is str:
        value.encode('utf-16-be')  # rejects unpaired surrogate code points
        return json.dumps(value, ensure_ascii=False).encode()
    if type(value) is list: return b'[' + b','.join(_canonical(v) for v in value) + b']'
    if type(value) is dict and all(type(k) is str for k in value):
        keys = sorted(value, key=lambda k: k.encode('utf-16-be'))
        return b'{' + b','.join(_canonical(k) + b':' + _canonical(value[k]) for k in keys) + b'}'
    raise ValueError('canonical_schema_invalid')


def digest(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def snapshot_digest(snapshot):
    # Data-only canonical task definitions are packed as one fixed USTAR member.
    return public._normalized_digest({'tasks.json': (canonical(snapshot), 0o644)})


def key(seed, purpose, *ids):
    parts = [seed] + [v.encode('utf-8') for v in (purpose, *ids)]
    return hashlib.sha256(b''.join(len(v).to_bytes(4, 'big') + v for v in parts)).digest()


def assignment_id(stage, unit, task, arm, group, replicate):
    values = ('mission-884-assignment', stage, unit, task, arm, group, str(replicate))
    return hashlib.sha256(b''.join(len(v.encode()).to_bytes(4, 'big') + v.encode() for v in values)).hexdigest()


def ranked(seed, purpose, ids):
    return sorted(ids, key=lambda value: (key(seed, purpose, value), value.encode()))


def validate_arms(arms):
    if (type(arms) not in (list, tuple) or not arms or any(type(a) is not str for a in arms)
            or len(set(arms)) != len(arms) or not set(arms) <= set(ARMS)):
        raise ValueError('arms_invalid')


def assignments(seed, stage, primary, units, control, arms, repeats):
    validate_arms(arms)
    rows = []
    for group, selected in (('worker', units), ('control', control)):
        for unit in selected:
            for arm in sorted(arms):
                for replicate in range(repeats):
                    task = primary[unit]
                    rows.append({'assignment_id': assignment_id(stage, unit, task, arm, group, replicate),
                                 'stage': stage, 'unit_id': unit, 'task_id': task, 'arm': arm,
                                 'fixture_group': group, 'replicate': replicate})
    ids = [row['assignment_id'] for row in rows]
    if len(set(ids)) != len(ids): raise ValueError('assignment_id_duplicate')
    return sorted(rows, key=lambda row: (key(seed, 'order', row['assignment_id']), row['assignment_id'].encode()))


def select(seed, manifest, arms):
    units = {}
    for task in manifest['tasks']:
        if task['accepted']: units.setdefault(task['unit_id'], []).append(task['task_id'])
    if len(units) < 12: raise ValueError('pilot_pool_insufficient')
    primary = {unit: min(tasks, key=lambda task: (key(seed, 'primary', unit, task), task.encode()))
               for unit, tasks in units.items()}
    pilot = ranked(seed, 'pilot', units)[:12]
    ranking = ranked(seed, 'unit', set(units) - set(pilot))
    return {'pilot': pilot, 'ranking': ranking, 'primary': primary, 'arms': sorted(arms),
            'assignments': assignments(seed, 'pilot', primary, pilot, [], arms, 2)}


def confirm(seed, selection, commit_c, k, arms):
    if type(k) is not int or not 0 < k <= len(selection['ranking']): raise ValueError('K_invalid')
    chosen = selection['ranking'][:k]
    control = ranked(seed, 'control', chosen)[:(k + 1) // 2]
    return {'K': k, 'arms': sorted(arms), 'commit_c': commit_c, 'control': control,
            'assignments': assignments(seed, 'confirmatory', selection['primary'], chosen, control, arms, 1)}


def repository(value):
    parsed = urlsplit(value.lower())
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.query or parsed.fragment:
        raise ValueError('repository_invalid')
    parts = parsed.path.strip('/').removesuffix('.git').split('/')
    if len(parts) != 2 or not all(parts): raise ValueError('repository_invalid')
    return parsed.hostname + '/' + '/'.join(parts)


def lines(text):
    return [' '.join(line.split()) for line in text.splitlines()]


def source(change):
    path = PurePosixPath(change['path'])
    return (not set(path.parts[:-1]) & {'test', 'tests', 'spec'}
            and not re.search(r'(^test_|_test\.|\.test\.|\.spec\.)', path.name))


def case_values(result, checks):
    cases = result['cases']
    if (result['reason'] not in (None, 'contract_mismatch') or result['status'] not in ('passed', 'failed')
            or type(result['case_count']) is not int or result['case_count'] != len(checks) or len(cases) != len(checks)
            or {v['name'] for v in cases} != {v['name'] for v in checks}
            or any(type(v['passed']) is not bool for v in cases)):
        raise ValueError('det_cases_invalid')
    passed = all(v['passed'] for v in cases)
    if result['status'] != ('passed' if passed else 'failed') or result['reason'] != (None if passed else 'contract_mismatch'):
        raise ValueError('det_cases_invalid')
    return {v['name']: v['passed'] for v in cases}


def deterministic(task, observation):
    try:
        checks = task['checks']
        if not checks or len({c['name'] for c in checks}) != len(checks): return False
        if any(c['kind'] not in ('fail_to_pass', 'pass_to_pass') for c in checks): return False
        if not re.fullmatch(r'.+@sha256:[0-9a-f]{64}', task['environment']['image']): return False
        if not task['environment']['command']: return False
        values = {}
        for variant in ('starter', 'reference'):
            runs = observation[variant]
            if len(runs) != 3: return False
            outcomes = [case_values(r, checks) for r in runs]
            if not all(v == outcomes[0] for v in outcomes): return False
            values[variant] = outcomes[0]
        return (all(values['reference'].values())
                and any(not values['starter'][c['name']] for c in checks if c['kind'] == 'fail_to_pass')
                and all(values['starter'][c['name']] for c in checks if c['kind'] == 'pass_to_pass'))
    except (KeyError, TypeError, ValueError):
        return False


def criteria(task, config, scope, observation):
    scopes = list(scope['mission'].values()) + [text for files in scope['packages'].values() for text in files.values()]
    required = {'copy', 'execute'} | ({'store'} if config['store_contents'] else set())
    licenses = [task['benchmark_license'], task['upstream_license']]
    lic = all(required <= set(config['licenses'].get(name, [])) for name in licenses)
    repo = repository(task['repository']).split('/', 1)[1]
    matching = False
    for change in task['changes']:
        if not source(change): continue
        normalized = lines('\n'.join(change['added']))
        for i in range(len(normalized) - config['con_lines'] + 1):
            sequence = normalized[i:i + config['con_lines']]
            if any(len(line) < config['con_length'] for line in sequence): continue
            for text in scopes:
                existing = lines(text)
                if any(existing[j:j + len(sequence)] == sequence for j in range(len(existing))): matching = True
    absent = not any(task['task_id'].casefold() in text.casefold() or repo.casefold() in text.casefold() for text in scopes)
    con = not matching and absent
    files = {posixpath.normpath(c['path']) for c in task['changes'] if source(c) and (c['added'] or c['deleted'])}
    changed = sum(len(c['added']) + len(c['deleted']) for c in task['changes'] if source(c))
    checks = task['checks']
    cx_values = {'source_files': len(files), 'changed_lines': changed, 'checks': len(checks),
                 'natural_request': task['natural_request'],
                 'fail_to_pass': sum(c['kind'] == 'fail_to_pass' for c in checks)}
    cx = (len(files) >= config['cx_files'] and changed >= config['cx_lines']
          and len(checks) >= config['cx_checks'] and cx_values['fail_to_pass'] >= 1
          and task['natural_request'] is True)
    return [('Lic', lic, licenses), ('Con', con, {'identifier_absent': absent, 'source_match': matching}),
            ('Cx', cx, cx_values), ('Det', deterministic(task, observation), observation)]


def lineage(tasks, percent):
    repos = {t['task_id']: repository(t['repository']) for t in tasks}
    groups = {repo: repo for repo in repos.values()}
    def root(repo):
        while repo != groups[repo]: repo = groups[repo]
        return repo
    fingerprints = {t['task_id']: {digest('\n'.join(lines(text)).encode())
                    for text in t['base_files'].values() if len(lines(text)) >= 4} for t in tasks}
    pairs = []
    for i, one in enumerate(tasks):
        if not one['roots'] or not all(SHA.fullmatch(r) for r in one['roots']): raise ValueError('lineage_roots_missing')
        for two in tasks[i + 1:]:
            a, b = one['task_id'], two['task_id']
            smaller = min(len(fingerprints[a]), len(fingerprints[b]))
            shared = len(fingerprints[a] & fingerprints[b])
            flags = {'G1': repos[a] == repos[b], 'G2': bool(set(one['roots']) & set(two['roots'])),
                     'G3': smaller > 0 and shared * 100 >= percent * smaller}
            pairs.append({'tasks': [a, b], **flags, 'shared_files': shared, 'smaller_files': smaller})
            if any(flags.values()):
                low, high = sorted((root(repos[a]), root(repos[b])))
                groups[high] = low
    return {task: root(repo) for task, repo in repos.items()}, pairs


def generate_pool(snapshot, config, scope, observations, *, commit_a):
    if snapshot_digest(snapshot) != config['snapshot_digest']: raise ValueError('snapshot_digest_mismatch')
    if digest(canonical(scope)) != config['scope_digest']: raise ValueError('scope_digest_mismatch')
    thresholds = ('cx_files', 'cx_checks', 'cx_lines', 'con_length', 'con_lines', 'g3_percent')
    if any(type(config[v]) is not int or config[v] <= 0 for v in thresholds) or config['g3_percent'] > 100:
        raise ValueError('threshold_invalid')
    ids = [t['task_id'] for t in snapshot]
    if len(set(ids)) != len(ids): raise ValueError('task_id_duplicate')
    if any(type(v) is not str or not v for v in ids): raise ValueError('task_id_invalid')
    rows = []
    for position, task in enumerate(snapshot):
        row = {'task_id': task['task_id'], 'snapshot_position': position, 'accepted': True,
               'reason': None, 'criteria': {}, 'unit_id': None, 'det': None,
               'benchmark': task['benchmark'], 'revision': task['revision'],
               'repository': repository(task['repository']), 'base_commit': task['base_commit'],
               'task_digest': digest(canonical(task))}
        observation = observations.get(task['task_id'])
        for name, accepted, values in criteria(task, config, scope, observation):
            if name == 'Det' and observation is not None:
                try: canonical(observation)
                except ValueError:
                    accepted = False; values = observation = {'invalid_schema_digest': digest(json.dumps(observation, sort_keys=True, separators=(',', ':')).encode())}
            evidence = {'task': digest(canonical(task)), 'config': digest(canonical({k: config[k] for k in
                            ('licenses', 'store_contents', *thresholds, 'snapshot_digest', 'scope_digest')})),
                        'scope': config['scope_digest'], 'values': values}
            row['criteria'][name] = {'accepted': accepted, 'values': values, 'evidence_digest': digest(canonical(evidence))}
            if name == 'Det':
                if observation is None: raise ValueError('det_observations_missing')
                row['det'] = observation
            if not accepted:
                row.update(accepted=False, reason=name)
                break
        rows.append(row)
    included = [t for t, row in zip(snapshot, rows) if row['accepted']]
    units, pairs = lineage(included, config['g3_percent'])
    for row in rows:
        if row['accepted']: row['unit_id'] = units[row['task_id']]
    return canonical({'schema': 'mission-pool-manifest/1', 'commit_a': commit_a,
                      'snapshot_digest': config['snapshot_digest'], 'tasks': rows,
                      'lineage': pairs, 'lineage_digest': digest(canonical(pairs))})




def acquire_history(provider):
    """Thin provider adapter; transport/authentication live outside this module."""
    head = provider.main_head()
    history = provider.main_history(ATTEMPT_PATH)
    items, cursor, visited, total = [], None, set(), None
    while True:
        if cursor in visited: raise ValueError('pagination_cycle')
        visited.add(cursor)
        page = provider.merged_pr_page(ATTEMPT_PATH, cursor)
        if type(page['total']) is not int or page['total'] < 0: raise ValueError('pagination_total_invalid')
        if total is not None and page['total'] != total: raise ValueError('pagination_total_changed')
        total = page['total']
        items.extend(page['items'])
        cursor = page['next']
        if cursor is None: break
        if len(items) >= total: raise ValueError('pagination_overflow')
    if len(items) != total: raise ValueError('pagination_incomplete')
    if head != provider.main_head(): raise ValueError('main_moved_during_acquisition')
    return history | {'prs': items, 'total_prs': total}


def enumerate_attempts(history, proofs):
    """Verify page totals, PR/main bijection, reachability and immutable bytes.

    Invalid/unconfirmed time proofs remain UNKNOWN (None), so V2/V5 cannot
    accept them. Structural history defects invalidate the complete cohort.
    """
    prs, commits = history['prs'], history['commits']
    if (type(history['total_prs']) is not int or any(type(v) is not bool for v in history['protection'].values())
            or len(prs) != history['total_prs'] or history['protection'] != {'force_push': False, 'deletion': False}
            or len({p['sha'] for p in prs}) != len(prs) or len({c['sha'] for c in commits}) != len(commits)
            or {p['sha'] for p in prs} != {c['sha'] for c in commits}):
        raise ValueError('history_incomplete')
    commit_files = {c['sha']: c['files'] for c in commits}
    attempts, seen = {}, {}
    for pr in sorted(prs, key=lambda p: (p['merged_at'], p['sha'])):
        if (pr['base'] != 'main' or pr['sha'] not in history['reachable']
                or commit_files[pr['sha']] != pr['files'] or not SHA.fullmatch(pr['sha'])):
            raise ValueError('history_route_invalid')
        for path, raw in pr['files'].items():
            match = re.fullmatch(re.escape(ATTEMPT_PATH) + r'/([0-9]{4})/([^/]+)', path)
            if not match or match[2] not in FILES or path in seen: raise ValueError('history_path_invalid')
            number, stage = int(match[1]), FILES[match[2]]
            attempt = attempts.setdefault(number, {'number': number, 'stages': {}})
            if stage in attempt['stages']: raise ValueError('stage_duplicate')
            try:
                time = proofs.verify_timestamp(digest(raw), pr['proofs'][path])
                if type(time) is not int or time < 0: time = None
            except (KeyError, TypeError, ValueError):
                time = None
            attempt['stages'][stage] = {'sha': pr['sha'], 'raw': raw, 'merged_at': pr['merged_at'], 'time': time}
            seen[path] = raw
    if seen != history['current_files']: raise ValueError('history_files_incomplete')
    for attempt in attempts.values():
        attempt['invalid_reason'] = None
        try:
            a = json.loads(attempt['stages']['A']['raw'], object_pairs_hook=public._unique_object)
            if type(a) is dict: attempt['a'] = a  # retain readable round even when V1 fails
            if type(a) is not dict or not {'number', 'round', 'margin', 'chain', 'snapshot_digest', 'scope_digest', 'generator_sha',
                    'prior_attempts_digest', 'licenses', 'store_contents', 'cx_files', 'cx_checks', 'cx_lines',
                    'con_length', 'con_lines', 'g3_percent', 'det_mode', 'packages', 'pilot_arms', 'confirmatory_arms'} <= a.keys() or type(a['number']) is not int or a['number'] != attempt['number']:
                raise ValueError('V1_invalid_A')
            if type(a['chain']) is not dict or not {'hash', 'public_key', 'genesis', 'period'} <= a['chain'].keys(): raise ValueError('V1_invalid_A')
            validate_arms(a['pilot_arms']); validate_arms(a['confirmatory_arms'])
            if attempt_round(attempt) is None: raise ValueError('V1_invalid_A')
        except (KeyError, TypeError, ValueError):
            attempt['invalid_reason'] = 'V1_invalid_A'; continue
        if not {'A', 'P', 'B'} <= attempt['stages'].keys():
            attempt['invalid_reason'] = 'V1_missing_stage'; continue
        attempt['schedule'] = proofs.chain_schedule(a['chain']['hash'])
        cutoff(attempt)
    ordered = sorted(attempts.values(), key=lambda a: (min(s['merged_at'] for s in a['stages'].values()), a['number']))
    if [a['number'] for a in ordered] != sorted(attempts): raise ValueError('attempt_order_invalid')
    return ordered


def prior_digest(attempts, *, before=None):
    rows = [{'number': a['number'], 'stages': {stage: {'sha': s['sha'], 'digest': digest(s['raw'])}
             for stage, s in a['stages'].items() if before is None or s['merged_at'] < before}}
            for a in attempts]
    return digest(canonical(rows))


def cutoff(attempt):
    a = attempt['a']; chain = attempt['schedule']
    if any(type(a['chain'][k]) is not int or a['chain'][k] != chain[k] for k in ('genesis', 'period')):
        raise ValueError('beacon_schedule_mismatch')
    if (any(type(v) is not int for v in (a['round'], a['margin'], chain['genesis'], chain['period']))
            or min(a['round'], a['margin'], chain['period']) <= 0): raise ValueError('beacon_schedule_invalid')
    return chain['genesis'] + (a['round'] - 1) * chain['period'] - a['margin']


def before(stage, limit):
    return (type(stage['merged_at']) is int and type(stage['time']) is int
            and 0 <= stage['merged_at'] < limit and 0 <= stage['time'] < limit)


def withdrawn(attempt):
    stage = attempt['stages'].get('W')
    return not attempt.get('invalid_reason') and stage is not None and before(stage, cutoff(attempt))


def pool_identity(manifest):
    return sorted((r['benchmark'], r['revision'], r['task_id'], r['repository'], r['base_commit'])
                  for r in manifest['tasks'] if r['accepted'])


class UnknownAttempt(ValueError): pass


def attempt_round(attempt):
    value = attempt.get('a', {}).get('round')
    return value if type(value) is int and value > 0 else None


def canonical_attempt(attempts, materials):
    rounds = [attempt_round(a) for a in attempts if attempt_round(a) is not None]
    if any(later <= earlier for earlier, later in zip(rounds, rounds[1:])):
        raise ValueError('no_canonical_attempt: attempt_chain_invalid')
    valid = [a for a in attempts if not a.get('invalid_reason')]
    for i, a in enumerate(valid):
        for p in valid[:i]:
            limit = max(p['stages']['W']['merged_at'], p['stages']['W']['time']) if withdrawn(p) else cutoff(p)
            times = [a['stages']['A'][k] for k in ('merged_at', 'time')]
            if all(type(t) is int for t in times) and min(times) <= limit:
                raise ValueError('no_canonical_attempt: attempt_chain_invalid')
    for index, attempt in enumerate(attempts):
        try:
            if attempt_round(attempt) is None: raise UnknownAttempt('attempt_round_unknown')
            if attempt.get('invalid_reason'): continue
            a, stages = attempt['a'], attempt['stages']
            previous = [p for p in attempts[:index] if not p.get('invalid_reason')]
            if withdrawn(attempt): continue
            if not all(before(stages[s], cutoff(attempt)) for s in ('A', 'P', 'B')): continue
            if stages['P']['sha'] != stages['A']['sha']: continue
            if stages['P']['raw'].splitlines().count(b'attempt_digest: ' + digest(stages['A']['raw']).encode()) != 1: continue
            if stages['A']['merged_at'] >= stages['B']['merged_at']: continue
            if a['prior_attempts_digest'] != prior_digest(attempts[:index], before=stages['A']['merged_at']): continue
            if any(a['snapshot_digest'] == p['a']['snapshot_digest'] for p in previous): continue
            if attempt['number'] not in materials: raise UnknownAttempt('attempt_materials_unknown')
            material = materials[attempt['number']]
            if type(material) is not dict or any(material.get(k) is None for k in ('snapshot', 'scope', 'generator_sha')): raise UnknownAttempt('attempt_materials_unknown')
            if a['generator_sha'] != material['generator_sha'] or not SHA.fullmatch(a['generator_sha']): continue
            manifest = json.loads(stages['B']['raw'], object_pairs_hook=public._unique_object)
            observations = {r['task_id']: r['det'] for r in manifest['tasks'] if r['det'] is not None}
            regenerated = generate_pool(material['snapshot'], a, material['scope'], observations, commit_a=stages['A']['sha'])
            if regenerated != stages['B']['raw']: continue
            reused = False
            for previous_attempt in previous:
                try:
                    old_pool = json.loads(previous_attempt['stages']['B']['raw'])
                    if pool_identity(old_pool) == pool_identity(manifest): reused = True
                except (KeyError, TypeError, ValueError):
                    pass
            if reused: continue
            return attempt
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            if isinstance(exc, UnknownAttempt): raise
            continue
    raise ValueError('no_canonical_attempt')
