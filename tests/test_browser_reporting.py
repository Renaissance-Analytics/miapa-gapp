"""Failure reports may expose a fixed stage code, never arbitrary child diagnostics."""
import unittest
from tools.check_browser import browser_failure_evidence


class BrowserReportingTests(unittest.TestCase):
    def test_known_failure_preserves_stage_and_verified_cleanup_only(self):
        self.assertEqual(browser_failure_evidence({"reason": "profile_parent", "cleanup": True,
            "stdout": "synthetic private cookie", "path": "synthetic private home"}),
            {"checks": 0, "reason": "profile_parent", "cleanup": True})

    def test_unknown_messages_and_nonboolean_cleanup_are_not_reported_as_verified(self):
        for value in (None, {}, {"reason": "synthetic private diagnostic", "cleanup": True},
                      {"reason": "profile_parent", "cleanup": 1}):
            result = browser_failure_evidence(value)
            self.assertNotIn("synthetic private", str(result))
            self.assertFalse(result["cleanup"])
            self.assertEqual(result["checks"], 0)
