# Issue #655 implementation report

## Outcome

`init --new-mission` now admits only an explicitly requested, decodable,
terminal V5 session. The prior authoritative state is published as a
content-addressed immutable archive generation before a new mission is
committed under the same session identifier. Plain `init` retains its prior
command intent and rejection behavior.

## Boundaries

- Terminal admission is decided in `mission_application.lifecycle` using the
  existing terminal diagnosis and outcome derivation helpers.
- Active, non-terminal, corrupt-head, and corrupt-referenced-document cases
  fail closed without publishing an archive or replacing the live head.
- Archive generation publication is owned by the administrative persistence
  protocol. Directory descriptors pin the destination, every nested directory
  is fsynced bottom-up, and live authority replacement remains fenced and
  CAS-bound.
- The old assumptions record is captured with the strict stable-reader and
  included in the immutable generation. The replacement reserves a
  generation-specific empty assumptions record with exclusive creation.
- Aggregate membership is reconciled through the recoverable durable intent
  protocol. The session identifier remains unchanged, so no separate removal
  is issued.
- The V5 authority commit point is exposed to the coordinator, so a later
  output or preflight failure never removes the assumptions record referenced
  by an already-advanced head.
- A fresh initializer constructs the new document, so score, specialist,
  command-outcome, and verification observations are not inherited.
- No handoff or specialist lifecycle implementation was changed.

## Verification

- Issue E2E plus targeted init, lifecycle, fenced-commit, crash-recovery, and
  Python 3.9 compatibility regressions: `450 passed` (`24` new Issue E2E and
  `426` existing regressions).
- Strict-reader, authoritative-reader, recoverable aggregate-index, artifact
  hygiene, and vendor-fingerprint regressions: `159 passed`.
- Thin-adapter ratchet against the exact `origin/main` SHA: passed with 486
  measured functions and no increase.
- System Python 3.9.6: CLI `init --help` exited 0.
- Canonical/plugin byte comparison: 9 of 9 changed production pairs matched.
- Whitespace validation: `git diff --check` passed.
- The prior session report recorded an independent final Checker `PASS`; it was
  not rerun in this continuation.

The targeted run intentionally excluded the full test suite, as required by
the task. No commit, push, merge, release, or activation was performed.
