import json
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SubscriptionStatusTests(unittest.TestCase):
    def status(self, **scenario):
        node = shutil.which('node')
        if node is None:
            self.fail('Required Node runtime unavailable')
        result = subprocess.run([node, '--experimental-vm-modules',
            str(ROOT / 'tests/fixtures/account_status.mjs'), json.dumps(scenario)],
            capture_output=True, text=True, timeout=20, cwd=ROOT)
        self.assertEqual(result.returncode, 0, 'Synthetic account controller did not complete')
        return json.loads(result.stdout)

    def test_confirmed_subscription_is_ready_and_confirmed_signed_out_can_sign_in(self):
        for code, output, state in ((0, 'Logged in using ChatGPT\n', 'ready'),
                                    (1, 'Not logged in\n', 'sign_in')):
            with self.subTest(state=state):
                self.assertEqual(self.status(code=code, stderr=output),
                    {'value': {'state': state}, 'stopCalls': 2})

    def test_runtime_errors_and_conflicting_output_do_not_request_repeat_login(self):
        for code, output in ((1, 'Configuration unavailable: SYNTHETIC_PRIVATE_VALUE'),
                             (0, 'Logged in using ChatGPT\nSYNTHETIC_PRIVATE_VALUE'),
                             (1, 'Logged in using ChatGPT'),
                             (0, 'Not logged in'), (1, ''),
                             (0, 'Logged in using an API key: SYNTHETIC_PRIVATE_VALUE')):
            with self.subTest(code=code, output=output):
                self.assertEqual(self.status(code=code, stderr=output),
                    {'error': 'Account unavailable', 'stopCalls': 2})

    def test_failed_process_cleanup_refuses_even_a_confirmed_subscription(self):
        self.assertEqual(self.status(code=0, stderr='Logged in using ChatGPT', unverified=True),
                         {'error': 'Account unavailable', 'stopCalls': 2})
