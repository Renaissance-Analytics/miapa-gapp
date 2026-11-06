"""The real driver policy includes scoped public Form tools, never native or wildcard tools."""
import json,shutil,subprocess,unittest
from pathlib import Path
from tools.ci import child_environment


class SharedFormPolicyTests(unittest.TestCase):
    def test_native_permission_policy_matches_every_published_workflow_tool(self):
        from asgiref.sync import async_to_sync
        from backend.maipa.agent_accounts.workflow_tools import public_workflow_tools
        root=Path(__file__).resolve().parents[1]
        result=subprocess.run([shutil.which('node'),'--input-type=module','--eval',
            "import {claudeUiTools} from './backend/agent/host.mjs';console.log(JSON.stringify(claudeUiTools(true)));"],
            cwd=root,env=child_environment(),capture_output=True,text=True,timeout=10,check=True)
        permissions={name.removeprefix('mcp__maipa_ui__') for name in json.loads(result.stdout)
            if name.startswith(('mcp__maipa_ui__flow_','mcp__maipa_ui__maipa_workflow_'))}
        self.assertEqual(permissions,{tool['name'] for tool in async_to_sync(public_workflow_tools)()})
        self.assertTrue({'flow_run','flow_cancel','maipa_workflow_runs'}<=permissions)

    def test_claude_policy_allows_exact_shared_tools_and_no_native_or_foreign_capabilities(self):
        root=Path(__file__).resolve().parents[1]
        source="""import {claudeUiTools} from './backend/agent/host.mjs';
        const first=claudeUiTools(true);first.push('exec');
        console.log(JSON.stringify({enabled:claudeUiTools(true),disabled:claudeUiTools(false)}));"""
        result=subprocess.run([shutil.which('node'),'--input-type=module','--eval',source],cwd=root,
            env=child_environment(),capture_output=True,text=True,timeout=10,check=True)
        observed=json.loads(result.stdout)
        expected={'mcp__maipa_ui__'+name for name in (
            'maipa_ui_catalog','maipa_ui_present','maipa_ui_update','maipa_ui_dismiss',
            'maipa_views_list','maipa_views_save','maipa_views_change','maipa_views_open',
            'maipa_setup_read','maipa_setup_record',
            'form_list','form_describe','form_get_value','form_get_values',
            'form_set_value','form_set_values','form_focus','form_submit',
            'flow_get_state','flow_list_nodes','flow_get_node','flow_list_edges','flow_get_node_schema',
            'flow_add_node','flow_update_node','flow_delete_node','flow_connect','flow_disconnect',
            'maipa_workflow_list','maipa_workflow_create','maipa_workflow_publish',
            'flow_run','flow_cancel','maipa_workflow_runs','maipa_workflow_select_run',
            'maipa_books_list','maipa_books_read','maipa_books_activity','maipa_books_imports','maipa_journal_propose')}
        self.assertEqual(set(observed['enabled']),expected)
        self.assertEqual(len(observed['enabled']),40)
        self.assertEqual(observed['disabled'],[])
        self.assertFalse(any('*' in name or name=='exec' for name in observed['enabled']))
