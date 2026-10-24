import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from backend.maipa.acp import AcpClient, AcpError, Scope

ROOT = Path(__file__).resolve().parents[1]
PEER = ROOT / "tests/fixtures/acp_peer.py"


class AcpClientTests(unittest.TestCase):
    def run_case(self, body, *, mode="normal", timeout=2, budget=4096):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                owner = Scope("user-1", "account-1")
                client = AcpClient([sys.executable, str(PEER), mode], owner, Path(directory), timeout=timeout, budget=budget)
                await client.start()
                try:
                    await body(client, owner)
                finally:
                    await client.close()
                self.assertIsNotNone(client.returncode)
        asyncio.run(run())

    def test_correlates_reversed_responses_and_negotiates_protocol(self):
        async def case(client, owner):
            self.assertEqual((await client.initialize(owner))["protocolVersion"], 1)
            results = await asyncio.gather(client._request(owner, "echo", {"value": 1}), client._request(owner, "echo", {"value": 2}))
            self.assertEqual(results, [{"value": 1}, {"value": 2}])
        self.run_case(case)

    def test_foreign_user_or_account_cannot_send_or_read_session_data(self):
        async def case(client, owner):
            for foreign in (Scope("user-2", "account-1"), Scope("user-1", "account-2")):
                with self.assertRaises(PermissionError):
                    await client.new_session(foreign)
                with self.assertRaises(PermissionError):
                    client.drain_updates(foreign, "unknown")
            session = await client.new_session(owner)
            with self.assertRaises(PermissionError):
                await client.prompt(owner, "acp-local-1", "raw ID is not authority")
            self.assertNotEqual(session, "acp-local-1")
            result = await client.prompt(owner, session, "synthetic input")
            self.assertEqual(result["stopReason"], "end_turn")
        self.run_case(case)

    def test_reads_updates_and_denies_permission_before_prompt_finishes(self):
        async def case(client, owner):
            session = await client.new_session(owner)
            result = await client.prompt(owner, session, "synthetic input")
            self.assertEqual(result["permission"], {"outcome": {"outcome": "cancelled"}})
            self.assertEqual(result["unsupported"], -32601)
            self.assertEqual(client.drain_updates(owner, session), [{"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "synthetic reply"}}])
            self.assertEqual(client.drain_updates(owner, session), [])
        self.run_case(case)

    def test_session_launch_uses_fixed_workspace_and_no_browser_mcp_configuration(self):
        async def case(client, owner):
            session = await client.new_session(owner)
            self.assertEqual(client.session_details(owner, session)["received"], {"cwd": str(client.workspace), "mcpServers": []})
            self.assertNotIn("acp-local-1", session)
        self.run_case(case)

    def test_bad_frames_fail_pending_requests_without_reflecting_provider_data(self):
        for mode in ("malformed", "oversized", "exit", "duplicate", "bool-id", "unknown-id"):
            async def case(client, owner):
                results = await asyncio.gather(client._request(owner, "break", {}), client._request(owner, "echo", {}), return_exceptions=True)
                self.assertTrue(all(isinstance(value, AcpError) for value in results))
                self.assertTrue(all(str(value) == "ACP connection unavailable" for value in results))
            self.run_case(case, mode=mode)

    def test_deadline_closes_connection_without_retry_or_late_authority(self):
        async def case(client, owner):
            self.assertEqual((await client.initialize(owner))["protocolVersion"], 1)
            with self.assertRaisesRegex(AcpError, "^ACP connection unavailable$"):
                await client._request(owner, "break", {})
            with self.assertRaises(AcpError):
                await client.initialize(owner)
            with self.assertRaises(AcpError):
                await client.new_session(owner)
        self.run_case(case, mode="hang", timeout=0.15)

    def test_invalid_scope_and_relative_executable_are_refused_before_spawn(self):
        with self.assertRaises(ValueError):
            Scope("", "account-1")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                AcpClient(["python", str(PEER)], Scope("user-1", "account-1"), Path(directory))

    def test_shutdown_report_is_distinct_from_process_exit(self):
        async def case(client, owner):
            report = await client.shutdown(owner)
            self.assertEqual(report, {"verified": True, "survivors": 0, "sessions": 0})
        self.run_case(case)

    def test_worker_profile_must_match_account_user_and_fixed_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            owner = Scope("user-1", "account-1")
            profile = {"provider": "codex", "user_id": "user-1", "account_id": "account-1", "workspace": str(base)}
            for field, replacement in (("user_id", "user-2"), ("account_id", "account-2"), ("workspace", str(base.parent))):
                with self.subTest(field=field):
                    with self.assertRaises(PermissionError):
                        AcpClient([sys.executable, str(PEER)], owner, base,
                            env={"MAIPA_ACP_PROFILE": json.dumps(profile | {field: replacement})})

    def test_parent_secrets_and_runtime_injection_variables_are_not_inherited(self):
        synthetic = {key: "synthetic-private-value" for key in ("OPENAI_API_KEY", "NODE_OPTIONS", "PYTHONPATH", "MAIPA_SECRET_KEY")}
        async def case(client, owner):
            result = await client._request(owner, "inspect-environment", {})
            self.assertEqual(result, {"inherited": []})
            self.assertEqual({key: os.environ[key] for key in synthetic}, synthetic)
        with patch.dict(os.environ, synthetic):
            self.run_case(case)

    def test_oversized_outgoing_request_is_refused_without_breaking_initialization(self):
        async def case(client, owner):
            with self.assertRaisesRegex(AcpError, "application budget"):
                await client._request(owner, "echo", {"input": "x" * 5000})
            self.assertEqual((await client.initialize(owner))["protocolVersion"], 1)
        self.run_case(case)
