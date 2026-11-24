"""Scheduled workflow runs: approved once, started by the worker, once per time slot, never without the owner's current access."""
from datetime import datetime, timedelta, timezone as tz
from pathlib import Path
import tempfile
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from backend.maipa.approvals import services as approvals
from backend.maipa.approvals.models import ActionRequest
from backend.maipa.storage import prepare_directory, prepare_files
from backend.maipa.workflows import drafts, schedules, services as flows, worker
from backend.maipa.workflows.models import RunSchedule, WorkflowRun
from backend.maipa.workspace.models import Installation, Membership
from tests.test_workflow_drafts import graph


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=tz.utc)


class WorkflowScheduleTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='maipa-flow-schedule-'); self.addCleanup(temporary.cleanup)
        data = prepare_directory(Path(temporary.name).resolve() / 'install'); prepare_files(data)
        configured = override_settings(DATA_ROOT=data); configured.enable(); self.addCleanup(configured.disable)
        self.install = Installation.objects.create()
        self.owner = get_user_model().objects.create_user(username='schedule-owner')
        self.other = get_user_model().objects.create_user(username='schedule-other')
        self.member = Membership.objects.create(user=self.owner, installation=self.install, role='preparer')
        Membership.objects.create(user=self.other, installation=self.install, role='administrator')
        self.draft = drafts.create(self.owner, 'Morning summary', str(uuid.uuid4()))
        drafts.change(self.owner, self.draft['id'], 0, graph())
        self.publication = drafts.publish(self.owner, self.draft['id'], 1)
        self.created = at('2026-03-10T08:30:00')

    def ask(self, user=None, **changes):
        payload = {'draft_id': self.draft['id'], 'definition_revision': 1, 'every': 'day', 'hour': 9, **changes}
        return approvals.request_action(user or self.owner, {'operation': 'workflow.schedule', 'summary': 'Run the morning summary daily', 'payload': payload})

    def approve(self, item, user=None):
        with patch('backend.maipa.workflows.schedules.timezone.now', return_value=self.created):
            return approvals.decide(user or self.owner, item['id'], {'decision': 'approve', 'payload_hash': item['payload_hash']})

    def scheduled(self, **changes):
        done = self.approve(self.ask(**changes))
        self.assertEqual(done['state'], 'approved', done)
        return done['result']['id']

    def keys(self):
        return sorted(WorkflowRun.objects.values_list('dispatch_key', flat=True))

    def test_an_approved_schedule_starts_one_run_per_slot_with_the_pinned_version_and_seed(self):
        with patch('backend.maipa.workflows.schedules.timezone.now', return_value=self.created):
            item = self.ask(seed={'name': 'Monday'})
        self.assertEqual(item['detail'], {'workflow': 'Morning summary', 'version': 1, 'every': 'day', 'hour': 9, 'weekday': None, 'day': None,
                                          'input': {'name': 'Monday'}, 'next': '2026-03-10T09:00:00+00:00', 'current': True})
        self.assertEqual((RunSchedule.objects.count(), WorkflowRun.objects.count()), (0, 0))
        done = self.approve(item)
        self.assertEqual(done['state'], 'approved')
        saved = RunSchedule.objects.get(pk=done['result']['id'])
        self.assertEqual((saved.owner_id, saved.version.revision, saved.seed, saved.stopped_at, saved.last_slot), (self.owner.pk, 1, {'name': 'Monday'}, None, None))
        # Nothing is due before the first slot after approval; then each slot starts exactly one run.
        self.assertEqual(schedules.run_due(at('2026-03-10T08:59:59')), {'started': 0, 'skipped': 0})
        self.assertEqual(schedules.run_due(at('2026-03-10T09:00:00')), {'started': 1, 'skipped': 0})
        self.assertEqual(schedules.run_due(at('2026-03-10T09:00:20')), {'started': 0, 'skipped': 0})
        self.assertEqual(schedules.run_due(at('2026-03-10T23:59:00')), {'started': 0, 'skipped': 0})
        self.assertEqual(schedules.run_due(at('2026-03-11T09:03:00')), {'started': 1, 'skipped': 0})
        self.assertEqual(self.keys(), [f'schedule:{saved.pk}:2026-03-10T09', f'schedule:{saved.pk}:2026-03-11T09'])
        run = WorkflowRun.objects.order_by('created_at').first()
        self.assertEqual((run.origin_owner_id, run.origin_kind, run.version.revision, run.seed, run.status), (self.owner.pk, 'local', 1, {'name': 'Monday'}, 'queued'))
        # A later published version does not change what the schedule runs.
        drafts.change(self.owner, self.draft['id'], 1, graph('Changed {{ in.name }}'))
        drafts.publish(self.owner, self.draft['id'], 2)
        self.assertEqual(schedules.run_due(at('2026-03-12T09:00:00')), {'started': 1, 'skipped': 0})
        self.assertEqual(WorkflowRun.objects.get(dispatch_key=f'schedule:{saved.pk}:2026-03-12T09').version.revision, 1)
        # The worker finishes a scheduled run like any other and its output uses the seed.
        for step in range(3):
            self.assertEqual(worker.process_job(str(run.pk), step), 'advanced')
        self.assertEqual(flows.get_run(self.owner, str(run.pk))['outputs']['result'], 'Ready Monday')
        self.assertEqual(self.approve(item)['result'], done['result'])
        self.assertEqual(RunSchedule.objects.count(), 1)

    def test_missed_slots_are_not_made_up_and_week_and_month_slots_fall_on_their_day(self):
        daily = self.scheduled()
        # Three days pass with the worker stopped: only the latest slot runs.
        self.assertEqual(schedules.run_due(at('2026-03-13T15:00:00')), {'started': 1, 'skipped': 0})
        self.assertEqual(self.keys(), [f'schedule:{daily}:2026-03-13T09'])
        schedules.stop(self.owner, daily)
        weekly = self.scheduled(every='week', hour=6, weekday=0)             # Mondays; 2026-03-10 is a Tuesday.
        self.assertEqual(schedules.run_due(at('2026-03-15T23:00:00')), {'started': 0, 'skipped': 0})
        self.assertEqual(schedules.run_due(at('2026-03-16T06:00:00')), {'started': 1, 'skipped': 0})
        self.assertEqual(schedules.run_due(at('2026-03-22T23:00:00')), {'started': 0, 'skipped': 0})
        schedules.stop(self.owner, weekly)
        monthly = self.scheduled(every='month', hour=0, day=31)              # Clamped to the last day of shorter months.
        self.assertEqual(schedules.run_due(at('2026-03-30T23:00:00')), {'started': 0, 'skipped': 0})
        self.assertEqual(schedules.run_due(at('2026-03-31T00:00:00')), {'started': 1, 'skipped': 0})
        self.assertEqual(schedules.run_due(at('2026-04-29T23:59:00')), {'started': 0, 'skipped': 0})
        self.assertEqual(schedules.run_due(at('2026-04-30T00:10:00')), {'started': 1, 'skipped': 0})
        self.assertEqual(self.keys(), sorted([f'schedule:{daily}:2026-03-13T09', f'schedule:{weekly}:2026-03-16T06',
                                              f'schedule:{monthly}:2026-03-31T00', f'schedule:{monthly}:2026-04-30T00']))
        listed = {row['id']: row for row in schedules.listing(self.owner, at('2026-04-30T00:10:00'))}
        self.assertEqual((listed[daily]['active'], listed[daily]['next'], listed[weekly]['active']), (False, None, False))
        self.assertEqual(listed[monthly], {'id': monthly, 'draft_id': self.draft['id'], 'workflow': 'Morning summary', 'version': 1, 'every': 'month', 'hour': 0,
            'weekday': None, 'day': 31, 'active': True, 'next': '2026-05-31T00:00:00+00:00', 'last_slot': '2026-04-30T00', 'last_result': 'started'})

    def test_a_slot_does_not_run_without_the_owners_current_access_and_is_not_made_up_later(self):
        saved = self.scheduled()
        Membership.objects.filter(pk=self.member.pk).update(enabled=False)
        self.assertEqual(schedules.run_due(at('2026-03-10T09:00:00')), {'started': 0, 'skipped': 1})
        self.assertEqual(schedules.run_due(at('2026-03-10T09:01:00')), {'started': 0, 'skipped': 0})
        Membership.objects.filter(pk=self.member.pk).update(enabled=True)
        self.assertEqual(schedules.run_due(at('2026-03-10T09:02:00')), {'started': 0, 'skipped': 0})
        self.assertEqual((WorkflowRun.objects.count(), RunSchedule.objects.get(pk=saved).last_result), (0, 'no_access'))
        self.assertEqual(schedules.run_due(at('2026-03-11T09:00:00')), {'started': 1, 'skipped': 0})
        # A reader role and an inactive account are refused the same way.
        Membership.objects.filter(pk=self.member.pk).update(role='viewer')
        self.assertEqual(schedules.run_due(at('2026-03-12T09:00:00')), {'started': 0, 'skipped': 1})
        Membership.objects.filter(pk=self.member.pk).update(role='preparer')
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        self.assertEqual(schedules.run_due(at('2026-03-13T09:00:00')), {'started': 0, 'skipped': 1})
        self.assertEqual(WorkflowRun.objects.count(), 1)

    def test_a_stopped_schedule_starts_nothing_and_stopping_needs_its_own_approval(self):
        saved = self.scheduled()
        stop = approvals.request_action(self.owner, {'operation': 'workflow.unschedule', 'summary': 'Stop the daily run', 'payload': {'schedule_id': saved}})
        self.assertEqual(stop['detail'], {'workflow': 'Morning summary', 'version': 1, 'every': 'day', 'hour': 9, 'weekday': None, 'day': None, 'input': {}, 'current': True})
        self.assertEqual(schedules.run_due(at('2026-03-10T09:00:00')), {'started': 1, 'skipped': 0})    # Still active until approved.
        done = approvals.decide(self.owner, stop['id'], {'decision': 'approve', 'payload_hash': stop['payload_hash']})
        self.assertEqual((done['state'], done['result']), ('approved', {'id': saved, 'active': False}))
        self.assertEqual(schedules.run_due(at('2026-03-11T09:00:00')), {'started': 0, 'skipped': 0})
        self.assertEqual(WorkflowRun.objects.count(), 1)
        with self.assertRaises(approvals.ApprovalError) as refused:
            approvals.request_action(self.owner, {'operation': 'workflow.unschedule', 'summary': 'Stop it again', 'payload': {'schedule_id': saved}})
        self.assertEqual(refused.exception.code, 'workflow_schedule_stopped')

    def test_unpublished_foreign_malformed_and_excess_schedules_are_refused_before_anything_is_saved(self):
        unpublished = drafts.create(self.owner, 'Not published', str(uuid.uuid4()))
        foreign = drafts.create(self.other, 'Someone else', str(uuid.uuid4()))
        drafts.change(self.other, foreign['id'], 0, graph()); drafts.publish(self.other, foreign['id'], 1)
        cases = ((dict(draft_id=unpublished['id']), 'workflow_not_published'), (dict(draft_id=foreign['id']), 'workflow_unavailable'),
                 (dict(draft_id=str(uuid.uuid4())), 'workflow_unavailable'), (dict(definition_revision=2), 'workflow_not_published'),
                 (dict(every='hour'), 'workflow_schedule_invalid'), (dict(hour=24), 'workflow_schedule_invalid'), (dict(hour='9'), 'workflow_schedule_invalid'),
                 (dict(hour=True), 'workflow_schedule_invalid'), (dict(weekday=1), 'workflow_schedule_invalid'), (dict(day=3), 'workflow_schedule_invalid'),
                 (dict(every='week'), 'workflow_schedule_invalid'), (dict(every='week', weekday=7), 'workflow_schedule_invalid'),
                 (dict(every='month'), 'workflow_schedule_invalid'), (dict(every='month', day=0), 'workflow_schedule_invalid'),
                 (dict(every='month', day=32), 'workflow_schedule_invalid'), (dict(seed=[1]), 'workflow_schedule_invalid'), (dict(seed={'nested': {'a': 1}}), 'workflow_schedule_invalid'),
                 (dict(seed={str(n): n for n in range(21)}), 'workflow_schedule_invalid'),
                 (dict(seed={'big': 'x' * 2000}), 'workflow_schedule_invalid'), (dict(draft_id='not-an-id'), 'workflow_unavailable'))
        for changes, code in cases:
            with self.assertRaises(approvals.ApprovalError, msg=changes) as refused:
                self.ask(**changes)
            self.assertEqual(refused.exception.code, code, changes)
        with self.assertRaises(approvals.ApprovalError):
            self.ask(force=True)
        with self.assertRaises(approvals.ApprovalError) as refused:
            self.ask(self.other)
        self.assertEqual(refused.exception.code, 'workflow_unavailable')
        with self.assertRaises(approvals.ApprovalError) as refused:
            approvals.request_action(self.owner, {'operation': 'workflow.unschedule', 'summary': 'Stop nothing', 'payload': {'schedule_id': str(uuid.uuid4())}})
        self.assertEqual(refused.exception.code, 'workflow_unavailable')
        self.assertEqual((ActionRequest.objects.count(), RunSchedule.objects.count()), (0, 0))
        # Another person cannot stop or see this owner's schedule.
        saved = self.scheduled()
        with self.assertRaises(approvals.ApprovalError):
            approvals.request_action(self.other, {'operation': 'workflow.unschedule', 'summary': 'Stop theirs', 'payload': {'schedule_id': saved}})
        self.assertEqual(schedules.listing(self.other, self.created), [])
        # The number of active schedules per person is limited; the request past the limit fails at approval.
        waiting = self.ask(hour=10)
        with patch('backend.maipa.workflows.schedules.MAX_ACTIVE', 1):
            with self.assertRaises(approvals.ApprovalError) as refused:
                self.ask(hour=11)
            self.assertEqual(refused.exception.code, 'workflow_schedule_limit')
            done = self.approve(waiting)
        self.assertEqual((done['state'], done['error'], RunSchedule.objects.count()), ('failed', 'workflow_schedule_limit', 1))

    def test_a_person_cannot_use_a_schedule_slot_key_and_a_refused_slot_is_recorded(self):
        saved = self.scheduled()
        with self.assertRaises(ValueError):
            flows.dispatch(self.owner, self.publication['definition_id'], 1, {}, f'schedule:{saved}:2026-03-10T09')
        self.assertEqual(WorkflowRun.objects.count(), 0)
        with patch('backend.maipa.workflows.schedules.services._dispatch_row', side_effect=flows.Conflict()):
            self.assertEqual(schedules.run_due(at('2026-03-10T09:00:00')), {'started': 0, 'skipped': 1})
        self.assertEqual(RunSchedule.objects.get(pk=saved).last_result, 'refused')
        self.assertEqual(schedules.run_due(at('2026-03-10T09:05:00')), {'started': 0, 'skipped': 0})
        with patch('backend.maipa.workflows.schedules.services._dispatch_row', side_effect=flows.Unavailable()):
            self.assertEqual(schedules.run_due(at('2026-03-11T09:00:00')), {'started': 0, 'skipped': 1})
        self.assertEqual((RunSchedule.objects.get(pk=saved).last_result, WorkflowRun.objects.count()), ('unavailable', 0))

    def test_the_worker_checks_schedules_at_most_every_few_seconds_and_keeps_working_runs(self):
        saved = self.scheduled()
        clock = [100.0]
        with patch('backend.maipa.workflows.worker.monotonic', side_effect=lambda: clock[0]), patch('backend.maipa.workflows.worker._DUE_AT', 0.0), \
                patch('backend.maipa.workflows.schedules.timezone.now', return_value=at('2026-03-10T09:00:01')):
            worker._start_due()
            self.assertEqual(self.keys(), [f'schedule:{saved}:2026-03-10T09'])
            self.assertEqual(worker.work_once(limit=1), {'observed': 1, 'advanced': 1, 'blocked': 0, 'stale': 0})
            with patch('backend.maipa.workflows.schedules.run_due') as checked:
                clock[0] = 114.0; worker._start_due(); self.assertEqual(checked.call_count, 0)
                clock[0] = 115.5; worker._start_due(); self.assertEqual(checked.call_count, 1)

    def test_the_serving_worker_starts_due_runs_before_each_queue_pass(self):
        from threading import Event
        from backend.maipa.storage import install_lock
        order, seen = [], Event()

        def passed(**kwargs):
            order.append('work'); seen.set()
        with tempfile.TemporaryDirectory(prefix='maipa-schedule-serving-') as directory:
            root = Path(directory).resolve()
            with install_lock(root) as guard, patch('backend.maipa.workflows.worker._start_due', side_effect=lambda: order.append('due')), \
                    patch('backend.maipa.workflows.worker.work_once', side_effect=passed):
                with worker.serving(guard, root, poll_seconds=0.01):
                    self.assertTrue(seen.wait(5))
        self.assertEqual(order[:2], ['due', 'work'])

    def test_the_agent_workflow_list_shows_the_schedules_and_each_published_version(self):
        from backend.maipa.agent_accounts.workflow_tools import schedule_summary
        saved = self.scheduled()
        value = schedule_summary(self.owner)
        self.assertEqual([(row['id'], row['draft_id'], row['active']) for row in value['schedules']], [(saved, self.draft['id'], True)])
        self.assertEqual(value['published'], {self.draft['id']: 1})
        self.assertEqual(schedule_summary(self.other), {'schedules': [], 'published': {}})
