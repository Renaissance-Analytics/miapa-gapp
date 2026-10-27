"""Failure reports may expose a fixed stage code, never arbitrary child diagnostics."""
import unittest
from tools.check_browser import browser_failure_evidence


class BrowserReportingTests(unittest.TestCase):
    def test_child_timeout_retains_private_diagnostics_and_cannot_claim_cleanup(self):
        import json
        from pathlib import Path
        import subprocess
        import tempfile
        from unittest.mock import Mock, patch
        from tools import check_browser
        for reason in ('cleanup', 'SYNTHETIC_PRIVATE_REASON'):
            with tempfile.TemporaryDirectory() as temporary, patch.object(check_browser, 'ROOT', Path(temporary)):
                stdout = json.dumps({'reason': reason, 'cleanup': True, 'checks': 38, 'private': 'SYNTHETIC_PRIVATE_BODY'})
                process = Mock(pid=314159)
                process.communicate.side_effect = [subprocess.TimeoutExpired(['synthetic-browser'], 120,
                    output=stdout.encode(), stderr=b'SYNTHETIC_PRIVATE_ERROR'), (stdout, 'SYNTHETIC_PRIVATE_ERROR')]
                with patch.object(check_browser.subprocess, 'Popen', return_value=process), patch.object(check_browser.subprocess, 'run') as stop:
                    with self.assertRaises(check_browser.BrowserFailure) as captured:
                        check_browser.browser_process({'synthetic': True}, 'synthetic-node', {})
                expected = {'checks': 0, 'reason': 'cleanup' if reason == 'cleanup' else 'browser_process', 'cleanup': False}
                self.assertEqual(captured.exception.evidence, expected)
                self.assertNotIn('SYNTHETIC_PRIVATE', str(captured.exception.evidence))
                artifact = Path(temporary) / 'artifacts/ci/browser-failure.log'
                self.assertEqual(artifact.read_text(encoding='utf-8'), stdout + '\nSYNTHETIC_PRIVATE_ERROR')
                self.assertEqual(process.communicate.call_args_list[0].kwargs['timeout'], 120)
                self.assertEqual(process.communicate.call_args_list[1].kwargs, {'timeout': 10})
                self.assertEqual(stop.call_args.args[0], ['taskkill.exe', '/PID', '314159', '/T', '/F'])
                self.assertEqual(stop.call_args.kwargs['timeout'], 15)

    def test_progress_is_closed_and_io_failure_cannot_interrupt_cleanup(self):
        import json
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from tools import check_browser
        with tempfile.TemporaryDirectory() as temporary, patch.object(check_browser, 'ROOT', Path(temporary)):
            check_browser.save_progress('resume')
            path = Path(temporary) / 'artifacts/ci/browser-runner-progress.json'
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'stage': 'resume'})
            for invalid in ('SYNTHETIC_PRIVATE_PHASE', True, {}, ''):
                with self.assertRaisesRegex(ValueError, '^Invalid fixture progress$'):
                    check_browser.save_progress(invalid)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), {'stage': 'resume'})
            with patch.object(Path, 'write_text', side_effect=OSError('SYNTHETIC_PRIVATE_PATH')):
                self.assertIsNone(check_browser.save_progress('cleanup'))

    def test_shared_surface_diagnostics_keep_only_fixed_codes_statuses_and_booleans(self):
        for phase in ('admin_users', 'work_views', 'agent_shell'):
            self.assertEqual(browser_failure_evidence({'reason':phase,'cleanup':True,
                'failure_kind':'assertion','surface_responses':{'session':200,'chat':503,'admin':200,'token':200},
                'surface_ui':{'admin_panel':True,'admin_ready':False,'admin_fields':False,
                    'conversation':True,'contextual_panel':False,'chat_alert':True,'password':True},
                'stderr':'synthetic-private-error','content':'synthetic-private-transcript'}),
                {'checks':0,'reason':phase,'cleanup':True,'failure_kind':'assertion',
                 'surface_responses':{'session':200,'chat':503,'admin':200},
                 'surface_ui':{'admin_panel':True,'admin_ready':False,'admin_fields':False,
                    'conversation':True,'contextual_panel':False,'chat_alert':True}})
            self.assertEqual(browser_failure_evidence({'reason':phase,'cleanup':True,
                'failure_kind':'synthetic-private-error','surface_responses':{'session':True,'chat':700,'admin':'secret'},
                'surface_ui':{'admin_panel':'private-label','admin_ready':1,'token':True}}),
                {'checks':0,'reason':phase,'cleanup':True})
        self.assertEqual(browser_failure_evidence({'reason':'anonymous','cleanup':True,
            'failure_kind':'assertion','surface_responses':{'chat':503},'surface_ui':{'conversation':True}}),
            {'checks':0,'reason':'anonymous','cleanup':True})
        self.assertEqual(browser_failure_evidence({'reason':'agent_shell','cleanup':True,
            'agent_state':{'phase':'unavailable','interruption_received':True,'private':'synthetic-secret'}}),
            {'checks':0,'reason':'agent_shell','cleanup':True,
             'agent_state':{'phase':'unavailable','interruption_received':True}})
        for reason in ('anonymous', 'agent_shell'):
            self.assertEqual(browser_failure_evidence({'reason':reason,'cleanup':True,
                'agent_state':{'phase':'synthetic-private','interruption_received':'secret','private':True}}),
                {'checks':0,'reason':reason,'cleanup':True})
        self.assertEqual(browser_failure_evidence({'reason':'agent_shell','cleanup':True,
            'surface_ui':{'composer_nonempty':True,'composer_focused':False,'send_present':True,
                'send_disabled':False,'ui_phase_ready':True,'draft':'synthetic-private'}}),
            {'checks':0,'reason':'agent_shell','cleanup':True,
             'surface_ui':{'composer_nonempty':True,'composer_focused':False,'send_present':True,
                'send_disabled':False,'ui_phase_ready':True}})
        self.assertEqual(browser_failure_evidence({'reason':'intake_facts','cleanup':True,
            'failure_kind':'timeout','surface_responses':{'intake':400,'private':200},
            'surface_ui':{'intake_form':True,'intake_valid':False,'intake_reference':True,
                'intake_locator':False,'intake_alert':False,'intake_save_disabled':False,
                'intake_revision_one':False,'source':'synthetic-private-evidence'}}),
            {'checks':0,'reason':'intake_facts','cleanup':True,'failure_kind':'timeout',
             'surface_responses':{'intake':400},'surface_ui':{'intake_form':True,'intake_valid':False,
                'intake_reference':True,'intake_locator':False,'intake_alert':False,
                'intake_save_disabled':False,'intake_revision_one':False}})
        self.assertEqual(browser_failure_evidence({'reason':'intake_facts','cleanup':True,
            'surface_responses':{'intake':True},'surface_ui':{'intake_valid':'private','intake_reference':1}}),
            {'checks':0,'reason':'intake_facts','cleanup':True})

    def test_known_setup_checkpoint_survives_without_browser_text_or_answer(self):
        self.assertEqual(browser_failure_evidence({'reason':'setup_chat','cleanup':True,
            'checkpoint':'setup_confirm','answer':'synthetic-private-answer','stderr':'private-path'}),
            {'checks':0,'reason':'setup_chat','cleanup':True,'checkpoint':'setup_confirm'})
        self.assertEqual(browser_failure_evidence({'reason':'logout_viewer','cleanup':True,
            'checkpoint':'logout_form','failure_kind':'timeout',
            'auth_responses':{'logout':200,'login':401},
            'auth_ui':{'login_form':False,'sign_out':True,'sign_out_pending':False,
                'sign_out_failed':False,'release_notes':False},
            'answer':'synthetic-private-answer','stderr':'private-path'}),
            {'checks':0,'reason':'logout_viewer','cleanup':True,'checkpoint':'logout_form',
             'failure_kind':'timeout','auth_responses':{'logout':200,'login':401},
             'auth_ui':{'login_form':False,'sign_out':True,'sign_out_pending':False,
                'sign_out_failed':False,'release_notes':False}})
        self.assertEqual(browser_failure_evidence({'reason':'login','cleanup':True,
            'checkpoint':'login_submit','failure_kind':'timeout','auth_responses':{'login':403},
            'auth_ui':{'login_form':True,'release_notes':False},'stderr':'synthetic-private'}),
            {'checks':0,'reason':'login','cleanup':True,'checkpoint':'login_submit',
             'failure_kind':'timeout','auth_responses':{'login':403},
             'auth_ui':{'login_form':True,'release_notes':False}})

    def test_unknown_misplaced_or_nonstring_checkpoints_are_discarded(self):
        for reason, checkpoint in [('setup_chat','synthetic-private-answer'), ('setup_chat',{}),
                                   ('setup_chat',True), ('anonymous','setup_confirm')]:
            result=browser_failure_evidence({'reason':reason,'cleanup':True,'checkpoint':checkpoint})
            self.assertNotIn('checkpoint', result)
            self.assertNotIn('synthetic-private-answer', str(result))
        self.assertEqual(browser_failure_evidence({'reason':'logout_viewer','cleanup':True,
            'checkpoint':'synthetic-private-answer','failure_kind':'private-path',
            'auth_responses':{'logout':True,'login':700,'workspace':'secret','session':{},'token':200},
            'auth_ui':{'login_form':1,'sign_out':'secret','sign_out_pending':{},'token':True}}),
            {'checks':0,'reason':'logout_viewer','cleanup':True})
        misplaced = browser_failure_evidence({'reason':'anonymous','cleanup':True,
            'checkpoint':'logout_form','failure_kind':'timeout',
            'auth_responses':{'logout':200},'auth_ui':{'login_form':True}})
        self.assertEqual(misplaced, {'checks':0,'reason':'anonymous','cleanup':True})
        self.assertEqual(browser_failure_evidence({'reason':'login','cleanup':True,
            'checkpoint':'synthetic-private','failure_kind':'private-path',
            'auth_responses':{'login':True,'session':700,'token':200},
            'auth_ui':{'login_form':'private-path','token':True}}),
            {'checks':0,'reason':'login','cleanup':True})

    def test_browser_startup_paths_are_resolved_without_inheriting_credentials(self):
        from tools.check_browser import browser_environment
        ambient = {"PATH": "synthetic-tools", "TEMP": "synthetic-temp", "TMP": "synthetic-temp",
            "USERPROFILE": "synthetic-stale-profile", "MAIPA_SECRET_KEY": "synthetic-private-key",
            "NODE_OPTIONS": "synthetic-private-injection", "OPENAI_API_KEY": "synthetic-private-token"}
        paths = {"USERPROFILE": "synthetic-os-profile", "LOCALAPPDATA": "synthetic-os-local",
            "APPDATA": "synthetic-os-roaming", "SYSTEMROOT": "synthetic-windows", "private": "synthetic-private-extra"}
        self.assertEqual(browser_environment(ambient, paths), {"PATH": "synthetic-tools", "TEMP": "synthetic-temp",
            "TMP": "synthetic-temp", "USERPROFILE": "synthetic-os-profile", "LOCALAPPDATA": "synthetic-os-local",
            "APPDATA": "synthetic-os-roaming", "SYSTEMROOT": "synthetic-windows"})
        self.assertEqual(ambient["MAIPA_SECRET_KEY"], "synthetic-private-key")

    def test_launch_evidence_keeps_fixed_state_and_numeric_exit_without_child_text(self):
        self.assertEqual(browser_failure_evidence({"reason": "browser_launch", "cleanup": True,
            "startup_state": "exited", "launch_exit": 21, "launch_error": "EACCES", "stderr": "synthetic-private-path"}),
            {"checks": 0, "reason": "browser_launch", "cleanup": True,
             "startup_state": "exited", "launch_exit": 21, "launch_error": "EACCES"})

    def test_untrusted_launch_metadata_and_boolean_exit_are_discarded(self):
        result = browser_failure_evidence({"reason": "browser_launch", "cleanup": True,
            "startup_state": "synthetic-private-state", "launch_exit": True, "launch_error": "synthetic-private-error"})
        self.assertEqual(result, {"checks": 0, "reason": "browser_launch", "cleanup": True})

    def test_known_failure_preserves_stage_and_verified_cleanup_only(self):
        self.assertEqual(browser_failure_evidence({"reason": "profile_parent", "cleanup": True,
            "stdout": "synthetic private cookie", "path": "synthetic private home"}),
            {"checks": 0, "reason": "profile_parent", "cleanup": True})

    def test_unknown_messages_and_nonboolean_cleanup_are_not_reported_as_verified(self):
        for value in (None, {}, {"reason": "synthetic private diagnostic", "cleanup": True},
                      {"reason": "profile_parent", "cleanup": 1}):
            result = browser_failure_evidence(value)
            self.assertNotIn("synthetic private", str(result))
            self.assertFalse(result["cleanup"])
            self.assertEqual(result["checks"], 0)
