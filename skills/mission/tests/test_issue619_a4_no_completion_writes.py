"""Issue #619 批1-c: A4.specialist-planning は完了隣接フィールドを書かない.

実測: 対象 9 command（planning adopt-core / promote-provider-plan / reselect、
specialists invoke-command / log-invocation / plan-import /
prepare-invocation / reconcile-invocation / verify-approval）の完了隣接言及は
すべて読み（phase 検証・invocation record の属性）であり、書き込みは存在
しない。本テストはその不在を AST で固定し、A4 に completion authority が
後から混入する backslide を CI で検出する（provider は evidence provider に
限定し pass / review / score authority を持たせない境界の静的固定）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


MISSION_STATE_SOURCE = (
    Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
)

# 批1-c の対象 9 command の実装関数（parser の set_defaults から確定）
A4_COMMAND_FUNCTIONS = (
    "cmd_planning_adopt_core",
    "cmd_planning_promote_provider_plan",
    "cmd_planning_reselect",
    "cmd_invoke_command_provider",
    "cmd_log_specialist_invocation",
    "cmd_plan_import",
    "cmd_prepare_provider_invocation",
    "cmd_reconcile_provider_invocation",
    "cmd_verify_provider_approval",
)

# 完了隣接 control フィールド + provider が持ってはならない score authority
FORBIDDEN_WRITE_KEYS = frozenset(
    {
        "terminal_outcome",
        "phase",
        "passes",
        "loop_active",
        "halt_reason",
        "halt_category",
        "score_history",
    }
)

# completion authority を直接呼ぶヘルパー（A4 からの呼び出しは境界違反）
FORBIDDEN_HELPER_CALLS = frozenset(
    {
        "_transition_phase",
        "_write_terminal_outcome",
        "derive_terminal_outcome",
        "run_mark_halt",
        "run_mark_pass",
        "monotonic_halt_decision",
    }
)

SESSION_AUTHORITY_KEYS = FORBIDDEN_WRITE_KEYS | frozenset(
    {
        "executor_handoff",
        "decisions",
        "task_profile",
        "specialists_candidates",
        "specialists_selected",
        "specialists_unavailable",
        "specialists_ineligible",
        "specialist_registry_projection",
        "specialists_decision",
        "specialists_phase_plan",
        "specialists_mode",
        "planning_policy_version",
        "planning_strategy",
        "planning_contract_digest",
        "planning_provider_binding",
        "updated_at",
    }
)
SESSION_RESOLVER_CALLS = frozenset(
    {
        "resolve_state_file",
        "session_file",
        "_activity_state_file",
        "_legacy_lifecycle_repository",
        "LocalFencedRepository",
        "decode_mission_state",
        "decide",
    }
)
SESSION_WRITER_CALLS = frozenset(
    {
        "save",
        "execute",
        "execute_transition_effects",
        "stage",
        "commit",
        "run_mark_halt",
        "run_mark_pass",
        "_transition_phase",
        "_write_terminal_outcome",
    }
)


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _literal_keys(node: ast.expr) -> set[str]:
    if isinstance(node, ast.Dict):
        return {
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
    return set()


def _path_tokens(
    node: ast.AST | None, bindings: dict[str, frozenset[str]]
) -> frozenset[str]:
    if node is None:
        return frozenset()
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return frozenset(
            segment
            for segment in node.value.replace("\\", "/").split("/")
            if segment
        )
    if isinstance(node, ast.Name):
        return bindings.get(node.id, frozenset())
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _path_tokens(node.left, bindings) | _path_tokens(
            node.right, bindings
        )
    if isinstance(node, ast.Call):
        tokens = _path_tokens(node.func, bindings)
        for argument in node.args:
            tokens |= _path_tokens(argument, bindings)
        return tokens
    if isinstance(node, ast.Attribute):
        return _path_tokens(node.value, bindings)
    return frozenset()


def _session_path_bindings(function: ast.AST) -> dict[str, frozenset[str]]:
    bindings: dict[str, frozenset[str]] = {}
    assignments = [
        node
        for node in ast.walk(function)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
    ]
    for _ in range(len(assignments) + 1):
        changed = False
        for assignment in assignments:
            value = assignment.value
            targets = (
                assignment.targets
                if isinstance(assignment, ast.Assign)
                else [assignment.target]
            )
            tokens = _path_tokens(value, bindings)
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and bindings.get(target.id) != tokens
                ):
                    bindings[target.id] = tokens
                    changed = True
        if not changed:
            break
    return bindings


def _is_session_storage_path(tokens: frozenset[str]) -> bool:
    return ".mission-state" in tokens and "sessions" in tokens


def find_completion_write_violations(function: ast.FunctionDef) -> list[str]:
    """Return human-readable violations of the A4 no-completion-write contract."""
    violations: list[str] = []
    for node in ast.walk(function):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                    and target.slice.value in FORBIDDEN_WRITE_KEYS
                ):
                    violations.append(
                        f"L{node.lineno}: assigns forbidden key "
                        f"{target.slice.value!r}"
                    )
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in FORBIDDEN_HELPER_CALLS:
                violations.append(f"L{node.lineno}: calls forbidden helper {name}")
            if name == "update":
                for argument in node.args:
                    forbidden = _literal_keys(argument) & FORBIDDEN_WRITE_KEYS
                    if forbidden:
                        violations.append(
                            f"L{node.lineno}: update() with forbidden keys "
                            f"{sorted(forbidden)}"
                        )
            if name == "setdefault" and node.args:
                first = node.args[0]
                if (
                    isinstance(first, ast.Constant)
                    and isinstance(first.value, str)
                    and first.value in FORBIDDEN_WRITE_KEYS
                ):
                    violations.append(
                        f"L{node.lineno}: setdefault of forbidden key "
                        f"{first.value!r}"
                    )
    return violations


def find_session_write_violations(
    entrypoint: str, tree: ast.Module
) -> list[str]:
    """Follow local helpers and report reachability to session authority."""
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    violations: list[str] = []
    visited: set[str] = set()

    def inspect(name: str) -> None:
        if name in visited or name not in functions:
            return
        visited.add(name)
        function = functions[name]
        path_bindings = _session_path_bindings(function)
        for node in ast.walk(function):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if ".mission-state/sessions" in node.value:
                    violations.append(
                        f"L{node.lineno}: references session storage path"
                    )
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and _is_session_storage_path(
                _path_tokens(node.value, path_bindings)
            ):
                violations.append(
                    f"L{node.lineno}: references session storage path"
                )
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = (
                    node.targets
                    if isinstance(node, ast.Assign)
                    else [node.target]
                )
                for target in targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value in SESSION_AUTHORITY_KEYS
                    ):
                        violations.append(
                            f"L{node.lineno}: assigns session key "
                            f"{target.slice.value}"
                        )
            if isinstance(node, ast.Delete):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value in SESSION_AUTHORITY_KEYS
                    ):
                        violations.append(
                            f"L{node.lineno}: deletes session key "
                            f"{target.slice.value}"
                        )
            if not isinstance(node, ast.Call):
                continue
            if any(
                _is_session_storage_path(_path_tokens(argument, path_bindings))
                for argument in node.args
            ):
                violations.append(
                    f"L{node.lineno}: references session storage path"
                )
            call = _call_name(node)
            if call in SESSION_RESOLVER_CALLS:
                violations.append(
                    f"L{node.lineno}: calls session helper {call}"
                )
            if call in SESSION_WRITER_CALLS:
                violations.append(
                    f"L{node.lineno}: calls session writer {call}"
                )
            if call in {"setdefault", "pop"} and node.args:
                key = node.args[0]
                if (
                    isinstance(key, ast.Constant)
                    and key.value in SESSION_AUTHORITY_KEYS
                ):
                    violations.append(
                        f"L{node.lineno}: mutates session key {key.value}"
                    )
            if call == "update":
                for argument in node.args:
                    for key in _literal_keys(argument) & SESSION_AUTHORITY_KEYS:
                        violations.append(
                            f"L{node.lineno}: updates session key {key}"
                        )
            if isinstance(node.func, ast.Name):
                inspect(node.func.id)

    inspect(entrypoint)
    return sorted(set(violations))


def _module_functions() -> dict[str, ast.FunctionDef]:
    tree = ast.parse(MISSION_STATE_SOURCE.read_text(encoding="utf-8"))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }


def test_a4_command_functions_exist():
    functions = _module_functions()
    missing = [name for name in A4_COMMAND_FUNCTIONS if name not in functions]
    assert missing == []


@pytest.mark.parametrize("name", A4_COMMAND_FUNCTIONS)
def test_a4_commands_do_not_write_completion_adjacent_fields(name):
    functions = _module_functions()
    violations = find_completion_write_violations(functions[name])
    assert violations == [], f"{name} writes completion authority: {violations}"


def test_guard_detects_forbidden_subscript_assignment():
    """ガード自体の検出力を固定する（劣化した検査で null を信じない）。"""
    source = (
        "def offender(data):\n"
        "    data[\"passes\"] = True\n"
        "    data.update({\"phase\": \"done\"})\n"
        "    data.setdefault(\"halt_reason\", \"x\")\n"
        "    _write_terminal_outcome(data)\n"
    )
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    violations = find_completion_write_violations(function)
    assert len(violations) == 4


def test_guard_accepts_reads_and_neutral_writes():
    source = (
        "def reader(data, args):\n"
        "    if data.get(\"phase\") != \"planning\":\n"
        "        return\n"
        "    record = {\"phase\": str(args.phase)}\n"
        "    data[\"specialists_selected\"] = []\n"
        "    data[\"updated_at\"] = \"2026-08-23T00:00:00Z\"\n"
        "    return record\n"
    )
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    assert find_completion_write_violations(function) == []


def test_provider_generic_writes_cannot_reach_pass_authority():
    """provider 由来の汎用書き込み（extension_fields_decision 経由）は kernel の
    frozen 分類により pass / score authority へ到達できない。"""
    from mission_application.lifecycle import extension_fields_decision

    for field in ("passes", "score_history", "terminal_outcome", "threshold"):
        decision = extension_fields_decision(
            {"phase": "planning"}, {field: "provider-forged"}
        )
        assert decision.accepted is False
        assert decision.rejection.code == "frozen-field"


def test_consent_session_write_guard_detects_synthetic_transitive_escape():
    tree = ast.parse(
        """
def helper(data, repository):
    data["specialists_selected"] = []
    data["passes"] = True
    repository.save(data)

def offender(args):
    state_file = resolve_state_file(Path.cwd())
    repository = _legacy_lifecycle_repository(Path.cwd(), state_file, stamp=True)
    with repository.transaction():
        data = repository.load()
        helper(data, repository)
"""
    )

    violations = find_session_write_violations("offender", tree)

    assert {violation.split(":", 1)[1] for violation in violations} >= {
        " calls session helper resolve_state_file",
        " calls session helper _legacy_lifecycle_repository",
        " assigns session key specialists_selected",
        " assigns session key passes",
        " calls session writer save",
    }


def test_consent_guard_detects_split_session_path_and_atomic_write():
    tree = ast.parse(
        """
def offender(data):
    session_path = Path('.mission-state') / 'sessions' / 'forged.json'
    atomic_write_json(session_path, {'passes': True})
"""
    )

    violations = find_session_write_violations("offender", tree)

    assert any("references session storage path" in item for item in violations)


def test_specialists_consent_cannot_reach_session_authority():
    tree = ast.parse(MISSION_STATE_SOURCE.read_text(encoding="utf-8"))

    assert find_session_write_violations("cmd_specialists_consent", tree) == []


def test_consent_guard_allows_its_separate_provider_consent_aggregate():
    tree = ast.parse(
        """
def consent(provider, consent_path):
    consent_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"providers": {}}
    providers = data.setdefault("providers", {})
    providers[provider] = {"granted_at": iso_now()}
    atomic_write_json(consent_path, data)
"""
    )

    assert find_session_write_violations("consent", tree) == []
