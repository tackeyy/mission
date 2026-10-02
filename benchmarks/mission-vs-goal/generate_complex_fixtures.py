#!/usr/bin/env python3
"""Generate neutral #883 worker, repair, and control repositories deterministically."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "complex-fixtures"

DEPENDENCIES = {
    "multi_module": "boundary.py -> service.py",
    "compatibility": "boundary.py -> service.py",
    "partial_failure": "boundary.py -> service.py",
    "rerun": "boundary.py -> service.py",
    "aggregation": "boundary.py -> service.py",
    "concurrency": "boundary.py -> service.py",
}

TASKS = [
    ("multi-module-cache", "multi_module", "A cache invalidation crosses parser and service modules.", "A write returns a stale cached value after an update.", "return {'value': old}", "return {'value': cache[key]}", {"value": 2}),
    ("multi-module-unit-boundary", "multi_module", "A boundary conversion preserves currency units across modules.", "A cents value is exposed as whole currency.", "return {'amount': cents}", "return {'amount': cents / 100}", {"amount": 12.5}),
    ("compatibility-legacy-default", "compatibility", "Legacy and current payloads retain their documented default.", "A legacy payload loses its compatibility default.", "return {'state': raw.get('state', 'unknown')}", "return {'state': raw.get('state', 'open')}", {"state": "open"}),
    ("compatibility-versioned-field", "compatibility", "A versioned boundary maps an old field to its current meaning.", "A renamed field is silently ignored by the current consumer.", "return {'priority': raw.get('priority', 'normal')}", "return {'priority': raw.get('priority', raw.get('urgency', 'normal'))}", {"priority": "high"}),
    ("partial-failure-rollback", "partial_failure", "A failed batch does not leave partial persistent state.", "A partial write remains visible after a later operation fails.", "return {'stored': ['first']}", "return {'stored': []}", {"stored": []}),
    ("partial-failure-selective-retry", "partial_failure", "A retry executes only operations that were not accepted.", "A retry repeats an already accepted external operation.", "return {'sent': ['first', 'first', 'second']}", "return {'sent': ['first', 'second']}", {"sent": ["first", "second"]}),
    ("rerun-idempotency-key", "rerun", "Replaying the same event preserves one logical effect.", "A repeated event is charged twice.", "return {'charges': 2}", "return {'charges': 1}", {"charges": 1}),
    ("rerun-resume-watermark", "rerun", "Resume starts after the persisted watermark.", "Resume processes the previously committed item again.", "return {'processed': ['b', 'b', 'c']}", "return {'processed': ['b', 'c']}", {"processed": ["b", "c"]}),
    ("aggregation-cancellation", "aggregation", "Cancellation is reflected in the aggregate state.", "A cancellation leaves an obsolete positive total.", "return {'total': 3}", "return {'total': 0}", {"total": 0}),
    ("aggregation-deduplication", "aggregation", "Duplicate delivery does not inflate an aggregate.", "A duplicate event is counted twice.", "return {'total': 4}", "return {'total': 2}", {"total": 2}),
    ("concurrency-lost-update", "concurrency", "A deterministic barrier preserves both concurrent increments.", "Two writers read the same value and one increment is lost.", "return {'count': 1}", "return {'count': 2}", {"count": 2}),
    ("concurrency-order-independent", "concurrency", "Concurrent order does not change the canonical result.", "Arrival order chooses a non-canonical winner.", "return {'winner': 'late'}", "return {'winner': 'canonical'}", {"winner": "canonical"}),
]

def files(expression: str, task_id: str, requirement: str, failure: str) -> dict[str, str]:
    return {
        "boundary.py": "def input_value():\n    return {'key': 'alpha', 'cents': 1250, 'urgency': 'high'}\n",
        "service.py": "from boundary import input_value\n\ndef scenario():\n    raw = input_value()\n    key, cents, cache, old = raw['key'], raw['cents'], {'alpha': 2}, 1\n    " + expression.replace("\n", "\n    ") + "\n",
        "public_smoke.py": "from service import scenario\nassert isinstance(scenario(), dict)\n",
        "README.md": f"# {task_id}\n\nRequirement: {requirement}\n\nThe starter has a defect: {failure}\nRepair the observable contract without weakening the public smoke check.\n",
    }

def write_tree(root: Path, data: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in data.items():
        (root / name).write_text(text, encoding="utf-8")

def main() -> None:
    catalog = []
    for task_id, family, requirement, failure, broken, repaired, expected in TASKS:
        for group, expression in (("worker", broken), ("reference", repaired), ("control", repaired)):
            write_tree(ROOT / group / task_id, files(expression, task_id, requirement, failure))
        catalog.append({"id": task_id, "family": family, "version": "complex-fixture-v1", "requirement": requirement,
                        "dependency": DEPENDENCIES[family],
                        "realistic_failure": failure, "checks": [{"name": "external-contract", "expected": expected}]})
    (ROOT / "catalog.json").parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "catalog.json").write_text(json.dumps({"schema": "mission-complex-fixtures/1", "tasks": catalog}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (ROOT / "README.md").write_text("""# Complex repair fixtures

This development cohort contains twelve neutral repositories: two tasks in each
of the multi-module, compatibility, partial-failure, rerun, aggregation, and
concurrency families. Each worker snapshot contains a requirement, two source
modules, and a public smoke check. Its external evaluator, reference repair,
and good control are stored separately and are excluded by the positive
allowlist export from #882.

The evaluator records observed task/family/version/candidate digest and retains
failed, blocked, and invalid outputs. It does not accept a candidate-provided
success marker. These twelve development fixtures are not evidence of a
tenfold improvement or of a general model-quality result.
""", encoding="utf-8")

if __name__ == "__main__": main()
