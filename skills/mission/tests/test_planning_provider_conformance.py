"""#399 conformance projections reuse #394-#398 state contracts, not new E2E flows."""
import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))
from planning_provider_metrics import reduce_planning_provider_kpis


@pytest.mark.parametrize("name,state,assertion", [
    ("simple-floor", {"planning_policy_version": 1, "complexity": "Simple", "planning_strategy": "core"}, lambda t: t["eligible_complex_planning_selection"]["denominator"] == 0),
    ("unknown-floor", {"planning_policy_version": 1, "complexity": "Unknown", "planning_strategy": "core"}, lambda t: t["eligible_complex_planning_selection"]["denominator"] == 0),
    ("complex-primary", {"planning_policy_version": 1, "complexity": "Complex", "planning_strategy": "provider-primary"}, lambda t: t["eligible_complex_planning_selection"]["rate"] == 1.0),
    ("optional-fallback", {"planning_policy_version": 1, "complexity": "Complex", "planning_strategy": "core", "specialist_invocations": [{"phase": "planning", "status": "failed", "reason_code": "provider-unavailable"}]}, lambda t: t["eligible_complex_planning_selection"]["rate"] == 0.0),
    ("approval-wait", {"planning_policy_version": 1, "provider_preflights": {"p": {"status": "awaiting-approval"}}}, lambda t: t["preflight_live_digest_match"]["denominator"] == 0),
    ("invalid-plan", {"planning_policy_version": 1, "specialist_invocations": [{"phase": "planning", "status": "failed", "reason_code": "invalid-plan"}]}, lambda t: t["canonical_plan_executor_lineage"]["denominator"] == 0),
    ("legacy", {"complexity": "Complex", "legacy_session_retroactive_provider_invocations": 0}, lambda t: t["legacy_session_retroactive_provider_invocations"] == 0),
])
def test_existing_lifecycle_state_contracts_reduce_to_conformance_kpis(name, state, assertion):
    totals = reduce_planning_provider_kpis([state], population_kind="controlled")["totals"]
    assert assertion(totals), name
