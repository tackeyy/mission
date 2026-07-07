# Release announcement — draft (English, source of truth)

We are announcing the public beta of our task runner.

- In one internal pilot, median latency improved by 18%. This is a single
  controlled measurement, not a general performance claim.
- Interrupted runs can be resumed from persisted state. Resumption succeeded
  in our test scenarios; it is not guaranteed under every failure mode.
- The release is verified by 402 automated tests.
- Irreversible actions require manual approval before they run.
- It works with existing CLI workflows without changes.
- No telemetry is collected.
