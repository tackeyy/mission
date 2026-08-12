import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB_DIR))

from planning_provider_metrics import PlanningProviderMetricError, reduce_planning_provider_kpis


def test_zero_denominator_rate_is_null_and_required_totals_exist():
    result = reduce_planning_provider_kpis([], population_kind="controlled")
    totals = result["totals"]
    assert result["schema"] == "mission-planning-provider-kpi/1"
    assert totals["eligible_complex_planning_selection"] == {"numerator": 0, "denominator": 0, "rate": None}
    assert totals["preflight_live_digest_match"]["rate"] is None
    assert totals["canonical_plan_executor_lineage"]["rate"] is None


def test_reducer_rejects_unknown_population_kind():
    with pytest.raises(PlanningProviderMetricError, match="population"):
        reduce_planning_provider_kpis([], population_kind="unknown")
