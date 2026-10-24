import json
from pathlib import Path
import tempfile

from django.test import SimpleTestCase, override_settings


class BuildVersionTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dist = Path(self.temp.name)

    def test_version_exposes_only_public_built_identity_without_caching(self):
        (self.dist / "build-info.json").write_text(json.dumps({
            "schema": 1, "build_id": "a" * 64,
            "release_id": "development-2", "private": "synthetic-private-value",
        }), encoding="utf-8")
        with override_settings(FRONTEND_DIST=self.dist):
            response = self.client.get("/api/version/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"build_id": "a" * 64})
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertNotContains(response, "synthetic-private-value")

    def test_missing_or_invalid_metadata_never_claims_a_version_or_leaks_details(self):
        invalid = [None, "private-broken-json", "x" * 4097,
                   json.dumps({"schema": 1, "build_id": "../private-value"}),
                   json.dumps({"schema": True, "build_id": "a" * 64}),
                   json.dumps({"schema": 1, "build_id": 42}), "[]"]
        with override_settings(FRONTEND_DIST=self.dist):
            for content in invalid:
                with self.subTest(content=content is None):
                    if content is not None:
                        (self.dist / "build-info.json").write_text(content, encoding="utf-8")
                    response = self.client.get("/api/version/")
                    self.assertEqual(response.status_code, 503)
                    self.assertEqual(response.json(), {"error": {"code": "version_unavailable"}})
                    self.assertEqual(response["Cache-Control"], "no-store")

    def test_version_is_read_only(self):
        response = self.client.post("/api/version/", {"build_id": "forged"})
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "GET")
        self.assertEqual(response.json(), {"error": {"code": "method_not_allowed"}})
