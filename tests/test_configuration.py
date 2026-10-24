"""Check the public settings module in fresh processes, without real secrets."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest


class ConfigurationTests(unittest.TestCase):
    def load_settings(self, changes):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("MAIPA_") and key != "DJANGO_SETTINGS_MODULE"}
        env.update(changes)
        command = (
            "import json; from backend.maipa import settings as s; "
            "print(json.dumps({'debug':s.DEBUG,'hosts':s.ALLOWED_HOSTS,"
            "'session_secure':s.SESSION_COOKIE_SECURE,'csrf_secure':s.CSRF_COOKIE_SECURE}))"
        )
        return subprocess.run([sys.executable, "-c", command], env=env,
                              cwd=Path(__file__).resolve().parents[1],
                              capture_output=True, text=True, timeout=10)

    def test_production_defaults_require_a_private_key(self):
        result = self.load_settings({"MAIPA_ALLOWED_HOSTS": "maipa.example"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Set MAIPA_SECRET_KEY", result.stderr)

    def test_short_key_is_rejected_without_reflection(self):
        result = self.load_settings({"MAIPA_SECRET_KEY": "synthetic-short-key", "MAIPA_ALLOWED_HOSTS": "maipa.example"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("at least 50", result.stderr)
        self.assertNotIn("synthetic-short-key", result.stderr)

    def test_production_requires_explicit_hosts(self):
        result = self.load_settings({"MAIPA_SECRET_KEY": "synthetic-not-a-real-key-" * 4})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MAIPA_ALLOWED_HOSTS", result.stderr)

    def test_wildcard_hosts_are_rejected(self):
        result = self.load_settings({"MAIPA_SECRET_KEY": "synthetic-not-a-real-key-" * 4,
                                     "MAIPA_ALLOWED_HOSTS": "*"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("wildcard hosts are forbidden", result.stderr)

    def test_production_uses_secure_cookies_and_debug_off(self):
        result = self.load_settings({"MAIPA_SECRET_KEY": "synthetic-not-a-real-key-" * 4,
                                     "MAIPA_ALLOWED_HOSTS": "maipa.example"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"debug": False, "hosts": ["maipa.example"],
                                                    "session_secure": True, "csrf_secure": True})

    def test_explicit_local_mode_uses_loopback_http_only(self):
        result = self.load_settings({"MAIPA_SECRET_KEY": "synthetic-not-a-real-key-" * 4, "MAIPA_LOCAL_DEV": "1"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"debug": False, "hosts": ["localhost", "127.0.0.1", "[::1]"],
                                                    "session_secure": False, "csrf_secure": False})
