"""Neutral subprocess observation fixture; no output-import authority."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from fresh_review_runtime import CollectedReview
from mission_kernel.fresh_review import canonical_digest
from mission_kernel.json_codec import freeze_json_value


def _journal():
    return Path(os.environ['FIXTURE_REVIEW_JOURNAL'])


class Adapter:
    def observe_parent(self):
        return freeze_json_value({'parent_identity': 'fixture-parent'})

    def launch(self, request_bytes, input_handle, snapshot_handles, dispatch):
        envelope = dispatch.thaw()
        request = json.loads(request_bytes)
        state_root = Path(os.environ['FIXTURE_REVIEW_STATE'])
        state = json.loads((state_root / 'sessions' / 'test.json').read_bytes())
        if state.get('schema') == 'mission-head/1':
            manifest = json.loads((state_root / state['state_generation']['path']).read_bytes())
            state = json.loads((state_root / manifest['state']['object']).read_bytes())
        record = next(item for item in state['fresh_review']['requests'] if item['request']['request_id'] == request['request_id'])
        assert record['status'] == 'dispatch-unknown' and record['dispatch'] == envelope
        if os.environ.get('FIXTURE_REVIEW_MODE') == 'intent-crash':
            os._exit(7)
        if os.environ.get('FIXTURE_REVIEW_MODE') == 'unavailable':
            from mission_kernel.fresh_review import FreshReviewError
            raise FreshReviewError('fresh-review-launch-unavailable')
        # Real child reads the immutable materialization, never an echoed digest.
        child = subprocess.run([sys.executable, '-c',
            'import hashlib,json,sys; from pathlib import Path; '
            'print(json.dumps({"digest":"sha256:"+hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest()}))',
            input_handle.relative_path], capture_output=True, text=True, check=True)
        launch = dict(schema='mission-fresh-review-launch/1', request_id=request['request_id'],
            request_digest=canonical_digest(request), nonce=request['nonce'], operation_id=envelope['operation_id'],
            fencing_epoch=envelope['fencing_epoch'], adapter_registration_digest=request['adapter_registration_digest'],
            parent_identity='fixture-parent', child_identity='fixture-child:' + request['nonce'],
            context_identity='fixture-context:' + request['nonce'], context_mode='fresh',
            received_input_digest=json.loads(child.stdout)['digest'], started_at=request['created_at'],
            enforced_tools=request['allowed_tools'], enforced_budget={key: request[key] for key in (
                'wall_time_sec', 'max_tool_calls', 'max_replays', 'max_output_bytes', 'max_packet_bytes')})
        mode = os.environ.get('FIXTURE_REVIEW_MODE', '')
        if mode == 'inline':
            launch.update(context_mode='inline', child_identity='fixture-parent', context_identity='fixture-parent')
        if mode == 'unobservable':
            launch.pop('child_identity')
        if mode == 'provider-invalid':
            launch['child_identity'] = 'file:opaque'
        output = b'{"diagnostic":"import is out of scope"}'
        prior = json.loads(_journal().read_text()) if _journal().exists() else {'count': 0}
        _journal().write_text(json.dumps({'launch': launch, 'count': prior['count'] + 1,
                                        'output': output.decode()}))
        if mode == 'crash':
            os._exit(7)
        return freeze_json_value(launch)

    def recover(self, dispatch):
        if not _journal().exists():
            return CollectedReview(freeze_json_value({}), None)
        journal = json.loads(_journal().read_text())
        return CollectedReview(freeze_json_value({'launch_receipt': journal['launch']}),
                               journal['output'].encode())

    def collect(self, launch):
        return self.recover(launch)

    def cancel(self, dispatch):
        return freeze_json_value({'status': 'cancelled'})


def factory():
    return Adapter()
