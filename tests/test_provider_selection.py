"""Provider-neutral app selection, with no claim of a generic native driver."""
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend.maipa.agent_accounts import chat
from backend.maipa.agent_accounts.models import AgentAccount, Conversation, ProviderProfile


@override_settings(ACP_PROVIDER_LABELS={'independent-acp': 'Independent ACP'})
class ProviderSelectionTests(TestCase):
    def setUp(self):
        from tests.test_agent_chat import AgentChatTests
        AgentChatTests.setUp(self)

    def test_first_connection_requires_a_choice_and_never_creates_a_codex_account(self):
        AgentAccount.objects.filter(owner=self.other).delete()
        state = chat.snapshot(self.other)
        self.assertIsNone(state['connection']['provider'])
        with patch.object(chat, 'start_worker') as start:
            with self.assertRaises(chat.ChatUnavailable):
                chat.connect(self.other, 0, session_key='synthetic')
            start.assert_not_called()
        self.assertFalse(AgentAccount.objects.filter(owner=self.other).exists())

    def test_third_registered_provider_is_owned_and_truthfully_refuses_missing_runtime(self):
        before = chat.snapshot(self.owner)
        state = chat.select_provider(self.owner, before['connection']['revision'], 'independent-acp')
        account = AgentAccount.objects.get(pk=state['connection']['account_id'])
        self.assertEqual((account.owner_id, account.provider), (self.owner.pk, 'independent-acp'))
        self.assertEqual(state['connection']['label'], 'Independent ACP')
        self.assertFalse(state['connection']['available'])
        with patch.object(chat, 'start_worker') as start:
            for operation in [lambda: chat.connect(self.owner, 0, session_key='synthetic'),
                              lambda: chat.send(self.owner, 0, str(uuid.uuid4()), 'Private draft')]:
                with self.assertRaises(chat.ChatUnavailable):
                    operation()
            start.assert_not_called()
        self.assertFalse(Conversation.objects.filter(account=account).exists())
        self.assertFalse(ProviderProfile.objects.filter(account=account).exists())

    def test_switch_back_preserves_transcript_and_rejects_a_stale_selection_even_at_same_chat_revision(self):
        with patch.object(chat, 'start_worker'):
            original = chat.send(self.owner, 0, str(uuid.uuid4()), 'Keep original conversation')
        Conversation.objects.filter(pk=original['id']).update(phase='offline')
        revision = original['connection']['revision']
        other = chat.select_provider(self.owner, revision, 'independent-acp')
        self.assertEqual(other['messages'], [])
        restored = chat.select_provider(self.owner, other['connection']['revision'], 'codex')
        self.assertEqual(restored['id'], original['id'])
        self.assertEqual(restored['messages'], original['messages'])
        with patch.object(chat, 'start_worker') as start:
            with self.assertRaises(chat.ChatConflict):
                chat.send(self.owner, restored['revision'], str(uuid.uuid4()), 'Stale tab draft',
                          selection_revision=revision)
            start.assert_not_called()
        self.assertEqual(chat.snapshot(self.owner)['messages'], original['messages'])

    def test_live_worker_and_unfinished_connection_prevent_provider_switch(self):
        with patch.object(chat, 'start_worker'):
            state = chat.connect(self.owner, 0, session_key='synthetic')
        with self.assertRaises(chat.ChatConflict):
            chat.select_provider(self.owner, state['connection']['revision'], 'independent-acp')
        Conversation.objects.filter(pk=state['id']).update(phase='ready', worker=uuid.uuid4())
        with self.assertRaises(chat.ChatConflict):
            chat.select_provider(self.owner, state['connection']['revision'], 'independent-acp')
        self.assertFalse(AgentAccount.objects.filter(owner=self.owner, provider='independent-acp').exists())

    def test_unknown_provider_and_browser_command_are_rejected_without_creating_accounts(self):
        state = chat.snapshot(self.owner)
        with self.assertRaises(chat.ChatUnavailable):
            chat.select_provider(self.owner, state['connection']['revision'], 'not-registered')
        self.client.force_login(self.owner)
        response = self.client.post('/api/agent/chat/', {'action': 'select_provider',
            'selection_revision': state['connection']['revision'], 'provider': 'independent-acp',
            'command': 'untrusted'}, content_type='application/json',
            HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(AgentAccount.objects.filter(owner=self.owner).count(), 1)

    def test_selection_persists_and_foreign_or_disabled_account_is_never_adopted(self):
        state = chat.select_provider(self.owner, 0, 'independent-acp')
        fresh = get_user_model().objects.get(pk=self.owner.pk)
        self.assertEqual(chat.snapshot(fresh)['connection'], state['connection'])
        self.assertIsNone(chat.snapshot(self.other)['connection']['account_id'])
        from backend.maipa.agent_accounts.models import ChatSelection
        ChatSelection.objects.filter(user=self.other).update(account_id=state['connection']['account_id'])
        with self.assertRaises(chat.ChatUnavailable):
            chat.snapshot(self.other)
        AgentAccount.objects.filter(pk=state['connection']['account_id']).update(enabled=False)
        with self.assertRaises(chat.ChatUnavailable):
            chat.snapshot(self.owner)

    def test_http_mutation_requires_current_selection_and_does_not_route_old_payloads(self):
        state = chat.snapshot(self.owner)
        self.client.force_login(self.owner)
        headers = {'HTTP_X_MAIPA_AGENT_ACTOR': str(self.owner.pk)}
        with patch.object(chat, 'start_worker') as start:
            legacy = self.client.post('/api/agent/chat/', {'action': 'connect', 'revision': 0},
                content_type='application/json', **headers)
            self.assertEqual(legacy.status_code, 400)
            chat.select_provider(self.owner, state['connection']['revision'], 'independent-acp')
            stale = self.client.post('/api/agent/chat/', {'action': 'connect', 'revision': 0,
                'selection_revision': state['connection']['revision']}, content_type='application/json', **headers)
            self.assertEqual(stale.status_code, 409)
            start.assert_not_called()

    def test_removed_registration_keeps_original_metadata_and_refuses_connection(self):
        state = chat.select_provider(self.owner, 0, 'independent-acp')
        with override_settings(ACP_PROVIDER_LABELS={}):
            saved = chat.snapshot(self.owner)
            self.assertEqual(saved['connection']['account_id'], state['connection']['account_id'])
            self.assertFalse(saved['connection']['available'])
            with self.assertRaises(chat.ChatUnavailable):
                chat.connect(self.owner, 0, session_key='synthetic')

    def test_upgrade_retains_original_account_profile_session_and_transcript_ids(self):
        from importlib import import_module
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        from backend.maipa.agent_accounts.models import ChatSelection, SessionReference
        from backend.maipa.agent_accounts.profiles import provision_profile
        from backend.maipa.agent_accounts.references import record_session
        profile = provision_profile(self.owner, str(self.account.pk))
        reference = record_session(self.owner, str(self.account.pk), 'synthetic-native-session')
        with patch.object(chat, 'start_worker'):
            saved = chat.send(self.owner, 0, str(uuid.uuid4()), 'Keep original private conversation')
        ChatSelection.objects.filter(user=self.owner).delete()
        AgentAccount.objects.create(owner=self.owner, provider='claude', label='Other owned connection')
        migration = import_module('backend.maipa.agent_accounts.migrations.0007_provider_selection')
        migration.preserve_existing_selection(apps, SimpleNamespace(connection=connection))
        self.assertEqual(ChatSelection.objects.get(user=self.owner).account_id, self.account.pk)
        self.assertEqual(str(ProviderProfile.objects.get(account=self.account).pk), profile)
        self.assertEqual(str(SessionReference.objects.get(account=self.account).pk), reference)
        self.assertEqual(chat.snapshot(self.owner)['id'], saved['id'])
        self.assertEqual(chat.snapshot(self.owner)['messages'], saved['messages'])
