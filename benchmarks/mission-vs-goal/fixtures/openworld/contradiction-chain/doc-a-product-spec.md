# Product spec: checkout service

This is the source-of-truth product specification for the checkout service.

## Service level objectives

- Checkout API latency SLO (p95): **200 ms**
- Payment retry limit: **5 attempts**
- Idle connection timeout: **30 s**

The latency SLO of 200 ms is the number every downstream document must agree
with. The retry limit counts every attempt against the payment gateway.
