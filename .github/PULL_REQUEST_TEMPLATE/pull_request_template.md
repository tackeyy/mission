## Description

Describe the change and why it is needed.

## Type of Change

- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Test improvement
- [ ] Refactoring
- [ ] Configuration or tooling change

## Test value

Decision criteria: [Test Value Policy](https://github.com/tackeyy/mission/blob/main/AGENTS.md#test-value-policy).

<!-- One short explanation
per group with the same rationale; for unrelated changes, replace the bullets
with N/A and a reason. Name sufficient existing tests when no new test is needed. -->

- Failure detected:
- Difference from existing tests / retained protection after consolidation or deletion:
- Execution and maintenance cost (measured if available; otherwise unmeasured):

## Testing

List the commands you ran and their results.

```bash
cd skills/mission
python3 -m pytest -q
```

## Checklist

- [ ] I have performed a self-review
- [ ] I have updated relevant documentation
- [ ] I have checked regression protection under the Test Value Policy and completed Test value
- [ ] Existing tests pass locally
- [ ] Hook changes were checked with `shellcheck scripts/mission-stop-guard.sh`
- [ ] User-visible behavior changes are described clearly

## Additional Notes

Add any migration notes, compatibility concerns, or reviewer context.
