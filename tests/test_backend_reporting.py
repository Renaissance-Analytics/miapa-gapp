"""Public CI evidence must locate failures without disclosing diagnostics."""
import io
import json
import unittest


class BackendReportingTests(unittest.TestCase):
    def test_known_test_and_native_phase_are_retained_without_exception_text(self):
        from tools.backend_reporting import failure_record
        identifier = "tests.test_worker_isolation.WorkerIsolationTests.test_real_worker_and_descendant_can_access_only_their_assigned_files"
        error = OSError("synthetic-private-path-and-token")
        error.operation, error.native_code = "create_process", 5
        error.__context__ = TimeoutError("synthetic-private-child-detail")
        record = failure_record(identifier, error, {identifier})
        self.assertEqual(record, {"test": identifier, "chain": [
            {"kind": "OSError", "operation": "create_process", "native_code": 5}, {"kind": "TimeoutError"}]})
        self.assertNotIn("synthetic-private", json.dumps(record))

    def test_unknown_test_exception_metadata_and_cycles_are_bounded_and_redacted(self):
        from tools.backend_reporting import failure_record
        error = RuntimeError("synthetic-private-message")
        error.operation, error.native_code = "synthetic-private-operation", "synthetic-private-code"
        error.__context__ = error
        record = failure_record("synthetic-private-test", error, set())
        self.assertEqual(record, {"test": "unavailable", "chain": [{"kind": "RuntimeError"}]})
        self.assertNotIn("synthetic-private", json.dumps(record))

    def test_real_test_result_keeps_failure_and_collects_only_safe_evidence(self):
        from tools.backend_reporting import ReportedTextTestResult
        result = ReportedTextTestResult(io.StringIO(), True, 0)
        case = unittest.FunctionTestCase(lambda: None)
        result.startTest(case)
        error = AssertionError("synthetic-private-assertion-detail")
        result.addFailure(case, (AssertionError, error, None))
        result.stopTest(case)
        self.assertEqual(result.testsRun, 1)
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.safe_failures, [{"test": "unavailable", "chain": [{"kind": "AssertionError"}]}])
        self.assertNotIn("synthetic-private", json.dumps(result.safe_failures))

    def test_untrusted_failure_report_fields_cannot_escape_the_ci_sanitizer(self):
        from tools.backend_reporting import failure_evidence
        identifier = "tests.test_backend_reporting.BackendReportingTests.test_untrusted_failure_report_fields_cannot_escape_the_ci_sanitizer"
        data = {"tests": 1, "passed": 0, "failed": 1, "skipped": 0, "secret": "synthetic-private",
            "failures": [{"test": identifier, "traceback": "synthetic-private", "chain": [
                {"kind": "OSError", "operation": "grant_acl", "native_code": 5, "message": "synthetic-private"}]}]}
        evidence = failure_evidence(data, {identifier})
        self.assertEqual(evidence, {"returncode": 1, "tests": 1, "passed": 0, "failed": 1, "skipped": 0,
            "failures": [{"test": identifier, "chain": [{"kind": "OSError", "operation": "grant_acl", "native_code": 5}]}]})
        self.assertNotIn("synthetic-private", json.dumps(evidence))
