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

    def test_claude_status_is_bound_to_its_subscription_and_assigned_configuration(self):
        for code, logged_in, method, state in ((0, True, 'claude.ai', 'ready'), (1, False, 'none', 'sign_in')):
            self.assertEqual(self.status(provider='claude', code=code, stdout=json.dumps({
                'loggedIn': logged_in, 'authMethod': method, 'configDirectory': 'C:\\synthetic\\account',
                'email': 'synthetic@example.invalid'}), expectedArgs=['auth', 'status']),
                {'value': {'state': state}, 'stopCalls': 2})

    def test_claude_refuses_foreign_home_api_credentials_conflicts_and_private_output(self):
        valid = {'loggedIn': True, 'authMethod': 'claude.ai', 'configDirectory': 'C:/synthetic/account'}
        for output, code, stderr in ((json.dumps({**valid, 'configDirectory': 'C:/foreign'}), 0, ''),
                (json.dumps({**valid, 'authMethod': 'api_key'}), 0, ''),
                (json.dumps(valid), 1, ''), (json.dumps(valid), 0, 'PRIVATE_PROVIDER_ERROR'),
                ('PRIVATE_PROVIDER_ERROR', 1, ''), ('{}', 0, '')):
            self.assertEqual(self.status(provider='claude', code=code, stdout=output, stderr=stderr),
                             {'error': 'Account unavailable', 'stopCalls': 2})

    def test_claude_login_exposes_only_a_validated_transient_provider_link(self):
        url = 'https://claude.ai/oauth/authorize?state=synthetic&code_challenge=synthetic'
        result = self.status(provider='claude', login=True, stdout='Open this URL: '+url,
                             expectedArgs=['auth', 'login', '--claudeai'])
        self.assertEqual(result, {'value': {'state': 'sign_in', 'login': {'url': url, 'accepts_code': True}}, 'stopCalls': 1})
        for unsafe in ('https://attacker.invalid/oauth/authorize', 'https://claude.ai.attacker.invalid/oauth/authorize',
                       'https://user:secret@claude.ai/oauth/authorize', 'https://claude.ai/oauth/authorize#secret'):
            self.assertEqual(self.status(provider='claude', login=True, stdout=unsafe),
                             {'value': {'state': 'sign_in'}, 'stopCalls': 1})

    def test_claude_private_callback_goes_only_to_native_stdin_once_with_no_echo(self):
        self.assertEqual(self.status(provider='claude', login=True,
            stdout='https://claude.ai/oauth/authorize?state=synthetic', authorize='SYNTHETIC_CODE#STATE'),
            {'value': {'state': 'sign_in'}, 'stopCalls': 1, 'privateInputValid': True})
        for code in ('', 'two codes', 'code\nINJECT', 'x'*2049):
            self.assertEqual(self.status(provider='claude', login=True,
                stdout='https://claude.ai/oauth/authorize?state=synthetic', authorize=code),
                {'error': 'Account unavailable', 'stopCalls': 1})
