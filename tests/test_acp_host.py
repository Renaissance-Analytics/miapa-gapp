import asyncio
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from backend.maipa.acp import AcpClient, AcpError, Scope

ROOT = Path(__file__).resolve().parents[1]


class AcpHostTests(unittest.TestCase):
    def run_host(self, body, *, real_entry=False, native_policy=False, startup_features=None, model_reply=None,
                 claude_scenario=None):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                for name in ("workspace", "home", "supervisor"):
                    (base / name).mkdir()
                profile = {"provider": "codex", "user_id": "user-1", "account_id": "account-1",
                    "workspace": str((base / "workspace").resolve()), "home": str(base / "home"), "supervisor_directory": str(base / "supervisor")}
                script = ROOT / ("backend/agent/host.mjs" if real_entry else "tests/fixtures/acp_host.mjs")
                if native_policy or claude_scenario is not None:
                    runtime = base / 'runtime'
                    runtime.mkdir()
                    shutil.copy2(ROOT/'backend/agent/host.mjs', runtime/'host.mjs')
                    shutil.copytree(ROOT/'backend/agent/node_modules/@particle-academy/prism-acp', runtime/'node_modules/@particle-academy/prism-acp')
                    if claude_scenario is not None: profile['provider'] = 'claude'
                    binary = runtime/('provider/claude.exe' if claude_scenario is not None else 'provider/vendor/x86_64-pc-windows-msvc/bin/codex.exe')
                    binary.parent.mkdir(parents=True)
                    binary.write_bytes(b'Synthetic non-executable policy fixture; never launched')
                    profile['provider_binary'] = str(binary)
                    script = runtime/'policy-host.mjs'
                    shutil.copy2(ROOT/('tests/fixtures/claude_policy_host.mjs' if claude_scenario is not None else 'tests/fixtures/acp_policy_host.mjs'), script)
                    if claude_scenario is not None:
                        profile['synthetic_claude_scenario'] = claude_scenario
                        shutil.copy2(ROOT/'tests/fixtures/claude_policy_cli.mjs', runtime/'claude-policy-cli.mjs')
                owner = Scope("user-1", "account-1")
                node = shutil.which("node")
                if node is None:
                    self.fail("Required Node runtime unavailable")
                environment = {"MAIPA_ACP_PROFILE": json.dumps(profile)}
                if startup_features is not None:
                    # Only the copied synthetic CLI transport reads this fixture value.
                    # The real host's validated profile discards it; keep the ACP env closed.
                    profile['synthetic_startup_features'] = startup_features
                    environment['MAIPA_ACP_PROFILE'] = json.dumps(profile)
                if model_reply is not None:
                    profile['synthetic_model_reply'] = model_reply
                    environment['MAIPA_ACP_PROFILE'] = json.dumps(profile)
                client = AcpClient([node, str(script)], owner, base / "workspace", env=environment, timeout=15)
                await client.start()
                try:
                    await body(client, owner)
                finally:
                    await client.close()
                self.assertEqual(client.returncode, 0)
        asyncio.run(run())

    async def native_result(self, client, owner):
        async with asyncio.timeout(5):
            while True:
                result = await client._request(owner, 'maipa/native/status', {})
                self.assertEqual(set(result), {'state'})
                self.assertIn(result['state'], ('starting', 'ready', 'unavailable'))
                if result['state'] != 'starting': return result
                await asyncio.sleep(.01)

    def test_real_entry_initializes_published_host_and_reports_zero_provider_shutdown(self):
        async def case(client, owner):
            result = await client.initialize(owner)
            self.assertEqual(result["protocolVersion"], 1)
            self.assertEqual(result["agentInfo"]["version"], "0.33.0")
            self.assertEqual(result["authMethods"], [])
            self.assertEqual(await client.shutdown(owner), {"verified": True, "survivors": 0, "sessions": 0})
        self.run_host(case, real_entry=True)

    def test_claude_models_enable_first_prompt_before_native_session_announcement(self):
        async def case(client, owner):
            session = await client.new_session(owner)
            self.assertEqual(await self.native_result(client, owner), {'state': 'ready'})
            # Native Claude only announces system/init after this first user input.
            self.assertFalse(any(item.get('_meta', {}).get('particle.academy/cli_session_id')
                                 for item in client.drain_updates(owner, session)))
            self.assertEqual((await client.prompt(owner, session, 'Synthetic first prompt'))['stopReason'], 'end_turn')
            self.assertEqual(await client.native_status(owner, session), 'ready')
            with self.assertRaises(AcpError): await client.new_session(owner)
            self.assertEqual(await client.shutdown(owner), {'verified': True, 'survivors': 0, 'sessions': 1})
        self.run_host(case, claude_scenario='valid')

    def test_claude_unsupported_version_or_empty_models_cannot_become_ready(self):
        async def case(client, owner):
            await client.new_session(owner)
            self.assertEqual(await self.native_result(client, owner), {'state': 'unavailable'})
            self.assertEqual((await client.shutdown(owner))['survivors'], 0)
        for scenario in ('wrong-version', 'empty-models'):
            with self.subTest(scenario=scenario): self.run_host(case, claude_scenario=scenario)

    def test_claude_native_catalog_refusal_stays_unavailable_after_model_readiness(self):
        async def case(client, owner):
            session = await client.new_session(owner)
            self.assertEqual(await self.native_result(client, owner), {'state': 'ready'})
            self.assertEqual((await client.prompt(owner, session, 'Synthetic forbidden catalog'))['stopReason'], 'refusal')
            self.assertEqual(await client.native_status(owner, session), 'unavailable')
            self.assertEqual((await client.shutdown(owner))['survivors'], 0)
        self.run_host(case, claude_scenario='native-tool')

    def test_native_host_enforces_published_mcp_only_policy_and_fixed_model(self):
        async def case(client, owner):
            # Host-owned policy must override even authority-shaped client hints.
            await client.initialize(owner)
            result = await client._request(owner, 'session/new', {'cwd': str(client.workspace), 'mcpServers': [],
                'model': 'gpt-6.1', 'nativeTools': 'default'})
            self.assertTrue(result['sessionId'])
            policy = await client._request(owner, 'fixture/policy-result', {})
            self.assertEqual(policy['startupArgs'][-4:], ['-c', 'features.plugins=false', '-c', 'features.remote_plugin=false'])
            self.assertTrue(policy['threadStarted'])
            self.assertFalse(policy['refused'])
            self.assertEqual(await self.native_result(client, owner), {'state': 'ready'})
            with self.assertRaises(AcpError):
                await client._request(owner, 'maipa/native/status', {'sessionId': 'foreign'})
            with self.assertRaises(AcpError):
                await client.new_session(owner)
            self.assertEqual((await client.shutdown(owner))['sessions'], 1)
        self.run_host(case, native_policy=True)

    def test_native_host_refuses_missing_or_enabled_startup_plugin_features(self):
        async def case(client, owner):
            await client.initialize(owner)
            await client.new_session(owner)
            policy = await client._request(owner, 'fixture/policy-result', {})
            self.assertEqual(policy['startupArgs'][-4:], ['-c', 'features.plugins=false', '-c', 'features.remote_plugin=false'])
            self.assertFalse(policy['threadStarted'])
            self.assertTrue(policy['refused'])
            self.assertEqual(await self.native_result(client, owner), {'state': 'unavailable'})
            self.assertEqual(await client.shutdown(owner), {'verified': True, 'survivors': 0, 'sessions': 1})
        for features in ({}, {'plugins': False}, {'plugins': True, 'remote_plugin': False},
                         {'plugins': False, 'remote_plugin': True}):
            with self.subTest(features=features):
                self.run_host(case, native_policy=True, startup_features=features)

    def test_native_thread_id_does_not_override_later_startup_model_refusal(self):
        async def case(client, owner):
            await client.initialize(owner)
            session = await client.new_session(owner)
            self.assertTrue((await client._request(owner, 'fixture/policy-result', {}))['threadStarted'])
            self.assertEqual(await self.native_result(client, owner), {'state': 'unavailable'})
            self.assertEqual(await client.native_status(owner, session), 'unavailable')
            updates = client.drain_updates(owner, session)
            self.assertTrue(any(item.get('_meta', {}).get('particle.academy/cli_session_id')
                                for item in updates), 'Fixture must reach native ID before the model refusal')
            self.assertEqual(await client.shutdown(owner), {'verified': True, 'survivors': 0, 'sessions': 1})
        self.run_host(case, native_policy=True, model_reply='synthetic-unapproved-model')

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
