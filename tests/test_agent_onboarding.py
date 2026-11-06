"""The connected agent guides account setup; only the person confirms the save."""
from pathlib import Path
import tempfile

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend.maipa.agent_accounts import ui
from backend.maipa.agent_accounts.models import AgentAccount
from backend.maipa.agent_accounts.references import record_session
from backend.maipa.intake.models import Engagement, FactRevision
from backend.maipa.storage import prepare_directory, prepare_files
from backend.maipa.workspace.models import AccountOnboarding, Installation, Membership


class AgentGuidedSetupTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='maipa-agent-setup-')
        self.addCleanup(temporary.cleanup)
        data = Path(temporary.name).resolve() / 'install'
        prepare_directory(data)
        prepare_files(data)
        configured = override_settings(DATA_ROOT=data)
        configured.enable()
        self.addCleanup(configured.disable)
        installation = Installation.objects.create()
        self.owner = get_user_model().objects.create_user(username='setup-agent-owner')
        Membership.objects.create(user=self.owner, installation=installation, role='preparer')
        self.account = AgentAccount.objects.create(owner=self.owner, provider='claude', label='Synthetic setup account')
        session = record_session(self.owner, str(self.account.pk), 'synthetic-private-provider-handle')
        self.lease = ui.open_ui_lease(self.owner, str(self.account.pk), session)

    def call(self, name, arguments):
        return ui.dispatch_ui(self.owner, str(self.account.pk), self.lease,
                              {'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})

    def record(self, answer, action='reply'):
        state = self.call('maipa_setup_read', {})
        return self.call('maipa_setup_record', {'setup_id': state['setup_id'], 'expected_revision': state['revision'],
                                                'action': action, 'answer': answer})

    def test_agent_reads_explained_steps_and_records_conversation_answers_as_its_own(self):
        state = self.call('maipa_setup_read', {})
        self.assertEqual(state['step'], 'display_name')
        self.assertIn('not a client or business name', state['explanation'])
        self.assertEqual([step['id'] for step in state['steps']], ['display_name', 'workspace', 'engagement_name',
                         'entity_name', 'currency', 'reporting_basis', 'effective_from', 'review', 'complete'])
        self.assertTrue(all(step['title'] and step['explanation'] for step in state['steps']))
        recorded = self.record('Morgan')
        self.assertTrue(recorded['recorded'])
        self.assertEqual(recorded['setup']['step'], 'workspace')
        self.assertEqual(recorded['setup']['draft']['display_name'], 'Morgan')
        self.assertEqual(AccountOnboarding.objects.get(user=self.owner).messages[1], {'role': 'agent', 'text': 'Morgan'})
        later = self.record('later')['setup']
        self.assertEqual([step['id'] for step in later['steps']], ['display_name', 'workspace', 'review', 'complete'])

    def test_agent_cannot_confirm_save_and_invalid_or_stale_answers_change_nothing(self):
        for answer in ['Morgan', 'new', 'Synthetic client work', 'Synthetic LLC']:
            self.assertTrue(self.record(answer)['recorded'])
        refused = self.record('dollars')
        self.assertFalse(refused['recorded'])
        self.assertEqual(refused['setup']['step'], 'currency')
        state = self.call('maipa_setup_read', {})
        with self.assertRaises(ui.UiConflict):
            self.call('maipa_setup_record', {'setup_id': state['setup_id'], 'expected_revision': state['revision'] - 1,
                                             'action': 'reply', 'answer': 'USD'})
        for answer in ['USD', 'cash', '2026-10-01']:
            self.assertTrue(self.record(answer)['recorded'])
        self.assertEqual(self.call('maipa_setup_read', {})['step'], 'review')
        confirm = self.record('confirm')
        self.assertFalse(confirm['recorded'])
        self.assertIn('Confirm and save setup', confirm['reason'])
        self.assertEqual(confirm['setup']['step'], 'review')
        self.assertEqual(Engagement.objects.count(), 0)
        self.assertEqual(FactRevision.objects.count(), 0)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, '')
        with self.assertRaises(ui.UiUnavailable):
            self.call('maipa_setup_record', {'setup_id': state['setup_id'], 'expected_revision': 0,
                                             'action': 'confirm', 'answer': '', 'user_id': '1'})
