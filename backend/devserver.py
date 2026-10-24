"""Explicit local-only Django development entrypoint for a Genie-managed site.

Generate a private ignored development key, migrate local SQLite and serve the
built frontend/API at one origin. Never use this entrypoint for deployment.
"""
import argparse
import os
from pathlib import Path
import secrets
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", type=int)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("A valid unprivileged local port is required")
    root = Path(__file__).resolve().parents[1]
    if not (root / "frontend/dist/index.html").is_file():
        parser.error("Build the frontend before starting this development site")
    if os.environ.get("DJANGO_SETTINGS_MODULE") not in (None, "backend.maipa.settings"):
        parser.error("This entrypoint serves only the Maipa development settings")
    runtime = root / "runtime"
    runtime.mkdir(exist_ok=True)
    if not os.environ.get("MAIPA_SECRET_KEY"):
        key_path = runtime / "development-key"
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(secrets.token_urlsafe(72))
        except FileExistsError:
            pass
        os.environ["MAIPA_SECRET_KEY"] = key_path.read_text(encoding="utf-8")
    os.environ["MAIPA_LOCAL_DEV"] = "1"
    os.environ.setdefault("MAIPA_ALLOWED_HOSTS", "maipa.gen,localhost,127.0.0.1,[::1]")
    os.environ["DJANGO_SETTINGS_MODULE"] = "backend.maipa.settings"
    sys.path.insert(0, str(root))
    from django.core.management import execute_from_command_line
    execute_from_command_line(["manage.py", "migrate", "--noinput"])
    from backend.maipa.workspace.bootstrap import prepare_local_setup
    prepare_local_setup()
    execute_from_command_line(["manage.py", "runserver", f"127.0.0.1:{args.port}", "--noreload"])


if __name__ == "__main__":
    main()
