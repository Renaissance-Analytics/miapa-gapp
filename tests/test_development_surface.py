from pathlib import Path
import tempfile

from django.test import SimpleTestCase, override_settings


class DevelopmentSurfaceTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dist = Path(self.temp.name)
        (self.dist / "assets").mkdir()
        (self.dist / "index.html").write_text("<!doctype html><title>Maipa synthetic build</title>", encoding="utf-8")
        (self.dist / "assets/app.js").write_text("/* synthetic asset */", encoding="utf-8")

    def test_development_serves_built_shell_and_asset(self):
        with override_settings(LOCAL_DEV=True, FRONTEND_DIST=self.dist):
            shell = self.client.get("/")
            self.assertEqual(shell.status_code, 200)
            self.assertEqual(b"".join(shell.streaming_content), b"<!doctype html><title>Maipa synthetic build</title>")
            self.assertEqual(shell["Cache-Control"], "no-store")
            asset = self.client.get("/assets/app.js")
            self.assertEqual(asset.status_code, 200)
            self.assertEqual(b"".join(asset.streaming_content), b"/* synthetic asset */")

    def test_non_development_never_exposes_local_build(self):
        with override_settings(LOCAL_DEV=False, FRONTEND_DIST=self.dist):
            for path in ("/", "/assets/app.js"):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {"error": {"code": "not_found"}})

    def test_missing_development_build_is_unavailable(self):
        with override_settings(LOCAL_DEV=True, FRONTEND_DIST=self.dist / "missing"):
            response = self.client.get("/")
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json(), {"error": {"code": "frontend_unavailable"}})

    def test_asset_traversal_cannot_read_private_runtime_file(self):
        (self.dist / "private-secret.txt").write_text("synthetic-private-value", encoding="utf-8")
        with override_settings(LOCAL_DEV=True, FRONTEND_DIST=self.dist):
            response = self.client.get("/assets/../private-secret.txt")
            self.assertEqual(response.status_code, 404)
            self.assertNotContains(response, "synthetic-private-value", status_code=404)
