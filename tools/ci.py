"""Fixed local/Actions pipeline for implemented bootstrap contracts, not MVP release acceptance."""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time

STAGES = ("alignment", "dependencies", "static", "backend", "frontend", "migrations", "build", "browser")
ROOT = Path(__file__).resolve().parents[1]
BROWSER_CASES = ('local-first-run-admin', 'account-chat-resume-and-confirm', 'first-run-stays-closed',
                 "anonymous-authority-and-build", "fancy-release-controls", "theme-and-reload", "keyboard-divider",
                 "mobile-panels", "real-login-and-server-role", 'onboarding-user-isolation', "authenticated-account-scope", "csrf-and-real-zero-provider-transport",
                 "intake-initial-load-recovery", 'persistent-chat-work-views', 'layout-gestures-conflict-recovery', "intake-facts-and-evidence", "intake-history-and-conflicts", 'client-authority-and-revocation', "logout-and-viewer-denial", "intake-user-isolation",
                 'layout-user-isolation-and-login', 'layout-fresh-browser-restore',
                 "no-uncaught-exceptions", "owned-browser-cleanup")


def _positive(value):
    return type(value) is int and value > 0


def valid_evidence(stage, value, build_id):
    if not isinstance(value, dict) or type(value.get("returncode")) is not int or value["returncode"] != 0:
        return False
    if stage in {"backend", "frontend"}:
        return (_positive(value.get("tests")) and value.get("passed") == value["tests"]
                and type(value.get("passed")) is int and type(value.get("skipped")) is int
                and type(value.get("failed")) is int and value["skipped"] == value["failed"] == 0)
    if not _positive(value.get("checks")):
        return False
    if stage in {"build", "browser"} and not re.fullmatch(r"[0-9a-f]{64}", str(value.get("build_id", ""))):
        return False
    if stage == "browser":
        return (value["build_id"] == build_id and value.get("cleanup") is True
                and isinstance(value.get("browser"), str) and bool(value["browser"].strip()))
    return True


def run_pipeline(execute, snapshot):
    report = {"schema": 1, "scope": "implemented-bootstrap", "ok": False, "stages": []}
    try:
        candidate = snapshot()
        report["candidate"] = candidate
        if (not isinstance(candidate, dict)
                or any(not re.fullmatch(r"[0-9a-f]{40}", str(candidate.get(key, ""))) for key in ("commit", "tree"))
                or not re.fullmatch(r"[0-9a-f]{64}", str(candidate.get("source_sha256", "")))):
            report["failure"] = "candidate_identity"
            return report
        build_id = None
        for stage in STAGES:
            try:
                value = execute(stage)
            except Exception:
                report["stages"].append({"stage": stage, "ok": False})
                report["failure"] = stage
                return report
            valid = valid_evidence(stage, value, build_id)
            report["stages"].append({"stage": stage, **(value if isinstance(value, dict) else {}), "ok": valid})
            if not valid:
                report["failure"] = stage
                return report
            if snapshot() != candidate:
                report["failure"] = "candidate_changed"
                return report
            if stage == "build":
                build_id = value["build_id"]
        report["ok"] = True
    except Exception:
        report["failure"] = "prerequisite_or_execution"
    return report


def source_files(root):
    raw = subprocess.check_output(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root)
    return sorted(set(raw.decode("utf-8").strip("\0").split("\0")))


def candidate_snapshot(root):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    inputs = []
    for name in source_files(root):
        path = root / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Unsafe candidate source")
        raw = path.read_bytes()
        try:
            raw = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode()
        except UnicodeError:
            pass
        inputs.append([name, hashlib.sha256(raw).hexdigest()])
    return {"commit": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
            "source_sha256": hashlib.sha256(json.dumps(inputs, separators=(",", ":")).encode()).hexdigest(),
            "dirty": bool(git("status", "--porcelain")), "files": len(inputs)}


def child_environment():
    # Never forward provider tokens, npm auth, cloud credentials or Python/Node injection flags.
    names = {"PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"}
    return {**{key: value for key, value in os.environ.items() if key in names},
            "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1", "CI": "true", "NO_COLOR": "1"}


class Commands:
    def __init__(self, root):
        self.root = root
        self.artifacts = root / "artifacts/ci"
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.node = shutil.which("node")
        if not self.node:
            raise ValueError("Node prerequisite")
        self.env = child_environment()
        self.frontend = root / "frontend"

    def command(self, argv, cwd=None, timeout=240):
        process = subprocess.Popen(argv, cwd=cwd or self.root, env=self.env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            process.wait(timeout=10)
            raise ValueError("Required command deadline")
        if process.returncode:
            # Raw child logs remain local; sanitized reports alone are artifact candidates.
            (self.artifacts / "last-failure.log").write_text(stdout + stderr, encoding="utf-8")
            raise ValueError("Required command failed")
        if "chunk size" in stderr.lower() or "chunks are larger" in stderr.lower():
            print(stderr.strip(), file=sys.stderr)
        return stdout

    def __call__(self, stage):
        start = time.monotonic()
        value = {"returncode": 0, "checks": 1}
        if stage == "alignment":
            self.command([sys.executable, "-m", "tools.alignment"])
            self.command([self.node, "frontend/tools/alignment.mjs"])
            review = json.loads((self.root / "tools/browser-review.json").read_text(encoding="utf-8"))
            if review.get("decision") != "aligned" or tuple(item["id"] for item in review["cases"]) != BROWSER_CASES:
                raise ValueError("Browser review missing")
            for name in ("tools/browser.mjs", "tools/check_browser.py", "tools/browser-profile.mjs"):
                digest = hashlib.sha256((self.root / name).read_text(encoding="utf-8").encode()).hexdigest()
                if review["source_sha256"][name] != digest:
                    raise ValueError("Browser source review stale")
            value["checks"] = 3
        elif stage == "dependencies":
            self.command([sys.executable, "-m", "tools.check_dependencies"], timeout=180)
        elif stage == "static":
            paths = source_files(self.root)
            checked = 1  # Strict TypeScript project check below.
            for name in paths:
                if name.endswith(".py"):
                    ast.parse((self.root / name).read_text(encoding="utf-8"), filename=name)
                    checked += 1
                if name.endswith(".mjs"):
                    self.command([self.node, "--check", name])
                    checked += 1
            self.command([self.node, "node_modules/typescript/bin/tsc", "-b"], cwd=self.frontend)
            value["checks"] = checked
        elif stage == "backend":
            target = self.artifacts / "backend.json"
            target.unlink(missing_ok=True)
            try:
                self.command([sys.executable, "-m", "tools.check_backend", "--report", str(target)])
            except ValueError:
                from tools.alignment import discovered_test_ids
                from tools.backend_reporting import failure_evidence
                return failure_evidence(json.loads(target.read_text(encoding="utf-8")) if target.is_file() else None,
                    discovered_test_ids(self.root))
            value = {"returncode": 0, **json.loads(target.read_text(encoding="utf-8"))}
        elif stage == "frontend":
            self.command([self.node, "node_modules/typescript/bin/tsc", "-p", "tsconfig.test.json"], cwd=self.frontend)
            files = sorted(str(p.relative_to(self.frontend)) for p in (self.frontend / "tests").glob("*.test.mjs"))
            output = self.command([self.node, "--test", "--test-reporter=tap", *files], cwd=self.frontend)
            counts = {key: int(number) for key, number in re.findall(r"^# (tests|pass|fail|cancelled|skipped|todo) (\d+)\s*$", output, re.M)}
            if set(counts) != {"tests", "pass", "fail", "cancelled", "skipped", "todo"}:
                raise ValueError("Missing TAP evidence")
            acceptance = json.loads((self.frontend / "tests/acceptance.json").read_text(encoding="utf-8"))
            required = {test for requirement in acceptance["requirements"] for scenario in requirement["scenarios"] for test in scenario["tests"]}
            if counts["tests"] != len(required):
                raise ValueError("Required frontend tests not executed")
            value = {"returncode": 0, "tests": counts["tests"], "passed": counts["pass"],
                     "skipped": counts["skipped"] + counts["todo"], "failed": counts["fail"] + counts["cancelled"]}
        elif stage == "migrations":
            self.command([sys.executable, "backend/manage.py", "makemigrations", "--check", "--dry-run", "--settings=backend.maipa.test_settings"])
        elif stage == "build":
            self.command([self.node, "node_modules/vite/bin/vite.js", "build"], cwd=self.frontend)
            manifest = json.loads((self.frontend / "dist/build-info.json").read_text(encoding="utf-8"))
            value["build_id"] = manifest["build_id"]
        elif stage == "browser":
            target = self.artifacts / "browser-result.json"
            target.unlink(missing_ok=True)
            try:
                output = self.command([sys.executable, "-m", "tools.check_browser"], timeout=180)
            except Exception:
                if target.is_file():
                    from tools.check_browser import browser_failure_evidence
                    return {"returncode": 1, **browser_failure_evidence(json.loads(target.read_text(encoding="utf-8")))}
                return {"returncode": 1, "checks": 0, "reason": "browser_process", "cleanup": False}
            value = {"returncode": 0, **json.loads(output)}
            if tuple(value.get("scenarios", ())) != BROWSER_CASES or value.get("checks") != len(BROWSER_CASES):
                raise ValueError("Required browser scenarios not executed")
        else:
            raise ValueError("Unknown required stage")
        return {**value, "seconds": round(time.monotonic() - start, 3)}


def main():
    started = datetime.now(timezone.utc).isoformat()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-dirty", action="store_true", help="Development evidence only; never a publication candidate")
    args = parser.parse_args()
    try:
        commands = Commands(ROOT)
        runtime = {"python": platform.python_version(), "node": commands.command([commands.node, "--version"]).strip(), "platform": sys.platform}
        if runtime != {"python": "3.13.15", "node": "v24.19.0", "platform": "win32"}:
            raise ValueError("Unverified runtime")
        initial = candidate_snapshot(ROOT)
        if initial["dirty"] and not args.allow_dirty:
            raise ValueError("Clean commit required")
        report = run_pipeline(commands, lambda: candidate_snapshot(ROOT))
        report["runtime"] = runtime
        report["publication_candidate"] = report["ok"] and not initial["dirty"]
    except Exception:
        report = {"schema": 1, "scope": "implemented-bootstrap", "ok": False, "failure": "prerequisite_or_execution"}
    report["started_at"] = started
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    path = ROOT / "artifacts/ci/report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
