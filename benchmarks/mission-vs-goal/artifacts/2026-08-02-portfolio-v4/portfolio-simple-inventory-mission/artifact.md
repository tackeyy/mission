# portfolio-simple-inventory (mission arm)

## Goal

Reconcile the two inventories in `benchmarks/mission-vs-goal/fixtures/portfolio/inventory-lists.md`: report every SKU present in List A (physical scan) but missing from List B (recorded), and every SKU present in List B but missing from List A. Quote each SKU exactly.

## Result

**A-only SKUs (in List A, physical scan, but missing from List B, recorded):**

- `SKU-1093`
- `SKU-1177`
- `SKU-9001`

**B-only SKUs (in List B, recorded, but missing from List A, physical scan):**

- `SKU-8002`
- `SKU-8107`

No other discrepancies were found: every other SKU in List A appears in List B and vice versa.

## Evidence

- **Routing**: `mission-state.py init` was invoked with `--complexity Simple` and no `--issue-ref`. The CLI returned a `route: "goal"` verdict (`mission_id: f7f16ae6a23b3411`, reason: `"Simple complexity with no irreversible/security signals (#276)"`). Per the routing guidance, this task is completed under the goal-contract headings rather than a mission loop, and no mission `pass` is claimed. No mission state was created (`.mission-state/` was not touched for this task).
- **Fixture read**: Read exactly `benchmarks/mission-vs-goal/fixtures/portfolio/inventory-lists.md` (187 lines) in full. It contains two sections: `# Warehouse Inventory (List A: physical scan)` (lines 3-93, 91 bullet items) and `# Inventory System (List B: recorded)` (lines 97-186, 90 bullet items).
- **Extraction method**: Split the fixture into List A and List B item sets using an `awk` pass keyed on the two section headers, restricted to lines under this file only (no other files under `benchmarks/mission-vs-goal/` were read, opened, or listed).
- **Duplicate check**: `sort | uniq -d` found zero duplicate SKUs within List A and zero within List B. Raw line counts (List A = 91, List B = 90) equal the unique-value counts, confirming each list has no repeated entries that could distort the reconciliation.
- **Set comparison**: `comm -23` (A minus B) produced exactly `SKU-1093`, `SKU-1177`, `SKU-9001` — each verified present verbatim in List A (fixture lines 89, 17, 79 respectively) and absent from every line of List B.
- **Set comparison**: `comm -13` (B minus A) produced exactly `SKU-8002`, `SKU-8107` — each verified present verbatim in List B (fixture lines 140, 168 respectively) and absent from every line of List A.
- **Confirmed findings**: the 5 SKUs listed under Result above (3 A-only, 2 B-only). There were no candidate mismatches considered and then rejected — the set-difference computation is exact and exhaustive over both lists (91 + 90 = 181 total line-items partitioned into 86 common SKUs, 3 A-only, and 2 B-only: 86+3 = 89 unique-in-A minus... — concretely: |A|=91, |B|=90, |A∩B|=88, |A only|=3, |B only|=2, consistent with 88+3=91 and 88+2=90).

## Assumptions

- SKU identifiers are treated as case-sensitive, exact-string matches (e.g., `SKU-1093` must match `SKU-1093` verbatim; no fuzzy or numeric-only matching was applied).
- The fixture's two `#`-headed sections were treated as authoritative for List A/List B membership; no other file or external system was consulted, per the task's scope restriction.
- Unmeasured: whether the fixture is itself internally consistent with any upstream physical/system-of-record data outside this file — that is out of scope and was not checked.

## Stop Condition

Task complete: the artifact has been written to `benchmarks/mission-vs-goal/run-output/2026-08-02-portfolio-v4/portfolio-simple-inventory-mission.md` with the A-only and B-only SKU lists reported separately, each backed by quoted exact identifiers and a documented extraction/verification method. No further iteration is needed for this Simple, goal-routed task; no commit/push/network/package-install actions were taken.
