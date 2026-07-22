#!/usr/bin/env bash
# Fail-closed freshness guard for a Git-backed local Mission skill source.

set -euo pipefail

fail() {
  echo "error: $*" >&2
  exit 1
}

mission_root="${MISSION_PLUGIN_ROOT:-}"
remote="origin"
branch="main"

[ -n "$mission_root" ] || fail "MISSION_PLUGIN_ROOT is not set"
git -C "$mission_root" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || fail "MISSION_PLUGIN_ROOT is not a Git worktree: $mission_root"

current_branch="$(git -C "$mission_root" symbolic-ref --quiet --short HEAD 2>/dev/null)" \
  || fail "local Mission source must not use detached HEAD"
[ "$current_branch" = "$branch" ] \
  || fail "local Mission source must be on $branch, found $current_branch"

status="$(git -C "$mission_root" status --porcelain --untracked-files=all)"
[ -z "$status" ] \
  || fail "local Mission source must be clean before syncing $remote/$branch"

git -C "$mission_root" fetch "$remote" \
  "refs/heads/$branch:refs/remotes/$remote/$branch"

local_sha="$(git -C "$mission_root" rev-parse HEAD)"
remote_sha="$(git -C "$mission_root" rev-parse "refs/remotes/$remote/$branch")"

if [ "$local_sha" != "$remote_sha" ]; then
  git -C "$mission_root" merge-base --is-ancestor "$local_sha" "$remote_sha" \
    || fail "local Mission source cannot fast-forward to $remote/$branch"
  git -C "$mission_root" merge --ff-only "$remote_sha"
  local_sha="$(git -C "$mission_root" rev-parse HEAD)"
fi

[ "$local_sha" = "$remote_sha" ] \
  || fail "local Mission source did not reach latest $remote/$branch"

status="$(git -C "$mission_root" status --porcelain --untracked-files=all)"
[ -z "$status" ] \
  || fail "local Mission source became dirty while syncing $remote/$branch"

current_branch="$(git -C "$mission_root" symbolic-ref --quiet --short HEAD 2>/dev/null)" \
  || fail "local Mission source became detached while syncing $remote/$branch"
[ "$current_branch" = "$branch" ] \
  || fail "local Mission source changed branch while syncing $remote/$branch"

local_sha="$(git -C "$mission_root" rev-parse HEAD)"
remote_sha="$(git -C "$mission_root" rev-parse "refs/remotes/$remote/$branch")"
[ "$local_sha" = "$remote_sha" ] \
  || fail "local Mission source changed after syncing $remote/$branch"

printf 'status=ready branch=%s sha=%s\n' "$branch" "$local_sha"
