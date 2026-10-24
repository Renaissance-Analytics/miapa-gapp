"""API behavior from independent security/transport outcomes, not UI sketches."""
from django.contrib.auth import get_user_model
from django.test import Client, SimpleTestCase, TestCase, override_settings


class PublicApiTests(SimpleTestCase):
    def test_health_is_minimal_public_liveness(self):
        response = self.client.get("/api/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_health_rejects_mutation(self):
        response = self.client.post("/api/health/")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json(), {"error": {"code": "method_not_allowed"}})
        self.assertEqual(response["Allow"], "GET")

    def test_unknown_api_returns_generic_json(self):
        response = self.client.get("/api/unknown-private-name/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"error": {"code": "not_found"}})
        self.assertNotContains(response, "unknown-private-name", status_code=404)

    def test_untrusted_host_is_rejected_without_reflection(self):
        response = self.client.get("/api/health/", HTTP_HOST="untrusted.invalid")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": {"code": "bad_request"}})
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertNotContains(response, "untrusted.invalid", status_code=400)


class SessionApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="synthetic-user")
        cls.other = get_user_model().objects.create_user(username="synthetic-other")
        from backend.maipa.workspace.models import Installation, Membership
        installation = Installation.objects.create()
        for user in (cls.user, cls.other):
            Membership.objects.create(installation=installation, user=user, role="preparer")

    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def signed_in(self, user=None):
        self.client.force_login(user or self.user)
        return self.client.get("/api/session/", secure=True)

    def test_anonymous_session_is_unauthorized(self):
        response = self.client.get("/api/session/", secure=True)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": {"code": "authentication_required"}})
        self.assertNotIn("csrf_token", response.json())

    def test_session_uses_authenticated_identity_only(self):
        self.signed_in()
        response = self.client.get("/api/session/?user_id=" + str(self.other.pk), secure=True,
                                   HTTP_X_USER_ID=str(self.other.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"], {"id": str(self.user.pk)})
        self.assertEqual(set(response.json()), {"user", "csrf_token"})
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("Cookie", response["Vary"])

    def test_users_have_separate_sessions(self):
        first = self.signed_in().json()["user"]
        self.client = Client(enforce_csrf_checks=True)
        second = self.signed_in(self.other).json()["user"]
        self.assertEqual(first, {"id": str(self.user.pk)})
        self.assertEqual(second, {"id": str(self.other.pk)})
        self.assertNotEqual(first, second)

    def test_inactive_user_is_unauthorized(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        self.client.force_login(self.user)
        response = self.client.get("/api/session/", secure=True)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"error": {"code": "authentication_required"}})

    def test_session_rejects_write_method(self):
        response = self.signed_in()
        token = response.json()["csrf_token"]
        response = self.client.post("/api/session/", secure=True, HTTP_X_CSRFTOKEN=token,
                                    HTTP_ORIGIN="https://testserver")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "GET")

    def test_logout_requires_csrf_and_preserves_session_on_failure(self):
        self.signed_in()
        response = self.client.post("/api/session/logout/", secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": {"code": "csrf_failed"}})
        self.assertEqual(self.client.get("/api/session/", secure=True).status_code, 200)

    def test_foreign_origin_cannot_logout(self):
        token = self.signed_in().json()["csrf_token"]
        response = self.client.post("/api/session/logout/", secure=True,
                                    HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="https://foreign.invalid")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"error": {"code": "csrf_failed"}})
        self.assertEqual(self.client.get("/api/session/", secure=True).status_code, 200)

    def test_valid_same_origin_logout_invalidates_session(self):
        token = self.signed_in().json()["csrf_token"]
        response = self.client.post("/api/session/logout/", secure=True,
                                    HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="https://testserver")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "signed_out"})
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/api/session/", secure=True).status_code, 401)

    def test_get_cannot_logout(self):
        self.signed_in()
        response = self.client.get("/api/session/logout/", secure=True)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "POST")
        self.assertEqual(self.client.get("/api/session/", secure=True).status_code, 200)

    def test_forged_cookie_is_not_authentication(self):
        self.client.cookies["sessionid"] = "forged-session-value"
        response = self.client.get("/api/session/", secure=True)
        self.assertEqual(response.status_code, 401)

    def test_secure_cookie_defaults(self):
        self.signed_in()
        csrf = self.client.cookies["csrftoken"]
        self.assertTrue(csrf["secure"])
        self.assertTrue(csrf["httponly"])
        self.assertEqual(csrf["samesite"], "Lax")
        from django.conf import settings
        self.assertTrue(settings.SESSION_COOKIE_SECURE)
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)

    def test_security_headers(self):
        response = self.signed_in()
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["X-Frame-Options"], "DENY")
