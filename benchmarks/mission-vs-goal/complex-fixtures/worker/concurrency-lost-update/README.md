# concurrency-lost-update

Requirement: A deterministic barrier preserves both concurrent increments.

The starter has a defect: Two writers read the same value and one increment is lost.
Repair the observable contract without weakening the public smoke check.
