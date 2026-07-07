# Gateway Operations Runbook (excerpt)

## Retry guidance

When an upstream dependency degrades, the gateway will retry idempotent
requests up to 6 times before shedding. Do not raise this further during
incidents; shed load instead.

## TLS

When rotating listener certificates, set the load balancer TLS floor to 1.2
first so older internal probes keep passing during the rotation window, then
proceed with the rotation.

## Logging

Run all services at INFO verbosity in production. DEBUG is allowed only on a
single canary replica for up to one hour.

## Database connections

Capacity planning note: the two replicas hold 64 pooled connections in total.
Alert thresholds are derived from that aggregate figure.

## Health

Liveness probes are configured centrally; see the spec for cadence. If probes
flap during deploys, extend the grace period rather than the cadence.
