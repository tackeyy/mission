"""#747 P2-b: a CLI retry is the same operation, not a second one.

Contracts 1-11 of the P2-b design (Issue #747, design v1-v5, GO at round 5).

The identity is opt-in through ``MISSION_OPERATION_ID``: without it the
repository mints a fresh id per transaction and a retry runs again, which is
what every command did before.  With it, the same command run twice replays
the first, and a *different* command reusing the id is refused rather than
silently treated as the same operation.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

MISSION_ROOT = Path(__file__).resolve().parents[1]


def _state_module(name="state_p2b_test"):
    script = MISSION_ROOT / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location(name, script)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _identity_services(recorder=None):
    """The two adapter helpers the identity derivation is given, unchanged."""
    module = _state_module("state_p2b_services")

    def compatibility_arguments(arguments, *, target_digest, require_caller):
        if recorder is not None:
            recorder.append(dict(arguments))
        return module._compatibility_operation_arguments(
            arguments, target_digest=target_digest, require_caller=require_caller
        )

    return compatibility_arguments, module._canonical_compatibility_operation


def _identity(builder, *args, session_id="test", recorder=None, **kwargs):
    compatibility_arguments, canonical_operation = _identity_services(recorder)
    return builder(
        *args,
        session_id=session_id,
        compatibility_arguments=compatibility_arguments,
        canonical_operation=canonical_operation,
        **kwargs,
    )


def _arguments_of(identity):
    """The semantic arguments the identity actually carries."""
    command = identity.operation_command
    thawed = command.thaw() if hasattr(command, "thaw") else command
    return thawed["arguments"]


# --------------------------------------------------------------------------
# Contract 3, 4, 8, 9: what makes two invocations the same operation
# --------------------------------------------------------------------------


class TestSemanticArguments:
    def test_the_identity_is_absent_without_the_environment_variable(self, monkeypatch):
        from mission_application.artifact import prepare_artifact_append_operation

        monkeypatch.delenv("MISSION_OPERATION_ID", raising=False)
        identity = _identity(
            prepare_artifact_append_operation, "plan", "first", None, None
        )
        assert identity.operation_id is None
        assert identity.operation_command is None
        assert identity.command_type is None
        assert identity.opted_in is False

    def test_the_body_enters_as_a_digest_and_never_as_text(self, monkeypatch):
        from mission_application.artifact import (
            prepare_artifact_append_operation,
            prepare_artifact_publish_operation,
        )

        monkeypatch.setenv("MISSION_OPERATION_ID", "op-1")
        append = _identity(
            prepare_artifact_append_operation, "plan", "a secret paragraph", None, None
        )
        arguments = _arguments_of(append)
        assert "a secret paragraph" not in json.dumps(arguments, ensure_ascii=False)
        assert arguments["content_digest"].startswith("sha256:")
        assert "content" not in arguments

        publish = _identity(
            prepare_artifact_publish_operation, "local", None, "approved by the owner"
        )
        published = _arguments_of(publish)
        assert "approved by the owner" not in json.dumps(published, ensure_ascii=False)
        assert published["approval_digest"].startswith("sha256:")
        assert "approval_text" not in published

    def test_no_argument_carries_the_moment_the_command_ran(self, monkeypatch):
        from mission_application.artifact import (
            prepare_artifact_append_operation,
            prepare_artifact_export_operation,
            prepare_artifact_init_operation,
            prepare_artifact_publish_operation,
            prepare_artifact_render_operation,
        )
        from mission_application.evidence import prepare_progress_update_operation

        monkeypatch.setenv("MISSION_OPERATION_ID", "op-1")
        built = [
            _identity(prepare_artifact_append_operation, "plan", "x", None, None),
            _identity(prepare_artifact_init_operation, "a.md", "markdown", "T", "unchecked", False),
            _identity(prepare_artifact_render_operation, "checked"),
            _identity(prepare_artifact_export_operation, "out/a.md", "checked"),
            _identity(prepare_artifact_publish_operation, "local", None, "ok"),
            _identity(prepare_progress_update_operation, 3, 1, None, None, None, 1),
        ]
        for identity in built:
            arguments = _arguments_of(identity)
            assert not {"at", "now", "timestamp"} & set(arguments), arguments

    @pytest.mark.parametrize(
        ("first", "second"),
        [
            (("follow-ups", "x", None, None), ("follow_ups", "x", None, None)),
            (("plan", "x", None, None), ("plan", "x\n", None, None)),
            (("plan", "x", None, ""), ("plan", "x", None, None)),
            (("plan", "x", "", None), ("plan", "x", None, None)),
        ],
    )
    def test_spellings_the_rules_treat_alike_are_one_operation(self, monkeypatch, first, second):
        from mission_application.artifact import prepare_artifact_append_operation

        monkeypatch.setenv("MISSION_OPERATION_ID", "op-1")
        one = _identity(prepare_artifact_append_operation, *first)
        other = _identity(prepare_artifact_append_operation, *second)
        assert _arguments_of(one) == _arguments_of(other)

    def test_a_different_body_is_a_different_intent(self, monkeypatch):
        from mission_application.artifact import prepare_artifact_append_operation

        monkeypatch.setenv("MISSION_OPERATION_ID", "op-1")
        one = _identity(prepare_artifact_append_operation, "plan", "first", None, None)
        other = _identity(prepare_artifact_append_operation, "plan", "second", None, None)
        # The id is the caller's, so it is the *arguments* that differ; the
        # repository turns that into a collision.
        assert one.operation_id == other.operation_id == "op-1"
        assert _arguments_of(one) != _arguments_of(other)

    def test_a_publish_destination_is_not_resolved_as_a_path(self, monkeypatch):
        """A publication may name a URL; two URLs must not collapse onto one file path."""
        from mission_application.artifact import prepare_artifact_publish_operation

        monkeypatch.setenv("MISSION_OPERATION_ID", "op-1")
        one = _identity(prepare_artifact_publish_operation, "local", "https://example.invalid/x", "ok")
        other = _identity(prepare_artifact_publish_operation, "local", "https:/example.invalid/x", "ok")
        assert _arguments_of(one) != _arguments_of(other)
        absent = _identity(prepare_artifact_publish_operation, "local", None, "ok")
        empty = _identity(prepare_artifact_publish_operation, "local", "", "ok")
        assert _arguments_of(absent) == _arguments_of(empty)

    def test_the_normalisation_is_the_kernels_own(self):
        """Two copies of a rule drift; these call the kernel's."""
        import inspect

        from mission_application import artifact as application

        source = inspect.getsource(application.prepare_artifact_append_operation)
        assert "normalized_block_content(content)" in source
        assert "normalized_artifact_section(section)" in source
        assert "optional_artifact_text(" in source
        assert ".rstrip()" not in source, "the rule belongs to the kernel, not here"

    def test_the_context_identity_reads_the_plans_normalised_fields(self, tmp_path):
        from mission_application.evidence import prepare_context_manifest_operation
        from mission_application.retry_plan import ContextManifestRetryPlan

        plan = ContextManifestRetryPlan.for_request(
            now="2030-01-01T00:00:00Z",
            iteration=2,
            publication_path="build//manifest.json",
            project_root=tmp_path,
        )
        assert plan.publication_path == "build/manifest.json"
        identity = _identity(prepare_context_manifest_operation, plan)
        assert identity is not None


# --------------------------------------------------------------------------
# Contract 5 and 10: what the CLI does and does not reach
# --------------------------------------------------------------------------


def test_the_cli_does_not_call_the_read_only_lookup():
    """`lookup_operation` is for the lease preflight (item 5), not for this wiring.

    Short-circuiting `begin()` with it would skip the durable-prepare
    recovery, the finalized-index agreement and the replay payload checks.
    """
    source = (MISSION_ROOT / "bin" / "mission-state.py").read_text(encoding="utf-8")
    assert "lookup_operation" not in source
    for module in ("artifact_cli.py", "artifact.py", "evidence.py"):
        text = (MISSION_ROOT / "lib" / "mission_application" / module).read_text(encoding="utf-8")
        assert "lookup_operation" not in text, module


def test_the_outcome_classification_is_fixed_where_it_can_be_observed():
    """These commands do not record an outcome, so the classification is pinned here."""
    module = _state_module("state_p2b_outcomes")
    assert module._fenced_cli_outcome_kind("operation-intent-collision") == "invalid-input"
    assert module._fenced_cli_outcome_kind("operation-history-collected") == "expected-gate"


def test_the_cli_reports_the_detail_rather_than_the_code():
    """The E2E assertions below match the detail, so the branch that prints it is held."""
    import inspect

    module = _state_module("state_p2b_detail")
    source = inspect.getsource(module._reject_fenced_lease_for_cli)
    assert 'print("ERROR: %s" % (detail or error.code)' in source


# --------------------------------------------------------------------------
# Contract 1, 2, 4, 7: the real CLI
# --------------------------------------------------------------------------


def _projected_state(run_cli, root):
    """The state as the CLI itself reads it (the session file is a v5 head record)."""
    result = run_cli("get", cwd=root)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _artifact(run_cli, root):
    return _projected_state(run_cli, root).get("artifact") or {}


def _artifact_blocks(run_cli, root):
    return _artifact(run_cli, root).get("blocks") or []


def _init_mission(run_cli, root):
    result = run_cli("init", "P2-b wiring", cwd=root)
    assert result.returncode == 0, result.stderr
    result = run_cli("artifact", "init", "--title", "T", cwd=root)
    assert result.returncode == 0, result.stderr


class TestRealCli:
    def test_a_retry_with_the_same_identity_appends_once(self, run_cli, tmp_path):
        _init_mission(run_cli, tmp_path)
        env = {"MISSION_OPERATION_ID": "append-once"}
        first = run_cli("artifact", "append", "--section", "plan", "--text", "one", cwd=tmp_path, env_extra=env)
        assert first.returncode == 0, first.stderr
        after_first = _artifact_blocks(run_cli, tmp_path)

        second = run_cli("artifact", "append", "--section", "plan", "--text", "one", cwd=tmp_path, env_extra=env)

        assert second.returncode == 0, second.stderr
        assert len(after_first) == 1
        assert _artifact_blocks(run_cli, tmp_path) == after_first
        # The replay answers with the block the first run committed, timestamp
        # and all: the second run's clock does not reach the record.
        assert json.loads(second.stdout)["block"] == json.loads(first.stdout)["block"]

    def test_without_the_identity_a_second_run_appends_again(self, run_cli, tmp_path):
        _init_mission(run_cli, tmp_path)
        for _ in range(2):
            result = run_cli("artifact", "append", "--section", "plan", "--text", "one", cwd=tmp_path)
            assert result.returncode == 0, result.stderr
        assert len(_artifact_blocks(run_cli, tmp_path)) == 2

    def test_reusing_the_identity_for_other_content_is_refused(self, run_cli, tmp_path):
        _init_mission(run_cli, tmp_path)
        env = {"MISSION_OPERATION_ID": "append-once"}
        first = run_cli("artifact", "append", "--section", "plan", "--text", "one", cwd=tmp_path, env_extra=env)
        assert first.returncode == 0, first.stderr
        before = _artifact_blocks(run_cli, tmp_path)

        second = run_cli("artifact", "append", "--section", "plan", "--text", "two", cwd=tmp_path, env_extra=env)

        assert second.returncode == 2
        assert "operation ID has a different intent" in second.stderr
        assert _artifact_blocks(run_cli, tmp_path) == before

    def test_an_export_repeats_without_recording_twice(self, run_cli, tmp_path):
        _init_mission(run_cli, tmp_path)
        run_cli("artifact", "append", "--section", "plan", "--text", "one", cwd=tmp_path)
        env = {"MISSION_OPERATION_ID": "export-once"}
        arguments = (
            "artifact", "export", "--to", "docs/p2b-export.md", "--redaction-status", "checked",
        )
        first = run_cli(*arguments, cwd=tmp_path, env_extra=env)
        assert first.returncode == 0, first.stderr
        exports = _artifact(run_cli, tmp_path)["exports"]

        second = run_cli(*arguments, cwd=tmp_path, env_extra=env)

        assert second.returncode == 0, second.stderr
        assert len(exports) == 1
        assert _artifact(run_cli, tmp_path)["exports"] == exports

    def test_a_progress_update_repeats_without_counting_twice(self, run_cli, tmp_path):
        _init_mission(run_cli, tmp_path)
        env = {"MISSION_OPERATION_ID": "progress-once"}
        arguments = ("progress", "update", "--total", "5", "--completed", "2", "--iteration", "1")
        first = run_cli(*arguments, cwd=tmp_path, env_extra=env)
        assert first.returncode == 0, first.stderr
        progress = _projected_state(run_cli, tmp_path).get("progress")

        second = run_cli(*arguments, cwd=tmp_path, env_extra=env)

        assert second.returncode == 0, second.stderr
        assert progress is not None
        assert _projected_state(run_cli, tmp_path).get("progress") == progress
