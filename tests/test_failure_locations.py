"""Safe source locations improve hosted diagnostics without raw tracebacks."""
import json
import sys
import unittest


class FailureLocationTests(unittest.TestCase):
    def test_failure_location_is_a_source_line_without_path_or_exception_text(self):
        from tools.backend_reporting import failure_record, failure_evidence
        identity = self.id()
        try:
            expected_line = sys._getframe().f_lineno + 1
            raise AssertionError('synthetic-private-path-and-token')
        except AssertionError as error:
            record = failure_record(identity, error, {identity})
        self.assertEqual(record, {'test': identity, 'chain': [{'kind': 'AssertionError', 'test_line': expected_line}]})
        evidence = failure_evidence({'failures': [record]}, {identity})
        self.assertEqual(evidence['failures'], [record])
        self.assertNotIn('synthetic-private', json.dumps(evidence))
        self.assertNotIn(__file__, json.dumps(evidence))

    def test_untrusted_source_locations_are_bounded_and_unknown_tests_have_no_line(self):
        from tools.backend_reporting import failure_evidence
        for identity, location in ((self.id(), True), (self.id(), 1_000_001),
                                   (self.id(), 'synthetic-private'), ('synthetic-private-test', 14)):
            record = failure_evidence({'failures': [{'test': identity, 'chain': [
                {'kind': 'AssertionError', 'test_line': location, 'path': 'synthetic-private'}]}]}, {self.id()})
            self.assertEqual(record['failures'][0]['chain'], [{'kind': 'AssertionError'}])
            self.assertNotIn('synthetic-private', json.dumps(record))
