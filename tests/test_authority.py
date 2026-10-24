"""P03 independent authority outcomes, using synthetic principals and evidence."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone


class AuthorityTests(TestCase):
    def setUp(self):
        from backend.maipa.workspace.models import Installation, Membership
        from backend.maipa.intake.models import Engagement, Entity
        self.install = Installation.objects.create()
        self.users = []
        for name, role in [('maker', 'preparer'), ('reviewer', 'reviewer'),
                           ('approver', 'administrator'), ('executor', 'preparer'), ('other', 'administrator')]:
            user = get_user_model().objects.create_user(username=name, password='synthetic-policy-password')
            Membership.objects.create(user=user, installation=self.install, role=role)
            self.users.append(user)
        self.maker, self.reviewer, self.approver, self.executor, self.other = self.users
        engagement = Engagement.objects.create(installation=self.install, owner=self.maker, name='Synthetic engagement')
        self.entity = Entity.objects.create(engagement=engagement, name='Synthetic entity')
        self.now = timezone.now()

    def api(self):
        from backend.maipa.policies import authority
        return authority

    def context(self, user):
        return self.api().human(user)

    def grant(self, user, duty, **changes):
        values = dict(entity_id=self.entity.pk, subject_id=user.pk, action='ledger.post', duty=duty,
            starts_at=self.now-timedelta(hours=1), expires_at=self.now+timedelta(days=1),
            limit='1000.0000', currency='USD', evidence='synthetic signed authority reference', can_delegate=False)
        values.update(changes)
        return self.api().provision_root(**values)

    def snapshot(self, **changes):
        value = dict(entity_id=str(self.entity.pk), action='ledger.post', entity_revision=0,
            record_revision='journal-7:3', graph_revision=None, resource_id=None,
            payload={'lines': [{'account': 'cash', 'debit': '125.0000'}, {'account': 'capital', 'credit': '125.0000'}]},
            amount='125.0000', currency='USD', evidence=['synthetic journal workpaper'],
            executor=self.api().actor_key(self.context(self.executor)),
            starts_at=(self.now-timedelta(minutes=1)).isoformat(), expires_at=(self.now+timedelta(hours=1)).isoformat())
        value.update(changes)
        return value

    def approved(self):
        for user, duty in zip(self.users, ('preparer', 'reviewer', 'approver', 'executor')):
            self.grant(user, duty)
        snapshot = self.snapshot()
        row = self.api().prepare(self.context(self.maker), snapshot)
        self.api().attest(self.context(self.reviewer), row.pk, snapshot, 'reviewer')
        self.api().attest(self.context(self.approver), row.pk, snapshot, 'approver')
        return row, snapshot

    def check(self, row, snapshot, user=None):
        return self.api().check_execution(self.context(user or self.executor), row.pk, snapshot)

    def test_exact_approved_authority_is_reused_and_audited_without_new_approval(self):
        row, snapshot = self.approved()
        first = self.check(row, snapshot)
        second = self.check(row, snapshot)
        self.assertEqual((first.allowed, second.allowed, first.code, second.code), (True, True, 'authorized', 'authorized'))
        from backend.maipa.policies.models import Attestation, PolicyAudit
        self.assertEqual(Attestation.objects.filter(authorization=row, duty='approver').count(), 1)
        events = list(PolicyAudit.objects.filter(authorization=row, operation='execution.check').order_by('created_at'))
        self.assertEqual([event.allowed for event in events], [True, True])
        self.assertEqual(events[0].snapshot_hash, events[1].snapshot_hash)
        self.assertTrue(events[0].grant_ids)

    def test_workspace_roles_contacts_and_superuser_cannot_invent_financial_authority(self):
        self.maker.is_staff = self.maker.is_superuser = True
        self.maker.save()
        with self.assertRaises(self.api().Denied):
            self.api().prepare(self.context(self.maker), self.snapshot())
        self.grant(self.maker, 'preparer')
        row = self.api().prepare(self.context(self.maker), self.snapshot())
        self.assertEqual(self.check(row, self.snapshot()).code, 'authority_denied')

    def test_changed_payload_action_actor_entity_record_and_graph_cannot_reuse_approval(self):
        row, snapshot = self.approved()
        changes = [{'payload': {'lines': []}}, {'action': 'payment.release'}, {'record_revision': 'journal-7:4'},
            {'graph_revision': 'changed-graph'}, {'executor': self.api().actor_key(self.context(self.other))},
            {'entity_id': '00000000-0000-0000-0000-000000000001'}, {'evidence': ['different evidence']},
            {'amount': '126.0000'}, {'currency': 'EUR'}]
        for change in changes:
            with self.subTest(change=change):
                self.assertFalse(self.check(row, snapshot | change).allowed)
        self.assertFalse(self.check(row, snapshot, self.other).allowed)
        self.assertTrue(self.check(row, snapshot).allowed)

    def test_current_entity_revision_and_membership_are_rechecked(self):
        row, snapshot = self.approved()
        from backend.maipa.intake.models import Entity
        Entity.objects.filter(pk=self.entity.pk).update(revision=1)
        self.assertFalse(self.check(row, snapshot).allowed)
        Entity.objects.filter(pk=self.entity.pk).update(revision=0)
        from backend.maipa.workspace.models import Membership
        Membership.objects.filter(user=self.reviewer).update(enabled=False)
        self.assertFalse(self.check(row, snapshot).allowed)

    def test_expired_future_and_revoked_grants_fail_and_history_survives(self):
        from unittest.mock import patch
        row, snapshot = self.approved()
        with patch('backend.maipa.policies.authority.timezone.now', return_value=self.now+timedelta(days=2)):
            self.assertFalse(self.check(row, snapshot).allowed)
        with patch('backend.maipa.policies.authority.timezone.now', return_value=self.now-timedelta(days=2)):
            self.assertFalse(self.check(row, snapshot).allowed)
        from backend.maipa.policies.models import AuthorityGrant, Attestation
        grant = AuthorityGrant.objects.get(subject=self.approver, duty='approver')
        self.api().revoke(self.context(self.approver), grant.pk, 'synthetic revocation')
        self.assertFalse(self.check(row, snapshot).allowed)
        self.assertEqual(Attestation.objects.filter(authorization=row).count(), 2)
        grant.refresh_from_db()
        self.assertIsNotNone(grant.revoked_at)
        with self.assertRaises(ValueError):
            grant.delete()

    def test_decimal_materiality_is_exact_bounded_and_currency_specific(self):
        self.grant(self.maker, 'preparer', limit='125.0000')
        self.assertIsNotNone(self.api().prepare(self.context(self.maker), self.snapshot()).pk)
        for change in [{'amount': '125.0001'}, {'currency': 'EUR'}, {'amount': 125.0}, {'amount': 'NaN'},
                       {'amount': '-1.0000'}, {'amount': '1e2'}, {'amount': '0.00001'}, {'amount': '9'*100}]:
            with self.subTest(change=change), self.assertRaises(self.api().Denied):
                self.api().prepare(self.context(self.maker), self.snapshot(**change))

    def test_preparer_cannot_review_or_approve_own_work_even_with_grants(self):
        self.grant(self.maker, 'preparer'); self.grant(self.maker, 'reviewer'); self.grant(self.maker, 'approver')
        row = self.api().prepare(self.context(self.maker), self.snapshot())
        for duty in ('reviewer', 'approver'):
            with self.assertRaises(self.api().Denied):
                self.api().attest(self.context(self.maker), row.pk, self.snapshot(), duty)

    def test_approval_requires_review_and_exact_validity_not_just_approver_role(self):
        self.grant(self.maker, 'preparer'); self.grant(self.approver, 'approver')
        snapshot = self.snapshot()
        row = self.api().prepare(self.context(self.maker), snapshot)
        with self.assertRaises(self.api().Denied):
            self.api().attest(self.context(self.approver), row.pk, snapshot, 'approver')
        with self.assertRaises(self.api().Denied):
            self.api().prepare(self.context(self.maker), self.snapshot(expires_at=self.now.isoformat(), starts_at=self.now.isoformat()))

    def test_delegation_can_only_narrow_live_parent_and_cannot_self_grant(self):
        parent = self.grant(self.approver, 'approver', can_delegate=True)
        options = dict(subject_id=self.reviewer.pk, starts_at=self.now, expires_at=self.now+timedelta(hours=1),
            limit='100.0000', currency='USD', evidence='synthetic delegated authority')
        child = self.api().delegate(self.context(self.approver), parent.pk, **options)
        self.assertEqual((child.parent_id, child.limit, child.can_delegate), (parent.pk, '100.0000', False))
        for change in [{'subject_id': self.approver.pk}, {'limit': '1000.0001'}, {'currency': 'EUR'},
                       {'expires_at': self.now+timedelta(days=2)}, {'starts_at': self.now-timedelta(days=2)}]:
            with self.subTest(change=change), self.assertRaises(self.api().Denied):
                self.api().delegate(self.context(self.approver), parent.pk, **(options | change))
        self.api().revoke(self.context(self.approver), parent.pk, 'synthetic parent revoked')
        self.assertFalse(self.api().grant_is_current(child.pk))

    def test_unrelated_admin_cannot_revoke_or_delegate_and_foreign_installation_denies(self):
        grant = self.grant(self.approver, 'approver', can_delegate=True)
        with self.assertRaises(self.api().Denied):
            self.api().revoke(self.context(self.other), grant.pk, 'forged administrator shortcut')
        from backend.maipa.workspace.models import Membership
        Membership.objects.filter(user=self.approver).update(enabled=False)
        self.assertFalse(self.api().grant_is_current(grant.pk))

    def test_unknown_actions_floats_oversized_payload_and_forged_context_fail_closed(self):
        self.grant(self.maker, 'preparer')
        for change in [{'action': 'anything.execute'}, {'payload': {'amount': 1.2}},
                       {'payload': {'text': 'x'*65537}}, {'owner': str(self.maker.pk)}, {'entity_revision': True}]:
            with self.subTest(change=change), self.assertRaises(self.api().Denied):
                self.api().prepare(self.context(self.maker), self.snapshot(**change))
        with self.assertRaises(self.api().Denied):
            self.api().prepare({'user': self.maker, 'kind': 'human'}, self.snapshot())

    def test_agent_identity_is_server_registered_and_cannot_approve_or_impersonate(self):
        from backend.maipa.policies.models import AgentPrincipal
        agent = AgentPrincipal.objects.create(user=self.executor, installation=self.install,
            reference='synthetic registered server agent', expires_at=self.now+timedelta(days=1))
        context = self.api().agent(self.executor, agent.pk)
        key = self.api().actor_key(context)
        self.assertNotEqual(key, self.api().actor_key(self.context(self.executor)))
        with self.assertRaises(self.api().Denied):
            self.api().agent(self.other, agent.pk)
        with self.assertRaises(self.api().Denied):
            self.grant(self.executor, 'approver', agent_id=agent.pk)
        for user, duty in [(self.maker, 'preparer'), (self.reviewer, 'reviewer'), (self.approver, 'approver')]:
            self.grant(user, duty)
        self.grant(self.executor, 'executor', agent_id=agent.pk)
        snapshot = self.snapshot(executor=key)
        row = self.api().prepare(self.context(self.maker), snapshot)
        self.api().attest(self.context(self.reviewer), row.pk, snapshot, 'reviewer')
        self.api().attest(self.context(self.approver), row.pk, snapshot, 'approver')
        self.assertTrue(self.api().check_execution(context, row.pk, snapshot).allowed)
        self.assertFalse(self.check(row, snapshot).allowed)
        AgentPrincipal.objects.filter(pk=agent.pk).update(enabled=False)
        self.assertFalse(self.api().check_execution(context, row.pk, snapshot).allowed)

    def test_policy_change_invalidates_previously_approved_decisions(self):
        row, snapshot = self.approved()
        self.grant(self.other, 'preparer')
        self.assertFalse(self.check(row, snapshot).allowed)

    def test_authority_records_are_immutable_and_execution_is_still_disabled(self):
        row, snapshot = self.approved()
        row.payload = {'changed': True}
        with self.assertRaises(ValueError):
            row.save()
        self.assertFalse(self.api().execute(self.context(self.executor), row.pk, snapshot).allowed)
        self.assertEqual(self.api().execute(self.context(self.executor), row.pk, snapshot).code, 'execution_not_available')
