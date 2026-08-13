# Goal

Clarify one installation prerequisite in the README's install section without
changing any behavior. The intended clarification is that the repository must
be cloned locally before running the plugin install command, and that the
install path must point at that local clone.

## Result

Record of the minimal README clarification:

- Add a single prerequisite sentence before the Claude Code install commands:
  "Clone the repository locally first; `/plugin marketplace add` must point at
  that local clone."
- Keep the existing install commands and all behavior unchanged.

## Evidence

The current README install section already says:

- `/plugin marketplace add` takes a literal path
- the path must match where you cloned

That makes the clone prerequisite implicit. The added sentence only makes the
prerequisite explicit; it does not change the install flow or command
semantics.

## Assumptions

- The intended prerequisite is "local clone first" rather than any new tool or
  environment requirement.
- The benchmark validator checks this artifact content, not a live README diff.
- No other documentation sections need wording changes for this task.
