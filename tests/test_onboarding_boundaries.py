"""Browser-only setup guidance and concurrent HTTP installation claims."""
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
import json
from pathlib import Path
import tempfile
import threading

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connections
from django.test import Client, TestCase, TransactionTestCase, override_settings

from backend.maipa.workspace.models import BootstrapState, Installation, Membership


class LocalSetupCommandTests(TestCase):
    def test_existing_setup_command_guides_browser_without_creating_a_code(self):
        with tempfile.TemporaryDirectory(prefix='maipa-code-test-') as temporary:
            path = Path(temporary) / 'setup-code'
            with override_settings(LOCAL_SETUP_CODE_FILE=path):
                for _ in range(2):
                    output = StringIO()
                    call_command('prepare_local_setup', stdout=output)
                    self.assertIn('Open Maipa in your browser', output.getvalue())
                    self.assertNotIn('code', output.getvalue().lower())
                    self.assertFalse(path.exists())
                    self.assertEqual(get_user_model().objects.count(), 0)
                Installation.objects.create()
                output = StringIO()
                call_command('prepare_local_setup', stdout=output)
                self.assertIn('setup is closed', output.getvalue())
                self.assertFalse(path.exists())


class ConcurrentBootstrapTests(TransactionTestCase):
    def test_two_real_connections_can_claim_only_one_admin_and_replay_stays_closed(self):
        BootstrapState.objects.get_or_create(slot=1)
        with tempfile.TemporaryDirectory(prefix='maipa-claim-test-') as temporary:
            path = Path(temporary) / 'setup-code'
            barrier = threading.Barrier(2)
            def attempt(number, synchronize=True):
                try:
                    client = Client(enforce_csrf_checks=True)
                    token = client.get('/api/session/csrf/', secure=True).json()['csrf_token']
                    if synchronize:
                        barrier.wait(timeout=10)
                    response = client.post('/api/setup/', data=json.dumps({'username': f'claim-{number}',
                        'password': 'synthetic-long-claim!932', 'password_confirmation': 'synthetic-long-claim!932'}),
                        content_type='application/json', secure=True, HTTP_ORIGIN='https://testserver', HTTP_X_CSRFTOKEN=token)
                    return response.status_code
                finally:
                    connections.close_all()
            with override_settings(LOCAL_SETUP_CODE_FILE=path):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [executor.submit(attempt, number) for number in (1, 2)]
                    statuses = [future.result(timeout=20) for future in futures]
                self.assertTrue(all(status in (201, 409, 503) for status in statuses))
                # Shared-memory SQLite can reject both simultaneous upgrades;
                # the next clean request must still yield exactly one winner.
                if 201 not in statuses:
                    self.assertEqual(attempt(3, False), 201)
                self.assertLessEqual(statuses.count(201), 1)
                self.assertEqual(get_user_model().objects.count(), 1)
                self.assertEqual(Membership.objects.filter(role='administrator').count(), 1)
                self.assertTrue(BootstrapState.objects.get(slot=1).closed)
                self.assertEqual(attempt(4, False), 409)
                self.assertFalse(path.exists())
