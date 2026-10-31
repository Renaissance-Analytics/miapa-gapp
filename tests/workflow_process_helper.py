"""Finite synthetic workflow worker used only by process recovery tests."""
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
database,action,*arguments=sys.argv[1:]
database=Path(database).resolve()
if not database.is_absolute() or database.name!='synthetic.sqlite3' or not database.parent.name.startswith('maipa-flow-process-'):
    raise ValueError('Synthetic process fixture required')

from django.conf import settings
settings.configure(SECRET_KEY='synthetic-workflow-process-test',USE_TZ=True,DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
    INSTALLED_APPS=['django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions',
        'backend.maipa.workspace','backend.maipa.intake','backend.maipa.policies','backend.maipa.agent_accounts','backend.maipa.workflows'],
    DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':str(database)}})
import django
django.setup()

from django.contrib.auth import get_user_model
from django.core.management import call_command
from backend.maipa.workspace.models import Installation,Membership
from backend.maipa.workflows import services as flows
from backend.maipa.workflows.models import WorkflowRun
from backend.maipa.workflows.store import DjangoClaimStore

if action=='prepare':
    call_command('migrate',interactive=False,verbosity=0)
    installation=Installation.objects.create();user=get_user_model().objects.create_user(username='synthetic-process-owner')
    Membership.objects.create(user=user,installation=installation,role='preparer')
    schema={'version':1,'graph':{'nodes':[{'id':'entry','kind':'manual_trigger','config':{}},
        {'id':'answer','kind':'output','config':{}}],'edges':[{'id':'connection','source':'entry','target':'answer'}]}}
    definition=flows.save_definition(user,'Synthetic process recovery',schema)
    result=flows.dispatch(user,definition['id'],1,{'name':'Synthetic process'},'synthetic-delivery')
else:
    user=get_user_model().objects.get(username='synthetic-process-owner');run=WorkflowRun.objects.get()
    if action=='read':
        result=flows.get_run(user,str(run.pk))
        result['first_clocks']={row.node_id:row.first_attempt_at.isoformat() for row in run.checkpoints.all()}
    elif action=='advance':
        result=flows.advance(user,str(run.pk),int(arguments[0]))
    elif action=='crash':
        marker=Path(arguments[0]).resolve()
        if marker.parent!=database.parent: raise ValueError('Synthetic marker required')
        original=DjangoClaimStore.complete
        def interrupted(store,*values):
            original(store,*values)
            marker.write_text('uncommitted-real-node-output',encoding='utf-8')
            sys.stdin.read(1) # The test kills this finite owned process here.
            raise RuntimeError('Crash test must terminate the worker')
        DjangoClaimStore.complete=interrupted
        flows.advance(user,str(run.pk),0)
        raise AssertionError('Interrupted transaction unexpectedly returned')
    else:
        raise ValueError('Unknown fixture action')
print(json.dumps(result,sort_keys=True))
