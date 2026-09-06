"""#747 P2-b: the artifact CLI commands, decided here rather than in the adapter.

Each command needs three things the adapter used to do itself: validate what
was typed, bind the invocation to a caller-stable operation identity, and
build the repository that identity runs on.  Doing them in the adapter is
what made a retry a new operation -- there was nowhere to put the identity --
and the thin-adapter budget has no room for the extra steps.

The adapter keeps what only it can do: reading argv and files, resolving
paths against the process's working directory, rendering markdown, and
turning a refusal into a message and an exit code.  Those arrive as
``ArtifactCliServices``.
"""

from __future__ import annotations

import json

from mission_application.artifact import (
    ArtifactAppendRequest,
    ArtifactExportRequest,
    ArtifactInitRequest,
    ArtifactPublishRequest,
    ArtifactRenderRequest,
    EvidenceFailure,
    prepare_artifact_append_operation,
    prepare_artifact_export_operation,
    prepare_artifact_init_operation,
    prepare_artifact_publish_operation,
    prepare_artifact_render_operation,
    run_artifact_append,
    run_artifact_export,
    run_artifact_init,
    run_artifact_publish,
    run_artifact_render,
)
from mission_kernel.artifact import (
    ARTIFACT_PUBLISH_PROVIDERS,
    ARTIFACT_REDACTION_STATUSES,
    ArtifactRuleError,
    normalized_artifact_section,
)

STATE_FILE_MISSING = "ERROR: state.json が見つかりません。先に `init` してください。"
EXPORT_REDACTION_REQUIRED = (
    "ERROR: export requires --redaction-status checked|reviewed|not-needed"
)
PUBLISH_PROVIDER_UNSUPPORTED = "ERROR: unsupported artifact publish provider"
PUBLISH_CONSENT_REQUIRED = (
    "ERROR: artifact publish requires --require-confirm and --approval-text. "
    "This command records publish consent; it does not silently publish remotely."
)


def _state_file(cwd, services):
    state_file = services.resolve_state_file(cwd)
    if not state_file.exists():
        services.fail(STATE_FILE_MISSING, 1)
    return state_file


def _repository(services, cwd, state_file, identity):
    """Build the repository this operation runs on, carrying its identity.

    When the caller did not opt in, every identity field is ``None`` and the
    repository mints its own per-transaction id, exactly as before.
    """
    return services.repository(
        cwd,
        state_file,
        stamp=False,
        pre_admit_lease=True,
        session_id=state_file.stem,
        operation_id=identity.operation_id,
        operation_command=identity.operation_command,
        operation_command_type=identity.command_type,
    )


def _rendered(result, args):
    return json.dumps(
        {"ok": True, **result},
        indent=2 if getattr(args, "json", False) else None,
        ensure_ascii=False,
    )


def _refuse(services, code):
    services.fail("ERROR: %s" % (code,), 2)


def run_artifact_init_cli(args, cwd, services) -> str:
    state_file = _state_file(cwd, services)
    if getattr(args, "redaction_status") not in ARTIFACT_REDACTION_STATUSES:
        services.fail("ERROR: invalid --redaction-status", 2)
    artifact_path = services.state_relative_path(
        cwd, str(services.artifact_path(cwd, state_file.stem))
    )
    identity = prepare_artifact_init_operation(
        artifact_path,
        getattr(args, "format"),
        getattr(args, "title"),
        getattr(args, "redaction_status"),
        bool(getattr(args, "required_for_pass")),
        session_id=state_file.stem,
        compatibility_arguments=services.compatibility_arguments,
        canonical_operation=services.canonical_operation,
    )
    try:
        result = run_artifact_init(
            ArtifactInitRequest(
                now=services.now(),
                artifact_path=artifact_path,
                format=getattr(args, "format"),
                title=getattr(args, "title"),
                redaction_status=getattr(args, "redaction_status"),
                required_for_pass=bool(getattr(args, "required_for_pass")),
            ),
            _repository(services, cwd, state_file, identity),
            services.render_markdown,
        )
    except EvidenceFailure as exc:
        _refuse(services, exc.code)
    return _rendered(result, args)


def run_artifact_append_cli(args, cwd, services) -> str:
    state_file = _state_file(cwd, services)
    section = _section(args, services)
    content, source = services.read_input(args)
    identity = prepare_artifact_append_operation(
        section,
        content,
        source,
        getattr(args, "label"),
        session_id=state_file.stem,
        compatibility_arguments=services.compatibility_arguments,
        canonical_operation=services.canonical_operation,
    )
    try:
        result = run_artifact_append(
            ArtifactAppendRequest(
                services.now(), section, content, source, getattr(args, "label")
            ),
            _repository(services, cwd, state_file, identity),
        )
    except EvidenceFailure as exc:
        _refuse(services, exc.code)
    return _rendered(result, args)


def _section(args, services):
    try:
        return normalized_artifact_section(getattr(args, "section"))
    except ArtifactRuleError:
        services.fail(services.unknown_section_message(), 2)


def run_artifact_render_cli(args, cwd, services) -> str:
    state_file = _state_file(cwd, services)
    identity = prepare_artifact_render_operation(
        getattr(args, "redaction_status"),
        session_id=state_file.stem,
        compatibility_arguments=services.compatibility_arguments,
        canonical_operation=services.canonical_operation,
    )
    try:
        result = run_artifact_render(
            ArtifactRenderRequest(services.now(), getattr(args, "redaction_status")),
            _repository(services, cwd, state_file, identity),
            services.render_markdown,
        )
    except EvidenceFailure as exc:
        _refuse(services, exc.code)
    return _rendered(result, args)


def run_artifact_export_cli(args, cwd, services) -> str:
    state_file = _state_file(cwd, services)
    if getattr(args, "redaction_status") not in ARTIFACT_REDACTION_STATUSES - {"unchecked"}:
        services.fail(EXPORT_REDACTION_REQUIRED, 2)
    destination = services.state_relative_path(
        cwd, str(services.resolve_output_path(cwd, getattr(args, "to")))
    )
    identity = prepare_artifact_export_operation(
        destination,
        getattr(args, "redaction_status"),
        session_id=state_file.stem,
        compatibility_arguments=services.compatibility_arguments,
        canonical_operation=services.canonical_operation,
    )
    try:
        result = run_artifact_export(
            ArtifactExportRequest(
                services.now(), destination, getattr(args, "redaction_status")
            ),
            _repository(services, cwd, state_file, identity),
            services.render_markdown,
        )
    except EvidenceFailure as exc:
        _refuse(services, exc.code)
    return _rendered(result, args)


def run_artifact_publish_cli(args, cwd, services) -> str:
    state_file = _state_file(cwd, services)
    if getattr(args, "provider") not in ARTIFACT_PUBLISH_PROVIDERS:
        services.fail(PUBLISH_PROVIDER_UNSUPPORTED, 2)
    if not getattr(args, "require_confirm") or not getattr(args, "approval_text"):
        services.fail(PUBLISH_CONSENT_REQUIRED, 2)
    identity = prepare_artifact_publish_operation(
        getattr(args, "provider"),
        getattr(args, "destination"),
        getattr(args, "approval_text"),
        session_id=state_file.stem,
        compatibility_arguments=services.compatibility_arguments,
        canonical_operation=services.canonical_operation,
    )
    try:
        result = run_artifact_publish(
            ArtifactPublishRequest(
                services.now(),
                getattr(args, "provider"),
                getattr(args, "destination"),
                getattr(args, "approval_text"),
                bool(getattr(args, "require_confirm")),
            ),
            _repository(services, cwd, state_file, identity),
            services.render_markdown,
        )
    except EvidenceFailure as exc:
        _refuse(services, exc.code)
    return _rendered(result, args)
