"""A worker transition cannot add a different phase/account's transient fields."""
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings

from backend.maipa.agent_accounts import chat, chat_runtime
from backend.maipa.agent_accounts.models import AgentAccount, Conversation
from backend.maipa.storage import prepare_directory, prepare_files
from backend.maipa.workspace.models import Installation, Membership

MODELS={'currentModelId':'synthetic-a','availableModels':[{'modelId':'synthetic-a','name':'Synthetic A'}]}
LOGIN={'url':'https://identity.example.test/authorize','accepts_code':True}


class ChatSnapshotConsistencyTests(TransactionTestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='maipa-chat-snapshot-');self.addCleanup(temporary.cleanup)
        data=prepare_directory(Path(temporary.name)/'install');prepare_files(data)
        config=override_settings(DATA_ROOT=data);config.enable();self.addCleanup(config.disable)
        installation=Installation.objects.create();self.owner=get_user_model().objects.create_user(username='snapshot-owner')
        Membership.objects.create(user=self.owner,installation=installation,role='preparer')
        self.account=AgentAccount.objects.create(owner=self.owner,provider='claude',label='Synthetic snapshot account')
        self.client.force_login(self.owner);self.row=chat._conversation(self.owner,self.account,create=True)
        self.worker=SimpleNamespace(session_key=self.client.session.session_key,stopping=threading.Event(),
            identity=self.row.pk,account_id=self.account.pk,models=MODELS,consent=LOGIN,check=lambda:None)
        worker_state=patch.dict(chat_runtime._workers,{self.owner.pk:self.worker});worker_state.start();self.addCleanup(worker_state.stop)
        self.headers={'HTTP_X_MAIPA_AGENT_ACTOR':str(self.owner.pk),'HTTP_X_MAIPA_AGENT_MODELS':'1'}

    def snapshot(self,phase,stream=False):
        Conversation.objects.filter(pk=self.row.pk).update(phase=phase)
        response=self.client.get('/api/agent/chat/stream/' if stream else '/api/agent/chat/',**self.headers)
        self.assertEqual(response.status_code,200)
        if not stream:return response.json()
        try:return json.loads(next(iter(response.streaming_content)).decode().split('data: ',1)[1])
        finally:response.close()

    def test_new_worker_models_do_not_corrupt_an_older_offline_or_stopping_snapshot(self):
        for phase in ('offline','unavailable','stopping'):
            with self.subTest(phase=phase):
                state=self.snapshot(phase);self.assertNotIn('models',state);self.assertNotIn('login',state)

    def test_sign_in_snapshot_keeps_its_challenge_without_ready_models(self):
        state=self.snapshot('sign_in',stream=True)
        self.assertEqual(state['login'],LOGIN);self.assertNotIn('models',state)
        self.assertNotIn('login',self.snapshot('sign_in'))

    def test_ready_snapshot_keeps_models_without_a_late_sign_in_challenge(self):
        state=self.snapshot('ready',stream=True)
        self.assertEqual(state['models'],MODELS);self.assertNotIn('login',state)
        self.assertEqual(state['connection']['account_id'],str(self.account.pk))

    def test_a_different_worker_identity_or_account_cannot_attach_transient_fields(self):
        for attribute in ('identity','account_id'):
            original=getattr(self.worker,attribute);setattr(self.worker,attribute,uuid.uuid4())
            try:
                state=self.snapshot('ready',stream=True)
                self.assertNotIn('models',state);self.assertNotIn('login',state)
            finally:setattr(self.worker,attribute,original)
