"""Conversation acceptance: persistence, actor isolation, concurrency and shared UI."""
import uuid
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from backend.maipa.storage import prepare_directory, prepare_files

from backend.maipa.agent_accounts.models import AgentAccount
from backend.maipa.workspace.models import Installation, Membership


class AgentChatTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='maipa-chat-store-')
        self.addCleanup(temporary.cleanup)
        data = prepare_directory(Path(temporary.name).resolve() / 'install')
        prepare_files(data)
        configured = override_settings(DATA_ROOT=data)
        configured.enable()
        self.addCleanup(configured.disable)
        self.installation = Installation.objects.create()
        self.owner = get_user_model().objects.create_user(username='chat-owner')
        self.other = get_user_model().objects.create_user(username='chat-other')
        self.member = Membership.objects.create(user=self.owner, installation=self.installation, role='preparer')
        Membership.objects.create(user=self.other, installation=self.installation, role='preparer')
        self.account = AgentAccount.objects.create(owner=self.owner, provider='codex', label='Codex')

    def api(self):
        from backend.maipa.agent_accounts import chat
        return chat

    def test_empty_chat_has_no_predefined_composition_or_provider_readiness(self):
        state = self.api().snapshot(self.owner)
        self.assertEqual(state['messages'], [])
        self.assertIsNone(state['ui'])
        self.assertEqual(state['phase'], 'offline')

    def test_full_worker_capacity_keeps_queued_message_and_allows_explicit_reconnect(self):
        from backend.maipa.agent_accounts import chat_runtime
        api = self.api()
        identity = str(uuid.uuid4())
        with patch.dict(chat_runtime._workers, {object(): object() for _ in range(16)}, clear=True):
            with self.assertRaises(api.ChatUnavailable):
                api.send(self.owner, 0, identity, 'Keep this request until a connection is available', session_key='synthetic')
            state = api.snapshot(self.owner)
            self.assertEqual(state['phase'], 'unavailable')
            self.assertEqual(state['messages'][0]['id'], identity)
            self.assertEqual(state['messages'][0]['state'], 'queued')
        with patch.object(api, 'start_worker') as start:
            resumed = api.connect(self.owner, state['revision'], session_key='synthetic')
            self.assertEqual(resumed['phase'], 'connecting')
            self.assertEqual(resumed['messages'], state['messages'])
            start.assert_called_once()

    def test_acknowledged_turn_is_durable_and_duplicate_is_not_run_twice(self):
        api = self.api()
        request_id = str(uuid.uuid4())
        with patch.object(api, 'start_worker') as start:
            state = api.send(self.owner, 0, request_id, 'Please show my client facts')
            self.assertEqual(state['messages'][0]['text'], 'Please show my client facts')
            self.assertEqual(state['phase'], 'connecting')
            duplicate = api.send(self.owner, 0, request_id, 'Please show my client facts')
            self.assertEqual(duplicate, state)
            self.assertEqual(start.call_count, 1)
        self.assertEqual(api.snapshot(self.owner)['messages'], state['messages'])
        self.assertEqual(api.snapshot(self.other)['messages'], [])

    def test_stale_or_changed_authority_cannot_submit_or_replay(self):
        api = self.api()
        with patch.object(api, 'start_worker'):
            api.send(self.owner, 0, str(uuid.uuid4()), 'First')
            with self.assertRaises(api.ChatConflict):
                api.send(self.owner, 0, str(uuid.uuid4()), 'Stale second turn')
            Membership.objects.filter(pk=self.member.pk).update(role='viewer')
            with self.assertRaises(api.ChatUnavailable):
                api.snapshot(self.owner)
        Membership.objects.filter(pk=self.member.pk).update(role='preparer')
        self.assertEqual(len(api.snapshot(self.owner)['messages']), 1)

    def test_http_requires_actor_and_csrf_and_does_not_accept_provider_or_commands(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get('/api/agent/chat/').status_code, 409)
        response = self.client.get('/api/agent/chat/', HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
        self.assertEqual(response.status_code, 200)
        response = self.client.post('/api/agent/chat/', {'action': 'send', 'revision': 0,
            'request_id': str(uuid.uuid4()), 'text': 'x', 'binary': 'anything'},
            content_type='application/json', HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
        self.assertEqual(response.status_code, 400)

    def test_original_owner_and_storage_binding_cannot_be_adopted_after_reassignment(self):
        api = self.api()
        with patch.object(api, 'start_worker'):
            api.send(self.owner, 0, str(uuid.uuid4()), 'Original private transcript')
        AgentAccount.objects.filter(pk=self.account.pk).update(owner=self.other)
        with self.assertRaises(api.ChatUnavailable):
            api.snapshot(self.other)
        AgentAccount.objects.filter(pk=self.account.pk).update(owner=self.owner)
        with patch('backend.maipa.agent_accounts.references.installation_metadata', return_value={'storage_id': str(uuid.uuid4())}):
            with self.assertRaises(api.ChatUnavailable):
                api.snapshot(self.owner)
        self.assertEqual(api.snapshot(self.owner)['messages'][0]['text'], 'Original private transcript')
