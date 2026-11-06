"""Owned persisted navigation, separate from grants and immutable run origin."""
from django.db import transaction
from . import services,drafts
from .models import WorkflowSelection,WorkflowRun
from .errors import Unavailable,Conflict
from .schema import require

def _scope(user,draft_id):return services._principal(user),drafts.get(user,draft_id)

def _target(member,draft,run_id):
    if run_id is None:return None
    run=services._run(member,run_id);publication=draft['publication']
    if (not publication or str(run.definition_id)!=publication['definition_id']
            or run.version.revision>publication['definition_revision'] or not services._origin_matches(run)):raise Unavailable()
    return run

@transaction.atomic
def read(user,draft_id):
    member,draft=_scope(user,draft_id);row=WorkflowSelection.objects.filter(draft_id=draft_id).first()
    if row:
        _target(member,draft,str(row.run_id) if row.run_id else None)
        return {'revision':row.revision,'run_id':str(row.run_id) if row.run_id else None,'show_details':row.show_details,'source':row.source}
    publication=draft['publication'];latest=None
    if publication:latest=WorkflowRun.objects.filter(definition_id=publication['definition_id'],
        origin_owner_id=member.user_id,origin_installation_id=member.installation_id).order_by('-created_at','-id').first()
    if latest:_target(member,draft,str(latest.pk))
    return {'revision':0,'run_id':str(latest.pk) if latest else None,'show_details':False,'source':'default'}

@transaction.atomic
def change(user,draft_id,*,expected_revision,run_id,show_details,source='human'):
    require(type(show_details) is bool and source in ('human','agent'))
    expected_revision=services._revision(expected_revision);member,draft=_scope(user,draft_id);current=read(user,draft_id)
    if expected_revision!=current['revision'] or expected_revision>=1_999_999_999:raise Conflict()
    target=_target(member,draft,run_id);row=WorkflowSelection.objects.filter(draft_id=draft_id).first()
    if row and (current['run_id'],current['show_details'])==(run_id,show_details):return current
    if row is None:row=WorkflowSelection(draft_id=draft_id)
    row.run=target;row.show_details=show_details;row.source=source;row.revision=expected_revision+1;row.save()
    return read(user,draft_id)

@transaction.atomic
def focus(user,draft_id,run_id,*,source):
    current=read(user,draft_id)
    return change(user,draft_id,expected_revision=current['revision'],run_id=run_id,
        show_details=True if source=='agent' else current['show_details'],source=source)
