"""Shared authoring state is private, revisioned and distinct from execution."""
from copy import deepcopy
from pathlib import Path
import tempfile,uuid
from types import SimpleNamespace
from django.contrib.auth import get_user_model
from django.test import TestCase,override_settings,Client
from backend.maipa.workflows import services
from backend.maipa.workflows.models import Definition,DefinitionVersion,WorkflowRun
from backend.maipa.agent_accounts import ui
from backend.maipa.agent_accounts.models import AgentAccount,SessionReference
from backend.maipa.agent_accounts.references import record_session
from backend.maipa.storage import prepare_directory,prepare_files
from backend.maipa.workspace.models import Installation,Membership
from backend.maipa.intake.models import Engagement,Entity


def graph(text='Ready {{ in.name }}'):
    return {'nodes':[
        {'id':'start','type':'manual_trigger','position':{'x':0,'y':0},'data':{'kind':'manual_trigger','label':'Start','config':{}}},
        {'id':'format','type':'transform','position':{'x':180,'y':0},'data':{'kind':'transform','label':'Format','config':{'expression':text}}},
        {'id':'result','type':'output','position':{'x':360,'y':0},'data':{'kind':'output','label':'Result','config':{}}}],
        'edges':[{'id':'first','source':'start','target':'format'},{'id':'second','source':'format','target':'result'}]}


class WorkflowDraftTests(TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory(prefix='maipa-flow-draft-');self.addCleanup(temporary.cleanup)
        data=prepare_directory(Path(temporary.name).resolve()/'install');prepare_files(data)
        configured=override_settings(DATA_ROOT=data);configured.enable();self.addCleanup(configured.disable)
        self.install=Installation.objects.create()
        self.owner=get_user_model().objects.create_user(username='workflow-author')
        self.other=get_user_model().objects.create_user(username='other-author')
        self.member=Membership.objects.create(user=self.owner,installation=self.install,role='preparer')
        Membership.objects.create(user=self.other,installation=self.install,role='administrator')
        client=Engagement.objects.create(owner=self.owner,installation=self.install,name='Synthetic owned client')
        self.entity=Entity.objects.create(engagement=client,name='Synthetic entity')
        self.key=str(uuid.uuid4())

    def api(self):
        from backend.maipa.workflows import drafts
        return drafts

    def create(self,**values):
        return self.api().create(self.owner,'Shared workflow',self.key,entity_id=str(self.entity.pk),**values)

    def native(self):
        account=AgentAccount.objects.create(owner=self.owner,provider='claude',label='Synthetic authoring account')
        session=record_session(self.owner,str(account.pk),'synthetic-session')
        lease=ui.open_ui_lease(self.owner,str(account.pk),session)
        return self.api().native_actor(self.owner,str(account.pk),lease),session

    def test_empty_detached_draft_is_idempotent_and_cannot_be_dispatched(self):
        first=self.create();again=self.create();first['graph']['nodes'].append({'untrusted':'caller mutation'})
        self.assertEqual(again,self.api().get(self.owner,again['id']))
        self.assertEqual((again['revision'],again['graph'],again['publication']),(0,{'nodes':[],'edges':[]},None))
        self.assertEqual((Definition.objects.count(),DefinitionVersion.objects.count(),WorkflowRun.objects.count()),(0,0,0))
        self.assertEqual(len(self.api().list_drafts(self.owner)),1)

    def test_native_and_human_share_incomplete_graph_and_exact_cas(self):
        first=self.create();actor,_=self.native()
        partial=graph();partial['nodes']=partial['nodes'][:2];partial['edges']=[];partial['nodes'][1]['data']['config']={}
        human=self.api().change(self.owner,first['id'],0,partial)
        native=self.api().get(actor,first['id']);self.assertEqual(native,human)
        changed=self.api().change(actor,first['id'],1,graph())
        self.assertEqual((changed['id'],changed['revision']),(first['id'],2))
        self.assertEqual(changed,self.api().get(self.owner,first['id']))
        self.assertFalse(Definition.objects.exists());self.assertFalse(WorkflowRun.objects.exists())

    def test_stale_graph_cannot_overwrite_newer_human_work(self):
        first=self.create();saved=self.api().change(self.owner,first['id'],0,graph('Human'))
        with self.assertRaises(services.Conflict):self.api().change(self.owner,first['id'],0,graph('Stale agent'))
        self.assertEqual(self.api().get(self.owner,first['id']),saved)
        self.assertEqual(self.api().change(self.owner,first['id'],1,graph('Human')),saved)

    def test_create_retry_cannot_change_original_scope_or_name(self):
        first=self.create()
        with self.assertRaises(services.Conflict):self.api().create(self.owner,'Other name',self.key,entity_id=str(self.entity.pk))
        with self.assertRaises(services.Conflict):self.api().create(self.owner,'Shared workflow',self.key)
        self.assertEqual(self.api().get(self.owner,first['id']),first)

    def test_foreign_entity_user_and_actor_hints_never_select_authority(self):
        first=self.create()
        for actor in (self.other,{'user_id':self.owner.pk},SimpleNamespace(pk=self.owner.pk,is_authenticated=True)):
            with self.subTest(actor=type(actor).__name__),self.assertRaises(services.Unavailable):self.api().get(actor,first['id'])
        with self.assertRaises(services.Unavailable):self.api().create(self.other,'Foreign',str(uuid.uuid4()),entity_id=str(self.entity.pk))
        self.assertEqual(self.api().list_drafts(self.other),[])

    def test_original_native_lease_is_rechecked_but_human_history_survives(self):
        first=self.create();actor,session=self.native()
        self.assertEqual(self.api().get(actor,first['id']),first)
        from django.utils import timezone
        SessionReference.objects.filter(pk=session).update(revoked_at=timezone.now())
        with self.assertRaises(services.Unavailable):self.api().change(actor,first['id'],0,graph())
        self.assertEqual(self.api().get(self.owner,first['id']),first)
        self.member.enabled=False;self.member.save(update_fields=['enabled'])
        with self.assertRaises(services.Unavailable):self.api().get(self.owner,first['id'])

    def test_unsafe_unknown_duplicate_and_dangling_graph_writes_refuse_atomically(self):
        first=self.create();invalid=[]
        for mutate in (
            lambda g:g['nodes'][1]['data'].update(kind='api_request'),
            lambda g:g['nodes'][1]['data']['config'].update(executable='hidden'),
            lambda g:g['nodes'][1].update(id='start'),
            lambda g:g['nodes'][1]['data'].update(status='done'),
            lambda g:g['edges'][0].update(target='missing'),
            lambda g:g['nodes'][1]['position'].update(x=float('nan')),
            lambda g:g.update(actor_id=str(self.other.pk)),
            lambda g:g['nodes'][1]['data'].update(label='bad\x00label')):
            value=graph();mutate(value);invalid.append(value)
        for value in invalid:
            with self.subTest(value=invalid.index(value)),self.assertRaises(services.InvalidWorkflow):self.api().change(self.owner,first['id'],0,value)
        self.assertEqual(self.api().get(self.owner,first['id']),first)

    def test_complete_publication_uses_real_fancy_export_and_immutable_version(self):
        first=self.create();saved=self.api().change(self.owner,first['id'],0,graph())
        receipt=self.api().publish(self.owner,first['id'],1)
        self.assertEqual(receipt['status'],'published');self.assertEqual(receipt['draft_revision'],1)
        version=DefinitionVersion.objects.get();self.assertEqual(version.schema['graph']['nodes'][1]['kind'],'transform')
        self.assertNotIn('config',version.schema['graph']['nodes'][0])
        self.assertEqual(version.schema['graph']['nodes'][1]['config'],{'expression':'Ready {{ in.name }}'})
        self.assertFalse(WorkflowRun.objects.exists())
        run=services.dispatch(self.owner,receipt['definition_id'],1,{'name':'Ada'},'published-workflow')
        for _ in range(3):run=services.advance(self.owner,run['id'],run['step'])
        self.assertEqual((run['status'],run['outputs']['result']),('completed','Ready Ada'))
        self.api().change(self.owner,first['id'],1,graph('New {{ in.name }}'))
        self.assertEqual(version.schema['graph']['nodes'][1]['config']['expression'],'Ready {{ in.name }}')
        self.assertEqual(saved['publication'],None)

    def test_incomplete_cycle_wrong_ports_and_prefilled_approval_cannot_publish(self):
        first=self.create()
        with self.assertRaises(services.InvalidWorkflow):self.api().publish(self.owner,first['id'],0)
        bad=graph();bad['nodes'][1]['data']['config']={};self.api().change(self.owner,first['id'],0,bad)
        with self.assertRaises(services.InvalidWorkflow):self.api().publish(self.owner,first['id'],1)
        bad=graph();bad['edges'][0]['sourceHandle']='wrong';self.api().change(self.owner,first['id'],1,bad)
        with self.assertRaises(services.InvalidWorkflow):self.api().publish(self.owner,first['id'],2)
        bad=graph();bad['edges'].append({'id':'cycle','source':'format','target':'start'})
        self.api().change(self.owner,first['id'],2,bad)
        with self.assertRaises(services.InvalidWorkflow):self.api().publish(self.owner,first['id'],3)
        bad=graph();bad['nodes'][1]['type']=bad['nodes'][1]['data']['kind']='human_approval'
        bad['nodes'][1]['data']['config']={'autoAnswerFromInput':True}
        with self.assertRaises(services.InvalidWorkflow):self.api().change(self.owner,first['id'],3,bad)
        self.assertEqual((Definition.objects.count(),WorkflowRun.objects.count()),(0,0))

    def test_published_empty_config_omission_still_parks_real_durable_human_gate(self):
        first=self.create();value=graph();value['nodes'][1]['type']=value['nodes'][1]['data']['kind']='human_approval'
        value['nodes'][1]['data']['config']={};value['edges'][1]['sourceHandle']='approved'
        self.api().change(self.owner,first['id'],0,value);receipt=self.api().publish(self.owner,first['id'],1)
        self.assertNotIn('config',DefinitionVersion.objects.get().schema['graph']['nodes'][1])
        run=services.dispatch(self.owner,receipt['definition_id'],1,{'approved':True},'gate-export')
        run=services.advance(self.owner,run['id'],0);run=services.advance(self.owner,run['id'],1)
        self.assertEqual(run['status'],'waiting')
        from backend.maipa.workflows import gates
        pending=gates.get_pending(self.owner,run['id'])
        self.assertEqual(pending['request']['title'],'Approve action')
        gates.respond(self.owner,run['id'],pending['id'],expected_step=pending['step'],definition_revision=1,
            request_sha256=pending['request_sha256'],request_key=str(uuid.uuid4()),answer=True)
        run=services.get_run(self.owner,run['id'])
        run=services.advance(self.owner,run['id'],run['step']);run=services.advance(self.owner,run['id'],run['step'])
        self.assertEqual(run['status'],'completed');self.assertEqual(run['outputs']['result']['approved'],True)

    def test_publish_receipt_retry_never_appends_twice_or_adopts_foreign_definition_edit(self):
        first=self.create();self.api().change(self.owner,first['id'],0,graph())
        published=self.api().publish(self.owner,first['id'],1)
        self.assertEqual(self.api().publish(self.owner,first['id'],1),published)
        self.assertEqual(DefinitionVersion.objects.count(),1)
        version=DefinitionVersion.objects.get()
        services.save_definition(self.owner,'Other authorized edit',version.schema,entity_id=str(self.entity.pk),
            definition_id=published['definition_id'],expected_revision=1)
        self.assertEqual(self.api().publish(self.owner,first['id'],1),published)
        self.api().change(self.owner,first['id'],1,graph('Later'))
        with self.assertRaises(services.Conflict):self.api().publish(self.owner,first['id'],2)
        self.assertEqual(DefinitionVersion.objects.count(),2)

    def test_stored_hash_damage_and_transferred_entity_are_not_silently_repaired(self):
        first=self.create()
        from backend.maipa.workflows.models import WorkflowDraft
        WorkflowDraft.objects.filter(pk=first['id']).update(graph={'nodes':graph()['nodes'],'edges':[]})
        with self.assertRaises(services.Unavailable):self.api().get(self.owner,first['id'])
        WorkflowDraft.objects.filter(pk=first['id']).update(graph=first['graph'])
        self.entity.engagement.owner=self.other;self.entity.engagement.save(update_fields=['owner'])
        with self.assertRaises(services.Unavailable):self.api().get(self.owner,first['id'])

    def test_http_actual_session_csrf_owner_and_closed_payload(self):
        c=Client(enforce_csrf_checks=True)
        path='/api/workflows/drafts/'
        self.assertEqual(c.get(path).status_code,401)
        c.force_login(self.owner)
        command={'name':'Shared workflow','entity_id':str(self.entity.pk),'request_key':self.key}
        self.assertEqual(c.post(path,command,content_type='application/json').status_code,403)
        token=c.get('/api/session/csrf/').json()['csrf_token']
        headers={'HTTP_X_CSRFTOKEN':token,'HTTP_X_MAIPA_WORKFLOW_ACTOR':str(self.owner.pk)}
        created=c.post(path,command,content_type='application/json',**headers);self.assertEqual(created.status_code,201)
        saved=created.json();item=path+saved['id']+'/'
        bad=c.post(item,{'expected_revision':0,'graph':graph(),'actor_id':str(self.other.pk)},content_type='application/json',**headers)
        self.assertEqual(bad.status_code,400)
        accepted=c.post(item,{'expected_revision':0,'graph':graph()},content_type='application/json',**headers)
        self.assertEqual((accepted.status_code,accepted.json()['revision']),(200,1))
        self.assertEqual(c.post(item,{'expected_revision':0,'graph':graph('Stale')},content_type='application/json',**headers).status_code,409)
        c.force_login(self.other)
        self.assertEqual(c.get(item,HTTP_X_MAIPA_WORKFLOW_ACTOR=str(self.other.pk)).status_code,404)
        self.assertEqual(c.get(item,HTTP_X_MAIPA_WORKFLOW_ACTOR=str(self.owner.pk)).status_code,409)
