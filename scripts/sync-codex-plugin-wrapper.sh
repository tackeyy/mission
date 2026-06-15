#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRAPPER="$ROOT/plugins/mission"

mkdir -p "$WRAPPER/skills" "$WRAPPER/scripts"

rsync -a --delete --delete-excluded \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude 'mission/pytest.ini' \
  --exclude 'mission/tests/' \
  "$ROOT/skills/" "$WRAPPER/skills/"

rsync -a --delete --delete-excluded \
  --exclude 'sync-codex-plugin-wrapper.sh' \
  "$ROOT/scripts/" "$WRAPPER/scripts/"

echo "Synced Codex plugin wrapper: $WRAPPER"
