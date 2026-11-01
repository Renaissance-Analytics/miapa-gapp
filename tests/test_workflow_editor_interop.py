"""Independent current editor exports, upgrade history and component boundaries."""
from copy import deepcopy
import json
from pathlib import Path
from django.test import TestCase
from backend.maipa.agent_accounts.ui import validate_schema
from backend.maipa.workflows import services
from backend.maipa.workflows.models import Definition,DefinitionVersion,WorkflowRun
from backend.maipa.workflows.schema import bounded_json
from tests.test_workflows import fixture,graph


class WorkflowEditorInteropTests(TestCase):
    def setUp(self):
        self.install,self.owner,self.other,self.member,self.engagement,self.entity=fixture()
        self.exports=json.loads((Path(__file__).parent/'fixtures/flow-ts0821.json').read_text(encoding='utf-8'))

    def execute(self,schema,seed,key):
        saved=services.save_definition(self.owner,'Current editor',schema,entity_id=str(self.entity.pk))
        run=services.dispatch(self.owner,saved['id'],saved['revision'],seed,key)
        for step in range(3):run=services.advance(self.owner,run['id'],step)
        self.assertEqual(run['status'],'completed')
        self.assertEqual(run['runtime'],'fancy-flow/0.30.1')
        self.assertEqual(DefinitionVersion.objects.get(pk=WorkflowRun.objects.get(pk=run['id']).version_id).schema,schema)
        return run

    def test_actual_editor_fields_export_preserves_typed_outputs_and_literal_schema(self):
        row=self.exports['fields']
        run=self.execute(row['schema'],row['seed'],'actual-fields')
        self.assertEqual(run['outputs']['o'],{'person':'Hello Ada','count':7,'flag':False})

    def test_actual_untouched_editor_defaults_are_publishable_and_preserve_input(self):
        run=self.execute(self.exports['defaults'],{'untouched':7},'actual-defaults')
        self.assertEqual(run['outputs']['o'],{'untouched':7})

    def test_invalid_mode_types_and_value_with_trimmed_blank_key_never_persist(self):
        for config in ({'mode':'rows','fields':[]},{'mode':'fields','fields':{}},
            {'mode':'fields','fields':[{'key':'   ','value':'Must not vanish'}]},
            {'mode':'expression','expression':12}):
            value=deepcopy(self.exports['defaults']);value['graph']['nodes'][1]['config']=config
            with self.subTest(config=config),self.assertRaises(services.InvalidWorkflow):
                services.save_definition(self.owner,'Refused editor graph',value)
        self.assertEqual(Definition.objects.count(),0)

    def test_old_runtime_history_is_readable_but_never_relabelled_or_executed(self):
        schema,digest,_=services.validate(graph())
        definition=Definition.objects.create(owner=self.owner,installation=self.install,entity=self.entity,name='Old runtime')
        version=DefinitionVersion.objects.create(definition=definition,revision=1,schema=schema,schema_sha256=digest,runtime='fancy-flow/0.29.0')
        seed,seed_hash=bounded_json({'name':'Ada'})
        run=WorkflowRun.objects.create(definition=definition,version=version,dispatch_key='old-runtime',seed=seed,seed_sha256=seed_hash,
            access_revision=self.install.access_revision,origin_owner=self.owner,origin_installation=self.install,origin_entity=self.entity)
        before=services.get_run(self.owner,str(run.pk))
        self.assertEqual(before['runtime'],'fancy-flow/0.29.0')
        with self.assertRaises(services.Unavailable):services.advance(self.owner,str(run.pk),0)
        self.assertEqual(services.get_run(self.owner,str(run.pk)),before)
        revised=services.save_definition(self.owner,'New runtime',schema,entity_id=str(self.entity.pk),definition_id=str(definition.pk),expected_revision=1)
        self.assertEqual(revised['runtime'],'fancy-flow/0.30.1')
        version.refresh_from_db();self.assertEqual((version.runtime,version.schema_sha256),('fancy-flow/0.29.0',digest))

    def test_workflow_component_contains_only_one_stable_draft_reference(self):
        schema={'id':'shared-workflow','type':'Maipa.Workflow','props':{'draft_id':'12345678-1234-4234-8234-123456789012'}}
        self.assertEqual(validate_schema(schema,'preparer'),schema)
        for props in ({'draft_id':'../other'},{'draft_id':schema['props']['draft_id'],'graph':{}},
            {'draft_id':schema['props']['draft_id'],'owner_id':str(self.other.pk)}):
            with self.subTest(props=props),self.assertRaises(ValueError):validate_schema({**schema,'props':props},'preparer')
        with self.assertRaises(ValueError):validate_schema({'id':'group','type':'Maipa.Stack','children':[schema,{**schema,'id':'duplicate'}]},'preparer')
