"""Actual local-code ACL and concurrent HTTP installation claims."""
from concurrent.futures import ThreadPoolExecutor
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile
import threading

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import Client, TestCase, TransactionTestCase, override_settings

from backend.maipa.workspace.bootstrap import prepare_local_setup
from backend.maipa.workspace.models import BootstrapState, Installation, Membership


class LocalCodeTests(TestCase):
    def test_launcher_creates_private_random_code_once_and_existing_install_gets_no_code(self):
        with tempfile.TemporaryDirectory(prefix='maipa-code-test-') as temporary:
            path = Path(temporary) / 'setup-code'
            with override_settings(LOCAL_SETUP_CODE_FILE=path):
                prepare_local_setup()
                self.assertRegex(path.read_text(), r'^[a-f0-9]{64}$')
                first = path.read_bytes()
                prepare_local_setup()
                self.assertTrue(path.read_bytes() == first)
                if os.name == 'nt':
                    security = ctypes.WinDLL('advapi32', use_last_error=True)
                    security.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
                    security.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
                    size = wintypes.DWORD()
                    security.GetFileSecurityW(str(path), 4, None, 0, ctypes.byref(size))
                    buffer = ctypes.create_string_buffer(size.value)
                    self.assertTrue(security.GetFileSecurityW(str(path), 4, buffer, size, ctypes.byref(size)))
                    sddl = wintypes.LPWSTR()
                    self.assertTrue(security.ConvertSecurityDescriptorToStringSecurityDescriptorW(buffer, 1, 4, ctypes.byref(sddl), None))
                    try:
                        self.assertIn('D:P', sddl.value)
                        self.assertEqual(sddl.value.count('(A;'), 2)
                        self.assertNotIn(';;;WD)', sddl.value)
                        self.assertNotIn(';;;BU)', sddl.value)
                    finally:
                        kernel = ctypes.WinDLL('kernel32')
                        kernel.LocalFree.argtypes = [wintypes.HLOCAL]
                        kernel.LocalFree(ctypes.cast(sddl, wintypes.HLOCAL))
                else:
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                path.unlink()
                Installation.objects.create()
                prepare_local_setup()
                self.assertFalse(path.exists())


class ConcurrentBootstrapTests(TransactionTestCase):
    def test_two_real_connections_can_claim_only_one_admin_and_replay_stays_closed(self):
        BootstrapState.objects.get_or_create(slot=1)
        with tempfile.TemporaryDirectory(prefix='maipa-claim-test-') as temporary:
            path = Path(temporary) / 'setup-code'
            code = 'd7' * 32
            path.write_text(code)
            barrier = threading.Barrier(2)
            def attempt(number, synchronize=True):
                try:
                    client = Client(enforce_csrf_checks=True)
                    token = client.get('/api/session/csrf/', secure=True).json()['csrf_token']
                    if synchronize:
                        barrier.wait(timeout=10)
                    response = client.post('/api/setup/', data=json.dumps({'setup_code': code, 'username': f'claim-{number}',
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
