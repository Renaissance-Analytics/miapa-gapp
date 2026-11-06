"""Private durable drafts never constitute a provider turn or authority."""
import json,uuid
from unittest.mock import patch
from django.test import TestCase,Client,TransactionTestCase
from tests import test_agent_chat as existing
from backend.maipa.agent_accounts.models import AgentAccount,Conversation
from backend.maipa.workspace.models import Membership

def draft(text='Unsent Ω'):
    return {'v':1,'text':text,'pills':[{'kind':'text','id':'held','text':'Full pasted text'}],'attachments':[]}

class ChatDraftStoreTests(TestCase):
    setUp=existing.AgentChatTests.setUp
    def api(self):
        from backend.maipa.agent_accounts import draft_store
        return draft_store
    def test_saved_full_draft_and_explicit_empty_restore_without_provider_side_effects(self):
        api=self.api();key=str(self.account.pk);initial=api.read(self.owner,key)
        self.assertEqual(initial['revision'],0)
        saved=api.save(self.owner,key,0,draft(),None)
        self.assertEqual(api.read(self.owner,key),saved);self.assertEqual(saved['draft'],draft())
        empty={'v':1,'text':'','pills':[],'attachments':[]}
        self.assertEqual(api.save(self.owner,key,1,empty,None)['draft'],empty)
        self.assertEqual(api.read(self.owner,key)['revision'],2);self.assertFalse(Conversation.objects.exists())
    def test_unselected_and_other_accounts_are_separate_and_reassignment_refuses(self):
        api=self.api();key=str(self.account.pk);api.save(self.owner,key,0,draft('Account'),None)
        api.save(self.owner,None,0,draft('Unselected'),None)
        second=AgentAccount.objects.create(owner=self.owner,provider='claude',label='Claude')
        self.assertEqual(api.read(self.owner,str(second.pk))['draft']['text'],'')
        self.assertEqual(api.read(self.owner,None)['draft']['text'],'Unselected')
        with self.assertRaises(api.DraftUnavailable):api.read(self.other,key)
        AgentAccount.objects.filter(pk=self.account.pk).update(owner=self.other)
        with self.assertRaises(api.DraftUnavailable):api.read(self.other,key)
        AgentAccount.objects.filter(pk=self.account.pk).update(owner=self.owner)
        self.assertEqual(api.read(self.owner,key)['draft']['text'],'Account')
    def test_stale_write_preserves_newer_draft_and_invalid_shapes_refuse(self):
        api=self.api();key=str(self.account.pk);first=api.save(self.owner,key,0,draft('Newer'),None)
        with self.assertRaises(api.DraftConflict):api.save(self.owner,key,0,draft('Stale'),None)
        for invalid in ({**draft(),'v':2},{**draft(),'actor':1},{**draft(),'text':'x'*65537},
                {**draft(),'attachments':[{'id':'f','name':'f','type':'','bytes':1,'ref':'file:///private'}]}):
            with self.assertRaises(ValueError):api.save(self.owner,key,1,invalid,None)
        self.assertEqual(api.read(self.owner,key),first)
    def test_saved_retry_identity_resolves_actual_accepted_request_without_duplicate(self):
        from backend.maipa.agent_accounts import chat
        api=self.api();key=str(self.account.pk);pending={'id':str(uuid.uuid4()),'text':'Send once','draft':draft('Send once')}
        api.save(self.owner,key,0,pending['draft'],pending)
        self.assertFalse(api.read(self.owner,key)['accepted'])
        with patch.object(chat,'start_worker') as start:
            chat.send(self.owner,0,pending['id'],pending['text'])
            restored=api.read(self.owner,key);self.assertTrue(restored['accepted']);self.assertEqual(restored['pending'],pending)
            chat.send(self.owner,0,pending['id'],pending['text']);self.assertEqual(start.call_count,1)
        self.assertEqual(len(Conversation.objects.get().messages),1)
        self.assertEqual(api.read(self.owner,key)['draft'],pending['draft'])
    def test_current_role_storage_and_original_installation_are_rechecked(self):
        api=self.api();key=str(self.account.pk);api.save(self.owner,key,0,draft(),None)
        Membership.objects.filter(user=self.owner).update(role='viewer')
        with self.assertRaises(api.DraftUnavailable):api.read(self.owner,key)
        Membership.objects.filter(user=self.owner).update(role='preparer')
        with patch('backend.maipa.agent_accounts.draft_store.installation_metadata',return_value={'storage_id':str(uuid.uuid4())}):
            with self.assertRaises(api.DraftUnavailable):api.read(self.owner,key)
        self.assertEqual(api.read(self.owner,key)['draft'],draft())
    def test_http_requires_current_actor_csrf_and_exact_body(self):
        key=str(self.account.pk);url='/api/agent/drafts/'+key+'/'
        self.assertEqual(self.client.get(url).status_code,401);self.client.force_login(self.owner)
        self.assertEqual(self.client.get(url).status_code,409)
        headers={'HTTP_X_MAIPA_AGENT_ACTOR':str(self.owner.pk)}
        self.assertEqual(self.client.get(url,**headers).status_code,200)
        guarded=Client(enforce_csrf_checks=True);guarded.force_login(self.owner)
        command={'expected_revision':0,'draft':draft(),'pending':None}
        self.assertEqual(guarded.post(url,data=json.dumps(command),content_type='application/json',**headers).status_code,403)
        self.assertEqual(self.client.post(url,data=json.dumps({**command,'account_id':key}),content_type='application/json',**headers).status_code,400)
        self.assertEqual(self.client.post(url,data=json.dumps(command),content_type='application/json',**headers).status_code,200)
        self.assertEqual(self.client.post(url,data=json.dumps(command),content_type='application/json',**headers).status_code,409)
    def test_file_metadata_is_honest_and_forged_pending_receipts_are_rejected(self):
        api=self.api();key=str(self.account.pk);value={**draft(),'attachments':[{'id':'f','name':'workpaper.pdf','type':'application/pdf','bytes':12}]}
        self.assertEqual(api.save(self.owner,key,0,value,None)['draft'],value)
        with self.assertRaises(ValueError):api.save(self.owner,key,1,value,{'id':str(uuid.uuid4()),'text':'X','draft':draft(),'accepted':True})
        self.assertFalse(api.read(self.owner,key)['accepted']);self.assertFalse(Conversation.objects.exists())


class ChatDraftMigrationTests(TransactionTestCase):
    setUp=existing.AgentChatTests.setUp
    def test_downgrade_refuses_before_removing_saved_draft_or_migration_record(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from backend.maipa.agent_accounts import draft_store
        key=str(self.account.pk);saved=draft_store.save(self.owner,key,0,draft(),None)
        try:
            with self.assertRaisesRegex(RuntimeError,'Saved chat drafts require offline backup recovery'):
                MigrationExecutor(connection).migrate([('agent_accounts','0009_uicomposition_form_drafts')])
            self.assertIn(('agent_accounts','0010_chatdraftrecord'),MigrationExecutor(connection).loader.applied_migrations)
        finally:
            executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes())
        self.assertEqual(draft_store.read(self.owner,key),saved)
        self.assertIn(('agent_accounts','0010_chatdraftrecord'),MigrationExecutor(connection).loader.applied_migrations)


class ChatDraftConcurrencyTests(TransactionTestCase):
    setUp=existing.AgentChatTests.setUp
    def test_save_waits_for_another_owned_writer_without_losing_the_draft(self):
        import threading
        from django.db import connections,transaction
        from backend.maipa.agent_accounts import draft_store
        key=str(self.account.pk);draft_store.save(self.owner,key,0,draft('First'),None)
        held=threading.Event();release=threading.Event();errors=[]
        def writer():
            try:
                with transaction.atomic():
                    Membership.objects.filter(user_id=self.other.pk).update(role='preparer')
                    held.set();release.wait(2)
            except Exception as error:errors.append(type(error).__name__)
            finally:connections.close_all()
        thread=threading.Thread(target=writer);thread.start();self.assertTrue(held.wait(2));timer=threading.Timer(.2,release.set);timer.start()
        try:saved=draft_store.save(self.owner,key,1,draft('Keep this draft'),None)
        finally:release.set();thread.join(3);timer.cancel()
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assertEqual(saved['revision'],2);self.assertEqual(saved['draft']['text'],'Keep this draft')
    def test_windows_pasted_text_line_endings_restore_exactly_without_sending(self):
        from backend.maipa.agent_accounts import draft_store
        value=draft('Windows line\r\nSecond line');value['pills'][0]['text']='Pasted Windows\r\ntext'
        saved=draft_store.save(self.owner,str(self.account.pk),0,value,None)
        self.assertEqual(saved['draft'],value);self.assertFalse(Conversation.objects.exists())

    def test_saved_view_reorder_waits_for_concurrent_draft_writer(self):
        import threading
        from django.db import connections,transaction
        from backend.maipa.workspace.personal_views import read_personal_views,mutate_personal_views
        state=read_personal_views(self.owner)
        for title in ('First','Second'):
            state=mutate_personal_views(self.owner,state['views_id'],{'action':'create','expected_revision':state['revision'],'title':title,'schema':{'id':'note','type':'Maipa.Note','props':{'text':'Synthetic'}}})
        expected=[v['id'] for v in reversed(state['views'])];held=threading.Event();release=threading.Event();errors=[]
        def writer():
            try:
                with transaction.atomic():
                    Membership.objects.filter(user_id=self.other.pk).update(role='preparer');held.set();release.wait(2)
            except Exception as error:errors.append(type(error).__name__)
            finally:connections.close_all()
        thread=threading.Thread(target=writer);thread.start();self.assertTrue(held.wait(2));timer=threading.Timer(.2,release.set);timer.start()
        try:saved=mutate_personal_views(self.owner,state['views_id'],{'action':'reorder','expected_revision':state['revision'],'ids':expected})
        finally:release.set();thread.join(3);timer.cancel()
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(saved['revision'],3);self.assertEqual([v['id'] for v in saved['views']],expected)
