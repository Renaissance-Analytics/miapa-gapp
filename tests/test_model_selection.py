"""Independent provider-owned model catalog and original-session model controls."""
import asyncio,json,sys,tempfile,time,uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from django.contrib.auth import get_user_model
from django.test import Client,TransactionTestCase,override_settings
from backend.maipa.acp import AcpClient,AcpError,Scope
from backend.maipa.agent_accounts import chat_runtime
from backend.maipa.agent_accounts.models import AgentAccount,Conversation
from backend.maipa.storage import prepare_directory,prepare_files
from backend.maipa.workspace.models import Installation,Membership

PEER=Path(__file__).parent/'fixtures/model_peer.py'
CATALOG={'currentModelId':'model-a','availableModels':[{'modelId':'model-a','name':'Model A'},{'modelId':'model-b','name':'Model B'}]}

class ModelClientTests(unittest.TestCase):
    def test_no_advertised_models_cannot_enable_invented_model_selection(self):
        async def body(client,scope):
            session=await client.new_session(scope)
            self.assertIsNone(client.model_state(scope,session))
            with self.assertRaises(ValueError):await client.set_model(scope,session,'model-a')
        self.case(body,synthetic_empty_models=True)

    def case(self,body,**flags):
        async def run():
            with tempfile.TemporaryDirectory() as temporary:
                scope=Scope('model-owner','model-account');workspace=Path(temporary)
                profile={'user_id':scope.user_id,'account_id':scope.account_id,'workspace':str(workspace),**flags}
                client=AcpClient((sys.executable,str(PEER)),scope,workspace,env={'MAIPA_ACP_PROFILE':json.dumps(profile)},timeout=3)
                await client.start()
                try:await body(client,scope)
                finally:await client.close()
                self.assertIsNotNone(client.returncode)
        asyncio.run(run())

    def test_catalog_and_model_switch_are_provider_reported_scoped_and_detached(self):
        async def body(client,scope):
            session=await client.new_session(scope)
            self.assertEqual(client.model_state(scope,session),CATALOG)
            detached=client.model_state(scope,session);detached['availableModels'].clear()
            details=client.session_details(scope,session);details['models']['availableModels'].clear()
            self.assertEqual(client.model_state(scope,session),CATALOG)
            for foreign in (Scope('other',scope.account_id),Scope(scope.user_id,'other')):
                with self.assertRaises(PermissionError):client.model_state(foreign,session)
                with self.assertRaises(PermissionError):await client.set_model(foreign,session,'model-b')
            for model in ('foreign-model','',None):
                with self.assertRaises(ValueError):await client.set_model(scope,session,model)
            await client.set_model(scope,session,'model-b')
            self.assertEqual(client.model_state(scope,session),{**CATALOG,'currentModelId':'model-b'})
        self.case(body)

    def test_malformed_model_catalog_never_becomes_a_selectable_session(self):
        async def body(client,scope):
            with self.assertRaises(AcpError):await client.new_session(scope)
        self.case(body,synthetic_malformed_models=True)

    def test_provider_refusal_keeps_the_confirmed_model_and_does_not_claim_success(self):
        async def body(client,scope):
            session=await client.new_session(scope)
            with self.assertRaises(AcpError):await client.set_model(scope,session,'model-b')
            self.assertEqual(client.model_state(scope,session),CATALOG)
        self.case(body,synthetic_model_refusal=True)

class ModelSelectionTests(TransactionTestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='maipa-model-selection-');self.root=Path(self.temporary.name).resolve()
        data=prepare_directory(self.root/'install');prepare_files(data)
        self.config=override_settings(DATA_ROOT=data);self.config.enable()
        install=Installation.objects.create();self.owner=get_user_model().objects.create_user(username='model-owner')
        Membership.objects.create(user=self.owner,installation=install,role='preparer')
        self.account=AgentAccount.objects.create(owner=self.owner,provider='claude',label='Synthetic model account')
        self.client.force_login(self.owner);self.flags={};self.workers=[]
        def prepare(worker):
            worker.check();self.workers.append(worker);work=self.root/'workspace';work.mkdir(exist_ok=True)
            scope=Scope(str(worker.owner),str(worker.account_id));worker.lease=SimpleNamespace(scope=scope,revalidate=lambda:None)
            return SimpleNamespace(command=(sys.executable,str(PEER)),workspace=work,
                profile={'user_id':scope.user_id,'account_id':scope.account_id,'workspace':str(work),'provider':'claude',**self.flags}),None
        self.prepared=patch.object(chat_runtime._Worker,'prepare',prepare);self.prepared.start()

    def tearDown(self):
        for worker in self.workers:worker.stopping.set()
        for worker in self.workers:
            worker.thread.join(10);self.assertFalse(worker.thread.is_alive())
        self.prepared.stop();self.config.disable();self.temporary.cleanup();super().tearDown()

    def state(self,client=None):
        response=(client or self.client).get('/api/agent/chat/',HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk),HTTP_X_MAIPA_AGENT_MODELS='1')
        self.assertEqual(response.status_code,200);return response.json()

    def post(self,payload,client=None):
        return (client or self.client).post('/api/agent/chat/',payload,content_type='application/json',HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))

    def connect(self):
        state=self.state();self.assertEqual(self.post({'action':'connect','revision':state['revision'],'selection_revision':state['connection']['revision']}).status_code,200)
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            state=self.state()
            if state['phase']=='ready':return state
            time.sleep(.02)
        self.fail('Finite model peer did not become ready')

    def change(self,state,model='model-b',client=None):
        return self.post({'action':'set_model','revision':state['revision'],'selection_revision':state['connection']['revision'],'model_id':model},client)

    def test_owned_switch_confirms_provider_model_without_replacing_chat_or_ui(self):
        before=self.connect();self.assertEqual(before['models'],CATALOG)
        after=self.change(before);self.assertEqual(after.status_code,200);after=after.json()
        self.assertEqual(after['models'],{**CATALOG,'currentModelId':'model-b'})
        for key in ('id','messages','ui','connection'):self.assertEqual(after[key],before[key])
        self.assertGreater(after['revision'],before['revision'])
        self.assertEqual(self.change(before).status_code,409)
        self.assertEqual(self.change(after,'foreign-model').status_code,400)
        self.assertEqual(self.state()['models'],after['models'])

    def test_older_browser_contract_does_not_receive_unknown_model_fields(self):
        before=self.connect();self.assertEqual(before['models'],CATALOG)
        old=self.client.get('/api/agent/chat/',HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
        self.assertEqual(old.status_code,200);self.assertNotIn('models',old.json())
        self.assertEqual(old.json()['id'],before['id'])
        stream=self.client.get('/api/agent/chat/stream/',HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
        try:
            first=next(iter(stream.streaming_content)).decode()
            self.assertTrue(first.startswith('event: state\n'))
            self.assertNotIn('models',json.loads(first.split('data: ',1)[1]))
        finally:stream.close()
        self.assertEqual(self.state()['models'],CATALOG)

    def test_foreign_browser_session_and_busy_turn_cannot_change_live_model(self):
        before=self.connect();other=Client();other.force_login(self.owner)
        self.assertNotIn('models',self.state(other))
        self.assertEqual(self.change(before,client=other).status_code,503)
        Conversation.objects.filter(pk=before['id']).update(phase='thinking')
        self.assertEqual(self.change(before).status_code,409)
        Conversation.objects.filter(pk=before['id']).update(phase='ready')
        self.assertEqual(self.state()['models'],CATALOG)
        Conversation.objects.filter(pk=before['id']).update(messages=[{'id':str(uuid.uuid4()),'role':'user','state':'queued','text':'Synthetic queued input'}])
        self.assertEqual(self.change(before).status_code,409)

    def test_native_refusal_cannot_report_the_requested_model_as_selected(self):
        self.flags['synthetic_model_refusal']=True;before=self.connect()
        response=self.change(before);self.assertEqual(response.status_code,503)
        self.assertNotEqual(response.json(),{'currentModelId':'model-b'})
        for worker in self.workers:worker.thread.join(10)
        state=self.state();self.assertNotIn('models',state)
        self.assertIn(state['phase'],('offline','unavailable'))
