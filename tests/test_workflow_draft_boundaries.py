"""Actual DB races and schema reversal must not lose shared authoring work."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid
from django.contrib.auth import get_user_model
from django.db import close_old_connections,connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from backend.maipa.workflows import drafts,services
from backend.maipa.workflows.models import WorkflowDraft,WorkflowRun
from tests.test_workflows import fixture
from tests.test_workflow_drafts import graph


class WorkflowDraftBoundaries(TransactionTestCase):
    def test_two_real_connections_accept_only_one_original_revision(self):
        install,owner,other,member,client,entity=fixture()
        first=drafts.create(owner,'Racing authors',str(uuid.uuid4()))
        barrier=Barrier(2)
        def change(text):
            close_old_connections()
            try:
                actor=get_user_model().objects.get(pk=owner.pk);barrier.wait(timeout=5)
                try:drafts.change(actor,first['id'],0,graph(text));return 'accepted'
                except services.Conflict:return 'conflict'
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(change,('Human','Native')))
        self.assertCountEqual(outcomes,['accepted','conflict'])
        saved=drafts.get(owner,first['id']);self.assertEqual(saved['revision'],1)
        self.assertIn(saved['graph']['nodes'][1]['data']['config']['expression'],('Human','Native'))
        self.assertEqual(WorkflowDraft.objects.count(),1);self.assertFalse(WorkflowRun.objects.exists())

    def test_reverse_migration_refuses_before_dropping_saved_authoring_work(self):
        install,owner,other,member,client,entity=fixture()
        first=drafts.create(owner,'Retained authoring',str(uuid.uuid4()))
        saved=drafts.change(owner,first['id'],0,graph('Keep this user work'))
        try:
            with self.assertRaisesRegex(RuntimeError,'offline backup recovery'):
                MigrationExecutor(connection).migrate([('workflows','0004_human_gates')])
            self.assertEqual(drafts.get(owner,first['id']),saved)
        finally:
            executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes())
