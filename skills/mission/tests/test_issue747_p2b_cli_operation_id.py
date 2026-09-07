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
import time
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

    def test_the_context_identity_reads_the_plans_normalised_fields(self, tmp_path, monkeypatch):
        from mission_application.evidence import prepare_context_manifest_operation
        from mission_application.retry_plan import ContextManifestRetryPlan

        monkeypatch.setenv("MISSION_OPERATION_ID", "context-1")

        def plan_for(path):
            return ContextManifestRetryPlan.for_request(
                now="2030-01-01T00:00:00Z", iteration=2, publication_path=path, project_root=tmp_path
            )

        plan = plan_for("build//manifest.json")
        assert plan.publication_path == "build/manifest.json"
        identity = _identity(prepare_context_manifest_operation, plan)
        assert identity.opted_in
        assert _arguments_of(identity) == {"iteration": 2, "publication_path": "build/manifest.json"}
        # The path the caller typed does not decide the operation; the plan's
        # canonical form does, so two spellings are one operation.
        assert _arguments_of(_identity(prepare_context_manifest_operation, plan_for("build/manifest.json"))) == (
            _arguments_of(identity)
        )

    def test_an_unusable_caller_id_is_refused_rather_than_raised(self, monkeypatch):
        """A typo in the environment is bad input, not an internal error."""
        from mission_application.artifact import prepare_artifact_append_operation
        from mission_application.cli_operation import CliOperationRejected

        monkeypatch.setenv("MISSION_OPERATION_ID", "not a token")
        with pytest.raises(CliOperationRejected) as excinfo:
            _identity(prepare_artifact_append_operation, "plan", "x", None, None)
        assert "MISSION_OPERATION_ID" in str(excinfo.value)


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

    def test_an_export_repeats_across_a_second_boundary(self, run_cli, tmp_path):
        """The clock moves between the two runs; the recorded export does not."""
        _init_mission(run_cli, tmp_path)
        run_cli("artifact", "append", "--section", "plan", "--text", "one", cwd=tmp_path)
        env = {"MISSION_OPERATION_ID": "export-once"}
        arguments = (
            "artifact", "export", "--to", "docs/p2b-export.md", "--redaction-status", "checked",
        )
        first = run_cli(*arguments, cwd=tmp_path, env_extra=env)
        assert first.returncode == 0, first.stderr
        exports = _artifact(run_cli, tmp_path)["exports"]
        time.sleep(1.1)

        second = run_cli(*arguments, cwd=tmp_path, env_extra=env)

        assert second.returncode == 0, second.stderr
        assert len(exports) == 1
        assert _artifact(run_cli, tmp_path)["exports"] == exports
        # The reply carries the first run's timestamp, so this is the recorded
        # export and not a second one that happens to look alike.
        assert json.loads(second.stdout)["export"] == json.loads(first.stdout)["export"]

    def test_a_progress_update_repeats_across_a_second_boundary(self, run_cli, tmp_path):
        """``updated_at`` separates a replay from a fresh run that writes the same counts."""
        _init_mission(run_cli, tmp_path)
        env = {"MISSION_OPERATION_ID": "progress-once"}
        arguments = ("progress", "update", "--total", "5", "--completed", "2", "--iteration", "1")
        first = run_cli(*arguments, cwd=tmp_path, env_extra=env)
        assert first.returncode == 0, first.stderr
        progress = _projected_state(run_cli, tmp_path).get("progress")
        assert progress is not None
        time.sleep(1.1)

        second = run_cli(*arguments, cwd=tmp_path, env_extra=env)

        assert second.returncode == 0, second.stderr
        assert _projected_state(run_cli, tmp_path).get("progress") == progress

        # Without the identity the same counts are written again, and the
        # timestamp moves: that is what distinguishes the two.
        time.sleep(1.1)
        third = run_cli(*arguments, cwd=tmp_path)
        assert third.returncode == 0, third.stderr
        rewritten = _projected_state(run_cli, tmp_path).get("progress")
        assert rewritten["updated_at"] != progress["updated_at"]

    def test_a_retry_still_replays_after_another_operation_wrote_the_same_slot(
        self, run_cli, tmp_path
    ):
        """The crash-retry case: the head has moved on before the retry arrives.

        A replay answers from the state its own operation committed.  Reading
        the current head instead would report a mismatch here, which is the
        one situation a caller-stable id exists to survive.
        """
        _init_mission(run_cli, tmp_path)
        env = {"MISSION_OPERATION_ID": "progress-interrupted"}
        arguments = ("progress", "update", "--total", "5", "--completed", "2", "--iteration", "1")
        first = run_cli(*arguments, cwd=tmp_path, env_extra=env)
        assert first.returncode == 0, first.stderr
        recorded = json.loads(first.stdout)["progress"]

        intervening = run_cli(
            "progress", "update", "--total", "5", "--completed", "4", "--iteration", "1",
            cwd=tmp_path,
        )
        assert intervening.returncode == 0, intervening.stderr
        head = _projected_state(run_cli, tmp_path).get("progress")
        assert head["completed"] == 4

        retry = run_cli(*arguments, cwd=tmp_path, env_extra=env)

        assert retry.returncode == 0, retry.stderr
        # The reply is the original record, not the head and not a new write.
        assert json.loads(retry.stdout)["progress"] == recorded
        assert _projected_state(run_cli, tmp_path).get("progress") == head

    def test_a_context_manifest_retry_survives_an_intervening_operation(
        self, run_cli, tmp_path
    ):
        """Same shape for the manifest route, which reads a keyed record."""
        _init_mission(run_cli, tmp_path)
        env = {"MISSION_OPERATION_ID": "manifest-interrupted"}
        arguments = (
            "context-manifest", "--iteration", "1", "--out", "docs/p2b-manifest.md",
        )
        first = run_cli(*arguments, cwd=tmp_path, env_extra=env)
        assert first.returncode == 0, first.stderr
        recorded = json.loads(first.stdout)

        # The intervening run must overwrite the same keyed record, or the
        # head and the historical state would not differ where it matters.
        intervening = run_cli(
            "context-manifest", "--iteration", "1", "--out", "docs/p2b-other.md",
            cwd=tmp_path,
        )
        assert intervening.returncode == 0, intervening.stderr
        assert json.loads(intervening.stdout) != recorded

        retry = run_cli(*arguments, cwd=tmp_path, env_extra=env)

        assert retry.returncode == 0, retry.stderr
        assert json.loads(retry.stdout) == recorded

    def test_a_context_manifest_retry_survives_a_change_to_what_it_digests(
        self, run_cli, push_provenance_score, tmp_path
    ):
        """The manifest digests the score history, which a later run moves.

        Overwriting the keyed record is not enough to catch this: the reply
        has to come from the record the operation committed, because what the
        command would compute *now* is legitimately different.
        """
        _init_mission(run_cli, tmp_path)
        env = {"MISSION_OPERATION_ID": "manifest-restated"}
        arguments = (
            "context-manifest", "--iteration", "1", "--out", "docs/p2b-scored.md",
        )
        first = run_cli(*arguments, cwd=tmp_path, env_extra=env)
        assert first.returncode == 0, first.stderr
        recorded = json.loads(first.stdout)

        # Findings put entries in the manifest, so both the digest and the
        # count a fresh run reports move away from what the first run saw.
        push_provenance_score(tmp_path, open_high=1)
        moved = run_cli(
            "context-manifest", "--iteration", "1", "--out", "docs/p2b-moved.md",
            cwd=tmp_path,
        )
        assert moved.returncode == 0, moved.stderr
        moved_reply = json.loads(moved.stdout)
        assert moved_reply["digest"] != recorded["digest"]
        assert moved_reply["findings_count"] != recorded["findings_count"]

        retry = run_cli(*arguments, cwd=tmp_path, env_extra=env)

        assert retry.returncode == 0, retry.stderr
        assert json.loads(retry.stdout) == recorded

    def test_an_unusable_identity_is_reported_as_input_and_changes_nothing(self, run_cli, tmp_path):
        _init_mission(run_cli, tmp_path)
        before = _artifact_blocks(run_cli, tmp_path)

        result = run_cli(
            "artifact", "append", "--section", "plan", "--text", "one",
            cwd=tmp_path, env_extra={"MISSION_OPERATION_ID": "not a token"},
        )

        assert result.returncode == 2
        assert "MISSION_OPERATION_ID" in result.stderr
        assert _artifact_blocks(run_cli, tmp_path) == before
