# Versioning Policy

[Japanese](VERSIONING.ja.md) | **English**

This repository separates normal PR integration from distribution releases.

## Definitions

- **Merge release**: merge a PR to `main`, wait for CI, close linked issues, and
  clean up branches. This does not change plugin versions.
- **Distribution release**: intentionally publish a new plugin version with a
  `vX.Y.Z` tag, GitHub Release, manifest version bumps, install-path updates, and
  release changelog entries.

When a user asks to "release" without explicitly asking for a version bump,
GitHub Release, marketplace release, or tag, treat it as a merge release.

## Default Rule

Do not bump versions for every merged PR.

Keep user-facing changes in `CHANGELOG.md` and `CHANGELOG.ja.md` under
`[Unreleased]`. Create a distribution release only when intentionally publishing
a batch of changes to plugin users.

The default cadence is at most weekly, unless a hotfix is required.

## Hotfix Exceptions

Create an immediate patch distribution release only when a published version has
one of these problems:

- install, startup, or basic `mission-state.py` commands are broken
- state corruption or pass/fail gate bypass
- security or privacy risk
- marketplace metadata or wrapper sync drift causes installed users to receive a
  broken package

Small documentation edits, tests, internal refactors, audit output polish, and
non-blocking UX improvements should accumulate in `[Unreleased]`.

## SemVer Mapping

- **MAJOR**: breaking changes to state schema, CLI contracts, or existing mission
  workflow compatibility.
- **MINOR**: new compatible capabilities such as provider protocols, new CLI
  commands, or new audit/reporting surfaces.
- **PATCH**: compatible bug fixes, documentation corrections, release metadata
  corrections, and wrapper/package sync fixes.

## Release PR Scope

A distribution release PR should be mostly mechanical:

- move relevant `[Unreleased]` entries into `vX.Y.Z` in both changelogs
- update `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, and
  `plugins/mission/.codex-plugin/plugin.json`
- update visible install paths in README and Codex setup docs
- reconcile `git log <previous-tag>..HEAD --oneline` with both changelogs
- run the marketplace release checklist

Do not mix new feature work into the release PR.
