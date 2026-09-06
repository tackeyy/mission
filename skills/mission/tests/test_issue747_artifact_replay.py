"""#747 項目 6: artifact 経路の `decision=None` replay を契約化する.

replay の結果は「その operation が commit した generation の state」から復元する。
operation record が指す commit を lineage 検証して読み戻し（persistence）、executor が
transaction 内で取得して execution result に載せ（ports / executor）、application が
command ごとに payload を組み立てる。設計は Issue #747 のコメント v5〜v9（9 巡目で GO）。
"""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from .evidence_doubles import decoded_state, in_memory_v5_repository

AT = "2030-01-01T00:00:01Z"


# ----------------------------------------------------------------- fixtures


def _plan(path):
    from mission_application.retry_plan import ContextManifestRetryPlan

    return ContextManifestRetryPlan(now="2026-01-01T00:00:00Z", iteration=1, publication_path=path)


def _v5(local, operation_id):
    from mission_kernel.json_codec import decode_json_object
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    return V5CompatibilityRepository(
        repository=local,
        session_id="test",
        lease_owner_session_id="test",
        presented_lease_id="fixture-lease",
        operation_id=operation_id,
        operation_command=decode_json_object(
            b'{"schema":"mission-command-intent/1","type":"compatibility-mutation"}'
        ),
        operation_command_type="compatibility-mutation",
    )


def _committed_repository(tmp_path):
    """A real repository with two committed operations after the init generation."""
    from .test_issue503_fenced_commit import _commit_cli_init

    local, root, _clock, _sp, _sb, _r = _commit_cli_init(tmp_path)
    states = {}
    for operation_id, path in (("op-1", "build/a.json"), ("op-2", "build/b.json")):
        _v5(local, operation_id).execute_retry_safe_evidence_plan(_plan(path))
        states[operation_id] = local.read("test")
    return local, root, states


def _operation_record(root, session_id, operation_id):
    """Read the operation record the way a replay would find it."""
    from mission_persistence.fenced_commit import CommitResult

    for path in sorted((root / "operations").glob("*.json")):
        document = json.loads(path.read_bytes())
        if document["operation_id"] == operation_id and document["session_id"] == session_id:
            result = CommitResult(**document["result"])
            # #747 P2: the historical read binds the record's materialization
            # to the commit it opens, so the caller passes what the record holds.
            return result, document["intent_digest"], path, document.get("materialization")
    raise AssertionError("operation record not found: " + operation_id)


def _all_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


# ----------------------------------------------------------------- persistence


class TestReadOperationState:
    def test_the_commit_reader_returns_the_state_the_head_reader_saw(self, tmp_path):
        from mission_kernel import project_legacy_document

        local, root, states = _committed_repository(tmp_path)
        result, intent, _, materialization = _operation_record(root, "test", "op-1")
        state = local.read_operation_state(
            result, session_id="test", operation_id="op-1", intent_digest=intent, record_version=2, materialization=materialization
        )
        assert project_legacy_document(state) == project_legacy_document(states["op-1"].state)
        # op-2 moved the head since; op-1's generation is still what op-1 committed.
        assert project_legacy_document(state) != project_legacy_document(states["op-2"].state)

    def test_reading_is_read_only(self, tmp_path):
        local, root, _states = _committed_repository(tmp_path)
        result, intent, _, materialization = _operation_record(root, "test", "op-1")
        before = _all_bytes(root)
        local.read_operation_state(result, session_id="test", operation_id="op-1", intent_digest=intent, record_version=2, materialization=materialization)
        assert _all_bytes(root) == before

    @pytest.mark.parametrize("field", ["operation_id", "intent_digest", "session_id"])
    def test_a_foreign_identity_is_a_lineage_mismatch(self, tmp_path, field):
        from mission_persistence.fenced_commit import FencedCommitError

        local, root, _states = _committed_repository(tmp_path)
        result, intent, _, materialization = _operation_record(root, "test", "op-1")
        kwargs = {"session_id": "test", "operation_id": "op-1", "intent_digest": intent, "record_version": 2, "materialization": materialization}
        kwargs[field] = {"operation_id": "op-2", "intent_digest": "sha256:" + "1" * 64,
                         "session_id": "other"}[field]
        with pytest.raises(FencedCommitError) as excinfo:
            local.read_operation_state(result, **kwargs)
        assert excinfo.value.code == "lineage-mismatch"

    @pytest.mark.parametrize("field", ["head_digest", "state_generation_digest", "generation"])
    def test_a_result_that_disagrees_with_the_commit_is_a_lineage_mismatch(self, tmp_path, field):
        from dataclasses import replace

        from mission_persistence.fenced_commit import FencedCommitError

        local, root, _states = _committed_repository(tmp_path)
        result, intent, _, materialization = _operation_record(root, "test", "op-1")
        broken = replace(result, **{field: 99 if field == "generation" else "sha256:" + "2" * 64})
        with pytest.raises(FencedCommitError) as excinfo:
            local.read_operation_state(broken, session_id="test", operation_id="op-1", intent_digest=intent, record_version=2, materialization=materialization)
        assert excinfo.value.code == "lineage-mismatch"

    def test_a_collected_generation_manifest_is_named_as_such(self, tmp_path):
        """Only the manifest is what GC removes; that is the one retention signal."""
        from mission_persistence.fenced_commit import FencedCommitError

        local, root, _states = _committed_repository(tmp_path)
        result, intent, _, materialization = _operation_record(root, "test", "op-1")
        commit = json.loads((root / "commits" / (result.commit_digest.removeprefix("sha256:") + ".json")).read_bytes())
        (root / commit["generation"]["path"]).unlink()
        with pytest.raises(FencedCommitError) as excinfo:
            local.read_operation_state(result, session_id="test", operation_id="op-1", intent_digest=intent, record_version=2, materialization=materialization)
        assert excinfo.value.code == "operation-history-collected"

    @pytest.mark.parametrize("which", ["commit", "state"])
    def test_a_missing_commit_or_state_is_corruption_not_retention(self, tmp_path, which):
        from mission_persistence.fenced_commit import FencedCommitError

        local, root, _states = _committed_repository(tmp_path)
        result, intent, _, materialization = _operation_record(root, "test", "op-1")
        commit_path = root / "commits" / (result.commit_digest.removeprefix("sha256:") + ".json")
        commit = json.loads(commit_path.read_bytes())
        (commit_path if which == "commit" else root / commit["state"]["path"]).unlink()
        with pytest.raises(FencedCommitError) as excinfo:
            local.read_operation_state(result, session_id="test", operation_id="op-1", intent_digest=intent, record_version=2, materialization=materialization)
        assert excinfo.value.code == "record-missing"


    def test_replaying_the_same_operation_carries_its_committed_state_end_to_end(self, tmp_path):
        """Production join: executor -> read_operation_state -> replayed_document."""
        from mission_kernel import project_legacy_document

        local, _root, states = _committed_repository(tmp_path)
        # A third operation moves the head; then op-1 is run again -> replay.
        _v5(local, "op-3").execute_retry_safe_evidence_plan(_plan("build/c.json"))
        # The same operation again: same operation id, same plan (intent), so
        # the unit of work answers with the recorded commit instead of a new one.
        _prepared, execution = _v5(local, "op-1").execute_retry_safe_evidence_plan(_plan("build/a.json"))
        assert execution.replayed is True
        assert json.dumps(execution.replayed_document, sort_keys=True) == json.dumps(
            json.loads(project_legacy_document(states["op-1"].state)), sort_keys=True
        )
        assert execution.projection != execution.replayed_document, "the head has moved since op-1"


# ----------------------------------------------------------------- ports


class TestReplayResultCarriesTheHistoricalState:
    def test_a_replay_without_the_state_is_refused(self):
        from mission_application.ports import LegacyCommandExecutionResult
        from mission_kernel.json_codec import freeze_json_value

        with pytest.raises(ValueError, match="legacy-command-replay-result-invalid"):
            LegacyCommandExecutionResult(None, freeze_json_value({"a": 1}), replayed=True)

    def test_a_non_replay_must_not_carry_one(self):
        from mission_application.ports import LegacyCommandExecutionResult
        from mission_kernel.json_codec import freeze_json_value
        frozen = freeze_json_value({"a": 1})
        with pytest.raises(ValueError, match="legacy-command-replay-result-invalid"):
            LegacyCommandExecutionResult(_accepted_decision(), frozen, replayed=False, replayed_state=frozen)

    def test_the_document_is_thawed_for_callers(self):
        from mission_application.ports import LegacyCommandExecutionResult
        from mission_kernel.json_codec import freeze_json_value

        result = LegacyCommandExecutionResult(
            None, freeze_json_value({"now": 1}), replayed=True, replayed_state=freeze_json_value({"then": 0})
        )
        assert result.replayed_document == {"then": 0}
        assert result.projection == {"now": 1}


def _accepted_decision():
    """A closed accepted decision: transition present, rejection absent."""
    from mission_kernel.transitions import Decision

    return Decision(True, SimpleNamespace(events=()), None)


# ----------------------------------------------------------------- executor


class TestTheExecutorReadsTheHistoricalStateInsideTheTransaction:
    def test_a_replay_result_carries_the_state_of_the_replayed_operation(self):
        from mission_application.evidence import prepare_progress_update

        current = {"phase": "executing", "loop_active": True, "session_id": "portable", "marker": "now"}
        historical = {"phase": "executing", "loop_active": True, "session_id": "portable", "marker": "then"}
        calls = []
        repository = in_memory_v5_repository(current, replayed=True, replayed_document=historical, read_calls=calls)

        _prepared, execution = repository.execute_evidence_transition_effects(  # opens its own transaction
            lambda state: prepare_progress_update(
                state, now=AT, total=1, completed=0, batch_size=1, last_unit=None,
                artifact_path=None, iteration=1, evidence_path="progress.json",
            )
        )
        assert execution.replayed is True
        assert execution.replayed_document["marker"] == "then"
        assert execution.projection["marker"] == "now"
        # Read with the identity the replay was admitted under, inside the transaction.
        assert len(calls) == 1
        assert calls[0]["session_id"] == "portable" and calls[0]["operation_id"] == "op"
        assert calls[0]["inside_transaction"] is True


# ----------------------------------------------------------------- application


def _render(_state, artifact):
    return "# " + artifact.get("title", "")


def _documents():
    """Historical state after an init+append+export+publish sequence, and a later current."""
    from mission_kernel.artifact import (
        append_artifact_block_document,
        export_artifact_document,
        initialize_artifact_document,
        record_artifact_publication_document,
    )

    base = {"phase": "executing", "loop_active": True, "session_id": "portable", "mission": "g"}
    after_init = initialize_artifact_document(
        base, at="2030-01-01T00:00:00Z", path="artifacts/mission-artifact.md", format="markdown",
        title="T", redaction_status="unchecked", required_for_pass=False, effect=None,
    )
    # The historical records carry fields the replaying request does not pass
    # (`source`, a different redaction status), so a payload built from the
    # prepared result instead of the historical state is distinguishable.
    after_append = append_artifact_block_document(
        after_init, at=AT, section="plan", content="first", source="historical-source", label=None
    )
    after_export = export_artifact_document(
        after_append, at=AT, destination="out/a.md", redaction_status="reviewed",
        artifact_effect=None, export_effect=None,
    )
    after_publish = record_artifact_publication_document(
        after_export, at=AT, provider="prov", destination="https://example.invalid/x",
        approval_text="ok", confirmed=True, effect=None,
    )
    # Intervening operations after the replayed ones: another append/export/publish later.
    later = append_artifact_block_document(
        after_publish, at="2030-01-02T00:00:00Z", section="plan", content="second", source=None, label=None
    )
    later = export_artifact_document(
        later, at="2030-01-02T00:00:00Z", destination="out/a.md", redaction_status="checked",
        artifact_effect=None, export_effect=None,
    )
    later = record_artifact_publication_document(
        later, at="2030-01-02T00:00:00Z", provider="prov", destination="https://example.invalid/y",
        approval_text="ok", confirmed=True, effect=None,
    )
    return {"init": after_init, "append": after_append, "export": after_export,
            "publish": after_publish, "current": later}


class _ReplayingRepository:
    """Runs prepare against the current state and answers a replay with a historical one."""

    def __init__(self, current, historical):
        self.current = current
        self.historical = historical

    def execute_transition_effects(self, prepare, **_kwargs):
        from mission_application.ports import LegacyCommandExecutionResult
        from mission_kernel.json_codec import freeze_json_value

        prepared = prepare(copy.deepcopy(self.current))
        return prepared, LegacyCommandExecutionResult(
            None, freeze_json_value(self.current), replayed=True,
            replayed_state=freeze_json_value(self.historical),
        )


class TestArtifactReplayPayloads:
    def test_append_returns_its_own_block_not_the_latest(self):
        from mission_application.artifact import ArtifactAppendRequest, run_artifact_append

        docs = _documents()
        payload = run_artifact_append(
            ArtifactAppendRequest(now=AT, section="plan", content="first", source=None, label=None),
            _ReplayingRepository(docs["current"], docs["append"]),
        )
        assert set(payload) == {"section", "block"}
        assert payload["block"] == docs["append"]["artifact"]["blocks"][-1]
        assert payload["block"]["source"] == "historical-source", "the prepared block has no source"

    def test_export_returns_its_own_export_and_the_current_artifact(self):
        from mission_application.artifact import ArtifactExportRequest, run_artifact_export

        docs = _documents()
        payload = run_artifact_export(
            ArtifactExportRequest(now=AT, destination="out/a.md", redaction_status="checked"),
            _ReplayingRepository(docs["current"], docs["export"]), _render,
        )
        assert set(payload) == {"export", "artifact"}
        assert payload["export"] == docs["export"]["artifact"]["exports"][-1]
        assert payload["export"]["redaction_status"] == "reviewed", "the prepared export says checked"
        assert payload["artifact"] == docs["current"]["artifact"], "artifact is the current projection"

    def test_publish_returns_its_own_event(self):
        from mission_application.artifact import ArtifactPublishRequest, run_artifact_publish

        docs = _documents()
        payload = run_artifact_publish(
            ArtifactPublishRequest(now=AT, provider="prov", destination="https://example.invalid/x",
                                   approval_text="ok", confirmed=True),
            _ReplayingRepository(docs["current"], docs["publish"]), _render,
        )
        assert set(payload) == {"publish_event", "artifact"}
        assert payload["publish_event"] == docs["publish"]["artifact"]["publish_events"][-1]
        assert "artifact_path" not in payload["publish_event"], "the prepared event binds an artifact_path"
        assert payload["artifact"] == docs["current"]["artifact"]

    def test_render_and_init_return_the_current_artifact(self):
        from mission_application.artifact import (
            ArtifactInitRequest, ArtifactRenderRequest, run_artifact_init, run_artifact_render,
        )

        docs = _documents()
        rendered = run_artifact_render(
            ArtifactRenderRequest(now=AT, redaction_status="unchecked"),
            _ReplayingRepository(docs["current"], docs["export"]), _render,
        )
        assert set(rendered) == {"path", "artifact"}
        assert rendered["artifact"] == docs["current"]["artifact"]
        initialised = run_artifact_init(
            ArtifactInitRequest(now="2030-01-01T00:00:00Z", artifact_path="artifacts/mission-artifact.md",
                                format="markdown", title="T", redaction_status="unchecked", required_for_pass=False),
            _ReplayingRepository(docs["current"], docs["init"]), _render,
        )
        assert set(initialised) == {"artifact"}

    def test_a_historical_state_that_does_not_carry_the_record_is_a_mismatch(self):
        from mission_application.artifact import ArtifactAppendRequest, EvidenceFailure, run_artifact_append

        docs = _documents()
        with pytest.raises(EvidenceFailure) as excinfo:
            run_artifact_append(
                ArtifactAppendRequest(now=AT, section="plan", content="first", source=None, label=None),
                _ReplayingRepository(docs["current"], docs["init"]),  # no blocks yet
            )
        assert excinfo.value.code == "artifact-projection-mismatch"

    def test_a_retry_with_its_own_clock_gets_the_records_timestamp(self):
        """#747 P2: the timestamp is store-authoritative, so a retry that arrives
        with a later clock is still the same operation and receives what the
        record holds, not what it sent."""
        from mission_application.artifact import ArtifactAppendRequest, run_artifact_append

        docs = _documents()
        payload = run_artifact_append(
            ArtifactAppendRequest(now="2030-01-01T00:00:09Z", section="plan", content="first",
                                  source=None, label=None),
            _ReplayingRepository(docs["current"], docs["append"]),
        )
        assert payload["block"]["timestamp"] == AT

    def test_a_record_without_a_timestamp_is_a_mismatch(self):
        from mission_application.artifact import ArtifactAppendRequest, EvidenceFailure, run_artifact_append

        docs = _documents()
        broken = json.loads(json.dumps(docs["append"]))
        broken["artifact"]["blocks"][-1]["timestamp"] = 7
        with pytest.raises(EvidenceFailure) as excinfo:
            run_artifact_append(
                ArtifactAppendRequest(now=AT, section="plan", content="first", source=None, label=None),
                _ReplayingRepository(docs["current"], broken),
            )
        assert excinfo.value.code == "artifact-projection-mismatch"

    def test_a_missing_decision_without_replay_is_still_rejected(self):
        """`decision=None` without `replayed=True` is not a replay; it stays a rejection."""
        from mission_application.artifact import ArtifactAppendRequest, EvidenceFailure, run_artifact_append

        docs = _documents()

        class _Forged:  # a foreign result shape: no decision, not a replay
            decision = None
            replayed = False
            projection = docs["current"]

        class _NoDecision:
            def execute_transition_effects(self, prepare, **_kwargs):
                return prepare(copy.deepcopy(docs["current"])), _Forged()

        with pytest.raises(EvidenceFailure) as excinfo:
            run_artifact_append(
                ArtifactAppendRequest(now=AT, section="plan", content="first", source=None, label=None),
                _NoDecision(),
            )
        assert excinfo.value.code == "artifact-transition-rejected"


# ----------------------------------------------------------------- CLI


def test_the_collected_history_code_is_an_expected_gate_to_the_cli():
    import importlib.util
    import sys

    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("issue747_history_outcome", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module._fenced_cli_outcome_kind("operation-history-collected") == "expected-gate"
    assert module._fenced_cli_outcome_kind("record-missing") == "internal-error"


def test_executor_handoff_reports_collected_history_as_an_expected_gate(tmp_path, monkeypatch, capsys):
    """The real path: the command lets FencedCommitError reach main's classifier.

    executor-handoff used to catch FencedCommitError itself and exit as
    invalid-input, so a replay whose generation was collected -- or a lease
    rejection -- never reached `_fenced_cli_outcome_kind`.
    """
    import importlib.util
    import sys

    from .mission_state_fixture_corpus import generate_cli_state_bytes
    from mission_persistence.fenced_commit import FencedCommitError

    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("issue747_handoff_outcome", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    state_path, _bytes = generate_cli_state_bytes(tmp_path / "cli-init")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(module, "resolve_state_file", lambda _cwd: Path(state_path))

    class _Collected:
        def execute_transition_effects(self, _prepare, **_kwargs):
            raise FencedCommitError("operation-history-collected", "the generation is no longer retained")

    monkeypatch.setattr(module, "_legacy_lifecycle_repository", lambda *a, **k: _Collected())
    monkeypatch.setattr(sys, "argv", ["mission-state.py", "executor-handoff", "begin"])

    with pytest.raises(SystemExit) as excinfo:
        module.main()
    # main() re-raises the classified exit, so the classification is observable
    # on the exception itself (executor-handoff has no --json envelope).
    assert excinfo.value.code == 2
    assert getattr(excinfo.value, "outcome_kind", None) == "expected-gate"
    err = capsys.readouterr().err
    assert "no longer retained" in err
    assert "executor handoff rejected" not in err, "the local invalid-input handler must not catch it"


def test_the_pass_through_use_case_refuses_to_run_without_a_pass_through():
    """The default that let FencedCommitError be swallowed is gone; `()` is refused too."""
    from mission_application.planning import run_transition_effects

    with pytest.raises(TypeError):
        run_transition_effects(object(), lambda s: s)  # keyword required
    with pytest.raises(ValueError, match="non-empty passthrough"):
        run_transition_effects(object(), lambda s: s, passthrough=())
