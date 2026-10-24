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
        from django.test import Client
        from backend.maipa.agent_accounts.models import AgentAccount
        from django.core.wsgi import get_wsgi_application
        from django.db import connections
        with open(os.devnull, "w") as silent:
            call_command("migrate", interactive=False, verbosity=0, stdout=silent)
        owner = get_user_model().objects.create_user(username="synthetic-browser-owner")
        other = get_user_model().objects.create_user(username="synthetic-browser-other")
        owned = AgentAccount.objects.create(owner=owner, provider="codex", label="Synthetic owned account")
        foreign = AgentAccount.objects.create(owner=other, provider="claude", label="Synthetic foreign account")
        client = Client()
        client.force_login(owner)
        cookie = client.cookies[settings.SESSION_COOKIE_NAME].value
        server = make_server("127.0.0.1", 0, get_wsgi_application(), handler_class=QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            manifest = json.loads((ROOT / "frontend/dist/build-info.json").read_text(encoding="utf-8"))
            data = {"origin": f"http://127.0.0.1:{server.server_port}", "profile_root": str(fixture_root), "build_id": manifest["build_id"],
                    "release_label": manifest["release_label"], "cookie": cookie, "user_id": str(owner.pk),
                    "account_id": str(owned.pk), "foreign_account_id": str(foreign.pk)}
            node = shutil.which("node")
            if not node:
                raise ValueError("Node prerequisite")
            process = subprocess.Popen([node, "tools/browser.mjs"], cwd=ROOT,
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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
                raise ValueError("Browser assertions or cleanup failed")
            evidence = json.loads(stdout)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            connections.close_all()
        if thread.is_alive():
            raise ValueError("Fixture cleanup failed")
        print(json.dumps(evidence))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Browser acceptance or prerequisite FAIL", file=sys.stderr)
        raise SystemExit(1)
