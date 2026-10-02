# multi-module-cache

Requirement: A cache invalidation crosses parser and service modules.

The starter has a defect: A write returns a stale cached value after an update.
Repair the observable contract without weakening the public smoke check.
