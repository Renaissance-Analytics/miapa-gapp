"""The real driver policy includes scoped public Form tools, never native or wildcard tools."""
import json,shutil,subprocess,unittest
from pathlib import Path
from tools.ci import child_environment


class SharedFormPolicyTests(unittest.TestCase):
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
            'form_list','form_describe','form_get_value','form_get_values',
            'form_set_value','form_set_values','form_focus','form_submit')}
        self.assertEqual(set(observed['enabled']),expected)
        self.assertEqual(len(observed['enabled']),16)
        self.assertEqual(observed['disabled'],[])
        self.assertFalse(any('*' in name or name=='exec' for name in observed['enabled']))
