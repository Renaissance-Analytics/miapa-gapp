"""Real site-owned worker state, separate from HTTP readiness."""
from pathlib import Path
import tempfile
from threading import Event
import time
from unittest.mock import patch

from django.test import TransactionTestCase

from backend.maipa.storage import install_lock, StorageUnavailable
from backend.maipa.workflows import worker, services as flows
from backend.maipa.workflows.models import WorkflowRun
from tests.test_workflow_native import native_fixture,grant


class WorkflowStatusTests(TransactionTestCase):
    def setUp(self):native_fixture(self)

    def test_status_is_scoped_and_stays_unavailable_until_a_pass_succeeds(self):
        entered,release=Event(),Event()
        def pass_once(**kwargs):entered.set();self.assertTrue(release.wait(5))
        with install_lock(self.data) as guard,patch.object(worker,'work_once',side_effect=pass_once):
            with worker.serving(guard,self.data,poll_seconds=10) as runner:
                self.assertTrue(entered.wait(5))
                self.assertEqual(worker.get_status(self.owner),{'available':False,'state':'starting'})
                with self.assertRaises(flows.Unavailable):worker.get_status(object())
                release.set();self.assertTrue(runner.ready.wait(5))
                self.assertEqual(worker.get_status(self.owner),{'available':True,'state':'running'})
        self.assertEqual(worker.get_status(self.owner),{'available':False,'state':'not_running'})

    def test_worker_failure_is_reported_without_a_false_ready_or_private_diagnostic(self):
        release=Event()
        def fail(**kwargs):self.assertTrue(release.wait(5));raise RuntimeError('PRIVATE failure details')
        with install_lock(self.data) as guard,patch.object(worker,'work_once',side_effect=fail), \
                self.assertLogs('backend.maipa.workflows.worker',level='ERROR') as logs:
            with self.assertRaises(StorageUnavailable):
                with worker.serving(guard,self.data,poll_seconds=10) as runner:
                    release.set();self.assertTrue(runner.failed.wait(5))
                    self.assertEqual(worker.get_status(self.owner),{'available':False,'state':'failed'})
                    self.assertFalse(runner.ready.is_set())
        self.assertNotIn('PRIVATE',str(logs.output))

    def test_real_worker_accepts_native_dispatch_restarts_and_finishes_from_durable_origin(self):
        from backend.maipa.workflows import native
        native.prepare_graph(self.owner,self.saved['id'],1);grant(self,'run')
        binding=native.bind_principal(self.owner,str(self.account.pk),self.ui,self.principal)
        actor=native.actor(self.owner,binding)
        with install_lock(self.data) as guard:
            with worker.serving(guard,self.data,poll_seconds=10) as runner:
                self.assertTrue(runner.ready.wait(5))
                run=native.dispatch(actor,self.saved['id'],1,{'name':'Ada'},'real-worker')
                self.assertEqual((run['status'],run['worker']),('queued',{'available':True,'state':'running'}))
            self.assertEqual(WorkflowRun.objects.get(pk=run['id']).step,0)
            with worker.serving(guard,self.data,poll_seconds=0.01) as resumed:
                self.assertTrue(resumed.ready.wait(5));deadline=time.monotonic()+5
                while WorkflowRun.objects.get(pk=run['id']).status!='completed' and time.monotonic()<deadline:time.sleep(0.02)
                self.assertEqual(flows.get_run(self.owner,run['id'])['outputs']['result'],'Ready Ada')
        self.assertFalse(resumed.thread.is_alive())

    def test_native_migration_cannot_strip_provenance_into_local_owner_authority(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from backend.maipa.workflows import native
        from backend.maipa.workflows.models import NativeRunAuthority
        native.prepare_graph(self.owner,self.saved['id'],1);grant(self,'run')
        actor=native.actor(self.owner,native.bind_principal(self.owner,str(self.account.pk),self.ui,self.principal))
        with patch.object(worker,'runtime_status',return_value={'available':True,'state':'running'}):
            run=native.dispatch(actor,self.saved['id'],1,{},'migration-boundary')
        try:
            with self.assertRaisesRegex(RuntimeError,'offline backup recovery'):
                MigrationExecutor(connection).migrate([('workflows','0002_worker_origin')])
            self.assertEqual(WorkflowRun.objects.get(pk=run['id']).origin_kind,'native')
            self.assertEqual(NativeRunAuthority.objects.filter(run_id=run['id']).count(),1)
        finally:
            executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes())
