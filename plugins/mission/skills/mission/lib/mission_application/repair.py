"""Start repair obligations and recover lost publication without replaying work."""
from functools import partial
from pathlib import Path
import json
import secrets

from acceptance_contract import canonical_contract_digest, frozen_verifier_commands
from mission_kernel.commands import BeginFindingRepair, ReconcileFindingRepair, FreshReviewInputEffectClaim
from mission_kernel.fresh_review import FreshReviewError, canonical_bytes, canonical_digest
from mission_kernel.repair_lineage import decode_projection
from mission_kernel.repair_attempts import attempt_id, find_attempt, reference, terminal_operation, baseline_receipts
from mission_kernel.json_codec import freeze_json_value
from mission_kernel.errors import StateBoundaryError
from .artifact import EvidenceFailure, make_evidence_effect
from .cli_operation import prepare_cli_operation, CliOperationRejected
from .evidence import PreparedEvidenceOperation, execute_evidence_operation
from .fresh_review import _capture
from .fresh_review_completion import observe_lineage_evidence, _read
from mission_kernel.fresh_review import ContentAddressedRef
from .verification_runner import VerificationRunnerError
from .evidence_publication import EvidencePublicationError


def prepare_begin(state, *, root, lineage_id, operation, plan, services):
    projection = decode_projection(state)
    matches = [row.document.thaw() for row in projection.lineages if row.document.thaw()['lineage_id'] == lineage_id]
    if len(matches) != 1 or matches[0]['kind'] != 'finding':
        raise FreshReviewError('repair-replay-unsupported')
    row = matches[0]
    identifier = attempt_id(lineage_id, operation)
    old = next((a for a in row['attempts'] if a['attempt_id'] == identifier), None)
    evidence = observe_lineage_evidence(state, root=root, read_evidence=services.read_evidence)
    finding = _read(root, ContentAddressedRef(**row['finding_ref']), read_evidence=services.read_evidence)
    repro = canonical_bytes(finding['repro_input'])
    if old is not None:
        body = _read(root, ContentAddressedRef(**old['history_ref']), read_evidence=services.read_evidence)
        if body['plan_ref'] != reference('repair-plan', plan.encode('utf-8')):
            raise FreshReviewError('repair-operation-conflict')
    else:
        contract = state['acceptance_contract']
        commands = frozen_verifier_commands(contract)
        if services.load_verifier_policy(root)['digest'] != contract['verifier_policy']['digest']:
            raise FreshReviewError('repair-contract-stale')
        ids = row['introduced_candidate']['snapshots']
        snapshots = _capture(root, {key: commands[key] for key in ids})
        candidates = {key: value.digest for key, value in snapshots.items()}
        if candidates != {key: value.digest for key, value in _capture(root, {key: commands[key] for key in ids}).items()}:
            raise FreshReviewError('repair-candidate-stale')
        body = dict(schema='mission-repair-attempt/1', lineage_id=lineage_id, attempt_id=identifier,
            before_candidate=dict(snapshots=candidates, contract_digest=canonical_contract_digest(contract),
                requirement_digest=contract['requirement_digest'], verifier_policy_digest=contract['verifier_policy']['digest'],
                iteration=state['iteration']), plan_ref=reference('repair-plan', plan.encode('utf-8')),
            repro_input_ref=reference('repair-repro', repro), repro_input_digest=canonical_digest(finding['repro_input']),
            baseline_receipts=baseline_receipts(state, row))
    contents = (canonical_bytes(body), plan.encode('utf-8'), repro)
    refs = (reference('repair-attempt', contents[0]), body['plan_ref'], body['repro_input_ref'])
    effects = tuple(make_evidence_effect(ref['kind'], ref['relative_path'], content) for ref, content in zip(refs, contents))
    claims = tuple(FreshReviewInputEffectClaim(e.kind, e.target, e.digest, e.size) for e in effects)
    return PreparedEvidenceOperation(BeginFindingRepair(lineage_id, operation, freeze_json_value(body), plan, *claims, evidence), effects, {})


def prepare_reconcile(state, *, identifier, reason):
    _, item = find_attempt(state, identifier)
    operation = terminal_operation(identifier, 'blocked', reason)
    return PreparedEvidenceOperation(ReconcileFindingRepair(identifier, operation, reason), (), {})


def run_repair_cli(args, services):
    root = Path.cwd()
    sf = services.resolve_state_file(root)
    if not sf.exists():
        services.fail('repair-state-missing', 2)
    def repo(operation=None, command=None, stamp=True, command_type='repair-reconcile'):
        if operation is not None and command is None:
            _, command = services.canonical_operation(sf.stem, command_type, dict(operation_id=operation), caller_operation_id=operation)
        return services.repository(root, sf, stamp=stamp, strict_read=True, pre_admit_lease=stamp,
            session_id=sf.stem, operation_id=operation, operation_command=command, operation_command_type=command_type)
    def read():
        reader = repo(stamp=False)
        with reader.transaction():
            return reader.load()
    try:
        if args.repair_command == 'begin':
            raw = services.read_evidence(root, args.plan_ref, 262144)
            plan = raw.decode('utf-8')
            identity = prepare_cli_operation('repair-begin', dict(lineage_id=args.finding, plan_digest=reference('repair-plan', raw)['digest']),
                session_id=sf.stem, compatibility_arguments=services.compatibility_arguments, canonical_operation=services.canonical_operation)
            operation = identity.operation_id or 'repair-begin:' + secrets.token_hex(16)
            identifier = attempt_id(args.finding, operation)
            started_here = False
            def prepare(state):
                nonlocal started_here
                started_here = not any(a['attempt_id'] == identifier for line in decode_projection(state).lineages
                    for a in line.document.thaw()['attempts'])
                return prepare_begin(state, root=root, lineage_id=args.finding, operation=operation, plan=plan, services=services)
            # Observation failures on retries must not terminate an existing
            # obligation. Only a new attempt admitted below can need recovery.
            prepare(read())
            if not started_here:
                _, item = find_attempt(read(), identifier)
                return json.dumps(dict(ok=True, attempt=item), ensure_ascii=False, indent=2)
            try:
                execute_evidence_operation(repo(operation, identity.operation_command, command_type='repair-begin'), prepare)
            except (OSError, StateBoundaryError) as exc:
                if isinstance(exc, StateBoundaryError) and not isinstance(exc.__cause__, OSError):
                    raise
                if not started_here:
                    raise
                # A failed begin which published nothing creates no obligation.
                # If publication reached the head, recovery must close that exact
                # pending attempt; a second state-only failure leaves it recoverable.
                try:
                    find_attempt(read(), identifier)
                except FreshReviewError:
                    raise EvidenceFailure('repair-effects-unavailable')
                execute_evidence_operation(repo(terminal_operation(identifier, 'blocked', 'effects-unavailable')),
                    partial(prepare_reconcile, identifier=identifier, reason='effects-unavailable'))
        else:
            identifier = args.attempt
            _, item = find_attempt(read(), identifier)  # begin/load recovers durable prepare first
            if item['status'] == 'pending':
                def advance(_number):
                    return execute_evidence_operation(repo(terminal_operation(identifier, 'blocked', 'publication-result-lost')),
                        partial(prepare_reconcile, identifier=identifier, reason='publication-result-lost'))
                services.run_with_base_retry(None, advance)
        _, item = find_attempt(read(), identifier)
        return json.dumps(dict(ok=True, attempt=item), ensure_ascii=False, indent=2)
    except (OSError, UnicodeError):
        services.fail('repair-io-unavailable', 2)
    except (ValueError, VerificationRunnerError, EvidencePublicationError, CliOperationRejected) + services.commit_errors as exc:
        services.fail(getattr(exc, 'code', 'repair-input-invalid'), 2)
