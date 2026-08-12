import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from planning_lifecycle import derive_planning_lifecycle


def test_policy_v1_primary_without_invocation_prepares_one_safe_action():
    state = {
        "planning_policy_version": 1,
        "phase": "planning",
        "iteration": 1,
        "planning_strategy": "provider-primary",
        "specialists_selected": [{"provider_id": "planner"}],
        "specialist_invocations": [],
    }
    assert derive_planning_lifecycle(state)["next_action"] == "prepare-planning-provider"


@pytest.mark.parametrize(
    ("status", "expected"),
    [("running", "reconcile-provider-invocation"), ("completed", "import-planning-result")],
)
def test_running_invocation_reconciles_before_any_new_action(status, expected):
    state = {
        "planning_policy_version": 1, "phase": "planning", "iteration": 1,
        "planning_strategy": "provider-primary",
        "specialist_invocations": [{"invocation_id": "inv_" + "a" * 32, "phase": "planning", "iteration": 1, "status": status}],
    }
    assert derive_planning_lifecycle(state)["next_action"] == expected


def test_legacy_policy_absent_stays_core_without_provider_action():
    state = {"phase": "planning", "iteration": 1, "specialist_invocations": []}
    result = derive_planning_lifecycle(state)
    assert result["mode"] == "legacy-core"
    assert result["next_action"] == "run-planner"
