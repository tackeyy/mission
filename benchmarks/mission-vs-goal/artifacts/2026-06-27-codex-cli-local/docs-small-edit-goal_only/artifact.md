# Goal

Clarify one README installation prerequisite without changing behavior.

# Result

Identified the smallest useful documentation clarification: the README installation instructions use `git clone`, so the installation prerequisites should explicitly require the Git CLI to be installed and available on `PATH`.

Proposed README-only clarification:

```md
- Git CLI available on `PATH` for the clone-based installation steps
```

# Evidence

- `README.md` `Installation` starts with `git clone https://github.com/tackeyy/mission.git "$MISSION_REPO"`.
- `README.md` `Requirements` currently lists macOS or Linux, Python 3.9 or later, `jq`, and Claude Code or Codex, but does not explicitly list Git.
- This benchmark run was constrained to write only this artifact, so no README behavior or repository behavior was changed.

# Assumptions

- The missing installation prerequisite is Git, because the documented install path depends on `git clone`.
- "Without changing behavior" means documentation-only wording, not command, package, hook, or runtime changes.
