"""#6: stats に by_project / by_complexity / iteration_histogram 内訳が出る."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "mission_state", Path(__file__).resolve().parents[1] / "bin" / "mission-state.py"
)
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)


def _state(project, complexity, agent, passes, iteration, composite=4.2):
    return {
        "project_root": project, "complexity": complexity, "agent": agent,
        "passes": passes, "loop_active": False, "halt_reason": "",
        "iteration": iteration,
        "score_history": [{"composite": composite, "min_item": 4.0}] if composite else [],
    }


def test_aggregate_has_project_complexity_histogram():
    states = [
        _state("/Users/x/dev/alpha", "Complex", "claude-code", True, 2),
        _state("/Users/x/dev/alpha", "Complex", "codex", True, 1),
        _state("/Users/x/dev/beta", "Standard", "claude-code", False, 0, composite=None),
    ]
    agg = ms._aggregate(states)
    # by_project
    assert agg["by_project"]["alpha"]["total"] == 2
    assert agg["by_project"]["alpha"]["pass"] == 2
    assert agg["by_project"]["beta"]["total"] == 1
    # by_complexity
    assert agg["by_complexity"]["Complex"]["total"] == 2
    assert agg["by_complexity"]["Standard"]["total"] == 1
    # iteration_histogram
    assert agg["iteration_histogram"]["2"] == 1
    assert agg["iteration_histogram"]["1"] == 1
    assert agg["iteration_histogram"]["0"] == 1


def test_empty_aggregate_has_breakdown_keys():
    agg = ms._aggregate([])
    assert agg["by_project"] == {}
    assert agg["by_complexity"] == {}
    assert agg["iteration_histogram"] == {}


def test_format_text_renders_breakdowns():
    states = [_state("/Users/x/dev/alpha", "Complex", "claude-code", True, 2)]
    agg = ms._aggregate(states)
    txt = ms._format_text(agg, None, None)
    assert "by_project:" in txt and "alpha" in txt
    assert "by_complexity:" in txt and "Complex" in txt
    assert "iteration_histogram:" in txt
