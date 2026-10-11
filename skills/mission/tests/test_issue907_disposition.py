"""Independent dispositions cannot transfer obligations across replay scopes."""
from copy import deepcopy
import pytest
from .test_issue906_reverification import reverified
from .test_issue906_repair_attempts import repair_state, published, gate_state, completed_carrier


def test_duplicate_cannot_discharge_a_different_criterion(reverified):
    from mission_kernel.finding_disposition import duplicate_scope
    row = reverified[2].repair.lineages[0].document.thaw()
    other = deepcopy(row)
    other['criterion_id'] = 'other-criterion'
    assert not duplicate_scope(row, other)


def test_duplicate_scope_uses_typed_replay_not_origin_reference(reverified):
    from mission_kernel.finding_disposition import duplicate_scope
    row = reverified[2].repair.lineages[0].document.thaw()
    other = deepcopy(row)
    other['repro']['repro_input_ref'] = 'sha256:' + 'c' * 64
    assert duplicate_scope(row, other)


def request_body(state, row, *, operation='disposition-prepare', supersedes=None):
    from acceptance_contract import canonical_contract_digest
    from mission_kernel.repair_lineage import origin_identity
    from mission_kernel.repair_attempts import reference, absent
    from mission_kernel.fresh_review import canonical_digest
    from mission_kernel.repair_reverification import full_candidate_digest
    contract = state.legacy_passthrough.thaw()['acceptance_contract']
    candidates = dict(row['introduced_candidate']['snapshots'])
    return dict(schema='mission-finding-disposition-request/1', request_id=canonical_digest(dict(
        domain='mission-finding-disposition-request/1', mission_id=row['origin']['mission_id'],
        session_id=row['origin']['session_id'], lineage_id=row['lineage_id'], operation_id=operation)),
        mission_id=row['origin']['mission_id'], session_id=row['origin']['session_id'],
        lineage_id=row['lineage_id'], operation_id=operation, origin_digest=canonical_digest(origin_identity(row)),
        contract_digest=canonical_contract_digest(contract), requirement_digest=contract['requirement_digest'],
        verifier_policy_digest=contract['verifier_policy']['digest'], full_candidate_digest=full_candidate_digest(contract, candidates),
        supersedes_attempt_id=absent('no-superseded-attempt') if supersedes is None else supersedes,
        input_ref=reference('finding-disposition-input', b'{}'), source_ref=row['finding_ref'],
        adapter_registration_digest='sha256:'+'a'*64, wall_time_sec=300, max_packet_bytes=1048576,
        max_output_bytes=262144)


def test_request_decoder_rejects_authored_resolution_authority(reverified):
    from mission_kernel.finding_disposition import decode_request
    from mission_kernel.fresh_review import FreshReviewError
    state = reverified[2]; row = state.repair.lineages[0].document.thaw()
    body = request_body(state, row)
    assert decode_request(body).document.thaw() == body
    with pytest.raises(FreshReviewError):
        decode_request(dict(body, resolved=True))


@pytest.fixture
def rejected(repair_state):
    from types import SimpleNamespace
    from mission_kernel.fresh_review import canonical_bytes, canonical_digest
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.repair_attempts import reference, _store
    from mission_kernel.finding_disposition import DispositionArtifact, DispositionEvidence
    from .test_issue917_fresh_review_bounds import maximum_launch
    state, evidence = repair_state
    row = state.repair.lineages[0].document.thaw()
    doc = state.legacy_passthrough.thaw(); row = doc['repair_lineage']['lineages'][0]
    request = request_body(state, row)
    item = dict(request_id=request['request_id'], request_ref=reference('finding-disposition-request', canonical_bytes(request)),
        full_candidate_digest=request['full_candidate_digest'], supersedes_attempt_id=request['supersedes_attempt_id'],
        operation_id=request['operation_id'], fencing_epoch=2, generation=1, stage='prepared',
        intent={'kind':'absent','reason':'not-dispatched'}, launch_ref={'kind':'absent','reason':'not-launched'},
        terminal={'kind':'absent','reason':'not-terminal'}, duplicate_targets=[])
    row['dispositions'] = [item]
    command = SimpleNamespace(request=freeze_json_value(request), evidence=(evidence,),
        observed_candidates=freeze_json_value(row['introduced_candidate']['snapshots']))
    dispatch = dict(operation_id='disposition-dispatch:'+request['request_id'][7:], fencing_epoch=2,
        reservation_id='disposition:'+request['request_id'][7:], budget_class='repair', parent_identity='parent',
        started_at='2026-01-01T00:00:00Z', deadline_at='2026-01-01T00:05:00Z')
    launch = maximum_launch()
    launch.update(request_id=request['request_id'], request_digest=canonical_digest(request), nonce=request['request_id'][7:39],
        operation_id=dispatch['operation_id'], fencing_epoch=2, adapter_registration_digest=request['adapter_registration_digest'],
        parent_identity='parent', child_identity='child', context_identity='context', context_mode='fresh',
        received_input_digest=request['input_ref']['digest'], started_at='2026-01-01T00:00:00.000000Z')
    launch['enforced_budget'].update(wall_time_sec=300)
    launch_ref = reference('finding-disposition-launch', canonical_bytes(launch))
    source = evidence.findings[0].thaw()
    scope = dict(criterion_ids=[row['criterion_id']], requirement_ids=row['requirement_ids'],
        prohibited_side_effect_ids=row['prohibited_side_effect_ids'], repro_digest=row['repro']['repro_digest'])
    output = dict(schema='mission-finding-disposition-output/1', request_id=request['request_id'],
        input_digest=request['input_ref']['digest'], verdict='not-violation', reason_code='not-violation',
        reason='The observed effect does not violate the bound obligation.', scope=scope,
        evidence=[row['finding_ref']], mapping_ref={'kind':'absent','reason':'no-mapping'})
    output_ref = reference('finding-disposition-output', canonical_bytes(output))
    terminal = dict(schema='mission-finding-disposition/1', request_id=request['request_id'], request_ref=item['request_ref'],
        launch_ref=launch_ref, output_ref=output_ref, mapping_ref=output['mapping_ref'], dispatch=dispatch,
        operation_id='disposition-terminal', fencing_epoch=2, generation=item['generation']+1,
        outcome='rejected', reason_code='not-violation')
    terminal_ref = reference('finding-disposition', canonical_bytes(terminal))
    item.update(stage='terminal', intent=dispatch, launch_ref=launch_ref,
        terminal=dict(ref=terminal_ref, operation_id=terminal['operation_id'], fencing_epoch=2,
            generation=terminal['generation'], outcome='rejected', reason_code='not-violation'))
    row['lifecycle'] = 'rejected'
    artifact = lambda ref, body: DispositionArtifact(freeze_json_value(ref), freeze_json_value(body))
    carrier = DispositionEvidence(tuple(artifact(ref, body) for ref, body in (
        (item['request_ref'], request), (request['input_ref'], {}), (launch_ref, launch),
        (output_ref, output), (terminal_ref, terminal), (row['finding_ref'], source))))
    return state, _store(state, doc), command, carrier


def test_independent_terminal_has_validated_evidence_without_completion_authority(rejected):
    from mission_kernel.finding_disposition import validate_terminal
    from mission_kernel.fresh_review_coverage import FreshReviewBindings
    from mission_kernel.repair_lineage import effective_unresolved_findings
    _, state, command, carrier = rejected
    document = state.legacy_passthrough.thaw(); row = document['repair_lineage']['lineages'][0]
    assert validate_terminal(document, row, row['dispositions'][-1], carrier,
        command.observed_candidates.thaw()) == ('rejected', ())
    bindings = FreshReviewBindings(command.request.thaw()['contract_digest'], (), (), (), command.observed_candidates)
    assert effective_unresolved_findings(state.repair, state.fresh_review, command.evidence,
        document['acceptance_contract'], bindings)


def test_persisted_terminal_summary_is_closed_and_cannot_supply_resolved(rejected):
    from mission_kernel.repair_lineage import decode_projection
    from mission_kernel.fresh_review import FreshReviewError
    doc = rejected[1].legacy_passthrough.thaw()
    doc['repair_lineage']['lineages'][0]['dispositions'][-1]['terminal']['resolved'] = True
    with pytest.raises(FreshReviewError):
        decode_projection(doc)


def rebind(rejected, changes, ref_changes=None, raw_document=None):
    """Rebind digests so semantic validators, rather than corrupt bytes, are exercised."""
    from mission_kernel.finding_disposition import DispositionEvidence, DispositionArtifact
    from mission_kernel.repair_attempts import reference, _store
    from mission_kernel.fresh_review import canonical_bytes
    from mission_kernel.json_codec import freeze_json_value
    before, state, command, carrier = rejected
    doc = state.legacy_passthrough.thaw() if raw_document is None else deepcopy(raw_document)
    bodies = [(a.reference.thaw(), a.content.thaw()) for a in carrier.artifacts]
    for ref, body in bodies:
        if ref['kind'] in changes:
            changes[ref['kind']](body)
    def replace_ref(value, old, new):
        if isinstance(value, dict):
            if value == old:
                value.clear(); value.update(new)
            else:
                for child in value.values(): replace_ref(child, old, new)
        elif isinstance(value, list):
            for child in value: replace_ref(child, old, new)
    for index, (old, body) in enumerate(bodies):
        new = reference(old['kind'], canonical_bytes(body))
        new['relative_path'] = old['relative_path'].rsplit('/', 1)[0] + '/' + new['digest'][7:] + '.json'
        if ref_changes and old['kind'] in ref_changes:
            ref_changes[old['kind']](new)
        if new != old:
            replace_ref(doc, old, new)
            for _, other in bodies[index+1:]: replace_ref(other, old, new)
        bodies[index] = new, body
    return before, (_store(state, doc) if raw_document is None else doc), command, DispositionEvidence(tuple(
        DispositionArtifact(freeze_json_value(ref), freeze_json_value(body)) for ref, body in bodies))


def test_terminal_cannot_publish_with_a_fence_older_than_its_dispatch(rejected):
    from mission_kernel.finding_disposition import validate_terminal
    from mission_kernel.fresh_review import FreshReviewError
    # The same request is admitted at epoch 2, but its actual dispatch is at epoch 3.
    changed = rebind(rejected, {'finding-disposition-launch': lambda b: b.update(fencing_epoch=3),
        'finding-disposition': lambda b: b['dispatch'].update(fencing_epoch=3)})
    doc = changed[1].legacy_passthrough.thaw(); row = doc['repair_lineage']['lineages'][0]
    row['dispositions'][-1]['intent']['fencing_epoch'] = 3
    with pytest.raises(FreshReviewError):
        validate_terminal(doc, row, row['dispositions'][-1], changed[3], changed[2].observed_candidates.thaw())


@pytest.mark.parametrize('kind,mutation', [
    ('finding-disposition-launch', lambda b: b.update(context_mode='shared')),
    ('finding-disposition-launch', lambda b: b.update(child_identity='parent')),
    ('finding-disposition-launch', lambda b: b.update(received_input_digest='sha256:'+'e'*64)),
    ('finding-disposition-output', lambda b: b['scope'].update(criterion_ids=['other'])),
    ('finding-disposition-output', lambda b: b['scope'].update(requirement_ids=[])),
    ('finding-disposition-output', lambda b: b.update(resolved=True)),
    ('finding-disposition', lambda b: b.update(generation=1)),
])
def test_rebound_artifacts_do_not_bypass_independence_scope_or_generation(rejected, kind, mutation):
    from mission_kernel.finding_disposition import validate_terminal
    from mission_kernel.fresh_review import FreshReviewError
    changed = rebind(rejected, {kind: mutation})
    doc = changed[1].legacy_passthrough.thaw(); row = doc['repair_lineage']['lineages'][0]
    with pytest.raises(FreshReviewError):
        validate_terminal(doc, row, row['dispositions'][-1], changed[3], changed[2].observed_candidates.thaw())


def test_overflow_mapping_covers_every_element_even_when_ids_repeat(rejected):
    from mission_kernel.finding_disposition import validate_mapping, artifact_body
    from mission_kernel.fresh_review import canonical_digest, FreshReviewError
    _, state, command, carrier = rejected
    doc = state.legacy_passthrough.thaw(); row = doc['repair_lineage']['lineages'][0]
    source = artifact_body(carrier, row['finding_ref'])
    output = artifact_body(carrier, row['dispositions'][-1]['terminal']['ref'])
    # The original output can exceed the D import count while IDs repeat.
    from .test_issue896_output_kernel import finding
    hypothesis = finding()
    raw = dict(criterion_results=[dict(criterion_id='AC1', findings=[deepcopy(hypothesis) for _ in range(62)])])
    overflow = dict(kind='unimported-findings', request_id=row['origin']['request_id'], lineage_id='sha256:'+'d'*64)
    terminal = artifact_body(carrier, output['output_ref'])
    mapping = dict(schema='mission-finding-disposition-mapping/1', source_digest=canonical_digest(raw),
        entries=[dict(index=i, element_digest=canonical_digest(hypothesis), verdict='not-violation',
            target={'kind':'absent','reason':'no-target'}, scope=terminal['scope']) for i in range(62)])
    assert validate_mapping(doc, overflow, raw, mapping, terminal, carrier) == ()
    mapping['entries'].pop()
    with pytest.raises(FreshReviewError, match='disposition-mapping-incomplete'):
        validate_mapping(doc, overflow, raw, mapping, terminal, carrier)


@pytest.mark.parametrize('stage', ['prepared', 'dispatched', 'running', 'terminal'])
def test_nonempty_disposition_history_never_falls_back_to_repair_resolution(reverified, rejected, stage):
    from mission_kernel.repair_attempts import _store, absent
    from mission_kernel.fresh_review_coverage import FreshReviewBindings
    from mission_kernel.repair_lineage import effective_unresolved_findings
    from mission_kernel.finding_disposition import lifecycle
    _, repair_command, state = reverified
    doc = state.legacy_passthrough.thaw(); row = doc['repair_lineage']['lineages'][0]
    item = deepcopy(rejected[1].repair.lineages[0].document.thaw()['dispositions'][0])
    item.update(stage=stage, generation=0)
    if stage != 'terminal': item['terminal'] = absent('not-terminal')
    else: item['terminal']['generation'] = 1
    if stage == 'prepared': item['intent'] = absent('not-dispatched')
    if stage in ('prepared', 'dispatched'): item['launch_ref'] = absent('not-launched')
    row['dispositions'] = [item]; row['lifecycle'] = lifecycle(row)
    assert row['lifecycle'] == 'verified'  # A later valid repair must not activate E3 semantics in this stage.
    state = _store(state, doc)
    bindings = FreshReviewBindings(repair_command.result.thaw()['receipt']['contract_digest'], (),
        tuple(repair_command.result.thaw()['candidate'].items()), (repair_command.result,))
    assert effective_unresolved_findings(reverified[2].repair, reverified[2].fresh_review, repair_command.evidence,
        doc['acceptance_contract'], bindings) == ()
    assert effective_unresolved_findings(state.repair, state.fresh_review, repair_command.evidence,
        doc['acceptance_contract'], bindings)


def test_alias_closure_requires_every_canonical_target_and_rejects_cycles():
    from mission_kernel.finding_disposition import resolved_aliases
    from mission_kernel.fresh_review import FreshReviewError
    a, b, c = ('sha256:'+letter*64 for letter in ('a','b','c'))
    edges = {a: (b,), b: (c,)}
    assert resolved_aliases(edges, set(), {a,b,c}) == set()
    assert resolved_aliases(edges, {c}, {a,b,c}) == {a,b}
    with pytest.raises(FreshReviewError):
        resolved_aliases({a:(b,), b:(a,)}, set(), {a,b})
    with pytest.raises(FreshReviewError):
        resolved_aliases({a:(c,)}, set(), {a,b})


@pytest.mark.parametrize('field', ['generation', 'fencing_epoch'])
def test_terminal_numbers_are_integers_even_when_equal_to_the_summary(rejected, field):
    from mission_kernel.finding_disposition import validate_terminal
    from mission_kernel.fresh_review import FreshReviewError
    changed = rebind(rejected, {'finding-disposition': lambda b: b.update({field: float(b[field])})})
    doc = changed[1].legacy_passthrough.thaw(); row = doc['repair_lineage']['lineages'][0]
    with pytest.raises(FreshReviewError):
        validate_terminal(doc, row, row['dispositions'][-1], changed[3], changed[2].observed_candidates.thaw())


@pytest.mark.parametrize('scope', [None, [], {'resolved': True}])
def test_deferred_output_still_requires_a_closed_typed_scope(rejected, scope):
    from mission_kernel.finding_disposition import validate_terminal
    from mission_kernel.fresh_review import FreshReviewError
    changed = rebind(rejected, {
        'finding-disposition-output': lambda b: b.update(verdict='deferred', reason_code='deferred', scope=scope),
        'finding-disposition': lambda b: b.update(outcome='deferred', reason_code='deferred')})
    doc = changed[1].legacy_passthrough.thaw(); row = doc['repair_lineage']['lineages'][0]
    row['dispositions'][-1]['terminal'].update(outcome='deferred', reason_code='deferred')
    row['lifecycle'] = 'deferred'
    with pytest.raises(FreshReviewError):
        validate_terminal(doc, row, row['dispositions'][-1], changed[3], changed[2].observed_candidates.thaw())


def test_duplicate_alias_cannot_resolve_without_a_canonical_target():
    from mission_kernel.finding_disposition import resolved_aliases
    from mission_kernel.fresh_review import FreshReviewError
    identifier = 'sha256:'+'a'*64
    with pytest.raises(FreshReviewError):
        resolved_aliases({identifier: ()}, set(), {identifier})
    assert resolved_aliases({}, {identifier}, {identifier}) == set()  # Nonviolations use direct validated results.


@pytest.mark.parametrize('verdict', ['deferred', 'not-violation', 'duplicate'])
def test_output_mapping_reference_rejects_null_after_digest_rebinding(rejected, verdict):
    from mission_kernel.finding_disposition import validate_terminal
    from mission_kernel.fresh_review import FreshReviewError
    outcome = 'deferred' if verdict == 'deferred' else 'rejected'
    changed = rebind(rejected, {
        'finding-disposition-output': lambda b: b.update(verdict=verdict, reason_code=verdict, mapping_ref=None),
        'finding-disposition': lambda b: b.update(outcome=outcome, reason_code=verdict, mapping_ref=None)})
    doc = changed[1].legacy_passthrough.thaw(); row = doc['repair_lineage']['lineages'][0]
    row['dispositions'][-1]['terminal'].update(outcome=outcome, reason_code=verdict)
    with pytest.raises(FreshReviewError):
        validate_terminal(doc, row, row['dispositions'][-1], changed[3], changed[2].observed_candidates.thaw())


@pytest.mark.parametrize('source_ref', [None, True, 1, {}])
def test_request_source_reference_uses_closed_decoder_errors(rejected, source_ref):
    from mission_kernel.finding_disposition import decode_request
    from mission_kernel.fresh_review import FreshReviewError
    request = rejected[2].request.thaw()
    request['source_ref'] = source_ref
    with pytest.raises(FreshReviewError):
        decode_request(request)


@pytest.mark.parametrize('evidence', [[None], [{}], [True]])
def test_output_evidence_reference_uses_closed_decoder_errors(rejected, evidence):
    from mission_kernel.finding_disposition import validate_terminal
    from mission_kernel.fresh_review import FreshReviewError
    changed = rebind(rejected, {'finding-disposition-output': lambda b: b.update(evidence=evidence)})
    doc = changed[1].legacy_passthrough.thaw(); row = doc['repair_lineage']['lineages'][0]
    with pytest.raises(FreshReviewError):
        validate_terminal(doc, row, row['dispositions'][-1], changed[3], changed[2].observed_candidates.thaw())


def test_superseded_repair_and_disposition_are_bound_in_both_directions(rejected):
    from mission_kernel.finding_disposition import validate_dispositions
    from mission_kernel.fresh_review import FreshReviewError
    row = rejected[1].repair.lineages[0].document.thaw()
    item = row['dispositions'][0]
    identifier = 'sha256:'+'b'*64
    item['supersedes_attempt_id'] = identifier
    attempt = dict(attempt_id=identifier, status='blocked', terminal=dict(reason='disposition-requested',
        operation_id=item['operation_id'], fencing_epoch=item['fencing_epoch']))
    row['attempts'] = [attempt]
    validate_dispositions(row)
    for fault in ('request-missing', 'attempt-missing', 'fence', 'intent'):
        changed = deepcopy(row)
        if fault == 'request-missing': changed['dispositions'] = []
        elif fault == 'attempt-missing': changed['attempts'] = []
        elif fault == 'fence': changed['attempts'][0]['terminal']['fencing_epoch'] += 1
        else: changed['attempts'][0]['intent'] = {}
        with pytest.raises(FreshReviewError):
            validate_dispositions(changed)


def test_absent_and_empty_disposition_history_preserve_old_state_resolution(reverified):
    from mission_kernel.repair_attempts import _store
    from mission_kernel.fresh_review_coverage import FreshReviewBindings
    from mission_kernel.repair_lineage import effective_unresolved_findings
    _, command, state = reverified
    bindings = FreshReviewBindings(command.result.thaw()['receipt']['contract_digest'], (),
        tuple(command.result.thaw()['candidate'].items()), (command.result,))
    doc = state.legacy_passthrough.thaw()
    for present in (False, True):
        if present: doc['repair_lineage']['lineages'][0]['dispositions'] = []
        restored = _store(state, doc)
        assert effective_unresolved_findings(restored.repair, restored.fresh_review, command.evidence,
            doc['acceptance_contract'], bindings) == ()


def test_projection_refuses_simultaneous_pending_repair_and_disposition(rejected):
    from mission_kernel.finding_disposition import validate_dispositions
    from mission_kernel.repair_attempts import absent
    from mission_kernel.fresh_review import FreshReviewError
    row = rejected[1].repair.lineages[0].document.thaw()
    item = row['dispositions'][0]
    item.update(stage='prepared', intent=absent('not-dispatched'), launch_ref=absent('not-launched'), terminal=absent('not-terminal'))
    row.update(lifecycle='repairing', attempts=[dict(status='pending', terminal=absent('not-terminal'))])
    with pytest.raises(FreshReviewError):
        validate_dispositions(row)


def test_blocked_summary_cannot_claim_an_independent_verdict(rejected):
    from mission_kernel.finding_disposition import validate_dispositions
    from mission_kernel.fresh_review import FreshReviewError
    row = rejected[1].repair.lineages[0].document.thaw()
    row['dispositions'][0]['terminal']['outcome'] = 'blocked'
    row['lifecycle'] = 'open'
    with pytest.raises(FreshReviewError):
        validate_dispositions(row)


def overflow_case(rejected, substitute=False):
    from dataclasses import replace
    from mission_kernel.finding_disposition import validate_terminal, DispositionArtifact
    from mission_kernel.fresh_review import canonical_bytes, canonical_digest, decode_projection
    from mission_kernel.repair_attempts import reference
    from mission_kernel.repair_lineage import origin_identity
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.fresh_review import FreshReviewError
    from .test_issue896_output_kernel import output_document, finding
    before, state, command, carrier = rejected
    doc = state.legacy_passthrough.thaw(); record = decode_projection(doc).requests[0]
    source = output_document(record)
    source['criterion_results'][0]['findings'] = [finding(i) for i in range(62)]
    original_ref = reference('fresh-review-output', canonical_bytes(source))
    original_ref['relative_path'] = 'evidence/fresh-review/'+original_ref['digest'][7:]+'.json'
    overflow = dict(kind='unimported-findings', request_id=record.request.request_id,
        lineage_id=command.request.thaw()['lineage_id'], output_digest=original_ref['digest'], lifecycle='rejected',
        last_candidate_change={'kind':'absent','reason':'no-resolution-binding'},
        disposition={'kind':'absent','reason':'disposition-not-enabled'})
    if substitute: source['criterion_results'][0]['findings'][0]['summary'] = 'Another output element.'
    source_ref = reference('fresh-review-output', canonical_bytes(source))
    source_ref['relative_path'] = 'evidence/fresh-review/'+source_ref['digest'][7:]+'.json'
    scope = dict(criterion_ids=['AC1'], requirement_ids=['R1'], prohibited_side_effect_ids=['AC1:0'],
        repro_digest=state.repair.lineages[0].document.thaw()['repro']['repro_digest'])
    mapping = dict(schema='mission-finding-disposition-mapping/1', source_digest=canonical_digest(source),
        entries=[dict(index=i, element_digest=canonical_digest(element), verdict='not-violation',
            target={'kind':'absent','reason':'no-target'}, scope=scope)
            for i, element in enumerate(source['criterion_results'][0]['findings'])])
    mapping_ref = reference('finding-disposition-mapping', canonical_bytes(mapping))
    additions = tuple(DispositionArtifact(freeze_json_value(ref), freeze_json_value(body))
        for ref, body in ((source_ref, source), (mapping_ref, mapping)))
    request = command.request.thaw()
    request.update(source_ref=source_ref, origin_digest=canonical_digest(origin_identity(overflow)))
    changed = rebind((before, state, command, replace(carrier, artifacts=carrier.artifacts+additions)), {
        'finding-disposition-request': lambda b: b.update(request),
        'finding-disposition-launch': lambda b: b.update(request_digest=canonical_digest(request)),
        'finding-disposition-output': lambda b: b.update(verdict='duplicate', reason_code='duplicate', mapping_ref=mapping_ref),
        'finding-disposition': lambda b: b.update(reason_code='duplicate', mapping_ref=mapping_ref)})
    doc = changed[1].legacy_passthrough.thaw()
    doc['fresh_review']['requests'][0]['status'] = 'failed'
    terminal = doc['fresh_review']['requests'][0]['result']
    for key in ('coverage_receipt', 'findings', 'independent'): terminal.pop(key)
    terminal.update(outcome='failed', reason='output-over-import-limit', output_ref=original_ref, output_digest=original_ref['digest'])
    overflow['dispositions'] = doc['repair_lineage']['lineages'][0]['dispositions']
    overflow['dispositions'][0]['terminal']['reason_code'] = 'duplicate'
    doc['repair_lineage']['lineages'] = [overflow]
    args = (doc, overflow, overflow['dispositions'][0], changed[3], command.observed_candidates.thaw())
    return args


@pytest.mark.parametrize('substitute', [False, True])
def test_overflow_rejection_is_bound_to_retained_output_and_complete_mapping(rejected, substitute):
    from mission_kernel.finding_disposition import validate_terminal
    from mission_kernel.fresh_review import FreshReviewError
    args = overflow_case(rejected, substitute)
    if substitute:
        with pytest.raises(FreshReviewError, match='disposition-origin-mismatch'): validate_terminal(*args)
    else:
        assert validate_terminal(*args) == ('rejected', ())


@pytest.mark.parametrize('fault', ['kind', 'path', 'unknown', 'hard-cap', 'launch-cap'])
def test_output_reference_is_closed_and_within_both_enforced_limits(rejected, fault):
    from mission_kernel.finding_disposition import validate_terminal
    from mission_kernel.fresh_review import FreshReviewError
    changes = {}
    refs = {}
    if fault == 'hard-cap': changes['finding-disposition-output'] = lambda b: b.update(reason='x'*262144)
    elif fault == 'launch-cap': changes['finding-disposition-launch'] = lambda b: b['enforced_budget'].update(max_output_bytes=1)
    else:
        refs['finding-disposition-output'] = lambda ref: ref.update(
            {'kind':'repair-plan'} if fault == 'kind' else {'relative_path':'outside/output.json'} if fault == 'path' else {'resolved':True})
    changed = rebind(rejected, changes, refs)
    doc = changed[1].legacy_passthrough.thaw(); row = doc['repair_lineage']['lineages'][0]
    with pytest.raises(FreshReviewError):
        validate_terminal(doc, row, row['dispositions'][-1], changed[3], changed[2].observed_candidates.thaw())


@pytest.mark.parametrize('entry', ['terminal', 'mapping'])
@pytest.mark.parametrize('fault', ['scope', 'repro'])
def test_original_finding_defeats_rebound_summary_authority(rejected, entry, fault):
    from mission_kernel.finding_disposition import validate_terminal, validate_mapping, artifact_body
    from mission_kernel.repair_lineage import origin_identity
    from mission_kernel.fresh_review import canonical_digest, FreshReviewError
    row = rejected[1].repair.lineages[0].document.thaw()
    if fault == 'scope': row.update(requirement_ids=[], prohibited_side_effect_ids=[])
    else: row['repro']['repro_digest'] = 'sha256:'+'f'*64
    request = rejected[2].request.thaw(); request['origin_digest'] = canonical_digest(origin_identity(row))
    scope = dict(criterion_ids=[row['criterion_id']], requirement_ids=row['requirement_ids'],
        prohibited_side_effect_ids=row['prohibited_side_effect_ids'], repro_digest=row['repro']['repro_digest'])
    changed = rebind(rejected, {
        'finding-disposition-request': lambda b: b.update(request),
        'finding-disposition-launch': lambda b: b.update(request_digest=canonical_digest(request)),
        'finding-disposition-output': lambda b: b.update(scope=scope)})
    doc = changed[1].legacy_passthrough.thaw()
    row['dispositions'] = doc['repair_lineage']['lineages'][0]['dispositions']
    doc['repair_lineage']['lineages'] = [row]
    source = artifact_body(changed[3], row['finding_ref'])
    mapping = dict(schema='mission-finding-disposition-mapping/1', source_digest=canonical_digest(source),
        entries=[dict(index=0, element_digest=canonical_digest(source), verdict='not-violation',
            target={'kind':'absent','reason':'no-target'}, scope=scope)])
    with pytest.raises(FreshReviewError):
        if entry == 'terminal': validate_terminal(doc, row, row['dispositions'][0], changed[3], changed[2].observed_candidates.thaw())
        else: validate_mapping(doc, row, source, mapping, {}, changed[3])


@pytest.mark.parametrize('fault', ['scope', 'repro'])
def test_duplicate_target_is_authenticated_even_when_its_scope_contains_source(rejected, fault):
    from mission_kernel.finding_disposition import validate_mapping
    from mission_kernel.fresh_review import canonical_digest, FreshReviewError
    from .test_issue896_output_kernel import finding
    doc = rejected[1].legacy_passthrough.thaw(); target = doc['repair_lineage']['lineages'][0]
    hypothesis = finding()
    source = dict(criterion_results=[dict(findings=[hypothesis])])
    overflow = dict(kind='unimported-findings', request_id=target['origin']['request_id'], lineage_id='sha256:'+'d'*64)
    scope = dict(criterion_ids=['AC1'], requirement_ids=target['requirement_ids'],
        prohibited_side_effect_ids=target['prohibited_side_effect_ids'], repro_digest=target['repro']['repro_digest'])
    mapping = dict(schema='mission-finding-disposition-mapping/1', source_digest=canonical_digest(source),
        entries=[dict(index=0, element_digest=canonical_digest(hypothesis), verdict='duplicate', target=target['lineage_id'], scope=scope)])
    assert validate_mapping(doc, overflow, source, mapping, {}, rejected[3]) == (target['lineage_id'],)
    if fault == 'scope': target['requirement_ids'] = [*target['requirement_ids'], 'other-requirement']
    else: target['repro']['repro_input_ref'] = 'sha256:'+'f'*64
    with pytest.raises(FreshReviewError): validate_mapping(doc, overflow, source, mapping, {}, rejected[3])


@pytest.mark.parametrize('field', ['mission_id', 'session_id'])
@pytest.mark.parametrize('kind', ['finding', 'unimported-findings'])
def test_request_identity_cannot_be_rebound_away_from_its_d_origin(rejected, field, kind):
    from mission_kernel.finding_disposition import validate_terminal, request_id
    from mission_kernel.repair_lineage import origin_identity
    from mission_kernel.fresh_review import canonical_digest, FreshReviewError
    from mission_kernel.finding_disposition import artifact_body
    if kind == 'finding':
        doc = rejected[1].legacy_passthrough.thaw(); row = doc['repair_lineage']['lineages'][0]
        carrier = rejected[3]; candidates = rejected[2].observed_candidates.thaw()
    else:
        doc, row, _, carrier, candidates = overflow_case(rejected)
    request = artifact_body(carrier, row['dispositions'][0]['request_ref'])
    request[field] = 'another-identity'
    request['request_id'] = request_id(request)
    identifier = request['request_id']; operation = 'disposition-dispatch:'+identifier[7:]
    changed = rebind((*rejected[:3], carrier), {
        'finding-disposition-request': lambda b: b.update(request),
        'finding-disposition-launch': lambda b: b.update(request_id=identifier, request_digest=canonical_digest(request),
            nonce=identifier[7:39], operation_id=operation),
        'finding-disposition-output': lambda b: b.update(request_id=identifier),
        'finding-disposition': lambda b: (b.update(request_id=identifier),
            b['dispatch'].update(operation_id=operation))}, raw_document=doc)
    doc = changed[1]
    row['dispositions'] = doc['repair_lineage']['lineages'][0]['dispositions']
    item = row['dispositions'][0]; item.update(request_id=identifier)
    item['intent']['operation_id'] = operation
    doc['repair_lineage']['lineages'] = [row]
    with pytest.raises(FreshReviewError, match='disposition-origin-mismatch'):
        validate_terminal(doc, row, item, changed[3], candidates)
