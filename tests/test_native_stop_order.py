"""Actual startup cancellation may finish before its caller resumes."""
import threading,time
from unittest.mock import patch
from django.test import TransactionTestCase
from backend.maipa.agent_accounts.models import Conversation,AgentAccount
from tests import test_agent_chat_runtime as runtime_fixture


class NativeStopOrderingTests(TransactionTestCase):
    setUp=runtime_fixture.AgentChatRuntimeTests.setUp
    state=runtime_fixture.AgentChatRuntimeTests.state
    until=runtime_fixture.AgentChatRuntimeTests.until
    native_start=runtime_fixture.AgentChatRuntimeTests.native_start
    assert_no_native_authority=runtime_fixture.AgentChatRuntimeTests.assert_no_native_authority

    def tearDown(self):
        runtime=runtime_fixture.chat_runtime
        with runtime._lock:
            workers=list(runtime._workers.values())
            for worker in workers:worker.stopping.set()
        for worker in workers:
            worker.thread.join(timeout=10);self.assertFalse(worker.thread.is_alive())
            self.assertIsNotNone(worker.client.returncode)
        self.prepared.stop();self.configured.disable();self.temporary.cleanup()
        super().tearDown()

    def test_verified_cleanup_can_finish_immediately_when_disconnect_is_signalled(self):
        pending,worker=self.native_start('deferred')
        original_set=worker.stopping.set;request_thread=threading.get_ident()
        def stop_and_allow_cleanup():
            original_set()
            if threading.get_ident()!=request_thread:return
            deadline=time.monotonic()+5
            while Conversation.objects.filter(pk=worker.identity,worker=worker.token).exists() and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertFalse(Conversation.objects.filter(pk=worker.identity,worker=worker.token).exists())
        # Hold the request only at the real signal boundary. The actual worker
        # and ACP peer finish their ordinary shutdown/checkpoint cleanup.
        with patch.object(worker.stopping,'set',side_effect=stop_and_allow_cleanup):
            response=self.client.post('/api/agent/chat/',{'action':'disconnect',
                'selection_revision':pending['connection']['revision']},content_type='application/json',
                HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
        self.assertEqual(response.status_code,200)
        worker.thread.join(timeout=10);self.assertFalse(worker.thread.is_alive())
        self.assertTrue(worker.client.shutdown_report['verified']);self.assertEqual(worker.client.shutdown_report['survivors'],0)
        self.assertEqual((self.state()['phase'],self.state()['connection']['account_id']),('offline',str(self.account.pk)))
        self.assertTrue(AgentAccount.objects.filter(pk=self.account.pk,owner=self.owner).exists())
        self.assert_no_native_authority()
