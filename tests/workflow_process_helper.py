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

if action=='legacy':
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    target=[('workflows','0001_initial')]
    executor=MigrationExecutor(connection);executor.migrate(target)
    apps=executor.loader.project_state(target).apps
    install=apps.get_model('workspace','Installation').objects.create()
    owner=apps.get_model('auth','User').objects.create(username='synthetic-legacy-owner')
    definition=apps.get_model('workflows','Definition').objects.create(owner_id=owner.pk,installation_id=install.pk,name='Private legacy')
    version=apps.get_model('workflows','DefinitionVersion').objects.create(definition=definition,revision=1,
        schema={'legacy':'private graph'},schema_sha256='a'*64,runtime='fancy-flow/0.29.0')
    runs=apps.get_model('workflows','WorkflowRun')
    ids=[]
    for status in ('queued','running','completed'):
        item=runs.objects.create(definition=definition,version=version,dispatch_key=status,seed={'private':'legacy seed'},
            seed_sha256='b'*64,access_revision=1,status=status)
        ids.append(str(item.pk))
        apps.get_model('workflows','NodeCheckpoint').objects.create(run=item,node_id='legacy',status='completed',
            output={'private':'legacy output'},attempts=1)
    call_command('migrate',interactive=False,verbosity=0)
    rows=WorkflowRun.objects.order_by('dispatch_key')
    result={'ids':sorted(str(row.pk) for row in rows), 'expected_ids':sorted(ids),
        'states':{row.dispatch_key:[row.status,row.error,row.origin_owner_id,row.origin_installation_id,row.origin_entity_id] for row in rows},
        'seeds':[row.seed for row in rows],'outputs':[row.checkpoints.get().output for row in rows]}
    from backend.maipa.workflows.worker import work_once
    result['queue']=work_once(limit=10)
elif action=='prepare':
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
    elif action=='worker':
        from backend.maipa.workflows.worker import process_job
        if len(arguments)>1:
            marker,gate=map(lambda value:Path(value).resolve(),arguments[1:])
            if marker.parent!=database.parent or gate.parent!=database.parent: raise ValueError('Synthetic marker required')
            marker.write_text('ready',encoding='utf-8')
            import time
            deadline=time.monotonic()+15
            while not gate.exists() and time.monotonic()<deadline: time.sleep(0.02)
            if not gate.exists(): raise ValueError('Synthetic race gate expired')
        result={'outcome':process_job(str(run.pk),int(arguments[0]))}
    elif action in ('crash','crash-worker'):
        marker=Path(arguments[0]).resolve()
        if marker.parent!=database.parent: raise ValueError('Synthetic marker required')
        original=DjangoClaimStore.complete
        def interrupted(store,*values):
            original(store,*values)
            marker.write_text('uncommitted-real-node-output',encoding='utf-8')
            sys.stdin.read(1) # The test kills this finite owned process here.
            raise RuntimeError('Crash test must terminate the worker')
        DjangoClaimStore.complete=interrupted
        if action=='crash': flows.advance(user,str(run.pk),0)
        else:
            from backend.maipa.workflows.worker import process_job
            process_job(str(run.pk),0)
        raise AssertionError('Interrupted transaction unexpectedly returned')
    else:
        raise ValueError('Unknown fixture action')
print(json.dumps(result,sort_keys=True))
