"""Independent failure scenarios for the first CI gate; no application imports."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tools.alignment import check_alignment


class AlignmentGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "tests").mkdir()
        self.source = (
            "import unittest\n"
            "class ExampleTests(unittest.TestCase):\n"
            "    def test_allowed(self):\n"
            "        self.assertEqual(sum([2, 3]), 5)\n"
            "    def test_denied(self):\n"
            "        with self.assertRaises(ValueError):\n"
            "            int('invalid')\n"
        )
        self.acceptance = {"schema_version": 1, "requirements": [{
            "id": "EXAMPLE", "risk": "An invalid request could be accepted.",
            "scenarios": [
                {"id": "EXAMPLE.allowed", "kind": "positive", "given": "Valid inputs 2 and 3",
                 "when": "They are summed", "then": "The exact total is 5",
                 "tests": ["tests.test_example.ExampleTests.test_allowed"]},
                {"id": "EXAMPLE.denied", "kind": "negative", "given": "The text invalid",
                 "when": "It is converted to an integer", "then": "ValueError is raised",
                 "tests": ["tests.test_example.ExampleTests.test_denied"]},
            ]
        }]}
        self.write_source()
        self.write_acceptance()
        self.review()

    def write_source(self):
        (self.root / "tests/test_example.py").write_text(self.source, encoding="utf-8")

    def write_acceptance(self):
        (self.root / "tests/acceptance.json").write_text(json.dumps(self.acceptance), encoding="utf-8")

    def review(self):
        # Fixture author computes the independent review format, not the gate's helper.
        reviews = []
        for requirement in self.acceptance["requirements"]:
            for scenario in requirement["scenarios"]:
                digest = hashlib.sha256(json.dumps(scenario, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                for test in scenario["tests"]:
                    reviews.append({"scenario": scenario["id"], "test": test,
                                    "scenario_sha256": digest,
                                    "source_sha256": hashlib.sha256(self.source.encode()).hexdigest(),
                                    "expected_observation": scenario["then"], "decision": "aligned",
                                    "reason": "Exact result and invalid-input exception are asserted.",
                                    "reviewer": "fixture author"})
        (self.root / "tests/review.json").write_text(json.dumps({"schema_version": 1, "reviews": reviews}), encoding="utf-8")

    def assert_rejected(self, fragment):
        errors = check_alignment(self.root)
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_accepts_reviewed_positive_and_negative_cases(self):
        self.assertEqual(check_alignment(self.root), [])

    def test_rejects_missing_manifest(self):
        (self.root / "tests/acceptance.json").unlink()
        self.assert_rejected("acceptance.json")

    def test_rejects_empty_requirements(self):
        self.acceptance["requirements"] = []
        self.write_acceptance()
        self.assert_rejected("requirements")

    def test_rejects_missing_negative_case(self):
        self.acceptance["requirements"][0]["scenarios"].pop()
        self.write_acceptance()
        self.assert_rejected("negative")

    def test_rejects_nonexistent_test(self):
        self.acceptance["requirements"][0]["scenarios"][0]["tests"] = ["tests.test_example.ExampleTests.test_imaginary"]
        self.write_acceptance()
        self.assert_rejected("not discovered")

    def test_rejects_empty_test_suite(self):
        self.source = "import unittest\n"
        self.write_source()
        self.assert_rejected("No tests")

    def test_rejects_skipped_required_test(self):
        self.source = self.source.replace("    def test_allowed", "    @unittest.skip('later')\n    def test_allowed")
        self.write_source()
        self.assert_rejected("skip")

    def test_rejects_runtime_skip(self):
        self.source = self.source.replace("self.assertEqual(sum([2, 3]), 5)", "self.skipTest('later')")
        self.write_source()
        self.assert_rejected("skip")

    def test_rejects_expected_failure(self):
        self.source = self.source.replace("    def test_allowed", "    @unittest.expectedFailure\n    def test_allowed")
        self.write_source()
        self.assert_rejected("expectedFailure")

    def test_rejects_weakened_assertion_without_new_review(self):
        self.source = self.source.replace("self.assertEqual(sum([2, 3]), 5)", "self.assertIsNotNone(sum([2, 3]))")
        self.write_source()
        self.assert_rejected("source changed")

    def test_rejects_vacuous_assertion_even_if_reviewed(self):
        self.source = self.source.replace("self.assertEqual(sum([2, 3]), 5)", "self.assertTrue(True)")
        self.write_source()
        self.review()
        self.assert_rejected("meaningful assertion")

    def test_rejects_unreviewed_unrelated_mapping(self):
        scenarios = self.acceptance["requirements"][0]["scenarios"]
        scenarios[0]["tests"], scenarios[1]["tests"] = scenarios[1]["tests"], scenarios[0]["tests"]
        self.write_acceptance()
        self.assert_rejected("review")

    def test_rejects_changed_outcome_without_new_review(self):
        self.acceptance["requirements"][0]["scenarios"][0]["then"] = "Any non-null value is enough"
        self.write_acceptance()
        self.assert_rejected("scenario changed")

    def test_rejects_unmapped_real_test(self):
        self.source += "    def test_unmapped(self):\n        self.assertEqual(1 + 1, 2)\n"
        self.write_source()
        self.assert_rejected("Unmapped")

    def test_rejects_duplicate_requirement_id(self):
        self.acceptance["requirements"].append(self.acceptance["requirements"][0])
        self.write_acceptance()
        self.assert_rejected("Duplicate")

    def test_rejects_malformed_json_without_crashing(self):
        (self.root / "tests/acceptance.json").write_text("{broken", encoding="utf-8")
        self.assert_rejected("Invalid")

    def test_ignores_methods_outside_testcase_classes(self):
        self.source += "class Pretend:\n    def test_fake(self):\n        pass\n"
        self.write_source()
        self.review()
        self.assertEqual(check_alignment(self.root), [])

    def test_rejects_missing_assertion(self):
        self.source = self.source.replace("self.assertEqual(sum([2, 3]), 5)", "sum([2, 3])")
        self.write_source()
        self.assert_rejected("meaningful assertion")
