# concurrency-lost-update

Contract: A deterministic barrier preserves both concurrent increments.

The service receives an initial count and two deltas. It must return the final count and report that both worker threads completed.
