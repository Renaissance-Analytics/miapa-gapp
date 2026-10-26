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
    sys.path.insert(0, str(root))
    from backend.maipa.storage import (prepare_install, prepare_files, development_key,
        install_lock, StorageUnavailable)
    try:
        data = prepare_install(root)
        os.environ["MAIPA_DATA_DIR"] = str(data)
        supplied = os.environ.get('MAIPA_SECRET_KEY')
        key = development_key(data, lambda length: supplied or secrets.token_urlsafe(length))
        if supplied and not secrets.compare_digest(supplied.encode('utf-8'), key.encode('utf-8')):
            raise StorageUnavailable('Configured signing key differs from this installation')
        os.environ['MAIPA_SECRET_KEY'] = key
    except (OSError, ValueError, UnicodeError, RecursionError):
        parser.error("Private installation unavailable; check local storage or offline legacy adoption")
    os.environ["MAIPA_LOCAL_DEV"] = "1"
    os.environ.setdefault("MAIPA_ALLOWED_HOSTS", "maipa.gen,localhost,127.0.0.1,[::1]")
    os.environ["DJANGO_SETTINGS_MODULE"] = "backend.maipa.settings"
    from django.core.management import execute_from_command_line
    try:
        with install_lock(data):
            prepare_files(data)
            execute_from_command_line(["manage.py", "migrate", "--noinput"])
            execute_from_command_line(["manage.py", "runserver", f"127.0.0.1:{args.port}", "--noreload"])
    except (OSError, ValueError, UnicodeError, RecursionError):
        parser.error("Private installation unavailable or already active")


if __name__ == "__main__":
    main()
