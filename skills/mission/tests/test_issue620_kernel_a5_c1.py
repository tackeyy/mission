"""Issue #620 批1-d: A5 + C1 の完了隣接 command を kernel 化する.

実測: permission-preflight（`record_permission_observation`）だけが完了隣接を
書き（blocked-external halt）、kernel 非経由だった。archive-worktree は
loop_active の読み、cleanup-empty / parallel-init は言及ゼロ（filesystem /
分離 aggregate のみ）、resolve-archive は U5-1 protocol 経由の derived
recompute のみ。permission halt を decide(MarkHalt) gate + #630 claims 検証
経由へ移し、降格 3 command には no-write ガードを張る。
"""

from __future__ import annotations

import ast
import contextlib
import copy
from pathlib import Path

import pytest


MISSION_STATE_SOURCE = (
    Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
)


class _RecordingRepository:
    def __init__(self, before):
        self._before = copy.deepcopy(before)
        self.executed_transition = "unset"
        self.saved = None

    def transaction(self):
        return contextlib.nullcontext()

    def load(self):
        return copy.deepcopy(self._before)

    def execute(self, state, mutation, transition=None, finalize=None):
        from mission_persistence.legacy_v4 import _apply_transition_claims

        self.executed_transition = transition
        proposed = copy.deepcopy(state)
        mutation(proposed)
        # 批2-a-3 (#632): 実 repository は mutation の後に claims を適用する。
        # double が適用しないと「writer を消した field」が落ちて偽陽性になる。
        if transition is not None:
            _apply_transition_claims(transition, proposed)
        if finalize is not None:
            finalize(proposed)
        self._executed = copy.deepcopy(proposed)
        return proposed

    def save(self, state, *, backup=True, administrative=False, aggregate_action=None):
        self.saved = copy.deepcopy(state)


def _active_state() -> dict:
    return {
        "schema_version": 4,
        "mission": "issue620 fixture mission",
        "phase": "planning",
        "iteration": 1,
        "loop_active": True,
        "passes": False,
        "halt_reason": "",
        "session_role": "implementer",
        "updated_at": "2026-08-23T00:00:00Z",
    }


def _denied_request():
    from mission_application.runtime_guard import (
        PermissionObservationRequest,
        PermissionProbe,
    )

    return PermissionObservationRequest(
        probes=(PermissionProbe("state", "denied", "write-unavailable"),),
        observed_at="2026-08-23T01:00:00Z",
    )


def test_permission_halt_gates_through_kernel_decision():
    from mission_application.runtime_guard import record_permission_observation

    repository = _RecordingRepository(_active_state())
    result = record_permission_observation(repository, _denied_request())

    assert result.halt_recorded is True
    assert result.decision is not None
    assert result.decision.accepted is True
    assert result.decision.rule_id == "mark-halt"
    # 認可済み transition が execute へ渡り、#630 claims 検証の対象になる
    assert repository.executed_transition is result.decision.transition
    assert repository.saved["phase"] == "halted"
    assert repository.saved["loop_active"] is False
    assert repository.saved["halt_category"] == "blocked-external"
    assert repository.saved["terminal_outcome"] == "blocked_external"


def test_permission_allowed_observation_has_no_decision():
    from mission_application.runtime_guard import (
        PermissionObservationRequest,
        PermissionProbe,
        record_permission_observation,
    )

    repository = _RecordingRepository(_active_state())
    result = record_permission_observation(
        repository,
        PermissionObservationRequest(
            probes=(
                PermissionProbe("state", "allowed", None),
                PermissionProbe("assumptions", "allowed", None),
            ),
            observed_at="2026-08-23T01:00:00Z",
        ),
    )
    assert result.ok is True
    assert result.decision is None
    assert repository.saved is None


def test_permission_halt_claims_hold_through_real_repository():
    """#630 の claims 検証が有効な実 repository で halt が成立する end-to-end。"""
    from mission_application.runtime_guard import record_permission_observation
    from mission_kernel.transitions import transition_control_claims
    from mission_persistence.legacy_v4 import LegacyV4Repository

    before = _active_state()
    saved = {}
    repository = LegacyV4Repository(
        lock=contextlib.nullcontext,
        read_state=lambda: copy.deepcopy(before),
        write_state=lambda state: saved.update(copy.deepcopy(state)),
        backup_state=lambda: None,
    )
    result = record_permission_observation(repository, _denied_request())

    claims = transition_control_claims(result.decision.transition)
    for field, value in claims.items():
        expected = value.value if field == "phase" else value
        assert saved.get(field, False) == expected


# --- 降格 3 command の no-write ガード（批1-c #619 と同型の静的固定） ---

DEMOTED_FUNCTIONS = (
    "cmd_archive_worktree",
    "cmd_cleanup_empty",
    "cmd_parallel_init",
)

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


def _violations(function: ast.FunctionDef) -> list[str]:
    found: list[str] = []
    for node in ast.walk(function):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value in FORBIDDEN_WRITE_KEYS
                ):
                    found.append(f"L{node.lineno}: assigns {target.slice.value!r}")
        if isinstance(node, ast.Call):
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else None
            )
            if name in FORBIDDEN_HELPER_CALLS:
                found.append(f"L{node.lineno}: calls {name}")
    return found


@pytest.mark.parametrize("name", DEMOTED_FUNCTIONS)
def test_demoted_commands_do_not_write_completion_adjacent_fields(name):
    tree = ast.parse(MISSION_STATE_SOURCE.read_text(encoding="utf-8"))
    functions = {
        node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)
    }
    assert name in functions
    assert _violations(functions[name]) == []


def test_guard_detects_synthetic_violation():
    source = "def offender(data):\n    data[\"loop_active\"] = False\n"
    function = ast.parse(source).body[0]
    assert isinstance(function, ast.FunctionDef)
    assert _violations(function) != []
