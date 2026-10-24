"""Independent orchestration outcomes; no real subprocess work in these fixtures."""
from copy import deepcopy
import unittest

from tools import ci


class CiTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.identity = {"commit": "a" * 40, "tree": "b" * 40, "source_sha256": "c" * 64}
        self.good = {"returncode": 0, "checks": 1}

    def execute(self, stage):
        self.calls.append(stage)
        if stage in {"backend", "frontend"}:
            return {"returncode": 0, "tests": 3, "passed": 3, "skipped": 0, "failed": 0}
        if stage == "browser":
            return {"returncode": 0, "checks": 8, "build_id": "d" * 64,
                    "browser": "Chrome/156.0", "cleanup": True}
        if stage == "build":
            return {"returncode": 0, "checks": 1, "build_id": "d" * 64}
        return deepcopy(self.good)

    def test_complete_run_binds_identity_and_browser_is_last(self):
        report = ci.run_pipeline(self.execute, lambda: deepcopy(self.identity))
        self.assertTrue(report["ok"])
        self.assertEqual(self.calls, ["alignment", "dependencies", "static", "backend", "frontend", "migrations", "build", "browser"])
        self.assertEqual(report["candidate"], self.identity)
        self.assertEqual(report["stages"][-1]["build_id"], "d" * 64)

    def test_alignment_failure_prevents_all_later_work(self):
        self.good["returncode"] = 1
        report = ci.run_pipeline(self.execute, lambda: self.identity)
        self.assertFalse(report["ok"])
        self.assertEqual(self.calls, ["alignment"])

    def test_failure_at_every_stage_stops_later_effects(self):
        expected = ["alignment", "dependencies", "static", "backend", "frontend", "migrations", "build", "browser"]
        for failed in expected:
            self.calls.clear()
            def execute(stage):
                result = self.execute(stage)
                if stage == failed:
                    result["returncode"] = 1
                return result
            report = ci.run_pipeline(execute, lambda: self.identity)
            self.assertFalse(report["ok"], failed)
            self.assertEqual(self.calls, expected[:expected.index(failed) + 1])

    def test_empty_skipped_failed_or_missing_test_evidence_cannot_pass(self):
        for evidence in ({"tests": 0, "passed": 0, "skipped": 0, "failed": 0},
                         {"tests": 3, "passed": 2, "skipped": 1, "failed": 0},
                         {"tests": 3, "passed": 2, "skipped": 0, "failed": 1},
                         {"tests": 3, "passed": 2, "skipped": 0, "failed": 0}, {}):
            self.calls.clear()
            def execute(stage):
                result = self.execute(stage)
                return {"returncode": 0, **evidence} if stage == "backend" else result
            self.assertFalse(ci.run_pipeline(execute, lambda: self.identity)["ok"])
            self.assertEqual(self.calls[-1], "backend")

    def test_browser_missing_cleanup_or_wrong_build_cannot_pass(self):
        for change in ({"cleanup": False}, {"browser": ""}, {"build_id": "e" * 64}, {"checks": 0}):
            def execute(stage):
                result = self.execute(stage)
                return {**result, **change} if stage == "browser" else result
            self.assertFalse(ci.run_pipeline(execute, lambda: self.identity)["ok"])

    def test_source_change_even_after_browser_invalidates_candidate(self):
        counter = 0
        def snapshot():
            nonlocal counter
            counter += 1
            return {**self.identity, "source_sha256": ("e" * 64 if counter > 8 else "c" * 64)}
        report = ci.run_pipeline(self.execute, snapshot)
        self.assertFalse(report["ok"])
        self.assertEqual(self.calls[-1], "browser")

    def test_command_exception_and_unstructured_success_fail_closed(self):
        for invalid in (None, {}, {"returncode": 0}, {"returncode": False, "checks": 1}):
            self.assertFalse(ci.run_pipeline(lambda stage: invalid, lambda: self.identity)["ok"])
        def broken(stage):
            raise RuntimeError("synthetic private diagnostic")
        report = ci.run_pipeline(broken, lambda: self.identity)
        self.assertFalse(report["ok"])
        self.assertNotIn("synthetic private diagnostic", str(report))

    def test_missing_or_malformed_candidate_identity_prevents_execution(self):
        for candidate in (None, {}, {**self.identity, "commit": "main"}, {**self.identity, "source_sha256": "unknown"}):
            self.calls.clear()
            self.assertFalse(ci.run_pipeline(self.execute, lambda: candidate)["ok"])
            self.assertEqual(self.calls, [])
