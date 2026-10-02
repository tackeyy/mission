"""#883: complex fixture cohorts distinguish repairs from plausible regressions."""

import importlib.util
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "benchmarks" / "mission-vs-goal" / "complex_fixture_benchmark.py"


def _load():
    spec = importlib.util.spec_from_file_location("complex_fixture_benchmark", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _materialize(module, tmp_path, task_id, group):
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    destination = tmp_path / f"{task_id}-{group}"
    module.materialize_task(ROOT, commit, task_id, group, destination)
    return destination / task_id


def _materialize_record(module, tmp_path, task_id, group):
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    destination = tmp_path / f"{task_id}-{group}"
    record = module.materialize_task(ROOT, commit, task_id, group, destination)
    return record, destination / task_id


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
    assert all(len(entry["checks"]) >= 3 for entry in catalog)


def test_starters_fail_but_reference_repairs_and_good_controls_pass_external_evaluation(tmp_path):
    module = _load()
    root = ROOT / "benchmarks" / "mission-vs-goal" / "complex-fixtures"
    catalog = module.load_catalog(root)

    for entry in catalog:
        starter = module.evaluate_candidate(root, entry, _materialize(module, tmp_path, entry["id"], "worker"))
        repair = module.evaluate_candidate(root, entry, _materialize(module, tmp_path, entry["id"], "reference"))
        control = module.evaluate_candidate(root, entry, _materialize(module, tmp_path, entry["id"], "control"))
        assert starter["status"] == "failed", entry["id"]
        assert repair["status"] == "passed", entry["id"]
        assert control["status"] == "passed", entry["id"]
        for record in (starter, repair, control):
            assert record["task_id"] == entry["id"]
            assert record["family"] == entry["family"]
            assert record["version"] == entry["version"]
            assert record["candidate_digest"].startswith("sha256:")
            assert record["case_count"] > 0


def test_worker_fixture_has_no_evaluator_or_reference_material_and_public_smoke_is_separate(tmp_path):
    module = _load()
    root = ROOT / "benchmarks" / "mission-vs-goal" / "complex-fixtures"
    for entry in module.load_catalog(root):
        worker = _materialize(module, tmp_path, entry["id"], "worker")
        names = {path.name for path in worker.rglob("*")}
        assert {"README.md", "public_smoke.py", "boundary.py", "service.py"} <= names
        assert not {"evaluator.json", "reference", "control", "answer-key"} & names
        source = "\n".join(path.read_text(encoding="utf-8") for path in worker.glob("*.py"))
        assert "MODE" not in source
        assert "BROKEN" not in source
        assert module.run_public_smoke(worker)["status"] == "passed"


def test_evaluator_fails_closed_for_no_cases_and_timeout(monkeypatch, tmp_path):
    module = _load()
    root = ROOT / "benchmarks" / "mission-vs-goal" / "complex-fixtures"
    entry = module.load_catalog(root)[0]
    no_cases = {**entry, "checks": []}
    record = module.evaluate_candidate(root, no_cases, _materialize(module, tmp_path, entry["id"], "worker"))
    assert record["status"] == "failed"
    assert record["reason"] == "no_evaluation_cases"

    monkeypatch.setattr(module, "_run_bounded", lambda *args, **kwargs: (None, b"", True, False, False))
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
    assert all("reference" not in path and "control" not in path for path in paths)
    assert not any("catalog.json" in path or "evaluator" in path for path in paths)
    manifest = __import__("json").loads((exported / "manifest.json").read_text())
    assert manifest["source_commit"] == commit
    assert manifest["schema"] == "mission-complex-fixture-export/1"
    assert manifest["assignment_count"] == 12
    assert manifest["catalog_digest"].startswith("sha256:")
    assert len(manifest["assignments"]) == 12
    for assignment in manifest["assignments"]:
        assert assignment["fixture_group"] == "worker"
        assert assignment["task_root"] == assignment["task_id"]
        assert assignment["family"] and assignment["version"]
        assert assignment["candidate_digest"] == assignment["worker_digest"]
        assert assignment["input_manifest_identity"].startswith("sha256:")


def test_concurrency_tasks_use_real_shared_state_and_evaluator_owned_scenarios(tmp_path):
    module = _load()
    root = ROOT / "benchmarks" / "mission-vs-goal" / "complex-fixtures"
    entries = {entry["id"]: entry for entry in module.load_catalog(root)}

    lost_update = entries["concurrency-lost-update"]
    assert [check["expected"]["final_state"] for check in lost_update["checks"]] == [2, 14, -1]
    assert all(check["expected"]["threads_completed"] == 2 for check in lost_update["checks"])

    ordering = entries["concurrency-order-independent"]
    assert {check["expected"]["winner"]["id"] for check in ordering["checks"]} == {"a", "b", "z"}
    assert all("scenario" in check for check in (*lost_update["checks"], *ordering["checks"]))

    for task_id in ("concurrency-lost-update", "concurrency-order-independent"):
        worker = _materialize(module, tmp_path, task_id, "worker")
        source = "\n".join(path.read_text(encoding="utf-8") for path in worker.glob("*.py"))
        assert "MODE" not in source
        assert "BROKEN" not in source
        assert {"boundary.py", "service.py", "store.py"} <= {path.name for path in worker.iterdir()}


def test_evaluator_materializes_candidate_and_bounds_output(tmp_path):
    module = _load()
    root = ROOT / "benchmarks" / "mission-vs-goal" / "complex-fixtures"
    entry = next(entry for entry in module.load_catalog(root) if entry["id"] == "concurrency-lost-update")
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "boundary.py").write_text("def normalise(value): return value\n", encoding="utf-8")
    (candidate / "store.py").write_text("", encoding="utf-8")
    (candidate / "service.py").write_text("def execute(value):\n    print('x' * 70000)\n    return {}\n", encoding="utf-8")
    before = {path.name: path.read_bytes() for path in candidate.iterdir()}

    record = module.evaluate_candidate(root, entry, candidate)

    assert record["status"] == "failed"
    assert record["reason"] == "evaluator_output_too_large"
    assert before == {path.name: path.read_bytes() for path in candidate.iterdir() if path.is_file()}


def test_templates_render_worker_reference_and_control_without_hidden_flags():
    generator_path = ROOT / "benchmarks" / "mission-vs-goal" / "generate_complex_fixtures.py"
    spec = importlib.util.spec_from_file_location("generate_complex_fixtures", generator_path)
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    root = ROOT / "benchmarks" / "mission-vs-goal" / "complex-fixtures"

    for group in ("worker", "reference", "control"):
        for task_id, _, requirement, _ in generator.TASKS:
            expected = generator.files(task_id, group == "worker", requirement=requirement)
            assert {"README.md", "boundary.py", "service.py"} <= set(expected)
            assert "MODE" not in "\n".join(expected.values())
            assert "BROKEN" not in "\n".join(expected.values())
            assert "Input:" in expected["README.md"]
            assert "Output:" in expected["README.md"]
            assert all(";" not in content for name, content in expected.items() if name.endswith(".py"))


def test_assignment_requires_fixed_manifest_worker_and_candidate_envelope(tmp_path):
    module = _load()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    assignment, worker = _materialize_record(module, tmp_path, "concurrency-lost-update", "worker")
    _, repair = _materialize_record(module, tmp_path, "concurrency-lost-update", "reference")
    envelope = module.freeze_candidate(assignment, repair)
    record = module.evaluate_assignment(ROOT, commit, assignment, worker, repair, envelope)
    assert record["status"] == "passed"

    record = module.evaluate_assignment(ROOT, commit, assignment, tmp_path / "wrong", repair, envelope)
    assert record["status"] == "failed"
    assert record["reason"] == "assignment_worker_mismatch"

    altered = {**envelope, "candidate_digest": assignment["worker_digest"]}
    record = module.evaluate_assignment(ROOT, commit, assignment, worker, repair, altered)
    assert record["status"] == "failed"
    assert record["reason"] == "candidate_digest_mismatch"

    altered_assignment = {**assignment, "input_manifest_identity": "sha256:" + "0" * 64}
    record = module.evaluate_assignment(ROOT, commit, altered_assignment, worker, repair, envelope)
    assert record["status"] == "failed"
    assert record["reason"] == "assignment_manifest_mismatch"


def test_materializer_binds_current_source_commit_and_creates_one_task_repo(tmp_path):
    module = _load()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    record = module.materialize_task(ROOT, commit, "concurrency-lost-update", "worker", tmp_path / "generated")
    assert record["source_commit"] == commit
    assert record["task_id"] == "concurrency-lost-update"
    assert record["generated_commit"]
    assert record["generator_digest"].startswith("sha256:")
    assert record["catalog_digest"].startswith("sha256:")
    assert record["generated_digest"] == record["worker_digest"]
    assert record["worker_digest"].startswith("sha256:")


def test_materializer_rejects_a_mutable_source_ref(tmp_path):
    module = _load()
    with __import__("pytest").raises(ValueError, match="full immutable SHA"):
        module.materialize_task(ROOT, "HEAD", "concurrency-lost-update", "worker", tmp_path / "generated")


def test_export_rejects_a_caller_catalog_root_that_is_not_the_fixed_source(tmp_path):
    module = _load()
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip()
    with __import__("pytest").raises(ValueError, match="catalog root"):
        module.export_worker_fixtures(ROOT, commit, tmp_path / "worker", tmp_path / "other")


def test_evaluator_rejects_a_snapshot_change_without_discarding_its_record(monkeypatch, tmp_path):
    module = _load()
    root = ROOT / "benchmarks" / "mission-vs-goal" / "complex-fixtures"
    entry = next(entry for entry in module.load_catalog(root) if entry["id"] == "concurrency-lost-update")
    candidate = _materialize(module, tmp_path, entry["id"], "worker")
    monkeypatch.setattr(module, "_materialize_candidate", lambda *args: (candidate, "sha256:changed"))

    record = module.evaluate_candidate(root, entry, candidate)

    assert record["status"] == "failed"
    assert record["reason"] == "candidate_changed"
    assert record["cases"] == []


def test_evaluator_distinguishes_json_boolean_from_number_and_rejects_invalid_cases(tmp_path):
    module = _load()
    root = ROOT / "benchmarks" / "mission-vs-goal" / "complex-fixtures"
    entry = next(entry for entry in module.load_catalog(root) if entry["id"] == "compatibility-legacy-default")
    candidate = _materialize(module, tmp_path, entry["id"], "reference")
    (candidate / "service.py").write_text("def execute(value): return {'state': 'open', 'version': True}\n", encoding="utf-8")
    record = module.evaluate_candidate(root, entry, candidate)
    assert record["status"] == "failed"
    assert record["reason"] == "contract_mismatch"

    for checks, reason in (([None], "evaluation_case_invalid"), ([{"name": "missing", "scenario": {}}], "evaluation_case_invalid"), ([{"name": "nan", "scenario": float("nan"), "expected": None}], "evaluation_case_non_json")):
        record = module.evaluate_candidate(root, {**entry, "checks": checks}, candidate)
        assert record["status"] == "failed"
        assert record["reason"] == reason

    (candidate / "service.py").write_text("def execute(value): return float('nan')\n", encoding="utf-8")
    record = module.evaluate_candidate(root, entry, candidate)
    assert record["status"] == "failed"
    assert record["reason"] == "evaluator_output_invalid"


def test_public_smoke_returns_a_failure_record_for_timeout_and_reaps_group_child(tmp_path):
    module = _load()
    candidate = tmp_path / "candidate"; candidate.mkdir()
    survivor = tmp_path / "survivor"
    descendant = f"import pathlib, time; time.sleep(.35); pathlib.Path({str(survivor)!r}).write_text('alive')"
    (candidate / "public_smoke.py").write_text(
        "import subprocess, sys\n" + f"subprocess.Popen([sys.executable, '-c', {descendant!r}])\n" + "raise SystemExit(0)\n",
        encoding="utf-8",
    )
    original = module._run_bounded
    module._run_bounded = lambda command, timeout_seconds: original(command, timeout_seconds=0.05)
    try:
        record = module.run_public_smoke(candidate)
    finally:
        module._run_bounded = original
    assert record["status"] == "failed"
    assert record["reason"] == "smoke_reader_incomplete"
    time.sleep(0.45)
    assert not survivor.exists()


def test_bounded_runner_reaps_a_parent_exit_descendant_holding_a_pipe(tmp_path):
    module = _load()
    pid_path = tmp_path / "descendant.pid"
    survivor_path = tmp_path / "descendant-survived"
    descendant = (
        "import pathlib, time; time.sleep(.35); "
        f"pathlib.Path({str(survivor_path)!r}).write_text('alive')"
    )
    command = [
        sys.executable,
        "-c",
        (
            "import pathlib, subprocess, sys; "
            f"child = subprocess.Popen([sys.executable, '-c', {descendant!r}]); "
            f"pathlib.Path({str(pid_path)!r}).write_text(str(child.pid))"
        ),
    ]
    started = time.monotonic()
    _, _, _, _, incomplete = module._run_bounded(command, timeout_seconds=0.15)

    assert incomplete
    assert time.monotonic() - started < 0.5
    assert int(pid_path.read_text()) > 0
    time.sleep(0.45)
    assert not survivor_path.exists()
