"""I2a per-record configuration checks, independent of quality scoring.

Policy is supplied data only. No policy initialisation or enforcement happens
here; the runner can connect the pre-turn hook after budget support lands.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from exec_event_scan import scan_exec_events


def policy_digest(policy):
    if not isinstance(policy, dict):
        raise ValueError('policy_unavailable')
    fixed = {k: v for k, v in policy.items() if k != 'external_deadline_at'}
    return 'sha256:' + hashlib.sha256(json.dumps(fixed, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _time(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('timezone_required')
    return result


def read_evaluated_state(workspace, thread_id):
    """Read the exact evaluated head through verified lineage, without mtime filtering."""
    if not isinstance(thread_id, str) or not thread_id or '/' in thread_id or '\\' in thread_id:
        raise ValueError('thread_identity_unavailable')
    lib = str(Path(__file__).resolve().parents[2] / 'skills/mission/lib')
    sys.path.insert(0, lib)
    try:
        from mission_persistence.authoritative_reader import read_authoritative_snapshot
    finally:
        sys.path.remove(lib)
    session_id = f'cx-{thread_id}'
    path = Path(workspace) / '.mission-state/sessions' / f'{session_id}.json'
    state = read_authoritative_snapshot(path, expected_session_id=session_id).document_copy()
    # A newly initialised session has no persisted history yet. The kernel uses
    # absence as the empty history; expose it explicitly to evaluation consumers.
    state.setdefault('reactivation_history', [])
    return state


def collect_post_run(workspace, evidence, post_run=None):
    """Always called after app-server shutdown, including EOF and deadline exits."""
    if post_run is not None:
        try:
            evidence.update(post_run(workspace, evidence))
        except Exception as exc:
            evidence.update(mission_state=None, post_run_error=type(exc).__name__)
    elif evidence.get('thread_id'):
        try:
            evidence['mission_state'] = read_evaluated_state(workspace, evidence['thread_id'])
        except Exception as exc:
            evidence.update(mission_state=None, post_run_error=type(exc).__name__)
    try:
        evidence['exec_scan'] = scan_exec_events(evidence['exec_events'], evidence['mission_state_path'], evidence['interpreter_path'], str(workspace))
    except Exception as exc:
        evidence.update(exec_scan=None, exec_scan_error=type(exc).__name__)


def _check_record(record, planned_arms, expected_arm=None):
    """Identify the package edition and fail closed on any configuration mismatch.

    A passing check is eligibility only, never a quality success. I1 owns the
    all-assignment classification and accounting of eligible candidates.
    """
    reasons, unobserved = [], []
    manifest = record.get('manifest') or {}
    candidates = [name for name, spec in planned_arms.items()
                  if record.get('arm') == ('codex_native_goal' if name == 'native_goal' else 'mission')
                  and manifest.get('mission_source_commit') == spec.get('mission_source_commit')
                  and (manifest.get('package') or {}).get('sha256') == spec.get('package_sha256')]
    arm = candidates[0] if len(candidates) == 1 else None
    if arm is None or (expected_arm is not None and arm != expected_arm):
        return dict(matches=False, planned_arm=arm, classification='non_quality', reasons=['execution_config_mismatch'])
    spec = planned_arms[arm]
    if record.get('record_persistence_error'):
        return dict(matches=False, planned_arm=arm, classification='non_quality', reasons=[
            'execution_config_mismatch' if arm == 'native_goal' else 'evaluated_session_unverifiable'])
    mission = arm != 'native_goal'
    verified = arm == 'mission_verified_complex'
    conditions = manifest.get('conditions') or {}
    required = ('host', 'arm', 'model_id', 'effort', 'permissions', 'timeout_seconds', 'max_turns', 'token_budget', 'max_budget_usd')
    observed = record.get('observed_config') or {}
    if (record.get('arm') != ('mission' if mission else 'codex_native_goal')
            or any(k not in conditions or k not in spec['conditions'] or conditions[k] != spec['conditions'][k] for k in required)
            or record.get('package_delivery', 'missing') != ('skill_input' if mission else None)
            or record.get('config_matches') is not True
            or not observed or observed.get('model') != conditions.get('model_id')
            or observed.get('reasoningEffort') != conditions.get('effort')
            or (observed.get('activePermissionProfile') or {}).get('id') != conditions.get('permissions')
            or not spec.get('provider_version') or manifest.get('provider_version') != spec['provider_version']
            or record.get('provider_version_after') != manifest.get('provider_version')
            or (manifest.get('task_snapshot') or {}).get('matches') is not True
            or not spec.get('initial_sha256') or (manifest.get('worker_export') or {}).get('initial_sha256') != spec['initial_sha256']
            or record.get('identity_conflicts')):
        reasons.append('execution_config_mismatch')
    if mission:
        if isinstance(record.get('exec_events'), list):
            try:
                scan = scan_exec_events(record['exec_events'], record.get('mission_state_path'), record.get('interpreter_path'), record.get('workspace', '/'))
                if scan:
                    reasons.append('evaluated_session_tampered')
            except Exception:
                if verified: reasons.append('evaluated_session_unverifiable')
        elif verified:
            reasons.append('evaluated_session_unverifiable')
        else:
            unobserved.append('exec_event_stream')
        if verified and (record.get('exec_scan') is None or record.get('exec_scan_error')):
            reasons.append('evaluated_session_unverifiable')
        state = record.get('mission_state')
        if verified:
            init = record.get('session_init')
            policy = record.get('budget_policy')
            if not isinstance(state, dict) or not isinstance(init, dict):
                reasons.append('evaluated_session_unverifiable')
            try:
                expected_deadline = _time(record['run_started_at']) + timedelta(seconds=conditions['timeout_seconds'] - spec['m_post'])
                digest = spec['budget_policy_template_sha256']
                valid = (init['budget_policy_template_sha256'] == digest
                         and policy['reactivate'] == 'forbidden' and policy_digest(policy) == digest
                         and _time(policy['external_deadline_at']) == expected_deadline)
                for snapshot in (init, state):
                    p = snapshot['budget_policy']
                    valid = valid and (snapshot['session_id'] == f"cx-{record['thread_id']}"
                                       and bool(snapshot['mission_id']) and snapshot['mission_id'] == init['mission_id']
                                       and p['reactivate'] == 'forbidden' and policy_digest(p) == digest
                                       and _time(p['external_deadline_at']) == expected_deadline)
                valid = valid and state['reactivation_history'] == []
                if not valid: reasons.append('evaluated_session_mismatch')
            except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
                reasons.append('evaluated_session_mismatch')
        elif state is None:
            unobserved.append('reactivation_history')
        elif state.get('reactivation_history') != []:
            reasons.append('evaluated_session_mismatch')
        if not verified and record.get('budget_policy') is not None:
            reasons.append('execution_config_mismatch')
    elif record.get('budget_policy') is not None:
        reasons.append('execution_config_mismatch')
    return dict(matches=not reasons, planned_arm=arm, classification='non_quality' if reasons else None, reasons=sorted(set(reasons)), unobserved=unobserved)


def check_record(record, planned_arms, expected_arm=None):
    """Malformed evidence is a non-quality record, never an aggregation crash."""
    try:
        return _check_record(record, planned_arms, expected_arm)
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return dict(matches=False, planned_arm=None, classification='non_quality', reasons=['execution_config_mismatch'])
