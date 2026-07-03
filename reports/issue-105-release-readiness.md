# Issue #105 Release Readiness

Date: 2026-07-03 JST

## Scope

Implement and release the ADR-002 Stage 1 default flip:

- Evidence-less `mission-state.py push-score` hard rejects by default.
- `--scoring-json` remains the preferred scoring path.
- `--scoring-output` remains accepted for legacy markdown scorer output.
- `MISSION_REQUIRE_SCORING_EVIDENCE=0` remains as a temporary migration escape hatch.
- The generated `generated=true` scoring fallback is removed.

## Precondition Check

Source checked:

- `/Users/tackeyy/.codex/worktrees`
- `/Users/tackeyy/dev/mission`
- `/Users/tackeyy/dev/mission/.mission-state/archive`

Observed `score_source == "scoring-json"` usage:

| Count | Distinct session | Evidence path exists |
|---:|---:|---|
| 3 entries | 2 sessions | yes |

Evidence rows:

- `cx-019f25ec-976b-7440-8604-dfe0b6dec748` / mission `3d08bc6d9af29fe5` / iteration 1 / archive exists
- `cx-019f267b-18fd-78d2-9cb1-efcdd0f9343e` / mission `a4e094b27a5c5035` / iteration 1 / archive exists
- archived duplicate of `cx-019f25ec-976b-7440-8604-dfe0b6dec748` / archive exists

Interpretation: enough to proceed with the default flip when combined with the preserved `MISSION_REQUIRE_SCORING_EVIDENCE=0` migration escape hatch and the existing strict-path tests. It is not a broad production bake-in; this report records that limitation.

## Implementation Summary

- `cmd_push_score` now rejects evidence-less legacy scoring unless `MISSION_REQUIRE_SCORING_EVIDENCE=0`.
- The generated fallback archive function and call path were removed.
- Tests now cover default reject, temporary escape hatch, scoring JSON acceptance, no generated archive, and existing legacy path behavior under explicit escape hatch.
- Canonical `skills/mission` changes were synced into `plugins/mission`.
- Plugin manifest/install path versions were bumped to `1.0.7`.

## Verification

- `python3 -m pytest skills/mission/tests -q`
  - Result: `473 passed in 45.00s`
- JSON validation:
  - `.claude-plugin/plugin.json`
  - `.codex-plugin/plugin.json`
  - `plugins/mission/.codex-plugin/plugin.json`
- SKILL size guard:
  - `skills/mission/SKILL.md`: 420 lines
  - `plugins/mission/skills/mission/SKILL.md`: 420 lines
- Remote tag pre-check:
  - `v1.0.6` exists
  - `v1.0.7` did not exist before release publication

## Release Plan

1. Commit the implementation and release metadata.
2. Push branch and open PR with `Closes #105`.
3. Merge after CI/checks are acceptable.
4. Tag merged `main` as `v1.0.7`.
5. Push tag and create GitHub Release `v1.0.7`.
6. Verify:
   - `git ls-remote --tags origin v1.0.7`
   - `gh release view v1.0.7 --repo tackeyy/mission`
