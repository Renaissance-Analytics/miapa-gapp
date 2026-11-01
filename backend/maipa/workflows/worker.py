"""Bounded deterministic dispatch, owned by the Genie site's installation lease.

No external effects, TTL claim theft, payload-selected principal or extra queue
engine. Every observed (run, step) is fenced inside the same authority transaction
as human commands. Process death rolls back the entire deterministic node.
"""
from contextlib import contextmanager
import logging
from threading import Event, Thread, RLock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections, transaction
from django.db.models import F

from backend.maipa.storage import InstallationLease, StorageUnavailable
from backend.maipa.workspace.models import Installation, Membership
from . import services
from .errors import Conflict, InvalidWorkflow, Unavailable
from .models import WorkflowRun

logger=logging.getLogger(__name__)
_ACTIVE=None
_STATUS_LOCK=RLock()


@transaction.atomic
def process_job(run_id, expected_step):
    """Trusted internal observation, never a public user/role selection API."""
    Installation.objects.filter(slot=1).update(access_revision=F('access_revision'))
    services._revision(expected_step)
    run=WorkflowRun.objects.select_related('definition','version').filter(pk=services.identity(run_id)).first()
    if run is None or run.step!=expected_step or run.status not in ('queued','running'): return 'stale'
    reason=None
    if run.origin_owner_id is None or run.origin_installation_id is None:
        reason='origin_unverified'
    elif not services._origin_matches(run):
        reason='origin_changed'
    else:
        member=Membership.objects.select_related('installation').filter(user_id=run.origin_owner_id,
            installation_id=run.origin_installation_id,installation__slot=1,user__is_active=True,
            enabled=True,role__in=('administrator','preparer')).first()
        if member is None: reason='authority_revoked'
        else:
            try: services._entity(member,run.origin_entity_id)
            except Unavailable: reason='scope_revoked'
    if reason is None and run.origin_kind=='native':
        from .native import guard_run
        if not guard_run(run): reason='native_authority_revoked'
    if reason is None:
        try:
            # A refused snapshot/step must leave no partial checkpoint. The
            # safe blocked reason commits in the outer authority transaction.
            with transaction.atomic():
                user=get_user_model().objects.get(pk=run.origin_owner_id)
                services.advance(user,str(run.pk),expected_step)
            return 'advanced'
        except Conflict: return 'stale'
        except (Unavailable,InvalidWorkflow): reason='snapshot_unavailable'
    run.status,run.error='blocked',reason
    run.step+=1
    run.save(update_fields=('status','error','step'))
    return 'blocked'


def work_once(*,limit=1):
    if type(limit) is not int or not 1<=limit<=100: raise ValueError('Invalid worker limit')
    observed=list(WorkflowRun.objects.filter(status__in=('queued','running')).order_by('created_at','id')
                  .values_list('id','step')[:limit])
    counts={'observed':len(observed),'advanced':0,'blocked':0,'stale':0}
    for run_id,step in observed: counts[process_job(str(run_id),step)]+=1
    return counts


class _ServingWorker:
    def __init__(self,guard,root,poll_seconds):
        self.guard,self.root,self.poll_seconds=guard,root,poll_seconds
        self.stop,self.failed=Event(),Event()
        self.ready=Event()
        self.thread=Thread(target=self._loop,name='maipa-flow-worker',daemon=False)

    def _loop(self):
        try:
            while not self.stop.is_set():
                self.guard.revalidate(self.root)
                work_once(limit=1)
                self.ready.set()
                if self.stop.wait(self.poll_seconds): break
        except Exception:
            # Never log exceptions, paths, seeds, node config or private IDs.
            self.failed.set()
            logger.error('Workflow worker unavailable')
        finally:
            connections.close_all()


@contextmanager
def serving(guard,root,*,poll_seconds=0.5):
    global _ACTIVE
    if not isinstance(guard,InstallationLease): raise StorageUnavailable('Workflow worker unavailable')
    guard.revalidate(root)
    if type(poll_seconds) not in (int,float) or not 0.01<=poll_seconds<=10:
        raise StorageUnavailable('Workflow worker unavailable')
    runner=_ServingWorker(guard,root,poll_seconds)
    with _STATUS_LOCK:
        if _ACTIVE is not None: raise StorageUnavailable('Workflow worker unavailable')
        _ACTIVE=runner
    try:
        runner.thread.start()
        yield runner
    finally:
        runner.stop.set()
        # Do not release the installation lease while an accepted deterministic
        # transaction is finishing. There is no unbounded I/O executor here.
        if runner.thread.ident is not None: runner.thread.join()
        with _STATUS_LOCK:
            if _ACTIVE is runner: _ACTIVE=None
    if runner.failed.is_set(): raise StorageUnavailable('Workflow worker unavailable')


def runtime_status():
    """Process-local worker evidence, not a claim about HTTP or provider health."""
    with _STATUS_LOCK:
        runner=_ACTIVE
        state='not_running'
        if runner is not None:
            state='starting'
            if runner.failed.is_set(): state='failed'
            elif runner.stop.is_set(): state='stopping'
            elif runner.thread.is_alive() and runner.ready.is_set():
                try:
                    runner.guard.revalidate(settings.DATA_ROOT)
                    state='running'
                except StorageUnavailable:
                    state='failed'
        return {'available':state=='running','state':state}


@transaction.atomic
def get_status(user):
    services._principal(user)
    return runtime_status()
