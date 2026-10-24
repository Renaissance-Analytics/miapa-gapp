import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from backend.maipa.agent_accounts.models import AgentAccount, TransportCheck
from backend.maipa.agent_accounts.runtime import TransportUnavailable


class AgentAccountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="synthetic-agent-owner")
        cls.other = get_user_model().objects.create_user(username="synthetic-agent-other")
        from backend.maipa.workspace.models import Installation, Membership
        installation = Installation.objects.create()
        for user in (cls.user, cls.other):
            Membership.objects.create(installation=installation, user=user, role="preparer")
        cls.account = AgentAccount.objects.create(owner=cls.user, provider="codex", label="Personal Codex")
        cls.foreign = AgentAccount.objects.create(owner=cls.other, provider="claude", label="synthetic-private-label")
        cls.disabled = AgentAccount.objects.create(owner=cls.user, provider="claude", label="Disabled", enabled=False)

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings_override = override_settings(ACP_CHECK_ROOT=Path(self.temp.name))
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

    def sign_in(self, user=None):
        self.client.force_login(user or self.user)
        return self.client.get("/api/session/", secure=True).json()["csrf_token"]

    def checks_url(self, account=None):
        return "/api/agent/accounts/" + str((account or self.account).pk) + "/transport-checks/"

    def post_check(self, *, account=None, payload="{}", token=None, origin="https://testserver"):
        return self.client.post(self.checks_url(account), data=payload, content_type="application/json", secure=True,
            HTTP_X_CSRFTOKEN=token or self.sign_in(), HTTP_ORIGIN=origin)

    def test_database_refuses_duplicate_provider_accounts_and_unknown_providers(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AgentAccount.objects.create(owner=self.user, provider="codex", label="Duplicate")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                AgentAccount.objects.create(owner=self.user, provider="untrusted", label="Unknown")

    def test_actual_transport_has_verified_appcontainer_and_empty_job(self):
        from backend.maipa.agent_accounts.runtime import probe_transport
        self.assertEqual(probe_transport(self.account),
            {"isolation": "windows-appcontainer", "job_empty": True, "provider_sessions": 0})

    def test_stale_reassigned_or_revoked_account_cannot_launch_a_worker(self):
        from backend.maipa.agent_accounts.runtime import probe_transport
        from backend.maipa.workspace.models import Membership
        Membership.objects.filter(user=self.user).update(enabled=False)
        with self.assertRaises(TransportUnavailable):
            probe_transport(self.account)
        Membership.objects.filter(user=self.user).update(enabled=True)
        AgentAccount.objects.filter(pk=self.account.pk).update(owner=self.other)
        with self.assertRaises(TransportUnavailable):
            probe_transport(self.account)

    def test_runtime_list_uses_authenticated_owner_and_exposes_no_paths_or_login_claim(self):
        self.sign_in()
        response = self.client.get("/api/agent/accounts/?user_id=" + str(self.other.pk), secure=True,
            HTTP_X_USER_ID=str(self.other.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            "runtime": {"package": "@particle-academy/prism-acp", "version": "0.24.1", "provider_sessions_enabled": False},
            "accounts": [{"id": str(self.account.pk), "provider": "codex", "label": "Personal Codex", "status": "setup_required"}],
        })
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("Cookie", response["Vary"])
        self.assertNotContains(response, "synthetic-private-label")
        self.assertNotContains(response, self.temp.name)

    def test_anonymous_and_inactive_users_cannot_read_accounts_or_check_history(self):
        for path in ("/api/agent/accounts/", self.checks_url()):
            response = self.client.get(path, secure=True)
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {"error": {"code": "authentication_required"}})
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/api/agent/accounts/", secure=True).status_code, 401)
        self.assertEqual(TransportCheck.objects.count(), 0)

    def test_foreign_disabled_and_missing_accounts_are_indistinguishable(self):
        token = self.sign_in()
        for account in (self.foreign, self.disabled):
            for response in (self.client.get(self.checks_url(account), secure=True), self.post_check(account=account, token=token)):
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"error": {"code": "not_found"}})
                self.assertNotContains(response, "synthetic-private-label", status_code=404)
        missing = self.client.get("/api/agent/accounts/" + str(uuid.uuid4()) + "/transport-checks/", secure=True)
        self.assertEqual(missing.json(), {"error": {"code": "not_found"}})
        self.assertEqual(TransportCheck.objects.count(), 0)

    def test_missing_csrf_and_foreign_origin_never_create_a_check(self):
        token = self.sign_in()
        with patch("backend.maipa.agent_accounts.runtime.probe_transport") as probe:
            responses = [self.client.post(self.checks_url(), data="{}", content_type="application/json", secure=True),
                         self.post_check(token=token, origin="https://foreign.invalid")]
            probe.assert_not_called()
        for response in responses:
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json(), {"error": {"code": "csrf_failed"}})
        self.assertEqual(TransportCheck.objects.count(), 0)
        self.account.refresh_from_db()
        self.assertIsNone(self.account.last_check_started_at)

    def test_client_cannot_supply_identity_profile_workspace_commands_or_mcp(self):
        token = self.sign_in()
        with patch("backend.maipa.agent_accounts.runtime.probe_transport") as probe:
            for payload in ({"user_id": str(self.other.pk)}, {"account_id": str(self.foreign.pk)},
                            {"workspace": "C:/private"}, {"command": "do-not-run"}, {"home": "C:/private"}, {"mcpServers": []}):
                response = self.post_check(payload=json.dumps(payload), token=token)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"error": {"code": "invalid_request"}})
            probe.assert_not_called()
        self.assertEqual(TransportCheck.objects.count(), 0)

    def test_malformed_or_overbudget_check_bodies_are_rejected(self):
        token = self.sign_in()
        for payload in ("[]", "null", "private-broken-json", "{\"provider\":NaN}"):
            response = self.post_check(payload=payload, token=token)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {"error": {"code": "invalid_request"}})
        response = self.post_check(payload=" " * 1025, token=token)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(TransportCheck.objects.count(), 0)

    def test_actual_authenticated_transport_check_records_no_provider_readiness(self):
        response = self.post_check()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.json()), {"id", "status", "provider_ready"})
        self.assertEqual(response.json()["status"], "transport_verified")
        self.assertIs(response.json()["provider_ready"], False)
        record = TransportCheck.objects.get(pk=response.json()["id"])
        self.assertEqual(record.account_id, self.account.pk)
        self.assertEqual(record.requested_by_id, self.user.pk)
        self.assertEqual(record.provider, "codex")
        self.assertEqual(record.status, "transport_verified")
        self.assertIsNotNone(record.completed_at)
        record.full_clean()
        self.assertNotContains(response, self.temp.name, status_code=201)
        self.assertEqual(response["Cache-Control"], "no-store")
        history = self.client.get(self.checks_url(), secure=True)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json()["checks"][0]["id"], str(record.pk))
        self.assertIs(history.json()["checks"][0]["provider_ready"], False)

    def test_account_cooldown_refuses_overlap_without_starting_or_recording_work(self):
        self.sign_in()
        self.account.last_check_started_at = timezone.now()
        self.account.save(update_fields=["last_check_started_at"])
        with patch("backend.maipa.agent_accounts.runtime.probe_transport") as probe:
            response = self.post_check()
            probe.assert_not_called()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json(), {"error": {"code": "check_rate_limited"}})
        self.assertEqual(response["Retry-After"], "30")
        self.assertEqual(TransportCheck.objects.count(), 0)

    def test_broken_runtime_is_generic_and_history_stays_owned(self):
        self.sign_in()
        with override_settings(ACP_RUNTIME_ROOT=Path(self.temp.name) / "missing-private-runtime"):
            response = self.client.get("/api/agent/accounts/", secure=True)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"error": {"code": "agent_runtime_unavailable"}})
            self.assertNotContains(response, "missing-private-runtime", status_code=503)
        TransportCheck.objects.create(account=self.foreign, requested_by=self.other, provider="claude", status="unavailable")
        response = self.client.get(self.checks_url(), secure=True)
        self.assertEqual(response.json(), {"checks": []})

    def test_accounts_are_read_only_and_check_history_rejects_delete(self):
        token = self.sign_in()
        response = self.client.post("/api/agent/accounts/", data="{}", content_type="application/json", secure=True,
            HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="https://testserver")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "GET")
        response = self.client.delete(self.checks_url(), secure=True, HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="https://testserver")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "GET, POST")
        self.assertEqual(TransportCheck.objects.count(), 0)

    def test_runtime_failure_is_recorded_without_reflecting_diagnostics_or_claiming_login(self):
        token = self.sign_in()
        with patch("backend.maipa.agent_accounts.runtime.probe_transport", side_effect=TransportUnavailable("synthetic-private-diagnostic")):
            response = self.post_check(token=token)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"error": {"code": "agent_transport_unavailable"}})
        record = TransportCheck.objects.get(account=self.account)
        self.assertEqual(record.status, "unavailable")
        self.assertIsNotNone(record.completed_at)
        self.assertNotContains(response, "synthetic-private-diagnostic", status_code=503)
        history = self.client.get(self.checks_url(), secure=True).json()
        self.assertEqual(history["checks"][0]["status"], "unavailable")
        self.assertIs(history["checks"][0]["provider_ready"], False)

    def test_second_user_owns_a_real_transport_check_without_access_to_first_user_history(self):
        TransportCheck.objects.create(account=self.account, requested_by=self.user, provider="codex", status="unavailable")
        token = self.sign_in(self.other)
        response = self.post_check(account=self.foreign, token=token)
        self.assertEqual(response.status_code, 201)
        record = TransportCheck.objects.get(pk=response.json()["id"])
        self.assertEqual(record.account.owner_id, self.other.pk)
        self.assertEqual(record.requested_by_id, self.other.pk)
        self.assertEqual(record.provider, "claude")
        denied = self.client.get(self.checks_url(), secure=True)
        self.assertEqual(denied.status_code, 404)
        own_history = self.client.get(self.checks_url(self.foreign), secure=True).json()
        self.assertEqual([check["id"] for check in own_history["checks"]], [str(record.pk)])

    def test_reassigned_account_does_not_disclose_the_previous_requesters_history(self):
        TransportCheck.objects.create(account=self.account, requested_by=self.user, provider="codex", status="unavailable")
        self.account.owner = self.other
        self.account.save(update_fields=["owner"])
        self.sign_in(self.other)
        response = self.client.get(self.checks_url(), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"checks": []})
