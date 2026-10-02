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

def concurrency_files(task_id: str, broken: bool, requirement: str) -> dict[str, str]:
    if task_id == "concurrency-lost-update":
        store = '''from threading import Lock


class Counter:
    def __init__(self, initial):
        self._value = initial
        self._lock = Lock()

    def update(self, delta, ready):
        ready.wait()
        with self._lock:
            self._value += delta

    def snapshot(self):
        with self._lock:
            return self._value
'''
        if broken:
            store = '''class Counter:
    def __init__(self, initial):
        self._value = initial

    def update(self, delta, ready):
        observed = self._value
        ready.wait()
        self._value = observed + delta

    def snapshot(self):
        return self._value
'''
        return {
            "boundary.py": '''def normalise(request):
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    deltas = request.get("deltas")
    if not isinstance(deltas, list) or len(deltas) != 2:
        raise ValueError("two deltas are required")
    return int(request.get("initial", 0)), tuple(int(delta) for delta in deltas)
''',
            "store.py": store,
            "service.py": '''from threading import Barrier, Thread

from boundary import normalise
from store import Counter


def execute(request):
    initial, deltas = normalise(request)
    counter = Counter(initial)
    ready = Barrier(2)
    threads = [Thread(target=counter.update, args=(delta, ready)) for delta in deltas]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)
    return {"final_state": counter.snapshot(), "threads_completed": sum(not thread.is_alive() for thread in threads)}
''',
            "public_smoke.py": '''from service import execute

assert execute({"initial": 0, "deltas": [0, 0]})["threads_completed"] == 2
''',
            "README.md": f"# {task_id}\n\nContract: {requirement}\n\nThe service receives an initial count and two deltas. It must return the final count and report that both worker threads completed.\n",
        }
    store = '''from threading import Lock


class WinnerStore:
    def __init__(self):
        self._winner = None
        self._lock = Lock()

    def register(self, candidate):
        with self._lock:
            if self._winner is None or (candidate["rank"], candidate["id"]) < (self._winner["rank"], self._winner["id"]):
                self._winner = candidate

    def snapshot(self):
        with self._lock:
            return dict(self._winner) if self._winner is not None else None
'''
    if broken:
        store = '''class WinnerStore:
    def __init__(self):
        self._winner = None

    def register(self, candidate):
        self._winner = candidate

    def snapshot(self):
        return dict(self._winner) if self._winner is not None else None
'''
    return {
        "boundary.py": '''def normalise(request):
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    candidates = request.get("candidates")
    arrival_order = request.get("arrival_order")
    if not isinstance(candidates, list) or len(candidates) != 2 or not isinstance(arrival_order, list):
        raise ValueError("two candidates and an arrival order are required")
    parsed = [{"id": str(candidate["id"]), "rank": int(candidate["rank"])} for candidate in candidates]
    by_id = {candidate["id"]: candidate for candidate in parsed}
    if len(by_id) != 2 or set(arrival_order) != set(by_id):
        raise ValueError("arrival order must name each candidate once")
    return tuple(by_id[candidate_id] for candidate_id in arrival_order)
''',
        "store.py": store,
        "service.py": '''from threading import Barrier, Event, Thread

from boundary import normalise
from store import WinnerStore


def execute(request):
    candidates = normalise(request)
    winner = WinnerStore()
    ready = Barrier(2)
    first_arrived = Event()

    def register(candidate, position):
        ready.wait()
        if position:
            first_arrived.wait()
        winner.register(candidate)
        if not position:
            first_arrived.set()

    threads = [Thread(target=register, args=(candidate, position)) for position, candidate in enumerate(candidates)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)
    return {"winner": winner.snapshot(), "threads_completed": sum(not thread.is_alive() for thread in threads)}
''',
        "public_smoke.py": '''from service import execute

assert execute({"candidates": [{"id": "x", "rank": 1}, {"id": "y", "rank": 2}], "arrival_order": ["x", "y"]})["threads_completed"] == 2
''',
        "README.md": f"# {task_id}\n\nContract: {requirement}\n\nThe service registers two ranked candidates in the supplied arrival order. It must return the canonical winner (lowest rank, then identifier) and report that both worker threads completed.\n",
    }


def files(mode: str, broken: bool, task_id: str, requirement: str, failure: str) -> dict[str, str]:
    if mode == "lost-update" or mode == "ordering":
        return concurrency_files(task_id, broken, requirement)
    return {
        "boundary.py": "def normalise(operation):\n    return dict(operation)\n",
        "store.py": "class Store:\n    def __init__(self): self.items = {}; self.sent = []; self.total = 0\n    def snapshot(self): return {'items': self.items, 'sent': self.sent, 'total': self.total}\n",
        "service.py": f'''from boundary import normalise
from store import Store
MODE = {mode!r}; BROKEN = {broken!r}

def execute(operations):
    store = Store(); seen = set(); watermark = 0
    for raw in operations:
        op = normalise(raw); kind = op['kind']
        if MODE == 'cache':
            if kind == 'write' and (BROKEN and op['key'] in store.items): pass
            elif kind == 'write': store.items[op['key']] = op['value']
        elif MODE == 'units':
            if kind == 'amount': store.items['amount'] = op['cents'] if BROKEN else op['cents'] / 100
        elif MODE == 'legacy':
            if kind == 'payload': store.items['state'] = op.get('state', 'unknown' if BROKEN else 'open')
        elif MODE == 'rename':
            if kind == 'payload': store.items['priority'] = op.get('priority', 'normal' if BROKEN else op.get('urgency', 'normal'))
        elif MODE == 'rollback':
            if kind == 'put': store.items[op['key']] = op['value']
            if kind == 'fail' and not BROKEN: store.items.clear()
        elif MODE == 'retry':
            if kind == 'send' and (BROKEN or op['id'] not in seen): store.sent.append(op['id']); seen.add(op['id'])
        elif MODE == 'idempotent':
            if kind == 'charge' and (BROKEN or op['id'] not in seen): store.total += op['amount']; seen.add(op['id'])
        elif MODE == 'resume':
            if kind == 'event' and (op['offset'] >= watermark if BROKEN else op['offset'] > watermark): store.sent.append(op['id']); watermark = max(watermark, op['offset'])
        elif MODE == 'cancel':
            if kind == 'add': store.total += op['amount']
            if kind == 'cancel' and not BROKEN: store.total -= op['amount']
        elif MODE == 'dedupe':
            if kind == 'add' and (BROKEN or op['id'] not in seen): store.total += op['amount']; seen.add(op['id'])
        elif MODE == 'lost-update':
            if kind == 'increment': store.total = 1 if BROKEN else store.total + op['amount']
        elif MODE == 'ordering':
            if kind == 'candidate': store.items['winner'] = op['name'] if BROKEN else min(store.items.get('winner', op['name']), op['name'])
    return store.snapshot()
''',
        "public_smoke.py": "from service import execute\nassert isinstance(execute([]), dict)\n",
        "README.md": f"# {task_id}\n\nRequirement: {requirement}\n\nThe starter has a defect: {failure}\nRepair the observable contract without weakening the public smoke check.\n",
    }

def write_tree(root: Path, data: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, text in data.items():
        (root / name).write_text(text, encoding="utf-8")

def main() -> None:
    catalog = []
    cases = {
      'cache': [{'name':'replace-cache','operations':[{'kind':'write','key':'x','value':1},{'kind':'write','key':'x','value':2}],'expected':{'items':{'x':2},'sent':[],'total':0}}],
      'units': [{'name':'cents-boundary','operations':[{'kind':'amount','cents':1250}],'expected':{'items':{'amount':12.5},'sent':[],'total':0}}],
      'legacy': [{'name':'legacy-default','operations':[{'kind':'payload'}],'expected':{'items':{'state':'open'},'sent':[],'total':0}}],
      'rename': [{'name':'renamed-field','operations':[{'kind':'payload','urgency':'high'}],'expected':{'items':{'priority':'high'},'sent':[],'total':0}}],
      'rollback': [{'name':'atomic-failure','operations':[{'kind':'put','key':'a','value':1},{'kind':'fail'}],'expected':{'items':{},'sent':[],'total':0}}],
      'retry': [{'name':'selective-retry','operations':[{'kind':'send','id':'a'},{'kind':'send','id':'a'},{'kind':'send','id':'b'}],'expected':{'items':{},'sent':['a','b'],'total':0}}],
      'idempotent': [{'name':'same-event','operations':[{'kind':'charge','id':'x','amount':3},{'kind':'charge','id':'x','amount':3}],'expected':{'items':{},'sent':[],'total':3}}],
      'resume': [{'name':'watermark','operations':[{'kind':'event','id':'a','offset':1},{'kind':'event','id':'b','offset':1},{'kind':'event','id':'c','offset':2}],'expected':{'items':{},'sent':['a','c'],'total':0}}],
      'cancel': [{'name':'cancellation','operations':[{'kind':'add','amount':3},{'kind':'cancel','amount':3}],'expected':{'items':{},'sent':[],'total':0}}],
      'dedupe': [{'name':'duplicate','operations':[{'kind':'add','id':'x','amount':2},{'kind':'add','id':'x','amount':2}],'expected':{'items':{},'sent':[],'total':2}}],
      'lost-update': [
          {'name': 'zero-plus-one-plus-one', 'scenario': {'initial': 0, 'deltas': [1, 1]}, 'expected': {'final_state': 2, 'threads_completed': 2}},
          {'name': 'seven-plus-two-plus-five', 'scenario': {'initial': 7, 'deltas': [2, 5]}, 'expected': {'final_state': 14, 'threads_completed': 2}},
          {'name': 'negative-three-plus-four-minus-two', 'scenario': {'initial': -3, 'deltas': [4, -2]}, 'expected': {'final_state': -1, 'threads_completed': 2}},
      ],
      'ordering': [
          {'name': 'z-over-a-by-rank', 'scenario': {'candidates': [{'id': 'z', 'rank': 1}, {'id': 'a', 'rank': 2}], 'arrival_order': ['a', 'z']}, 'expected': {'winner': {'id': 'z', 'rank': 1}, 'threads_completed': 2}},
          {'name': 'b-over-c-by-rank', 'scenario': {'candidates': [{'id': 'b', 'rank': 1}, {'id': 'c', 'rank': 2}], 'arrival_order': ['c', 'b']}, 'expected': {'winner': {'id': 'b', 'rank': 1}, 'threads_completed': 2}},
          {'name': 'same-rank-id-tie-break-in-reverse-arrival-order', 'scenario': {'candidates': [{'id': 'a', 'rank': 4}, {'id': 'z', 'rank': 4}], 'arrival_order': ['a', 'z']}, 'expected': {'winner': {'id': 'a', 'rank': 4}, 'threads_completed': 2}},
      ], }
    for task_id, family, requirement, failure, broken, repaired, expected in TASKS:
        mode = {'multi-module-cache':'cache','multi-module-unit-boundary':'units','compatibility-legacy-default':'legacy','compatibility-versioned-field':'rename','partial-failure-rollback':'rollback','partial-failure-selective-retry':'retry','rerun-idempotency-key':'idempotent','rerun-resume-watermark':'resume','aggregation-cancellation':'cancel','aggregation-deduplication':'dedupe','concurrency-lost-update':'lost-update','concurrency-order-independent':'ordering'}[task_id]
        for group, is_broken in (("worker", True), ("reference", False), ("control", False)):
            write_tree(ROOT / group / task_id, files(mode, is_broken, task_id, requirement, failure))
        catalog.append({"id": task_id, "family": family, "version": "complex-fixture-v1", "requirement": requirement,
                        "dependency": DEPENDENCIES[family],
                        "realistic_failure": failure, "checks": cases[mode]})
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
