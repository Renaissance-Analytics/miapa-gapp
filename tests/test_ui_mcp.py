"""Real same-AppContainer MCP relay and descendants, with synthetic tool content."""
import asyncio
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from backend.maipa.acp import Scope
from backend.maipa.windows_sandbox import WindowsSandbox
from backend.maipa.worker_capsule import WorkerCapsule

ROOT = Path(__file__).resolve().parents[1]


class UiMcpTests(unittest.TestCase):
    def probe(self, mode):
        async def run():
            with tempfile.TemporaryDirectory(prefix='maipa-native-ui-mcp-') as directory:
                scope = Scope('synthetic-ui-user', 'synthetic-ui-account')
                with WorkerCapsule(Path(directory).resolve(), ROOT / 'backend/agent', shutil.which('node'), scope, 'codex', ui_tools=True) as capsule:
                    self.assertEqual((capsule.code / 'ui-mcp.mjs').read_bytes(), (ROOT / 'backend/agent/ui-mcp.mjs').read_bytes())
                    self.assertTrue(capsule.profile['ui_tools'])
                    shutil.copyfile(ROOT / 'tests/fixtures/ui_mcp_probe.mjs', capsule.code / 'probe.mjs')
                    command = (*capsule.command[:-1], str(capsule.code / 'probe.mjs'), mode)
                    with WindowsSandbox(scope, capsule.base, writable=(capsule.workspace, capsule.home),
                                        readonly=(capsule.code, capsule.supervisor), installation_id='synthetic-ui-install') as sandbox:
                        process = await sandbox.spawn(command, scope=scope, cwd=capsule.workspace,
                                                      # This owned fixture report includes the complete
                                                      # text+structured MCP reply, not an agent request.
                                                      env={'MAIPA_ACP_PROFILE': json.dumps(capsule.profile)}, limit=262_144)
                        try:
                            raw = await asyncio.wait_for(process.stdout.readline(), 30)
                            self.assertTrue(raw, 'Native MCP fixture produced no result')
                            report = json.loads(raw)
                            self.assertEqual(await asyncio.wait_for(process.wait(), 5), 0)
                            self.assertTrue(process.job_empty)
                            self.assertEqual(await process.stderr.read(4096), b'')
                        finally:
                            if process.returncode is None:
                                process.kill()
                                await process.wait()
                    self.assertTrue(sandbox.profile_removed)
                self.assertFalse(capsule.base.exists())
            return report
        return asyncio.run(run())

    def assert_stopped(self, report):
        self.assertEqual(len(report['verdicts']), 2)
        for verdict in report['verdicts']:
            self.assertEqual((verdict['verified'], verdict['empty'], verdict['survivors']), (True, True, 0))

    def test_actual_mcp_child_lists_and_calls_over_local_pipe_in_zero_capability_worker(self):
        report = self.probe('normal')
        self.assertEqual(report['results']['initialize']['protocolVersion'], '2025-03-26')
        self.assertEqual(report['results']['list']['tools'][0]['name'], 'synthetic_ui')
        self.assertEqual(report['results']['call']['structuredContent'], {'accepted': True, 'revision': 1})
        self.assertEqual(report['calls'], [{'method': 'tools/list', 'params': {}},
                                         {'method': 'tools/call', 'params': {'name': 'synthetic_ui', 'arguments': {'revision': 0}}}])
        self.assertEqual(report['results']['foreign'], 'UI tool unavailable')
        self.assert_stopped(report)

    def test_relay_redacts_callback_errors_rejects_oversized_frames_and_foreign_capability(self):
        report = self.probe('errors')
        self.assertEqual(report['results']['error'], 'UI tool unavailable')
        # The published peer prefixes a locally failed outbound request with
        # its method. Remote refusal messages above/below remain unprefixed.
        self.assertEqual(report['results']['budget'], 'tools/call: UI tool unavailable')
        self.assertEqual(report['results']['foreign'], 'UI tool unavailable')
        self.assertEqual(report['calls'], [{'method': 'tools/list', 'params': {}}])
        self.assertNotIn('synthetic-private', json.dumps(report))
        self.assert_stopped(report)

    def test_valid_maximum_component_tree_survives_mcp_text_and_structured_output(self):
        report = self.probe('large')
        result = report['results']['call']
        schema = result['structuredContent']['composition']['schema']
        self.assertLessEqual(len(json.dumps(schema, separators=(',', ':')).encode('utf-8')), 32_768)
        self.assertGreater(len(json.dumps(result, separators=(',', ':')).encode('utf-8')), 65_536)
        self.assertEqual(len(schema['children']), 15)
        self.assertEqual(schema['children'][0]['props']['text'], '"' * 1000)
        self.assertEqual(json.loads(result['content'][0]['text']), result['structuredContent'])
        self.assertEqual(report['results']['foreign'], 'UI tool unavailable')
        self.assert_stopped(report)
