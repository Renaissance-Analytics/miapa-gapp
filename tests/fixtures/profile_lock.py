"""Synthetic process for real cross-process lease contention/death checks."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.maipa.storage import install_lock

try:
    with install_lock(Path(sys.argv[1])):
        print('acquired', flush=True)
        if sys.argv[2] == 'hold':
            sys.stdin.readline()
except OSError:
    print('busy', flush=True)
    raise SystemExit(23)
