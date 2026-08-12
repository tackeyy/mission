# Issue #399 implementation

## TDD list

- [x] Reducer validates `mission-planning-provider-kpi/1` and preserves a zero denominator as `null`.
- [x] Conformance scenarios reuse provider lifecycle state contracts without duplicating E2E setup.
- [x] Stats and audit produce byte-equivalent KPI blocks from the same normalized state records.
- [x] Benchmark consumer accepts only the versioned validated block.
- [x] English/Japanese migration and operator runbooks describe the portable flow.

## Focused conformance evidence

| Contract | Focused test evidence |
| --- | --- |
| Simple/Unknown floor and Complex primary selection | `test_planning_provider_conformance.py::{test_real_cli_floor_recommendation_has_no_provider_selection,test_real_cli_complex_primary_recommendation_selects_contract_bound_provider}` |
| Preflight receipt and digest drift | `test_provider_preflight.py::{test_host_verified_receipt_runs_exact_packet_once_and_rejects_replay,test_input_byte_mutation_after_approval_blocks_spawn}` |
| Provider import, promotion, handoff mutation and zero executed step | `test_planning_provider_conformance.py::test_real_cli_provider_promote_handoff_rejects_mutated_step_without_lineage` |
| Required/optional fallback and legacy isolation | `test_planning_provider_lifecycle.py::{test_terminal_provider_failure_has_deterministic_optional_or_required_outcome,test_legacy_reselection_is_explicit_and_drops_unsafe_raw_records}` |
| Orphan/invalid plan and strict registry contract | `test_plan_import.py::{test_invalid_plan_input_preserves_state_and_no_candidate,test_import_rejects_noncurrent_invocation_or_unproven_consumed_preflight}`; `test_planning_provider_eligibility.py::test_primary_planning_mode_requires_structured_result_contract` |
