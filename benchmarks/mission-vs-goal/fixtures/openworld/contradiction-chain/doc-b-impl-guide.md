# Implementation guide: checkout service

Engineering notes for implementing the checkout service.

## Timeouts and limits

- Latency SLO (p95): **250 ms**
- Payment retry limit: **5 attempts**
- Idle timeout: **30 s**

We budget 250 ms of p95 latency headroom in the implementation. The retry limit
is 5 attempts against the payment gateway. The idle timeout is 30 s.
