"""Protected nonmonetary duties must not invent an amount or currency."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone


class NonmonetaryAuthorityTests(TestCase):
    def setUp(self):
        from backend.maipa.workspace.models import Installation, Membership
        from backend.maipa.intake.models import Engagement, Entity
        from backend.maipa.policies import authority
        self.a = authority
        installation = Installation.objects.create()
        self.people = []
        for duty in ('preparer', 'reviewer', 'approver', 'executor'):
            user = get_user_model().objects.create_user(username='synthetic-nonmoney-'+duty)
            Membership.objects.create(user=user, installation=installation, role='preparer')
            self.people.append(user)
        engagement = Engagement.objects.create(installation=installation, owner=self.people[0], name='Synthetic nonmonetary engagement')
        self.entity = Entity.objects.create(engagement=engagement, name='Synthetic nonmonetary entity')
        self.now = timezone.now()

    def grant(self, action, user, duty, **changes):
        values = dict(entity_id=self.entity.pk, subject_id=user.pk, action=action, duty=duty,
            starts_at=self.now-timedelta(hours=1), expires_at=self.now+timedelta(hours=1),
            limit=None, currency=None, evidence='synthetic explicit nonmonetary authority')
        return self.a.provision_root(**(values | changes))

    def test_nonmonetary_protected_actions_require_independent_exact_authority_without_money(self):
        for action in ('period.reopen', 'vendor.payment_details.change', 'filing.submit', 'report.release'):
            with self.subTest(action=action):
                for user, duty in zip(self.people, ('preparer', 'reviewer', 'approver', 'executor')):
                    self.grant(action, user, duty)
                snapshot = dict(entity_id=str(self.entity.pk), action=action, entity_revision=0,
                    record_revision='synthetic-protected-record:1', graph_revision=None, resource_id=None,
                    payload={'synthetic_reference': 'protected source record'}, amount=None, currency=None,
                    evidence=['synthetic protected source'], executor=self.a.actor_key(self.a.human(self.people[3])),
                    starts_at=(self.now-timedelta(minutes=1)).isoformat(), expires_at=(self.now+timedelta(minutes=30)).isoformat())
                row = self.a.prepare(self.a.human(self.people[0]), snapshot)
                self.assertFalse(self.a.check_execution(self.a.human(self.people[3]), row.pk, snapshot).allowed)
                self.a.attest(self.a.human(self.people[1]), row.pk, snapshot, 'reviewer')
                self.a.attest(self.a.human(self.people[2]), row.pk, snapshot, 'approver')
                self.assertTrue(self.a.check_execution(self.a.human(self.people[3]), row.pk, snapshot).allowed)
                self.assertFalse(self.a.check_execution(self.a.human(self.people[3]), row.pk,
                    snapshot | {'payload': {'synthetic_reference': 'changed record'}}).allowed)

    def test_monetary_actions_cannot_omit_limits_or_invent_currency_for_nonmonetary_actions(self):
        for action in ('ledger.post', 'payment.release'):
            with self.subTest(action=action), self.assertRaises(self.a.Denied):
                self.grant(action, self.people[0], 'preparer')
        with self.assertRaises(self.a.Denied):
            self.grant('period.reopen', self.people[0], 'approver', limit='100.0000', currency='USD')
