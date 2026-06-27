# Agent Guardrails

This repository is OSS. Keep public behavior portable across users, machines, and agent runtimes.

## Personal Skill Boundary

- Do not add maintainer-local or organization-specific skills to OSS defaults.
- Do not hard-code personal skill names, private workflow taxonomies, home-directory paths, or local command providers into core behavior.
- Use project/user registries and skill manifests for private integrations:
  - `.mission/specialists.yml` for project-local policy
  - `~/.config/mission/specialists.yml` for user-local defaults
  - installed `mission-specialist.yml` files for skill-provided metadata
- Tests for extension behavior must use neutral fixture names, not a maintainer's private skill set.
- Documentation may mention private skills only as clearly labeled examples, never as required or bundled OSS capabilities.

## Specialist Policy

- OSS code may define generic provider protocols, registry schemas, ranking logic, audit output, and safety gates.
- External specialists are evidence providers only. `mission` owns state, scoring, pass/fail gates, and final reporting.
- Broad orchestrator skills must be bounded to a single evidence artifact such as a plan, review, or synthesis note. Do not nest a second autonomous completion loop inside `/mission`.

## Distribution Release Rule

- A version bump is not a completed distribution release until the matching `vX.Y.Z` git tag exists on the remote and the GitHub Release for that tag exists.
- Before reporting a distribution release as complete, verify both with `git ls-remote --tags origin vX.Y.Z` and `gh release view vX.Y.Z --repo tackeyy/mission`.
- If manifests, README install paths, or changelogs are updated to a new version, the same task must carry through tag push and GitHub Release creation/update, unless the user explicitly asks to stop before publication.
