"""A UI callback cannot stall ACP reading, grant provider permission or leak errors."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

from backend.maipa.acp import AcpClient, Scope

PEER = Path(__file__).resolve().parent / 'fixtures/acp_ui_peer.py'


class AcpUiTests(unittest.TestCase):
    def case(self, body, handler=None, timeout=2):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                owner = Scope('user-1', 'account-1')
                client = AcpClient([sys.executable, str(PEER)], owner, Path(directory),
                                   ui_handler=handler, timeout=timeout)
                await client.start()
                try:
                    await body(client, owner, await client.new_session(owner))
                finally:
                    await client.close()
                self.assertIsNotNone(client.returncode)
        asyncio.run(run())

    def test_callback_returns_only_its_result_on_the_same_scoped_connection(self):
        received = []
        async def handler(value):
            received.append(value)
            return {'tools': [{'name': 'maipa_ui_present'}]}
        async def body(client, owner, session):
            result = await client.prompt(owner, session, 'catalog')
            self.assertEqual(result['result'], {'tools': [{'name': 'maipa_ui_present'}]})
            self.assertEqual(received, [{'method': 'tools/list', 'params': {}}])
        self.case(body, handler)

    def test_slow_callback_does_not_block_streaming_notifications(self):
        release = asyncio.Event()
        entered = asyncio.Event()
        async def handler(value):
            entered.set()
            await release.wait()
            return {'tools': []}
        async def body(client, owner, session):
            task = asyncio.create_task(client.prompt(owner, session, 'catalog'))
            await asyncio.wait_for(entered.wait(), 1)
            for _ in range(100):
                updates = client.drain_updates(owner, session)
                if updates:
                    break
                await asyncio.sleep(.005)
            self.assertEqual(updates[0]['content']['text'], 'Still streaming')
            self.assertFalse(task.done())
            release.set()
            self.assertEqual((await task)['result'], {'tools': []})
        self.case(body, handler)

    def test_unconfigured_ui_and_foreign_rpc_never_gain_a_callback(self):
        async def body(client, owner, session):
            self.assertEqual((await client.prompt(owner, session, 'catalog'))['error']['code'], -32601)
        self.case(body)
        calls = []
        async def handler(value):
            calls.append(value)
            return {'tools': []}
        async def foreign(client, owner, session):
            self.assertEqual((await client.prompt(owner, session, 'foreign'))['error']['code'], -32601)
            self.assertEqual(len(calls), 1)
        self.case(foreign, handler)

    def test_ui_handler_cannot_approve_provider_permissions(self):
        async def handler(value):
            return {'tools': []}
        async def body(client, owner, session):
            self.assertEqual((await client.prompt(owner, session, 'permission'))['result'],
                             {'outcome': {'outcome': 'cancelled'}})
        self.case(body, handler)

    def test_callback_failure_and_oversized_result_are_redacted(self):
        async def broken(value):
            raise RuntimeError('synthetic-private-path-and-token')
        async def oversized(value):
            return {'private': 'x' * 1_048_576}
        async def body(client, owner, session):
            result = await client.prompt(owner, session, 'catalog')
            self.assertEqual(result['error'], {'code': -32000, 'message': 'UI tool unavailable'})
            self.assertNotIn('synthetic-private', str(result))
        for handler in (broken, oversized):
            self.case(body, handler)

    def test_pending_callbacks_are_bounded_and_cancelled_on_close(self):
        cancelled = []
        async def handler(value):
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)
        async def body(client, owner, session):
            result = await client.prompt(owner, session, 'flood')
            self.assertEqual(result['error'], {'code': -32000, 'message': 'UI tool unavailable'})
            await client.close()
            self.assertEqual(len(cancelled), 8)
        self.case(body, handler)
