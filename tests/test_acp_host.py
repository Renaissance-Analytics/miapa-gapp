import asyncio
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from backend.maipa.acp import AcpClient, AcpError, Scope

ROOT = Path(__file__).resolve().parents[1]


class AcpHostTests(unittest.TestCase):
    def run_host(self, body, *, real_entry=False):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                for name in ("workspace", "home", "supervisor"):
                    (base / name).mkdir()
                profile = {"provider": "codex", "user_id": "user-1", "account_id": "account-1",
                    "workspace": str((base / "workspace").resolve()), "home": str(base / "home"), "supervisor_directory": str(base / "supervisor")}
                script = ROOT / ("backend/agent/host.mjs" if real_entry else "tests/fixtures/acp_host.mjs")
                owner = Scope("user-1", "account-1")
                node = shutil.which("node")
                if node is None:
                    self.fail("Required Node runtime unavailable")
                client = AcpClient([node, str(script)], owner, base / "workspace", env={"MAIPA_ACP_PROFILE": json.dumps(profile)}, timeout=15)
                await client.start()
                try:
                    await body(client, owner)
                finally:
                    await client.close()
                self.assertEqual(client.returncode, 0)
        asyncio.run(run())

    def test_real_entry_initializes_published_host_and_reports_zero_provider_shutdown(self):
        async def case(client, owner):
            result = await client.initialize(owner)
            self.assertEqual(result["protocolVersion"], 1)
            self.assertEqual(result["agentInfo"]["version"], "0.28.0")
            self.assertEqual(result["authMethods"], [])
            self.assertEqual(await client.shutdown(owner), {"verified": True, "survivors": 0, "sessions": 0})
        self.run_host(case, real_entry=True)

    def test_python_host_published_codex_driver_session_and_stream_round_trip(self):
        async def case(client, owner):
            session = await client.new_session(owner)
            result = await client.prompt(owner, session, "Synthetic fixture input")
            self.assertEqual(result["stopReason"], "end_turn")
            updates = client.drain_updates(owner, session)
            chunks = [item for item in updates if item.get("sessionUpdate") == "agent_message_chunk"]
            self.assertEqual([item["content"]["text"] for item in chunks], ["Synthetic ACP round trip"])
            self.assertEqual(chunks[0]["messageId"], "fixture-message")
            with self.assertRaises(PermissionError):
                await client.prompt(Scope("user-2", "account-1"), session, "Denied")
        self.run_host(case)

    def test_host_refuses_foreign_workspace_or_mcp_before_constructing_a_driver(self):
        async def case(client, owner):
            await client.initialize(owner)
            for params in ({"cwd": str(client.workspace.parent), "mcpServers": []},
                           {"cwd": str(client.workspace), "mcpServers": [{"type": "http", "name": "foreign", "url": "http://127.0.0.1:1", "headers": []}]}):
                with self.assertRaises(AcpError):
                    await client._request(owner, "session/new", params)
            self.assertEqual(await client.shutdown(owner), {"verified": True, "survivors": 0, "sessions": 0})
        self.run_host(case)
