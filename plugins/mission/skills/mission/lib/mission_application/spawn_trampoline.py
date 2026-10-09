"""Fixed isolated entry point. Unsupported future job types fail closed."""
from __future__ import annotations

import os
from importlib.metadata import EntryPoint
from pathlib import Path
import re
import sys

# -I excludes the script directory and PYTHONPATH. Only the shipped lib is added.
if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from budgeted_exec import strict_json, write_frame
from budgeted_exec import JOB_LIMIT, read_job


def decode_job(raw):
    if not raw or len(raw) > JOB_LIMIT:
        raise ValueError('invalid job size')
    job = strict_json(raw)
    if isinstance(job, dict) and job.get('kind') == 'verification':
        if (set(job) != {'schema', 'kind', 'result_fd', 'contract', 'criterion', 'repro_input', 'deadline'}
                or job['schema'] != 'mission-exec-job/1' or type(job['result_fd']) is not int or job['result_fd'] < 3
                or not isinstance(job['criterion'], str) or not job['criterion']
                or type(job['deadline']) not in (int, float) or job['deadline'] <= 0
                or job['repro_input'] is not None and not isinstance(job['repro_input'], dict)):
            raise ValueError('invalid verification job')
        from acceptance_contract import frozen_verifier_commands
        commands = frozen_verifier_commands(job['contract'])
        criteria = [c for c in job['contract']['criteria'] if c['id'] == job['criterion']]
        if len(criteria) != 1:
            raise ValueError('invalid verification criterion')
        if job['repro_input'] is not None:
            replay = commands[criteria[0]['command_id']].get('replay')
            value = job['repro_input']
            if (not isinstance(replay, dict) or set(value) != {'artifact_kind', 'content'}
                    or not isinstance(value['artifact_kind'], str) or value['artifact_kind'] not in replay['allowed_artifact_kinds']
                    or not isinstance(value['content'], str) or len(value['content'].encode('utf-8')) > replay['max_bytes']):
                raise ValueError('invalid verification replay')
        return job
    if (not isinstance(job, dict) or set(job) != {'schema', 'kind', 'result_fd', 'verifier', 'request'}
            or job['schema'] != 'mission-exec-job/1' or job['kind'] != 'approval-verifier'
            or type(job['result_fd']) is not int or job['result_fd'] < 3):
        raise ValueError('invalid exec job')
    pin = job['verifier']
    patterns = {'entry_point': r'[a-z][a-z0-9-]{0,63}', 'distribution': r'[a-z0-9][a-z0-9._-]{0,127}',
                'module': r'[\w.]+',
                'source_digest': r'sha256:[0-9a-f]{64}', 'version': r'.{1,128}',
                'entry_point_value': EntryPoint.pattern.pattern}
    if (not isinstance(pin, dict) or set(pin) != set(patterns)
            or any(not isinstance(pin[key], str) or not re.fullmatch(pattern, pin[key]) for key, pattern in patterns.items())
            or not all(part.isidentifier() for part in pin['module'].split('.'))):
        raise ValueError('invalid verifier pin')
    request = job['request']
    if not isinstance(request, dict):
        raise ValueError('invalid approval request')
    if request.get('schema') == 'mission-force-approval-request/1':
        from scoring_provenance import validate_request
        validate_request(request)
    elif request.get('schema') == 'mission-provider-approval-request/1':
        fields = {'schema', 'preflight_id', 'session_id', 'mission_id', 'outbound_context_digest', 'invocation_id',
                  'outbound_packet_digest', 'registry_entry_digest', 'selection_id', 'selection_source',
                  'iteration', 'phase', 'risk_scopes', 'evidence_ref'}
        if (set(request) != fields or type(request['iteration']) is not int or request['iteration'] < 0
                or request['phase'] not in {'planning', 'execution', 'review', 'scoring', 'critic', 'synthesis'}
                or not isinstance(request['risk_scopes'], list)
                or any(not isinstance(x, str) or not x for x in request['risk_scopes'])
                or any(not isinstance(request[key], str) or not request[key] for key in fields - {'iteration', 'risk_scopes'})):
            raise ValueError('invalid provider approval request')
    else:
        raise ValueError('invalid approval request')
    return job


def main():
    if len(sys.argv) != 3:
        return 2
    try:
        job = decode_job(read_job(Path(sys.argv[1]), sys.argv[2]))
        from mission_application.approval_verifier import invoke_registered
        fd = job['result_fd']
        os.set_inheritable(fd, False)
        if job['kind'] == 'verification':
            from mission_application.verification_execution import run_contract_verifier
            result = run_contract_verifier({'acceptance_contract': job['contract']}, project_root=Path.cwd(),
                criterion_id=job['criterion'], repro_input=job['repro_input'], budget_deadline=job['deadline'])
        else:
            result = invoke_registered(job['verifier'], job['request'])
        if not isinstance(result, dict):
            raise ValueError('invalid approval result')
        write_frame(fd, {'ok': True, 'result': result}, **({'frame_limit': JOB_LIMIT} if job['kind'] == 'verification' else {}))
        os.close(fd)
        return 0
    except Exception:
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
