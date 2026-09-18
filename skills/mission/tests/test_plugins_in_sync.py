"""Plugin mirror checks that are not duplicate byte-for-byte sync assertions.

Recursive tree equality is covered by ``test_codex_wrapper_sync.py``. This
module keeps distribution-specific import smoke tests, critical marker checks,
and Python 3.9 compatibility checks.
"""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from mission_python_inventory import (
    assert_python_module_inventory_compatible,
    discover_python_module_inventory,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
LIB_ROOT = REPO_ROOT / "skills" / "mission" / "lib"
PLUGIN_LIB_ROOT = REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "lib"
PYTHON_LIBRARY_INVENTORY = discover_python_module_inventory(LIB_ROOT, PLUGIN_LIB_ROOT)

MISSION_STATE_DISTRIBUTION_MARKERS = (
    "specialist accounting required before pass",
    "PREPARATION_ONLY_MARKERS",
)


def test_provider_eligibility_py_is_importable():
    """Planning eligibility contract is importable from the plugin."""
    dst = PLUGIN_LIB_ROOT / "provider_eligibility.py"
    spec = importlib.util.spec_from_file_location("plugin_provider_eligibility", dst)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    result = module.normalize_selection_source("auto")
    assert result["selection_source"] == "automatic"


def test_provider_public_contract_py_is_importable():
    """Public provider-state hygiene is importable from the plugin."""
    dst = PLUGIN_LIB_ROOT / "provider_public_contract.py"
    spec = importlib.util.spec_from_file_location("plugin_provider_public_contract", dst)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.validate_specialist_public_state({"specialists_candidates": []})


def test_specialist_lifecycle_py_is_importable():
    """The plugin lifecycle contract imports and mints an identifier."""
    dst = PLUGIN_LIB_ROOT / "specialist_lifecycle.py"
    spec = importlib.util.spec_from_file_location("plugin_specialist_lifecycle", dst)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.new_selection_id().startswith("sel_")


def test_planning_lifecycle_py_is_importable():
    dst = PLUGIN_LIB_ROOT / "planning_lifecycle.py"
    spec = importlib.util.spec_from_file_location("plugin_planning_lifecycle", dst)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.derive_planning_lifecycle({"phase": "planning"})["mode"] == "legacy-core"


def test_planning_provider_metrics_py_is_importable():
    """The versioned KPI reducer imports and accepts an empty observation set."""
    dst = PLUGIN_LIB_ROOT / "planning_provider_metrics.py"
    spec = importlib.util.spec_from_file_location("plugin_planning_provider_metrics", dst)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    result = module.reduce_planning_provider_kpis([], population_kind="observed")
    assert result["schema"] == "mission-planning-provider-kpi/1"


def test_review_learning_py_is_importable():
    dst = PLUGIN_LIB_ROOT / "review_learning.py"
    spec = importlib.util.spec_from_file_location("plugin_review_learning", dst)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.learning_identity(
        "execution", "validate every boundary"
    ).startswith("sha256:")


def test_plugin_mirror_specialist_recommend_cli_smoke(tmp_path):
    """The distributed CLI imports its mirrored provider contract in a real process."""
    cli = REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "bin" / "mission-state.py"
    result = subprocess.run(
        [
            sys.executable,
            str(cli),
            "specialists",
            "recommend",
            "--no-default-skill-roots",
            "--task",
            "Update README documentation",
            "--installed-skills",
            "documentation-provider",
            "--json",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["specialists_candidates"][0]["provider_id"] == "documentation-provider"


def test_mission_state_distribution_contains_specialist_accounting_guards():
    """Both distribution entry points keep critical accounting markers."""
    paths = (
        REPO_ROOT / "skills" / "mission" / "bin" / "mission-state.py",
        REPO_ROOT / "plugins" / "mission" / "skills" / "mission" / "bin" / "mission-state.py",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8")
        missing = [
            marker for marker in MISSION_STATE_DISTRIBUTION_MARKERS if marker not in text
        ]
        assert not missing, f"{path} is missing distribution-critical markers: {missing}"


def test_recursive_python_library_inventory_is_python39_compatible():
    """Every recursive plugin module passes Python 3.9 parse/import."""
    assert_python_module_inventory_compatible(PYTHON_LIBRARY_INVENTORY)
