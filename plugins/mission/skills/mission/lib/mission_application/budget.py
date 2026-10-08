"""Read-only F1 queries; no policy intake, admission or mutation use case."""
from __future__ import annotations

import json
from pathlib import Path

from mission_kernel.budget import BudgetError, budget_status, decode_ledger


def run_budget_status_cli(args, services):
    root = Path.cwd()
    state_file = services.resolve_state_file(root)
    if not state_file.exists():
        services.fail('budget-state-missing', 2)
    try:
        # A query must not enter the write path: repository.load() would run lease
        # admission and pending-transaction recovery.  Read the authoritative snapshot.
        _, document = services.load_snapshot(state_file)
        ledger = decode_ledger(document)
        capacity = services.capacity_status(state_file)
        return json.dumps(budget_status(ledger, services.now(), capacity), ensure_ascii=False)
    except BudgetError as exc:
        services.fail(exc.code, 2)


def run_budget_next(out, data, at, legacy_pressure, spawn_actions, capacity_status=None):
    """Keep legacy advice intact. F2c owns any new action substitutions."""
    ledger = decode_ledger(data)
    if ledger.policy is not None:
        out['budget'] = budget_status(ledger, at, capacity_status() if capacity_status else None)
        consumed = out['budget']['consumed_sec']
        pct = round(consumed / ledger.policy.total_sec * 100, 1)
        out['budget_pressure'] = dict(budget_minutes=ledger.policy.total_sec / 60,
            elapsed_minutes=round(consumed / 60, 1), pressure_pct=pct,
            level='exceeded' if pct >= 100 else 'warn' if pct >= 80 else 'ok', basis='active-clock')
        return out
    pressure = legacy_pressure(data, at)
    out['budget_pressure'] = pressure
    if pressure and pressure['level'] == 'exceeded' and out.get('next_action') in spawn_actions:
        out['budget_overridden_action'] = out['next_action']
        out['next_action'] = 'consider-halt'
        out['summary'] = (
            f"時間予算 {pressure['budget_minutes']} 分を超過 ({pressure['elapsed_minutes']} 分経過)。"
            ' 新規 spawn を止め、現時点の成果物を確定して partial-done で終了する。')
        out['command_hint'] = 'mission-state.py mark-halt --reason "時間予算超過: 完了分と未完了作業を明記" --category partial-done'
    elif pressure and pressure['level'] == 'warn':
        out['budget_warning'] = (
            f"時間予算の {pressure['pressure_pct']}% を消費。optional specialist / critic の"
            ' 新規 spawn を控え、成果物の確定を優先する。')
    return out
