"""Independent P01 HTTP outcomes using real password authentication and sessions."""
import json

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import Client, SimpleTestCase, TestCase, override_settings

from backend.maipa.agent_accounts.models import AgentAccount, TransportCheck


class LoginSurfaceTests(SimpleTestCase):
    def test_anonymous_csrf_bootstrap_is_minimal_and_uncached(self):
        response = self.client.get("/api/session/csrf/", secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"csrf_token"})
        self.assertGreater(len(response.json()["csrf_token"]), 30)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_login_read_is_refused_without_redirect(self):
        response = self.client.get("/api/session/login/", secure=True)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "POST")
        self.assertNotIn("Location", response)


class WorkspaceAuthTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # Imports here let red tests report the missing product contract.
        from backend.maipa.workspace.models import Installation, Membership
        cls.install = Installation.objects.create()
        cls.people = {}
        for role in ("administrator", "preparer", "reviewer", "viewer"):
            user = get_user_model().objects.create_user(username="synthetic-" + role, password="synthetic-test-passphrase")
            Membership.objects.create(user=user, installation=cls.install, role=role)
            cls.people[role] = user
        cls.outsider = get_user_model().objects.create_user(username="synthetic-outsider", password="synthetic-test-passphrase")
        cls.account = AgentAccount.objects.create(owner=cls.people["preparer"], provider="codex", label="Synthetic private account")

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def csrf(self):
        response = self.client.get("/api/session/csrf/", secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()), {"csrf_token"})
        return response.json()["csrf_token"]

    def login(self, role="preparer", **overrides):
        body = {"username": "synthetic-" + role, "password": "synthetic-test-passphrase"} | overrides
        return self.client.post("/api/session/login/", data=json.dumps(body), content_type="application/json", secure=True,
            HTTP_ORIGIN="https://testserver", HTTP_X_CSRFTOKEN=self.csrf())

    def test_real_login_rotates_session_and_csrf_and_logout_invalidates_old_cookie(self):
        session = self.client.session
        session["untrusted_marker"] = "synthetic"
        session.save()
        self.client.cookies["sessionid"] = session.session_key
        old_key = session.session_key
        self.csrf()
        old_csrf = self.client.cookies["csrftoken"].value
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"], {"id": str(self.people["preparer"].pk)})
        self.assertNotEqual(self.client.cookies["sessionid"].value, old_key)
        self.assertNotEqual(self.client.cookies["csrftoken"].value, old_csrf)
        self.assertEqual(response["Cache-Control"], "no-store")
        captured = self.client.cookies["sessionid"].value
        response = self.client.post("/api/session/logout/", secure=True, HTTP_X_CSRFTOKEN=response.json()["csrf_token"], HTTP_ORIGIN="https://testserver")
        self.assertEqual(response.status_code, 200)
        stolen = Client()
        stolen.cookies["sessionid"] = captured
        self.assertEqual(stolen.get("/api/session/", secure=True).status_code, 401)

    def test_invalid_inactive_and_unprovisioned_login_are_identical(self):
        self.outsider.is_active = False
        self.outsider.save(update_fields=["is_active"])
        observed = []
        for username, password in (("synthetic-missing", "synthetic-test-passphrase"),
                                   ("synthetic-preparer", "incorrect"), ("synthetic-outsider", "synthetic-test-passphrase")):
            response = self.login(username=username, password=password)
            observed.append((response.status_code, response.json()))
            self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(observed, [(401, {"error": {"code": "invalid_credentials"}})] * 3)
        self.outsider.is_active = True
        self.outsider.save(update_fields=["is_active"])
        response = self.login(username="synthetic-outsider")
        self.assertEqual((response.status_code, response.json()), observed[0])

    def test_login_requires_csrf_same_origin_and_post(self):
        body = json.dumps({"username": "synthetic-preparer", "password": "synthetic-test-passphrase"})
        token = self.csrf()
        for headers in ({}, {"HTTP_X_CSRFTOKEN": token, "HTTP_ORIGIN": "https://foreign.invalid"}):
            response = self.client.post("/api/session/login/", data=body, content_type="application/json", secure=True, **headers)
            self.assertEqual((response.status_code, response.json()), (403, {"error": {"code": "csrf_failed"}}))
            self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get("/api/session/login/", secure=True).status_code, 405)

    @override_settings(ALLOWED_HOSTS=["maipa.gen"], CSRF_TRUSTED_ORIGINS=["https://maipa.gen"],
                       CSRF_COOKIE_SECURE=False, SESSION_COOKIE_SECURE=False)
    def test_genie_https_origin_login_works_over_the_loopback_proxy_transport(self):
        response = self.client.get("/api/session/csrf/", HTTP_HOST="maipa.gen")
        self.assertEqual(response.status_code, 200)
        response = self.client.post("/api/session/login/", data=json.dumps({"username": "synthetic-preparer", "password": "synthetic-test-passphrase"}),
            content_type="application/json", HTTP_HOST="maipa.gen", HTTP_ORIGIN="https://maipa.gen",
            HTTP_X_CSRFTOKEN=response.json()["csrf_token"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"], {"id": str(self.people["preparer"].pk)})

    def test_login_refuses_identity_role_duplicates_malformed_and_overbudget_payloads(self):
        token = self.csrf()
        for body in ('[]', 'null', '{"username":"synthetic-preparer","username":"synthetic-viewer","password":"synthetic-test-passphrase"}',
                     '{"username":"synthetic-preparer","password":"synthetic-test-passphrase","role":"administrator"}',
                     '{"username":"synthetic-preparer","password":"synthetic-test-passphrase","tenant_id":"foreign"}'):
            response = self.client.post("/api/session/login/", data=body, content_type="application/json", secure=True,
                HTTP_ORIGIN="https://testserver", HTTP_X_CSRFTOKEN=token)
            self.assertEqual((response.status_code, response.json()), (400, {"error": {"code": "invalid_request"}}))
        response = self.client.post("/api/session/login/", data=" " * 4097, content_type="application/json", secure=True,
            HTTP_ORIGIN="https://testserver", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 413)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_install_and_role_come_from_server_and_second_install_is_refused(self):
        from backend.maipa.workspace.models import Installation
        self.assertEqual(self.login().status_code, 200)
        response = self.client.get("/api/workspace/?tenant_id=foreign&role=administrator", secure=True,
            HTTP_X_TENANT_ID="foreign", HTTP_X_USER_ID=str(self.people["administrator"].pk), HTTP_X_ROLE="administrator")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"installation_id": str(self.install.id), "role": "preparer",
            "permissions": ["workspace.read", "agent.accounts.read", "agent.transport.check"]})
        for fields in ({}, {"slot": 2}):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    Installation.objects.create(**fields)

    def test_viewer_cannot_start_transport_check_or_escalate_role(self):
        account = AgentAccount.objects.create(owner=self.people["viewer"], provider="claude", label="Own viewer account")
        response = self.login("viewer")
        self.assertEqual(response.status_code, 200)
        token = response.json()["csrf_token"]
        self.assertEqual(self.client.get("/api/agent/accounts/", secure=True).status_code, 200)
        response = self.client.post(f"/api/agent/accounts/{account.pk}/transport-checks/?role=administrator", data="{}",
            content_type="application/json", secure=True, HTTP_ORIGIN="https://testserver", HTTP_X_CSRFTOKEN=token,
            HTTP_X_ROLE="administrator")
        self.assertEqual((response.status_code, response.json()), (403, {"error": {"code": "permission_denied"}}))
        self.assertEqual(TransportCheck.objects.count(), 0)

    def test_membership_revocation_takes_effect_on_existing_session(self):
        from backend.maipa.workspace.models import Membership
        self.assertEqual(self.login().status_code, 200)
        Membership.objects.filter(user=self.people["preparer"]).update(enabled=False)
        for path in ("/api/session/", "/api/workspace/", "/api/agent/accounts/"):
            response = self.client.get(path, secure=True)
            self.assertEqual((response.status_code, response.json()), (403, {"error": {"code": "workspace_access_denied"}}))

    def test_login_cannot_replace_an_already_authenticated_principal(self):
        self.assertEqual(self.login().status_code, 200)
        response = self.login("administrator")
        self.assertEqual((response.status_code, response.json()), (409, {"error": {"code": "already_signed_in"}}))
        self.assertEqual(self.client.get("/api/session/", secure=True).json()["user"], {"id": str(self.people["preparer"].pk)})

    def test_login_attempt_budget_is_shared_across_sessions_and_contains_no_plaintext(self):
        from backend.maipa.workspace.models import LoginBudget
        for _ in range(5):
            self.client = Client(enforce_csrf_checks=True)
            self.assertEqual(self.login(password="incorrect").status_code, 401)
        self.client = Client(enforce_csrf_checks=True)
        response = self.login()
        self.assertEqual((response.status_code, response.json()), (429, {"error": {"code": "login_rate_limited"}}))
        self.assertEqual(response["Retry-After"], "900")
        self.assertNotIn("synthetic-preparer", str(list(LoginBudget.objects.values())))

    def test_two_authenticated_users_cannot_read_foreign_accounts_or_history(self):
        self.assertEqual(self.login().status_code, 200)
        TransportCheck.objects.create(account=self.account, requested_by=self.people["preparer"], provider="codex", status="unavailable")
        other = Client(enforce_csrf_checks=True)
        self.client, first = other, self.client
        self.assertEqual(self.login("administrator").status_code, 200)
        self.assertEqual(self.client.get("/api/agent/accounts/", secure=True).json()["accounts"], [])
        foreign = self.client.get(f"/api/agent/accounts/{self.account.pk}/transport-checks/", secure=True)
        self.assertEqual((foreign.status_code, foreign.json()), (404, {"error": {"code": "not_found"}}))
        self.assertEqual(first.get(f"/api/agent/accounts/{self.account.pk}/transport-checks/", secure=True).json()["checks"][0]["status"], "unavailable")
