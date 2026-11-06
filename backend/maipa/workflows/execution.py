"""Human session controls over the existing durable worker, never a browser runner."""
from django.db import transaction
from . import services,drafts,gates,worker,selection
from .errors import Unavailable,Conflict
from .models import WorkflowRun


class WorkerUnavailable(Unavailable):pass


def available():
    status=worker.runtime_status()
    if not status['available']:raise WorkerUnavailable()
    return status


@transaction.atomic
def snapshot(user,draft_id):
    member=services._principal(user);row=drafts.get(user,draft_id)
    publication=row['publication'];runs=[];selected=selection.read(user,draft_id)
    if publication:
        definition=services._definition(member,publication['definition_id'])
        for run in WorkflowRun.objects.select_related('version').filter(definition=definition,
                origin_owner_id=member.user_id,origin_installation_id=member.installation_id).order_by('-created_at','-id')[:10]:
            if not services._origin_matches(run):raise Unavailable()
            runs.append(services._run_value(run))
        if selected['run_id'] and selected['run_id'] not in {r['id'] for r in runs}:
            runs=runs[:9]+[services._run_value(services._run(member,selected['run_id']))]
    return {'draft_id':row['id'],'draft_revision':row['revision'],'publication':publication,
        'worker':worker.runtime_status(),'runs':runs,'selection':selected}


@transaction.atomic
def dispatch(user,draft_id,*,expected_revision,definition_revision,request_key,seed):
    row=drafts.get(user,draft_id);publication=row['publication']
    if (row['revision']!=services._revision(expected_revision) or not publication
            or publication['draft_revision']!=row['revision']
            or publication['definition_revision']!=services._revision(definition_revision)):raise Conflict()
    status=available()
    existed=WorkflowRun.objects.filter(definition_id=publication['definition_id'],dispatch_key=request_key).exists()
    run=services.dispatch(user,publication['definition_id'],definition_revision,seed,request_key)
    chosen=selection.read(user,draft_id) if existed else selection.focus(user,draft_id,run['id'],source='human')
    return {'run':run,'worker':status,'gate':None,'selection':chosen}


@transaction.atomic
def inspect(user,run_id):
    run=gates._current_run(user,run_id)
    return {'run':services._run_value(run),'worker':worker.runtime_status(),'gate':gates.get_pending(user,run_id)}


@transaction.atomic
def control(user,run_id,operation,expected_step):
    gates._current_run(user,run_id)
    if operation=='resume':available();services.resume(user,run_id,expected_step)
    elif operation=='cancel':services.cancel(user,run_id,expected_step)
    else:raise Unavailable()
    return inspect(user,run_id)
