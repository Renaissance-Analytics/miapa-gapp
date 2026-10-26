"""Install data survives code replacement; all files here are owned synthetic data."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import tempfile
import unittest
import sys
from unittest.mock import patch

from backend import devserver


def descriptor(path):
    """Inspect the actual Windows DACL, independently of the production writer."""
    security = ctypes.WinDLL('advapi32', use_last_error=True)
    security.GetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    security.ConvertSecurityDescriptorToStringSecurityDescriptorW.argtypes = [
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD)]
    size = wintypes.DWORD()
    security.GetFileSecurityW(str(path), 4, None, 0, ctypes.byref(size))
    buffer = ctypes.create_string_buffer(size.value)
    if not security.GetFileSecurityW(str(path), 4, buffer, size, ctypes.byref(size)):
        raise OSError('Synthetic DACL inspection failed')
    value = wintypes.LPWSTR()
    if not security.ConvertSecurityDescriptorToStringSecurityDescriptorW(
            buffer, 1, 4, ctypes.byref(value), None):
        raise OSError('Synthetic DACL conversion failed')
    try:
        return value.value
    finally:
        kernel = ctypes.WinDLL('kernel32')
        kernel.LocalFree.argtypes = [wintypes.HLOCAL]
        kernel.LocalFree(ctypes.cast(value, wintypes.HLOCAL))


class InstallLauncherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='maipa-install-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.data = self.base / 'private-install'
        self.code = self.base / 'version-one'
        self.make_code(self.code)

    def make_code(self, root):
        (root / 'frontend/dist').mkdir(parents=True)
        (root / 'frontend/dist/index.html').write_text('Synthetic build', encoding='utf-8')

    def launch(self, *, data=None, code=None, generate=None, command_hook=None, supplied_key=None):
        environment = {key: value for key, value in os.environ.items()
            if not key.startswith('MAIPA_') and key != 'DJANGO_SETTINGS_MODULE'}
        environment['MAIPA_DATA_DIR'] = str(self.data if data is None else data)
        if supplied_key is not None:
            environment['MAIPA_SECRET_KEY'] = supplied_key
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(devserver, '__file__', str((code or self.code) / 'backend/devserver.py')), \
                patch('sys.argv', ['devserver', '4131']), \
                patch.object(sys, 'path', list(sys.path)), \
                patch('django.core.management.execute_from_command_line') as commands, \
                patch('backend.devserver.secrets.token_urlsafe', side_effect=generate or (lambda _: 'synthetic-key-' * 6)):
            commands.side_effect = command_hook
            devserver.main()
            return commands.call_args_list, os.environ['MAIPA_SECRET_KEY']

    def assert_private(self, path, *, directory=False):
        if os.name == 'nt':
            acl = descriptor(path)
            self.assertIn('D:P', acl)
            self.assertEqual(acl.count('(A;'), 2)
            self.assertNotIn(';;;WD)', acl)
            self.assertNotIn(';;;BU)', acl)
            if directory:
                self.assertEqual(acl.count('OICI'), 2)
        else:
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if directory else 0o600)

    def test_configured_install_is_outside_code_and_private_before_key_generation(self):
        observations = []
        def generate(_):
            self.assert_private(self.data, directory=True)
            # The root protects inheritance before any private bytes exist.
            self.assertEqual(list(self.data.glob('**/*')), [])
            observations.append(True)
            return 'synthetic-key-' * 6
        calls, key = self.launch(generate=generate)
        self.assertEqual(observations, [True])
        self.assertEqual(key, 'synthetic-key-' * 6)
        self.assertFalse((self.code / 'runtime').exists())
        self.assert_private(self.data / 'development-key')
        self.assertIn('migrate', calls[0].args[0])

    def test_code_replacement_reuses_private_key_and_install_data(self):
        self.launch()
        sentinel = self.data / 'synthetic-upgrade-marker'
        sentinel.write_text('retained synthetic state', encoding='utf-8')
        newer = self.base / 'version-two'
        self.make_code(newer)
        def forbidden(_):
            self.fail('Code upgrade must not create a replacement key')
        _, key = self.launch(code=newer, generate=forbidden)
        self.assertEqual(key, 'synthetic-key-' * 6)
        self.assertEqual(sentinel.read_text(), 'retained synthetic state')
        self.assertFalse((newer / 'runtime').exists())

    def test_relative_or_source_nested_data_path_refuses_without_creating_keys(self):
        for path in (Path('relative-install'), self.code, self.code / 'runtime'):
            with self.subTest(kind='relative' if not path.is_absolute() else 'source'), \
                    self.assertRaises(SystemExit):
                self.launch(data=path)
        self.assertFalse((self.code / 'runtime').exists())
        self.assertFalse((self.code / 'development-key').exists())

    def test_existing_legacy_data_does_not_silently_boot_a_different_install(self):
        legacy = self.code / 'runtime'
        legacy.mkdir()
        database = legacy / 'maipa.sqlite3'
        database.write_bytes(b'synthetic old database placeholder')
        with self.assertRaises(SystemExit):
            self.launch()
        self.assertEqual(database.read_bytes(), b'synthetic old database placeholder')
        self.assertFalse(self.data.exists())

    def test_failed_key_generation_never_leaves_a_usable_partial_key(self):
        def broken(_):
            raise OSError('Synthetic entropy unavailable')
        with self.assertRaises((SystemExit, OSError)):
            self.launch(generate=broken)
        self.assertFalse((self.data / 'development-key').exists())
        self.assertFalse((self.code / 'runtime/development-key').exists())

    def test_linked_install_path_cannot_redirect_secret_creation(self):
        target = self.base / 'unrelated-synthetic-folder'
        target.mkdir()
        if os.name == 'nt':
            import subprocess
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(self.data), str(target)],
                capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0)
        else:
            self.data.symlink_to(target, target_is_directory=True)
        self.addCleanup(lambda: os.rmdir(self.data) if os.name == 'nt' else self.data.unlink())
        with self.assertRaises(SystemExit):
            self.launch()
        self.assertEqual(list(target.iterdir()), [])

    def test_database_is_private_before_migrations_open_it(self):
        observed = []
        def check(_):
            database = self.data / 'maipa.sqlite3'
            self.assertTrue(database.is_file())
            self.assert_private(database)
            observed.append(True)
        self.launch(command_hook=check)
        self.assertEqual(observed, [True, True])

    def test_existing_database_and_setup_permissions_are_tightened_before_django(self):
        self.data.mkdir()
        database = self.data / 'maipa.sqlite3'
        code = self.data / 'local-setup-code'
        database.write_bytes(b'synthetic retained database')
        code.write_text('a9' * 32)
        observed = []
        def check(_):
            self.assert_private(database)
            self.assert_private(code)
            observed.append(True)
        self.launch(command_hook=check)
        self.assertEqual(observed, [True, True])
        self.assertEqual(database.read_bytes(), b'synthetic retained database')
        self.assertEqual(code.read_text(), 'a9' * 32)

    def test_hardlinked_database_refuses_without_changing_target_bytes_or_permissions(self):
        self.data.mkdir()
        target = self.base / 'foreign-synthetic-database'
        target.write_bytes(b'foreign synthetic data')
        before = descriptor(target) if os.name == 'nt' else target.stat().st_mode
        (self.data / 'maipa.sqlite3').hardlink_to(target)
        with self.assertRaises(SystemExit):
            self.launch()
        self.assertEqual(target.read_bytes(), b'foreign synthetic data')
        after = descriptor(target) if os.name == 'nt' else target.stat().st_mode
        self.assertEqual(after, before)

    def test_malformed_existing_key_refuses_without_replacement(self):
        self.data.mkdir()
        key = self.data / 'development-key'
        key.write_text('synthetic-invalid-short-key')
        with self.assertRaises(SystemExit):
            self.launch()
        self.assertEqual(key.read_text(), 'synthetic-invalid-short-key')

    def test_supplied_signing_key_is_retained_privately_and_reused_without_entropy(self):
        value = 'synthetic-provided-Django-key!@#$%^&*()_+=' * 2
        def forbidden(_):
            self.fail('An explicit key must not consume replacement entropy')
        _, actual = self.launch(supplied_key=value, generate=forbidden)
        self.assertEqual(actual, value)
        self.assertEqual((self.data / 'development-key').read_text(encoding='utf-8'), value)
        self.assert_private(self.data / 'development-key')
        _, after = self.launch(generate=forbidden)
        self.assertEqual(after, value)

    def test_supplied_key_mismatch_refuses_before_django_without_replacing_saved_key(self):
        self.launch()
        before = (self.data / 'development-key').read_bytes()
        calls = []
        with self.assertRaises(SystemExit):
            self.launch(supplied_key='different-synthetic-signing-key-' * 3,
                command_hook=lambda args: calls.append(args))
        self.assertEqual(calls, [])
        self.assertEqual((self.data / 'development-key').read_bytes(), before)

    def test_short_or_control_character_supplied_keys_cannot_create_an_install(self):
        calls = []
        for value in ('short-synthetic-key', 'synthetic-key-' * 6 + '\ncontrol'):
            with self.subTest(kind='invalid supplied key'), self.assertRaises(SystemExit):
                self.launch(supplied_key=value, command_hook=lambda args: calls.append(args))
        self.assertEqual(calls, [])
        self.assertFalse((self.data / 'development-key').exists())

    def test_missing_key_of_an_initialized_install_refuses_without_regeneration(self):
        self.launch()
        self.assertTrue((self.data / 'installation.json').is_file())
        (self.data / 'development-key').unlink()
        generated = []
        def replacement(_):
            generated.append(True)
            return 'synthetic-replacement-key-' * 4
        with self.assertRaises(SystemExit):
            self.launch(generate=replacement)
        self.assertEqual(generated, [])
        self.assertFalse((self.data / 'development-key').exists())


class InstallConfigurationTests(unittest.TestCase):
    def load(self, extra=None):
        import subprocess
        allowed = {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC', 'PATHEXT'}
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        env.update(MAIPA_SECRET_KEY='synthetic-only-configuration-key-' * 3,
            MAIPA_ALLOWED_HOSTS='maipa.example')
        env.update(extra or {})
        source = ('import json; from backend.maipa import settings; '
            'print(json.dumps({"data":str(settings.DATA_ROOT),"database":str(settings.DATABASES["default"]["NAME"])}))')
        return subprocess.run([sys.executable, '-c', source], cwd=Path(__file__).resolve().parents[1],
            env=env, capture_output=True, text=True, timeout=15)

    def test_scrubbed_environment_resolves_the_os_owned_default_without_writing_it(self):
        import json
        if os.name == 'nt':
            buffer = ctypes.create_unicode_buffer(32768)
            shell = ctypes.WinDLL('shell32')
            shell.SHGetFolderPathW.argtypes = [wintypes.HWND, ctypes.c_int,
                wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR]
            self.assertEqual(shell.SHGetFolderPathW(None, 0x1c, None, 0, buffer), 0)
            expected = Path(buffer.value) / 'Maipa/data'
        else:
            expected = Path.home() / '.local/share/maipa'
        before = expected.exists()
        result = self.load()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'data': str(expected), 'database': str(expected / 'maipa.sqlite3')})
        self.assertEqual(expected.exists(), before)

    def test_settings_import_does_not_create_a_configured_data_directory(self):
        import json
        with tempfile.TemporaryDirectory(prefix='maipa-config-storage-test-') as temporary:
            root = Path(temporary).resolve() / 'not-created-by-import'
            result = self.load({'MAIPA_DATA_DIR': str(root)})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Path(json.loads(result.stdout)['data']), root)
            self.assertFalse(root.exists())
