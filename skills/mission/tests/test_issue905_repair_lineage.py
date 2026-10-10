"""Durable origins cannot be erased by clean reviews, imports or resume."""
import copy
import json
from dataclasses import replace

import pytest

from mission_kernel.fresh_review import FreshReviewError, canonical_bytes, canonical_digest, projection_document
from mission_kernel.json_codec import freeze_json_value
from .test_issue896_completed import completed_carrier, replay_reviewer
from .test_issue912_fresh_review_dispatch import completion_session, invoke
from .test_issue896_publish import import_output
from .test_issue913_completion_inputs import published, gate_state
from .test_issue913_completion_judgement import clean, bound_record, command_for, rejected


def test_terminal_introduces_lineage_in_same_transition(completed_carrier, gate_state):
    from mission_kernel.transitions import decide
    from mission_kernel.fresh_review import FreshReviewProjection
    from mission_kernel.repair_lineage import projection_document as repair_document
    from mission_kernel.model import FencedLease
    record, command, contract = completed_carrier
    # The running fixture is intentionally partial; use the authenticated D3
    # record's dispatch fields when exercising the real reducer.
    from .test_issue917_fresh_review_bounds import maximum_intent
    from mission_kernel.fresh_review_dispatch import reservation_id_for_operation
    intent = maximum_intent()
    intent.update(operation_id='dispatch', fencing_epoch=2, parent_identity='parent',
        invocation_id='inv_' + canonical_digest(record.request.request_id)[7:39],
        outbound_packet_digest=record.request.input_digest, iteration=record.request.iteration,
        reservation_id=reservation_id_for_operation('dispatch'), budget_class='verification')
    record = replace(record, dispatch=freeze_json_value(intent), launch_operation_id='dispatch',
        intent_digest=record.request.input_digest, payload_digest=record.request.input_digest)
    document = gate_state.legacy_passthrough.thaw()
    document.update(acceptance_contract=contract, fresh_review=projection_document(FreshReviewProjection((record,))))
    state = replace(gate_state, fresh_review=FreshReviewProjection((record,)),
        lease=FencedLease("session", "lease", command.fencing_epoch, "2099-01-01T00:00:00Z", ()),
        legacy_passthrough=freeze_json_value(document))
    from mission_kernel.fresh_review_receipts import decode_terminal_receipt, receipt_document
    command = replace(command, receipt=freeze_json_value(receipt_document(decode_terminal_receipt(command.receipt.thaw()))))
    decision = decide(state, command)
    assert decision.accepted, decision.rejection
    result = decision.transition.new_state
    rows = repair_document(result.repair)['lineages']
    assert len(rows) == 1 and rows[0]['lifecycle'] == 'open'
    assert result.legacy_passthrough.thaw()['repair_lineage'] == repair_document(result.repair)
    assert decision.effects == ()  # The application publishes the three D blobs.


def test_codecs_and_backing_are_closed(published, gate_state):
    from mission_kernel import decode_mission_state, project_legacy_document, decode_snapshot
    from mission_kernel.codec_v5 import encode_v5_state
    from mission_kernel.repair_lineage import import_origins, projection_document as repair_document
    from mission_kernel.fresh_review_completion import decode_completion_evidence
    from .mission_state_fixture_corpus import current_v5_open_state
    record, terminal, _, coverage, findings = published
    evidence = decode_completion_evidence(record.request, terminal, json.loads(coverage), tuple(map(json.loads, findings)))
    document = gate_state.legacy_passthrough.thaw()
    document["fresh_review"] = projection_document(gate_state.fresh_review)
    state = import_origins(replace(gate_state, legacy_passthrough=freeze_json_value(document)), (evidence,))
    doc = state.legacy_passthrough.thaw()
    doc['fresh_review'] = projection_document(state.fresh_review)
    state = decode_mission_state(canonical_bytes(doc))
    assert state.repair.lineages
    assert json.loads(project_legacy_document(state))['repair_lineage'] == repair_document(state.repair)
    payload = current_v5_open_state()
    payload['extensions'].update(repair_lineage=repair_document(state.repair), fresh_review=doc['fresh_review'])
    closed = decode_mission_state(canonical_bytes(payload))
    assert closed.repair == state.repair
    assert decode_mission_state(encode_v5_state(closed, decode_snapshot(canonical_bytes(payload)).guidance)).repair == state.repair
    with pytest.raises(ValueError, match='repair-lineage-projection-mismatch'):
        project_legacy_document(replace(state, repair=type(state.repair)()))
    valid = repair_document(state.repair)
    for wire in (doc, payload):
        duplicated = canonical_bytes(wire).replace(b'"repair_lineage":', b'"repair_lineage":null,"repair_lineage":', 1)
        with pytest.raises(ValueError, match='duplicate'):
            decode_mission_state(duplicated)
    for malformed in (None, {}, dict(valid, schema='future'), dict(valid, extra=True),
                      dict(valid, lineages=valid['lineages'] * 2)):
        for container in (doc, payload['extensions']):
            container['repair_lineage'] = malformed
        for wire in (doc, payload):
            with pytest.raises(ValueError, match='repair-lineage-'):
                decode_mission_state(canonical_bytes(wire))


def test_stable_origin_and_dual_repro_digest(published, gate_state):
    from mission_kernel.repair_lineage import import_origins, lineage_id
    from mission_kernel.fresh_review_completion import decode_completion_evidence
    record, terminal, _, coverage, findings = published
    evidence = decode_completion_evidence(record.request, terminal, json.loads(coverage), tuple(map(json.loads, findings)))
    document = gate_state.legacy_passthrough.thaw()
    document["fresh_review"] = projection_document(gate_state.fresh_review)
    state = import_origins(replace(gate_state, legacy_passthrough=freeze_json_value(document)), (evidence,))
    row = state.repair.lineages[0].document.thaw()
    origin = row['origin']
    assert row['lineage_id'] == lineage_id(origin, row['criterion_id'])
    # Reused local ids in different requests and criteria are distinct origins.
    assert len({row['lineage_id'], lineage_id(dict(origin, request_id='another-request'), row['criterion_id']),
                lineage_id(origin, 'another-criterion')}) == 3
    assert row['repro']['runner_repro_digest'] == json.loads(findings[0])['replay']['repro_input_digest']
    assert row['repro']['repro_digest'] != row['repro']['runner_repro_digest']
    assert import_origins(state, (evidence,)) == state
    forged = copy.deepcopy(row)
    forged['repro']['runner_repro_digest'] = 'sha256:' + '0' * 64
    from mission_kernel.repair_lineage import decode_projection
    bad = decode_projection(dict(state.legacy_passthrough.thaw(), repair_lineage=dict(schema='mission-repair-lineage/1', lineages=[forged])))
    with pytest.raises(FreshReviewError, match='repair-lineage-origin-mismatch'):
        import_origins(replace(state, repair=bad, legacy_passthrough=freeze_json_value(dict(
            state.legacy_passthrough.thaw(), repair_lineage=dict(schema='mission-repair-lineage/1', lineages=[forged])))), (evidence,))


def test_projection_union_cannot_hide_old_low_finding(clean, published):
    from mission_kernel.fresh_review import FreshReviewProjection
    from mission_kernel.repair_lineage import import_origins
    state, command = clean
    finding = json.loads(published[4][0]); finding['severity'] = 'Low'
    coverage = command.fresh_review_evidence[0].coverage.thaw(); coverage['open_finding_ids'] = [finding['finding_id']]
    older, evidence = bound_record(state.fresh_review.requests[0], coverage, (finding,), request_id='older', nonce='older-nonce')
    state = replace(state, fresh_review=FreshReviewProjection((older, *state.fresh_review.requests)))
    document = state.legacy_passthrough.thaw(); document['fresh_review'] = projection_document(state.fresh_review)
    state = replace(state, legacy_passthrough=freeze_json_value(document))
    rejected(state, command_for(state, (evidence, *command.fresh_review_evidence)), 'acceptance-unresolved-finding')


@pytest.mark.parametrize('wire_schema', [4, 5], ids=['v4-flat', 'v5-container'])
def test_public_terminal_persists_lineage_and_rejects_completion(replay_reviewer, run_cli, wire_schema):
    root, _, _, _ = replay_reviewer
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    result = import_output(run_cli, replay_reviewer)
    assert result.returncode == 0, result.stderr
    state = json.loads(run_cli('get', cwd=root).stdout)
    assert len(state['repair_lineage']['lineages']) == 1
    from .test_issue879_completion_cli import _reject_unchanged
    run_cli('verification', 'run', '--criterion', 'AC1', cwd=root, check=True)
    if wire_schema == 4:
        from .test_issue879_completion_cli import _persist_fixture
        _persist_fixture(root, json.loads(run_cli('get', cwd=root).stdout), 4)
        _reject_unchanged(run_cli, root, ['init', 'replacement', '--force-mission'], 'repair-lineage-reinitialization-forbidden')
    _reject_unchanged(run_cli, root, ['mark-passes'], 'acceptance-unresolved-finding')
    _reject_unchanged(run_cli, root, ['set', 'repair_lineage=null'], 'dedicated')


@pytest.mark.parametrize('wire_schema', [4, 5], ids=['v4-flat', 'v5-container'])
@pytest.mark.parametrize('route', ['mark-passes', 'closeout', 'already-passed'])
def test_missing_contract_cannot_bypass_public_completion(replay_reviewer, run_cli, wire_schema, route):
    from .test_issue879_completion_cli import _persist_fixture, _reject_unchanged
    root = replay_reviewer[0]
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    assert import_output(run_cli, replay_reviewer).returncode == 0
    run_cli('verification', 'run', '--criterion', 'AC1', cwd=root, check=True)
    state = json.loads(run_cli('get', cwd=root).stdout)
    assert state['repair_lineage']['lineages'][0]['lifecycle'] == 'open'
    state.pop('acceptance_contract')
    if route == 'already-passed':
        state.update(passes=True, loop_active=False, phase='done', terminal_outcome='completed_pass')
    _persist_fixture(root, state, wire_schema, operation_id='missing-contract-fixture')
    loaded = run_cli('get', cwd=root)
    assert loaded.returncode == 0, loaded.stderr
    args = ['closeout' if route == 'already-passed' else route]
    _reject_unchanged(run_cli, root, args, 'acceptance-contract-missing')
    # The same scored legacy session is still usable without either origin store.
    state.pop('fresh_review'); state.pop('repair_lineage')
    _persist_fixture(root, state, wire_schema, operation_id='legacy-without-origins')
    result = run_cli(*args, cwd=root)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('store', ['origin', 'overflow', 'projection', 'typed-lineage'])
@pytest.mark.parametrize('closed', [False, True], ids=['v4-carrier', 'v5-extensions'])
def test_missing_contract_rejects_kernel_completion(clean, published, store, closed):
    from mission_kernel.fresh_review import FreshReviewProjection
    from mission_kernel.repair_lineage import import_origins, RepairProjection
    from mission_kernel.fresh_review_completion import decode_completion_evidence
    state, command = clean
    record, terminal, _, coverage, findings = published
    evidence = decode_completion_evidence(record.request, terminal, json.loads(coverage), tuple(map(json.loads, findings)))
    state = replace(state, fresh_review=FreshReviewProjection((record,)))
    document = state.legacy_passthrough.thaw(); document['fresh_review'] = projection_document(state.fresh_review)
    state = import_origins(replace(state, legacy_passthrough=freeze_json_value(document)), (evidence,))
    document = state.legacy_passthrough.thaw(); document.pop('acceptance_contract')
    if store in ('origin', 'typed-lineage', 'overflow'):
        document.pop('repair_lineage')
    if store == 'projection':
        document['repair_lineage']['lineages'] = []; document.pop('fresh_review')
        state = replace(state, fresh_review=FreshReviewProjection(), repair=RepairProjection())
    elif store == 'typed-lineage':
        document.pop('fresh_review')
        state = replace(state, fresh_review=FreshReviewProjection())
    elif store != 'typed-lineage':
        state = replace(state, repair=RepairProjection())
    if store == 'overflow':
        result = record.result.thaw()
        for key in ('coverage_receipt', 'findings', 'independent'):
            result.pop(key)
        result.update(outcome='failed', reason='output-over-import-limit')
        state = replace(state, fresh_review=FreshReviewProjection((replace(record, status='failed', result=freeze_json_value(result)),)))
        document['fresh_review'] = projection_document(state.fresh_review)
    if closed:
        from mission_kernel.model import SchemaOrigin
        state = replace(state, schema_origin=SchemaOrigin.V5, legacy_passthrough=None, extensions=freeze_json_value(document))
    else:
        state = replace(state, legacy_passthrough=freeze_json_value(document))
    rejected(state, command, 'acceptance-contract-missing')


def test_prohibited_side_effect_blocks_even_context_findings(published):
    from mission_kernel.repair_lineage import RepairProjection, effective_unresolved_findings
    record, _, contract, coverage, findings = published
    contract = copy.deepcopy(contract)
    contract['requirements'][0]['classification'] = 'context'
    finding = json.loads(findings[0])
    def unresolved(finding):
        changed, evidence = bound_record(record, json.loads(coverage), (finding,))
        from mission_kernel.fresh_review import FreshReviewProjection
        return effective_unresolved_findings(RepairProjection(), FreshReviewProjection((changed,)), (evidence,), contract)
    assert len(unresolved(finding)) == 1
    finding['prohibited_side_effect_ids'] = []
    assert unresolved(finding) == ()


@pytest.mark.parametrize('schema', [4, 5])
def test_persistence_rejects_raw_duplicate_repair_keys(tmp_path, schema):
    from mission_persistence.authoritative_reader import read_session_json
    projection = dict(schema='mission-repair-lineage/1', lineages=[])
    payload = dict(schema_version=schema, repair_lineage=projection)
    if schema == 5:
        payload = dict(schema_version=5, extensions=dict(repair_lineage=projection))
    raw = canonical_bytes(payload).replace(b'"repair_lineage":', b'"repair_lineage":null,"repair_lineage":', 1)
    path = tmp_path / 'session.json'; path.write_bytes(raw)
    with pytest.raises(ValueError, match='duplicate'):
        read_session_json(path)
    assert path.read_bytes() == raw


def test_initial_backfill_status_and_resume_keep_all_origins(replay_reviewer, run_cli):
    from .test_issue879_completion_cli import _rewrite_fixture_document, _reject_unchanged
    root, request, env, _ = replay_reviewer
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    assert import_output(run_cli, replay_reviewer).returncode == 0
    original = json.loads(run_cli('get', cwd=root).stdout)['repair_lineage']
    _rewrite_fixture_document(root, lambda doc: doc.get('extensions', doc).pop('repair_lineage'))
    status = json.loads(run_cli('fresh-review', 'status', cwd=root).stdout)
    assert status['repair']['lineages'] == []
    assert status['repair']['unresolved_lineage_ids'] == [original['lineages'][0]['lineage_id']]
    result = run_cli('fresh-review', 'import-lineage', cwd=root,
        env_extra={**env, 'MISSION_OPERATION_ID': 'backfill'})
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(run_cli('get', cwd=root).stdout)
    assert state['repair_lineage'] == original
    assert len(state['fresh_review']['requests']) == 1
    for args in (['set', 'repair_lineage.lineages=[]'], ['set', 'extensions.repair_lineage=null']):
        _reject_unchanged(run_cli, root, args, 'dedicated')
    # Existing control-only resume must not reset repair, even after dirty bytes.
    (root / 'app.txt').write_text('candidate changed')
    run_cli('refresh-pid', '--no-reactivate', cwd=root, check=True)
    assert json.loads(run_cli('get', cwd=root).stdout)['repair_lineage'] == original


def test_decoder_input_exploration_closes_nested_null_and_extra_fields(published, gate_state):
    from mission_kernel.repair_lineage import import_origins, decode_projection
    from mission_kernel.fresh_review_completion import decode_completion_evidence
    record, terminal, _, coverage, findings = published
    evidence = decode_completion_evidence(record.request, terminal, json.loads(coverage), tuple(map(json.loads, findings)))
    document = gate_state.legacy_passthrough.thaw(); document['fresh_review'] = projection_document(gate_state.fresh_review)
    state = import_origins(replace(gate_state, legacy_passthrough=freeze_json_value(document)), (evidence,))
    valid = state.legacy_passthrough.thaw()
    paths = []
    def visit(value, path):
        if isinstance(value, dict):
            paths.append(path)
            for key, child in value.items():
                visit(child, (*path, key))
    visit(valid['repair_lineage'], ('repair_lineage',))
    for i, row in enumerate(valid['repair_lineage']['lineages']):
        visit(row, ('repair_lineage', 'lineages', i))
    cases = 0
    for path in paths:
        original = valid
        for key in path:
            original = original[key]
        for key in original:
            for missing in (True, False):
                changed = copy.deepcopy(valid); target = changed
                for part in path:
                    target = target[part]
                if missing:
                    target.pop(key)
                else:
                    target[key] = None
                with pytest.raises(FreshReviewError, match='repair-lineage-'):
                    decode_projection(changed)
                cases += 1
        changed = copy.deepcopy(valid); target = changed
        for part in path:
            target = target[part]
        target['unknown'] = 1
        with pytest.raises(FreshReviewError, match='repair-lineage-'):
            decode_projection(changed)
        cases += 1
    # Include row dictionaries behind the collection, which visit skips.
    row = valid['repair_lineage']['lineages'][0]
    for key in row:
        changed = copy.deepcopy(valid); changed['repair_lineage']['lineages'][0][key] = None
        with pytest.raises(FreshReviewError, match='repair-lineage-'):
            decode_projection(changed)
        cases += 1
    assert cases >= 50


@pytest.mark.parametrize('side_effect_count', [0, 100])
def test_real_lineage_maximum_fits_reserved_bytes(published, side_effect_count):
    from mission_kernel.repair_lineage import _finding
    from mission_kernel.state_capacity import FRESH_REVIEW_FINDING_LINEAGE_FIXED_MAX_BYTES, lineage_variable_part
    record, terminal, contract, _, findings = published
    contract = copy.deepcopy(contract)
    criterion = 'c'*120
    contract['criteria'][0].update(id=criterion, prohibited_side_effects=['x']*side_effect_count)
    request = replace(record.request, mission_id='m'*128, session_id='s'*128, request_id='r'*128, criterion_ids=(criterion,))
    finding = json.loads(findings[0]); finding.update(finding_id='f'*128, criterion_id=criterion, command_id='replay-1',
        prohibited_side_effect_ids=[f'{criterion}:{i}' for i in range(side_effect_count)])
    row = _finding(request, terminal, finding, terminal.findings[0].__dict__, contract)
    # Maximise every bounded id independent of evidence contents, retain digest
    # and path sizes, and count the actual variable obligation/snapshot payload.
    def maximum(value, key=''):
        if isinstance(value, dict):
            return {k: maximum(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [maximum(v, key) for v in value]
        if isinstance(value, str) and key in ('command_id', 'mission_id', 'session_id', 'request_id', 'finding_id', 'criterion_id'):
            return 'x'*128
        if type(value) is int:
            return 262144 if key == 'size' else 2**63-1
        return value
    row = maximum(row)
    row['last_candidate_change'] = dict(generation=2**63-1, candidate_map_digest='sha256:'+'0'*64, operation_id='o'*128)
    assert len(canonical_bytes(row)) <= FRESH_REVIEW_FINDING_LINEAGE_FIXED_MAX_BYTES + lineage_variable_part(
        {'acceptance_contract': contract}, request)


def test_over_limit_origin_survives_a_later_clean_review(clean, published):
    from mission_kernel.fresh_review import FreshReviewProjection
    state, command = clean
    older, _ = bound_record(published[0], json.loads(published[3]), tuple(map(json.loads, published[4])),
        request_id='older-overflow', nonce='older-overflow-nonce')
    terminal = older.result.thaw()
    for key in ('coverage_receipt', 'findings', 'independent'):
        terminal.pop(key)
    terminal.update(outcome='failed', reason='output-over-import-limit')
    older = replace(older, status='failed', result=freeze_json_value(terminal))
    state = replace(state, fresh_review=FreshReviewProjection((older, *state.fresh_review.requests)))
    rejected(state, command_for(state, command.fresh_review_evidence), 'acceptance-unresolved-finding')


def test_only_missing_origin_reservations_are_released(published, gate_state):
    from mission_kernel.repair_lineage import import_origins
    from mission_kernel.fresh_review_completion import decode_completion_evidence
    from mission_kernel.state_capacity import lineage_residual
    record, terminal, _, coverage, findings = published
    evidence = decode_completion_evidence(record.request, terminal, json.loads(coverage), tuple(map(json.loads, findings)))
    document = gate_state.legacy_passthrough.thaw(); document['fresh_review'] = projection_document(gate_state.fresh_review)
    assert lineage_residual(document, record) > 0
    state = import_origins(replace(gate_state, legacy_passthrough=freeze_json_value(document)), (evidence,))
    assert lineage_residual(state.legacy_passthrough.thaw(), record) == 0
    from mission_kernel.repair_lineage import lineage_id
    row = state.repair.lineages[0].document.thaw()
    # Display changes and current-candidate changes cannot mint a new origin id.
    for severity in ('High', 'Medium', 'Low'):
        changed = dict(row, severity=severity, candidate_digest='sha256:'+'0'*64, summary='new wording')
        assert lineage_id(changed['origin'], changed['criterion_id']) == row['lineage_id']


def test_backfill_consumes_only_existing_reservation(published, gate_state):
    from mission_kernel.repair_lineage import import_origins
    from mission_kernel.fresh_review_completion import decode_completion_evidence
    from mission_kernel import state_capacity as sc
    record, terminal, _, coverage, findings = published
    evidence = decode_completion_evidence(record.request, terminal, json.loads(coverage), tuple(map(json.loads, findings)))
    document = gate_state.legacy_passthrough.thaw(); document['fresh_review'] = projection_document(gate_state.fresh_review)
    document['padding'] = ''
    document['padding'] = 'p' * (sc.STATE_LIMIT - sc.system_remaining(document) - sc.residual_reservation(document)
        - len(canonical_bytes(document)) - 256)
    state = replace(gate_state, legacy_passthrough=freeze_json_value(document))
    from mission_kernel.commands import ImportRepairOrigins
    from mission_kernel.transitions import decide, bind_transition_effects
    from mission_kernel.model import FencedLease
    state = replace(state, lease=FencedLease('session', 'lease', 1, '2099-01-01T00:00:00Z', ()))
    command = ImportRepairOrigins((evidence,), 1)
    decision = decide(state, command)
    assert decision.accepted, decision.rejection
    proposed = decision.transition.new_state.legacy_passthrough.thaw()
    verdict = sc.state_capacity_verdict(sc.CapacityBase(document, len(canonical_bytes(document))), proposed, len(canonical_bytes(proposed)), encoding=sc.StateEncoding.CANONICAL)
    assert verdict.accepted, verdict
    assert sc.residual_reservation(proposed) == 0
    assert decision.transition.new_state.fresh_review == state.fresh_review
    assert not decide(state, replace(command, fencing_epoch=0)).accepted
    with pytest.raises(ValueError, match='invalid-transition-effect-binding'):
        bind_transition_effects(decision.transition, (object(),))


@pytest.mark.parametrize('missing', ['requirements', 'criteria', 'classification',
    'empty-requirements', 'empty-criteria', 'unknown-requirement'])
def test_malformed_contract_rejects_with_reason(clean, replay_reviewer, run_cli, missing):
    from mission_kernel.repair_lineage import RepairProjection, effective_unresolved_findings
    from mission_kernel.fresh_review import FreshReviewProjection
    from .test_issue879_completion_cli import _rewrite_fixture_document, _reject_unchanged
    state, command = clean
    document = state.legacy_passthrough.thaw()
    contract = document['acceptance_contract']
    def corrupt(value):
        if missing == 'classification':
            value['requirements'][0].pop('classification')
        elif missing.startswith('empty-'):
            value[missing.removeprefix('empty-')] = []
        elif missing == 'unknown-requirement':
            value['criteria'][0]['requirement_ids'] = ['unknown']
        else:
            value.pop(missing)
    corrupt(contract)
    with pytest.raises(FreshReviewError) as error:
        effective_unresolved_findings(RepairProjection(), FreshReviewProjection(), (), contract)
    assert error.value.code == 'acceptance-contract-invalid'
    # The final transition and public status must reject without raw exceptions.
    rejected(replace(state, legacy_passthrough=freeze_json_value(document)), command, 'acceptance-contract-invalid')
    root = replay_reviewer[0]
    _rewrite_fixture_document(root, lambda doc: corrupt(doc.get('extensions', doc)['acceptance_contract']))
    _reject_unchanged(run_cli, root, ['fresh-review', 'status'], 'acceptance-contract-invalid')


def test_status_without_contract_or_origins_remains_available(completion_session, run_cli):
    from .test_issue879_completion_cli import _persist_fixture, _public_bytes
    root, state, schema = completion_session
    state.pop('acceptance_contract')
    _persist_fixture(root, state, schema)
    before = _public_bytes(root)
    result = run_cli('fresh-review', 'status', cwd=root)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['repair']['unresolved_lineage_ids'] == []
    assert _public_bytes(root) == before


def test_malformed_frozen_command_rejects_completed_origin(gate_state, published, replay_reviewer, run_cli):
    from mission_kernel.repair_lineage import RepairProjection, effective_unresolved_findings
    from mission_kernel.fresh_review_completion import decode_completion_evidence
    from .test_issue879_completion_cli import _rewrite_fixture_document, _reject_unchanged
    record, terminal, contract, coverage, findings = published
    evidence = decode_completion_evidence(record.request, terminal, json.loads(coverage), tuple(map(json.loads, findings)))
    def corrupt(value):
        commands = value['verifier_policy']['commands']
        commands[next(iter(commands))] = None
    corrupt(contract)
    with pytest.raises(FreshReviewError) as error:
        effective_unresolved_findings(RepairProjection(), gate_state.fresh_review, (evidence,), contract)
    assert error.value.code == 'acceptance-contract-invalid'
    invoke(run_cli, replay_reviewer, FIXTURE_REVIEW_MODE='counterexample')
    assert import_output(run_cli, replay_reviewer).returncode == 0
    root = replay_reviewer[0]
    _rewrite_fixture_document(root, lambda doc: corrupt(doc.get('extensions', doc)['acceptance_contract']))
    _reject_unchanged(run_cli, root, ['fresh-review', 'status'], 'acceptance-contract-invalid')


@pytest.mark.parametrize('absent', ['legacy-absent', 'none'])
def test_origin_import_requires_present_fenced_lease(gate_state, absent):
    from mission_kernel.commands import ImportRepairOrigins
    from mission_kernel.model import LegacyAbsentLease
    from mission_kernel.fresh_review import FreshReviewProjection
    from mission_kernel.repair_lineage import RepairProjection
    from mission_kernel.transitions import decide
    document = gate_state.legacy_passthrough.thaw()
    document.pop('fresh_review', None); document.pop('repair_lineage', None)
    state = replace(gate_state, fresh_review=FreshReviewProjection(), repair=RepairProjection(),
        lease=LegacyAbsentLease() if absent == 'legacy-absent' else None,
        legacy_passthrough=freeze_json_value(document))
    decision = decide(state, ImportRepairOrigins((), 0))
    assert not decision.accepted
    assert decision.rejection.code == 'repair-lineage-stale-fence'
    assert decision.transition is None and decision.events == decision.effects == ()
    assert state.legacy_passthrough.thaw() == document
