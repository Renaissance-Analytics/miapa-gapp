"""Check the real public runtime deployment and physical-path refusals."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from backend.maipa.acp import Scope
from backend.maipa.worker_capsule import WorkerCapsule
from backend.maipa.windows_sandbox import SandboxUnavailable, physical_path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "backend/agent"
NODE = shutil.which("node")


class WorkerCapsuleTests(unittest.TestCase):
    def test_deploys_exact_runtime_bytes_and_removes_only_its_owned_directory(self):
        with tempfile.TemporaryDirectory(prefix="maipa-capsule-test-") as directory:
            root = Path(directory).resolve()
            retained = root / "unrelated-synthetic.txt"
            retained.write_text("synthetic-only", encoding="utf-8")
            with WorkerCapsule(root, RUNTIME, NODE, Scope("user-1", "account-1"), "codex") as capsule:
                base = capsule.base
                self.assertEqual(base.parent, root)
                self.assertEqual(capsule.profile["user_id"], "user-1")
                self.assertEqual(capsule.profile["account_id"], "account-1")
                self.assertEqual(capsule.node_sha256, hashlib.sha256(Path(NODE).read_bytes()).hexdigest())
                self.assertEqual(capsule.worker_node.read_bytes(), Path(NODE).read_bytes())
                self.assertGreater(len(capsule.files_sha256), 10)
                for relative, digest in capsule.files_sha256.items():
                    self.assertEqual(hashlib.sha256((RUNTIME / relative).read_bytes()).hexdigest(), digest)
                    self.assertEqual((capsule.code / relative).read_bytes(), (RUNTIME / relative).read_bytes())
                self.assertTrue(any(capsule.supervisor.glob("*.exe")))
                self.assertEqual(set(capsule.workspace.iterdir()), set())
                self.assertEqual(set(capsule.home.iterdir()), set())
            self.assertFalse(base.exists())
            self.assertEqual(retained.read_text(encoding="utf-8"), "synthetic-only")
            self.assertEqual(set(root.iterdir()), {retained})

    def test_runtime_and_writable_root_junctions_are_refused(self):
        with tempfile.TemporaryDirectory(prefix="maipa-reparse-test-") as directory:
            base = Path(directory).resolve()
            target, link = base / "target", base / "link"
            target.mkdir()
            # Both paths are freshly minted synthetic paths. rmdir removes
            # the junction itself; no recursive shell cleanup is used.
            tool = str(Path(__import__("os").environ["SystemRoot"]) / "System32/cmd.exe")
            subprocess.run([tool, "/d", "/c", "mklink", "/J", str(link), str(target)],
                check=True, capture_output=True, timeout=10)
            try:
                with self.assertRaises(SandboxUnavailable):
                    physical_path(link)
                with self.assertRaises(SandboxUnavailable):
                    WorkerCapsule(base / "worker-root", link, NODE, Scope("user-1", "account-1"), "codex")
                with self.assertRaises(SandboxUnavailable):
                    with WorkerCapsule(link / "child", RUNTIME, NODE, Scope("user-1", "account-1"), "codex"):
                        self.fail("A junction-backed writable root was accepted")
                self.assertEqual(set(target.iterdir()), set())
            finally:
                link.rmdir()
