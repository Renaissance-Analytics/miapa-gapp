import asyncio, json, sys, tempfile, unittest, uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from backend.maipa.acp import AcpClient, AcpError, Scope
from backend.maipa.agent_accounts import chat
from backend.maipa.agent_accounts.models import AgentAccount, Conversation
from backend.maipa.storage import prepare_directory, prepare_files
from backend.maipa.workspace.models import Installation, Membership


class RpcTurnFailureTests(unittest.TestCase):
    def failure(self, mode='normal', method='session/prompt'):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                scope=Scope('synthetic-owner','synthetic-account')
                client=AcpClient([sys.executable,str(Path(__file__).parent/'fixtures/turn_failure_peer.py'),mode],scope,Path(directory),timeout=2)
                await client.start()
                try:
                    await client.initialize(scope);session=await client.new_session(scope)
                    with self.assertRaises(AcpError) as refused:
                        if method=='session/prompt':await client.prompt(scope,session,'Synthetic input')
                        else:await client._request(scope,method,{})
                    error=refused.exception
                    self.assertNotIn('PRIVATE',str(error));self.assertNotIn('PRIVATE',repr(error.__dict__))
                    return getattr(error,'failure',None)
                finally:
                    verdict=await client.shutdown(scope);self.assertTrue(verdict['verified']);await client.close()
                    self.assertIsNotNone(client.returncode)
        return asyncio.run(run())

    def test_prompt_preserves_only_public_closed_kind_and_finite_reset_without_raw_content(self):
        self.assertEqual(self.failure(),{'kind':'rate_limit','resets_at_ms':1793313000000})
        self.assertEqual(self.failure('other'),{'kind':'other'})

    def test_nonprompt_nested_unknown_or_malformed_metadata_never_invents_a_provider_cause(self):
        self.assertIsNone(self.failure(method='synthetic/nonprompt'))
        self.assertIsNone(self.failure('nested'));self.assertIsNone(self.failure('unknown'))
        self.assertEqual(self.failure('bad-reset'),{'kind':'rate_limit'})


class StoredTurnFailureTests(TransactionTestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='maipa-turn-failure-');self.addCleanup(temporary.cleanup)
        data=prepare_directory(Path(temporary.name).resolve()/'install');prepare_files(data)
        configured=override_settings(DATA_ROOT=data);configured.enable();self.addCleanup(configured.disable)
        install=Installation.objects.create();self.owner=get_user_model().objects.create_user(username='failure-owner')
        self.other=get_user_model().objects.create_user(username='failure-other')
        for actor in (self.owner,self.other):Membership.objects.create(user=actor,installation=install,role='preparer')
        self.account=AgentAccount.objects.create(owner=self.owner,provider='claude',label='Synthetic')
        self.row=chat._conversation(self.owner,self.account,create=True);self.client.force_login(self.owner)

    def test_acknowledged_failure_survives_login_is_scoped_and_opt_in_without_provider_content(self):
        token=chat.claim_worker(self.owner,self.row.pk)
        safe={'kind':'rate_limit','resets_at_ms':1793313000000}
        chat.worker_update(self.owner,self.row.pk,token,failure=safe,phase='unavailable')
        Conversation.objects.filter(pk=self.row.pk).update(worker=None)
        headers={'HTTP_X_MAIPA_AGENT_ACTOR':str(self.owner.pk)};url='/api/agent/chat/'
        self.assertNotIn('failure',self.client.get(url,**headers).json())
        self.assertEqual(self.client.get(url,HTTP_X_MAIPA_AGENT_FAILURES='1',**headers).json()['failure'],safe)
        self.client.logout();self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url,HTTP_X_MAIPA_AGENT_FAILURES='1',**headers).json()['failure'],safe)
        self.client.force_login(self.other)
        self.assertIsNone(self.client.get(url,HTTP_X_MAIPA_AGENT_FAILURES='1',HTTP_X_MAIPA_AGENT_ACTOR=str(self.other.pk)).json()['failure'])
        self.assertEqual(self.client.get(url,HTTP_X_MAIPA_AGENT_FAILURES='1',**headers).status_code,409)

    def test_cooldown_rejects_new_sends_without_queueing_and_expiry_allows_one_accepted_send(self):
        reset=int((timezone.now()+timedelta(hours=1)).timestamp()*1000)
        Conversation.objects.filter(pk=self.row.pk).update(failure={'kind':'rate_limit','resets_at_ms':reset})
        before=chat.snapshot(self.owner);request=str(uuid.uuid4())
        with patch('backend.maipa.agent_accounts.chat_runtime.start') as started:
            with self.assertRaises(chat.ChatUnavailable):chat.send(self.owner,before['revision'],request,'Synthetic new draft')
            started.assert_not_called();self.row.refresh_from_db();self.assertEqual(self.row.messages,[]);self.assertEqual(self.row.requests,[])
            Conversation.objects.filter(pk=self.row.pk).update(failure={'kind':'rate_limit','resets_at_ms':reset-7_200_000})
            result=chat.send(self.owner,before['revision'],request,'Synthetic new draft')
            self.assertEqual(len(result['messages']),1);self.assertIsNone(result['failure']);started.assert_called_once()
            Conversation.objects.filter(pk=self.row.pk).update(phase='unavailable',failure={'kind':'rate_limit','resets_at_ms':reset})
            started.reset_mock()
            replay=chat.send(self.owner,before['revision'],request,'Synthetic new draft')
            self.assertEqual(len(replay['messages']),1);started.assert_not_called()

    def test_worker_cannot_persist_arbitrary_reason_unknown_kinds_or_foreign_failure(self):
        token=chat.claim_worker(self.owner,self.row.pk)
        for value in ({'kind':'rate_limit','reason':'PRIVATE_REASON'},{'kind':'protocol'},{'kind':'rate_limit','resets_at_ms':True}):
            with self.assertRaises(ValueError):chat.worker_update(self.owner,self.row.pk,token,failure=value)
        with self.assertRaises((chat.ChatUnavailable,ObjectDoesNotExist)):chat.worker_update(self.other,self.row.pk,token,failure={'kind':'billing'})
        self.row.refresh_from_db();self.assertIsNone(self.row.failure)

    def test_failure_remains_with_selected_account_and_legacy_sse_excludes_optional_status(self):
        Conversation.objects.filter(pk=self.row.pk).update(failure={'kind':'billing'})
        before=chat.snapshot(self.owner)
        changed=chat.select_provider(self.owner,before['connection']['revision'],'codex')
        self.assertIsNone(changed['failure'])
        restored=chat.select_provider(self.owner,changed['connection']['revision'],'claude')
        self.assertEqual(restored['failure'],{'kind':'billing'})
        headers={'HTTP_X_MAIPA_AGENT_ACTOR':str(self.owner.pk)}
        for optin in (False,True):
            response=self.client.get('/api/agent/chat/stream/',**headers,**({'HTTP_X_MAIPA_AGENT_FAILURES':'1'} if optin else {}))
            content=response.streaming_content
            try:
                frame=next(content).decode()
                self.assertEqual('"failure"' in frame,optin)
                self.assertNotIn('PRIVATE',frame)
            finally:response.close()
