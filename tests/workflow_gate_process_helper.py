"""Finite synthetic human-gate persistence fixture, never a serving process."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
database,action,*arguments=sys.argv[1:];database=Path(database).resolve()
if database.name!='synthetic.sqlite3' or not database.parent.name.startswith('maipa-gate-process-'):
    raise ValueError('Synthetic fixture required')
from django.conf import settings
settings.configure(SECRET_KEY='synthetic-human-gate-process',USE_TZ=True,DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
    INSTALLED_APPS=['django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions',
        'backend.maipa.workspace','backend.maipa.intake','backend.maipa.policies','backend.maipa.agent_accounts','backend.maipa.workflows'],
    DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':str(database)}})
import django
django.setup()
from django.contrib.auth import get_user_model
from django.core.management import call_command
from backend.maipa.workspace.models import Installation,Membership
from backend.maipa.workflows import services as flows,gates
from backend.maipa.workflows.models import WorkflowRun,NodeCheckpoint,HumanResponse
from backend.maipa.workflows.worker import process_job
if action=='prepare':
    call_command('migrate',interactive=False,verbosity=0)
    install=Installation.objects.create();user=get_user_model().objects.create_user(username='synthetic-gate-owner')
    Membership.objects.create(user=user,installation=install,role='preparer')
    from tests.test_workflow_gates import gated_graph
    saved=flows.save_definition(user,'Synthetic process gate',gated_graph(arguments[0]))
    run=flows.dispatch(user,saved['id'],1,{'name':'Ada','approved':True,'values':{'answer':'Injected'}},'synthetic-gate')
    process_job(run['id'],0);process_job(run['id'],1)
else:
    user=get_user_model().objects.get(username='synthetic-gate-owner');run={'id':str(WorkflowRun.objects.get().pk)}
if action=='answer':
    import uuid
    pending=gates.get_pending(user,run['id'])
    gates.respond(user,run['id'],pending['id'],expected_step=pending['step'],definition_revision=pending['definition_revision'],
        request_sha256=pending['request_sha256'],request_key=str(uuid.uuid4()),answer=json.loads(arguments[0]))
elif action=='finish':
    for _ in range(6):
        row=WorkflowRun.objects.get()
        if row.status not in ('queued','running'):break
        process_job(str(row.pk),row.step)
elif action not in ('prepare','read'):raise ValueError('Unknown synthetic action')
result=flows.get_run(user,run['id']);result['pending']=gates.get_pending(user,run['id'])
result['first_clocks']={row.node_id:row.first_attempt_at.isoformat() for row in NodeCheckpoint.objects.all()}
result['responses']=HumanResponse.objects.count()
print(json.dumps(result,sort_keys=True))
