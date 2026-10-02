#!/usr/bin/env python3
"""Generate neutral #883 worker, repair, and control repositories deterministically."""
from __future__ import annotations

import json
import hashlib
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
    ("multi-module-cache", "multi_module", "A cache invalidation crosses parser and service modules.", "A write returns a stale cached value after an update."),
    ("multi-module-unit-boundary", "multi_module", "A boundary conversion preserves currency units across modules.", "A cents value is exposed as whole currency."),
    ("compatibility-legacy-default", "compatibility", "Legacy and current payloads retain their documented default.", "A legacy payload loses its compatibility default."),
    ("compatibility-versioned-field", "compatibility", "A versioned boundary maps an old field to its current meaning.", "A renamed field is silently ignored by the current consumer."),
    ("partial-failure-rollback", "partial_failure", "A failed batch does not leave partial persistent state.", "A partial write remains visible after a later operation fails."),
    ("partial-failure-selective-retry", "partial_failure", "A retry executes only operations that were not accepted.", "A retry repeats an already accepted external operation."),
    ("rerun-idempotency-key", "rerun", "Replaying the same event preserves one logical effect.", "A repeated event is charged twice."),
    ("rerun-resume-watermark", "rerun", "Resume starts after the persisted watermark.", "Resume processes the previously committed item again."),
    ("aggregation-cancellation", "aggregation", "Cancellation is reflected in the aggregate state.", "A cancellation leaves an obsolete positive total."),
    ("aggregation-deduplication", "aggregation", "Duplicate delivery does not inflate an aggregate.", "A duplicate event is counted twice."),
    ("concurrency-lost-update", "concurrency", "A deterministic barrier preserves both concurrent increments.", "Two writers read the same value and one increment is lost."),
    ("concurrency-order-independent", "concurrency", "Concurrent order does not change the canonical result.", "Arrival order chooses a non-canonical winner."),
]

TASK_MODES = {
    "multi-module-cache": "cache", "multi-module-unit-boundary": "units",
    "compatibility-legacy-default": "legacy", "compatibility-versioned-field": "rename",
    "partial-failure-rollback": "rollback", "partial-failure-selective-retry": "retry",
    "rerun-idempotency-key": "idempotent", "rerun-resume-watermark": "resume",
    "aggregation-cancellation": "cancel", "aggregation-deduplication": "dedupe",
    "concurrency-lost-update": "lost-update", "concurrency-order-independent": "ordering",
}

TASK_CONTRACTS = {
    "multi-module-cache": "Input: an initial key/value mapping and ordered read/write operations. Output: observed reads and final storage state. A write must invalidate that key's cached value without affecting other keys.",
    "multi-module-unit-boundary": "Input: an amount labelled major or minor plus a minor-unit fee. Output: the total in minor units and its major-unit display. Conversion occurs once at the boundary.",
    "compatibility-legacy-default": "Input: a versioned payload with an optional state. Output: persisted state and version. Version 1 defaults to open; later versions default to pending; an explicit state wins.",
    "compatibility-versioned-field": "Input: a payload with priority and/or legacy urgency. Output: persisted priority. Priority wins when present; otherwise urgency supplies the legacy value; otherwise normal applies.",
    "partial-failure-rollback": "Input: existing records, ordered writes, and an optional fault position. Output: commit status and stored state. A fault leaves the complete pre-batch state intact.",
    "partial-failure-selective-retry": "Input: accepted identifiers, delivery rounds, and identifiers that fail once. Output: attempts and accepted identifiers. Accepted work is never resent; failed work alone is retried.",
    "rerun-idempotency-key": "Input: persisted effects and event calls, optionally reloading between calls. Output: effects by event id and total. Repeating an id preserves one logical effect across reloads.",
    "rerun-resume-watermark": "Input: persisted watermark/processed ids and event runs, optionally reloading between runs. Output: watermark and processed ids. Offsets at or below the watermark are not processed again.",
    "aggregation-cancellation": "Input: add and cancel events. Output: retained entries and their total. Cancelling one id removes only that id; an unknown cancellation has no effect.",
    "aggregation-deduplication": "Input: delivery events with ids and amounts. Output: entries and total. Repeated delivery of an id must not inflate its aggregate.",
    "concurrency-lost-update": "Input: an initial count and exactly two integer deltas. Output: final count and completed-thread count. Both concurrent updates must be reflected after the barrier.",
    "concurrency-order-independent": "Input: two ranked candidates and their arrival order. Output: canonical winner and completed-thread count. The lowest rank, then identifier, wins regardless of arrival order.",
}

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
            "README.md": f"# {task_id}\n\nRequirement: {requirement}\n\nContract: {TASK_CONTRACTS[task_id]}\n",
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
        "README.md": f"# {task_id}\n\nRequirement: {requirement}\n\nContract: {TASK_CONTRACTS[task_id]}\n",
    }


def stateful_files(task_id: str, broken: bool, requirement: str) -> dict[str, str]:
    """Render task-specific worker code; only the evaluator owns case data."""
    common = {
        "README.md": (
            f"# {task_id}\n\nRequirement: {requirement}\n\n"
            f"Contract: {TASK_CONTRACTS[task_id]}\n"
        ),
    }
    if task_id == "multi-module-cache":
        common.update({
            "boundary.py": "def normalise(request):\n    return dict(request)\n",
            "storage.py": "class Storage:\n    def __init__(self, records): self.records = dict(records)\n    def read(self, key): return self.records.get(key)\n    def write(self, key, value): self.records[key] = value\n    def snapshot(self): return dict(self.records)\n",
            "cache.py": "class Cache:\n    def __init__(self): self.values = {}\n    def read(self, storage, key):\n        if key not in self.values: self.values[key] = storage.read(key)\n        return self.values[key]\n" + ("" if broken else "    def invalidate(self, key): self.values.pop(key, None)\n"),
            "service.py": "from boundary import normalise\nfrom storage import Storage\nfrom cache import Cache\n\ndef execute(request):\n    request = normalise(request); storage = Storage(request.get('initial', {})); cache = Cache(); values = []\n    for operation in request.get('operations', []):\n        if operation['kind'] == 'read': values.append(cache.read(storage, operation['key']))\n        else:\n            storage.write(operation['key'], operation['value'])\n" + ("            # retained cached value is observable through the next read\n" if broken else "            cache.invalidate(operation['key'])\n") + "    return {'values': values, 'state': storage.snapshot()}\n",
            "public_smoke.py": "from service import execute\nassert isinstance(execute({'initial': {}, 'operations': []}), dict)\n",
        })
    elif task_id == "multi-module-unit-boundary":
        common.update({
            "boundary.py": "def to_minor(payload):\n    amount = payload['amount']\n    return int(round(amount * 100)) if payload['unit'] == 'major' else int(amount)\n" if not broken else "def to_minor(payload):\n    return int(payload['amount'])\n",
            "consumer.py": "def present(minor, fee):\n    total = minor + fee\n    return {'minor_total': total, 'display_major': total / 100}\n",
            "store.py": "class Ledger:\n    def __init__(self): self.minor = 0\n    def post(self, value): self.minor += value\n",
            "service.py": "from boundary import to_minor\nfrom consumer import present\nfrom store import Ledger\n\ndef execute(request):\n    ledger = Ledger(); ledger.post(to_minor(request['payload']))\n    return present(ledger.minor, int(request.get('fee_minor', 0)))\n",
            "public_smoke.py": "from service import execute\nassert execute({'payload': {'amount': 1, 'unit': 'minor'}})['minor_total'] == 1\n",
        })
    elif task_id == "compatibility-legacy-default":
        common.update({
            "boundary.py": "def normalise(payload):\n    value = dict(payload); version = value.get('version', 1)\n    default = 'open' if version == 1 else 'pending'\n    return {'state': value.get('state', default), 'version': version}\n" if not broken else "def normalise(payload):\n    value = dict(payload); return {'state': value.get('state', 'unknown'), 'version': value.get('version', 1)}\n",
            "store.py": "class StateStore:\n    def __init__(self): self.value = None\n    def save(self, value): self.value = dict(value)\n    def snapshot(self): return dict(self.value)\n",
            "service.py": "from boundary import normalise\nfrom store import StateStore\n\ndef execute(request):\n    store = StateStore(); store.save(normalise(request)); return store.snapshot()\n",
            "public_smoke.py": "from service import execute\nassert isinstance(execute({}), dict)\n",
        })
    elif task_id == "compatibility-versioned-field":
        common.update({
            "boundary.py": "def normalise(payload):\n    value = dict(payload)\n    return {'priority': value.get('priority', value.get('urgency', 'normal'))}\n" if not broken else "def normalise(payload):\n    value = dict(payload); return {'priority': value.get('priority', 'normal')}\n",
            "store.py": "class PriorityStore:\n    def __init__(self): self.priority = None\n    def save(self, value): self.priority = value\n",
            "service.py": "from boundary import normalise\nfrom store import PriorityStore\n\ndef execute(request):\n    store = PriorityStore(); store.save(normalise(request)['priority']); return {'priority': store.priority}\n",
            "public_smoke.py": "from service import execute\nassert isinstance(execute({}), dict)\n",
        })
    elif task_id == "partial-failure-rollback":
        common.update({
            "boundary.py": "def normalise(request): return {'existing': dict(request.get('existing', {})), 'writes': list(request.get('writes', [])), 'fail_after': request.get('fail_after')}\n",
            "store.py": "class TransactionStore:\n    def __init__(self, records): self.records = dict(records)\n    def apply(self, writes, fail_after):\n        working = dict(self.records)\n        try:\n            for index, write in enumerate(writes):\n                working[write['key']] = write['value']\n                if index == fail_after: raise RuntimeError('injected')\n        except RuntimeError:\n            return False\n        self.records = working; return True\n    def snapshot(self): return dict(self.records)\n" if not broken else "class TransactionStore:\n    def __init__(self, records): self.records = dict(records)\n    def apply(self, writes, fail_after):\n        try:\n            for index, write in enumerate(writes):\n                self.records[write['key']] = write['value']\n                if index == fail_after: raise RuntimeError('injected')\n        except RuntimeError:\n            self.records.clear(); return False\n        return True\n    def snapshot(self): return dict(self.records)\n",
            "service.py": "from boundary import normalise\nfrom store import TransactionStore\n\ndef execute(request):\n    request = normalise(request); store = TransactionStore(request['existing']); committed = store.apply(request['writes'], request['fail_after']); return {'committed': committed, 'state': store.snapshot()}\n",
            "public_smoke.py": "from service import execute\nassert isinstance(execute({}), dict)\n",
        })
    elif task_id == "partial-failure-selective-retry":
        common.update({
            "boundary.py": "def normalise(request): return {'accepted': list(request.get('accepted', [])), 'rounds': list(request.get('rounds', [])), 'fail_once': set(request.get('fail_once', []))}\n",
            "store.py": "class DeliveryStore:\n    def __init__(self, accepted, fail_once): self.accepted = set(accepted); self.fail_once = set(fail_once); self.attempts = []\n    def send(self, identifier):\n        if identifier in self.accepted: return\n        self.attempts.append(identifier)\n        if identifier in self.fail_once: self.fail_once.remove(identifier); return\n        self.accepted.add(identifier)\n" if not broken else "class DeliveryStore:\n    def __init__(self, accepted, fail_once): self.accepted = set(accepted); self.fail_once = set(fail_once); self.attempts = []\n    def send(self, identifier):\n        self.attempts.append(identifier)\n        if identifier not in self.fail_once: self.accepted.add(identifier)\n        self.fail_once.discard(identifier)\n",
            "service.py": "from boundary import normalise\nfrom store import DeliveryStore\n\ndef execute(request):\n    request = normalise(request); store = DeliveryStore(request['accepted'], request['fail_once'])\n    for round_ids in request['rounds']:\n        for identifier in round_ids: store.send(identifier)\n    return {'attempts': store.attempts, 'accepted': sorted(store.accepted)}\n",
            "public_smoke.py": "from service import execute\nassert isinstance(execute({}), dict)\n",
        })
    elif task_id == "rerun-idempotency-key":
        common.update({
            "boundary.py": "def normalise(request): return {'state': dict(request.get('state', {})), 'calls': list(request.get('calls', []))}\n",
            "store.py": "class EffectStore:\n    def __init__(self, state): self.effects = dict(state)\n    def apply(self, event):\n        self.effects.setdefault(event['id'], event['amount'])\n    def snapshot(self): return dict(self.effects)\n" if not broken else "class EffectStore:\n    def __init__(self, state): self.effects = dict(state)\n    def apply(self, event): self.effects[event['id']] = self.effects.get(event['id'], 0) + event['amount']\n    def snapshot(self): return dict(self.effects)\n",
            "service.py": "from boundary import normalise\nfrom store import EffectStore\n\ndef execute(request):\n    request = normalise(request); store = EffectStore(request['state'])\n    for call in request['calls']:\n        store.apply(call['event'])\n        if call.get('reload'): store = EffectStore(store.snapshot())\n    return {'effects': store.snapshot(), 'total': sum(store.snapshot().values())}\n",
            "public_smoke.py": "from service import execute\nassert isinstance(execute({}), dict)\n",
        })
    elif task_id == "rerun-resume-watermark":
        common.update({
            "boundary.py": "def normalise(request): return {'state': dict(request.get('state', {})), 'runs': list(request.get('runs', []))}\n",
            "store.py": "class WatermarkStore:\n    def __init__(self, state): self.watermark = state.get('watermark', -1); self.processed = list(state.get('processed', []))\n    def apply(self, event):\n        if event['offset'] > self.watermark: self.processed.append(event['id']); self.watermark = event['offset']\n    def snapshot(self): return {'watermark': self.watermark, 'processed': list(self.processed)}\n" if not broken else "class WatermarkStore:\n    def __init__(self, state): self.watermark = state.get('watermark', -1); self.processed = list(state.get('processed', []))\n    def apply(self, event):\n        if event['offset'] >= self.watermark: self.processed.append(event['id']); self.watermark = event['offset']\n    def snapshot(self): return {'watermark': self.watermark, 'processed': list(self.processed)}\n",
            "service.py": "from boundary import normalise\nfrom store import WatermarkStore\n\ndef execute(request):\n    request = normalise(request); store = WatermarkStore(request['state'])\n    for run in request['runs']:\n        for event in run.get('events', []): store.apply(event)\n        if run.get('reload'): store = WatermarkStore(store.snapshot())\n    return store.snapshot()\n",
            "public_smoke.py": "from service import execute\nassert isinstance(execute({}), dict)\n",
        })
    elif task_id == "aggregation-cancellation":
        common.update({
            "boundary.py": "def normalise(request): return list(request.get('events', []))\n",
            "store.py": "class Aggregate:\n    def __init__(self): self.entries = {}\n    def apply(self, event):\n        if event['kind'] == 'add': self.entries[event['id']] = event['amount']\n        elif event['kind'] == 'cancel': self.entries.pop(event['id'], None)\n    def snapshot(self): return {'entries': dict(self.entries), 'total': sum(self.entries.values())}\n" if not broken else "class Aggregate:\n    def __init__(self): self.entries = {}\n    def apply(self, event):\n        if event['kind'] == 'add': self.entries[event['id']] = event['amount']\n        elif event['kind'] == 'cancel': self.entries.clear()\n    def snapshot(self): return {'entries': dict(self.entries), 'total': sum(self.entries.values())}\n",
            "service.py": "from boundary import normalise\nfrom store import Aggregate\n\ndef execute(request):\n    store = Aggregate()\n    for event in normalise(request): store.apply(event)\n    return store.snapshot()\n",
            "public_smoke.py": "from service import execute\nassert isinstance(execute({}), dict)\n",
        })
    elif task_id == "aggregation-deduplication":
        common.update({
            "boundary.py": "def normalise(request): return list(request.get('events', []))\n",
            "store.py": "class Aggregate:\n    def __init__(self): self.entries = {}\n    def apply(self, event): self.entries.setdefault(event['id'], event['amount'])\n    def snapshot(self): return {'entries': dict(self.entries), 'total': sum(self.entries.values())}\n" if not broken else "class Aggregate:\n    def __init__(self): self.entries = {}; self.total = 0\n    def apply(self, event): self.entries[event['id']] = event['amount']; self.total += event['amount']\n    def snapshot(self): return {'entries': dict(self.entries), 'total': self.total}\n",
            "service.py": "from boundary import normalise\nfrom store import Aggregate\n\ndef execute(request):\n    store = Aggregate()\n    for event in normalise(request): store.apply(event)\n    return store.snapshot()\n",
            "public_smoke.py": "from service import execute\nassert isinstance(execute({}), dict)\n",
        })
    else:
        raise ValueError(f"unknown stateful task: {task_id}")
    return common


def files(task_id: str, broken: bool, requirement: str) -> dict[str, str]:
    """Render only task-specific implementations; no mode switch reaches workers."""
    if TASK_MODES[task_id] in {"lost-update", "ordering"}:
        return concurrency_files(task_id, broken, requirement)
    return stateful_files(task_id, broken, requirement)

def write_tree(root: Path, data: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for path in root.iterdir():
        if path.is_file() and path.name not in data:
            path.unlink()
    for name, text in data.items():
        (root / name).write_text(text, encoding="utf-8")


def task_template(task_id: str, group: str) -> dict[str, str]:
    """Return one reproducible fixture tree without writing it to the source tree."""
    for known_id, _, requirement, _ in TASKS:
        if known_id == task_id:
            if group not in {"worker", "reference", "control"}:
                raise ValueError("unknown fixture group")
            return files(task_id, group == "worker", requirement)
    raise ValueError("unknown fixture task")


def template_digest(task_id: str, group: str) -> str:
    digest = hashlib.sha256()
    for name, content in sorted(task_template(task_id, group).items()):
        digest.update(name.encode("utf-8")); digest.update(b"\0"); digest.update(content.encode("utf-8")); digest.update(b"\0")
    return "sha256:" + digest.hexdigest()

def render_catalog() -> dict[str, object]:
    """Return evaluator-owned cases from this generator's single source of truth."""
    catalog = []
    cases = {
      'cache': [
          {'name':'read-write-read', 'scenario': {'initial': {'x': 1}, 'operations': [{'kind':'read','key':'x'}, {'kind':'write','key':'x','value':2}, {'kind':'read','key':'x'}]}, 'expected': {'values':[1,2], 'state':{'x':2}}},
          {'name':'independent-key', 'scenario': {'initial': {'x': 1, 'y': 4}, 'operations': [{'kind':'read','key':'y'}, {'kind':'write','key':'x','value':7}, {'kind':'read','key':'y'}, {'kind':'read','key':'x'}]}, 'expected': {'values':[4,4,7], 'state':{'x':7,'y':4}}},
          {'name':'two-updates', 'scenario': {'initial': {'x': 3}, 'operations': [{'kind':'read','key':'x'}, {'kind':'write','key':'x','value':5}, {'kind':'read','key':'x'}, {'kind':'write','key':'x','value':9}, {'kind':'read','key':'x'}]}, 'expected': {'values':[3,5,9], 'state':{'x':9}}},
      ],
      'units': [
          {'name':'major-with-fee', 'scenario': {'payload': {'amount': 12.5, 'unit':'major'}, 'fee_minor':25}, 'expected': {'minor_total':1275,'display_major':12.75}},
          {'name':'minor-with-fee', 'scenario': {'payload': {'amount': 1250, 'unit':'minor'}, 'fee_minor':50}, 'expected': {'minor_total':1300,'display_major':13.0}},
          {'name':'fractional-major', 'scenario': {'payload': {'amount': 0.75, 'unit':'major'}, 'fee_minor':5}, 'expected': {'minor_total':80,'display_major':0.8}},
      ],
      'legacy': [
          {'name':'legacy-default', 'scenario': {'version':1}, 'expected': {'state':'open','version':1}},
          {'name':'current-default', 'scenario': {'version':2}, 'expected': {'state':'pending','version':2}},
          {'name':'explicit-state', 'scenario': {'version':1,'state':'closed'}, 'expected': {'state':'closed','version':1}},
      ],
      'rename': [
          {'name':'legacy-urgency', 'scenario': {'urgency':'high'}, 'expected': {'priority':'high'}},
          {'name':'current-priority', 'scenario': {'priority':'low'}, 'expected': {'priority':'low'}},
          {'name':'explicit-current-wins', 'scenario': {'priority':'low','urgency':'high'}, 'expected': {'priority':'low'}},
      ],
      'rollback': [
          {'name':'preserve-existing-on-first-fault', 'scenario': {'existing':{'keep':9}, 'writes':[{'key':'new','value':1}], 'fail_after':0}, 'expected': {'committed':False,'state':{'keep':9}}},
          {'name':'commit-whole-batch', 'scenario': {'existing':{'keep':9}, 'writes':[{'key':'a','value':1},{'key':'b','value':2}], 'fail_after':None}, 'expected': {'committed':True,'state':{'keep':9,'a':1,'b':2}}},
          {'name':'preserve-existing-on-late-fault', 'scenario': {'existing':{'prior':4}, 'writes':[{'key':'a','value':1},{'key':'b','value':2}], 'fail_after':1}, 'expected': {'committed':False,'state':{'prior':4}}},
      ],
      'retry': [
          {'name':'skip-accepted', 'scenario': {'accepted':['a'], 'rounds':[['a','b']], 'fail_once':[]}, 'expected': {'attempts':['b'],'accepted':['a','b']}},
          {'name':'retry-only-failed', 'scenario': {'accepted':['a'], 'rounds':[['a','b','c'],['b']], 'fail_once':['b']}, 'expected': {'attempts':['b','c','b'],'accepted':['a','b','c']}},
          {'name':'retain-two-accepted', 'scenario': {'accepted':['a','c'], 'rounds':[['a','b','c'],['b']], 'fail_once':[]}, 'expected': {'attempts':['b'],'accepted':['a','b','c']}},
      ],
      'idempotent': [
          {'name':'same-event', 'scenario': {'state':{}, 'calls':[{'event':{'id':'a','amount':3}},{'event':{'id':'a','amount':3}}]}, 'expected': {'effects':{'a':3},'total':3}},
          {'name':'reload-retains-effect', 'scenario': {'state':{}, 'calls':[{'event':{'id':'a','amount':3},'reload':True},{'event':{'id':'a','amount':3}}]}, 'expected': {'effects':{'a':3},'total':3}},
          {'name':'persisted-plus-new', 'scenario': {'state':{'a':3}, 'calls':[{'event':{'id':'a','amount':3}},{'event':{'id':'b','amount':5},'reload':True}]}, 'expected': {'effects':{'a':3,'b':5},'total':8}},
      ],
      'resume': [
          {'name':'skip-persisted-watermark', 'scenario': {'state':{'watermark':1,'processed':['a']}, 'runs':[{'events':[{'id':'b','offset':1},{'id':'c','offset':2}]}]}, 'expected': {'watermark':2,'processed':['a','c']}},
          {'name':'reload-between-runs', 'scenario': {'state':{}, 'runs':[{'events':[{'id':'a','offset':1}], 'reload':True},{'events':[{'id':'b','offset':1},{'id':'c','offset':2}]}]}, 'expected': {'watermark':2,'processed':['a','c']}},
          {'name':'continue-after-watermark', 'scenario': {'state':{'watermark':2,'processed':['a','b']}, 'runs':[{'events':[{'id':'c','offset':3},{'id':'d','offset':4}], 'reload':True}]}, 'expected': {'watermark':4,'processed':['a','b','c','d']}},
      ],
      'cancel': [
          {'name':'cancel-one-keeps-other', 'scenario': {'events':[{'kind':'add','id':'a','amount':5},{'kind':'add','id':'b','amount':3},{'kind':'cancel','id':'a'}]}, 'expected': {'entries':{'b':3},'total':3}},
          {'name':'unknown-cancel', 'scenario': {'events':[{'kind':'add','id':'a','amount':2},{'kind':'cancel','id':'missing'}]}, 'expected': {'entries':{'a':2},'total':2}},
          {'name':'order-with-three-ids', 'scenario': {'events':[{'kind':'add','id':'a','amount':1},{'kind':'add','id':'b','amount':4},{'kind':'cancel','id':'b'},{'kind':'add','id':'c','amount':2}]}, 'expected': {'entries':{'a':1,'c':2},'total':3}},
      ],
      'dedupe': [
          {'name':'duplicate-one', 'scenario': {'events':[{'id':'a','amount':2},{'id':'a','amount':2}]}, 'expected': {'entries':{'a':2},'total':2}},
          {'name':'interleaved-duplicates', 'scenario': {'events':[{'id':'a','amount':2},{'id':'b','amount':3},{'id':'a','amount':2}]}, 'expected': {'entries':{'a':2,'b':3},'total':5}},
          {'name':'duplicate-after-new', 'scenario': {'events':[{'id':'c','amount':1},{'id':'c','amount':1},{'id':'a','amount':4}]}, 'expected': {'entries':{'c':1,'a':4},'total':5}},
      ],
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
    for task_id, family, requirement, failure in TASKS:
        mode = TASK_MODES[task_id]
        catalog.append({"id": task_id, "family": family, "version": "complex-fixture-v1", "requirement": requirement,
                        "dependency": DEPENDENCIES[family],
                        "realistic_failure": failure, "checks": cases[mode]})
    return {"schema": "mission-complex-fixtures/1", "tasks": catalog}


def render_catalog_bytes() -> bytes:
    return (json.dumps(render_catalog(), indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def main() -> None:
    for task_id, _, requirement, _ in TASKS:
        for group, is_broken in (("worker", True), ("reference", False), ("control", False)):
            write_tree(ROOT / group / task_id, files(task_id, is_broken, requirement))
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
