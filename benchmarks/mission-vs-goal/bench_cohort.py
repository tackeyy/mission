"""Offline cohort evidence and I2c replay; depends only on selection core."""
import json
import re

import bench_selection as selection_core
import public_benchmark as public


def collect_det(snapshot, config, scope, number, replay):
    """Capture six I2c evaluations per task passing Lic, Con and Cx.

    This adapter has evaluation side effects; generate_pool itself is pure.
    The caller records these observations in B before requesting any seed.
    """
    if isinstance(replay, BundleReplay): replay.bind_snapshot(number, snapshot, config['snapshot_digest'])
    observations = {}
    for task in snapshot:
        early = selection_core.criteria(task, config, scope, None)[:3]
        if all(accepted for _, accepted, _ in early):
            observations[task['task_id']] = {variant: [replay(number, task['task_id'], variant) for _ in range(3)]
                                            for variant in ('starter', 'reference')}
    return observations


def audit_tasks(seed, manifest, selected):
    result = set(selected)
    for accepted in (True, False):
        ids = [t['task_id'] for t in manifest['tasks'] if all(t['criteria'].get(c, {}).get('accepted') is True for c in ('Lic', 'Con', 'Cx')) and t['accepted'] is accepted]
        result.update(selection_core.ranked(seed, 'det-audit', ids)[:59])
    return sorted(result, key=lambda v: v.encode())


class BundleReplay:
    """Only evaluation route: jobs bind a frozen I2c bundle and candidate tree.

    Jobs for reference contain the reference-applied tree prepared by the
    benchmark adapter. A real call can run containers; invoke only with approval.
    """
    def __init__(self, jobs):
        self.jobs = jobs
        self.snapshots = {}

    def bind_snapshot(self, number, snapshot, expected_digest):
        if selection_core.snapshot_digest(snapshot) != expected_digest: raise ValueError('det_snapshot_mismatch')
        frozen = json.loads(selection_core.canonical(snapshot))
        tasks = {task['task_id']: task for task in frozen}
        if len(tasks) != len(frozen): raise ValueError('det_snapshot_duplicate_task')
        self.snapshots[number] = tasks

    def __call__(self, number, task_id, variant):
        bundle, assignment, candidate = self.jobs[number, task_id, variant]
        if assignment['task_id'] != task_id: raise ValueError('det_job_task_mismatch')
        task = self.snapshots[number][task_id]
        binding = task['det_binding']
        if (type(binding['bundle_digest']) is not str or not public.DIGEST.fullmatch(binding['bundle_digest'])
                or type(binding[variant]) is not str or not public.TREE_DIGEST.fullmatch(binding[variant])):
            raise ValueError('det_binding_invalid')
        if bundle.digest != binding['bundle_digest']: raise ValueError('det_bundle_mismatch')
        actual = bundle.task(task_id)
        revision = next((b['revision'] for b in json.loads(bundle.manifest_bytes)['benchmarks']
                         if b['name'] == task['benchmark']), None)
        if (any(actual[key] != task[key] for key in ('task_id', 'benchmark', 'base_commit', 'environment', 'checks'))
                or revision != task['revision']
                or selection_core.repository(actual['upstream']) != selection_core.repository(task['repository'])):
            raise ValueError('det_task_mismatch')
        envelope = public.freeze_candidate(bundle, assignment, candidate)
        if envelope['candidate_digest'] != binding[variant]: raise ValueError('det_candidate_mismatch')
        return public.evaluate_assignment(bundle, assignment, assignment['worker_export'], candidate, envelope)


def declared_lineage(raw):
    """Read one exact declaration; malformed/duplicate declarations cannot vanish."""
    lines = [line for line in raw.decode('utf-8').splitlines() if re.match(r'\s*lineage_digest\s*:', line)]
    if not lines: return None
    match = re.fullmatch(r'lineage_digest: (sha256:[0-9a-f]{64})', lines[0])
    if len(lines) != 1 or not match: raise ValueError('lineage_declaration_invalid')
    return match[1]


def lineage_expectation(attempt, material, proofs):
    """Recompute from available observations and require declarations to agree."""
    a, stages = attempt['a'], attempt['stages']
    declarations = [declared_lineage(stages['P']['raw'])]
    if 'preregistration' in material:
        try:
            document = material['preregistration']
            raw = document['raw']
            if type(raw) is not bytes: raise ValueError('document_bytes_invalid')
            time = proofs.verify_timestamp(selection_core.digest(raw), document['proof'])
            if (not selection_core.before({'time': time, 'merged_at': document['merged_at']}, selection_core.cutoff(attempt))
                    or raw.splitlines().count(b'attempt_digest: ' + selection_core.digest(stages['A']['raw']).encode()) != 1):
                raise ValueError('document_binding_invalid')
        except (KeyError, TypeError, ValueError):
            raise ValueError('preregistration_evidence_invalid') from None
        declarations.append(declared_lineage(raw))
    declared = {value for value in declarations if value is not None}
    if len(declared) > 1: raise ValueError('lineage_digest_mismatch')
    declared_digest = next(iter(declared), None)
    if declared_digest is not None and 'observations' not in material:
        # Design 884 section 3.2.1 check 2 requires the document digest to match B.
        # Without observations, retain that authenticated comparison; it cannot
        # establish independent recalculation. A declaration never bypasses
        # observations when they are supplied, even by a post-B document.
        return declared_digest
    included = [task for task in material['snapshot'] if all(accepted for _, accepted, _ in
                selection_core.criteria(task, a, material['scope'], material['observations'].get(task['task_id'])))]
    _, independent_pairs = selection_core.lineage(included, a['g3_percent'])
    recomputed = selection_core.digest(selection_core.canonical(independent_pairs))
    if declared_digest is not None and declared_digest != recomputed:
        raise ValueError('lineage_digest_mismatch')
    return recomputed


def validate_det_rows(manifest, tasks):
    """Check the whole Det pool's cheap schema, including unsampled rejections."""
    for row in manifest['tasks']:
        if not all(row['criteria'].get(c, {}).get('accepted') is True for c in ('Lic', 'Con', 'Cx')): continue
        det = row.get('det')
        if type(det) is not dict or not {'starter', 'reference'} <= det.keys():
            raise ValueError('det_observation_schema_invalid')
        for variant in ('starter', 'reference'):
            runs = det[variant]
            if type(runs) is not list or len(runs) != 3: raise ValueError('det_observation_count_invalid')
            try:
                for observation in runs: selection_core.case_values(observation, tasks[row['task_id']]['checks'])
            except (KeyError, TypeError, ValueError):
                raise ValueError('det_observation_schema_invalid') from None


def verify_cohort(history, materials, used_number, beacon, records, proofs, replay):
    """Return evidence or invalid_cohort; unexpected replay errors are distinguished.

    Known adapter ValueError codes remain evidence failures. Other Exception
    values return det_replay_exception and only their type, so callers can
    investigate evaluator/provider bugs without publishing private diagnostics.
    Process-control BaseException subclasses propagate to the caller.
    """
    result = {'status': 'invalid_cohort', 'canonical_attempt': None, 'checks': {'1': False, '2': False, '3': False}}
    try:
        if type(used_number) is not int: raise ValueError('attempt_number_invalid')
        attempts = selection_core.enumerate_attempts(history, proofs)
        attempt = selection_core.canonical_attempt(attempts, materials)
        result['canonical_attempt'] = attempt['number']
        if used_number != attempt['number']: raise ValueError('noncanonical_attempt_used')
        a, stages = attempt['a'], attempt['stages']
        packages = a['packages']
        if type(packages) is not dict or not set(a['pilot_arms'] + a['confirmatory_arms']) <= packages.keys():
            raise ValueError('package_invalid')
        for package in packages.values():
            if (type(package) is not dict or type(package.get('sha')) is not str
                    or not selection_core.SHA.fullmatch(package['sha'])
                    or type(package.get('digest')) is not str or not public.DIGEST.fullmatch(package['digest'])):
                raise ValueError('package_invalid')
        seed = proofs.verify_beacon(a['chain'], a['round'], beacon)
        if type(seed) is not bytes or len(seed) != 32 or seed.hex() != beacon['randomness']:
            raise ValueError('beacon_randomness_mismatch')
        manifest = json.loads(stages['B']['raw'])
        selection = json.loads(stages['C']['raw'])
        if not selection_core.before(stages['C'] | {'time': stages['C']['merged_at']}, stages['D']['merged_at']): raise ValueError('selection_timestamp_invalid')
        if stages['C']['merged_at'] < selection_core.cutoff(attempt) + a['margin']: raise ValueError('selection_before_beacon')
        if selection_core.canonical(selection_core.select(seed, manifest, a['pilot_arms'])) != stages['C']['raw']:
            raise ValueError('selection_mismatch')
        d = json.loads(stages['D']['raw'])
        minimum = materials[used_number]['minimum_k']
        if type(minimum) is not int or minimum < 1 or d['K'] < minimum: raise ValueError('K_below_power_plan')
        confirmation = selection_core.confirm(seed, selection, stages['C']['sha'], d['K'], a['confirmatory_arms'])
        if selection_core.canonical(confirmation) != stages['D']['raw']: raise ValueError('confirmation_mismatch')
        primary = [selection['primary'][u] for u in selection['ranking'][:d['K']]]
        tasks = {t['task_id']: t for t in materials[used_number]['snapshot']}
        validate_det_rows(manifest, tasks)
        result['checks']['1'] = True
        result['lineage_digest'] = manifest['lineage_digest']
        material = materials[used_number]
        expected_lineage = lineage_expectation(attempt, material, proofs)
        result['checks']['2'] = (expected_lineage == manifest['lineage_digest']
                                and ('lineage_digest' not in material or material['lineage_digest'] == expected_lineage))
        if not result['checks']['2']: raise ValueError('lineage_digest_mismatch')
        pilot_units = set(selection['pilot']); confirm_units = set(selection['ranking'][:d['K']])
        confirmation_records = [r for r in records if r['unit_id'] in confirm_units]
        if not confirmation_records: raise ValueError('confirmation_records_missing')
        if any(type(r['started_at']) is not int or r['started_at'] < 0 for r in records):
            raise ValueError('worker_time_invalid')
        first = min(r['started_at'] for r in confirmation_records)
        if not selection_core.before(stages['D'], first): raise ValueError('confirmation_timestamp_invalid')
        for r in records:
            if r['unit_id'] not in pilot_units | confirm_units: raise ValueError('unselected_worker_run')
            if r['task_id'] != selection['primary'][r['unit_id']]: raise ValueError('unselected_task_run')
            arms = d['arms'] if r['unit_id'] in confirm_units else selection['arms']
            if r['arm'] not in arms: raise ValueError('unplanned_arm_run')
            if r['started_at'] <= stages['C']['merged_at']: raise ValueError('worker_before_selection')
            if r['package'] != a['packages'][r['arm']]: raise ValueError('package_mismatch')
        chosen = primary + [selection['primary'][u] for u in selection['pilot']]
        mode = a['det_mode']
        if mode not in ('all', 'audit'): raise ValueError('det_mode_invalid')
        selected = (audit_tasks(seed, manifest, chosen) if mode == 'audit'
                    else [t['task_id'] for t in materials[used_number]['snapshot']
                          if all(v for _, v, _ in selection_core.criteria(t, a, materials[used_number]['scope'], None)[:3])])
        rows = {r['task_id']: r for r in manifest['tasks']}
        if isinstance(replay, BundleReplay):
            replay.bind_snapshot(used_number, materials[used_number]['snapshot'], a['snapshot_digest'])
        for task_id in selected:
            for variant in ('starter', 'reference'):
                runs = rows[task_id]['det'][variant]
                try:
                    evaluation = replay(used_number, task_id, variant)
                except Exception as exc:
                    if isinstance(exc, ValueError) and str(exc) in (
                            'det_job_task_mismatch', 'det_binding_invalid', 'det_bundle_mismatch',
                            'det_task_mismatch', 'det_candidate_mismatch'):
                        raise
                    return result | {'reason': 'det_replay_exception', 'replay_error_type': type(exc).__name__}
                observed = selection_core.case_values(evaluation, tasks[task_id]['checks'])
                if any(observed != selection_core.case_values(r, tasks[task_id]['checks']) for r in runs):
                    raise ValueError('det_replay_mismatch')
        return result | {'status': 'valid', 'checks': {'1': True, '2': True, '3': True},
                         'reason': None, 'det_replayed': selected,
                         'evidence_digest': selection_core.digest(selection_core.canonical({'attempt': used_number, 'manifest': selection_core.digest(stages['B']['raw']),
                             'selection': selection_core.digest(stages['C']['raw']), 'confirmation': selection_core.digest(stages['D']['raw'])}))}
    except (KeyError, TypeError, ValueError, UnicodeError, OverflowError, OSError, RecursionError) as exc:
        return result | {'reason': str(exc)}
