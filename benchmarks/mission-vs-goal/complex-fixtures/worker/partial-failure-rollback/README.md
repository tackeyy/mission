# partial-failure-rollback

Requirement: A failed batch does not leave partial persistent state.

The starter has a defect: A partial write remains visible after a later operation fails.
Repair the observable contract without weakening the public smoke check.
