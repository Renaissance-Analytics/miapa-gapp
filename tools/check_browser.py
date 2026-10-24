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
BROWSER_PHASES = {"fixture_input", "browser_binary", "profile_parent", "browser_launch", "debug_endpoint",
                  "anonymous", "release_controls", "theme", "keyboard_divider", "mobile_panels",
                  "login", "logout_viewer", "authenticated_scope", "transport_csrf", "intake_facts", "intake_history", "intake_scope", "browser_exceptions", "cleanup"}


class BrowserFailure(Exception):
    def __init__(self, evidence):
        self.evidence = evidence


def browser_failure_evidence(value):
    valid = isinstance(value, dict) and isinstance(value.get("reason"), str) and value["reason"] in BROWSER_PHASES
    result = {"checks": 0, "reason": value["reason"] if valid else "browser_process",
              "cleanup": valid and value.get("cleanup") is True}
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
        os.environ.update(MAIPA_LOCAL_DEV="1", MAIPA_SECRET_KEY=secrets.token_urlsafe(72),
                          MAIPA_ALLOWED_HOSTS="127.0.0.1", DJANGO_SETTINGS_MODULE="backend.maipa.settings")
        import django
        from django.conf import settings
        settings.DATABASES["default"]["NAME"] = fixture_root / "fixture.sqlite3"
        settings.ACP_CHECK_ROOT = fixture_root / "acp"
        django.setup()
        from django.core.management import call_command
        from django.contrib.auth import get_user_model
        from backend.maipa.agent_accounts.models import AgentAccount
        from backend.maipa.workspace.models import Installation, Membership
        from django.core.wsgi import get_wsgi_application
        from django.db import connections
        with open(os.devnull, "w") as silent:
            call_command("migrate", interactive=False, verbosity=0, stdout=silent)
        password = secrets.token_urlsafe(24)
        owner = get_user_model().objects.create_user(username="synthetic-browser-owner", password=password)
        other = get_user_model().objects.create_user(username="synthetic-browser-other", password=password)
        installation = Installation.objects.create()
        Membership.objects.create(installation=installation, user=owner, role="preparer")
        Membership.objects.create(installation=installation, user=other, role="viewer")
        owned = AgentAccount.objects.create(owner=owner, provider="codex", label="Synthetic owned account")
        foreign = AgentAccount.objects.create(owner=other, provider="claude", label="Synthetic foreign account")
        server = make_server("127.0.0.1", 0, get_wsgi_application(), handler_class=QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            manifest = json.loads((ROOT / "frontend/dist/build-info.json").read_text(encoding="utf-8"))
            data = {"origin": f"http://127.0.0.1:{server.server_port}", "profile_root": str(fixture_root), "build_id": manifest["build_id"],
                    "release_label": manifest["release_label"], "username": owner.username, "viewer_username": other.username,
                    "password": password, "user_id": str(owner.pk),
                    "account_id": str(owned.pk), "foreign_account_id": str(foreign.pk)}
            node = shutil.which("node")
            if not node:
                raise ValueError("Node prerequisite")
            from backend.maipa.windows_sandbox import _Api, _windows_paths
            environment = browser_environment(os.environ, _windows_paths(_Api()))
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
            evidence = json.loads(stdout)
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
