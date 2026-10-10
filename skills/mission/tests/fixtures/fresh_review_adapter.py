"""Neutral subprocess observation fixture; no output-import authority."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

from fresh_review_runtime import CollectedReview
from mission_kernel.fresh_review import canonical_digest
from mission_kernel.json_codec import freeze_json_value


def _journal():
    return Path(os.environ['FIXTURE_REVIEW_JOURNAL'])


def _state():
    root = Path(os.environ['FIXTURE_REVIEW_STATE'])
    state = json.loads((root / 'sessions' / 'test.json').read_bytes())
    if state.get('schema') == 'mission-head/1':
        manifest = json.loads((root / state['state_generation']['path']).read_bytes())
        state = json.loads((root / manifest['state']['object']).read_bytes())
    return state


REVIEW_CHILD = """
import hashlib,json,sys
from pathlib import Path
raw = Path(sys.argv[1]).read_bytes()
packet = json.loads(raw)
request = json.loads(sys.stdin.read())
criterion = next(c for c in packet['criteria'] if c['id'] in request['criterion_ids'])
replay = packet['verifier_policy']['commands'][criterion['command_id']]['replay']
keys = ('request_id','nonce','mission_id','session_id','requirement_digest','contract_digest',
        'verifier_policy_digest','candidate_digest','input_digest','adapter_registration_digest','iteration')
output = dict(schema='mission-fresh-review-output/1', request_digest=sys.argv[2],
    **{key:request[key] for key in keys}, criterion_results=[dict(criterion_id=criterion['id'],
    status='searched', reason_code='none', findings=[dict(finding_id='zero-input',
    criterion_id=criterion['id'], requirement_ids=criterion['requirement_ids'],
    prohibited_side_effect_ids=[], severity='Low', summary='Zero input fails the registered replay.',
    command_id=replay['command_id'], repro_input=dict(artifact_kind='counterexample',content='0'),
    actual=dict(exit_code=1), expected=dict(criterion_id=criterion['id']), replay_evidence_ref=None)])],
    coverage=[dict(requirement_id=item['id'],classification_confirmed=True,
    criterion_ids=[criterion['id']],status='valid',reason_code='none',reason='Registered criterion covers the span.')
    for item in packet['requirements']])
output['criterion_results'].extend(dict(criterion_id=c['id'], status='searched', reason_code='none', findings=[])
    for c in packet['criteria'] if c['id'] in request['criterion_ids'] and c['id'] != criterion['id'])
for item in output['coverage']:
    item['criterion_ids'] = [c['id'] for c in packet['criteria']
        if c['id'] in request['criterion_ids'] and item['requirement_id'] in c['requirement_ids']]
print(json.dumps(dict(digest='sha256:'+hashlib.sha256(raw).hexdigest(),output=output)))
"""


COMPLETION_CHILD = """
import hashlib,json,sys
from pathlib import Path
raw = Path(sys.argv[1]).read_bytes()
packet = json.loads(raw)
request = json.loads(sys.stdin.read())
keys = ('request_id','nonce','mission_id','session_id','requirement_digest','contract_digest',
        'verifier_policy_digest','candidate_digest','input_digest','adapter_registration_digest','iteration')
selected = [c for c in packet['criteria'] if c['id'] in request['criterion_ids']]
opened = sys.argv[3] == 'completion-open'
output = dict(schema='mission-fresh-review-output/1', request_digest=sys.argv[2],
    **{key:request[key] for key in keys},
    criterion_results=[dict(criterion_id=c['id'],status='searched',reason_code='none',findings=[])
                       for c in selected],
    coverage=[dict(requirement_id=r['id'],classification_confirmed=True,
        criterion_ids=[c['id'] for c in selected if r['id'] in c['requirement_ids']],
        status='open' if opened else 'valid',reason_code='unsearched' if opened else 'none',
        reason='Fixture child checked the registered requirement span.') for r in packet['requirements']])
if sys.argv[3] == 'completion-failed':
    output = dict(diagnostic='Fixture child returned an invalid output.')
print(json.dumps(dict(digest='sha256:'+hashlib.sha256(raw).hexdigest(),output=output)))
"""


class Adapter:
    def observe_parent(self):
        if os.environ.get('FIXTURE_REVIEW_MODE') == 'watchdog-stop':
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
            Path(os.environ['FIXTURE_REVIEW_STOP_MARKER']).write_text(
                json.dumps([os.getpgrp(), os.getpid(), child.pid]))
            os.killpg(os.getpgrp(), signal.SIGSTOP)
            time.sleep(60)
        if os.environ.get('FIXTURE_REVIEW_MODE') == 'callback-timeout':
            subprocess.Popen([sys.executable, '-c',
                'import time,sys; from pathlib import Path; '
                'p=Path(sys.argv[1]); i=0\n'
                'while True: p.write_text(str(i)); i+=1; time.sleep(.01)',
                str(_journal().with_suffix('.heartbeat'))],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Block only after the descendant has proven it runs, so the caller's
            # timeout always has a live descendant to kill (interpreter start-up
            # can exceed a short timeout on a loaded CI runner).
            heartbeat = _journal().with_suffix('.heartbeat')
            while not heartbeat.exists():
                time.sleep(.01)
            while True:
                time.sleep(1)
        return freeze_json_value({'parent_identity': 'fixture-parent'})

    def launch(self, request_bytes, input_handle, snapshot_handles, dispatch):
        envelope = dispatch.thaw()
        request = json.loads(request_bytes)
        state_root = Path(os.environ['FIXTURE_REVIEW_STATE'])
        state = _state()
        record = next(item for item in state['fresh_review']['requests']
                      if item['status'] != 'withdrawn' and item['request']['request_id'] == request['request_id'])
        assert record['status'] == 'dispatch-unknown' and record['dispatch'] == envelope
        if os.environ.get('FIXTURE_REVIEW_MODE') == 'intent-crash':
            os._exit(7)
        if os.environ.get('FIXTURE_REVIEW_MODE') == 'stale-unavailable':
            (state_root.parent / 'app.txt').write_text('candidate changed')
        if os.environ.get('FIXTURE_REVIEW_MODE') in ('unavailable', 'stale-unavailable'):
            from mission_kernel.fresh_review import FreshReviewError
            raise FreshReviewError('fresh-review-launch-unavailable')
        if os.environ.get('FIXTURE_REVIEW_MODE') == 'in-flight':
            import time
            _journal().with_suffix('.launching').touch()
            deadline = time.monotonic() + 30
            while not _journal().with_suffix('.release').exists():
                if time.monotonic() >= deadline:
                    os._exit(8)
                time.sleep(.01)
            if _journal().with_suffix('.cancel').exists():
                from mission_kernel.fresh_review import FreshReviewError
                raise FreshReviewError('fresh-review-launch-unavailable')
        # Real child reads the immutable materialization, never an echoed digest.
        child = subprocess.run([sys.executable, '-c',
            'import hashlib,json,sys; from pathlib import Path; '
            'print(json.dumps({"digest":"sha256:"+hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest()}))',
            input_handle.relative_path], capture_output=True, text=True, check=True)
        if os.environ.get('FIXTURE_REVIEW_MODE') == 'counterexample':
            child = subprocess.run([sys.executable, '-c', REVIEW_CHILD, input_handle.relative_path,
                                    canonical_digest(request)], input=request_bytes.decode(),
                                   capture_output=True, text=True, check=True)
        mode = os.environ.get('FIXTURE_REVIEW_MODE', '')
        completion = mode.startswith('completion-')
        if completion:
            child = subprocess.run([sys.executable, '-c', COMPLETION_CHILD, input_handle.relative_path,
                                    canonical_digest(request), mode], input=request_bytes.decode(),
                                   capture_output=True, text=True, check=True)
        launch = dict(schema='mission-fresh-review-launch/1', request_id=request['request_id'],
            request_digest=canonical_digest(request), nonce=request['nonce'], operation_id=envelope['operation_id'],
            fencing_epoch=envelope['fencing_epoch'], adapter_registration_digest=request['adapter_registration_digest'],
            parent_identity='fixture-parent', child_identity='fixture-child:' + request['nonce'],
            context_identity='fixture-context:' + request['nonce'], context_mode='fresh',
            received_input_digest=json.loads(child.stdout)['digest'], started_at=datetime.fromisoformat(request['created_at'].replace('Z', '+00:00')).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            enforced_tools=request['allowed_tools'], enforced_budget={key: request[key] for key in (
                'wall_time_sec', 'max_tool_calls', 'max_replays', 'max_output_bytes', 'max_packet_bytes')})
        mode = os.environ.get('FIXTURE_REVIEW_MODE', '')
        if mode == 'binding-mismatch':
            launch['parent_identity'] = 'foreign-parent'
        if mode in ('inline', 'completion-inline'):
            launch.update(context_mode='inline', child_identity='fixture-parent', context_identity='fixture-parent')
        if mode == 'unobservable':
            launch.pop('child_identity')
        if mode == 'provider-invalid':
            launch['child_identity'] = 'file:opaque'
        output = b'{"diagnostic":"import is out of scope"}'
        if mode == 'counterexample' or completion:
            output = json.dumps(json.loads(child.stdout)['output'], sort_keys=True, separators=(',', ':')).encode()
        prior = json.loads(_journal().read_text()) if _journal().exists() else {'count': 0}
        _journal().write_text(json.dumps({'launch': launch, 'count': prior['count'] + 1,
                                        'output': output.decode(), **(dict(process_exited=True, exit_code=0,
            budget_used=dict(wall_time_sec=1,tool_calls=0,replays=0,output_bytes=len(output))) if mode == 'counterexample' or completion else {})}))
        if mode == 'crash':
            os._exit(7)
        return freeze_json_value(launch)

    def recover(self, dispatch):
        _journal().with_suffix('.recover').write_text('called')
        if os.environ.get('FIXTURE_REVIEW_MODE') == 'malformed-observation':
            from types import SimpleNamespace
            return CollectedReview(SimpleNamespace(thaw=lambda: None), None)
        if not _journal().exists():
            return CollectedReview(freeze_json_value({}), None)
        journal = json.loads(_journal().read_text())
        launch = journal['launch']
        observation = {'launch_receipt': launch, 'process_exited': journal.get('process_exited')}
        if not journal.get('minimal_observation'):
            observation.update({key: launch[key] for key in
                                ('operation_id', 'fencing_epoch', 'request_id', 'nonce', 'child_identity')})
        if journal.get('process_exited') is True:
            observation.update(exit_code=journal.get('exit_code'), budget_used=journal.get('budget_used'))
        observation.update(journal.get('observation_updates', {}))
        return CollectedReview(freeze_json_value(observation),
                               None if journal['output'] is None else journal['output'].encode())

    def collect(self, launch):
        return self.recover(launch)

    def cancel(self, dispatch):
        if dispatch.thaw().get('operation_id'):
            record = next(item for item in _state()['fresh_review']['requests']
                          if item.get('dispatch', {}).get('operation_id') == dispatch.thaw().get('operation_id'))
            _journal().with_suffix('.cancel').write_text(record['status'])
        return freeze_json_value({'status': os.environ.get('FIXTURE_CANCEL', 'cancelled')})


def factory():
    return Adapter()
