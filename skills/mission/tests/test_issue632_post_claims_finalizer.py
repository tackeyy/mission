"""Issue #632: post-claims finalizer and terminal outcome unification."""

from __future__ import annotations

import ast
import contextlib
import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from .mission_state_fixture_corpus import generate_cli_state_bytes


def _halt_transition(tmp_path):
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide

    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    decision = decide(
        decode_snapshot(source).state,
        MarkHalt(HaltCategory.BLOCKED_EXTERNAL, "blocked"),
    )
    assert decision.accepted and decision.transition is not None
    return decision.transition


def _legacy_repository(*, writes=None):
    from mission_persistence.legacy_v4 import LegacyV4Repository

    return LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: {},
        write_state=lambda document, **_kwargs: writes.append(copy.deepcopy(document)) if writes is not None else None,
        backup_state=lambda: None,
    )


def test_supersede_marker_matches_legacy_string_normalization():
    from mission_common import is_supersede_marked, terminal_outcome_for_halt

    assert is_supersede_marked(" Superseded ", "") is True
    assert is_supersede_marked("", "SUPERSEDED BY A REPLACEMENT RUN") is True
    assert terminal_outcome_for_halt(
        "blocked-external", "implementer", superseded=True
    ) == "stale_superseded"
    assert terminal_outcome_for_halt(
        "evidence-submitted", "checker", superseded=False
    ) == "completed_evidence"


def test_markhalt_rejects_a_non_bool_supersede_marker(tmp_path):
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide

    from .mission_state_fixture_corpus import generate_cli_state_bytes

    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    state = decode_snapshot(source).state
    decision = decide(
        state,
        MarkHalt(HaltCategory.BLOCKED_EXTERNAL, "blocked", superseded=1),
    )

    assert decision.accepted is False
    assert decision.rejection.code == "invalid-supersede-marker"


def test_execute_calls_finalize_after_claims(tmp_path):
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory
    from mission_kernel.transitions import decide
    from mission_persistence.legacy_v4 import LegacyV4Repository
    from .mission_state_fixture_corpus import generate_cli_state_bytes
    import contextlib

    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    transition = decide(
        decode_snapshot(source).state, MarkHalt(HaltCategory.BLOCKED_EXTERNAL, "blocked")
    ).transition
    repository = LegacyV4Repository(lock=contextlib.nullcontext, read_state=lambda: {}, write_state=lambda state: None, backup_state=lambda: None)
    result = repository.execute(
        {"phase": "planning", "loop_active": True, "passes": False},
        lambda document: None,
        transition,
        lambda document: document.update({"finalized": document["terminal_outcome"]}),
    )
    assert result["finalized"] == "blocked_external"


def test_finalize_cannot_overwrite_claimed_fields(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    with pytest.raises(FencedCommitError, match="finalizer diverges") as error:
        _legacy_repository().execute(
            {"phase": "planning", "loop_active": True},
            lambda document: None,
            _halt_transition(tmp_path),
            lambda document: document.update({"phase": "reviewing"}),
        )
    assert error.value.code == "transition-divergence"


def test_prepare_state_cannot_change_claimed_fields_before_serialization(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError
    from mission_persistence.legacy_v4 import V5CompatibilityRepository

    repository = V5CompatibilityRepository(
        repository=None,
        session_id="issue632",
        lease_owner_session_id="issue632",
        presented_lease_id=None,
        prepare_state=lambda document: dict(document, phase="reviewing"),
    )
    proposed = repository.execute(
        {"phase": "planning", "loop_active": True}, lambda document: None,
        _halt_transition(tmp_path),
    )
    # _replayed makes save return before backend admission; verification must
    # nevertheless happen before that early return.
    repository._replayed = object()
    with pytest.raises(FencedCommitError) as error:
        repository.save(proposed)
    assert error.value.code == "transition-divergence"


def test_saving_a_document_other_than_the_executed_one_is_rejected(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    repository = _legacy_repository()
    with repository.transaction():
        repository.execute({"phase": "planning", "loop_active": True}, lambda document: None, _halt_transition(tmp_path))
        with pytest.raises(FencedCommitError, match="save target") as error:
            repository.save({"phase": "halted", "loop_active": False})
    assert error.value.code == "transition-divergence"


def test_second_save_of_the_same_document_is_verified_again(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    writes = []
    repository = _legacy_repository(writes=writes)
    with repository.transaction():
        proposed = repository.execute({"phase": "planning", "loop_active": True}, lambda document: None, _halt_transition(tmp_path))
        repository.save(proposed)
        proposed["phase"] = "reviewing"
        with pytest.raises(FencedCommitError) as error:
            repository.save(proposed)
    assert len(writes) == 1
    assert error.value.code == "transition-divergence"


def test_failed_finalize_leaves_no_pending_claims(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    repository = _legacy_repository()
    with pytest.raises(FencedCommitError):
        repository.execute({"phase": "planning", "loop_active": True}, lambda document: None, _halt_transition(tmp_path), lambda document: document.update({"phase": "reviewing"}))
    assert repository._pending == []






# The following assertions deliberately reuse the first-stage behavioral
# corpus.  This file owns the finalizer/lifecycle and static-boundary tests;
# the corpus owns the expensive eight-path and compatibility permutations.




def test_mark_halt_saved_document_is_unchanged_for_every_category(tmp_path):
    from . import test_issue631_real_state_halt as corpus
    from mission_kernel.model import HaltCategory, SessionRole
    from mission_kernel.transitions import transition_control_claims
    from mission_application.lifecycle import MarkHaltRequest, MarkHaltServices, mark_halt

    for category in HaltCategory:
        for role in SessionRole:
            saved = {}
            state = corpus._active_state(session_role=role.value)
            repository = _legacy_repository(writes=[])
            repository._read_state = lambda state=state: copy.deepcopy(state)
            repository._write_state = lambda document, **_kwargs: saved.update(copy.deepcopy(document))
            result = mark_halt(
                repository,
                MarkHaltRequest("blocked", category.value, "2030-08-23T00:00:00Z", True),
                MarkHaltServices(
                    lambda *_args: None,
                    lambda document, phase, _at, **_kwargs: document.update({"phase": phase}),
                    lambda _state: {
                        "goal_dispatch_effective": True,
                        "goal_dispatch_host": "test-host",
                    },
                ),
            )
            assert result.decision.accepted
            for field, value in transition_control_claims(result.decision.transition).items():
                assert saved[field] == getattr(value, "value", value)


def test_supersede_marker_is_propagated_from_every_markhalt_construction_site():
    # The three construction sites must all pass the normalized marker into
    # MarkHalt; dynamic source inspection prevents a later site from silently
    # reverting to the role-independent legacy derivation.
    targets = (
        ("monotonic_halt_decision", _SOURCE_ROOT / "lib" / "mission_application" / "lifecycle.py"),
        ("mark_halt", _SOURCE_ROOT / "lib" / "mission_application" / "lifecycle.py"),
        ("route_simple_to_goal", _SOURCE_ROOT / "lib" / "mission_application" / "lifecycle.py"),
    )
    for name, path in targets:
        function = _function_from_source(path.read_text(encoding="utf-8"), name)
        calls = [node for node in ast.walk(function) if isinstance(node, ast.Call) and _name_of_call(node) == "MarkHalt"]
        assert calls
        assert any(keyword.arg == "superseded" and isinstance(keyword.value, ast.Call) and _name_of_call(keyword.value) == "is_supersede_marked" for call in calls for keyword in call.keywords)


def test_kernel_and_legacy_derivations_agree_for_every_category_and_role(tmp_path):
    from dataclasses import replace

    from mission_common import terminal_outcome_for_halt
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import MarkHalt
    from mission_kernel.model import HaltCategory, SessionRole
    from mission_kernel.transitions import decide

    # Use one decodable active state and replace only the typed session role;
    # this makes all 9 x 5 combinations a kernel-vs-legacy comparison.
    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    base = decode_snapshot(source).state

    for category in HaltCategory:
        for role in SessionRole:
            state = replace(base, control=replace(base.control, session_role=role))
            decision = decide(state, MarkHalt(category, "blocked", superseded=False))
            assert decision.accepted and decision.transition is not None
            assert decision.transition.new_state.terminal_outcome.value == terminal_outcome_for_halt(category.value, role.value, superseded=False)




def test_third_value_injection_is_rejected_on_every_path(tmp_path):
    # The repository invariant is path-independent: every transition path
    # reaches execute(), which rejects a value neither before nor after.
    from mission_persistence.fenced_commit import FencedCommitError

    from mission_kernel.transitions import transition_control_claim_bounds

    bounds = transition_control_claim_bounds(_halt_transition(tmp_path))
    thirds = {
        "phase": "reviewing",
        "loop_active": "not-a-bool",
        "halt_category": "stale",
        "terminal_outcome": "completed_pass",
    }
    for field, third in thirds.items():
        before, after = bounds[field]
        assert third not in {getattr(before, "value", before), getattr(after, "value", after)}
        with pytest.raises(FencedCommitError):
            _legacy_repository().execute({"phase": "planning", "loop_active": True}, lambda document, field=field, third=third: document.update({field: third}), _halt_transition(tmp_path))


def test_halt_category_claim_is_absent_when_the_document_already_matches(tmp_path):
    from mission_kernel.transitions import transition_control_claim_bounds

    transition = _halt_transition(tmp_path)
    assert "halt_category" in transition_control_claim_bounds(transition)














def test_set_extension_fields_transition_claims_no_control_change(tmp_path):
    from mission_kernel.commands import SetExtensionFields
    from mission_kernel.json_codec import freeze_json_value
    from mission_kernel.transitions import decide, transition_control_claim_bounds
    from mission_kernel import decode_snapshot

    _path, source = generate_cli_state_bytes(tmp_path.resolve())
    decision = decide(decode_snapshot(source).state, SetExtensionFields(freeze_json_value({"custom": "value"})))
    assert transition_control_claim_bounds(decision.transition) == {}














def test_permission_preflight_and_init_report_internal_invariant_without_traceback(monkeypatch, capsys):
    from mission_persistence.fenced_commit import FencedCommitError

    path = Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
    spec = importlib.util.spec_from_file_location("issue632_invariant_cli", path)
    cli = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = cli
    spec.loader.exec_module(cli)
    monkeypatch.setattr(cli, "_permission_preflight", lambda _cwd: (_ for _ in ()).throw(FencedCommitError("transition-divergence", "test")))
    with pytest.raises(SystemExit) as result:
        cli.cmd_permission_preflight(type("Args", (), {"json": True})())
    assert result.value.code == 2
    assert "internal-invariant: transition-divergence" in capsys.readouterr().err


# --- acceptance condition 11: structural guard for post-claim closures ---

_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_CLOSURE_SPECS = (
    ("mark_pass", _SOURCE_ROOT / "lib" / "mission_application" / "review.py", {"passes", "loop_active", "terminal_outcome"}),
    ("mark_halt", _SOURCE_ROOT / "lib" / "mission_application" / "lifecycle.py", {"loop_active", "halt_category", "terminal_outcome"}),
    ("record_permission_observation", _SOURCE_ROOT / "lib" / "mission_application" / "runtime_guard.py", {"loop_active", "halt_category", "terminal_outcome"}),
    ("_supersede_reviews_locked", _SOURCE_ROOT / "bin" / "mission-state.py", {"loop_active", "halt_category", "terminal_outcome"}),
)
_INDIRECT_WRITERS = frozenset({"_write_terminal_outcome", "write_terminal_outcome"})


def _name_of_call(node):
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _literal_strings(node):
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return {item.value for item in node.elts if isinstance(item, ast.Constant) and isinstance(item.value, str)}
    if isinstance(node, ast.Dict):
        return {key.value for key in node.keys if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    return set()


def _guarded_by_claim(node, parents, field):
    current = node
    while id(current) in parents:
        parent = parents[id(current)]
        if isinstance(parent, ast.If) and isinstance(parent.test, ast.Compare):
            test = parent.test
            if (len(test.ops) == 1 and isinstance(test.ops[0], ast.NotIn)
                    and isinstance(test.left, ast.Constant) and test.left.value == field
                    and len(test.comparators) == 1 and isinstance(test.comparators[0], ast.Name)
                    and test.comparators[0].id == "claimed"):
                return True
        current = parent
    return False


def find_unguarded_claim_writes(function, forbidden):
    """Find direct and indirect compatibility writes outside their claim guard."""
    parents = {id(child): parent for parent in ast.walk(function) for child in ast.iter_child_nodes(parent)}
    violations = []
    for node in ast.walk(function):
        fields = set()
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Subscript):
                    fields |= _literal_strings(target.slice)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if isinstance(target, ast.Subscript):
                    fields |= _literal_strings(target.slice)
        elif isinstance(node, ast.Call):
            name = _name_of_call(node)
            if name in _INDIRECT_WRITERS:
                fields.add("terminal_outcome")
            elif name in {"update", "setdefault", "pop"}:
                fields |= _literal_strings(node.args[0]) if node.args else set()
        for field in fields & forbidden:
            if not _guarded_by_claim(node, parents, field):
                violations.append("L%s: unguarded write to %r" % (node.lineno, field))
    return violations


def _function_from_source(source, name):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("function not found: " + name)


def _mutation_closure(function):
    """Return the compatibility ``mutate`` closure, not its enclosing use case."""
    closures = [
        node for node in ast.walk(function)
        if isinstance(node, ast.FunctionDef) and node.name == "mutate" and node is not function
    ]
    assert len(closures) == 1, "expected exactly one mutate closure"
    return closures[0]


@pytest.mark.parametrize(("name", "path", "forbidden"), _CLOSURE_SPECS)
def test_post_claim_closures_have_no_unguarded_claim_writes(name, path, forbidden):
    function = _function_from_source(path.read_text(encoding="utf-8"), name)
    closure = _mutation_closure(function)
    assert find_unguarded_claim_writes(closure, forbidden) == []


def test_claim_write_guard_detects_direct_assignment_fixture():
    function = _function_from_source(
        "def mutate(proposed, claimed):\n    proposed['loop_active'] = False\n", "mutate"
    )
    assert find_unguarded_claim_writes(function, {"loop_active"})


def test_claim_write_guard_detects_indirect_writer_fixture():
    function = _function_from_source(
        "def mutate(proposed, claimed):\n    _write_terminal_outcome(proposed)\n", "mutate"
    )
    assert find_unguarded_claim_writes(function, {"terminal_outcome"})


# --- 保存 document の全 key/value 一致（現行 main の保存結果を golden として固定） ---
#
# golden は main `ba5a87c` で同じ driver を実行して採取した。driver は固定の
# timestamp しか使わないため決定的であり、claim 化・writer 削除・finalizer 導入が
# 保存結果を 1 field でも動かしたらここで落ちる（受け入れ条件 4 / 6 / 7 / 15）。

_MAIN_SAVED_DOCUMENTS = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "issue632_main_saved_documents.json").read_text(
        encoding="utf-8"
    )
)


def _transition_paths():
    from . import test_issue632_transition_is_the_writer as harness

    return {
        name: getattr(harness, name)
        for name in dir(harness)
        if name.startswith("_path_")
    }


# 実行時刻・実行ホスト・実行環境に由来する field は golden 採取時と一致しない
# （CI と開発機でも異なる）。完了隣接 field は 1 つも含まれないため、比較から
# 除外しても本 PR の検出力は落ちない。fixture 側では採取環境の値が漏れないよう
# これらを placeholder へ差し替えてある。
_ENVIRONMENT_DERIVED_FIELDS = frozenset(
    {
        "activity_rollup",
        "activity_segments",
        "command_outcomes",
        "created_at_session",
        "goal_dispatch_host",
        "host_run_id",
        "hostname",
        "last_activity_at",
        "lease_expires_at",
        "phase_durations_sec",
        "pid",
        "pid_source",
        "project_root",
        "root_run_id",
        "score_history",
        "specialists_decision",
        "started_at",
    }
)

_CLAIMABLE_FIELDS = ("phase", "passes", "loop_active", "halt_category", "terminal_outcome")

_ENVIRONMENT_PLACEHOLDER = "<environment-derived>"


def _strip_environment(document):
    return {
        key: value
        for key, value in document.items()
        if key not in _ENVIRONMENT_DERIVED_FIELDS
    }


@pytest.mark.parametrize("path_name", sorted(_MAIN_SAVED_DOCUMENTS))
def test_saved_document_matches_main_on_every_transition_path(tmp_path, path_name):
    driver = _transition_paths()[path_name]
    saved = {}
    driver(tmp_path, saved)

    golden = _MAIN_SAVED_DOCUMENTS[path_name]
    assert "__error__" not in golden, "golden fixture captured a driver failure"
    assert set(saved) == set(golden), "key 集合が現行 main と一致すること"

    for key in _ENVIRONMENT_DERIVED_FIELDS & set(golden):
        assert golden[key] == _ENVIRONMENT_PLACEHOLDER, (
            "環境由来 field は fixture 側で placeholder 化しておくこと: %s" % key
        )
    differing = {key for key in golden if saved[key] != golden[key]}
    assert differing <= _ENVIRONMENT_DERIVED_FIELDS, (
        "環境由来以外の field が現行 main と食い違っている: %s" % sorted(differing)
    )
    # 完了隣接 field は環境由来の除外に一切かからない（検出力の担保）。
    assert not (_ENVIRONMENT_DERIVED_FIELDS & set(_CLAIMABLE_FIELDS))
    for field in _CLAIMABLE_FIELDS:
        assert (field in saved) == (field in golden)
        if field in golden:
            assert saved[field] == golden[field]


@pytest.mark.parametrize("path_name", sorted(_MAIN_SAVED_DOCUMENTS))
def test_saved_document_matches_the_decided_claims_on_every_transition_path(
    tmp_path, path_name
):
    """claim された field は保存値と一致し、claim の無い field は claim 判定に現れない。"""
    from mission_kernel.transitions import (
        transition_control_claim_bounds,
        transition_control_claims,
    )

    driver = _transition_paths()[path_name]
    saved = {}
    decision = driver(tmp_path, saved)
    if decision is None or getattr(decision, "transition", None) is None:
        pytest.skip("this path intentionally sends no transition")

    bounds = transition_control_claim_bounds(decision.transition)
    for field, after in transition_control_claims(decision.transition).items():
        expected = after.value if hasattr(after, "value") else after
        before = bounds[field][0]
        assert before != after, "claim は before と after が異なる field にだけ生じる"
        if expected is None:
            assert field not in saved
        else:
            assert saved[field] == expected


def test_golden_comparison_detects_a_claim_regression(tmp_path):
    """検出力の実証: claim 値を 1 つ変えた偽 golden は必ず不一致になる。"""
    driver = _transition_paths()["_path_mark_pass"]
    saved = {}
    driver(tmp_path, saved)
    tampered = dict(_MAIN_SAVED_DOCUMENTS["_path_mark_pass"])
    tampered["terminal_outcome"] = "failed"
    assert saved != tampered


# --- finalizer の契約（設計書 §3） ---


def test_finalize_cannot_reintroduce_a_removed_field(tmp_path):
    """after=None の claim は「field 不在」。finalizer が明示 None を戻したら fail-closed."""
    from mission_kernel import decode_snapshot
    from mission_kernel.commands import Reactivate
    from mission_kernel.model import HaltCategory, Phase
    from mission_kernel.transitions import decide, transition_control_claims
    from mission_persistence.fenced_commit import FencedCommitError

    halted = {
        "schema_version": 4,
        "mission": "issue632 finalize",
        "phase": "halted",
        "iteration": 1,
        "loop_active": False,
        "passes": False,
        "halt_reason": "external dependency down",
        "halt_category": "blocked-external",
        "terminal_outcome": "blocked_external",
        "session_role": "implementer",
        "updated_at": "2030-08-23T00:00:00Z",
    }
    state = decode_snapshot(
        json.dumps(halted, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).state
    decision = decide(
        state,
        Reactivate(
            HaltCategory.BLOCKED_EXTERNAL,
            "operator restarted the run",
            True,
            Phase.EXECUTING,
        ),
    )
    assert decision.accepted and decision.transition is not None
    assert transition_control_claims(decision.transition)["terminal_outcome"] is None

    def mutate(document):
        document.pop("terminal_outcome", None)
        document["halt_reason"] = ""
        document["loop_active"] = True
        document["phase"] = "executing"
        document.pop("halt_category", None)

    repository = _legacy_repository()
    with pytest.raises(FencedCommitError) as error:
        repository.execute(
            copy.deepcopy(halted),
            mutate,
            decision.transition,
            lambda document: document.update({"terminal_outcome": None}),
        )
    assert error.value.code == "transition-divergence"


def test_finalize_requires_a_transition(tmp_path):
    from mission_persistence.fenced_commit import FencedCommitError

    repository = _legacy_repository()
    with pytest.raises(FencedCommitError) as error:
        repository.execute(
            {"phase": "planning", "loop_active": True, "passes": False},
            lambda document: None,
            None,
            lambda document: None,
        )
    assert error.value.code == "request-invalid"


def test_execute_without_save_does_not_leak_claims_into_the_next_transaction(tmp_path):
    """transaction を抜けたら pending は破棄され、次の無関係な save は素通りする."""
    writes = []
    repository = _legacy_repository(writes=writes)
    transition = _halt_transition(tmp_path)
    with repository.transaction():
        repository.execute(
            {"phase": "planning", "loop_active": True, "passes": False},
            lambda document: None,
            transition,
        )
    with repository.transaction():
        repository.save({"phase": "planning", "loop_active": True, "passes": False})
    assert len(writes) == 1


# --- mark_pass の force 経路（設計書 §4・受け入れ条件 5） ---


def _force_pass(tmp_path, harness, cli, *, verification, validate):
    from dataclasses import replace as _replace
    from mission_application.review import MarkPassRequest, mark_pass

    saved = {}
    repository = harness._in_memory_repository(
        harness._review_state(tmp_path), saved=saved
    )
    mark_pass(
        repository,
        MarkPassRequest(True, "forced for the test", True, "", "2030-08-23T01:00:00Z"),
        _replace(
            harness._pass_services(cli),
            verify_force_approval=lambda _data: copy.deepcopy(verification),
            validate_force_terminal=validate,
        ),
    )
    return saved


def test_force_validation_runs_against_the_post_claims_document(tmp_path):
    """検出力: 突合は claims 適用後の document で行われ、適用前だと digest が一致しない."""
    from . import test_issue632_transition_is_the_writer as harness

    cli = harness._load_cli_module("issue632_force_digest")
    observed = {}

    def _spy(document, _verification):
        observed["digest"] = cli.terminal_state_digest(document)
        observed["passes"] = document.get("passes")
        observed["loop_active"] = document.get("loop_active")
        observed["terminal_outcome"] = document.get("terminal_outcome")

    saved = _force_pass(
        tmp_path,
        harness,
        cli,
        verification={"consumed": False, "request": {"terminal_object_digest": "placeholder"}},
        validate=_spy,
    )

    # finalizer は claims 適用後に走る。
    assert observed["passes"] is True
    assert observed["loop_active"] is False
    assert observed["terminal_outcome"] == "completed_pass"
    assert observed["digest"] == cli.terminal_state_digest(saved)
    assert saved["force_approval"]["consumed"] is True

    # 同じ突合を claims 適用前（passes / terminal_outcome 未設定）で行うと不一致になる。
    pre_claims = copy.deepcopy(saved)
    pre_claims.pop("passes", None)
    pre_claims.pop("terminal_outcome", None)
    assert cli.terminal_state_digest(pre_claims) != observed["digest"]


def test_force_approval_binding_holds_with_the_real_validator(tmp_path):
    """実物の `_validate_force_pass_terminal` が post-claims document で成立する."""
    from . import test_issue632_transition_is_the_writer as harness

    cli = harness._load_cli_module("issue632_force_real")
    observed = {}
    _force_pass(
        tmp_path,
        harness,
        cli,
        verification={"consumed": False, "request": {"terminal_object_digest": "placeholder"}},
        validate=lambda document, _v: observed.update(
            {"digest": cli.terminal_state_digest(document)}
        ),
    )
    saved = _force_pass(
        tmp_path,
        harness,
        cli,
        verification={
            "consumed": False,
            "request": {"terminal_object_digest": observed["digest"]},
        },
        validate=cli._validate_force_pass_terminal,
    )
    assert saved["passes"] is True
    assert saved["terminal_outcome"] == "completed_pass"
    assert saved["force_approval"]["consumed"] is True


# --- permission observation（設計書 §2b / §4 / §4b） ---


def _permission_observation(document, *, saved):
    from . import test_issue632_transition_is_the_writer as harness
    from mission_application.runtime_guard import (
        PermissionObservationRequest,
        PermissionProbe,
        record_permission_observation,
    )

    repository = harness._in_memory_repository(document, saved=saved)
    return record_permission_observation(
        repository,
        PermissionObservationRequest(
            probes=(PermissionProbe("state", "denied", "write-unavailable"),),
            observed_at="2030-08-23T01:00:00Z",
        ),
    )


def test_permission_observation_saved_document_is_unchanged(tmp_path):
    """通常 active state は従来どおり blocked_external を保存する."""
    from . import test_issue632_transition_is_the_writer as harness

    saved = {}
    result = _permission_observation(harness._active_document(), saved=saved)
    assert saved["terminal_outcome"] == "blocked_external"
    assert saved["halt_category"] == "blocked-external"
    assert saved["loop_active"] is False
    assert result.terminal_outcome == "blocked_external"
    assert saved == _MAIN_SAVED_DOCUMENTS["_path_permission_preflight"]


def test_permission_observation_on_supersede_marked_state_returns_the_persisted_outcome(
    tmp_path,
):
    """(A) の先行規則が kernel でも成立し、保存値・claim・戻り値が一致する."""
    from . import test_issue632_transition_is_the_writer as harness
    from mission_kernel.transitions import transition_control_claims

    saved = {}
    result = _permission_observation(
        harness._active_document(resolution_status="superseded"), saved=saved
    )
    claims = transition_control_claims(result.decision.transition)
    assert claims["terminal_outcome"].value == "stale_superseded"
    assert saved["terminal_outcome"] == "stale_superseded"
    assert result.terminal_outcome == "stale_superseded"

    # 現行 main は明示 blocked_external を書くため復号値と矛盾していた。解消を固定する。
    from mission_common import derive_terminal_outcome

    assert derive_terminal_outcome(saved) == "stale_superseded"


def test_permission_observation_result_is_derived_from_the_saved_document(tmp_path):
    from . import test_issue632_transition_is_the_writer as harness

    saved = {}
    result = _permission_observation(
        harness._active_document(resolution_status="superseded"), saved=saved
    )
    assert result.terminal_outcome == saved["terminal_outcome"]
    assert result.halt_category == saved["halt_category"]


@pytest.mark.parametrize(
    "outcome", ("completed_pass", "blocked_external", "stale_superseded")
)
def test_permission_observation_on_a_terminal_document_falls_back_to_gate_only(
    tmp_path, outcome
):
    """既に terminal な document では transition を送らず、従来の compat 書き込みが残る."""
    from . import test_issue632_transition_is_the_writer as harness

    terminal = harness._active_document(
        phase="done" if outcome == "completed_pass" else "halted",
        loop_active=False,
        passes=outcome == "completed_pass",
        halt_reason="" if outcome == "completed_pass" else "already halted",
        terminal_outcome=outcome,
    )
    saved = {}
    result = _permission_observation(copy.deepcopy(terminal), saved=saved)
    assert result.decision.transition is not None  # gate としては通っている
    # gate-only 経路では compat writer が固定値を書く（現行 main と同じ）。
    assert saved["terminal_outcome"] == "blocked_external"
    assert saved["halt_category"] == "blocked-external"
    assert saved["loop_active"] is False


# --- mark_halt の gate-only 経路（設計書 §4） ---


def test_mark_halt_gate_only_paths_still_write_compatibility_fields(tmp_path):
    from . import test_issue632_transition_is_the_writer as harness
    from mission_application.lifecycle import (
        MarkHaltRequest,
        MarkHaltServices,
        mark_halt,
    )

    saved = {}
    repository = harness._in_memory_repository(harness._active_document(), saved=saved)
    result = mark_halt(
        repository,
        MarkHaltRequest(
            reason="janitor orphan",
            category="stale",
            at="2030-08-23T01:00:00Z",
            set_terminal_phase=False,
        ),
        MarkHaltServices(
            reject_active_provider_mutation=lambda _state, _command: None,
            transition_phase=harness._timing_transition_phase,
            goal_dispatch_fields=lambda _state: {},
            terminalize_without_phase=lambda proposed, at, _stale: proposed.update(
                {"terminalized_at": at}
            ),
        ),
    )
    # set_terminal_phase=False は kernel の主張から意図的に逸脱する soft-terminal。
    assert saved["halt_category"] == "stale"
    assert saved["loop_active"] is False
    assert saved["terminal_outcome"] == "stale_superseded"
    assert saved["phase"] == "executing"
    assert result.decision.accepted


# --- goal-route（設計書 §2 の `_SetFieldsPlan`） ---


def _goal_route_services(cli, *, calls, guidance=None, tier=None):
    from mission_application.lifecycle import SetFieldsServices

    def _record(name, value):
        calls.append(name)
        return value

    return SetFieldsServices(
        frozen_fields=frozenset(cli.FROZEN_FIELDS),
        reject_active_provider_mutation=lambda _state, _command: calls.append(
            "reject_active_provider_mutation"
        ),
        normalize_phase=cli._normalize_set_phase_value,
        transition_phase=cli._transition_phase,
        ensure_phase_timing=lambda _state, _at: calls.append("ensure_phase_timing"),
        derive_review_tier=lambda *args: _record(
            "derive_review_tier", tier or cli.derive_review_tier(*args)
        ),
        derive_review_tier_decision=lambda *args: _record(
            "derive_review_tier_decision", cli.derive_review_tier_decision(*args)
        ),
        reviewer_count_by_tier=dict(cli.TIER_REVIEWER_COUNT),
        goal_dispatch_fields=lambda state: _record(
            "goal_dispatch_fields", cli._goal_dispatch_route_fields(state)
        ),
        goal_dispatch_guidance=lambda _dispatch, _prefix: _record(
            "goal_dispatch_guidance", guidance if guidance is not None else ""
        ),
    )


def _route_document():
    from . import test_issue632_transition_is_the_writer as harness

    return harness._active_document(phase="planning", iteration=1)


def _run_set_fields(document, kvs, services, *, saves):
    from mission_application.lifecycle import SetFieldsRequest, set_fields
    from mission_persistence.legacy_v4 import LegacyV4Repository

    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(document),
        write_state=lambda proposed, **kwargs: saves.append(
            (copy.deepcopy(proposed), kwargs)
        ),
        backup_state=lambda: None,
        add_to_aggregate=lambda: saves.append(("aggregate-add", {})),
        remove_from_aggregate=lambda: saves.append(("aggregate-remove", {})),
    )
    return set_fields(
        repository,
        SetFieldsRequest(kvs=kvs, at="2030-08-23T01:00:00Z"),
        services,
    )


def test_goal_route_sends_its_markhalt_transition(tmp_path):
    from . import test_issue632_transition_is_the_writer as harness
    from mission_kernel.commands import MarkHalt
    from mission_kernel.transitions import transition_control_claims

    cli = harness._load_cli_module("issue632_goal_route")
    calls, saves = [], []
    result = _run_set_fields(
        _route_document(), ("complexity=Simple",), _goal_route_services(cli, calls=calls), saves=saves
    )

    assert result.routed_verdict is not None
    assert result.routed_verdict["route"] == "goal"
    assert isinstance(result.decision.transition.new_state.control.halt_category.value, str)
    claims = transition_control_claims(result.decision.transition)
    assert claims["terminal_outcome"].value == "routed_elsewhere"

    document = next(saved for saved, _kwargs in saves if isinstance(saved, dict))
    assert document["terminal_outcome"] == "routed_elsewhere"
    assert document["halt_category"] == "routed-goal"
    assert document["loop_active"] is False


def test_goal_route_specific_services_are_called_once_per_plan(tmp_path):
    from . import test_issue632_transition_is_the_writer as harness

    cli = harness._load_cli_module("issue632_goal_route_once")
    calls, saves = [], []
    _run_set_fields(
        _route_document(), ("complexity=Simple",), _goal_route_services(cli, calls=calls), saves=saves
    )
    assert calls.count("goal_dispatch_fields") == 1
    assert calls.count("goal_dispatch_guidance") == 1
    assert calls.count("ensure_phase_timing") == 1


def test_goal_route_preserves_administrative_flag_and_aggregate_action(tmp_path):
    from . import test_issue632_transition_is_the_writer as harness

    cli = harness._load_cli_module("issue632_goal_route_admin")
    calls, saves = [], []
    _run_set_fields(
        _route_document(), ("complexity=Simple",), _goal_route_services(cli, calls=calls), saves=saves
    )
    write = next((saved, kwargs) for saved, kwargs in saves if isinstance(saved, dict))
    assert write[1].get("administrative") is True
    assert ("aggregate-remove", {}) in saves


@pytest.mark.parametrize(
    ("kvs", "reason"),
    (
        (("no-separator",), "key-value-format"),
        (("review_tier=bogus",), "review-tier-invalid"),
    ),
)
def test_set_fields_error_precedence_is_unchanged(tmp_path, kvs, reason):
    from . import test_issue632_transition_is_the_writer as harness
    from mission_application.lifecycle import LifecycleFailure

    cli = harness._load_cli_module("issue632_set_errors")
    calls, saves = [], []
    with pytest.raises(LifecycleFailure) as error:
        _run_set_fields(
            _route_document(), kvs, _goal_route_services(cli, calls=calls), saves=saves
        )
    assert error.value.reason == reason
    assert saves == [], "拒否時は保存しない"


def test_set_fields_service_call_sequence_is_unchanged_for_duplicate_keys(tmp_path):
    """重複 key の service 呼び出し回数は現行どおり（plan 化で 1 回に潰さない）."""
    from . import test_issue632_transition_is_the_writer as harness

    cli = harness._load_cli_module("issue632_set_duplicates")
    calls, saves = [], []
    _run_set_fields(
        _route_document(),
        ("review_tier=light", "complexity=Critical", "review_tier=light"),
        _goal_route_services(cli, calls=calls),
        saves=saves,
    )
    # `review_tier` の出現ごとに derive_review_tier が呼ばれる現行挙動を固定する。
    assert calls.count("derive_review_tier") == 2
    assert calls.count("derive_review_tier_decision") == 0


# set_fields は plan 化で service 呼び出し列・warning・保存 document が変わり得るため、
# main `ba5a87c` で同じ 3 シナリオを実行した結果を golden として固定する。
_MAIN_SET_FIELDS = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "issue632_main_set_fields.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize(
    ("label", "kvs"),
    (
        ("route", ("complexity=Simple",)),
        ("dupe", ("review_tier=light", "complexity=Critical", "review_tier=light")),
        ("order", ("complexity=Critical", "review_tier=light")),
    ),
)
def test_set_fields_matches_main_for_calls_warnings_and_saves(tmp_path, label, kvs):
    from . import test_issue632_transition_is_the_writer as harness

    cli = harness._load_cli_module("issue632_set_golden_" + label)
    calls, saves = [], []
    services = _goal_route_services(cli, calls=calls)
    # golden 側は reject_active_provider_mutation を "reject" として記録している。
    from dataclasses import replace as _replace

    services = _replace(
        services,
        reject_active_provider_mutation=lambda _state, _command: calls.append("reject"),
    )
    result = _run_set_fields(_route_document(), kvs, services, saves=saves)

    golden = _MAIN_SET_FIELDS[label]
    assert calls == golden["calls"]
    assert list(result.warnings) == golden["warnings"]
    normalized = [
        [_strip_environment(saved), sorted(kwargs.items(), key=str)]
        if isinstance(saved, dict)
        else [saved, []]
        for saved, kwargs in saves
    ]
    expected = [
        [_strip_environment(saved), kwargs] if isinstance(saved, dict) else [saved, kwargs]
        for saved, kwargs in golden["saves"]
    ]
    assert json.loads(json.dumps(normalized, default=str)) == expected
    routed = json.loads(json.dumps(result.routed_verdict, default=str))
    if isinstance(routed, dict):
        routed = _strip_environment(routed)
    expected_routed = golden["routed"]
    if isinstance(expected_routed, dict):
        expected_routed = _strip_environment(expected_routed)
    assert routed == expected_routed
