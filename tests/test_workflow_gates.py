"""Human gates use real durable executors and independently expected outcomes."""
from copy import deepcopy
import uuid

from django.test import TestCase, TransactionTestCase, Client

from backend.maipa.workflows import services as flows
from backend.maipa.workflows.models import WorkflowRun,NodeCheckpoint
from backend.maipa.workflows.worker import process_job,work_once
from tests.test_workflows import fixture


def gated_graph(kind='human_approval'):
    config={'title':'Review synthetic request'}
    if kind=='human_approval':config['description']='Nonfinancial example only'
    else:config['fields']=[{'key':'answer','label':'Your answer','type':'textarea','required':True}]
    nodes=[{'id':'start','kind':'manual_trigger','config':{}},{'id':'ask','kind':kind,'config':config}]
    edges=[{'id':'a','source':'start','target':'ask'}]
    if kind=='human_approval':
        for port in ('approved','denied'):
            nodes.append({'id':port,'kind':'output','config':{}})
            edges.append({'id':port+'-edge','source':'ask','target':port,'sourceHandle':port})
    else:
        nodes.append({'id':'result','kind':'output','config':{}})
        edges.append({'id':'b','source':'ask','target':'result'})
    return {'version':1,'graph':{'nodes':nodes,'edges':edges}}


class HumanGateTests(TestCase):
    def setUp(self):
        self.install,self.owner,self.other,self.member,self.engagement,self.entity=fixture()

    def api(self):
        from backend.maipa.workflows import gates
        return gates

    def parked(self,kind='human_approval',seed=None,key=None):
        saved=flows.save_definition(self.owner,'Synthetic human gate',gated_graph(kind),entity_id=str(self.entity.pk))
        run=flows.dispatch(self.owner,saved['id'],1,seed or {'name':'Ada','approved':True,'values':{'answer':'Injected'}},key or str(uuid.uuid4()))
        self.assertEqual(process_job(run['id'],0),'advanced')
        self.assertEqual(process_job(run['id'],1),'advanced')
        gate=self.api().get_pending(self.owner,run['id'])
        return run,gate

    def answer(self,run,gate,value,**changes):
        command={'expected_step':gate['step'],'definition_revision':gate['definition_revision'],
            'request_sha256':gate['request_sha256'],'request_key':str(uuid.uuid4()),'answer':value}
        command.update(changes)
        return self.api().respond(self.owner,run['id'],gate['id'],**command)

    def test_prefilled_input_cannot_answer_gate_and_waiting_leaves_dispatch_queue(self):
        from backend.maipa.workflows.models import HumanGate,HumanResponse
        for kind in ('human_approval','user_input'):
            with self.subTest(kind=kind):
                run,gate=self.parked(kind);state=flows.get_run(self.owner,run['id'])
                self.assertEqual((state['status'],state['step'],list(state['outputs'])),('waiting',2,['start']))
                self.assertEqual((gate['node_id'],gate['kind'],gate['input']['in']['name']),('ask',kind,'Ada'))
                self.assertEqual(state['nodes']['ask']['status'],'paused')
                self.assertEqual(flows.advance(self.owner,run['id'],2),state)
                self.assertEqual(process_job(run['id'],2),'stale')
        self.assertEqual(work_once(limit=100),{'observed':0,'advanced':0,'blocked':0,'stale':0})
        self.assertEqual(HumanGate.objects.count(),2);self.assertEqual(HumanResponse.objects.count(),0)
        self.assertNotIn('fancy-flow:pause:',str(gate)+str(state))

    def test_exact_human_boolean_routes_real_approved_and_denied_ports_without_replay(self):
        from backend.maipa.workflows.models import HumanResponse
        for decision,active,inactive in ((True,'approved','denied'),(False,'denied','approved')):
            with self.subTest(decision=decision):
                run,gate=self.parked();initial=NodeCheckpoint.objects.get(run_id=run['id'],node_id='start')
                paused=NodeCheckpoint.objects.get(run_id=run['id'],node_id='ask')
                receipt=self.answer(run,gate,decision)
                self.assertEqual((receipt['status'],receipt['next_step']),('recorded',3))
                self.assertEqual(process_job(run['id'],2),'stale')
                self.assertEqual(process_job(run['id'],3),'advanced')
                self.assertEqual(process_job(run['id'],4),'advanced')
                state=flows.get_run(self.owner,run['id'])
                self.assertEqual((state['status'],state['nodes']['ask']['ports'],state['nodes'][inactive]['status']),('completed',[active],'skipped'))
                self.assertEqual(state['outputs'][active]['name'],'Ada')
                current=NodeCheckpoint.objects.get(run_id=run['id'],node_id='start')
                resumed=NodeCheckpoint.objects.get(run_id=run['id'],node_id='ask')
                self.assertEqual((current.first_attempt_at,current.attempts),(initial.first_attempt_at,1))
                self.assertEqual((resumed.first_attempt_at,resumed.attempts),(paused.first_attempt_at,2))
                self.assertEqual(NodeCheckpoint.objects.filter(run_id=run['id'],node_id='ask').count(),1)
        self.assertEqual(HumanResponse.objects.count(),2)

    def test_recorded_user_input_is_validated_and_real_output_uses_only_human_values(self):
        run,gate=self.parked('user_input')
        for value in ({},{'answer':''},{'answer':123},{'answer':'OK','extra':'hidden'},{'answer':'x'*2001}):
            with self.subTest(value=str(value)[:30]),self.assertRaises(flows.InvalidWorkflow):self.answer(run,gate,value)
        self.answer(run,gate,{'answer':'Human value'})
        process_job(run['id'],3);process_job(run['id'],4)
        self.assertEqual(flows.get_run(self.owner,run['id'])['outputs']['result'],{'answer':'Human value'})

    def test_approval_rejects_truthy_nonbooleans_and_actor_hints(self):
        run,gate=self.parked()
        for value in (1,0,'yes',None,{'approved':True},[]):
            with self.subTest(value=value),self.assertRaises(flows.InvalidWorkflow):self.answer(run,gate,value)
        with self.assertRaises(flows.Unavailable):self.api().respond({'user_id':self.owner.pk},run['id'],gate['id'],
            expected_step=2,definition_revision=1,request_sha256=gate['request_sha256'],request_key=str(uuid.uuid4()),answer=True)
        self.assertEqual(WorkflowRun.objects.get(pk=run['id']).status,'waiting')

    def test_response_and_request_are_append_only_and_acknowledged_retry_is_exact(self):
        from backend.maipa.workflows.models import HumanGate,HumanResponse
        run,gate=self.parked();key=str(uuid.uuid4());receipt=self.answer(run,gate,True,request_key=key)
        process_job(run['id'],3);process_job(run['id'],4)
        self.assertEqual(self.answer(run,gate,True,request_key=key),receipt)
        with self.assertRaises(flows.Conflict):self.answer(run,gate,False,request_key=key)
        with self.assertRaises(flows.Conflict):self.answer(run,gate,True)
        self.assertEqual(HumanResponse.objects.count(),1)
        for row in (HumanGate.objects.get(),HumanResponse.objects.get()):
            with self.assertRaises(ValueError):row.save()
            with self.assertRaises(ValueError):row.delete()

    def test_stale_node_version_hash_step_and_foreign_human_refuse_atomically(self):
        from backend.maipa.workflows.models import HumanResponse
        run,gate=self.parked()
        for change in ({'expected_step':1},{'definition_revision':2},{'request_sha256':'0'*64}):
            with self.subTest(change=change),self.assertRaises(flows.Conflict):self.answer(run,gate,True,**change)
        with self.assertRaises(flows.Unavailable):self.api().get_pending(self.other,run['id'])
        with self.assertRaises(flows.Unavailable):self.api().respond(self.other,run['id'],gate['id'],
            expected_step=2,definition_revision=1,request_sha256=gate['request_sha256'],request_key=str(uuid.uuid4()),answer=True)
        with self.assertRaises(flows.Unavailable):self.api().respond(self.owner,run['id'],str(uuid.uuid4()),
            expected_step=2,definition_revision=1,request_sha256=gate['request_sha256'],request_key=str(uuid.uuid4()),answer=True)
        self.assertFalse(HumanResponse.objects.exists());self.assertEqual(WorkflowRun.objects.get(pk=run['id']).step,2)

    def test_cancel_and_revoked_current_human_cannot_record_or_release_gate(self):
        from backend.maipa.workflows.models import HumanResponse
        run,gate=self.parked();type(self.member).objects.filter(pk=self.member.pk).update(enabled=False)
        with self.assertRaises(flows.Unavailable):self.answer(run,gate,True)
        type(self.member).objects.filter(pk=self.member.pk).update(enabled=True)
        stopped=flows.cancel(self.owner,run['id'],2)
        with self.assertRaises(flows.Conflict):self.answer(run,gate,True)
        self.assertFalse(HumanResponse.objects.exists());self.assertEqual(process_job(run['id'],stopped['step']),'stale')

    def test_graph_edit_does_not_change_pending_question_or_response_version(self):
        run,gate=self.parked();new=gated_graph();new['graph']['nodes'][1]['config']['title']='Different question'
        flows.save_definition(self.owner,'Revised graph',new,entity_id=str(self.entity.pk),definition_id=run['definition_id'],expected_revision=1)
        self.assertEqual(self.api().get_pending(self.owner,run['id'])['request'],gate['request'])
        self.answer(run,gate,False);process_job(run['id'],3);process_job(run['id'],4)
        self.assertEqual(flows.get_run(self.owner,run['id'])['nodes']['ask']['ports'],['denied'])

    def test_auto_answer_and_unbounded_or_unsupported_form_schema_are_never_admitted(self):
        cases=[]
        for kind in ('human_approval','user_input'):
            for auto in (True,False):
                value=gated_graph(kind);value['graph']['nodes'][1]['config']['autoAnswerFromInput']=auto;cases.append(value)
        for fields in ([],[{'key':'x','type':'file','label':'Upload'}],[{'key':'x','type':'text','label':'X'}]*2):
            value=gated_graph('user_input');value['graph']['nodes'][1]['config']['fields']=fields;cases.append(value)
        for value in cases:
            with self.subTest(value=value),self.assertRaises(flows.InvalidWorkflow):flows.save_definition(self.owner,'Invalid gate',value)

    def test_damaged_response_cannot_release_or_answer_a_paused_checkpoint(self):
        from backend.maipa.workflows.models import HumanResponse
        run,gate=self.parked();self.answer(run,gate,True);HumanResponse.objects.all().delete()
        self.assertEqual(process_job(run['id'],3),'blocked')
        state=flows.get_run(self.owner,run['id'])
        self.assertEqual((state['status'],state['error']),('blocked','snapshot_unavailable'))
        self.assertNotIn('approved',state['outputs'])

    def test_sequential_gates_do_not_reuse_an_answer_or_release_the_next_question(self):
        from backend.maipa.workflows.models import HumanResponse
        schema=gated_graph();schema['graph']['nodes'].append({'id':'later','kind':'user_input',
            'config':gated_graph('user_input')['graph']['nodes'][1]['config']})
        schema['graph']['edges'][1]['target']='later'
        schema['graph']['edges'].append({'id':'later-edge','source':'later','target':'approved'})
        saved=flows.save_definition(self.owner,'Sequential questions',schema)
        run=flows.dispatch(self.owner,saved['id'],1,{'name':'Ada'},'sequential')
        process_job(run['id'],0);process_job(run['id'],1);first=self.api().get_pending(self.owner,run['id'])
        key=str(uuid.uuid4());receipt=self.answer(run,first,True,request_key=key)
        process_job(run['id'],3);process_job(run['id'],4);second=self.api().get_pending(self.owner,run['id'])
        self.assertEqual((second['node_id'],second['step']),('later',5))
        self.assertEqual(self.answer(run,first,True,request_key=key),receipt)
        self.assertEqual(WorkflowRun.objects.get(pk=run['id']).step,5)
        self.answer(run,second,{'answer':'Second human answer'})
        process_job(run['id'],6);process_job(run['id'],7)
        state=flows.get_run(self.owner,run['id'])
        self.assertEqual((state['status'],state['outputs']['approved']),('completed',{'answer':'Second human answer'}))
        self.assertEqual(HumanResponse.objects.count(),2)


class HumanGateHttpTests(TestCase):
    setUp=HumanGateTests.setUp
    api=HumanGateTests.api
    parked=HumanGateTests.parked

    def client_for(self,user):
        client=Client(enforce_csrf_checks=True);client.force_login(user)
        self.assertEqual(client.get('/api/session/csrf/').status_code,200)
        return client,client.cookies['csrftoken'].value

    def command(self,gate,**change):
        result={'expected_step':gate['step'],'definition_revision':gate['definition_revision'],
            'request_sha256':gate['request_sha256'],'request_key':str(uuid.uuid4()),'answer':True}
        result.update(change);return result

    def test_human_response_requires_real_session_csrf_and_owned_current_role(self):
        from backend.maipa.workflows.models import HumanResponse
        run,gate=self.parked();url=f"/api/workflows/runs/{run['id']}/human/"
        post=f"/api/workflows/runs/{run['id']}/gates/{gate['id']}/response/"
        self.assertEqual(Client().get(url).status_code,401)
        client,csrf=self.client_for(self.owner)
        self.assertEqual(client.get(url).json()['id'],gate['id'])
        self.assertEqual(client.post(post,self.command(gate),content_type='application/json').status_code,403)
        foreign,foreign_csrf=self.client_for(self.other)
        self.assertEqual(foreign.get(url).status_code,404)
        self.assertEqual(foreign.post(post,self.command(gate),content_type='application/json',HTTP_X_CSRFTOKEN=foreign_csrf).status_code,404)
        type(self.member).objects.filter(pk=self.member.pk).update(role='viewer')
        self.assertEqual(client.post(post,self.command(gate),content_type='application/json',HTTP_X_CSRFTOKEN=csrf).status_code,404)
        self.assertFalse(HumanResponse.objects.exists())

    def test_authenticated_command_records_actual_human_and_rejects_actor_or_stale_hints(self):
        from backend.maipa.workflows.models import HumanResponse
        run,gate=self.parked();client,csrf=self.client_for(self.owner)
        url=f"/api/workflows/runs/{run['id']}/gates/{gate['id']}/response/"
        for change,status in (({'actor':'human:'+str(self.other.pk)},400),({'node_id':'another'},400),
                              ({'request_sha256':'0'*64},409),({'expected_step':1},409)):
            with self.subTest(change=change):
                response=client.post(url,self.command(gate,**change),content_type='application/json',HTTP_X_CSRFTOKEN=csrf)
                self.assertEqual(response.status_code,status)
        result=client.post(url,self.command(gate),content_type='application/json',HTTP_X_CSRFTOKEN=csrf)
        self.assertEqual((result.status_code,result.json()['status']),(200,'recorded'))
        self.assertEqual(HumanResponse.objects.get().human_id,self.owner.pk)
        self.assertEqual(WorkflowRun.objects.get(pk=run['id']).status,'queued')


class NativeHumanGateTests(TestCase):
    def test_current_original_native_run_continues_only_after_recorded_human_answer(self):
        from unittest.mock import patch
        from backend.maipa.workflows import native,gates
        from tests.test_workflow_native import native_fixture,grant
        native_fixture(self)
        saved=flows.save_definition(self.owner,'Native current gate',gated_graph(),entity_id=str(self.entity.pk))
        native.prepare_graph(self.owner,saved['id'],1);grant(self,'run')
        actor=native.actor(self.owner,native.bind_principal(self.owner,str(self.account.pk),self.ui,self.principal))
        with patch('backend.maipa.workflows.worker.runtime_status',return_value={'available':True,'state':'running'}):
            run=native.dispatch(actor,saved['id'],1,{'name':'Ada'},'native-current-gate')
        process_job(run['id'],0);process_job(run['id'],1);gate=gates.get_pending(self.owner,run['id'])
        gates.respond(self.owner,run['id'],gate['id'],expected_step=2,definition_revision=1,
            request_sha256=gate['request_sha256'],request_key=str(uuid.uuid4()),answer=True)
        process_job(run['id'],3);process_job(run['id'],4)
        state=flows.get_run(self.owner,run['id'])
        self.assertEqual((state['status'],state['outputs']['approved']['name']),('completed','Ada'))

    def test_a_human_response_cannot_substitute_for_revoked_native_origin_or_an_agent_actor(self):
        from unittest.mock import patch
        from backend.maipa.policies import authority
        from backend.maipa.workflows import native,gates
        from backend.maipa.workflows.models import HumanResponse
        from tests.test_workflow_native import native_fixture,grant
        native_fixture(self)
        saved=flows.save_definition(self.owner,'Native human gate',gated_graph(),entity_id=str(self.entity.pk))
        native.prepare_graph(self.owner,saved['id'],1);original_grant=grant(self,'run')
        actor=native.actor(self.owner,native.bind_principal(self.owner,str(self.account.pk),self.ui,self.principal))
        with patch('backend.maipa.workflows.worker.runtime_status',return_value={'available':True,'state':'running'}):
            run=native.dispatch(actor,saved['id'],1,{'name':'Ada'},'native-human-gate')
        process_job(run['id'],0);process_job(run['id'],1);gate=gates.get_pending(self.owner,run['id'])
        command={'expected_step':2,'definition_revision':1,'request_sha256':gate['request_sha256'],
            'request_key':str(uuid.uuid4()),'answer':True}
        with self.assertRaises(flows.Unavailable):gates.respond(actor,run['id'],gate['id'],**command)
        authority.operator_revoke(original_grant.pk,'Synthetic native revocation')
        with self.assertRaises(flows.Unavailable):gates.respond(self.owner,run['id'],gate['id'],**command)
        self.assertFalse(HumanResponse.objects.exists())
        self.assertEqual(flows.cancel(self.owner,run['id'],2)['status'],'cancelled')


class HumanGateConcurrencyTests(TransactionTestCase):
    setUp=HumanGateTests.setUp
    api=HumanGateTests.api
    parked=HumanGateTests.parked

    def test_racing_human_answers_record_and_release_exactly_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.contrib.auth import get_user_model
        from django.db import close_old_connections
        from backend.maipa.workflows.models import HumanResponse
        run,gate=self.parked();barrier=Barrier(2)
        def submit(value):
            close_old_connections()
            try:
                user=get_user_model().objects.get(pk=self.owner.pk);barrier.wait(5)
                try:
                    self.api().respond(user,run['id'],gate['id'],expected_step=2,definition_revision=1,
                        request_sha256=gate['request_sha256'],request_key=str(uuid.uuid4()),answer=value)
                    return 'recorded'
                except flows.Conflict:return 'conflict'
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,[True,False]))
        self.assertEqual(sorted(results),['conflict','recorded'])
        self.assertEqual((HumanResponse.objects.count(),WorkflowRun.objects.get(pk=run['id']).step),(1,3))
        process_job(run['id'],3);process_job(run['id'],4)
        active='approved' if HumanResponse.objects.get().answer else 'denied'
        self.assertEqual(flows.get_run(self.owner,run['id'])['nodes']['ask']['ports'],[active])
