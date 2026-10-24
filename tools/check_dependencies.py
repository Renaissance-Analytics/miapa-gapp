"""Approved closure integrity and current public advisory checks. No install or hooks."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def verify_lock(directory):
    manifest = json.loads((directory / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((directory / "package-lock.json").read_text(encoding="utf-8"))
    if lock.get("lockfileVersion") != 3 or not lock.get("packages"):
        raise ValueError("Missing lock")
    packages = lock["packages"]
    for key in ("dependencies", "devDependencies"):
        if manifest.get(key, {}) != packages[""].get(key, {}):
            raise ValueError("Manifest/lock divergence")
        for name, version in manifest.get(key, {}).items():
            if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[a-zA-Z0-9.-]+)?", version) or packages.get("node_modules/" + name, {}).get("version") != version:
                raise ValueError("Unpinned dependency")
    installed = 0
    def registry_verified(metadata):
        return (str(metadata.get("resolved", "")).startswith("https://registry.npmjs.org/")
                and str(metadata.get("integrity", "")).startswith("sha512-") and not metadata.get("link"))

    for name, metadata in packages.items():
        if not name:
            continue
        parent_name = name.rsplit("/node_modules/", 1)[0] if "/node_modules/" in name else None
        bundled = (metadata.get("inBundle") is True and parent_name is not None
                   and registry_verified(packages.get(parent_name, {})))
        if (not name.startswith("node_modules/") or ".." in Path(name).parts or metadata.get("link")
                or not (registry_verified(metadata) or bundled)):
            raise ValueError("Untrusted dependency source")
        path = directory / name / "package.json"
        if path.is_file():
            if json.loads(path.read_text(encoding="utf-8"))["version"] != metadata["version"]:
                raise ValueError("Installed version divergence")
            installed += 1
        elif not metadata.get("optional"):
            raise ValueError("Required dependency absent")

    def inspect_modules(modules):
        if not modules.exists():
            return
        if modules.is_symlink():
            raise ValueError("Linked installed dependency")
        for entry in modules.iterdir():
            if entry.name.startswith("."):
                continue
            if entry.is_symlink():
                raise ValueError("Linked installed dependency")
            entries = list(entry.iterdir()) if entry.name.startswith("@") else [entry]
            for package in entries:
                if package.is_symlink() or not package.is_dir():
                    raise ValueError("Invalid installed dependency")
                if package.relative_to(directory).as_posix() not in packages:
                    raise ValueError("Unexpected installed dependency")
                inspect_modules(package / "node_modules")
    inspect_modules(directory / "node_modules")
    return installed


def main():
    policy = json.loads((ROOT / "tools/dependency-policy.json").read_text(encoding="utf-8"))
    for name, expected in policy["inputs"].items():
        actual = hashlib.sha256((ROOT / name).read_text(encoding="utf-8").encode()).hexdigest()
        if actual != expected:
            raise ValueError("Dependency closure requires renewed review")
    expected = policy["python"]
    actual = {dist.metadata["Name"].lower(): dist.version for dist in importlib.metadata.distributions()}
    if {key: value for key, value in actual.items() if key != "pip"} != expected:
        raise ValueError("Python environment differs from approved closure")
    for name, version in expected.items():
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as response:
            metadata = json.load(response)
        if metadata.get("vulnerabilities") is None or metadata["vulnerabilities"]:
            raise ValueError("Python advisory check unavailable or nonempty")
    node = shutil.which("node")
    npm = Path(shutil.which("npm.cmd") or "").parent / "node_modules/npm/bin/npm-cli.js"
    if not node or not npm.is_file():
        raise ValueError("npm prerequisite")
    for name in ("frontend", "backend/agent"):
        verify_lock(ROOT / name)
        with tempfile.TemporaryDirectory(prefix="maipa-npm-audit-") as temporary:
            user_config, global_config = Path(temporary) / "user.npmrc", Path(temporary) / "global.npmrc"
            user_config.write_text("", encoding="utf-8")
            global_config.write_text("", encoding="utf-8")
            result = subprocess.run([node, str(npm), "audit", "--json", "--ignore-scripts",
                                     f"--userconfig={user_config}", f"--globalconfig={global_config}",
                                     "--registry=https://registry.npmjs.org/"], cwd=ROOT / name,
                                    capture_output=True, text=True, timeout=90)
        audit = json.loads(result.stdout)
        if result.returncode or audit.get("metadata", {}).get("vulnerabilities", {}).get("total") != 0:
            raise ValueError("npm advisory check unavailable or nonempty")
    print("Approved closure and public advisory checks PASS; no known advisories is not a full security review.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Dependency/security prerequisite FAIL ({type(error).__name__}): {error}", file=sys.stderr)
        raise SystemExit(1)
