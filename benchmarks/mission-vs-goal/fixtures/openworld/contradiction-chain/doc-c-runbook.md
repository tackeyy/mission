# Operations runbook: checkout service

On-call reference for the checkout service.

## Observed limits

- Latency SLO (p95): 200 ms
- Payment attempts: the gateway allows **up to 6 tries**, but this count
  includes the initial attempt. So there are 5 retries after the first try,
  which matches the spec's retry limit of 5.
- Idle timeout: 30 s.

When triaging, remember the "6 tries" figure already includes the initial
attempt, so it is equivalent to a retry limit of 5 and is not a discrepancy.
