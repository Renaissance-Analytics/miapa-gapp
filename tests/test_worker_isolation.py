"""P01 native Windows outcomes; required real processes, no skips or provider login."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from backend.maipa.acp import AcpClient, AcpError, Scope

NODE = shutil.which("node")
FIXTURE = Path(__file__).resolve().parent / "fixtures/isolation_worker.mjs"


def worker_command(code, *args):
    node = code / "node.exe"
    shutil.copyfile(NODE, node)
    return [str(node), "--preserve-symlinks", "--preserve-symlinks-main", *args]


class WorkerIsolationTests(unittest.TestCase):
    def test_two_live_user_workers_cannot_read_each_others_assigned_files(self):
        from backend.maipa.windows_sandbox import WindowsSandbox, ProcessWitness
        async def run():
            with tempfile.TemporaryDirectory(prefix="maipa-native-users-") as directory:
                base = Path(directory).resolve()
                code, first, second, server = [base / name for name in ("code", "first", "second", "server")]
                for folder in (code, first, second, server):
                    folder.mkdir()
                    (folder / "synthetic.txt").write_text("synthetic-only", encoding="utf-8")
                shutil.copyfile(FIXTURE, code / "worker.mjs")
                prefix = worker_command(code, str(code / "worker.mjs"), "access-hold")
                owners = (Scope("user-1", "account-1"), Scope("user-2", "account-2"))
                with WindowsSandbox(owners[0], base, writable=(first,), readonly=(code,), installation_id="synthetic-install") as one:
                    with WindowsSandbox(owners[1], base, writable=(second,), readonly=(code,), installation_id="synthetic-install") as two:
                        self.assertNotEqual(one.profile_name, two.profile_name)
                        children = []
                        for own, foreign, scope, sandbox in ((first, second, owners[0], one), (second, first, owners[1], two)):
                            paths = [str(folder / "synthetic.txt") for folder in (own, foreign, server, code)]
                            children.append(await sandbox.spawn([*prefix, json.dumps(paths)], scope=scope, cwd=own, env={}, limit=4096))
                        for child in children:
                            result = json.loads(await asyncio.wait_for(child.stdout.readline(), 10))
                            self.assertEqual([result["reads"][0], result["reads"][3]], [True, True])
                            self.assertTrue(all(value in {"EACCES", "EPERM"} for value in result["reads"][1:3]))
                            self.assertEqual(json.loads(await asyncio.wait_for(child.stdout.readline(), 10)), result["reads"])
                        with ProcessWitness(children[0].pid) as live_one, ProcessWitness(children[1].pid) as live_two:
                            self.assertFalse(live_one.exited)
                            self.assertFalse(live_two.exited)
                            for child in children:
                                child.kill()
                                await child.wait()
                                self.assertTrue(child.job_empty)
                            self.assertTrue(live_one.exited)
                            self.assertTrue(live_two.exited)
                self.assertTrue(one.profile_removed)
                self.assertTrue(two.profile_removed)
        asyncio.run(run())

    def test_blocked_native_write_expires_and_leaves_an_empty_job(self):
        from backend.maipa.windows_sandbox import WindowsSandbox
        async def run():
            with tempfile.TemporaryDirectory(prefix="maipa-native-write-") as directory:
                base = Path(directory).resolve()
                work, code = base / "work", base / "code"
                work.mkdir()
                code.mkdir()
                command = worker_command(code, "-e", "setInterval(()=>{},1000)")
                owner = Scope("user-1", "account-1")
                with WindowsSandbox(owner, base, writable=(work,), readonly=(code,), installation_id="synthetic-install") as sandbox:
                    client = AcpClient(command, owner, work, launcher=sandbox, timeout=0.2, budget=131072)
                    await client.start()
                    try:
                        with self.assertRaisesRegex(AcpError, "^ACP connection unavailable$"):
                            await asyncio.wait_for(client._request(owner, "synthetic-blocked-write", {"input": "x" * 65536}), 3)
                    finally:
                        await client.close()
                    self.assertIsNotNone(client.returncode)
                    self.assertTrue(sandbox.process.job_empty)
                self.assertTrue(sandbox.profile_removed)
        asyncio.run(run())

    def test_native_environment_is_filtered_and_scope_revocation_ends_worker(self):
        from backend.maipa.windows_sandbox import WindowsSandbox, ProcessWitness
        async def run():
            with tempfile.TemporaryDirectory(prefix="maipa-native-env-") as directory:
                base = Path(directory).resolve()
                work, code = base / "work", base / "code"
                work.mkdir()
                code.mkdir()
                forbidden = ["OPENAI_API_KEY", "NODE_OPTIONS", "PYTHONPATH", "MAIPA_SECRET_KEY"]
                script = "console.log(JSON.stringify(" + json.dumps(forbidden) + ".filter(k=>k in process.env)));setInterval(()=>{},1000)"
                command = worker_command(code, "-e", script)
                owner = Scope("user-1", "account-1")
                environment = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
                environment.update({key: "synthetic-private-value" for key in forbidden})
                with patch.dict(os.environ, environment, clear=True):
                    with WindowsSandbox(owner, base, writable=(work,), readonly=(code,), installation_id="synthetic-install") as sandbox:
                        child = await sandbox.spawn(command, scope=owner, cwd=work, env={}, limit=4096)
                        self.assertEqual(json.loads(await asyncio.wait_for(child.stdout.readline(), 10)), [])
                        with ProcessWitness(child.pid) as worker:
                            with self.assertRaises(PermissionError):
                                sandbox.revoke(Scope("foreign-user", "account-1"))
                            self.assertFalse(worker.exited)
                            sandbox.revoke(owner)
                            await child.wait()
                            self.assertTrue(worker.exited)
                            self.assertTrue(child.job_empty)
                self.assertTrue(sandbox.profile_removed)
        asyncio.run(run())

    def test_real_worker_and_descendant_can_access_only_their_assigned_files(self):
        from backend.maipa.windows_sandbox import WindowsSandbox
        async def run():
            with tempfile.TemporaryDirectory(prefix="maipa-native-test-") as directory:
                base = Path(directory).resolve()
                code, work, foreign, server = [base / name for name in ("code", "work", "foreign", "server")]
                for folder in (code, work, foreign, server):
                    folder.mkdir()
                    (folder / "synthetic.txt").write_text("synthetic-only", encoding="utf-8")
                shutil.copyfile(FIXTURE, code / "worker.mjs")
                paths = [str(folder / "synthetic.txt") for folder in (work, foreign, server, code)]
                command = worker_command(code, str(code / "worker.mjs"), "access", json.dumps(paths))
                control = subprocess.run(command, cwd=work, check=True, capture_output=True, text=True, timeout=15)
                self.assertEqual(json.loads(control.stdout.splitlines()[0])["reads"], [True] * 4)
                owner = Scope("user-1", "account-1")
                with WindowsSandbox(owner, base, writable=(work,), readonly=(code,), installation_id="synthetic-install") as sandbox:
                    child = await sandbox.spawn(command, scope=owner, cwd=work, env={}, limit=4096)
                    self.assertEqual(child.isolation, "windows-appcontainer")
                    data = json.loads(await asyncio.wait_for(child.stdout.readline(), 10))
                    self.assertEqual(data["reads"][0], True)
                    self.assertEqual(data["reads"][3], True)
                    self.assertTrue(all(code in {"EACCES", "EPERM"} for code in data["reads"][1:3]))
                    self.assertEqual(json.loads(await asyncio.wait_for(child.stdout.readline(), 10)), data["reads"])
                    self.assertTrue(data["own_write"])
                    self.assertIn(data["code_write"], {"EACCES", "EPERM"})
                    self.assertEqual(await child.wait(), 0)
                    self.assertTrue(child.job_empty)
                self.assertTrue(sandbox.profile_removed)
                self.assertEqual((server / "synthetic.txt").read_text(encoding="utf-8"), "synthetic-only")
        asyncio.run(run())

    def test_kill_ends_the_actual_root_and_detached_descendant(self):
        from backend.maipa.windows_sandbox import WindowsSandbox, ProcessWitness
        async def run():
            with tempfile.TemporaryDirectory(prefix="maipa-native-test-") as directory:
                base = Path(directory).resolve()
                (base / "work").mkdir()
                (base / "code").mkdir()
                shutil.copyfile(FIXTURE, base / "code/worker.mjs")
                owner = Scope("user-1", "account-1")
                with WindowsSandbox(owner, base, writable=(base / "work",), readonly=(base / "code",), installation_id="synthetic-install") as sandbox:
                    child = await sandbox.spawn(worker_command(base / "code", str(base / "code/worker.mjs"), "hang"), scope=owner, cwd=base / "work", env={}, limit=4096)
                    data = json.loads(await asyncio.wait_for(child.stdout.readline(), 10))
                    with ProcessWitness(child.pid) as root_process, ProcessWitness(data["descendant_pid"]) as descendant:
                        self.assertFalse(root_process.exited)
                        self.assertFalse(descendant.exited)
                        child.kill()
                        await child.wait()
                        self.assertTrue(child.job_empty)
                        self.assertTrue(root_process.exited)
                        self.assertTrue(descendant.exited)
                self.assertTrue(sandbox.profile_removed)
        asyncio.run(run())

    def test_server_process_death_closes_the_job_and_ends_its_descendant(self):
        from backend.maipa.windows_sandbox import ProcessWitness
        helper = Path(__file__).resolve().parent / "fixtures/isolation_owner.py"
        with tempfile.TemporaryDirectory(prefix="maipa-native-death-") as directory:
            base = Path(directory).resolve()
            process = subprocess.Popen([sys.executable, str(helper), str(base), NODE, str(FIXTURE)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=Path(__file__).resolve().parents[1], text=True, encoding="utf-8")
            try:
                line = process.stdout.readline(4097)
                self.assertLessEqual(len(line), 4096)
                receipt = json.loads(line)
                with ProcessWitness(receipt["root_pid"]) as worker, ProcessWitness(receipt["descendant_pid"]) as descendant:
                    self.assertFalse(worker.exited)
                    self.assertFalse(descendant.exited)
                    process.kill()
                    process.wait(timeout=10)
                    self.assertTrue(worker.wait(10))
                    self.assertTrue(descendant.wait(10))
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=10)
                # Profile name is a synthetic receipt from our own helper.
                # Cleanup only that exact profile, after observed job death.
                if "receipt" in locals():
                    from backend.maipa.windows_sandbox import remove_abandoned_profile
                    self.assertTrue(remove_abandoned_profile(receipt["profile_name"]))

    def test_foreign_scope_and_writable_code_overlap_are_refused_before_spawn(self):
        from backend.maipa.windows_sandbox import WindowsSandbox, SandboxUnavailable
        async def run():
            with tempfile.TemporaryDirectory(prefix="maipa-native-test-") as directory:
                base = Path(directory).resolve()
                work = base / "work"
                work.mkdir()
                owner = Scope("user-1", "account-1")
                with self.assertRaises(SandboxUnavailable):
                    WindowsSandbox(owner, base, writable=(base,), readonly=(work,), installation_id="synthetic-install")
                with WindowsSandbox(owner, base, writable=(work,), readonly=(), installation_id="synthetic-install") as sandbox:
                    with self.assertRaises(PermissionError):
                        await sandbox.spawn([NODE, "-e", "process.exit(0)"], scope=Scope("user-2", "account-1"), cwd=work, env={}, limit=4096)
                    self.assertIsNone(sandbox.process)
        asyncio.run(run())
