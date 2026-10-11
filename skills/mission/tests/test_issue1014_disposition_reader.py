"""Retained disposition proofs are reread, never promoted to completion authority."""
from dataclasses import FrozenInstanceError
import pytest
from .test_issue907_disposition import rejected
from .test_issue906_repair_attempts import repair_state, published, gate_state, completed_carrier
from mission_kernel.fresh_review import canonical_bytes


def materialize(root, evidence):
    for artifact in evidence.artifacts:
        path = root / artifact.reference.thaw()['relative_path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(canonical_bytes(artifact.content.thaw()))


def test_reader_collects_retained_proof_without_activating_completion(rejected, tmp_path):
    from mission_application.disposition_evidence import observe_disposition_results
    from mission_kernel.finding_disposition import validate_terminal
    from mission_kernel.fresh_review_coverage import FreshReviewBindings
    from mission_kernel.repair_lineage import effective_unresolved_findings
    _, state, command, saved = rejected
    document = state.legacy_passthrough.thaw()
    materialize(tmp_path, saved)
    carrier = observe_disposition_results(document, root=tmp_path, candidates=command.observed_candidates.thaw())
    row = document['repair_lineage']['lineages'][0]
    assert validate_terminal(document, row, row['dispositions'][-1], carrier,
        command.observed_candidates.thaw()) == ('rejected', ())
    with pytest.raises(FrozenInstanceError):
        carrier.artifacts = ()
    bindings = FreshReviewBindings(command.request.thaw()['contract_digest'], (), (), (), command.observed_candidates)
    assert effective_unresolved_findings(state.repair, state.fresh_review, command.evidence,
        document['acceptance_contract'], bindings)


@pytest.mark.parametrize('replacement', ['same-bytes', 'changed-bytes'])
def test_read_set_rejects_request_replacement_after_its_initial_read(rejected, tmp_path, monkeypatch, replacement):
    import mission_application.disposition_evidence as reader
    from mission_kernel.fresh_review import FreshReviewError
    from mission_persistence.strict_reader import read_stable_bytes_beneath
    document = rejected[1].legacy_passthrough.thaw()
    item = document['repair_lineage']['lineages'][0]['dispositions'][-1]
    materialize(tmp_path, rejected[3])
    request_path = tmp_path / item['request_ref']['relative_path']
    swapped = False

    def observe(root, path, *, limit):
        nonlocal swapped
        result = read_stable_bytes_beneath(root, path, limit=limit)
        if path == item['terminal']['ref']['relative_path'] and not swapped:
            swapped = True
            substitute = request_path.with_suffix('.replacement')
            substitute.write_bytes(request_path.read_bytes() if replacement == 'same-bytes' else b'{}')
            substitute.replace(request_path)
        return result

    monkeypatch.setattr(reader, 'read_stable_bytes_beneath', observe)
    with pytest.raises(FreshReviewError, match='disposition-evidence-unavailable'):
        reader.observe_disposition_results(document, root=tmp_path, candidates=rejected[2].observed_candidates.thaw())


@pytest.mark.parametrize('history', [None, False, {}, ''])
def test_unknown_history_shape_cannot_look_like_legacy_absence(rejected, tmp_path, history):
    from mission_application.disposition_evidence import observe_disposition_results
    from mission_kernel.fresh_review import FreshReviewError
    document = rejected[1].legacy_passthrough.thaw()
    document['repair_lineage']['lineages'][0]['dispositions'] = history
    with pytest.raises(FreshReviewError):
        observe_disposition_results(document, root=tmp_path, candidates={})


def test_input_must_fit_the_effective_launch_packet_cap(rejected, tmp_path):
    from .test_issue907_disposition import rebind
    from mission_application.disposition_evidence import observe_disposition_results
    from mission_kernel.fresh_review import FreshReviewError
    changed = rebind(rejected, {'finding-disposition-launch':
        lambda body: body['enforced_budget'].update(max_packet_bytes=1)})
    materialize(tmp_path, changed[3])
    with pytest.raises(FreshReviewError):
        observe_disposition_results(changed[1].legacy_passthrough.thaw(), root=tmp_path,
            candidates=changed[2].observed_candidates.thaw())


@pytest.mark.parametrize('kind', ['finding-disposition-request', 'finding-disposition-input',
    'fresh-review-finding', 'finding-disposition-launch', 'finding-disposition', 'finding-disposition-output'])
@pytest.mark.parametrize('fault', ['missing', 'modified', 'partial', 'symlink', 'hardlink'])
def test_each_retained_path_requires_original_regular_stable_bytes(rejected, tmp_path, kind, fault):
    from mission_application.disposition_evidence import observe_disposition_results
    from mission_kernel.fresh_review import FreshReviewError
    materialize(tmp_path, rejected[3])
    ref = next(a.reference.thaw() for a in rejected[3].artifacts if a.reference.thaw()['kind'] == kind)
    path = tmp_path / ref['relative_path']
    if fault == 'missing':
        path.unlink()
    elif fault == 'modified':
        path.write_bytes(b'{"tampered":true}')
    elif fault == 'partial':
        path.write_bytes(path.read_bytes()[:-1])
    else:
        other = tmp_path / 'other.json'
        if fault == 'symlink':
            other.write_bytes(path.read_bytes()); path.unlink(); path.symlink_to(other)
        else:
            other.hardlink_to(path)
    with pytest.raises(FreshReviewError):
        observe_disposition_results(rejected[1].legacy_passthrough.thaw(), root=tmp_path,
            candidates=rejected[2].observed_candidates.thaw())


@pytest.mark.parametrize('fault', ['kind', 'path', 'field', 'size', 'digest'])
def test_named_reference_contract_is_checked_before_filesystem_access(rejected, tmp_path, fault):
    from mission_application.disposition_evidence import observe_disposition_results
    from mission_kernel.fresh_review import FreshReviewError
    document = rejected[1].legacy_passthrough.thaw()
    ref = document['repair_lineage']['lineages'][0]['dispositions'][-1]['request_ref']
    mutations = dict(kind={'kind':'repair-plan'}, path={'relative_path':'outside/request.json'},
        field={'resolved':True}, size={'size':True}, digest={'digest':'sha256:'+'f'*64})
    ref.update(mutations[fault])
    with pytest.raises(FreshReviewError):
        observe_disposition_results(document, root=tmp_path, candidates=rejected[2].observed_candidates.thaw())


@pytest.mark.parametrize('size', [1048576, 1048577])
def test_only_input_packet_can_use_the_one_mebibyte_limit(rejected, tmp_path, size):
    from .test_issue907_disposition import rebind
    from mission_kernel.repair_attempts import reference
    from mission_kernel.fresh_review import canonical_digest, FreshReviewError
    from mission_application.disposition_evidence import observe_disposition_results
    body = {'payload':'x'*(size-len(canonical_bytes({'payload':''})))}
    ref = reference('finding-disposition-input', canonical_bytes(body))
    request = rejected[2].request.thaw(); request['input_ref'] = ref
    changed = rebind(rejected, {
        'finding-disposition-input': lambda b: b.update(body),
        'finding-disposition-request': lambda b: b.update(request),
        'finding-disposition-launch': lambda b: b.update(request_digest=canonical_digest(request), received_input_digest=ref['digest']),
        'finding-disposition-output': lambda b: b.update(input_digest=ref['digest'])})
    materialize(tmp_path, changed[3])
    if size > 1048576:
        with pytest.raises(FreshReviewError):
            observe_disposition_results(changed[1].legacy_passthrough.thaw(), root=tmp_path,
                candidates=changed[2].observed_candidates.thaw())
    else:
        assert observe_disposition_results(changed[1].legacy_passthrough.thaw(), root=tmp_path,
            candidates=changed[2].observed_candidates.thaw()).artifacts


@pytest.mark.parametrize('history', ['absent', 'empty'])
def test_legacy_histories_need_no_disposition_artifacts(rejected, tmp_path, history):
    from mission_application.disposition_evidence import observe_disposition_results
    document = rejected[0].legacy_passthrough.thaw()
    if history == 'empty': document['repair_lineage']['lineages'][0]['dispositions'] = []
    assert observe_disposition_results(document, root=tmp_path, candidates={}).artifacts == ()


def test_pure_proof_revalidates_carrier_instead_of_trusting_its_construction(rejected):
    from .test_issue907_disposition import rebind
    from mission_kernel.finding_disposition import validate_disposition_evidence
    from mission_kernel.fresh_review import FreshReviewError
    document = rejected[1].legacy_passthrough.thaw()
    candidates = rejected[2].observed_candidates.thaw()
    assert validate_disposition_evidence(document, rejected[3], candidates) is None
    forged = rebind(rejected, {'finding-disposition-launch':
        lambda body: body['enforced_budget'].update(max_packet_bytes=1)})
    with pytest.raises(FreshReviewError):
        validate_disposition_evidence(forged[1].legacy_passthrough.thaw(), forged[3], candidates)


def overflow_proof(rejected):
    """Reuse the D overflow payload, adding the projection's real origin ID."""
    from .test_issue907_disposition import overflow_case, rebind
    from mission_kernel.finding_disposition import artifact_body, request_id
    from mission_kernel.repair_lineage import _overflow_id, origin_identity
    from mission_kernel.fresh_review import decode_projection, canonical_digest
    document, row, item, evidence, candidates = overflow_case(rejected)
    record = decode_projection(document).requests[0]
    row['lineage_id'] = _overflow_id(record.request, row['output_digest'])
    request = artifact_body(evidence, item['request_ref'])
    request.update(lineage_id=row['lineage_id'], origin_digest=canonical_digest(origin_identity(row)))
    request['request_id'] = request_id(request)
    identifier = request['request_id']; operation = 'disposition-dispatch:'+identifier[7:]
    item['request_id'] = identifier; item['intent']['operation_id'] = operation
    changed = rebind((*rejected[:3], evidence), {
        'finding-disposition-request': lambda b: b.update(request),
        'finding-disposition-launch': lambda b: b.update(request_id=identifier, request_digest=canonical_digest(request),
            nonce=identifier[7:39], operation_id=operation),
        'finding-disposition-output': lambda b: b.update(request_id=identifier),
        'finding-disposition': lambda b: (b.update(request_id=identifier), b['dispatch'].update(operation_id=operation))},
        raw_document=document)
    return changed[1], changed[3], candidates


@pytest.mark.parametrize('fault', ['none', 'missing', 'partial', 'index', 'element'])
def test_overflow_reader_requires_the_original_complete_index_mapping(rejected, tmp_path, fault):
    from .test_issue907_disposition import rebind
    from mission_application.disposition_evidence import observe_disposition_results
    from mission_kernel.fresh_review import FreshReviewError
    document, evidence, candidates = overflow_proof(rejected)
    if fault in ('partial', 'index', 'element'):
        def mutate(body):
            if fault == 'partial': body['entries'].pop()
            elif fault == 'index': body['entries'][1]['index'] = 0
            else: body['entries'][0]['element_digest'] = 'sha256:'+'f'*64
        from dataclasses import replace
        ordered = replace(evidence, artifacts=tuple(sorted(evidence.artifacts, key=lambda a:
            {'finding-disposition-mapping':0, 'finding-disposition-output':1, 'finding-disposition':2}.get(a.reference.thaw()['kind'], -1))))
        changed = rebind((*rejected[:3], ordered), {'finding-disposition-mapping': mutate}, raw_document=document)
        document, evidence = changed[1], changed[3]
    materialize(tmp_path, evidence)
    if fault == 'missing':
        ref = next(a.reference.thaw() for a in evidence.artifacts if a.reference.thaw()['kind'] == 'finding-disposition-mapping')
        (tmp_path / ref['relative_path']).unlink()
    if fault == 'none':
        assert observe_disposition_results(document, root=tmp_path, candidates=candidates).artifacts
    else:
        with pytest.raises(FreshReviewError, match='disposition-evidence-unavailable' if fault == 'missing' else 'disposition-mapping-incomplete'):
            observe_disposition_results(document, root=tmp_path, candidates=candidates)


def duplicate_proof(rejected):
    from copy import deepcopy
    from dataclasses import replace
    from .test_issue907_disposition import rebind
    from mission_kernel.finding_disposition import artifact_body, DispositionArtifact
    from mission_kernel.repair_lineage import lineage_id, origin_identity
    from mission_kernel.repair_attempts import reference
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.fresh_review import canonical_digest
    document = rejected[1].legacy_passthrough.thaw()
    source = document['repair_lineage']['lineages'][0]
    body = artifact_body(rejected[3], source['finding_ref'])
    target_body = dict(body, finding_id='canonical-finding')
    target_ref = reference('fresh-review-finding', canonical_bytes(target_body))
    target_ref['relative_path'] = 'evidence/fresh-review/'+target_ref['digest'][7:]+'.json'
    target = deepcopy(source)
    target['origin']['finding_id'] = target_body['finding_id']
    target.update(lineage_id=lineage_id(target['origin'], target['criterion_id']), finding_ref=target_ref,
        lifecycle='open', dispositions=[])
    target['repro']['repro_input_ref'] = target_ref['digest']
    if 'digest' in target['replay']: target['replay']['evidence_ref'] = target_ref['digest']
    end = document['fresh_review']['requests'][0]['result']
    end['findings'].append(target_ref)
    source['terminal_digest'] = target['terminal_digest'] = canonical_digest(end)
    document['repair_lineage']['lineages'].append(target)
    scope = dict(criterion_ids=[source['criterion_id']], requirement_ids=source['requirement_ids'],
        prohibited_side_effect_ids=source['prohibited_side_effect_ids'], repro_digest=source['repro']['repro_digest'])
    mapping = dict(schema='mission-finding-disposition-mapping/1', source_digest=canonical_digest(body), entries=[dict(
        index=0, element_digest=canonical_digest(body), verdict='duplicate', target=target['lineage_id'], scope=scope)])
    mapping_ref = reference('finding-disposition-mapping', canonical_bytes(mapping))
    request = rejected[2].request.thaw(); request['origin_digest'] = canonical_digest(origin_identity(source))
    source['dispositions'][0]['duplicate_targets'] = [target['lineage_id']]
    source['dispositions'][0]['terminal']['reason_code'] = 'duplicate'
    additions = tuple(DispositionArtifact(freeze_json_value(ref), freeze_json_value(value))
        for ref, value in ((target_ref, target_body), (mapping_ref, mapping)))
    ordered = replace(rejected[3], artifacts=rejected[3].artifacts[:4]+additions+rejected[3].artifacts[4:])
    changed = rebind((*rejected[:3], ordered), {
        'finding-disposition-request': lambda b: b.update(request),
        'finding-disposition-launch': lambda b: b.update(request_digest=canonical_digest(request)),
        'finding-disposition-output': lambda b: b.update(verdict='duplicate', reason_code='duplicate', mapping_ref=mapping_ref),
        'finding-disposition': lambda b: b.update(reason_code='duplicate', mapping_ref=mapping_ref)}, raw_document=document)
    return changed[1], changed[3], rejected[2].observed_candidates.thaw()


@pytest.mark.parametrize('fault', ['none', 'scope', 'repro', 'missing-target', 'summary-target'])
def test_duplicate_target_is_reread_and_authenticated_without_resolving_it(rejected, tmp_path, fault):
    from mission_application.disposition_evidence import observe_disposition_results
    from mission_kernel.fresh_review import FreshReviewError
    document, evidence, candidates = duplicate_proof(rejected)
    target = document['repair_lineage']['lineages'][1]
    if fault == 'scope': target['requirement_ids'].append('another-obligation')
    elif fault == 'repro': target['repro']['verifier_definition_digest'] = 'sha256:'+'f'*64
    elif fault == 'missing-target': document['repair_lineage']['lineages'].pop()
    elif fault == 'summary-target': document['repair_lineage']['lineages'][0]['dispositions'][0]['duplicate_targets'] = []
    materialize(tmp_path, evidence)
    if fault == 'none':
        assert observe_disposition_results(document, root=tmp_path, candidates=candidates).artifacts
        assert target['lifecycle'] == 'open'
    else:
        with pytest.raises(FreshReviewError):
            observe_disposition_results(document, root=tmp_path, candidates=candidates)


@pytest.mark.parametrize('latest', ['pending', 'blocked', 'deferred'])
def test_latest_unresolved_disposition_never_falls_back_to_old_rejection(rejected, tmp_path, latest):
    from copy import deepcopy
    from mission_application.disposition_evidence import observe_disposition_results
    from mission_kernel.repair_attempts import absent
    from mission_kernel.fresh_review import FreshReviewError
    document = rejected[1].legacy_passthrough.thaw(); row = document['repair_lineage']['lineages'][0]
    newer = deepcopy(row['dispositions'][-1]); newer.update(request_id='sha256:'+'e'*64, generation=3)
    if latest == 'pending':
        newer.update(stage='prepared', intent=absent('not-dispatched'), launch_ref=absent('not-launched'), terminal=absent('not-terminal'))
    else:
        newer['intent']['operation_id'] = 'disposition-dispatch:'+newer['request_id'][7:]
        newer['terminal'].update(generation=4, outcome=latest,
            reason_code='effects-unavailable' if latest == 'blocked' else 'deferred')
        if latest == 'blocked': newer['terminal']['ref'] = absent('effects-unavailable')
    row['dispositions'].append(newer); row['lifecycle'] = 'deferred' if latest == 'deferred' else 'open'
    materialize(tmp_path, rejected[3])
    with pytest.raises(FreshReviewError, match='disposition-unresolved'):
        observe_disposition_results(document, root=tmp_path, candidates=rejected[2].observed_candidates.thaw())


@pytest.mark.parametrize('kind,mutate', [
    ('finding-disposition-launch', lambda b: b.update(context_mode='shared')),
    ('finding-disposition-output', lambda b: b['scope'].update(requirement_ids=[])),
    ('finding-disposition', lambda b: b.update(operation_id='unbound-operation')),
])
def test_reader_invokes_pure_origin_independence_and_terminal_validation(rejected, tmp_path, kind, mutate):
    from .test_issue907_disposition import rebind
    from mission_application.disposition_evidence import observe_disposition_results
    from mission_kernel.fresh_review import FreshReviewError
    changed = rebind(rejected, {kind:mutate})
    materialize(tmp_path, changed[3])
    with pytest.raises(FreshReviewError):
        observe_disposition_results(changed[1].legacy_passthrough.thaw(), root=tmp_path,
            candidates=changed[2].observed_candidates.thaw())


@pytest.mark.parametrize('candidates', [{}, {'unknown-command':'sha256:'+'f'*64}])
def test_reader_rejects_partial_and_unknown_current_candidate_sets(rejected, tmp_path, candidates):
    from mission_application.disposition_evidence import observe_disposition_results
    from mission_kernel.fresh_review import FreshReviewError
    materialize(tmp_path, rejected[3])
    with pytest.raises(FreshReviewError):
        observe_disposition_results(rejected[1].legacy_passthrough.thaw(), root=tmp_path, candidates=candidates)


def test_alias_cycle_is_rejected_after_both_edges_have_authenticated_proofs(rejected, tmp_path):
    from copy import deepcopy
    from dataclasses import replace
    from mission_kernel.finding_disposition import artifact_body, DispositionArtifact, request_id
    from mission_kernel.repair_lineage import origin_identity
    from mission_kernel.repair_attempts import reference
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.fresh_review import canonical_digest, FreshReviewError
    from mission_application.disposition_evidence import observe_disposition_results
    document, evidence, candidates = duplicate_proof(rejected)
    source, target = document['repair_lineage']['lineages']
    item = source['dispositions'][0]
    terminal = artifact_body(evidence, item['terminal']['ref'])
    output = artifact_body(evidence, terminal['output_ref'])
    launch = artifact_body(evidence, item['launch_ref'])
    request = artifact_body(evidence, item['request_ref'])
    target_body = artifact_body(evidence, target['finding_ref'])
    request.update(lineage_id=target['lineage_id'], origin_digest=canonical_digest(origin_identity(target)),
        source_ref=target['finding_ref'], operation_id='target-prepare')
    request['request_id'] = request_id(request)
    identifier = request['request_id']; operation = 'disposition-dispatch:'+identifier[7:]
    dispatch = dict(item['intent'], operation_id=operation)
    request_ref = reference('finding-disposition-request', canonical_bytes(request))
    launch.update(request_id=identifier, request_digest=canonical_digest(request), nonce=identifier[7:39], operation_id=operation)
    launch_ref = reference('finding-disposition-launch', canonical_bytes(launch))
    mapping = dict(schema='mission-finding-disposition-mapping/1', source_digest=canonical_digest(target_body), entries=[dict(
        index=0, element_digest=canonical_digest(target_body), verdict='duplicate', target=source['lineage_id'], scope=output['scope'])])
    mapping_ref = reference('finding-disposition-mapping', canonical_bytes(mapping))
    output.update(request_id=identifier, mapping_ref=mapping_ref)
    output_ref = reference('finding-disposition-output', canonical_bytes(output))
    terminal.update(request_id=identifier, request_ref=request_ref, launch_ref=launch_ref, output_ref=output_ref,
        mapping_ref=mapping_ref, dispatch=dispatch, operation_id='target-terminal')
    terminal_ref = reference('finding-disposition', canonical_bytes(terminal))
    target_item = deepcopy(item)
    target_item.update(request_id=identifier, request_ref=request_ref, operation_id=request['operation_id'],
        intent=dispatch, launch_ref=launch_ref, duplicate_targets=[source['lineage_id']])
    target_item['terminal'].update(ref=terminal_ref, operation_id='target-terminal')
    target.update(dispositions=[target_item], lifecycle='rejected')
    additions = tuple(DispositionArtifact(freeze_json_value(ref), freeze_json_value(body)) for ref, body in (
        (request_ref, request), (launch_ref, launch), (mapping_ref, mapping), (output_ref, output), (terminal_ref, terminal)))
    evidence = replace(evidence, artifacts=evidence.artifacts+additions)
    materialize(tmp_path, evidence)
    # Both source and target pass terminal proof validation; only graph closure
    # rejects this pair, so an earlier malformed receipt cannot mask the cycle.
    from mission_kernel.finding_disposition import validate_terminal
    assert validate_terminal(document, source, item, evidence, candidates)[1] == (target['lineage_id'],)
    assert validate_terminal(document, target, target_item, evidence, candidates)[1] == (source['lineage_id'],)
    with pytest.raises(FreshReviewError, match='disposition-alias-invalid'):
        observe_disposition_results(document, root=tmp_path, candidates=candidates)
