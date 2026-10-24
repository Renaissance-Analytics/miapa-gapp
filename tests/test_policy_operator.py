"""Root authority requires an explicit trusted local operator record."""
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase
from django.utils import timezone


class PolicyOperatorTests(TestCase):
    def setUp(self):
        from backend.maipa.workspace.models import Installation, Membership
        from backend.maipa.intake.models import Engagement, Entity
        self.install = Installation.objects.create()
        self.user = get_user_model().objects.create_user(username='synthetic-operator-subject')
        Membership.objects.create(user=self.user, installation=self.install, role='preparer')
        engagement = Engagement.objects.create(installation=self.install, owner=self.user, name='Synthetic command engagement')
        self.entity = Entity.objects.create(engagement=engagement, name='Synthetic operator entity')
        self.now = timezone.now()

    def options(self):
        return dict(entity=str(self.entity.pk), username=self.user.username, action='ledger.post', duty='executor',
            valid_from=(self.now-timedelta(hours=1)).isoformat(), expires=(self.now+timedelta(hours=1)).isoformat(),
            limit='200.0000', currency='USD', reference='Synthetic signed operator authority')

    def test_local_command_records_exact_root_and_retains_operator_revocation(self):
        output = StringIO()
        call_command('record_authority', **self.options(), stdout=output)
        from backend.maipa.policies.models import AuthorityGrant, GrantRevocation, PolicyAudit
        grant = AuthorityGrant.objects.get()
        self.assertEqual((grant.subject_id, grant.action, grant.duty, grant.limit, grant.currency),
            (self.user.pk, 'ledger.post', 'executor', '200.0000', 'USD'))
        self.assertIsNone(grant.parent_id)
        self.assertEqual(grant.evidence, 'Synthetic signed operator authority')
        self.assertNotIn(grant.evidence, output.getvalue())
        call_command('revoke_authority', grant=str(grant.pk), reference='Synthetic local withdrawal', stdout=StringIO())
        grant.refresh_from_db()
        self.assertIsNotNone(grant.revoked_at)
        self.assertEqual(GrantRevocation.objects.get().actor, 'operator')
        self.assertEqual(list(PolicyAudit.objects.order_by('created_at').values_list('operation', flat=True)), ['grant.root', 'grant.revoke'])

    def test_local_command_rejects_missing_principal_or_invalid_bounds_without_records(self):
        from backend.maipa.policies.models import AuthorityGrant
        for changes in [{'username': 'missing'}, {'limit': 'NaN'}, {'reference': ''}, {'action': 'unregistered'},
                        {'expires': (self.now-timedelta(days=2)).isoformat()}, {'currency': 'usd'}]:
            with self.subTest(changes=changes), self.assertRaises(CommandError):
                call_command('record_authority', **(self.options() | changes), stdout=StringIO())
        self.assertEqual(AuthorityGrant.objects.count(), 0)
