"""Finite browser acceptance fixture: synthetic SQLite/session, loopback only, no real profiles."""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from wsgiref.simple_server import make_server, WSGIRequestHandler
from wsgiref.simple_server import WSGIServer
from socketserver import ThreadingMixIn

ROOT = Path(__file__).resolve().parents[1]
BROWSER_RUN_SECONDS = 120
BROWSER_PROFILE_RUNS = 3  # First administrator, ordinary session, fresh-profile restore.
BROWSER_FIXTURE_OVERHEAD_SECONDS = 60
BROWSER_PHASES = {"fixture_input", "browser_binary", "profile_parent", "browser_launch", "debug_endpoint", "service_storage", "ui_composition", "admin_users", "client_catalog", "agent_shell",
                  "anonymous", "release_controls", "theme", "keyboard_divider", "mobile_panels",
                  "login", "logout_viewer", "authenticated_scope", "transport_csrf", "intake_recovery", "intake_facts", "intake_history", "intake_scope", "authority", "first_run", "setup_chat", "setup_isolation", "work_views", "layout", "layout_resume", "preferences", "browser_exceptions", "cleanup"}
BROWSER_SETUP_CHECKPOINTS = {'setup_mobile', 'setup_display_name', 'setup_workspace', 'setup_resume',
    'setup_engagement', 'setup_entity', 'setup_currency', 'setup_reporting_basis', 'setup_effective_from',
    'setup_review', 'setup_confirm', 'setup_stale', 'setup_open_facts'}
BROWSER_AUTH_CHECKPOINTS = {'logout_click', 'logout_form', 'logout_session', 'logout_notes',
    'viewer_username', 'viewer_password', 'viewer_submit', 'viewer_workspace', 'viewer_notes',
    'viewer_scope', 'viewer_assertions'}
BROWSER_LOGIN_CHECKPOINTS = {'login_viewport', 'login_username', 'login_password', 'login_submit',
    'login_workspace', 'login_notes', 'login_wrong_username', 'login_wrong_password',
    'login_wrong_submit', 'login_wrong_response', 'login_password_cleared', 'login_reload',
    'login_form', 'login_scope', 'login_assertions'}


class BrowserFailure(Exception):
    def __init__(self, evidence):
        self.evidence = evidence


def browser_failure_evidence(value):
    valid = isinstance(value, dict) and isinstance(value.get("reason"), str) and value["reason"] in BROWSER_PHASES
    result = {"checks": 0, "reason": value["reason"] if valid else "browser_process",
              "cleanup": valid and value.get("cleanup") is True}
    if valid and value['reason'] in ('admin_users', 'work_views', 'agent_shell', 'intake_facts'):
        if value.get('failure_kind') in ('timeout', 'assertion', 'evaluation', 'protocol', 'other'):
            result['failure_kind'] = value['failure_kind']
        responses = value.get('surface_responses')
        if isinstance(responses, dict):
            responses = {key: responses[key] for key in ('session', 'chat', 'admin', 'intake')
                         if type(responses.get(key)) is int and 100 <= responses[key] <= 599}
            if responses:
                result['surface_responses'] = responses
        ui = value.get('surface_ui')
        if isinstance(ui, dict):
            ui = {key: ui[key] for key in ('admin_panel', 'admin_ready', 'admin_fields',
                  'conversation', 'contextual_panel', 'chat_alert', 'composer_nonempty', 'composer_focused',
                  'send_present', 'send_disabled', 'ui_phase_ready', 'intake_form', 'intake_valid',
                  'intake_reference', 'intake_locator', 'intake_alert', 'intake_save_disabled',
                  'intake_revision_one') if type(ui.get(key)) is bool}
            if ui:
                result['surface_ui'] = ui
    if valid and value['reason'] == 'setup_chat' and isinstance(value.get('checkpoint'), str):
        if value['checkpoint'] in BROWSER_SETUP_CHECKPOINTS:
            result['checkpoint'] = value['checkpoint']
    if valid and value['reason'] == 'agent_shell' and isinstance(value.get('agent_state'), dict):
        state = value['agent_state']
        safe = {}
        if state.get('phase') in ('offline', 'connecting', 'sign_in', 'ready', 'thinking', 'stopping', 'unavailable'):
            safe['phase'] = state['phase']
        if type(state.get('interruption_received')) is bool:
            safe['interruption_received'] = state['interruption_received']
        if safe:
            result['agent_state'] = safe
    if valid and value['reason'] in ('login', 'logout_viewer'):
        checkpoint = value.get('checkpoint')
        checkpoints = BROWSER_LOGIN_CHECKPOINTS if value['reason'] == 'login' else BROWSER_AUTH_CHECKPOINTS
        if isinstance(checkpoint, str) and checkpoint in checkpoints:
            result['checkpoint'] = checkpoint
        if value.get('failure_kind') in ('timeout', 'assertion', 'evaluation', 'protocol', 'other'):
            result['failure_kind'] = value['failure_kind']
        responses = value.get('auth_responses')
        if isinstance(responses, dict):
            responses = {key: responses[key] for key in ('session', 'login', 'logout', 'workspace')
                         if type(responses.get(key)) is int and 100 <= responses[key] <= 599}
            if responses:
                result['auth_responses'] = responses
        ui = value.get('auth_ui')
        if isinstance(ui, dict):
            ui = {key: ui[key] for key in ('login_form', 'sign_out', 'sign_out_pending',
                  'sign_out_failed', 'release_notes') if type(ui.get(key)) is bool}
            if ui:
                result['auth_ui'] = ui
    if valid and value["reason"] == "browser_launch":
        if value.get("startup_state") in ("starting", "spawn_error", "exited", "devtools_file"):
            result["startup_state"] = value["startup_state"]
        code = value.get("launch_exit")
        if type(code) is int and -(2 ** 31) <= code < 2 ** 32:
            result["launch_exit"] = code
        if value.get("launch_error") in ("ENOENT", "EACCES", "EPERM", "EINVAL", "ENOMEM", "UNKNOWN"):
            result["launch_error"] = value["launch_error"]
    return result


def browser_environment(ambient, windows_paths):
    # Only OS startup paths cross into the browser helper. Its fresh profile
    # and synthetic login travel through the existing owned fixture protocol.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"}
    result = {key.upper(): value for key, value in ambient.items() if key.upper() in allowed}
    allowed_paths = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}
    result.update({key.upper(): value for key, value in windows_paths.items() if key.upper() in allowed_paths})
    return result


def save_result(value):
    target = ROOT / "artifacts/ci/browser-result.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value), encoding="utf-8")


def save_progress(stage):
    if type(stage) is not str or stage not in ('preparation', 'migrations', 'first_run', 'ordinary', 'resume', 'cleanup', 'complete'):
        raise ValueError('Invalid fixture progress')
    target = ROOT / 'artifacts/ci/browser-runner-progress.json'
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({'stage': stage}), encoding='utf-8')
    except OSError:
        pass  # Optional progress must never prevent native/fixture cleanup.


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def browser_process(data, node, environment):
    process = subprocess.Popen([node, "tools/browser.mjs"], cwd=ROOT,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
    try:
        stdout, stderr = process.communicate(json.dumps(data), timeout=BROWSER_RUN_SECONDS)
    except subprocess.TimeoutExpired as expired:
        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired as remaining:
            stdout = remaining.stdout if remaining.stdout is not None else expired.stdout
            stderr = remaining.stderr if remaining.stderr is not None else expired.stderr
        def captured(value):
            return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''
        stdout, stderr = captured(stdout), captured(stderr)
        artifact = ROOT / 'artifacts/ci/browser-failure.log'
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(stdout + '\n' + stderr, encoding='utf-8')
        try:
            failure = json.loads(stdout)
        except ValueError:
            failure = None
        evidence = browser_failure_evidence(failure)
        evidence['cleanup'] = False  # A timed-out child cannot attest its own completed cleanup.
        raise BrowserFailure(evidence)
    if process.returncode:
        artifact = ROOT / "artifacts/ci/browser-failure.log"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(stderr, encoding="utf-8")
        try:
            failure = json.loads(stdout)
        except ValueError:
            failure = None
        raise BrowserFailure(browser_failure_evidence(failure))
    return json.loads(stdout)


class ThreadedFixtureServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self._request_threads, self._request_lock = set(), threading.Lock()
        self._request_cleanup_failed = False
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        # Register before start so shutdown cannot miss an allocated request.
        worker = threading.Thread(target=self.process_request_thread, args=(request, client_address), daemon=True)
        with self._request_lock:
            self._request_threads.add(worker)
        try:
            worker.start()
        except Exception:
            with self._request_lock:
                self._request_threads.discard(worker)
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            # Django connections are thread-local: the fixture main thread
            # cannot close a streaming request's SQLite handle for it.
            from django.db import connections
            try:
                connections.close_all()
            except Exception:
                with self._request_lock:
                    self._request_cleanup_failed = True
            finally:
                with self._request_lock:
                    self._request_threads.discard(threading.current_thread())

    def wait_for_requests(self, *, deadline):
        with self._request_lock:
            workers = list(self._request_threads)
        for worker in workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        with self._request_lock:
            return not self._request_threads and not self._request_cleanup_failed


def main():
    save_progress('preparation')
    (ROOT / "artifacts/ci/browser-failure.log").unlink(missing_ok=True)
    # This module is invoked in a dedicated child, never in the running Genie app.
    if os.environ.get("DJANGO_SETTINGS_MODULE"):
        raise ValueError("Dedicated fixture process required")
    with tempfile.TemporaryDirectory(prefix="maipa-browser-fixture-") as temporary:
        fixture_root = Path(temporary).resolve()
        data_root = fixture_root / 'install'
        os.environ.update(MAIPA_LOCAL_DEV="1", MAIPA_SECRET_KEY=secrets.token_urlsafe(72),
                          MAIPA_DATA_DIR=str(data_root),
                          MAIPA_ALLOWED_HOSTS="127.0.0.1", DJANGO_SETTINGS_MODULE="backend.maipa.settings")
        from backend.maipa.storage import prepare_directory, prepare_files, write_private
        prepare_directory(data_root)
        write_private(data_root / 'development-key', os.environ['MAIPA_SECRET_KEY'].encode('ascii'))
        prepare_files(data_root)
        import django
        from django.conf import settings
        if (settings.DATA_ROOT != data_root or settings.DATABASES['default']['NAME'] != data_root / 'maipa.sqlite3'
                or settings.ACP_CHECK_ROOT != data_root / 'workers'):
            raise ValueError('Synthetic installation storage binding')
        django.setup()
        settings.ACP_PROVIDER_LABELS = {'independent-acp': 'Independent ACP'}
        # Only this dedicated synthetic fixture substitutes the provider process.
        # All HTTP, transcript, session authority and UI tool handling remain real.
        from types import SimpleNamespace
        from unittest.mock import patch
        from backend.maipa.acp import Scope
        from backend.maipa.agent_accounts import chat_runtime
        def synthetic_prepare(worker):
            worker.check()
            workspace = fixture_root / ('chat-' + str(worker.identity))
            workspace.mkdir(exist_ok=True)
            scope = Scope(str(worker.owner), str(worker.account_id))
            worker.lease = SimpleNamespace(scope=scope, revalidate=lambda: None)
            capsule = SimpleNamespace(command=(sys.executable, str(ROOT / 'tests/fixtures/agent_chat_peer.py')),
                workspace=workspace, profile={'user_id': scope.user_id, 'account_id': scope.account_id,
                    'provider': worker.provider, 'workspace': str(workspace), 'synthetic_models': True,
                    'synthetic_private_login': worker.provider == 'claude'})
            return capsule, None
        synthetic = patch.object(chat_runtime._Worker, 'prepare', synthetic_prepare)
        synthetic.start()
        from django.core.management import call_command
        from django.contrib.auth import get_user_model
        from backend.maipa.agent_accounts.models import AgentAccount
        from backend.maipa.workspace.models import Installation, Membership
        from django.core.wsgi import get_wsgi_application
        from django.db import connections
        with open(os.devnull, "w") as silent:
            save_progress('migrations')
            call_command("migrate", interactive=False, verbosity=0, stdout=silent)
        if (data_root / 'local-setup-code').exists():
            raise ValueError('First-run fixture must not require a setup-code file')
        application = get_wsgi_application()
        failed_initial_intake = False
        enable_intake_failure = False
        seeded_authority = False

        def first_intake_failure(environ, start_response):
            # One deliberate failure in this isolated fixture only. Every retry
            # reaches actual Django/auth/database; no success response is faked.
            nonlocal failed_initial_intake, seeded_authority
            if (enable_intake_failure and not seeded_authority and environ.get('REQUEST_METHOD') == 'GET'
                    and environ.get('PATH_INFO', '').startswith('/api/policies/entities/')):
                # Trusted operator fixture record only, never a production route.
                # The response still comes from actual Django/auth/database policy.
                from datetime import timedelta
                from django.utils import timezone
                from backend.maipa.intake.models import Entity
                from backend.maipa.policies.authority import provision_root
                entity = Entity.objects.filter(name='Synthetic browser entity', engagement__owner=owner).first()
                if entity is not None and environ['PATH_INFO'] == f'/api/policies/entities/{entity.pk}/':
                    now = timezone.now()
                    provision_root(entity_id=entity.pk, subject_id=owner.pk, action='ledger.post', duty='approver',
                        starts_at=now-timedelta(minutes=5), expires_at=now+timedelta(days=1),
                        limit='500.0000', currency='USD', evidence='synthetic-browser-authority')
                    seeded_authority = True
            if (enable_intake_failure and not failed_initial_intake and environ.get("PATH_INFO") == "/api/intake/engagements/"
                    and environ.get("REQUEST_METHOD") == "GET"):
                failed_initial_intake = True
                payload = b'{"error":{"code":"intake_unavailable"}}'
                start_response("503 Service Unavailable", [("Content-Type", "application/json"),
                    ("Cache-Control", "no-store"), ("Content-Length", str(len(payload)))])
                return [payload]
            result = application(environ, start_response)
            if enable_intake_failure and environ.get('PATH_INFO') == '/api/agent/chat/stream/':
                def interrupted_stream():
                    from backend.maipa.agent_accounts.models import Conversation
                    try:
                        for chunk in result:
                            failed = Conversation.objects.filter(account=owned, phase='unavailable', worker__isnull=True).first()
                            last_user = next((m for m in reversed(failed.messages) if m['role'] == 'user'), None) if failed else None
                            # Each subscriber to this failed synthetic request
                            # must see the break. A stale stream from before a
                            # reload cannot consume a single global injection.
                            if last_user and last_user['text'] == 'Simulate connection interruption':
                                yield b'event: revoked\ndata: {}\n\n'
                                return
                            yield chunk
                    finally:
                        result.close()
                return interrupted_stream()
            return result

        server = make_server("127.0.0.1", 0, first_intake_failure, server_class=ThreadedFixtureServer, handler_class=QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            manifest = json.loads((ROOT / "frontend/dist/build-info.json").read_text(encoding="utf-8"))
            base_data = {"origin": f"http://127.0.0.1:{server.server_port}", "profile_root": str(fixture_root), "build_id": manifest["build_id"],
                         "release_label": manifest["release_label"]}
            node = shutil.which("node")
            if not node:
                raise ValueError("Node prerequisite")
            from backend.maipa.windows_sandbox import _Api, _windows_paths
            environment = browser_environment(os.environ, _windows_paths(_Api()))
            def run_browser(data):
                return browser_process(data, node, environment)
            save_progress('first_run')
            first_run = run_browser(base_data | {'first_run': True,
                'username': 'synthetic-first-admin', 'password': secrets.token_urlsafe(24)})
            if (first_run.get('cleanup') is not True or first_run.get('checks') != 9
                    or first_run.get('scenarios') != ['local-first-run-admin', 'account-chat-resume-and-confirm',
                        'client-catalog-create-rename-conflict', 'admin-users-create-update-conflict', 'first-run-stays-closed', 'admin-users-restricted-login', 'client-catalog-restricted-scope',
                        'no-uncaught-exceptions', 'owned-browser-cleanup']):
                raise ValueError('First-run browser cleanup required')
            if (data_root / 'local-setup-code').exists():
                raise ValueError('Browser onboarding must not generate a setup-code file')
            password = secrets.token_urlsafe(24)
            owner = get_user_model().objects.create_user(username='synthetic-browser-owner', password=password)
            other = get_user_model().objects.create_user(username='synthetic-browser-other', password=password)
            installation = Installation.objects.get()
            Membership.objects.create(installation=installation, user=owner, role='preparer')
            Membership.objects.create(installation=installation, user=other, role='viewer')
            owned = AgentAccount.objects.create(owner=owner, provider='codex', label='Synthetic owned account')
            foreign = AgentAccount.objects.create(owner=other, provider='claude', label='Synthetic foreign account')
            from backend.maipa.agent_accounts.references import record_session, store_artifact, read_artifact
            reference = record_session(owner, str(owned.pk), 'synthetic-browser-provider-session')
            artifact = store_artifact(owner, str(owned.pk), reference, b'synthetic browser resource payload\x00')
            assert read_artifact(owner, str(owned.pk), reference, artifact.artifact_id) == b'synthetic browser resource payload\x00'
            # Trusted synthetic session setup, not a provider readiness claim.
            from backend.maipa.agent_accounts.ui import open_ui_lease
            ui_lease = open_ui_lease(owner, str(owned.pk), reference)
            enable_intake_failure = True
            save_progress('ordinary')
            evidence = run_browser(base_data | {'username': owner.username, 'viewer_username': other.username,
                'password': password, 'user_id': str(owner.pk), 'account_id': str(owned.pk), 'foreign_account_id': str(foreign.pk),
                'service_reference': reference, 'artifact_reference': artifact.artifact_id, 'ui_lease': ui_lease})
            if evidence.get('cleanup') is not True or evidence.get('scenarios', [])[-2:] != ['no-uncaught-exceptions', 'owned-browser-cleanup']:
                raise ValueError('Ordinary browser exceptions and cleanup required')
            save_progress('resume')
            resumed = run_browser(base_data | {'layout_resume': True, 'username': owner.username,
                'password': password, 'user_id': str(owner.pk), 'expected_points': 3500})
            if (resumed.get('cleanup') is not True or resumed.get('checks') != 4
                    or resumed.get('scenarios') != ['layout-fresh-browser-restore', 'preferences-fresh-browser-restore', 'no-uncaught-exceptions', 'owned-browser-cleanup']
                    or resumed.get('build_id') != evidence.get('build_id') or resumed.get('browser') != evidence.get('browser')):
                raise ValueError('Fresh-profile layout acceptance and cleanup required')
            if first_run.get('build_id') != evidence.get('build_id') or first_run.get('browser') != evidence.get('browser'):
                raise ValueError('Browser fixture identity mismatch')
            # Both processes independently prove exceptions/cleanup. Report those
            # shared aggregate cases at the end with the ordinary fixture, while
            # retaining actual first-run and ordinary case order and every case.
            first_only = [case for case in first_run['scenarios'] if case not in evidence['scenarios']]
            evidence['scenarios'] = first_only + evidence['scenarios'][:-2] + resumed['scenarios']
            evidence['checks'] = len(evidence['scenarios'])
        finally:
            save_progress('cleanup')
            with chat_runtime._lock:
                workers = list(chat_runtime._workers.values())
                for worker in workers:
                    worker.stopping.set()
            for worker in workers:
                worker.thread.join(timeout=15)
                if worker.thread.is_alive():
                    raise ValueError('Synthetic agent worker cleanup failed')
            synthetic.stop()
            server.shutdown()
            server.server_close()
            deadline = time.monotonic() + 5
            thread.join(timeout=max(0, deadline - time.monotonic()))
            if not server.wait_for_requests(deadline=deadline):
                raise ValueError('Fixture request cleanup failed')
            connections.close_all()
        if thread.is_alive():
            raise ValueError("Fixture cleanup failed")
    # Success includes deletion of the temporary install, not just Chrome exit.
    save_result(evidence)
    save_progress('complete')
    print(json.dumps(evidence))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # This dedicated synthetic fixture's raw failure remains local. Public
        # CI evidence below contains only the closed failure allowlist.
        try:
            (ROOT / 'artifacts/ci/browser-runner-failure.log').write_text(traceback.format_exc(), encoding='utf-8')
        except OSError:
            pass
        evidence = error.evidence if isinstance(error, BrowserFailure) else browser_failure_evidence(None)
        save_result(evidence)
        print(json.dumps(evidence))
        raise SystemExit(1)
