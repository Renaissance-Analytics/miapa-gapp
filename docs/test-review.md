# Acceptance review

`tests/acceptance.json` defines expected outcomes independently of implementation.
`tests/review.json` records Maipa's acceptance-to-test review for this foundation;
it does not claim an independent human or second agent reviewed it.

Review each mapped test against its scenario's given/when/then and risk. Check
that assertions distinguish the required outcome from a plausible wrong one,
and that failure/authority cases test outcomes beyond a response status. The
review reason explains that comparison. After a genuine review, record the
SHA256 of canonical sorted scenario JSON and the UTF-8 test file with LF line
endings. Do not automatically refresh reviews to clear a failed gate.

The gate catches missing/empty/malformed coverage, nonexistent or unmapped tests,
missing positive/negative cases, duplicate IDs, skipped/expected-failure tests,
absent/vacuous assertions and stale scenario/source reviews. Its unrelated and
weakened fixtures prove stale or absent review is rejected. It cannot infer that
a newly approved assertion or review is truthful or sufficiently strong. Semantic
review is required in addition to traceability and execution.

Supported discovery is deliberately explicit: declared `unittest.TestCase` and
imported Django `TestCase`, `SimpleTestCase` or `TransactionTestCase`, with normal
`test_` methods. Dynamic/generated/inherited test classes need a supported
discovery extension and tests before adoption. The Django runner then discovers
and executes real tests; no mocked execution result satisfies the gate.

Observed TDD sequence in this local candidate:

1. Alignment tests failed on the missing module; the first implementation then
   failed three behavior cases (valid Windows source hashes and expectedFailure).
2. Fixing newline normalization and skip detection made all 18 gate tests pass.
3. With alignment first and empty API routes, all 16 API tests failed: eight
   wrong-status assertions and eight missing JSON/token/header errors.
4. The implemented API made all 34 tests pass (18 alignment, 16 API).
5. Add configuration and pipeline checks, then review/map their outcomes. The
   project-local isolated environment runs 45 required tests with alignment first.

The runtime runner also requires exact agreement with reviewed static test IDs.
Configuration tests use fresh processes and synthetic keys; pipeline tests prove
an alignment failure stops before Django import and non-test settings are refused.
Independent finance acceptance seeds are in [finance-oracles.md](finance-oracles.md).

These are scoped backend checks. No frontend/browser, live provider or release
acceptance is claimed by this record.
