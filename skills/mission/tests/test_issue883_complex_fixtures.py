"""#883: complex fixture cohorts distinguish repairs from plausible regressions."""

import importlib.util
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "benchmarks" / "mission-vs-goal" / "complex_fixture_benchmark.py"


def _load():
    spec = importlib.util.spec_from_file_location("complex_fixture_benchmark", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_complex_fixture_catalog_has_two_tasks_for_each_required_family():
    module = _load()
    catalog = module.load_catalog(ROOT / "benchmarks" / "mission-vs-goal" / "complex-fixtures")

    assert len(catalog) == 12
    assert {entry["family"] for entry in catalog} == {
        "multi_module", "compatibility", "partial_failure", "rerun",
        "aggregation", "concurrency",
    }
    assert all(sum(item["family"] == entry["family"] for item in catalog) == 2 for entry in catalog)
    assert all(entry["realistic_failure"] and entry["requirement"] and entry["dependency"] for entry in catalog)


def test_starters_fail_but_reference_repairs_and_good_controls_pass_external_evaluation():
    module = _load()
    root = ROOT / "benchmarks" / "mission-vs-goal" / "complex-fixtures"
    catalog = module.load_catalog(root)

    for entry in catalog:
        starter = module.evaluate_candidate(root, entry, root / "worker" / entry["id"])
        repair = module.evaluate_candidate(root, entry, root / "reference" / entry["id"])
        control = module.evaluate_candidate(root, entry, root / "control" / entry["id"])
        assert starter["status"] == "failed", entry["id"]
        assert repair["status"] == "passed", entry["id"]
        assert control["status"] == "passed", entry["id"]
        for record in (starter, repair, control):
            assert record["task_id"] == entry["id"]
            assert record["family"] == entry["family"]
            assert record["version"] == entry["version"]
            assert record["candidate_digest"].startswith("sha256:")
            assert record["case_count"] > 0


def test_worker_fixture_has_no_evaluator_or_reference_material_and_public_smoke_is_separate():
    module = _load()
    root = ROOT / "benchmarks" / "mission-vs-goal" / "complex-fixtures"
    for entry in module.load_catalog(root):
        worker = root / "worker" / entry["id"]
        names = {path.name for path in worker.rglob("*")}
        assert {"README.md", "public_smoke.py", "boundary.py", "service.py"} <= names
        assert not {"evaluator.json", "reference", "control", "answer-key"} & names
        assert module.run_public_smoke(worker)["status"] == "passed"


def test_evaluator_fails_closed_for_no_cases_and_timeout(monkeypatch, tmp_path):
    module = _load()
    root = ROOT / "benchmarks" / "mission-vs-goal" / "complex-fixtures"
    entry = module.load_catalog(root)[0]
    no_cases = {**entry, "checks": []}
    record = module.evaluate_candidate(root, no_cases, root / "worker" / entry["id"])
    assert record["status"] == "failed"
    assert record["reason"] == "no_evaluation_cases"

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(module.subprocess.TimeoutExpired(args[0], 1)))
    record = module.evaluate_candidate(root, entry, tmp_path)
    assert record["status"] == "blocked"
    assert record["reason"] == "evaluator_timeout"


def test_worker_export_uses_the_positive_allowlist_from_native_goal_benchmark(tmp_path):
    module = _load()
    root = ROOT / "benchmarks" / "mission-vs-goal" / "complex-fixtures"
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    exported = module.export_worker_fixtures(ROOT, commit, tmp_path / "worker", root)
    paths = {path.relative_to(exported).as_posix() for path in exported.rglob("*") if path.is_file()}
    assert paths
    assert all("/reference/" not in f"/{path}" and "/control/" not in f"/{path}" for path in paths)
    assert not any("catalog.json" in path or "evaluator" in path for path in paths)
