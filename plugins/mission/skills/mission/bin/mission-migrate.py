#!/usr/bin/env python3
"""mission-migrate.py — single state.json → sessions/<sid>.json 構造への変換

C-3 multi-session 構造への移行ツール。
- デフォルト dry-run。--execute で実際に変換。
- 既存 state.json は state.json.pre-migration として保管。
- session_id は state.json の session_id (B-3 で追加されたフィールド) を採用。
  存在しない場合は uuid を生成。
- aggregate.json を作成 (active_sessions に session_id を登録)。
- 後方互換: 既存 state.json はそのまま残す (新規 init からは sessions/ を使う)。
  完全移行する場合は --remove-legacy で削除。
"""

import argparse
import json
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
import importlib.util as _ilu

# Tier5: 検索 root / atomic_write を mission-state.py と単一ソース化 (重複・ドリフト防止)
_gs_spec = _ilu.spec_from_file_location("mission_state_for_migrate", Path(__file__).resolve().parent / "mission-state.py")
_gs = _ilu.module_from_spec(_gs_spec)
_gs_spec.loader.exec_module(_gs)


def migrate_one(state_file: Path, execute: bool, remove_legacy: bool, force: bool = False) -> dict:
    """1 つの state.json を sessions/<sid>.json + aggregate.json に変換."""
    if not state_file.is_file():
        return {"status": "skipped", "reason": "state.json not found"}

    try:
        data = json.loads(state_file.read_text())
    except Exception as e:
        return {"status": "error", "reason": f"invalid JSON: {e}"}

    state_dir = state_file.parent
    sessions_dir = state_dir / "sessions"
    aggregate = state_dir / "aggregate.json"

    if sessions_dir.exists() and any(sessions_dir.iterdir()):
        return {"status": "skipped", "reason": "sessions/ already exists with content"}

    # 進行中 (loop_active) state の migrate は中断リスクがあるため拒否 (--force で override)
    if data.get("loop_active") and not data.get("passes") and not data.get("halt_reason") and not force:
        return {"status": "skipped", "reason": "loop_active=true の進行中 state。/mission を halt 後、または --force で実行してください"}

    sid = data.get("session_id") or str(uuid.uuid4())
    target = sessions_dir / f"{sid}.json"

    result = {
        "status": "would_migrate" if not execute else "migrated",
        "state_file": str(state_file),
        "session_id": sid,
        "target": str(target),
        "remove_legacy": remove_legacy,
    }

    if not execute:
        return result

    sessions_dir.mkdir(parents=True, exist_ok=True)
    # state.json をコピー (session_id がない場合は追加)
    data["session_id"] = sid
    data.setdefault("pid", None)  # pid 未設定 legacy を明示 null 化 (hook の owner check 用)
    data.setdefault("created_at_session", data.get("started_at") or _gs.iso_now())
    _gs.atomic_write_json(target, data)

    # aggregate.json 更新 (既存があればマージ)
    agg = {}
    if aggregate.exists():
        try:
            agg = json.loads(aggregate.read_text())
        except Exception:
            agg = {}
    agg.setdefault("active_sessions", [])
    if sid not in agg["active_sessions"]:
        agg["active_sessions"].append(sid)
    agg["updated_at"] = _gs.iso_now()
    _gs.atomic_write_json(aggregate, agg)

    # 既存 state.json は .pre-migration として保管
    backup = state_file.with_suffix(state_file.suffix + ".pre-migration")
    shutil.copy2(state_file, backup)
    result["backup"] = str(backup)

    if remove_legacy:
        state_file.unlink()
        result["legacy_removed"] = True

    return result


def main():
    parser = argparse.ArgumentParser(description="single state.json → sessions/ migration")
    parser.add_argument(
        "paths",
        nargs="*",
        help="変換対象の project root パス (省略時は MISSION_SEARCH_ROOTS、未設定なら cwd 配下を全 scan)",
    )
    parser.add_argument("--execute", action="store_true", help="実際に変換実行 (デフォルトは dry-run)")
    parser.add_argument("--remove-legacy", action="store_true", help="変換後に元の state.json を削除")
    parser.add_argument("--force", action="store_true", help="loop_active=true の進行中 state も強制 migrate (中断リスクあり)")
    args = parser.parse_args()

    targets = []
    if args.paths:
        for p in args.paths:
            sf = Path(p).resolve() / ".mission-state" / "state.json"
            if sf.is_file():
                targets.append(sf)
            else:
                print(f"WARN: {sf} not found", file=sys.stderr)
    else:
        # NOTE: _default_search_roots() は MISSION_SEARCH_ROOTS (未設定なら cwd) を返す (mission-state と単一ソース)
        for root in _gs._default_search_roots():
            if root.exists():
                targets.extend(root.rglob(".mission-state/state.json"))

    results = []
    for sf in targets:
        results.append(migrate_one(sf, args.execute, args.remove_legacy, args.force))

    print(json.dumps({"dry_run": not args.execute, "remove_legacy": args.remove_legacy, "results": results}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
