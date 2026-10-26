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
from wsgiref.simple_server import make_server, WSGIRequestHandler

ROOT = Path(__file__).resolve().parents[1]
BROWSER_PHASES = {"fixture_input", "browser_binary", "profile_parent", "browser_launch", "debug_endpoint", "service_storage", "admin_users", "client_catalog",
                  "anonymous", "release_controls", "theme", "keyboard_divider", "mobile_panels",
                  "login", "logout_viewer", "authenticated_scope", "transport_csrf", "intake_recovery", "intake_facts", "intake_history", "intake_scope", "authority", "first_run", "setup_chat", "setup_isolation", "work_views", "layout", "layout_resume", "preferences", "browser_exceptions", "cleanup"}
BROWSER_SETUP_CHECKPOINTS = {'setup_mobile', 'setup_display_name', 'setup_workspace', 'setup_resume',
    'setup_engagement', 'setup_entity', 'setup_currency', 'setup_reporting_basis', 'setup_effective_from',
    'setup_review', 'setup_confirm', 'setup_stale', 'setup_open_facts'}
BROWSER_AUTH_CHECKPOINTS = {'logout_click', 'logout_form', 'logout_session', 'logout_notes',
    'viewer_username', 'viewer_password', 'viewer_submit', 'viewer_workspace', 'viewer_notes',
    'viewer_scope', 'viewer_assertions'}


class BrowserFailure(Exception):
    def __init__(self, evidence):
        self.evidence = evidence


def browser_failure_evidence(value):
    valid = isinstance(value, dict) and isinstance(value.get("reason"), str) and value["reason"] in BROWSER_PHASES
    result = {"checks": 0, "reason": value["reason"] if valid else "browser_process",
              "cleanup": valid and value.get("cleanup") is True}
    if valid and value['reason'] == 'setup_chat' and isinstance(value.get('checkpoint'), str):
        if value['checkpoint'] in BROWSER_SETUP_CHECKPOINTS:
            result['checkpoint'] = value['checkpoint']
    if valid and value['reason'] == 'logout_viewer':
        checkpoint = value.get('checkpoint')
        if isinstance(checkpoint, str) and checkpoint in BROWSER_AUTH_CHECKPOINTS:
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


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def main():
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
        from django.core.management import call_command
        from django.contrib.auth import get_user_model
        from backend.maipa.agent_accounts.models import AgentAccount
        from backend.maipa.workspace.models import Installation, Membership
        from django.core.wsgi import get_wsgi_application
        from django.db import connections
        with open(os.devnull, "w") as silent:
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
            return application(environ, start_response)

        server = make_server("127.0.0.1", 0, first_intake_failure, handler_class=QuietHandler)
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
                process = subprocess.Popen([node, "tools/browser.mjs"], cwd=ROOT,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=environment)
                try:
                    stdout, stderr = process.communicate(json.dumps(data), timeout=120)
                except subprocess.TimeoutExpired:
                    subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
                    process.wait(timeout=10)
                    raise ValueError("Owned browser fixture deadline")
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
            enable_intake_failure = True
            evidence = run_browser(base_data | {'username': owner.username, 'viewer_username': other.username,
                'password': password, 'user_id': str(owner.pk), 'account_id': str(owned.pk), 'foreign_account_id': str(foreign.pk),
                'service_reference': reference, 'artifact_reference': artifact.artifact_id})
            if evidence.get('cleanup') is not True or evidence.get('scenarios', [])[-2:] != ['no-uncaught-exceptions', 'owned-browser-cleanup']:
                raise ValueError('Ordinary browser exceptions and cleanup required')
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
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            connections.close_all()
        if thread.is_alive():
            raise ValueError("Fixture cleanup failed")
        save_result(evidence)
        print(json.dumps(evidence))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        evidence = error.evidence if isinstance(error, BrowserFailure) else browser_failure_evidence(None)
        save_result(evidence)
        print(json.dumps(evidence))
        raise SystemExit(1)
