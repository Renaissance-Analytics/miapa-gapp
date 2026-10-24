"""P03 server policy seams; these are not evidence of a Fancy Flow execution."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone


class WorkflowAuthorityTests(TestCase):
    def setUp(self):
        from backend.maipa.workspace.models import Installation, Membership
        from backend.maipa.intake.models import Engagement, Entity
        from backend.maipa.policies.models import PolicyResource
        self.install = Installation.objects.create()
        self.user = get_user_model().objects.create_user(username='flow-owner')
        self.other = get_user_model().objects.create_user(username='other-flow-owner')
        for user in (self.user, self.other):
            Membership.objects.create(user=user, installation=self.install, role='preparer')
        engagement = Engagement.objects.create(installation=self.install, owner=self.user, name='Synthetic flow engagement')
        self.entity = Entity.objects.create(engagement=engagement, name='Synthetic flow entity')
        self.graph = PolicyResource.objects.create(owner=self.user, entity=self.entity, kind='graph', graph_revision='graph:7')
        self.resources = [self.graph]
        for kind in ('run', 'job', 'log', 'checkpoint'):
            self.resources.append(PolicyResource.objects.create(owner=self.user, entity=self.entity, kind=kind,
                graph_revision='graph:7', parent=self.resources[-1]))
        from backend.maipa.policies import authority
        self.a = authority
        self.context = self.a.human(self.user)
        self.now = timezone.now()

    def grant(self, operation, user=None, **changes):
        duty = 'preparer' if operation in ('create', 'edit') else 'reviewer' if operation in ('discover', 'triage') else 'executor'
        values = dict(entity_id=self.entity.pk, subject_id=(user or self.user).pk,
            action='workflow.'+operation, duty=duty, starts_at=self.now-timedelta(hours=1),
            expires_at=self.now+timedelta(hours=1), limit=None, currency=None,
            evidence='synthetic workflow scope')
        values.update(changes)
        return self.a.provision_root(**values)

    def check(self, operation='run', resource=None, **changes):
        values = dict(context=self.context, resource_id=(resource or self.graph).pk,
            operation=operation, expected_revision=1, graph_revision='graph:7', stage='dispatch', outcome='not_started')
        values.update(changes)
        return self.a.check_workflow(**values)

    def test_all_eight_operations_use_explicit_current_grants(self):
        for operation in ('discover', 'create', 'edit', 'triage', 'run', 'resume', 'retry', 'cancel'):
            with self.subTest(operation=operation):
                self.assertFalse(self.check(operation).allowed)
                self.grant(operation)
                self.assertTrue(self.check(operation).allowed)
        self.assertFalse(self.check('invented-operation').allowed)

    def test_graph_run_job_log_and_checkpoint_retain_owner_entity_and_revision(self):
        self.grant('discover'); self.grant('run')
        for resource in self.resources:
            with self.subTest(kind=resource.kind):
                self.assertTrue(self.check('discover', resource).allowed)
                self.assertFalse(self.check('discover', resource, context=self.a.human(self.other)).allowed)
                self.assertFalse(self.check('discover', resource, expected_revision=2).allowed)
        from backend.maipa.policies.models import PolicyResource
        PolicyResource.objects.filter(pk=self.graph.pk).update(enabled=False)
        self.assertFalse(self.check('run', self.resources[-1]).allowed)

    def test_revoked_queued_work_is_denied_at_node_and_resume(self):
        grant = self.grant('run')
        self.assertTrue(self.check(stage='dispatch').allowed)
        self.a.revoke(self.context, grant.pk, 'synthetic scope withdrawn')
        self.assertFalse(self.check(stage='node').allowed)
        self.assertFalse(self.check(stage='resume').allowed)

    def test_graph_edit_and_parent_scope_change_invalidate_child_snapshots(self):
        self.grant('run')
        from backend.maipa.policies.models import PolicyResource
        self.assertTrue(self.check(resource=self.resources[-1]).allowed)
        PolicyResource.objects.filter(pk=self.graph.pk).update(graph_revision='graph:8', revision=2)
        self.assertFalse(self.check(resource=self.resources[-1]).allowed)
        self.assertFalse(self.check().allowed)
        self.assertTrue(self.check(expected_revision=2, graph_revision='graph:8').allowed)

    def test_unknown_result_cannot_authorize_unsafe_retry(self):
        self.grant('retry')
        self.assertTrue(self.check('retry', outcome='not_started').allowed)
        self.assertTrue(self.check('retry', outcome='verified_no_effect').allowed)
        for outcome in ('unknown', 'completed', 'partial', 'failed', 'guessed-safe'):
            self.assertFalse(self.check('retry', outcome=outcome).allowed)
        self.assertFalse(self.check('retry', stage='invented').allowed)

    def test_workflow_run_permission_never_implies_financial_node_authority(self):
        self.grant('run')
        self.assertTrue(self.check(stage='node').allowed)
        self.assertFalse(self.check(stage='node', financial_snapshot={'action': 'ledger.post'}).allowed)
        self.assertFalse(self.check(stage='node', authorization_id='00000000-0000-0000-0000-000000000001').allowed)

    def test_registered_agent_uses_same_workflow_scope_and_revocation(self):
        from backend.maipa.policies.models import AgentPrincipal
        principal = AgentPrincipal.objects.create(user=self.user, installation=self.install,
            reference='synthetic workflow server agent', expires_at=self.now+timedelta(hours=1))
        context = self.a.agent(self.user, principal.pk)
        self.grant('run')
        self.assertFalse(self.check(context=context).allowed)
        grant = self.grant('run', agent_id=principal.pk)
        self.assertTrue(self.check(context=context, stage='node').allowed)
        self.a.revoke(self.context, grant.pk, 'synthetic agent grant revoked')
        self.assertFalse(self.check(context=context, stage='resume').allowed)

    def test_resource_parent_entity_and_owner_cannot_be_laundered(self):
        self.grant('run')
        from backend.maipa.policies.models import PolicyResource
        PolicyResource.objects.filter(pk=self.resources[1].pk).update(owner=self.other)
        self.assertFalse(self.check(resource=self.resources[-1]).allowed)
        self.assertFalse(self.check(expected_revision=True).allowed)

    def test_financial_node_requires_separate_exact_approval_and_graph_edit_invalidates_it(self):
        self.grant('run')
        from backend.maipa.workspace.models import Membership
        people = []
        for name, duty in [('synthetic-finance-maker', 'preparer'), ('synthetic-finance-reviewer', 'reviewer'),
                           ('synthetic-finance-approver', 'approver')]:
            user = get_user_model().objects.create_user(username=name)
            Membership.objects.create(user=user, installation=self.install, role='preparer')
            self.a.provision_root(entity_id=self.entity.pk, subject_id=user.pk, action='ledger.post', duty=duty,
                starts_at=self.now-timedelta(hours=1), expires_at=self.now+timedelta(hours=1), limit='250.0000',
                currency='USD', evidence='synthetic separate finance authority')
            people.append(user)
        self.a.provision_root(entity_id=self.entity.pk, subject_id=self.user.pk, action='ledger.post', duty='executor',
            starts_at=self.now-timedelta(hours=1), expires_at=self.now+timedelta(hours=1), limit='250.0000',
            currency='USD', evidence='synthetic exact executor authority')
        snapshot = dict(entity_id=str(self.entity.pk), action='ledger.post', entity_revision=0, record_revision='synthetic-journal:1',
            graph_revision='graph:7', resource_id=str(self.graph.pk), payload={'synthetic_posting': '200.0000'},
            amount='200.0000', currency='USD', evidence=['synthetic journal source'], executor=self.a.actor_key(self.context),
            starts_at=(self.now-timedelta(minutes=1)).isoformat(), expires_at=(self.now+timedelta(minutes=30)).isoformat())
        row = self.a.prepare(self.a.human(people[0]), snapshot)
        self.assertFalse(self.check(stage='node', authorization_id=row.pk, financial_snapshot=snapshot).allowed)
        self.a.attest(self.a.human(people[1]), row.pk, snapshot, 'reviewer')
        self.a.attest(self.a.human(people[2]), row.pk, snapshot, 'approver')
        self.assertTrue(self.check(stage='node', authorization_id=row.pk, financial_snapshot=snapshot).allowed)
        from backend.maipa.policies.models import PolicyResource
        PolicyResource.objects.filter(pk=self.graph.pk).update(revision=2)
        self.assertFalse(self.check(stage='node', expected_revision=2,
            authorization_id=row.pk, financial_snapshot=snapshot).allowed)
        PolicyResource.objects.filter(pk=self.graph.pk).update(graph_revision='graph:8', revision=2)
        self.assertFalse(self.check(stage='resume', expected_revision=2, graph_revision='graph:8',
            authorization_id=row.pk, financial_snapshot=snapshot).allowed)
