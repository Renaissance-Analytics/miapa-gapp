"""Owned display metadata has its own revision, never connection authority."""
import json
import uuid
from unittest.mock import patch

from django.db import DatabaseError
from django.test import Client, TestCase, TransactionTestCase

from backend.maipa.agent_accounts.models import (
    AgentAccount, ChatSelection, Conversation, ProviderProfile, SessionReference,
)
from backend.maipa.storage import installation_metadata
from backend.maipa.workspace.models import Installation, Membership
from tests import test_ui_composition as composition


class AccountMetadataTests(TestCase):
    setUp = composition.UiCompositionTests.setUp

    def url(self, account=None):
        return f'/api/agent/accounts/{account or self.account.pk}/metadata/'

    def get(self):
        self.client.force_login(self.owner)
        return self.client.get(self.url(), HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk))

    def post(self, value, *, client=None, actor=None, account=None):
        current = client or self.client
        current.force_login(actor or self.owner)
        return current.post(self.url(account), value, content_type='application/json',
                            HTTP_X_MAIPA_AGENT_ACTOR=str((actor or self.owner).pk))

    def events(self):
        from backend.maipa.agent_accounts.models import AccountMetadataEvent
        return AccountMetadataEvent.objects

    def test_read_is_owned_closed_metadata_without_preparing_a_provider(self):
        before = list(SessionReference.objects.values())
        with patch('backend.maipa.agent_accounts.chat.start_worker') as worker:
            response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'user_id': str(self.owner.pk), 'account_id': str(self.account.pk),
            'provider': 'codex', 'label': 'Synthetic UI account',
            'profile_assigned': False, 'revision': 0,
        })
        self.assertIn('no-store', response.headers['Cache-Control'])
        self.assertEqual(list(SessionReference.objects.values()), before)
        self.assertFalse(ProviderProfile.objects.exists())
        self.assertFalse(Conversation.objects.exists())
        worker.assert_not_called()
        self.assertNotIn('synthetic-private-provider-handle', response.content.decode())

    def test_rename_is_audited_and_preserves_profile_selection_transcript_and_draft(self):
        storage = installation_metadata(self.data)['storage_id']
        profile = ProviderProfile.objects.create(account=self.account, user=self.owner,
            installation=self.installation, storage_id=storage, provider='codex')
        ChatSelection.objects.create(user=self.owner, account=self.account, revision=7)
        conversation = Conversation.objects.create(account=self.account, user=self.owner,
            installation=self.installation, storage_id=storage, phase='ready',
            worker=uuid.uuid4(), revision=11, messages=[{'id': 'original', 'text': 'Synthetic retained conversation'}])
        from backend.maipa.agent_accounts.draft_store import save
        save(self.owner, str(self.account.pk), 0,
             {'v': 1, 'text': 'Unsent original Ω', 'pills': [], 'attachments': []}, None)
        from backend.maipa.agent_accounts.models import ChatDraftRecord
        protected = lambda: [list(model.objects.values()) for model in
            (ProviderProfile, ChatSelection, Conversation, SessionReference, ChatDraftRecord, Installation)]
        before = protected()
        with patch('backend.maipa.agent_accounts.chat.start_worker') as worker:
            response = self.post({'expected_revision': 0, 'label': 'Work / personal Ω'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()['label'], response.json()['revision']), ('Work / personal Ω', 1))
        self.assertEqual(protected(), before)
        self.assertEqual(ProviderProfile.objects.get().pk, profile.pk)
        self.assertEqual(Conversation.objects.get().pk, conversation.pk)
        self.account.refresh_from_db()
        self.assertEqual((self.account.label, self.account.metadata_revision), ('Work / personal Ω', 1))
        event = self.events().get()
        self.assertEqual((event.actor_id, event.account_id, event.installation_id, str(event.storage_id)),
                         (self.owner.pk, self.account.pk, self.installation.pk, storage))
        self.assertEqual((event.before_label, event.after_label, event.revision),
                         ('Synthetic UI account', 'Work / personal Ω', 1))
        self.assertEqual(self.get().json(), response.json())
        worker.assert_not_called()

    def test_stale_and_duplicate_saves_cannot_overwrite_an_acknowledged_name(self):
        first = self.post({'expected_revision': 0, 'label': 'First acknowledged'})
        self.assertEqual(first.status_code, 200)
        for label in ('Older tab', 'First acknowledged'):
            response = self.post({'expected_revision': 0, 'label': label})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json(), {'error': {'code': 'account_metadata_conflict'}})
        self.assertEqual(self.get().json(), first.json())
        self.assertEqual(self.events().count(), 1)

    def test_noop_is_not_a_new_metadata_revision_or_audit_event(self):
        response = self.post({'expected_revision': 0, 'label': self.account.label})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['revision'], 0)
        self.assertEqual(self.events().count(), 0)

    def test_current_owner_role_membership_account_and_actor_are_required(self):
        self.assertEqual(self.client.get(self.url()).status_code, 401)
        self.assertEqual(self.post({'expected_revision': 0, 'label': 'Foreign'}, actor=self.other).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url(), HTTP_X_MAIPA_AGENT_ACTOR=str(self.other.pk)).status_code, 409)
        self.assertEqual(self.client.get(self.url()+'?owner='+str(self.other.pk),
            HTTP_X_MAIPA_AGENT_ACTOR=str(self.owner.pk)).status_code, 400)
        for change in ({'role': 'viewer'}, {'role': 'reviewer'}, {'enabled': False}):
            Membership.objects.filter(user=self.owner).update(role='preparer', enabled=True)
            Membership.objects.filter(user=self.owner).update(**change)
            self.assertEqual(self.post({'expected_revision': 0, 'label': 'Denied'}).status_code, 403)
        Membership.objects.filter(user=self.owner).update(role='preparer', enabled=True)
        AgentAccount.objects.filter(pk=self.account.pk).update(enabled=False)
        self.assertEqual(self.post({'expected_revision': 0, 'label': 'Disabled'}).status_code, 404)
        self.account.refresh_from_db()
        self.assertEqual(self.account.label, 'Synthetic UI account')
        self.assertEqual(self.events().count(), 0)

    def test_invalid_json_fields_revisions_names_and_csrf_cannot_change_metadata(self):
        invalid = [{'expected_revision': revision, 'label': 'Valid'} for revision in (True, -1, 2_000_000_000, 0.5, '0')]
        invalid += [{'expected_revision': 0, 'label': value} for value in ('', ' ', ' padded ', 'line\nbreak', 'x'*81, '😀'*41, None)]
        invalid += [{'expected_revision': 0, 'label': 'Valid', field: 'forged'}
                    for field in ('provider', 'user_id', 'enabled', 'profile_id', 'model_id', 'installation_id')]
        invalid += ['{"expected_revision":0,"label":"first","label":"second"}',
                    '{"expected_revision":NaN,"label":"bad"}', '[]']
        for value in invalid:
            with self.subTest(value=type(value).__name__):
                self.assertEqual(self.post(value).status_code, 400)
        self.assertEqual(self.post(' '*4097).status_code, 413)
        self.assertEqual(self.post({'expected_revision': 0, 'label': 'CSRF'},
                                  client=Client(enforce_csrf_checks=True)).status_code, 403)
        self.account.refresh_from_db()
        self.assertEqual(self.account.label, 'Synthetic UI account')
        self.assertEqual(self.events().count(), 0)

    def test_profile_origin_and_install_storage_mismatch_refuse_without_rebinding(self):
        self.assertEqual(self.get().status_code, 200)
        profile = ProviderProfile.objects.create(account=self.account, user=self.other,
            installation=self.installation, storage_id=installation_metadata(self.data)['storage_id'], provider='codex')
        self.assertEqual(self.post({'expected_revision': 0, 'label': 'Denied'}).status_code, 404)
        ProviderProfile.objects.filter(pk=profile.pk).update(user=self.owner)
        with patch('backend.maipa.agent_accounts.references.installation_metadata',
                   return_value={'storage_id': str(uuid.uuid4())}):
            self.assertEqual(self.post({'expected_revision': 0, 'label': 'Wrong install'}).status_code, 404)
        self.account.refresh_from_db()
        self.assertEqual(self.account.label, 'Synthetic UI account')
        self.assertEqual(ProviderProfile.objects.get().pk, profile.pk)
        self.assertEqual(self.events().count(), 0)

    def test_audit_failure_rolls_back_both_label_and_metadata_revision(self):
        self.assertEqual(self.get().status_code, 200)
        with patch('backend.maipa.agent_accounts.models.AccountMetadataEvent.objects.create',
                   side_effect=DatabaseError('synthetic private database failure')):
            response = self.post({'expected_revision': 0, 'label': 'Must roll back'})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('synthetic private database failure', response.content.decode())
        self.account.refresh_from_db()
        self.assertEqual((self.account.label, self.account.metadata_revision), ('Synthetic UI account', 0))
        self.assertEqual(self.events().count(), 0)


class AccountMetadataMigrationTests(TransactionTestCase):
    setUp = composition.UiCompositionTests.setUp

    def test_downgrade_refuses_before_erasing_saved_account_name_history(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from backend.maipa.agent_accounts import metadata
        from backend.maipa.agent_accounts.models import AccountMetadataEvent
        saved = metadata.rename(self.owner, self.account.pk, {'expected_revision': 0, 'label': 'Saved name Ω'})
        events = list(AccountMetadataEvent.objects.values())
        try:
            with self.assertRaisesRegex(RuntimeError, 'Saved account metadata requires offline backup recovery'):
                MigrationExecutor(connection).migrate([('agent_accounts', '0010_chatdraftrecord')])
            self.assertIn(('agent_accounts', '0011_account_metadata'), MigrationExecutor(connection).loader.applied_migrations)
        finally:
            executor = MigrationExecutor(connection); executor.migrate(executor.loader.graph.leaf_nodes())
        self.assertEqual(metadata.read(self.owner, self.account.pk), saved)
        self.assertEqual(list(AccountMetadataEvent.objects.values()), events)
