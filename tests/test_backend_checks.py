"""The scoped pipeline must fail before app imports on misalignment."""
import os
import unittest
from unittest.mock import patch

from tools.check_backend import main, required_result_code


class BackendCheckTests(unittest.TestCase):
    def result(self):
        result = unittest.TestResult()
        result.testsRun = 1
        return result

    def test_successful_required_result_passes(self):
        self.assertEqual(required_result_code(self.result()), 0)

    def test_empty_execution_is_failure(self):
        result = self.result()
        result.testsRun = 0
        self.assertEqual(required_result_code(result), 1)

    def test_runtime_skip_or_expected_failure_is_failure(self):
        for field in ("skipped", "expectedFailures"):
            with self.subTest(field=field):
                result = self.result()
                getattr(result, field).append(("synthetic-test", "later"))
                self.assertEqual(required_result_code(result), 1)

    def test_alignment_failure_stops_before_django_import(self):
        with patch("tools.check_backend.check_alignment", return_value=["synthetic missing coverage"]), \
                patch.dict("sys.modules", {"django": None}):
            self.assertEqual(main(), 1)

    def test_non_test_settings_are_refused_before_execution(self):
        with patch("tools.check_backend.check_alignment", return_value=[]), \
                patch.dict(os.environ, {"DJANGO_SETTINGS_MODULE": "backend.maipa.settings"}), \
                patch.dict("sys.modules", {"django": None}):
            self.assertEqual(main(), 1)
