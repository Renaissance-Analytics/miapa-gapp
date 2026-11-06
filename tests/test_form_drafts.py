"""Pending form input is scoped shared state, never a business-write receipt."""
from copy import deepcopy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from backend.maipa.agent_accounts import form_drafts as drafts, ui
from backend.maipa.agent_accounts.models import AgentAccount, SessionReference, UiComposition
from backend.maipa.agent_accounts.references import record_session
from backend.maipa.intake.models import Engagement, Entity
from backend.maipa.storage import prepare_directory, prepare_files
from backend.maipa.workspace.models import Installation, Membership


class FormDraftTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='maipa-form-drafts-')
        self.addCleanup(temporary.cleanup)
        data = Path(temporary.name).resolve() / 'install'
        prepare_directory(data); prepare_files(data)
        configured = override_settings(DATA_ROOT=data); configured.enable(); self.addCleanup(configured.disable)
        self.install = Installation.objects.create()
        self.owner = get_user_model().objects.create_user(username='draft-owner')
        self.other = get_user_model().objects.create_user(username='draft-other')
        self.member = Membership.objects.create(user=self.owner, installation=self.install, role='preparer')
        Membership.objects.create(user=self.other, installation=self.install, role='preparer')
        self.account = AgentAccount.objects.create(owner=self.owner, provider='claude', label='Owned synthetic draft')
        self.session = record_session(self.owner, str(self.account.pk), 'synthetic-private-handle')
        self.lease = ui.open_ui_lease(self.owner, str(self.account.pk), self.session)
        self.schema = {'id': 'catalog-form', 'type': 'Maipa.ClientCatalog'}
        self.present(self.schema)

    def call(self, name, arguments):
        return ui.dispatch_ui(self.owner, str(self.account.pk), self.lease,
            {'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})

    def present(self, schema):
        return self.call('maipa_ui_present', {'revision': 0, 'title': 'Owned setup', 'schema': schema})

    def read(self, **changes):
        return drafts.read_new_client_form(changes.get('actor', self.owner), changes.get('account', str(self.account.pk)),
            changes.get('lease', self.lease), changes.get('component', 'catalog-form'))

    def change(self, state, values, **changes):
        command = {'id': state['id'], 'expected_revision': state['revision'], 'values': values}
        command.update(changes)
        return drafts.change_new_client_form(self.owner, str(self.account.pk), self.lease, 'catalog-form', command)

    def stored(self):
        return deepcopy(UiComposition.objects.get(pk=self.lease).form_drafts)

    def refused(self, operation):
        with self.assertRaises(drafts.FormUnavailable) as caught: operation()
        self.assertEqual(str(caught.exception), 'Form draft unavailable')
        self.assertIsNone(caught.exception.__cause__)

    def test_visible_form_gets_one_detached_identity_without_creating_business_records(self):
        state = self.read()
        self.assertEqual(set(state), {'id', 'revision', 'component_id', 'kind', 'fields', 'values'})
        self.assertEqual(str(uuid.UUID(state['id'])), state['id'])
        self.assertEqual((state['component_id'], state['kind'], state['revision']), ('catalog-form', 'new-client', 0))
        self.assertEqual(state['values'], {'name': '', 'entity_name': ''})
        self.assertEqual([field['name'] for field in state['fields']], ['name', 'entity_name'])
        state['values']['name'] = 'Mutated caller'; state['fields'].clear()
        self.assertEqual(self.read()['values']['name'], '')
        self.assertEqual(self.read()['id'], state['id'])
        self.assertEqual((Engagement.objects.count(), Entity.objects.count()), (0, 0))
        self.assertEqual(ui.ui_snapshot(self.owner, str(self.account.pk), self.lease)['revision'], 1)

    def test_sequential_human_and_agent_edits_share_exact_pending_values_and_revision(self):
        first = self.read()
        human = self.change(first, {'name': '  Synthetic Client  '})
        agent = self.change(human, {'entity_name': 'Synthetic Entity'})
        self.assertEqual(agent['values'], {'name': '  Synthetic Client  ', 'entity_name': 'Synthetic Entity'})
        self.assertEqual(agent['revision'], 2)
        self.assertEqual(self.read(), agent)
        self.assertEqual(agent['id'], first['id'])
        self.assertEqual((Engagement.objects.count(), Entity.objects.count()), (0, 0))

    def test_duplicate_value_and_empty_patch_do_not_invent_new_revisions(self):
        first = self.read(); changed = self.change(first, {'name': 'Synthetic'})
        self.assertEqual(self.change(changed, {'name': 'Synthetic'}), changed)
        self.assertEqual(self.change(changed, {}), changed)

    def test_stale_editor_and_wrong_form_identity_cannot_overwrite_newer_draft(self):
        first = self.read(); changed = self.change(first, {'name': 'Human newer'})
        with self.assertRaises(drafts.FormConflict): self.change(first, {'name': 'Agent stale'})
        self.refused(lambda: self.change(changed, {'name': 'Wrong identity'}, id=str(uuid.uuid4())))
        self.assertEqual(self.read(), changed)

    def test_unknown_private_executable_and_invalid_fields_preserve_every_pending_value(self):
        state = self.read(); before = self.stored()
        for values in ({'password': 'not-a-field'}, {'user_id': str(self.other.pk)}, {'url': '/foreign'},
                       {'name': None}, {'name': 1}, {'name': ['x']}, {'name': 'x' * 129},
                       {'name': 'x\x00'}, {'entity_name': 'x\n'}, ['name'], None):
            with self.subTest(values=values): self.refused(lambda: self.change(state, values))
        self.refused(lambda: self.change(state, {}, action='submit'))
        self.assertEqual(self.stored(), before)

    def test_other_actor_account_and_installation_cannot_read_or_mint_draft(self):
        before = self.stored()
        self.refused(lambda: self.read(actor=SimpleNamespace(pk=self.owner.pk, is_authenticated=True)))
        self.refused(lambda: self.read(actor=self.other))
        self.refused(lambda: self.read(account=str(uuid.uuid4())))
        self.refused(lambda: self.read(lease=str(uuid.uuid4())))
        # A local installation is a singleton. A changed storage binding on the
        # original private session must refuse rather than minting a new draft.
        SessionReference.objects.filter(pk=self.session).update(storage_id=uuid.uuid4())
        self.refused(self.read)
        self.assertEqual(self.stored(), before)

    def test_revoked_session_refuses_both_read_and_write_without_mutating_draft(self):
        state = self.change(self.read(), {'name': 'Retained pending input'}); before = self.stored()
        SessionReference.objects.filter(pk=self.session).update(revoked_at=timezone.now())
        self.refused(self.read); self.refused(lambda: self.change(state, {'name': 'Late write'}))
        self.assertEqual(self.stored(), before)

    def test_current_role_access_revision_and_enabled_status_are_rechecked(self):
        state = self.read(); before = self.stored()
        for field, value in (('role', 'viewer'), ('enabled', False)):
            Membership.objects.filter(pk=self.member.pk).update(**{field: value})
            self.refused(self.read); self.refused(lambda: self.change(state, {'name': 'Refused'}))
            Membership.objects.filter(pk=self.member.pk).update(role='preparer', enabled=True)
        Installation.objects.filter(pk=self.install.pk).update(access_revision=self.install.access_revision + 1)
        self.refused(self.read)
        self.assertEqual(self.stored(), before)

    def test_stale_actor_object_does_not_survive_primary_user_deactivation(self):
        state = self.read(); before = self.stored()
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        self.refused(self.read); self.refused(lambda: self.change(state, {'name': 'Refused'}))
        self.assertEqual(self.stored(), before)

    def test_only_a_current_client_catalog_node_has_this_business_form(self):
        self.refused(lambda: self.read(component='missing-form'))
        self.call('maipa_ui_update', {'revision': 1, 'title': 'Commentary',
            'schema': {'id': 'catalog-form', 'type': 'Maipa.Note', 'props': {'text': 'Commentary'}}})
        self.refused(self.read)
        self.assertEqual(self.stored(), {})

    def test_unrelated_composition_edit_keeps_draft_but_removed_and_readded_node_gets_new_identity(self):
        first = self.change(self.read(), {'name': 'Human pending'})
        self.call('maipa_ui_update', {'revision': 1, 'title': 'Grouped setup', 'schema': {
            'id': 'group', 'type': 'Maipa.Stack', 'children': [self.schema,
                {'id': 'comment', 'type': 'Maipa.Note', 'props': {'text': 'Unrelated edit'}}]}})
        self.assertEqual(self.read(), first)
        self.call('maipa_ui_dismiss', {'revision': 2})
        self.refused(self.read); self.assertEqual(self.stored(), {})
        self.call('maipa_ui_present', {'revision': 3, 'title': 'New instance', 'schema': self.schema})
        second = self.read(); self.assertNotEqual(second['id'], first['id'])
        self.assertEqual(second['values'], {'name': '', 'entity_name': ''})
        self.refused(lambda: self.change(second, {'name': 'Old reference'}, id=first['id']))

    def test_separate_native_ui_leases_cannot_share_pending_values_or_ids(self):
        first = self.change(self.read(), {'name': 'First lease only'})
        other = ui.open_ui_lease(self.owner, str(self.account.pk), self.session)
        ui.dispatch_ui(self.owner, str(self.account.pk), other, {'method': 'tools/call', 'params': {
            'name': 'maipa_ui_present', 'arguments': {'revision': 0, 'title': 'Other lease', 'schema': self.schema}}})
        second = self.read(lease=other)
        self.assertNotEqual(first['id'], second['id']); self.assertEqual(second['values']['name'], '')
        self.assertEqual(self.read(), first)

    def test_corrupt_stored_draft_and_exhausted_revision_refuse_without_replacement(self):
        first = self.read()
        for state in ({'catalog-form': {'private': 'PRIVATE_CORRUPTION'}}, ['PRIVATE_CORRUPTION'],
                      {'orphan-form': {'id': first['id'], 'revision': 0,
                                       'values': {'name': 'Unavailable form', 'entity_name': ''}}}):
            UiComposition.objects.filter(pk=self.lease).update(form_drafts=state)
            self.refused(self.read); self.assertEqual(self.stored(), state)
        UiComposition.objects.filter(pk=self.lease).update(form_drafts={})
        current = self.read(); stored = self.stored(); stored['catalog-form']['revision'] = 1_999_999_998
        UiComposition.objects.filter(pk=self.lease).update(form_drafts=stored)
        exhausted = self.read(); self.refused(lambda: self.change(exhausted, {'name': 'Overflow'}))
        self.assertEqual(self.stored(), stored)
        self.refused(lambda: self.change(current, {}, expected_revision=True))
