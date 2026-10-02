# partial-failure-selective-retry

Requirement: A retry executes only operations that were not accepted.

The starter has a defect: A retry repeats an already accepted external operation.
Repair the observable contract without weakening the public smoke check.
