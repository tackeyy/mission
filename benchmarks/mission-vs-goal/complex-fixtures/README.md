# Complex repair fixtures

This development cohort contains twelve neutral repositories: two tasks in each
of the multi-module, compatibility, partial-failure, rerun, aggregation, and
concurrency families. Each worker snapshot contains a requirement, two source
modules, and a public smoke check. Its external evaluator, reference repair,
and good control are stored separately and are excluded by the positive
allowlist export from #882.

The evaluator records observed task/family/version/candidate digest and retains
failed, blocked, and invalid outputs. It does not accept a candidate-provided
success marker. These twelve development fixtures are not evidence of a
tenfold improvement or of a general model-quality result.
