"""Authority and lifecycle outcomes for the actual deterministic Flow worker."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from django.db import connections
from django.test import TestCase

from backend.maipa.storage import install_lock, StorageUnavailable
from backend.maipa.workflows import services as flows
from backend.maipa.workflows.models import Definition, WorkflowRun, NodeCheckpoint
from backend.maipa.workspace.models import Membership
from tests.test_workflows import fixture, graph


class WorkflowWorkerTests(TestCase):
    def setUp(self):
        self.install, self.owner, self.other, self.member, self.engagement, self.entity = fixture()
        self.saved = flows.save_definition(self.owner, 'Scoped worker', graph(), entity_id=str(self.entity.pk))
        self.run = flows.dispatch(self.owner, self.saved['id'], 1, {'name':'Private worker seed'}, 'worker-delivery')

    def work(self, step):
        from backend.maipa.workflows.worker import process_job
        return process_job(self.run['id'], step)

    def test_run_origin_is_immutable_and_definition_transfer_cannot_expose_or_execute_it(self):
        row = WorkflowRun.objects.get(pk=self.run['id'])
        self.assertEqual((row.origin_owner_id,row.origin_installation_id,row.origin_entity_id),
                         (self.owner.pk,self.install.pk,self.entity.pk))
        row.origin_owner = self.other
        with self.assertRaises(ValueError): row.save()
        Definition.objects.filter(pk=self.saved['id']).update(owner=self.other)
        with self.assertRaises(flows.Unavailable): flows.get_run(self.other, self.run['id'])
        # Removing the definition's entity binding cannot launder the old key.
        Definition.objects.filter(pk=self.saved['id']).update(entity=None)
        with self.assertRaises(flows.Conflict):
            flows.dispatch(self.other,self.saved['id'],1,{'name':'Private worker seed'},'worker-delivery')
        self.assertEqual(self.work(0),'blocked')
        current=flows.get_run(self.owner,self.run['id'])
        self.assertEqual((current['status'],current['error'],current['outputs']),('blocked','origin_changed',{}))
        self.assertFalse(NodeCheckpoint.objects.exists())

    def test_revocation_blocks_without_busy_loop_and_restoration_requires_explicit_resume(self):
        from backend.maipa.workflows.worker import work_once
        self.assertEqual(self.work(0),'advanced')
        before=NodeCheckpoint.objects.get(node_id='start').first_attempt_at
        Membership.objects.filter(pk=self.member.pk).update(enabled=False)
        self.assertEqual(self.work(1),'blocked')
        self.assertEqual(work_once(limit=4),{'observed':0,'advanced':0,'blocked':0,'stale':0})
        Membership.objects.filter(pk=self.member.pk).update(enabled=True)
        self.assertEqual(work_once(limit=4)['observed'],0)
        blocked=flows.get_run(self.owner,self.run['id'])
        self.assertEqual((blocked['status'],blocked['step'],blocked['error']),('blocked',2,'authority_revoked'))
        with self.assertRaises(flows.Conflict): flows.resume(self.owner,self.run['id'],1)
        resumed=flows.resume(self.owner,self.run['id'],2)
        self.assertEqual((resumed['status'],resumed['step'],resumed['error']),('queued',3,None))
        self.assertEqual(self.work(2),'stale')
        self.assertEqual(self.work(3),'advanced');self.assertEqual(self.work(4),'advanced')
        final=flows.get_run(self.owner,self.run['id'])
        self.assertEqual(final['outputs']['result'],'Ready Private worker seed')
        checkpoint=NodeCheckpoint.objects.get(node_id='start')
        self.assertEqual((checkpoint.attempts,checkpoint.first_attempt_at),(1,before))

    def test_current_active_role_and_entity_are_checked_before_every_worker_step(self):
        mutations=[('role','reviewer'),('active',False),('entity',self.other)]
        for key,value in mutations:
            with self.subTest(key=key):
                run=flows.dispatch(self.owner,self.saved['id'],1,{},'check-'+key)
                if key=='role': Membership.objects.filter(pk=self.member.pk).update(role=value)
                elif key=='active': type(self.owner).objects.filter(pk=self.owner.pk).update(is_active=value)
                else: type(self.engagement).objects.filter(pk=self.engagement.pk).update(owner=value)
                from backend.maipa.workflows.worker import process_job
                self.assertEqual(process_job(run['id'],0),'blocked')
                stored=WorkflowRun.objects.get(pk=run['id'])
                self.assertEqual(stored.error,'scope_revoked' if key=='entity' else 'authority_revoked')
                self.assertEqual(stored.checkpoints.count(),0)
                Membership.objects.filter(pk=self.member.pk).update(role='preparer')
                type(self.owner).objects.filter(pk=self.owner.pk).update(is_active=True)
                type(self.engagement).objects.filter(pk=self.engagement.pk).update(owner=self.owner)

    def test_cancelled_and_stale_deliveries_never_execute_another_node(self):
        self.assertEqual(self.work(0),'advanced')
        self.assertEqual(self.work(0),'stale')
        stopped=flows.cancel(self.owner,self.run['id'],1)
        self.assertEqual(self.work(stopped['step']),'stale')
        with self.assertRaises(flows.Conflict): flows.resume(self.owner,self.run['id'],stopped['step'])
        self.assertEqual(list(NodeCheckpoint.objects.values_list('node_id',flat=True)),['start'])

    def test_queue_pass_is_bounded_and_returns_only_closed_counts(self):
        from backend.maipa.workflows.worker import work_once
        for index in range(4): flows.dispatch(self.owner,self.saved['id'],1,{},'bounded-'+str(index))
        with self.assertRaises(ValueError): work_once(limit=True)
        with self.assertRaises(ValueError): work_once(limit=101)
        # The indexed database query must apply its limit before materialization.
        with self.assertNumQueries(1):
            with patch('backend.maipa.workflows.worker.process_job',return_value='advanced') as job:
                result=work_once(limit=2)
        self.assertEqual(result,{'observed':2,'advanced':2,'blocked':0,'stale':0})
        self.assertEqual(job.call_count,2)
        self.assertNotIn('Private',str(result))
        with connections['default'].cursor() as cursor:
            indexes=connections['default'].introspection.get_constraints(cursor,WorkflowRun._meta.db_table)
        self.assertTrue(any(item['index'] and item['columns']==['status','created_at','id'] for item in indexes.values()))

    def test_invalid_snapshot_becomes_safe_blocked_diagnostic_without_raw_error(self):
        row=WorkflowRun.objects.get(pk=self.run['id'])
        row.version.__class__.objects.filter(pk=row.version_id).update(schema_sha256='0'*64)
        self.assertEqual(self.work(0),'blocked')
        row.refresh_from_db()
        self.assertEqual((row.status,row.error,row.step),('blocked','snapshot_unavailable',1))
        self.assertEqual(row.checkpoints.count(),0)
        self.assertEqual(self.work(1),'stale')


class WorkflowServingTests(unittest.TestCase):
    def test_serving_rechecks_live_lease_and_joins_worker_before_maintenance_unlock(self):
        from backend.maipa.workflows.worker import serving
        entered,release,finished=Event(),Event(),Event()
        with tempfile.TemporaryDirectory(prefix='maipa-worker-lifecycle-') as directory:
            root=Path(directory).resolve()
            def step(**kwargs):
                self.assertEqual(kwargs,{'limit':1})
                entered.set()
                self.assertTrue(release.wait(5))
                finished.set()
            with install_lock(root) as guard:
                with patch('backend.maipa.workflows.worker.work_once',side_effect=step):
                    with serving(guard,root,poll_seconds=0.01) as runner:
                        self.assertTrue(entered.wait(5))
                        self.assertFalse(runner.thread.daemon)
                        with self.assertRaises(StorageUnavailable):
                            with install_lock(root): pass
                        release.set()
                self.assertTrue(finished.is_set())
                self.assertFalse(runner.thread.is_alive())
            with self.assertRaises(StorageUnavailable):
                with serving(guard,root): pass
            with install_lock(root): pass

    def test_worker_failure_is_redacted_stops_and_surfaces_at_owned_shutdown(self):
        from backend.maipa.workflows.worker import serving
        with tempfile.TemporaryDirectory(prefix='maipa-worker-lifecycle-') as directory:
            root=Path(directory).resolve()
            with install_lock(root) as guard:
                with patch('backend.maipa.workflows.worker.work_once',side_effect=RuntimeError('PRIVATE-SEED')), \
                        self.assertLogs('backend.maipa.workflows.worker',level='ERROR') as logs:
                    with self.assertRaisesRegex(StorageUnavailable,'Workflow worker unavailable'):
                        with serving(guard,root,poll_seconds=0.01) as runner:
                            self.assertTrue(runner.failed.wait(5))
                self.assertFalse(runner.thread.is_alive())
                self.assertNotIn('PRIVATE-SEED',str(logs.output))
                self.assertEqual(len(logs.output),1)
