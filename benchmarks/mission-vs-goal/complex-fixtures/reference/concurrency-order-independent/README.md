# concurrency-order-independent

Contract: Concurrent order does not change the canonical result.

The service registers two ranked candidates in the supplied arrival order. It must return the canonical winner (lowest rank, then identifier) and report that both worker threads completed.
