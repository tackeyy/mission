"""Pure D2 output contracts and two-stage judgement; no state writer or IO.

An accepted output contains reviewer hypotheses, not verified findings. Replay
execution and atomic evidence publication belong to the import writer; kernel
checks ledger references. The failed-output writer uses this preflight;
completed publication and replay execution remain separate.
Host observation is separate from child-authored bytes. The child-facing fence
is the saved dispatch fence; the publisher must also enforce its current lease.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
import json

from .fresh_review import (
    FreshReviewError, FreshReviewRecord, FRESH_REVIEW_EVIDENCE_MAX_BYTES,
    FRESH_REVIEW_FINDINGS_LIMIT, _closed, _identifier, _digest, _integer,
    _json_builtins, _unique_strings, canonical_bytes, canonical_digest,
    decode_request, request_document,
)
from .fresh_review_dispatch import validate_launch
from .fresh_review_receipts import BudgetUsed, TerminalOutcome, TerminalReason
from .json_codec import freeze_json_value
from .model import FrozenJsonObject
from verifier_command import VerifierPolicyError, validate_command, validate_command_links
from acceptance_contract import AcceptanceContractError, canonical_contract_digest, validate as validate_contract

OUTPUT_SCHEMA = 'mission-fresh-review-output/1'
_BINDINGS = ('request_id', 'nonce', 'mission_id', 'session_id', 'requirement_digest',
             'contract_digest', 'verifier_policy_digest', 'candidate_digest', 'input_digest',
             'adapter_registration_digest', 'iteration')


@dataclass(frozen=True)
class FindingHypothesis:
    finding_id: str
    criterion_id: str
    requirement_ids: tuple[str, ...]
    prohibited_side_effect_ids: tuple[str, ...]
    severity: str
    summary: str
    command_id: str
    repro_input: FrozenJsonObject
    actual: FrozenJsonObject
    expected: FrozenJsonObject
    replay_evidence_ref: None


@dataclass(frozen=True)
class CriterionSearchResult:
    criterion_id: str
    status: str
    reason_code: str
    findings: tuple[FindingHypothesis, ...]


@dataclass(frozen=True)
class RequirementCoverageResult:
    requirement_id: str
    classification_confirmed: bool
    criterion_ids: tuple[str, ...]
    status: str
    reason_code: str
    reason: str


@dataclass(frozen=True)
class FreshReviewOutput:
    schema: str
    request_id: str
    nonce: str
    request_digest: str
    mission_id: str
    session_id: str
    requirement_digest: str
    contract_digest: str
    verifier_policy_digest: str
    candidate_digest: str
    input_digest: str
    adapter_registration_digest: str
    iteration: int
    criterion_results: tuple[CriterionSearchResult, ...]
    coverage: tuple[RequirementCoverageResult, ...]


@dataclass(frozen=True)
class FreshReviewOutputDecision:
    """An import preflight, not a terminal receipt or permission to complete.

    diagnostic_bytes contains the complete received bytes, including invalid JSON.
    Bytes exceeding the evidence budget have no diagnostic pair; no prefix is
    presented as the output. Its digest always describes the complete output.
    A writer must store it with output-over-import-limit in the same terminal
    commit. No finding or coverage effects are authorized by a failed decision.
    """
    outcome: TerminalOutcome
    reason: TerminalReason
    output: FreshReviewOutput | None
    diagnostic_bytes: bytes | None
    diagnostic_digest: str | None
    independent: bool


@dataclass(frozen=True)
class OutputCoverageDecision:
    status: str
    open_requirement_ids: tuple[str, ...]
    open_finding_ids: tuple[str, ...]


def _text(value):
    if type(value) is not str or not value.strip() or '\x00' in value:
        raise FreshReviewError('fresh-review-output-invalid')
    canonical_bytes(value)
    return value


def _choice(value, choices):
    if type(value) is not str or value not in choices:
        raise FreshReviewError('fresh-review-output-invalid')
    return value


def _object(value):
    if type(value) is not dict or not value:
        raise FreshReviewError('fresh-review-output-invalid')
    return freeze_json_value(value)


def _hypothesis(value, criterion_id, *, published=False):
    _json_builtins(value, 'output-invalid')
    canonical_bytes(value)
    raw = dict(_closed(value, FindingHypothesis.__dataclass_fields__, 'fresh-review-output-invalid'))
    for key in ('finding_id', 'criterion_id', 'command_id'):
        _identifier(raw[key])
    if raw['criterion_id'] != criterion_id or raw['replay_evidence_ref'] is not None:
        raise FreshReviewError('fresh-review-output-invalid')
    for key in ('requirement_ids', 'prohibited_side_effect_ids'):
        raw[key] = _unique_strings(raw[key], _identifier, nonempty=False)
    _choice(raw['severity'], ('High', 'Medium', 'Low'))
    _text(raw['summary'])
    repro = _closed(raw['repro_input'], ('artifact_kind', 'content'), 'fresh-review-output-invalid')
    _text(repro['artifact_kind'])
    if type(repro['content']) is not str:
        raise FreshReviewError('fresh-review-output-invalid')
    raw['repro_input'] = freeze_json_value(repro)
    # Published blocked findings have no observation when replay did not run.
    # Child output still requires a nonempty authored counterexample claim.
    raw['actual'] = (freeze_json_value(raw['actual']) if published and type(raw['actual']) is dict
                     else _object(raw['actual']))
    raw['expected'] = _object(raw['expected'])
    return FindingHypothesis(**raw)


def decode_output(value):
    """Decode a closed hypothesis envelope without trusting its claims.

    Import limits are applied by inspect_output after schema, binding and budget.

    The later importer must validate all ledger IDs and expected references
    against the frozen contract and replace authored actual with replay facts.
    """
    _json_builtins(value, 'output-invalid')
    raw = dict(_closed(value, FreshReviewOutput.__dataclass_fields__, 'fresh-review-output-invalid'))
    canonical_bytes(value)
    if raw['schema'] != OUTPUT_SCHEMA:
        raise FreshReviewError('fresh-review-output-invalid')
    for key in _BINDINGS + ('request_digest',):
        if key == 'iteration':
            _integer(raw[key], 'fresh-review-output-invalid')
        elif key.endswith('_digest'):
            _digest(raw[key])
        else:
            _identifier(raw[key])
    if type(raw['criterion_results']) is not list or not raw['criterion_results']:
        raise FreshReviewError('fresh-review-output-invalid')
    results, criterion_ids, finding_ids = [], set(), set()
    for item in raw['criterion_results']:
        _closed(item, CriterionSearchResult.__dataclass_fields__, 'fresh-review-output-invalid')
        identifier = _identifier(item['criterion_id'])
        if identifier in criterion_ids or type(item['findings']) is not list:
            raise FreshReviewError('fresh-review-output-invalid')
        criterion_ids.add(identifier)
        _choice(item['status'], ('searched', 'blocked'))
        _identifier(item['reason_code'])
        if item['status'] == 'blocked' and item['reason_code'] == 'none':
            raise FreshReviewError('fresh-review-output-invalid')
        findings = tuple(_hypothesis(finding, identifier) for finding in item['findings'])
        for finding in findings:
            if finding.finding_id in finding_ids:
                raise FreshReviewError('fresh-review-output-invalid')
            finding_ids.add(finding.finding_id)
        results.append(CriterionSearchResult(identifier, item['status'], item['reason_code'], findings))
    if type(raw['coverage']) is not list or not raw['coverage']:
        raise FreshReviewError('fresh-review-output-invalid')
    coverage, requirement_ids = [], set()
    for item in raw['coverage']:
        entry = dict(_closed(item, RequirementCoverageResult.__dataclass_fields__, 'fresh-review-output-invalid'))
        identifier = _identifier(entry['requirement_id'])
        if identifier in requirement_ids or type(entry['classification_confirmed']) is not bool:
            raise FreshReviewError('fresh-review-output-invalid')
        requirement_ids.add(identifier)
        entry['criterion_ids'] = _unique_strings(entry['criterion_ids'], _identifier, nonempty=False)
        _choice(entry['status'], ('valid', 'open'))
        _identifier(entry['reason_code']); _text(entry['reason'])
        if entry['status'] == 'open' and entry['reason_code'] == 'none':
            raise FreshReviewError('fresh-review-output-invalid')
        coverage.append(RequirementCoverageResult(**entry))
    raw['criterion_results'], raw['coverage'] = tuple(results), tuple(coverage)
    return FreshReviewOutput(**raw)


def _wire(value):
    if isinstance(value, FrozenJsonObject):
        return value.thaw()
    if isinstance(value, tuple):
        return [_wire(item) for item in value]
    if isinstance(value, (FreshReviewOutput, CriterionSearchResult, FindingHypothesis,
                          RequirementCoverageResult)):
        return {field.name: _wire(getattr(value, field.name)) for field in fields(value)}
    return value


def output_document(output):
    """Return independent JSON containers; typed carriers are not validator bypasses."""
    if not isinstance(output, FreshReviewOutput):
        raise FreshReviewError('fresh-review-output-invalid')
    raw = _wire(output)
    decode_output(raw)
    return raw


def _matches_request(output, request):
    return (output.request_digest == canonical_digest(request_document(request))
            and all(getattr(output, key) == getattr(request, key) for key in _BINDINGS))


def validate_output_sender(record, observation):
    """Stage 1: reject consumed/unlaunched requests or another dispatch/child.

    This never reads authored output and never changes the record. Commit fence
    checks are intentionally separate from the saved launch/dispatch identity.
    """
    if (not isinstance(record, FreshReviewRecord) or record.status != 'running'
            or not isinstance(record.launch, FrozenJsonObject)
            or not isinstance(record.dispatch, FrozenJsonObject)):
        raise FreshReviewError('fresh-review-output-request-unavailable')
    decode_request(request_document(record.request))
    launch, independent = validate_launch(record.request, record.dispatch.thaw(), record.launch.thaw())
    if record.independent is not independent:
        raise FreshReviewError('fresh-review-independent-invalid')
    _json_builtins(observation, 'output-sender-mismatch')
    if type(observation) is not dict:
        raise FreshReviewError('fresh-review-output-sender-mismatch')
    for key, expected in (('operation_id', launch.operation_id), ('fencing_epoch', launch.fencing_epoch),
        ('request_id', launch.request_id), ('nonce', launch.nonce), ('child_identity', launch.child_identity)):
        if type(observation.get(key)) is not type(expected) or observation[key] != expected:
            raise FreshReviewError('fresh-review-output-sender-mismatch')
    return independent


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FreshReviewError('fresh-review-output-invalid')
        result[key] = value
    return result


def _constant(_):
    raise FreshReviewError('fresh-review-output-invalid')


def inspect_output(record, observation, output_bytes, *, candidate_digest, budget_used):
    """Stage 2: failures from the bound child's content are terminal intentions.

    Deterministic priority: child failure, raw-byte budget safety preflight,
    closed schema, bindings, measured budget, then import limits. The raw-byte
    preflight bounds decoding; over-budget bytes never become import diagnostics.
    This does not certify coverage or replay and cannot publish state.
    None output is allowed only as a failed diagnostic with no output reference.
    """
    independent = validate_output_sender(record, observation)
    if (observation.get('process_exited') is not True or type(observation.get('exit_code')) is not int
            or not -(2**31) <= observation['exit_code'] < 2**31):
        raise FreshReviewError('fresh-review-output-observation-invalid')
    if output_bytes is not None and type(output_bytes) is not bytes:
        raise FreshReviewError('fresh-review-output-observation-invalid')
    _digest(candidate_digest)
    _json_builtins(budget_used, 'output-observation-invalid')
    used = _closed(budget_used, BudgetUsed.__dataclass_fields__, 'fresh-review-output-observation-invalid')
    for value in used.values():
        _integer(value, 'fresh-review-output-observation-invalid')
    diagnostic = (output_bytes if output_bytes is not None
                  and len(output_bytes) <= FRESH_REVIEW_EVIDENCE_MAX_BYTES else None)
    digest = None if diagnostic is None else 'sha256:' + hashlib.sha256(diagnostic).hexdigest()
    def decision(reason, output=None):
        return FreshReviewOutputDecision(TerminalOutcome.COMPLETED if reason == TerminalReason.NONE
            else TerminalOutcome.FAILED, reason, output, diagnostic, digest, independent)
    if observation['exit_code'] != 0:
        return decision(TerminalReason.CHILD_FAILED)
    limits = record.launch.thaw()['enforced_budget']
    if output_bytes is not None and len(output_bytes) > limits['max_output_bytes']:
        return decision(TerminalReason.BUDGET_EXCEEDED)
    if output_bytes is not None and used['output_bytes'] < len(output_bytes):
        raise FreshReviewError('fresh-review-output-observation-invalid')
    try:
        value = json.loads(output_bytes.decode('utf-8'), object_pairs_hook=_pairs, parse_constant=_constant)
        output = decode_output(value)
    except FreshReviewError:
        return decision(TerminalReason.OUTPUT_INVALID)
    except (AttributeError, UnicodeError, ValueError, RecursionError):
        return decision(TerminalReason.OUTPUT_INVALID)
    request = record.request
    if candidate_digest != request.candidate_digest or not _matches_request(output, request):
        return decision(TerminalReason.BINDING_MISMATCH)
    if {item.criterion_id for item in output.criterion_results} != set(request.criterion_ids):
        return decision(TerminalReason.OUTPUT_INVALID)
    if any(used[key] > limits[limit] for key, limit in (
            ('wall_time_sec', 'wall_time_sec'), ('tool_calls', 'max_tool_calls'),
            ('replays', 'max_replays'), ('output_bytes', 'max_output_bytes'))):
        return decision(TerminalReason.BUDGET_EXCEEDED)
    if (len(output_bytes) > FRESH_REVIEW_EVIDENCE_MAX_BYTES
            or sum(len(item.findings) for item in output.criterion_results) > FRESH_REVIEW_FINDINGS_LIMIT):
        return decision(TerminalReason.OUTPUT_OVER_IMPORT_LIMIT)
    return decision(TerminalReason.NONE, output)


def replay_eligibility(request, hypothesis, frozen_policy, *, published=False):
    """Select only the frozen replay command; a reason retains an open hypothesis.

    The importer supplies the entire frozen verifier policy. Both its policy
    digest and source/target command definition digests must match the request;
    the replay limits and materialization path are part of the source definition.
    This helper neither executes nor accepts replay evidence.
    """
    request = decode_request(request_document(request))
    if not isinstance(hypothesis, FindingHypothesis):
        raise FreshReviewError('fresh-review-output-invalid')
    hypothesis = _hypothesis(_wire(hypothesis), hypothesis.criterion_id, published=published)
    _json_builtins(frozen_policy, 'output-invalid')
    binding = next((item for item in request.candidate_bindings if item.role == 'replay'
                    and item.criterion_id == hypothesis.criterion_id), None)
    source = next((item for item in request.candidate_bindings if item.role == 'verification'
                   and item.criterion_id == hypothesis.criterion_id), None)
    if (binding is None or source is None or type(frozen_policy) is not dict
            or set(frozen_policy) != {'digest', 'commands'}
            or frozen_policy['digest'] != request.verifier_policy_digest
            or type(frozen_policy['commands']) is not dict):
        return 'replay-unsupported'
    commands = frozen_policy['commands']
    try:
        for identifier, definition in commands.items():
            validate_command(definition)
            if definition['id'] != identifier:
                return 'replay-unsupported'
        for item in (source, binding):
            definition = commands.get(item.command_id)
            validate_command(definition)
            if (definition['id'] != item.command_id
                    or canonical_digest(definition) != item.definition_digest):
                return 'replay-unsupported'
        # Links are a property of the whole frozen policy: a replay target may itself
        # name a further replay command that is not part of this binding pair.
        validate_command_links(commands)
    except (VerifierPolicyError, FreshReviewError):
        return 'replay-unsupported'
    replay_policy = commands[source.command_id].get('replay')
    if (type(replay_policy) is not dict
            or set(replay_policy) != {'command_id', 'allowed_artifact_kinds', 'max_bytes', 'relative_path'}
            or replay_policy['command_id'] != binding.command_id
            or hypothesis.command_id != binding.command_id
            or type(replay_policy['allowed_artifact_kinds']) is not list
            or not replay_policy['allowed_artifact_kinds']
            or any(type(item) is not str or not item or '\x00' in item
                   for item in replay_policy['allowed_artifact_kinds'])
            or type(replay_policy['max_bytes']) is not int or not 1 <= replay_policy['max_bytes'] <= 1048576):
        return 'replay-unsupported'
    path = replay_policy['relative_path']
    if (type(path) is not str or '\\' in path or '\x00' in path or path.startswith('/')
            or len(path) >= 2 and path[1] == ':' or any(part in ('', '.', '..') for part in path.split('/'))):
        return 'replay-unsupported'
    try:
        canonical_bytes(replay_policy)
    except FreshReviewError:
        return 'replay-unsupported'
    repro = hypothesis.repro_input.thaw()
    if repro['artifact_kind'] not in replay_policy['allowed_artifact_kinds']:
        return 'replay-unsupported'
    if len(repro['content'].encode('utf-8')) > replay_policy['max_bytes']:
        return 'replay-input-invalid'
    return None


def derive_output_coverage(output, request, contract):
    """Check the entire immutable ledger and retain open hypotheses separately.

    Required mappings come from the contract, never from child-authored subsets.
    Structural coverage validity is not a clean finding verdict. No authored
    severity, actual, or later clean attempt resolves a required hypothesis.
    Unconfirmed context retains its original requirement ID as an open obligation.
    """
    output = decode_output(output_document(output))
    request = decode_request(request_document(request))
    _json_builtins(contract, 'coverage-invalid')
    if type(contract) is not dict:
        raise FreshReviewError('fresh-review-coverage-invalid')
    try:
        immutable = {key: value for key, value in contract.items() if key != 'imported_at'}
        validated = validate_contract(immutable)
    except (AcceptanceContractError, UnicodeError) as exc:
        raise FreshReviewError('fresh-review-coverage-invalid') from exc
    if (not _matches_request(output, request)
            or canonical_contract_digest(validated) != request.contract_digest
            or validated['requirement_digest'] != request.requirement_digest
            or output.contract_digest != request.contract_digest):
        raise FreshReviewError('fresh-review-coverage-invalid')
    return _coverage_facts(output, request, validated)


def _coverage_facts(output, request, validated):
    """Shared ledger judgement after the caller closes the typed evidence."""
    requirements = {item['id']: item for item in validated['requirements']}
    criteria = {item['id']: item for item in validated['criteria']}
    searched = {item.criterion_id: item for item in output.criterion_results}
    if set(searched) != set(request.criterion_ids) or not set(searched).issubset(criteria):
        raise FreshReviewError('fresh-review-coverage-invalid')
    if {item.requirement_id for item in output.coverage} != set(requirements):
        raise FreshReviewError('fresh-review-coverage-invalid')
    open_requirements, open_findings = set(), set()
    for item in output.coverage:
        requirement = requirements[item.requirement_id]
        if any(key not in criteria or item.requirement_id not in criteria[key]['requirement_ids']
               for key in item.criterion_ids):
            raise FreshReviewError('fresh-review-coverage-invalid')
        if not item.classification_confirmed or item.status == 'open':
            open_requirements.add(item.requirement_id)
        if requirement['classification'] == 'obligation':
            required = [key for key, criterion in criteria.items() if criterion['required']
                        and item.requirement_id in criterion['requirement_ids']]
            # Every mapped required criterion must be searched in this output;
            # unselected or blocked searches leave an explicit open obligation.
            if (not required or not set(required).issubset(item.criterion_ids)
                    or any(key not in searched or searched[key].status != 'searched' for key in required)):
                open_requirements.add(item.requirement_id)
    for result in output.criterion_results:
        criterion = criteria[result.criterion_id]
        side_effects = {criterion['id'] + ':' + str(index)
                        for index in range(len(criterion['prohibited_side_effects']))}
        for finding in result.findings:
            if (not set(finding.requirement_ids).issubset(criterion['requirement_ids'])
                    or not set(finding.prohibited_side_effect_ids).issubset(side_effects)):
                raise FreshReviewError('fresh-review-finding-binding-invalid')
            if finding.prohibited_side_effect_ids or any(
                    requirements[key]['classification'] == 'obligation' for key in criterion['requirement_ids']):
                open_findings.add(finding.finding_id)
    return OutputCoverageDecision('open' if open_requirements else 'valid',
                                  tuple(sorted(open_requirements)), tuple(sorted(open_findings)))
