# mission v1.0.7 Version and Changelog Audit

Date: 2026-07-05

## Conclusion

- The latest published distribution version is `v1.0.7`: the local tag exists at `4678f5274f9c0b01b490ecb89baf3d5804dfff32`, the remote tag exists at the same SHA, and the GitHub Release exists.
- The installed marketplace cache at `/Users/tackeyy/.codex/plugins/cache/mission-marketplace/mission/1.0.7` is not the exact `v1.0.7` tag package. It matches local `HEAD` (`0897604 fix mission audit specialist state gates (#116)`) plus `__pycache__`, which is one commit after the `v1.0.7` tag.
- The installed cache is not current with source `origin/main`. `origin/main` is `4bffdd0fb6b74d338ca5f0867d5df96b5230c207`, 12 commits ahead of local `HEAD` and 13 commits ahead of `v1.0.7`.
- Manifest versions are still `1.0.7` in `origin/main`, `plugins/mission/.codex-plugin/plugin.json`, the `v1.0.7` tag, and the installed cache. This means `1.0.7` is the latest declared release version, but not the latest source code state.
- `CHANGELOG.md` and `CHANGELOG.ja.md` include substantial `Unreleased` entries for the post-`v1.0.7` changes, but they do not fully cover the `v1.0.7..origin/main` code range.

## Evidence

### Repository State

- Current checkout: `main...origin/main [behind 12]`.
- Existing unrelated local changes are present under `benchmarks/mission-vs-goal/`; this audit did not modify them.
- `origin/main`: `4bffdd0fb6b74d338ca5f0867d5df96b5230c207`.
- Local `HEAD`: `0897604 fix mission audit specialist state gates (#116)`.
- Local `v1.0.7`: `4678f5274f9c0b01b490ecb89baf3d5804dfff32`.

### Release Verification

- `git ls-remote --tags origin v1.0.7` returned `4678f5274f9c0b01b490ecb89baf3d5804dfff32 refs/tags/v1.0.7`.
- `gh release view v1.0.7 --repo tackeyy/mission` returned:
  - `tagName`: `v1.0.7`
  - `name`: `mission v1.0.7`
  - `isDraft`: `false`
  - `isPrerelease`: `false`
  - `publishedAt`: `2026-07-03T06:11:23Z`
  - `url`: `https://github.com/tackeyy/mission/releases/tag/v1.0.7`

### Manifest Version Check

All checked manifests declare `1.0.7`:

- `origin/main:.codex-plugin/plugin.json`
- `origin/main:plugins/mission/.codex-plugin/plugin.json`
- `v1.0.7:.codex-plugin/plugin.json`
- `/Users/tackeyy/.codex/plugins/cache/mission-marketplace/mission/1.0.7/.codex-plugin/plugin.json`

### Installed Cache Comparison

Comparison target: installed cache `/Users/tackeyy/.codex/plugins/cache/mission-marketplace/mission/1.0.7`.

- Compared to `v1.0.7:plugins/mission`, the installed cache differs in:
  - `scripts/mission-audit.py`
  - `skills/mission/bin/mission-state.py`
  - plus local `skills/mission/lib/__pycache__`
- Compared to local `HEAD:plugins/mission`, the installed cache only has extra `skills/mission/lib/__pycache__`.
- Compared to `origin/main:plugins/mission`, the installed cache differs across mission skill docs, refs, `mission-state.py`, `mission-audit.py`, and lacks `skills/mission/lib/mission_common.py`.

Interpretation: the installed `1.0.7` cache is a post-tag local-main package, not the exact published `v1.0.7` package and not the latest source package.

## Changelog Coverage

`v1.0.7..origin/main` commits:

```text
0897604 fix mission audit specialist state gates (#116)
1764d49 benchmark: arm-blind scoring, counterbalanced order, model_id (#129) (#130)
efecceb push-score: reject inflated self-reported scores, guard re-push (#122) (#131)
9e18802 refs: OSS portability sweep — remove maintainer home paths & personal skill name (#118) (#132)
bbdbf13 mission-state: add `resume` command to unify recovery ordering (#123) (#133)
14c1741 docs: consistency sweep — open_high gate, --scoring-json example, --root (#128) (#134)
3c44814 refactor: share mission state helpers (#135)
039fe2c feat: aggregate reviewer scoring (#136)
b57917c docs: route phase 5 through aggregate reviews (#137)
4be618c feat: gate passes on findings evidence (#138)
826d8db feat: separate review agreement gate (#139)
b18541a docs: make critic plans executor-compatible (#140)
4bffdd0 docs: slim mission skill instructions (#141)
```

Covered well in `Unreleased`:

- aggregate reviewer scoring / standard Phase 5 aggregate flow
- reviewer agreement gate
- findings evidence pass gate
- shared `mission_common.py` audit/state helpers
- stale active no-score session handling
- task-required specialist selection logging
- audit recognition of scoring evidence paths

Missing or under-covered in `Unreleased`:

- benchmark runner/reporting changes: arm-blind scoring, counterbalanced order, `model_id`, schema/README updates
- `push-score` self-reported score inflation rejection and re-push guard
- `mission-state.py resume` command
- OSS portability sweep removing maintainer home paths and personal skill names
- slimmed mission skill instructions and critic-plan compatibility docs

## Verification Commands

- `git fetch -p origin`
- `git status --short --branch`
- `git tag --list 'v*' --sort=-v:refname`
- `git log --oneline v1.0.7..origin/main`
- `git ls-remote --tags origin v1.0.7`
- `gh release view v1.0.7 --repo tackeyy/mission --json tagName,name,targetCommitish,isDraft,isPrerelease,publishedAt,url`
- `diff -qr <git archive v1.0.7 plugins/mission> /Users/tackeyy/.codex/plugins/cache/mission-marketplace/mission/1.0.7`
- `diff -qr <git archive HEAD plugins/mission> /Users/tackeyy/.codex/plugins/cache/mission-marketplace/mission/1.0.7`
- `diff -qr <git archive origin/main plugins/mission> /Users/tackeyy/.codex/plugins/cache/mission-marketplace/mission/1.0.7`
- `python3 -m pytest skills/mission/tests/test_doc_consistency.py -q` in a temporary `origin/main` archive: `31 passed in 0.05s`

