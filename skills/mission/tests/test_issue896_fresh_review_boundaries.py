"""Preserve nonce uniqueness and fail closed at every persisted read boundary."""
from types import SimpleNamespace
from dataclasses import replace
import copy
import contextlib
import json
from pathlib import Path

import pytest

from .test_issue879_completion_cli import completion_session


@pytest.mark.parametrize('stage', ['cwd', 'resolve', 'repository'])
def test_prepare_oserror_does_not_disclose_a_local_path(tmp_path, monkeypatch, stage):
    from mission_application.fresh_review import run_fresh_review_prepare_cli
    from .test_issue895_fresh_review import ADAPTER

    state = tmp_path / 'state.json'
    state.write_text('{}')
    private = str(tmp_path / 'private-input')

    def repository(*args, **kwargs):
        raise OSError(13, 'permission denied', private)

    class Rejected(Exception):
        pass

    def fail(code, exit_code):
        assert exit_code == 2
        raise Rejected(code)

    services = SimpleNamespace(resolve_state_file=lambda root: state, repository=repository,
                               compatibility_arguments=lambda *args, **kwargs: (None, {}),
                               canonical_operation=lambda *args, **kwargs: {},
                               fail=fail)
    if stage == 'cwd':
        monkeypatch.setattr(Path, 'cwd', repository)
    elif stage == 'resolve':
        services.resolve_state_file = repository
    args = SimpleNamespace(perspective='counterexamples', criterion=None,
                           adapter_registration_digest=ADAPTER, allowed_tool=[], wall_time_sec=300,
                           max_tool_calls=64, max_replays=16, max_output_bytes=262144,
                           max_packet_bytes=1048576)
    with pytest.raises(Rejected) as error:
        run_fresh_review_prepare_cli(args, services)
    assert str(error.value) == 'fresh-review-io-unavailable'
    assert private not in str(error.value)


@pytest.mark.parametrize('duplicate', ['nonce', 'request_id'])
def test_kernel_prepare_cannot_install_another_request_with_reused_identity(
    completion_session, run_cli, duplicate,
):
    from mission_kernel import decode_mission_state
    from mission_kernel.transitions import decide
    from mission_kernel.commands import PrepareFreshReview, FreshReviewInputEffectClaim
    from mission_kernel.fresh_review import decode_request
    from mission_kernel.json_codec import freeze_json_value
    from .test_issue895_fresh_review import _prepare, ADAPTER

    root, raw = _prepare(completion_session, run_cli)
    state = decode_mission_state(run_cli('get', cwd=root).stdout.encode())
    request = decode_request(raw)
    # Change every other unique identity: the duplicate alone must reject.
    changes = {'request_id': 'different-request', 'nonce': 'different-nonce'}
    changes[duplicate] = getattr(request, duplicate)
    ref = request.input_ref
    packet = json.loads((root / ref.relative_path).read_bytes())
    command = PrepareFreshReview(replace(request, **changes), 'prepare-two', ADAPTER, ADAPTER,
                                 freeze_json_value(packet),
                                 FreshReviewInputEffectClaim(ref.kind, ref.relative_path, ref.digest, ref.size))
    decision = decide(state, command)
    assert not decision.accepted
    assert decision.rejection.code == 'fresh-review-nonce-reused'


@pytest.mark.parametrize('duplicate', ['nonce', 'request_id'])
def test_projection_decoder_rejects_duplicate_identity_with_distinct_operations(duplicate):
    from mission_kernel.fresh_review import decode_projection, projection_document, FreshReviewError
    from .test_issue895_fresh_review import _pure_projection

    projection = projection_document(_pure_projection())
    other = copy.deepcopy(projection['requests'][0])
    other['prepare_operation_id'] = 'prepare-two'
    other['request'].update(request_id='request-two', nonce='nonce-two')
    other['request'][duplicate] = projection['requests'][0]['request'][duplicate]
    projection['requests'].append(other)
    with pytest.raises(FreshReviewError, match='fresh-review-identity-reused'):
        decode_projection({'fresh_review': projection})


@pytest.mark.parametrize('reader', ['archived-canonical', 'legacy-load', 'direct-session', 'legacy-fallback'])
def test_each_reader_rejects_forged_projection_before_exposing_state(tmp_path, reader):
    from mission_kernel.fresh_review import FreshReviewError
    from mission_persistence.authoritative_reader import (
        authoritative_snapshot_from_validated_archive_bytes, read_session_json, read_legacy_compatibility_snapshot,
    )
    from mission_persistence.legacy_v4 import LegacyV4Repository

    # A flat canonical archive never passes through the live-head decoder.
    document = {'schema_version': 4, 'session_id': 'fixture', 'mission': 'fixture',
                'phase': 'execute', 'loop_active': True, 'passes': False,
                'fresh_review': {'schema': 'future', 'requests': []}}
    source = json.dumps(document).encode()
    path = tmp_path / 'archive.json'
    if reader == 'legacy-fallback':
        document['threshold'] = 'historical value'
        source = json.dumps(document).encode()
    path.write_bytes(source)
    with pytest.raises(FreshReviewError, match='fresh-review-projection-schema-invalid'):
        if reader == 'archived-canonical':
            authoritative_snapshot_from_validated_archive_bytes(source)
        elif reader == 'direct-session':
            read_session_json(path)
        elif reader == 'legacy-fallback':
            read_legacy_compatibility_snapshot(path)
        else:
            repository = LegacyV4Repository(lock=contextlib.nullcontext, read_state=lambda: document,
                                            write_state=lambda value: pytest.fail('unexpected write'),
                                            backup_state=lambda: pytest.fail('unexpected backup'))
            with repository.transaction():
                repository.load()
    assert path.read_bytes() == source
