"""Failure reports may expose a fixed stage code, never arbitrary child diagnostics."""
import unittest
from tools.check_browser import browser_failure_evidence


class BrowserReportingTests(unittest.TestCase):
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
