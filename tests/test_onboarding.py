"""Independent local-bootstrap and persisted setup-chat acceptance."""
import json
from pathlib import Path
import tempfile

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from backend.maipa.intake.models import Engagement, Entity, FactRevision
from backend.maipa.workspace.models import Installation, Membership


class FirstRunTests(TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='maipa-setup-test-')
        self.addCleanup(self.temporary.cleanup)
        self.code_file = Path(self.temporary.name) / 'setup-code'
        self.code = 'a7' * 32
        self.code_file.write_text(self.code, encoding='utf-8')
        self.config = override_settings(LOCAL_SETUP_CODE_FILE=self.code_file)
        self.config.enable()
        self.addCleanup(self.config.disable)
        self.client = Client(enforce_csrf_checks=True)

    def submit(self, **changes):
        body = {'setup_code': self.code, 'username': 'local-admin',
                'password': 'synthetic-long-phrase!932', 'password_confirmation': 'synthetic-long-phrase!932'} | changes
        token = self.client.get('/api/session/csrf/', secure=True).json()['csrf_token']
        return self.client.post('/api/setup/', data=json.dumps(body), content_type='application/json',
            secure=True, HTTP_ORIGIN='https://testserver', HTTP_X_CSRFTOKEN=token)

    def test_pristine_install_offers_setup_without_disclosing_local_code(self):
        response = self.client.get('/api/setup/', secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'setup_required': True})
        self.assertNotIn(self.code, response.content.decode())
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(get_user_model().objects.count(), 0)

    def test_claim_creates_hashed_admin_rotates_session_and_permanently_closes_setup(self):
        session = self.client.session
        session['old'] = True
        session.save()
        self.client.cookies['sessionid'] = session.session_key
        previous = session.session_key
        self.assertEqual(self.submit().status_code, 201)
        user = get_user_model().objects.get(username='local-admin')
        self.assertTrue(user.check_password('synthetic-long-phrase!932'))
        self.assertFalse(user.is_superuser)
        self.assertEqual(Membership.objects.get(user=user).role, 'administrator')
        self.assertEqual(Installation.objects.count(), 1)
        self.assertNotEqual(self.client.cookies['sessionid'].value, previous)
        self.assertEqual(self.client.get('/api/session/', secure=True).json()['user']['id'], str(user.pk))
        self.assertEqual(self.client.get('/api/setup/', secure=True).json(), {'setup_required': False})
        other = Client(enforce_csrf_checks=True)
        self.client = other
        self.assertEqual(self.submit(username='second-admin').status_code, 409)
        self.assertEqual(get_user_model().objects.count(), 1)
        Membership.objects.update(enabled=False)
        self.assertEqual(self.client.get('/api/setup/', secure=True).json(), {'setup_required': False})

    def test_code_password_and_unknown_fields_cannot_claim_install(self):
        for changes in ({'setup_code': 'b1' * 32}, {'password': 'short', 'password_confirmation': 'short'},
                        {'password_confirmation': 'different'}, {'role': 'administrator'}, {'user_id': '9'}):
            response = self.submit(**changes)
            self.assertIn(response.status_code, (400, 403))
            self.assertEqual(get_user_model().objects.count(), 0)
            self.assertEqual(Installation.objects.count(), 0)
        self.assertEqual(self.submit().status_code, 201)

    def test_setup_requires_csrf_and_local_code_is_never_http_discoverable(self):
        response = self.client.post('/api/setup/', data='{}', content_type='application/json', secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.get('/runtime/local-setup-code', secure=True).status_code, 404)
        self.code_file.unlink()
        self.assertEqual(self.submit().status_code, 503)
        self.assertEqual(get_user_model().objects.count(), 0)

    def test_existing_users_or_installation_never_reopen_bootstrap(self):
        user = get_user_model().objects.create_user(username='existing', password='synthetic-test-passphrase')
        self.assertEqual(self.client.get('/api/setup/', secure=True).json(), {'setup_required': False})
        self.assertEqual(self.submit().status_code, 409)
        user.delete()
        Installation.objects.create()
        self.assertEqual(self.client.get('/api/setup/', secure=True).json(), {'setup_required': False})
        self.assertEqual(self.submit().status_code, 409)


class AccountOnboardingTests(TestCase):
    def setUp(self):
        self.install = Installation.objects.create()
        self.user = get_user_model().objects.create_user(username='setup-owner', password='synthetic-test-passphrase')
        self.other = get_user_model().objects.create_user(username='setup-other', password='synthetic-test-passphrase')
        Membership.objects.create(installation=self.install, user=self.user, role='preparer')
        Membership.objects.create(installation=self.install, user=self.other, role='viewer')
        self.client.force_login(self.user)

    def state(self):
        response = self.client.get('/api/onboarding/', secure=True)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def reply(self, answer, *, revision=None, action='reply'):
        state = self.state()
        if revision is None:
            revision = state['revision']
        return self.client.post('/api/onboarding/', data=json.dumps({
            'expected_revision': revision, 'action': action, 'answer': answer}),
            content_type='application/json', secure=True, HTTP_X_MAIPA_SETUP=state['setup_id'])

    def new_draft(self):
        for answer in ['Morgan', 'new', 'Synthetic client work', 'Synthetic LLC', 'USD', 'cash', '2026-10-01']:
            self.assertEqual(self.reply(answer).status_code, 200)
        self.assertEqual(self.state()['step'], 'review')

    def test_chat_retains_accepted_replies_across_login_and_has_no_fake_provider_readiness(self):
        first = self.state()
        self.assertEqual(first['step'], 'display_name')
        self.assertEqual(first['provider_ready'], False)
        self.assertEqual(self.reply('Morgan').status_code, 200)
        self.client.logout()
        self.client.force_login(self.user)
        state = self.state()
        self.assertEqual(state['step'], 'workspace')
        self.assertEqual(state['draft']['display_name'], 'Morgan')
        self.assertEqual(state['messages'][1]['text'], 'Morgan')
        self.assertEqual(state['revision'], first['revision'] + 1)

    def test_confirmation_saves_real_owned_entities_and_effective_dated_facts_once(self):
        self.new_draft()
        self.assertEqual(Engagement.objects.count(), 0)
        revision = self.state()['revision']
        self.assertEqual(self.reply('confirm', revision=revision).status_code, 200)
        state = self.state()
        self.assertEqual(state['step'], 'complete')
        row = Entity.objects.get(pk=state['entity_id'])
        self.assertEqual(row.engagement.owner, self.user)
        self.assertEqual(row.engagement.installation, self.install)
        self.assertEqual(row.name, 'Synthetic LLC')
        self.assertEqual(list(row.fact_revisions.order_by('revision').values_list('key', 'value')),
                         [('currency', 'USD'), ('reporting_basis', 'cash')])
        self.assertEqual({str(value) for value in row.fact_revisions.values_list('effective_from', flat=True)}, {'2026-10-01'})
        self.assertEqual(self.reply('confirm', revision=revision).status_code, 409)
        self.assertEqual(Engagement.objects.count(), 1)
        self.assertEqual(FactRevision.objects.count(), 2)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Morgan')

    def test_stale_reply_and_invalid_answer_do_not_advance_or_persist_untrusted_fields(self):
        first = self.state()['revision']
        self.assertEqual(self.reply('Morgan', revision=first).status_code, 200)
        self.assertEqual(self.reply('Racing name', revision=first).status_code, 409)
        self.assertEqual(self.reply({'role': 'administrator'}).status_code, 400)
        self.assertEqual(self.state()['draft'], {'display_name': 'Morgan'})

    def test_account_progress_is_private_and_revocation_is_immediate(self):
        self.assertEqual(self.reply('Private name').status_code, 200)
        previous_setup = self.state().get('setup_id', '11111111-1111-4111-8111-111111111111')
        self.client.force_login(self.other)
        self.assertEqual(self.state()['draft'], {})
        self.assertNotIn('Private name', json.dumps(self.state()))
        stale_tab = self.client.post('/api/onboarding/', data=json.dumps({
            'expected_revision': 0, 'action': 'reply', 'answer': 'Old tab answer'}),
            content_type='application/json', secure=True, HTTP_X_MAIPA_SETUP=previous_setup)
        self.assertEqual(stale_tab.status_code, 409)
        self.assertEqual(self.state()['draft'], {})
        Membership.objects.filter(user=self.other).update(enabled=False)
        self.assertEqual(self.client.get('/api/onboarding/', secure=True).status_code, 403)
        self.assertEqual(self.client.post('/api/onboarding/', data='{}', content_type='application/json', secure=True).status_code, 403)

    def test_defer_and_resume_keep_profile_and_never_claim_provider_connected(self):
        self.assertEqual(self.reply('Morgan').status_code, 200)
        self.assertEqual(self.reply('later').status_code, 200)
        self.assertEqual(self.reply('confirm').status_code, 200)
        state = self.state()
        self.assertTrue(state['deferred'])
        self.assertFalse(state['provider_ready'])
        self.assertEqual(Engagement.objects.count(), 0)
        self.assertEqual(self.reply('', action='resume').status_code, 200)
        self.assertEqual(self.state()['step'], 'workspace')
        self.assertEqual(self.state()['draft']['display_name'], 'Morgan')

    def test_viewer_only_sets_own_profile_and_cannot_create_financial_records(self):
        self.client.force_login(self.other)
        self.assertEqual(self.reply('Reader').status_code, 200)
        self.assertEqual(self.state()['step'], 'review')
        self.assertEqual(self.reply('new').status_code, 400)
        self.assertEqual(self.reply('confirm').status_code, 200)
        self.assertEqual(Engagement.objects.count(), 0)
        self.assertEqual(Membership.objects.get(user=self.other).role, 'viewer')

    def test_existing_entity_selection_is_owned_and_changed_fact_revision_blocks_confirmation(self):
        from backend.maipa.intake.services import create_engagement, record_fact
        row = create_engagement(self.user, {'name': 'Existing work', 'entity_name': 'Existing entity'}).entities.get()
        self.assertEqual(self.reply('Morgan').status_code, 200)
        self.assertEqual(self.reply(str(row.pk)).status_code, 200)
        for answer in ['USD', 'accrual', '2026-10-01']:
            self.assertEqual(self.reply(answer).status_code, 200)
        record_fact(self.user, row.pk, {'key': 'currency', 'knowledge': 'known', 'value': 'CAD',
            'expected_revision': 0, 'effective_from': '2026-10-01', 'effective_to': None,
            'evidence': {'reference': 'Independent correction', 'locator': 'owner', 'observed_on': '2026-10-24'}})
        self.assertEqual(self.reply('confirm').status_code, 409)
        self.assertEqual(FactRevision.objects.count(), 1)
        self.assertEqual(self.state()['step'], 'review')
        self.assertEqual(self.reply('', action='reset').status_code, 200)
        self.assertEqual(self.state()['step'], 'display_name')
        self.client.force_login(self.other)
        self.assertEqual(self.reply('Reader').status_code, 200)
        self.assertEqual(self.reply(str(row.pk)).status_code, 400)

    def test_chat_writes_require_authentication_csrf_and_strict_bounded_json(self):
        anonymous = Client(enforce_csrf_checks=True)
        self.assertEqual(anonymous.get('/api/onboarding/', secure=True).status_code, 401)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.user)
        self.assertEqual(protected.post('/api/onboarding/', data='{}', content_type='application/json', secure=True).status_code, 403)
        for raw in ['{"expected_revision":0,"expected_revision":1,"action":"reply","answer":"Name"}',
                    '{"expected_revision":0,"action":"reply","answer":"Name","user_id":"2"}', ' ' * 4097]:
            response = self.client.post('/api/onboarding/', data=raw, content_type='application/json', secure=True)
            self.assertIn(response.status_code, (400, 413))
        self.assertEqual(self.state()['revision'], 0)
